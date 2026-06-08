"""Rule #1: simultaneous heating and cooling at an AHU.

Both the heating and cooling coil open at once wastes energy directly. This rule
adapts the existing :func:`camber.ahu.analyze_ahu` math to the role-frame
interface: it needs the HEAT_VALVE and COOL_VALVE roles (occupancy/prep roles are
used opportunistically if present).
"""

from __future__ import annotations

import pandas as pd

from ..ahu import analyze_ahu
from ..model.roles import Role
from .base import Finding

# Map the roles this rule consumes to the column names analyze_ahu expects.
_ROLE_TO_AHU_COL = {
    Role.COOL_VALVE: "CHW_Valve",
    Role.HEAT_VALVE: "HHW_Valve",
    Role.OAT: "OSA",
    Role.RETURN_AIR_TEMP: "ReturnAir",
    Role.OA_DAMPER: "OA_Damper",
    Role.OCCUPANCY: "Occupancy",
    Role.WARMUP: "WarmUp",
    Role.COOLDOWN: "CoolDown",
}


class SimultaneousHeatCool:
    """Detects an AHU with heating and cooling coils open at once (PNNL Re-tuning Ch.5)."""

    name = "simultaneous_heat_cool"
    roles_required = (Role.HEAT_VALVE, Role.COOL_VALVE)
    roles_optional = (Role.OAT, Role.RETURN_AIR_TEMP, Role.OA_DAMPER,
                      Role.OCCUPANCY, Role.WARMUP, Role.COOLDOWN)

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        # translate role columns -> the legacy measure-named frame analyze_ahu wants
        cols = {role: col for role, col in _ROLE_TO_AHU_COL.items() if role in frame.columns}
        legacy = frame.rename(columns=cols)
        res = analyze_ahu(legacy, equip)
        if res is None:
            return Finding(rule=self.name, equip=equip, severity="info",
                           summary="insufficient data")
        pct = res.simultaneous_hc_pct
        severity = "fault" if pct >= 5.0 else ("warn" if pct >= 1.0 else "ok")
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "simultaneous_hc_pct": pct,
                "chw_open_pct": res.chw_open_pct,
                "hhw_open_pct": res.hhw_open_pct,
                "mean_overlap_when_simul": res.mean_overlap_when_simul,
                "n_considered": res.n_considered,
            },
            summary=(f"{equip}: both coils open {pct:.1f}% of occupied hours "
                     f"(CHW {res.chw_open_pct:.0f}%, HHW {res.hhw_open_pct:.0f}%)"),
        )
