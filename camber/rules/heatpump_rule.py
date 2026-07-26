"""Rule: heat-pump excess defrost / reversing-valve cycling.

An air-source heat pump reverses to cooling briefly to defrost the outdoor coil; a unit that reverses
far too often (iced coil, faulty defrost termination, or a hunting reversing valve) wastes the heat it
just delivered and drives up energy use. Counts reversing-valve transitions per day.
``max_reversals_per_day`` is defrost-strategy dependent, so it is a constructor parameter.
"""

from __future__ import annotations

import pandas as pd

from ..model.roles import Role
from .base import Finding
from .compressor_stage_rule import _changes_per_day


class HeatPumpDefrost:
    """Detects excess heat-pump defrost / reversing-valve cycling."""

    name = "heatpump_defrost"
    roles_required = (Role.REVERSING_VALVE_CMD,)
    roles_optional = (Role.COMPRESSOR_STATUS, Role.OAT)

    def __init__(self, max_reversals_per_day: float = 24.0):
        # normal defrost is ~ once/hour in cold weather at worst; 24/day is a generous ceiling
        self.max_reversals_per_day = max_reversals_per_day

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        if Role.REVERSING_VALVE_CMD not in frame.columns:
            return Finding(rule=self.name, equip=equip, severity="info",
                           summary="insufficient data (need reversing-valve command)")
        n_rev, per_day, span = _changes_per_day(frame[Role.REVERSING_VALVE_CMD])
        if span <= 0:
            return Finding(rule=self.name, equip=equip, severity="info",
                           summary="insufficient data (need reversing-valve command)")
        if per_day >= 2 * self.max_reversals_per_day:
            severity = "fault"
        elif per_day >= self.max_reversals_per_day:
            severity = "warn"
        else:
            severity = "ok"
        return Finding(
            rule=self.name, equip=equip, severity=severity,
            metrics={"reversals_per_day": round(per_day, 2),
                     "max_reversals_per_day": self.max_reversals_per_day,
                     "n_reversals": n_rev, "n_days": round(span, 2)},
            summary=(f"{equip}: {per_day:.1f} reversing-valve cycles/day "
                     f"(threshold {self.max_reversals_per_day:.0f}) — check defrost termination"),
        )
