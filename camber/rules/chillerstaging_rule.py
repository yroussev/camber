"""Rule: chiller staging / cycling (starts-per-day + low part-load; PNNL/ASHRAE).

Flags a chiller short-cycling (excess starts per day) and/or running persistently at
low part-load -- both signs of an oversized machine or mis-set staging thresholds,
costing efficiency and compressor life. Adapts
:func:`camber.chillerstaging.analyze_chiller_staging` to the role-frame interface.
``max_starts_per_day`` is the cycling threshold (manufacturer minimum-cycle-time
dependent), so it is a constructor parameter, not a baked constant.

0.108 (#235): the chiller reads as running by its run status / command (``compressor_status``)
when mapped, else by power above ``run_frac`` (a tenth) of its own 95th-percentile draw, else above
an explicit ``min_power_kw``. Before 0.108 it was a fixed 2 kW, so a stopped chiller's standby draw
(controls, oil and crankcase heaters) read as running and its starts went uncounted.
"""

from __future__ import annotations

import pandas as pd

from ..boilercycle import _interval_min
from ..chillerstaging import analyze_chiller_staging
from ..model.roles import Role
from ..schedules import CHILLER_POWER_RUN_FRAC
from .base import Finding

#: 0.105 (#148): running tons whose 10th-90th percentile spread is within this share (%) of the
#: observed peak are "one output": every run at the same load, give or take rounding jitter.
_CONSTANT_OUTPUT_SPREAD_PCT = 5.0

_ROLE_TO_COL = {
    Role.POWER: "Power",
    Role.CHW_SUPPLY_TEMP: "CHWS_Temp",
    Role.CHW_RETURN_TEMP: "CHWR_Temp",
    Role.CHW_FLOW: "CHW_Flow",
    Role.COMPRESSOR_STATUS: "Status",  # 0.108 (#235): the run gate
}


class ChillerStaging:
    """Detects chiller short-cycling and/or sustained low part-load (PNNL Re-tuning Ch.8)."""

    name = "chiller_staging"
    roles_required = (Role.POWER,)
    roles_optional = (
        Role.CHW_SUPPLY_TEMP,
        Role.CHW_RETURN_TEMP,
        Role.CHW_FLOW,
        Role.COMPRESSOR_STATUS,  # 0.108 (#235): the run gate
    )

    def __init__(
        self,
        max_starts_per_day: float = 6.0,
        low_load_pct: float = 40.0,
        max_interval_min: float = 15.0,
        min_power_kw: float | None = None,
        run_frac: float = CHILLER_POWER_RUN_FRAC,
    ):
        # Cycling threshold is manufacturer/min-cycle-time dependent; low_load_pct is
        # the "idling" judgment for the optional load-factor metric.
        self.max_starts_per_day = max_starts_per_day
        self.low_load_pct = low_load_pct
        # 0.104 (#143): coarser than this, the start count is caveated as a floor
        self.max_interval_min = max_interval_min
        # 0.108 (#235): the power run gate -- an explicit kW floor, else run_frac x p95 power
        self.min_power_kw = min_power_kw
        self.run_frac = run_frac

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        cols = {r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns}
        legacy = frame.rename(columns=cols)
        res = analyze_chiller_staging(
            legacy,
            equip,
            min_load_pct=self.low_load_pct,
            min_power_kw=self.min_power_kw,
            run_frac=self.run_frac,
        )
        if res is None:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                summary="insufficient data (need chiller power)",
            )
        cyc = res.starts_per_day
        low = res.low_load_pct if res.low_load_pct == res.low_load_pct else 0.0  # NaN-safe
        if cyc >= 2 * self.max_starts_per_day or low >= 50.0:
            severity = "fault"
        elif cyc >= self.max_starts_per_day or low >= 25.0:
            severity = "warn"
        else:
            severity = "ok"
        load_note = (
            f", load-factor median {res.load_factor_median_pct:.0f}% "
            f"({res.low_load_pct:.0f}% of run hrs below {self.low_load_pct:.0f}%)"
            if res.low_load_pct == res.low_load_pct
            else ""
        )
        # 0.104 (#143): an averaged power trend hides an off-on cycle shorter than its interval;
        # the chiller has no part-on status to show it, so the count is caveated as a floor
        dt = _interval_min(legacy["Power"].dropna().index) if "Power" in legacy else None
        caveats = []
        if dt is not None and dt > self.max_interval_min:
            caveats.append(
                f"starts counted on a {dt:g}-minute trend, coarser than the "
                f"{self.max_interval_min:g} minutes the count needs (max_interval_min): an "
                "averaged power reading hides an off-and-on cycle shorter than the interval, so "
                "starts_per_day is a floor; run at the stored interval for the true count"
            )
        # 0.105 (#148): a chiller that only ever runs at one output (its minimum, say) reads a
        # 100 % load factor, since that output is also its observed peak; the tons say so
        spread = res.load_spread_pct
        if spread == spread and spread <= _CONSTANT_OUTPUT_SPREAD_PCT:  # NaN-safe
            caveats.append(
                f"every run is at about the same output (the 10th-90th percentile of the running "
                f"tons spans {spread:g} % of their peak): the load factor is read against that "
                "same output, so it says nothing about part load -- compare the tons with the "
                "chiller's rated capacity"
            )
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            caveats=caveats,
            metrics={
                "starts_per_day": res.starts_per_day,
                "max_starts_per_day": self.max_starts_per_day,
                "runtime_pct": res.runtime_pct,
                "load_factor_median_pct": res.load_factor_median_pct,
                "low_load_pct": res.low_load_pct,
                "load_spread_pct": res.load_spread_pct,  # 0.105 (#148)
                "n_days": res.n_days,
                "run_source": res.run_source,  # 0.108 (#235)
                "run_threshold_kw": res.run_threshold_kw,
            },
            summary=(
                f"{equip}: {res.starts_per_day:.1f} starts/day "
                f"(threshold {self.max_starts_per_day:.0f}), running "
                f"{res.runtime_pct:.0f}% of the time{load_note}"
            ),
        )
