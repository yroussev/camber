"""Rule: boiler **combustion-efficiency** drift -- gas in per unit of heat out (0.92, #13).

A boiler whose fire side sooted up, or whose water side scaled, burns more gas for the same heat
delivered: its input/output ratio (the reciprocal of its efficiency) creeps up. No other CAMBER
detector watches this -- the loop delta-T, short-cycle and summer-lockout rules all look at the
water side, which a fouled boiler keeps in spec by firing harder. This rule compares::

    ratio = gas input rate / heat delivered            (1 / efficiency)

against a **frozen** baseline fitted over the boiler's own firing hours, at matched **load** (heat
delivered; part-load efficiency varies) and, where it moves, matched **return-water temperature**
(a condensing boiler's efficiency falls as its return water warms). It is **one-sided**: fouling
only ever raises the ratio.

Heat delivered is ``500 x gpm x (supply - return) / 3412.14`` kW from the hot-water flow when it is
mapped; without a flow meter, the pump speed times the loop delta-T stands in (a flow index -- the
affinity law's flow ~ speed), which is enough because the drift is judged as a **relative** change:
the ratio's units cancel, so a gas rate in any consistent unit works.

**The water-side sensors can fake it.** A supply- or return-temperature bias moves the computed
heat, and with it the ratio, without the boiler burning any more gas. So when outdoor-air
temperature is available a second frozen model -- gas input against OAT, the building's heating
signature -- corroborates: fouling raises the gas burned at matched weather, a sensor bias does not.
A ratio rise the gas bill does not share is reported as a heat-metering problem (``info``, with the
sensors named), not as a fouled boiler. Without OAT the rule cannot tell the two apart and caps its
severity at ``warn``.

Like the other drift rules this takes an injected
:class:`~camber.store.modelstore.BaselineStore`, so it is not auto-registered in
:func:`camber.rules.builtin.builtin_registry`; run it via
:meth:`camber.rules.base.Registry.run_periods` or the ``boiler`` drift family
(:mod:`camber.driftrun`). Thresholds are screening-grade (see :mod:`camber.driftthresholds`).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ..chillerbaseline import fit_load_baseline, load_drift_stats, unscoreable_reason
from ..driftthresholds import threshold_confidence
from ..model.roles import Role
from ..units import normalize_percent
from .base import Finding

_KIND = "boiler_efficiency"
_SIG_KIND = "boiler_gas_signature"
_RATIO = "input_output_ratio"
_LOAD = "heat"  # heat delivered (kW, or a flow-index x delta-T without a flow meter)

# ---------------------------------------------------------------------------------------------
# MAGNITUDE FLOORS -- SCREENING-GRADE (see camber.driftthresholds).
#
# Relative rise of the gas-in / heat-out ratio at matched load. 5 % is about four efficiency points
# on an 80 %-efficient boiler -- the size of change a combustion tune-up or a tube cleaning is
# usually justified on; 15 % is a boiler burning a sixth more gas than it did. A finding must also
# clear a sigma floor on the baseline's own scatter, so a noisy plant does not alarm on 5 %.
# Characterized from the physics of the signal, NOT tuned on the validation data.
# ---------------------------------------------------------------------------------------------
RATIO_WARN_REL = 0.05  # screening-grade
RATIO_FAULT_REL = 0.15  # screening-grade
RATIO_WARN_SIGMA = 1.5  # screening-grade
RATIO_FAULT_SIGMA = 3.0  # screening-grade
#: Gas at matched outdoor temperature must rise by at least this share of the ratio rise (and by
#: at least ``CORROBORATION_MIN_REL``) for a ratio rise to be read as the boiler, not its sensors.
#: Fouling moves both (the building needs the same heat, the boiler burns more for it); a biased
#: supply/return sensor moves only the ratio.
CORROBORATION_SHARE = 1.0 / 3.0
CORROBORATION_MIN_REL = 0.02

_MIN_DT_F = 0.5  # a loop delta-T below this carries no heat information
_MIN_SAMPLES = 30
_COV_MIN_SPAN_F = 2.0  # return-water temperature must move this much to be a covariate
_OAT_MIN_SPAN_F = 10.0  # outdoor temperature must span this much to fit a heating signature
BTU_PER_KWH = 3412.14


class BoilerEfficiencyDrift:
    """Detects a boiler's gas input per unit of heat delivered drifting **up** (fouling).

    Provisional (0.92). Required: the gas input rate (:attr:`Role.GAS_INPUT_RATE`) and the hot-water
    supply and return temperatures; a flow (:attr:`Role.HW_FLOW`) or, failing that, a pump speed
    (:attr:`Role.HW_PUMP_SPEED`) turns the delta-T into heat. OAT corroborates; a boiler status
    narrows the firing hours.
    """

    name = "boiler_efficiency_drift"
    roles_required = (Role.GAS_INPUT_RATE, Role.HW_SUPPLY_TEMP, Role.HW_RETURN_TEMP)
    roles_optional = (Role.HW_FLOW, Role.HW_PUMP_SPEED, Role.OAT, Role.BOILER_STATUS)

    def __init__(
        self,
        store,
        *,
        site: str = "",
        run_id: str = "",
        freeze_if_missing: bool = True,
        warn_rel: float = RATIO_WARN_REL,  # screening-grade -- see the module note
        fault_rel: float = RATIO_FAULT_REL,  # screening-grade
        warn_sigma: float = RATIO_WARN_SIGMA,  # screening-grade
        fault_sigma: float = RATIO_FAULT_SIGMA,  # screening-grade
        corroboration_share: float = CORROBORATION_SHARE,
    ):
        self.store = store
        self.site = site
        self.run_id = run_id
        self.freeze_if_missing = freeze_if_missing
        self.warn_rel = warn_rel
        self.fault_rel = fault_rel
        self.warn_sigma = warn_sigma
        self.fault_sigma = fault_sigma
        self.corroboration_share = corroboration_share

    # ------------------------------------------------------------------ inputs
    @staticmethod
    def heat_source(frame: pd.DataFrame) -> str | None:
        """``"flow"`` (kW from the HW flow), ``"pump_speed"`` (a flow index) or ``None``."""
        if Role.HW_FLOW in frame.columns and pd.to_numeric(frame[Role.HW_FLOW]).notna().any():
            return "flow"
        if Role.HW_PUMP_SPEED in frame.columns:
            if pd.to_numeric(frame[Role.HW_PUMP_SPEED]).notna().any():
                return "pump_speed"
        return None

    def _prepared(self, frame: pd.DataFrame, source: str) -> pd.DataFrame:
        """Firing samples with ``heat``, ``gas``, the ratio, return temperature and OAT."""
        num = lambda r: pd.to_numeric(frame[r], errors="coerce")  # noqa: E731
        gas = num(Role.GAS_INPUT_RATE)
        dt = num(Role.HW_SUPPLY_TEMP) - num(Role.HW_RETURN_TEMP)
        if source == "flow":
            heat = 500.0 * num(Role.HW_FLOW) * dt / BTU_PER_KWH
        else:
            heat = normalize_percent(num(Role.HW_PUMP_SPEED)) * dt
        out = pd.DataFrame(
            {_LOAD: heat, "gas": gas, Role.HW_RETURN_TEMP: num(Role.HW_RETURN_TEMP)},
            index=frame.index,
        )
        if Role.OAT in frame.columns:
            out[Role.OAT] = num(Role.OAT)
        firing = gas > 0
        if Role.BOILER_STATUS in frame.columns:
            status = num(Role.BOILER_STATUS)
            if status.notna().any():
                firing &= status > 0.5
        out = out[firing & (dt > _MIN_DT_F) & (heat > 0)]
        out[_RATIO] = out["gas"] / out[_LOAD]
        return out

    # ------------------------------------------------------------------ baselines
    def _ratio_range(self, base: pd.DataFrame) -> tuple[float, float]:
        """Plausible ratio band: a third to three times the baseline median (units cancel)."""
        med = float(base[_RATIO].median()) if len(base) else float("nan")
        if not med > 0:
            return (0.0, float("inf"))
        return (med / 3.0, med * 3.0)

    def _freeze(self, equip, base: pd.DataFrame, caveats: list):
        frozen = self.store.model_for(self.site, equip, _KIND)
        if frozen is not None:
            return frozen
        if not self.freeze_if_missing:
            caveats.append(f"could not evaluate {_KIND}: no frozen baseline and freezing is off")
            return None
        heat = base[_LOAD]
        min_load = 0.05 * float(heat.quantile(0.95)) if len(heat) else 0.0
        common: dict[str, Any] = dict(
            metric_col=_RATIO,
            load_col=_LOAD,
            min_load=min_load,
            metric_range=self._ratio_range(base),
            min_samples=_MIN_SAMPLES,
            min_load_span=0.10 * float(heat.quantile(0.95)) if len(heat) else 0.0,
            level_fallback=True,
        )
        fit = fit_load_baseline(
            base,
            covariate_col=Role.HW_RETURN_TEMP,
            min_covariate_span=_COV_MIN_SPAN_F,
            **common,
        )
        if fit is not None and fit.covariate_slope <= 0:
            caveats.append(
                "return-water temperature is mapped but the baseline gave it the physically "
                "wrong sign (efficiency should fall as return water warms); normalized on load "
                "only"
            )
            fit = None
        elif fit is None:
            caveats.append(
                "return-water temperature barely moved in the baseline (or its effect was not "
                "identified); normalized on load only"
            )
        if fit is None:
            fit = fit_load_baseline(base, **common)
        if fit is None:
            why = unscoreable_reason(
                base,
                metric_col=_RATIO,
                load_col=_LOAD,
                min_load=min_load,
                metric_range=common["metric_range"],
                min_samples=_MIN_SAMPLES,
                metric_name="gas-in / heat-out ratio",
                load_name="heat delivered",
            )
            caveats.append(
                f"could not evaluate {_KIND}: the baseline would not support a fit: {why}"
            )
            return None
        idx = base.index
        self.store.freeze(
            fit,
            site=self.site,
            equip=equip,
            kind=_KIND,
            frozen_at=self.run_id,
            period=(str(idx.min()), str(idx.max())) if len(idx) else ("", ""),
            reason="initial baseline frozen from the supplied baseline period",
        )
        return fit

    def _freeze_signature(self, equip, base: pd.DataFrame, caveats: list):
        """Gas input against OAT over the baseline's firing hours (the corroborating model)."""
        frozen = self.store.model_for(self.site, equip, _SIG_KIND)
        if frozen is not None or not self.freeze_if_missing or Role.OAT not in base.columns:
            return frozen
        fit = fit_load_baseline(
            base,
            metric_col="gas",
            load_col=Role.OAT,
            min_load=float("-inf"),
            metric_range=(0.0, float("inf")),
            min_samples=_MIN_SAMPLES,
            min_load_span=_OAT_MIN_SPAN_F,
        )
        if fit is None:
            caveats.append(
                "could not fit the gas-vs-outdoor-temperature signature over the baseline (OAT "
                f"spanned under {_OAT_MIN_SPAN_F:g}F of firing hours, or too few samples); the "
                "ratio rise is not corroborated"
            )
            return None
        idx = base.index
        self.store.freeze(
            fit,
            site=self.site,
            equip=equip,
            kind=_SIG_KIND,
            frozen_at=self.run_id,
            period=(str(idx.min()), str(idx.max())) if len(idx) else ("", ""),
            reason="initial gas signature frozen from the supplied baseline period",
        )
        return fit

    @staticmethod
    def _gas_rise(sig, cur: pd.DataFrame) -> float | None:
        """Mean gas residual at matched OAT, relative to the signature's prediction (unbiased)."""
        if sig is None or Role.OAT not in cur.columns:
            return None
        w = cur[["gas", Role.OAT]].dropna()
        if len(w) < _MIN_SAMPLES:
            return None
        pred = np.asarray(sig.predict(w[Role.OAT].to_numpy(dtype=float)), dtype=float)
        level = float(np.mean(pred))
        if not level > 0:
            return None
        return float(np.mean(w["gas"].to_numpy(dtype=float) - pred)) / level

    # ------------------------------------------------------------------ the rule
    def _decline(self, equip, reason: str, caveats: list, why: str) -> Finding:
        return Finding(
            rule=self.name,
            equip=equip,
            severity="info",
            metrics={"declined": True, "reason": reason},
            summary=f"{equip}: declined -- {why}",
            caveats=caveats,
        )

    def analyze_periods(self, equip: str, baseline: pd.DataFrame, current: pd.DataFrame) -> Finding:
        """Score the current period's gas-in / heat-out ratio against the frozen baseline."""
        caveats: list = []
        srcs = {self.heat_source(baseline), self.heat_source(current)}
        source = "flow" if srcs == {"flow"} else None
        if source is None and None not in srcs:
            both = all(
                Role.HW_PUMP_SPEED in f.columns and f[Role.HW_PUMP_SPEED].notna().any()
                for f in (baseline, current)
            )
            source = "pump_speed" if both else None
        if source is None:
            return self._decline(
                equip,
                "no_heat_measure",
                [
                    "could not evaluate boiler efficiency: heat delivered needs a hot-water flow "
                    "(hw_flow) or a pump speed (hw_pump_speed), in both periods, with the "
                    "supply/return temperatures"
                ],
                "no hot-water flow or pump speed to turn the delta-T into heat",
            )
        if source == "pump_speed":
            caveats.append(
                "no hot-water flow meter: heat delivered is indexed by pump speed x delta-T "
                "(flow ~ speed), so the ratio is relative only and a changed pump curve or valve "
                "line-up would read as efficiency"
            )
        base_t = self._prepared(baseline, source)
        cur_t = self._prepared(current, source)
        frozen = self._freeze(equip, base_t, caveats)
        if frozen is None:
            return self._decline(equip, "no_baseline", caveats, "no frozen boiler baseline")
        cov = Role.HW_RETURN_TEMP if frozen.covariate else None
        rng = self._ratio_range(base_t) if len(base_t) else (0.0, float("inf"))
        min_load = 0.05 * float(base_t[_LOAD].quantile(0.95)) if len(base_t) else 0.0
        drift = load_drift_stats(
            frozen,
            cur_t,
            metric_col=_RATIO,
            load_col=_LOAD,
            min_load=min_load,
            metric_range=rng,
            min_samples=_MIN_SAMPLES,
            covariate_col=cov,
        )
        if drift is None:
            why = unscoreable_reason(
                cur_t,
                metric_col=_RATIO,
                load_col=_LOAD,
                min_load=min_load,
                metric_range=rng,
                min_samples=_MIN_SAMPLES,
                covariate_col=cov,
                baseline=frozen,
                metric_name="gas-in / heat-out ratio",
                load_name="heat delivered",
            )
            caveats.append(
                f"could not evaluate {_KIND}: nothing scoreable in the current period: {why}"
            )
            return self._decline(equip, "nothing_scoreable", caveats, "no scoreable firing hours")

        heat_med = float(cur_t[_LOAD].median())
        level = float(np.asarray(frozen.predict(heat_med)))
        rel = drift.drift_f / level if level > 0 else float("nan")
        sig_ok = drift.drift_sigma == drift.drift_sigma
        if rel >= self.fault_rel and (not sig_ok or drift.drift_sigma >= self.fault_sigma):
            severity = "fault"
        elif rel >= self.warn_rel and (not sig_ok or drift.drift_sigma >= self.warn_sigma):
            severity = "warn"
        else:
            severity = "ok"

        sig = self._freeze_signature(equip, base_t, caveats)
        gas_rise = self._gas_rise(sig, cur_t)
        attribution = "boiler"
        if severity != "ok":
            need = max(CORROBORATION_MIN_REL, self.corroboration_share * rel)
            if gas_rise is None:
                attribution = "uncorroborated"
                if severity == "fault":
                    severity = "warn"
                caveats.append(
                    "severity capped at warn: without a gas-vs-outdoor-temperature signature the "
                    "rule cannot tell a fouled boiler from a biased supply/return sensor"
                )
            elif gas_rise < need:
                attribution = "heat_metering"
                severity = "info"
                caveats.append(
                    f"the ratio rose {rel:+.0%} but gas at matched outdoor temperature moved only "
                    f"{gas_rise:+.1%}: the heat measurement moved, not the boiler -- check the "
                    "hot-water supply/return sensors and the flow meter"
                )
        if drift.extrapolated:
            caveats.append(
                "over 10% of the current period ran outside the baseline's load envelope, so part "
                "of this drift is extrapolated"
            )
        metrics = {
            "ratio_drift": drift.drift_f,
            "ratio_drift_rel": round(rel, 4) if rel == rel else None,
            "ratio_drift_sigma": drift.drift_sigma,
            "ratio_baseline_level": round(level, 4),
            "ratio_baseline_sigma": frozen.sigma_f,
            "ratio_slope_per_month": drift.slope_f_per_month,
            "n_current": drift.n_current,
            "heat_source": source,
            "covariate": frozen.covariate,
            "baseline_model": frozen.load_model,
            "gas_rise_at_matched_oat": None if gas_rise is None else round(gas_rise, 4),
            "attribution": attribution,
        }
        metrics.update(threshold_confidence(magnitude=True, temporal=False))
        if attribution == "heat_metering":
            summary = (
                f"{equip}: gas-in/heat-out ratio {rel:+.0%} vs frozen baseline, but gas at matched "
                "OAT did not rise -- a heat-metering (HW temperature / flow sensor) problem, not "
                "boiler fouling"
            )
        else:
            summary = (
                f"{equip}: gas input per unit heat delivered {rel:+.1%} "
                f"({drift.drift_sigma:.1f}σ) vs frozen baseline at matched load"
                + (" and return-water temperature" if frozen.covariate else "")
                + (f"; gas at matched OAT {gas_rise:+.1%}" if gas_rise is not None else "")
            )
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics=metrics,
            summary=summary,
            caveats=caveats,
        )
