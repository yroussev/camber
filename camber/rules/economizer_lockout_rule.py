"""Rule: economizer not locked out above the high limit.

Above the outdoor-air high limit, bringing in more outdoor air only adds cooling load. The
economizer should hold minimum OA; still admitting excess OA in hot weather is a stuck/mis-tuned
economizer wasting cooling energy.

**What "excess" means depends on the building.** OA *damper position* is a weak proxy — it is
not linear in OA flow, and the design minimum outside air is a building property (a 100%-OA or
high-OA design correctly sits far open at minimum). So when mixed- and return-air temperatures
are available this rule judges on **outside-air fraction** (temperature balance, the method of
:mod:`camber.oafraction`) against a minimum-OA *fraction*; otherwise it falls back to a damper
threshold, and says so in a caveat. When the unit trends **measured** outdoor airflow
(``OA_AIRFLOW``) and supply airflow (``AIRFLOW``) the OA fraction is taken from those -- the most
direct measurement available, and free of the temperature balance's noise near OAT ≈ RAT.

**The minimum OA is a building property, not a constant.** A high-occupancy design can sit at
30-40 % minimum OA, which a generic 20 % assumption reads as "not locked out". So unless
``min_oa_pct`` is configured, the rule judges the OA fraction against **two** minimums: the generic
``assumed_min_oa_pct`` (20 %) and a generous ``conservative_min_oa_pct`` (50 %, above the design
minimum of any ordinary mixed-air unit). Excess above the conservative bound is excess whatever the
design minimum is, and sets the severity; excess that only clears the 20 % assumption *depends on
the unknown minimum*, so the rule declines (``info`` + caveat) instead of faulting.

**Differential changeover.** Many economizers use a *differential* dry-bulb high limit (outdoor vs
return air): economizing at 68 °F outdoor against a 74 °F return is correct control, not a missing
lockout. With ``differential=True`` (default) and a return-air temperature trended, samples above
the fixed high limit but still below return air are excluded (and counted in a caveat); only
``OAT > max(high limit, RAT)`` is judged. The fixed high limit is configurable too -- 65 °F encodes
a typical, not universal, building; e.g. CA Title 24 sets it by climate zone.

Distinct from :mod:`camber.rules.oafraction_rule` (`outdoor_air_fraction`), which flags excess OA
across cooling weather generally; this rule is specifically the *lockout above the high limit*.
numpy/pandas.
"""

from __future__ import annotations

import pandas as pd

from ..model.roles import Role
from ..schedules import FAN_GATE_NONE, fan_on_mask
from ..units import normalize_percent
from .base import Finding


