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

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        oat = frame[Role.OAT]
        hot = (oat > self.high_limit_f) & oat.notna()
        caveats: list = []
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
        oaf = None

        if measured is not None:
            oaf = measured
            valid = hot & oaf.notna()
            basis = "measured OA fraction"
        elif have_temps:
            # Temperature-balance OA-fraction (percent), same method + guards as camber.oafraction.
            mat, rat = frame[Role.MIXED_AIR_TEMP], frame[Role.RETURN_AIR_TEMP]
            denom = rat - oat
            oaf = 100.0 * (rat - mat) / denom
            plausible = (
                oat.between(20, 130)
                & mat.between(30, 120)
                & rat.between(40, 110)
                & (denom.abs() >= self.denom_min_f)
                & oaf.between(-20, 120)
            )
            valid = hot & plausible
            basis = "OA-fraction"
        else:
            # OA_DAMPER arrives 0-1 or 0-100 depending on the BAS; canonicalize to a fraction
            # so the fraction threshold is correct either way. (The role pipeline scales percent
            # roles to 0-100 -- comparing that against a 0-1 threshold makes every open damper
            # read "not locked out", the original mis-scaling behind this rule's false faults.)
            damper = normalize_percent(frame[Role.OA_DAMPER]) / 100.0
            valid = hot & damper.notna()
            basis = "damper position"
            caveats.append(
                "no mixed/return-air temps: judged on damper position "
                "(a weak proxy for outside-air fraction)"
            )
        n = int(valid.sum())

        if n == 0:
            msg = f"{equip}: no hours above the {self.high_limit_f:g}°F high limit"
            if basis != "damper position":
                msg += " with a usable OA-fraction"
            return Finding(
                rule=self.name, equip=equip, severity="info", summary=msg, caveats=caveats
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
        }
        if oaf is None:  # damper fallback
            not_locked = valid & (damper > self.min_damper + 0.05)
            pct = 100.0 * float(not_locked.sum()) / n
            sev = _sev(pct)
            thresh = f"damper > {self.min_damper:.0%}"
        elif self.min_oa_pct is not None:
            pct = 100.0 * float((valid & (oaf > self.min_oa_pct + self.oa_margin_pct)).sum()) / n
            sev = _sev(pct)
            thresh = f"OAF > {self.min_oa_pct:g}%"
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
            thresh = f"OAF > {hi:g}%"
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
        """Pattern J: OA damper vs OAT against the economizer expectation."""
        from ..charts.diagnostic import TEMPLATES
        from ..charts.evidence import Evidence

        return Evidence(
            renderer="diagnostic",
            template=TEMPLATES["economizer"],
            title=f"{equip}: economizer high-limit",
        )
