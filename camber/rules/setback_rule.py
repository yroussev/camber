"""Rule: AHU night/weekend setback (PNNL Re-tuning Ch.5).

Flags an air handler whose supply fan runs during unoccupied hours -- no effective
night/weekend setback, the cheapest large saver. Adapts
:func:`camber.setback.analyze_setback` to the role-frame interface.
"""

from __future__ import annotations

import pandas as pd

from ..model.roles import Role
from ..setback import analyze_setback
from .base import Finding

_ROLE_TO_COL = {
    Role.SUPPLY_FAN_STATUS: "SupplyFanStatus",
    Role.SUPPLY_FAN_SPEED: "SupplyFanSpeed",
}


class NightWeekendSetback:
    """Detects an AHU fan running unoccupied / missing night-weekend setback (PNNL Re-tuning Ch.5)."""

    name = "night_weekend_setback"
    # status preferred; speed is an acceptable substitute, so require neither
    # specifically -- gate on the pair via a custom check below.
    roles_required = ()
    roles_optional = (Role.SUPPLY_FAN_STATUS, Role.SUPPLY_FAN_SPEED)

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        if (Role.SUPPLY_FAN_STATUS not in frame.columns
                and Role.SUPPLY_FAN_SPEED not in frame.columns):
            return Finding(rule=self.name, equip=equip, severity="info",
                           summary="no fan status or speed")
        cols = {r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns}
        legacy = frame.rename(columns=cols)
        res = analyze_setback(legacy, equip)
        if res is None:
            return Finding(rule=self.name, equip=equip, severity="info",
                           summary="insufficient data")
        un = res.fan_run_unoccupied_pct
        # High unoccupied run = no setback. ok only if setback is effective.
        if res.setback_effective:
            severity = "ok"
        elif un >= 50.0:
            severity = "fault"
        else:
            severity = "warn"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "fan_run_unoccupied_pct": res.fan_run_unoccupied_pct,
                "fan_run_occupied_pct": res.fan_run_occupied_pct,
                "setback_effective": res.setback_effective,
                "n_unoccupied": res.n_unoccupied,
            },
            summary=(f"{equip}: supply fan runs {res.fan_run_unoccupied_pct:.0f}% of "
                     f"unoccupied hours (vs {res.fan_run_occupied_pct:.0f}% occupied); "
                     f"setback {'effective' if res.setback_effective else 'MISSING/weak'}"),
        )
