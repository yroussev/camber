"""Rule: cooling-tower approach (condenser-water supply vs wet-bulb; CTI/ASHRAE).

Flags a cooling tower achieving a persistently high approach at load -- fouled fill,
plugged nozzles, reduced airflow, or an over-loaded tower -- which raises condenser
water temperature, chiller lift, and kW/ton. Adapts
:func:`camber.coolingtower.analyze_cooling_tower_approach` to the role-frame interface.
Wet-bulb is taken from a mapped point or derived from OAT + RH; OAT/RH are
building-level and arrive via the runner's ``shared`` channel. The design approach is
tower/climate-specific, so it is a constructor parameter, not a baked constant.
"""

from __future__ import annotations

import pandas as pd

from ..coolingtower import analyze_cooling_tower_approach
from ..model.roles import Role
from .base import Finding

_ROLE_TO_COL = {
    Role.CW_SUPPLY_TEMP: "CWS_Temp",
    Role.CW_RETURN_TEMP: "CWR_Temp",
    Role.WETBULB_TEMP: "WetBulb",
    Role.OAT: "OAT",
    Role.OUTDOOR_RH: "RH",
    Role.TOWER_FAN_SPEED: "TowerFanSpeed",
}


class CoolingTowerApproach:
    """Detects a cooling tower running above its design approach at load (CTI/ASHRAE)."""

    name = "cooling_tower_approach"
    roles_required = (Role.CW_SUPPLY_TEMP,)
    roles_optional = (Role.WETBULB_TEMP, Role.OAT, Role.OUTDOOR_RH,
                      Role.CW_RETURN_TEMP, Role.TOWER_FAN_SPEED)

    def __init__(self, design_approach_f: float = 7.0):
        # Tower/climate-specific: confirm against the tower schedule / selection.
        self.design_approach_f = design_approach_f

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        cols = {r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns}
        legacy = frame.rename(columns=cols)
        res = analyze_cooling_tower_approach(legacy, equip,
                                             design_approach_f=self.design_approach_f)
        if res is None:
            return Finding(rule=self.name, equip=equip, severity="info",
                           summary="insufficient data (need CW supply temp + wet-bulb or OAT+RH)")
        ratio = res.approach_median_f / res.design_approach_f if res.design_approach_f else 0.0
        if ratio >= 1.7:
            severity = "fault"
        elif ratio >= 1.3:
            severity = "warn"
        else:
            severity = "ok"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "approach_median_f": res.approach_median_f,
                "design_approach_f": res.design_approach_f,
                "range_median_f": res.range_median_f,
                "pct_hours_high_approach": res.pct_hours_high_approach,
                "wetbulb_source": res.wetbulb_source,
                "n_operating": res.n_operating,
            },
            summary=(f"{equip}: tower approach median {res.approach_median_f:.1f}F "
                     f"vs design {res.design_approach_f:.1f}F "
                     f"({res.pct_hours_high_approach:.0f}% of operating hours high; "
                     f"wet-bulb {res.wetbulb_source})"),
        )
