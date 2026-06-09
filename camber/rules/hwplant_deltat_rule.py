"""Rule: hot-water plant loop low-deltaT (PNNL Ch.8 — heating-side overpumping).

The heating-loop analogue of the chilled-water low-deltaT rule: a hot-water loop
running at low delta-T (HWS - HWR) over boiler-running hours is moving a lot of water
for little heat transfer -- oversized/constant-speed pumps, three-way valves dumping
flow, or coils bypassing. It wastes pump energy and flattens the supply-temp reset.
Adapts :func:`camber.plant.analyze_hw_plant` to the role-frame interface; the design
loop delta-T is system-specific, so it is a constructor parameter.
"""

from __future__ import annotations

import pandas as pd

from ..model.roles import Role
from ..plant import analyze_hw_plant
from .base import Finding

_ROLE_TO_COL = {
    Role.BOILER_STATUS: "BoilerStatus",
    Role.HW_SUPPLY_TEMP: "HWS_Temp",
    Role.HW_RETURN_TEMP: "HWR_Temp",
    Role.OAT: "OAT",
}


class HWPlantDeltaT:
    """Detects low hot-water loop delta-T at the heating plant (PNNL Re-tuning Ch.8)."""

    name = "hw_plant_deltat"
    roles_required = (Role.BOILER_STATUS, Role.HW_SUPPLY_TEMP, Role.HW_RETURN_TEMP)
    roles_optional = (Role.OAT,)

    def __init__(self, design_deltaT_min_f: float = 20.0):
        # System-specific: confirm against the HW loop design delta-T.
        self.design_deltaT_min_f = design_deltaT_min_f

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        cols = {r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns}
        legacy = frame.rename(columns=cols)
        res = analyze_hw_plant(legacy, equip, design_deltaT_min_f=self.design_deltaT_min_f)
        if res is None or res.deltaT_median_f != res.deltaT_median_f:  # None / NaN dT
            return Finding(rule=self.name, equip=equip, severity="info",
                           summary="insufficient running data for loop delta-T")
        low = res.low_deltaT_pct
        if low >= 50.0:
            severity = "fault"
        elif low >= 20.0:
            severity = "warn"
        else:
            severity = "ok"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "deltaT_median_f": res.deltaT_median_f,
                "design_deltaT_min_f": res.design_deltaT_min_f,
                "low_deltaT_pct": res.low_deltaT_pct,
                "n_running": res.n_running,
            },
            summary=(f"{equip}: HW loop deltaT median {res.deltaT_median_f:.1f}F "
                     f"({res.low_deltaT_pct:.0f}% of running hours < "
                     f"{res.design_deltaT_min_f:.0f}F design)"),
        )
