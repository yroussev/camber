"""Rule: heat-pump excess defrost / reversing-valve cycling.

An air-source heat pump reverses to cooling briefly to defrost the outdoor coil; a unit that
reverses far too often (iced coil, faulty defrost termination, or a hunting reversing valve)
wastes the heat it just delivered and drives up energy use. Counts reversing-valve transitions
per day. ``max_reversals_per_day`` is defrost-strategy dependent, so it is a constructor
parameter.

**Trend resolution (0.106, #185).** A defrost lasts a few minutes. Resampled to a coarser grid
the valve holds each bin's mean, so a 5-minute defrost in a 15-minute bin reads 0.67, which a
rounded count read as heating: the defrost vanished. The count now takes the fewest changes the
means allow (a part-way bin between two heating bins is a reversal out and back), and on a trend
coarser than ``max_interval_min`` it is a floor: a count at or above the limit stands with a
caveat; below it, part-way samples decline the verdict to ``info`` instead of ``ok``.
"""

from __future__ import annotations

import pandas as pd

from ..boilercycle import _interval_min
from ..model.roles import Role
from .base import Finding
from .compressor_stage_rule import _binary_changes, _coarse_trend_check


class HeatPumpDefrost:
    """Detects excess heat-pump defrost / reversing-valve cycling."""

    name = "heatpump_defrost"
    roles_required = (Role.REVERSING_VALVE_CMD,)
    roles_optional = (Role.COMPRESSOR_STATUS, Role.OAT)

    def __init__(self, max_reversals_per_day: float = 24.0, max_interval_min: float = 5.0):
        # Defrost at worst ~once an hour in cold weather, and each defrost is two reversing-valve
        # transitions (into cooling and back): up to ~48 transitions a day. 24/day (~12 defrosts,
        # one every 2 h) is a CAMBER screening ceiling, not a generous one.
        self.max_reversals_per_day = max_reversals_per_day
        # 0.106 (#185): the coarsest trend whose count can vouch for "ok"; a defrost lasts a few
        # minutes, so a coarser bin averages it away
        self.max_interval_min = max_interval_min

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        if Role.REVERSING_VALVE_CMD not in frame.columns:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                summary="insufficient data (need reversing-valve command)",
            )
        valve = frame[Role.REVERSING_VALVE_CMD]
        n_rev, per_day, span, n_partial = _binary_changes(valve)
        if span <= 0:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                summary="insufficient data (need reversing-valve command)",
            )
        if per_day >= 2 * self.max_reversals_per_day:
            severity = "fault"
        elif per_day >= self.max_reversals_per_day:
            severity = "warn"
        else:
            severity = "ok"
        interval = _interval_min(valve.dropna().index)
        severity, caveats, declined = _coarse_trend_check(
            severity,
            interval_min=interval,
            max_interval_min=self.max_interval_min,
            n_partial=n_partial,
            counted="reversing-valve changes",
            hidden="a defrost shorter than the interval",
            limit_name="max_reversals_per_day",
            limit=self.max_reversals_per_day,
        )
        summary = (
            f"{equip}: {per_day:.1f} reversing-valve cycles/day "
            f"(threshold {self.max_reversals_per_day:.0f}) — check defrost termination"
        )
        if declined:
            summary += f"; cannot confirm on a {interval:g}-minute trend"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "reversals_per_day": round(per_day, 2),
                "max_reversals_per_day": self.max_reversals_per_day,
                "n_reversals": n_rev,
                "n_days": round(span, 2),
                "interval_min": interval,  # 0.106 (#185)
                "n_partial": n_partial,  # 0.106 (#185)
            },
            summary=summary,
            caveats=caveats,
        )