class EconomizerHighLimit:
    """Flags excess outside air admitted above the economizer high limit (no lockout)."""

    name = "economizer_high_limit"
    roles_required = (Role.OA_DAMPER, Role.OAT)
    # When both are present, judge on OA-fraction (temperature balance) instead of damper %;
    # measured OA + supply airflow (both present) beat the temperature balance.
    roles_optional = (
        Role.MIXED_AIR_TEMP,
        Role.RETURN_AIR_TEMP,
        Role.OA_AIRFLOW,
        Role.AIRFLOW,
        # fan-on gate: status, else speed (airflow, above, is the last resort)
        Role.SUPPLY_FAN_STATUS,
        Role.SUPPLY_FAN_SPEED,
    )

    def __init__(
        self,
        *,
        high_limit_f: float = 65.0,
        min_damper: float = 0.25,  # OA-damper fraction (0..1) for the fallback path
        min_oa_pct: float | None = None,  # design minimum OA fraction (%); None = unknown
        oa_margin_pct: float = 5.0,  # OAF must exceed min + this to count as "not locked out"
        warn_pct: float = 10.0,
        fault_pct: float = 25.0,
        denom_min_f: float = 5.0,  # skip OAF where |RAT-OAT| is too small to be reliable
        assumed_min_oa_pct: float = 20.0,  # generic design minimum when none is configured
        conservative_min_oa_pct: float = 50.0,  # generous upper bound on a design minimum
        differential: bool = True,  # exclude OAT < RAT (differential changeover still economizes)
        fan_gate: bool = True,  # judge fan-on samples only (fan off: no mixing to judge)
    ):
        self.high_limit_f = high_limit_f
        self.min_damper = min_damper
        self.min_oa_pct = min_oa_pct
        self.oa_margin_pct = oa_margin_pct
        self.warn_pct = warn_pct
        self.fault_pct = fault_pct
        self.denom_min_f = denom_min_f
        self.assumed_min_oa_pct = assumed_min_oa_pct
        self.conservative_min_oa_pct = conservative_min_oa_pct
        self.differential = differential
        self.fan_gate = fan_gate

    @staticmethod
    def _measured_oaf(frame: pd.DataFrame):
        """Measured OA fraction (%) = 100 * OA_AIRFLOW / AIRFLOW where supply flow is positive."""
        if Role.OA_AIRFLOW not in frame.columns or Role.AIRFLOW not in frame.columns:
            return None
        oa, sa = frame[Role.OA_AIRFLOW], frame[Role.AIRFLOW]
        p95 = sa.quantile(0.95)
        if not (p95 > 0):
            return None
        oaf = (100.0 * oa / sa).where((sa > 0.10 * p95) & (oa >= 0))
        oaf = oaf.where(oaf.between(0, 120))
        return oaf if int(oaf.notna().sum()) >= 10 else None

    def _judged(self, frame: pd.DataFrame) -> dict:
        """The samples this rule judges and the quantity it judges them on.

        Shared by :meth:`analyze` and :meth:`evidence`, so the evidence chart is drawn from exactly
        the samples, basis and threshold behind the verdict (a chart built from generic defaults
        would shade points the rule never counted). Returns ``oat``; ``hot`` (above the high limit,
        after the differential exclusion); ``y`` (measured or temperature-balance OA fraction in %,
        or the damper as a 0-1 fraction); ``plausible`` (samples where ``y`` is usable);
        ``valid = hot & plausible``; ``basis``; ``caveats``.
        """
        oat = frame[Role.OAT]
        caveats: list = []
        fan, fan_src = (None, "off")
        if self.fan_gate:
            fan, fan_src = fan_on_mask(frame)
        on = (
            pd.Series(True, index=frame.index)
            if fan is None
            else pd.Series(fan.to_numpy(dtype=bool), index=frame.index)
        )
        masked = {"fan_off": int((~on & oat.notna() & (oat > self.high_limit_f)).sum())}
        hot = (oat > self.high_limit_f) & oat.notna() & on
        if self.differential and Role.RETURN_AIR_TEMP in frame.columns:
            rat = frame[Role.RETURN_AIR_TEMP]
            econ_ok = hot & rat.notna() & (oat <= rat)
            n_diff = int(econ_ok.sum())
            hot = hot & ~econ_ok
            if n_diff:
                caveats.append(
                    f"{n_diff} sample(s) above the {self.high_limit_f:g}°F high limit but below "
                    "return-air temperature excluded: a differential dry-bulb economizer "
                    "correctly economizes there (pass differential=False for a fixed-limit unit)"
                )
        have_temps = Role.MIXED_AIR_TEMP in frame.columns and Role.RETURN_AIR_TEMP in frame.columns
        measured = self._measured_oaf(frame)

        if measured is not None:
            y = measured
            plausible = y.notna() & on
            basis = "measured OA fraction"
        elif have_temps:
            # Temperature-balance OA-fraction (percent), same method + guards as camber.oafraction.
            mat, rat = frame[Role.MIXED_AIR_TEMP], frame[Role.RETURN_AIR_TEMP]
            denom = rat - oat
            y = 100.0 * (rat - mat) / denom
            sensors_ok = oat.between(20, 130) & mat.between(30, 120) & rat.between(40, 110)
            stable = denom.abs() >= self.denom_min_f
            in_range = y.between(-20, 120)
            plausible = sensors_ok & stable & in_range & on
            masked["small_delta_t"] = int((hot & sensors_ok & ~stable).sum())
            masked["out_of_range"] = int((hot & sensors_ok & stable & ~in_range).sum())
            basis = "OA-fraction"
        else:
            # OA_DAMPER arrives 0-1 or 0-100 depending on the BAS; canonicalize to a fraction
            # so the fraction threshold is correct either way. (The role pipeline scales percent
            # roles to 0-100 -- comparing that against a 0-1 threshold makes every open damper
            # read "not locked out", the original mis-scaling behind this rule's false faults.)
            y = normalize_percent(frame[Role.OA_DAMPER]) / 100.0
            plausible = y.notna() & on
            basis = "damper position"
            caveats.append(
                "no mixed/return-air temps: judged on damper position "
                "(a weak proxy for outside-air fraction)"
            )
        return {
            "oat": oat,
            "hot": hot,
            "y": y,
            "plausible": plausible,
            "valid": hot & plausible,
            "basis": basis,
            "caveats": caveats,
            "fan_gate": fan_src,
            "masked": masked,
        }

    def excess_threshold(self, basis: str) -> float:
        """The value of the judged quantity above which a sample counts as *not locked out*.

        Damper basis: ``min_damper + 0.05`` (a 0-1 fraction). OA-fraction bases: the configured
        ``min_oa_pct + oa_margin_pct``, or -- design minimum unknown -- the severity-setting
        ``conservative_min_oa_pct + oa_margin_pct`` (percent).
        """
        if basis == "damper position":
            return self.min_damper + 0.05
        if self.min_oa_pct is not None:
            return self.min_oa_pct + self.oa_margin_pct
        return self.conservative_min_oa_pct + self.oa_margin_pct

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        j = self._judged(frame)
        caveats: list = j["caveats"]
        valid, basis = j["valid"], j["basis"]
        gate_metrics = {
            "fan_gate": j["fan_gate"],
            "denom_min_f": self.denom_min_f,
            "n_masked_fan_off": j["masked"].get("fan_off", 0),
            "n_masked_small_delta_t": j["masked"].get("small_delta_t", 0),
            "n_masked_out_of_range": j["masked"].get("out_of_range", 0),
        }
        missing = [
            r.value for r in self.roles_optional[:4] if r not in frame.columns
        ]  # MAT/RAT/OA flow/airflow
        if j["fan_gate"] == FAN_GATE_NONE:
            missing.append(Role.SUPPLY_FAN_STATUS.value)
        if missing:
            gate_metrics["_missing_optional"] = missing
        oaf = None if basis == "damper position" else j["y"]
        damper = j["y"]
        n = int(valid.sum())

        if n == 0:
            msg = f"{equip}: no hours above the {self.high_limit_f:g}°F high limit"
            if basis != "damper position":
                msg += " with a usable OA-fraction"
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics=gate_metrics,
                summary=msg,
                caveats=caveats,
            )

        def _sev(pct: float) -> str:
            return "fault" if pct >= self.fault_pct else "warn" if pct >= self.warn_pct else "ok"

        metrics = {
            "high_limit_f": self.high_limit_f,
            "basis": basis,
            "min_oa_pct": self.min_oa_pct,
            "min_oa_source": "configured" if self.min_oa_pct is not None else "unknown",
            "min_damper": self.min_damper,
            "n_above_limit": n,
            **gate_metrics,
        }
        if oaf is None:  # damper fallback
            not_locked = valid & (damper > self.min_damper + 0.05)
            pct = 100.0 * float(not_locked.sum()) / n
            sev = _sev(pct)
            thresh = f"damper > {self.min_damper + 0.05:.0%}"
        elif self.min_oa_pct is not None:
            pct = 100.0 * float((valid & (oaf > self.min_oa_pct + self.oa_margin_pct)).sum()) / n
            sev = _sev(pct)
            thresh = f"OAF > {self.min_oa_pct + self.oa_margin_pct:g}%"
        else:
            # Design minimum unknown: excess above a generous bound is excess whatever the minimum
            # is (it sets the severity); excess only above the generic 20 % depends on it.
            hi = self.conservative_min_oa_pct
            lo = self.assumed_min_oa_pct
            pct = 100.0 * float((valid & (oaf > hi + self.oa_margin_pct)).sum()) / n
            pct_lo = 100.0 * float((valid & (oaf > lo + self.oa_margin_pct)).sum()) / n
            metrics["excess_over_assumed_min_pct"] = round(pct_lo, 2)
            metrics["assumed_min_oa_pct"] = lo
            metrics["conservative_min_oa_pct"] = hi
            sev = _sev(pct)
            thresh = f"OAF > {hi + self.oa_margin_pct:g}%"
            if sev == "ok" and _sev(pct_lo) != "ok":
                metrics["not_locked_out_pct"] = None
                median = float(oaf[valid].median())
                caveats.append(
                    f"design minimum OA unknown: OA above the high limit sits at a median "
                    f"{median:.0f}% -- excess if the minimum is ~{lo:g}% ({pct_lo:.0f}% of "
                    f"samples), normal for a design minimum near {median:.0f}%; pass min_oa_pct "
                    "to judge"
                )
                return Finding(
                    rule=self.name,
                    equip=equip,
                    severity="info",
                    metrics=metrics,
                    summary=(
                        f"{equip}: economizer lockout not judged -- OA above the "
                        f"{self.high_limit_f:g}°F high limit (median {median:.0f}%) depends on "
                        "the unknown design minimum"
                    ),
                    caveats=caveats,
                )
            if sev == "ok":
                caveats.append(
                    f"design minimum OA unknown: judged against {hi:g}% (a generous bound) and "
                    f"{lo:g}% (generic); excess OA stayed below both"
                )
        metrics["not_locked_out_pct"] = round(pct, 2)
        return Finding(
            rule=self.name,
            equip=equip,
            severity=sev,
            metrics=metrics,
            summary=(
                f"{equip}: excess OA above the {self.high_limit_f:g}°F high limit "
                f"{pct:.0f}% of those samples ({basis}: {thresh}; economizer not locked out)"
            ),
            caveats=caveats,
        )

    def evidence(self, equip: str, frame: pd.DataFrame):
        """Pattern J: the judged quantity vs OAT against *this rule's* configured envelope.

        The chart is built from the same samples, basis and threshold as the verdict: the configured
        ``high_limit_f``, the differential changeover (excluded samples are not drawn), and the
        OA-fraction / damper threshold of :meth:`excess_threshold`. Below the high limit the rule
        makes no claim, so the band spans every value there; above it the band tops out at the
        threshold -- the red points are exactly the samples counted as *not locked out*.
        """
        import numpy as np

        from ..charts.diagnostic import DiagnosticTemplate
        from ..charts.evidence import Evidence

        if Role.OAT not in frame.columns:
            return None
        j = self._judged(frame)
        y = j["y"]
        # draw the judged (hot) samples plus the at/below-limit side the rule makes no claim about;
        # differential-excluded and implausible samples are left out
        keep = j["valid"] | (~(j["oat"] > self.high_limit_f) & j["plausible"] & j["oat"].notna())
        col = "oa_damper_frac" if j["basis"] == "damper position" else "oa_fraction_pct"
        derived = pd.DataFrame({Role.OAT: j["oat"], col: y.where(keep)})
        vals = derived[col].dropna()
        if vals.empty:
            return None
        thr = self.excess_threshold(j["basis"])
        floor = min(float(vals.min()), 0.0) - (0.05 if col == "oa_damper_frac" else 5.0)
        top = max(float(vals.max()), thr) + (0.05 if col == "oa_damper_frac" else 5.0)
        hl = float(self.high_limit_f)

        def expected(xv):
            xv = np.asarray(xv, dtype=float)
            return np.full(len(xv), floor), np.where(xv > hl, thr, top)

        unit = "0–1" if col == "oa_damper_frac" else "%"
        pct = "" if unit == "0–1" else "%"
        tmpl = DiagnosticTemplate(
            f"economizer lockout (high limit {hl:g}°F, excess > {thr:g}{pct})",
            Role.OAT,
            col,
            expected,
            "OAT (°F)",
            f"{j['basis']} ({unit})",
            "the rule's own parameters",
        )
        return Evidence(
            renderer="diagnostic",
            template=tmpl,
            frame=derived,
            title=f"{equip}: economizer high-limit",
        )
