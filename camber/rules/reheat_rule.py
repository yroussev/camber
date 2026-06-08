"""Rule: terminal-box reheat penalty.

A VAV/CAV box reheating while it is also being cooled (cold central supply air,
high outdoor temp, airflow above minimum, or space at/below the cooling setpoint)
wastes energy. Adapts :func:`camber.reheat.analyze_box` to the role-frame
interface; needs HEAT_VALVE, and uses OAT / SUPPLY_AIR_TEMP / airflow / setpoint
roles opportunistically for the richer indicators.
"""

from __future__ import annotations

import pandas as pd

from ..model.roles import Role
from ..reheat import analyze_box
from .base import Finding

# role -> the legacy column name analyze_box expects
_ROLE_TO_BOX_COL = {
    Role.HEAT_VALVE: "HWValve",
    Role.SPACE_TEMP: "SpaceTemp",
    Role.SUPPLY_AIR_TEMP: "SupplyAir",
    Role.HEAT_SP: "ActHeatSP",
    Role.COOL_SP: "ActCoolSP",
    Role.AIRFLOW: "ActFlow",
    Role.AIRFLOW_SP: "ActFlowSP",
    Role.DAMPER: "Damper",
    Role.WARMUP: "WarmUp",
    Role.COOLDOWN: "CoolDown",
}


class ReheatPenalty:
    """Detects terminal-box reheat that coincides with cooling (reheat penalty) (PNNL Re-tuning Ch.7)."""

    name = "reheat_penalty"
    roles_required = (Role.HEAT_VALVE,)
    roles_optional = (Role.OAT, Role.SPACE_TEMP, Role.SUPPLY_AIR_TEMP, Role.HEAT_SP,
                      Role.COOL_SP, Role.AIRFLOW, Role.AIRFLOW_SP, Role.DAMPER,
                      Role.WARMUP, Role.COOLDOWN)

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        cols = {r: c for r, c in _ROLE_TO_BOX_COL.items() if r in frame.columns}
        legacy = frame.rename(columns=cols)
        # OAT is passed to analyze_box as a separate series, not a column
        oat = frame[Role.OAT] if Role.OAT in frame.columns else None
        res = analyze_box(legacy, equip, oat=oat)
        if res is None:
            return Finding(rule=self.name, equip=equip, severity="info",
                           summary="insufficient data")
        # Headline = reheat at high OAT (heating in cooling weather). Falls back to
        # the cold-supply indicator if no OAT was available.
        hi = res.reheat_at_high_oat_pct
        headline = hi if oat is not None else res.reheat_and_coldsupply_pct
        severity = "fault" if headline >= 20.0 else ("warn" if headline >= 5.0 else "ok")
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "valve_open_pct": res.valve_open_pct,
                "reheat_at_high_oat_pct": res.reheat_at_high_oat_pct,
                "reheat_and_coldsupply_pct": res.reheat_and_coldsupply_pct,
                "reheat_above_min_flow_pct": res.reheat_above_min_flow_pct,
                "reheat_below_coolsp_pct": res.reheat_below_coolsp_pct,
                "mean_valve_when_open": res.mean_valve_when_open,
                "n_considered": res.n_considered,
            },
            summary=(f"{equip}: reheat valve open {res.valve_open_pct:.0f}% of occupied "
                     f"hours; {res.reheat_at_high_oat_pct:.0f}% at OAT>65F"),
        )
