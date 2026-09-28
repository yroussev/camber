"""Rule: cooling-tower fouling read from **fan effort** at matched load and wet-bulb (0.92, #14).

:class:`~camber.rules.coolingtower_drift_rule.CoolingTowerApproachDrift` watches the approach, and
that is the right signal for a tower whose fans are fixed or saturated. But most towers run their
fans under a leaving-water controller (often "wet-bulb + a set approach", with a floor), and a
controlled tower that fouls **keeps its approach and works its fans harder**. On the LBNL chiller
plant the 65 % fouled tower moved its high-fan median approach only about 1 F while its fan spent
3.3 times the hours at or above 90 % speed. This rule measures that effort::

    fan speed (%)  ~  condenser load  +  wet-bulb        (fitted on the tower's own baseline)

against a **frozen** baseline, and flags the current period's fan speed running above it at matched
load and wet-bulb. It is **one-sided**: fouling (fill scale, plugged nozzles, a slipping belt) only
ever makes the fans work harder for the same heat. The condenser load is the tower's own range
(``cw_return_temp - cw_supply_temp``: heat rejected per unit of the usually constant condenser
flow) when both are trended, else the chilled-water load in tons from the CHW flow and temperatures.
Wet-bulb is measured, or derived from OAT + RH (Stull).

**A biased leaving-water sensor drives the fan the same way.** A tower controller that reads its
water 2 F warm cools the real water 2 F further and spends the effort to do it (on the LBNL plant
the +2 F tower-sensor-bias run moved fan effort *more* than 65 % fouling). So when the water
entering the chillers is also trended (:attr:`Role.COND_ENTERING_WATER_TEMP`, equal to the tower's
leaving water with the bypass shut), the rule compares the two sensors' offset in the baseline and
the current period: a shift of ``sensor_shift_f`` or more means the leaving-water sensor moved, and
the effort rise is reported as a sensor problem (``info``, ``attribution="sensor_offset"``), not
fouling. Without that second sensor the rule says it cannot rule a sensor bias out.

Thresholds are screening-grade (:mod:`camber.driftthresholds`): the median fan-speed rise at matched
conditions must reach ``warn_pct`` (5 %-points) for a warn and ``fault_pct`` (10) for a fault. They
were set from the size of change a technician would act on, not tuned on the validation data.
Store-injected like the other drift rules; run it via ``run_periods`` or the ``tower`` family.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from ..chillerbaseline import (
    fit_load_baseline,
    load_drift_stats,
    size_relative_load_gates,
    unscoreable_reason,
)
from ..coolingtower import stull_wetbulb_f
from ..driftthresholds import threshold_confidence
from ..model.roles import Role
from ..units import normalize_percent
from .base import Finding

_KIND = "cooling_tower_fan_effort"
_FAN = "fan_pct"
_LOAD = "load"
_WB = "wetbulb"

FAN_WARN_PCT = 5.0  # screening-grade: median %-point rise at matched load and wet-bulb
FAN_FAULT_PCT = 10.0  # screening-grade
SENSOR_SHIFT_F = 1.0  # leaving-water vs condenser-entering offset shift that means a sensor moved
FAN_RUNNING_PCT = 5.0  # a fan at or below this is off / at its minimum hold
HIGH_FAN_PCT = 90.0
_MIN_SAMPLES = 30
_WB_MIN_SPAN_F = 2.0
_BYPASS_SHUT_PCT = 2.0


class CoolingTowerFanEffortDrift:
    """Detects a cooling tower's fans working harder at matched load and wet-bulb (fouling).

    Provisional (0.92). Required: the tower fan speed and its leaving-water temperature, plus a
    load (condenser return temperature, or CHW flow + supply + return) and a wet-bulb (measured,
    or OAT + RH). Optional: the condenser water entering the chillers (the sensor cross-check) and
    the tower-bypass valve (to read that cross-check with the bypass shut).
    """

    name = "cooling_tower_fan_effort_drift"
    roles_required = (Role.TOWER_FAN_SPEED, Role.CW_SUPPLY_TEMP)
    roles_optional = (
        Role.CW_RETURN_TEMP,
        Role.CHW_FLOW,
        Role.CHW_SUPPLY_TEMP,
        Role.CHW_RETURN_TEMP,
        Role.WETBULB_TEMP,
        Role.OAT,
        Role.OUTDOOR_RH,
        Role.COND_ENTERING_WATER_TEMP,
        Role.CW_BYPASS_VALVE,
    )

    def __init__(
        self,
        store,
        *,
        site: str = "",
        run_id: str = "",
        freeze_if_missing: bool = True,
        warn_pct: float = FAN_WARN_PCT,  # screening-grade -- see the module note
        fault_pct: float = FAN_FAULT_PCT,  # screening-grade
        sensor_shift_f: float = SENSOR_SHIFT_F,
        elevation_ft: float | None = None,
    ):
        self.store = store
        self.site = site
        self.run_id = run_id
        self.freeze_if_missing = freeze_if_missing
        self.warn_pct = warn_pct
        self.fault_pct = fault_pct
        self.sensor_shift_f = sensor_shift_f
        self.elevation_ft = elevation_ft

    # ------------------------------------------------------------------ inputs
    @staticmethod
    def _num(frame, role) -> pd.Series:
        return pd.to_numeric(frame[role], errors="coerce")

    def load_source(self, frame: pd.DataFrame) -> str | None:
        """``"cw_range"`` (tower range), ``"chw_tons"`` or ``None``."""
        if Role.CW_RETURN_TEMP in frame.columns:
            return "cw_range"
        if all(
            r in frame.columns for r in (Role.CHW_FLOW, Role.CHW_SUPPLY_TEMP, Role.CHW_RETURN_TEMP)
        ):
            return "chw_tons"
        return None

    def _wetbulb(self, frame: pd.DataFrame) -> pd.Series | None:
        if Role.WETBULB_TEMP in frame.columns:
            return self._num(frame, Role.WETBULB_TEMP)
        if Role.OAT in frame.columns and Role.OUTDOOR_RH in frame.columns:
            return pd.Series(
                stull_wetbulb_f(
                    self._num(frame, Role.OAT),
                    self._num(frame, Role.OUTDOOR_RH),
                    elevation_ft=self.elevation_ft,
                ),
                index=frame.index,
            )
        return None

    def _prepared(self, frame: pd.DataFrame, source: str) -> pd.DataFrame:
        """Fan-running samples: fan %, load and wet-bulb."""
        fan = normalize_percent(self._num(frame, Role.TOWER_FAN_SPEED))
        if source == "cw_range":
            load = self._num(frame, Role.CW_RETURN_TEMP) - self._num(frame, Role.CW_SUPPLY_TEMP)
        else:
            dt = self._num(frame, Role.CHW_RETURN_TEMP) - self._num(frame, Role.CHW_SUPPLY_TEMP)
            load = self._num(frame, Role.CHW_FLOW) * dt / 24.0
        out = pd.DataFrame({_FAN: fan, _LOAD: load}, index=frame.index)
        wb = self._wetbulb(frame)
        if wb is not None:
            out[_WB] = wb
        return out[out[_FAN] > FAN_RUNNING_PCT]

    def _sensor_offset(self, frame: pd.DataFrame) -> float | None:
        """Median tower-leaving minus condenser-entering water, bypass shut, fan running."""
        if Role.COND_ENTERING_WATER_TEMP not in frame.columns:
            return None
        d = self._num(frame, Role.CW_SUPPLY_TEMP) - self._num(frame, Role.COND_ENTERING_WATER_TEMP)
        ok = normalize_percent(self._num(frame, Role.TOWER_FAN_SPEED)) > FAN_RUNNING_PCT
        if Role.CW_BYPASS_VALVE in frame.columns:
            ok &= normalize_percent(self._num(frame, Role.CW_BYPASS_VALVE)) <= _BYPASS_SHUT_PCT
        d = d[ok].dropna()
        return float(d.median()) if len(d) >= _MIN_SAMPLES else None

    # ------------------------------------------------------------------ baseline
    def _freeze(self, equip, base: pd.DataFrame, gate, caveats: list):
        frozen = self.store.model_for(self.site, equip, _KIND)
        if frozen is not None:
            return frozen
        if not self.freeze_if_missing:
            caveats.append(f"could not evaluate {_KIND}: no frozen baseline and freezing is off")
            return None
        common: dict[str, Any] = dict(
            metric_col=_FAN,
            load_col=_LOAD,
            min_load=gate[0],
            metric_range=(FAN_RUNNING_PCT, 101.0),
            min_samples=_MIN_SAMPLES,
            min_load_span=gate[1],
            level_fallback=True,
        )
        fit = None
        if _WB in base.columns:
            fit = fit_load_baseline(
                base, covariate_col=_WB, min_covariate_span=_WB_MIN_SPAN_F, **common
            )
            if fit is None:
                caveats.append(
                    "the wet-bulb effect on fan speed could not be identified in the baseline; "
                    "normalized on load only"
                )
        else:
            caveats.append("no wet-bulb (measured, or OAT + RH): normalized on load only")
        if fit is None:
            fit = fit_load_baseline(base, **common)
        if fit is None:
            why = unscoreable_reason(
                base,
                metric_col=_FAN,
                load_col=_LOAD,
                min_load=gate[0],
                metric_range=common["metric_range"],
                min_samples=_MIN_SAMPLES,
                metric_name="tower fan speed",
                load_name="condenser load",
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

    # ------------------------------------------------------------------ the rule
    def _decline(self, equip, reason, caveats, why) -> Finding:
        return Finding(
            rule=self.name,
            equip=equip,
            severity="info",
            metrics={"declined": True, "reason": reason},
            summary=f"{equip}: declined -- {why}",
            caveats=caveats,
        )

    def analyze_periods(self, equip: str, baseline: pd.DataFrame, current: pd.DataFrame) -> Finding:
        """Score the current period's tower fan effort against the frozen baseline."""
        caveats: list = []
        src_b, src_c = self.load_source(baseline), self.load_source(current)
        if src_b is None or src_b != src_c:
            return self._decline(
                equip,
                "no_load",
                [
                    "could not evaluate tower fan effort: the condenser load needs the condenser "
                    "return temperature, or CHW flow + supply + return, in both periods"
                ],
                "no condenser load to match on",
            )
        base_t, cur_t = self._prepared(baseline, src_b), self._prepared(current, src_c)
        gate = size_relative_load_gates(
            base_t[_LOAD],
            min_load=0.5 if src_b == "cw_range" else None,
            min_load_span=2.0 if src_b == "cw_range" else None,
        )
        frozen = self._freeze(equip, base_t, gate, caveats)
        if frozen is None:
            return self._decline(equip, "no_baseline", caveats, "no frozen fan-effort baseline")
        cov = _WB if frozen.covariate else None
        if cov is not None and cov not in cur_t.columns:
            caveats.append("the baseline is normalized on wet-bulb, absent from the current period")
            return self._decline(equip, "no_wetbulb", caveats, "no wet-bulb in the current period")
        drift = load_drift_stats(
            frozen,
            cur_t,
            metric_col=_FAN,
            load_col=_LOAD,
            min_load=gate[0],
            metric_range=(FAN_RUNNING_PCT, 101.0),
            min_samples=_MIN_SAMPLES,
            covariate_col=cov,
        )
        if drift is None:
            return self._decline(
                equip, "nothing_scoreable", caveats, "no scoreable fan-running hours"
            )

        rise = drift.drift_f
        if rise >= self.fault_pct:
            severity = "fault"
        elif rise >= self.warn_pct:
            severity = "warn"
        else:
            severity = "ok"

        hi_b = float((base_t[_FAN] >= HIGH_FAN_PCT).mean()) if len(base_t) else float("nan")
        hi_c = float((cur_t[_FAN] >= HIGH_FAN_PCT).mean()) if len(cur_t) else float("nan")
        off_b, off_c = self._sensor_offset(baseline), self._sensor_offset(current)
        shift = None if off_b is None or off_c is None else off_c - off_b
        attribution = "tower"
        if severity != "ok":
            if shift is None:
                caveats.append(
                    "no condenser-entering water temperature to cross-check the tower's "
                    "leaving-water sensor: a sensor reading warm drives the fans the same way"
                )
            elif abs(shift) >= self.sensor_shift_f:
                attribution = "sensor_offset"
                severity = "info"
                caveats.append(
                    f"the tower's leaving-water sensor moved {shift:+.1f}F against the "
                    "condenser-entering sensor since the baseline: the fans are chasing a "
                    "biased reading -- recalibrate the sensor before cleaning the tower"
                )
        if drift.extrapolated:
            caveats.append(
                "over 10% of the current period ran outside the baseline's load envelope, so part "
                "of this drift is extrapolated"
            )
        metrics = {
            "fan_effort_drift_pct": round(rise, 2),
            "fan_effort_drift_sigma": drift.drift_sigma,
            "fan_baseline_sigma_pct": frozen.sigma_f,
            "fan_effort_slope_pct_per_month": drift.slope_f_per_month,
            "n_current": drift.n_current,
            "load_source": src_b,
            "covariate": frozen.covariate,
            "baseline_model": frozen.load_model,
            "high_fan_share_baseline": round(hi_b, 4) if hi_b == hi_b else None,
            "high_fan_share_current": round(hi_c, 4) if hi_c == hi_c else None,
            "sensor_offset_shift_f": None if shift is None else round(shift, 2),
            "attribution": attribution,
        }
        metrics.update(threshold_confidence(magnitude=True, temporal=False))
        matched = "matched load and wet-bulb" if frozen.covariate else "matched load"
        if attribution == "sensor_offset":
            summary = (
                f"{equip}: tower fans {rise:+.1f}%-pts at {matched}, but the leaving-water sensor "
                f"shifted {shift:+.1f}F -- a sensor bias, not fouling"
            )
        else:
            summary = (
                f"{equip}: tower fan speed {rise:+.1f}%-pts vs frozen baseline at {matched}; "
                f"hours at >= {HIGH_FAN_PCT:g}% {hi_b:.0%} -> {hi_c:.0%}"
            )
            if severity != "ok":
                summary += " -- the tower is working harder for the same heat (fouling?)"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics=metrics,
            summary=summary,
            caveats=caveats,
        )
