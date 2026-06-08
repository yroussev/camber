"""Fleet rule: zones heating vs cooling census.

How many terminal zones heat while others cool, building-wide. A fleet that is
never free of simultaneous heat/cool during occupancy points at central
overcooling re-warmed locally. Adapts :func:`camber.zones.zone_states` /
:func:`camber.zones.time_of_week_profile` to the fleet role-frame interface.
"""

from __future__ import annotations

from ..model.roles import Role
from ..schedules import occupied_mask
from ..zones import zone_states, time_of_week_profile
from .base import Finding

_ROLE_TO_ZONE_COL = {
    Role.HEAT_VALVE: "HWValve",
    Role.AIRFLOW: "ActFlow",
    Role.AIRFLOW_SP: "ActFlowSP",
    Role.SPACE_TEMP: "SpaceTemp",
    Role.COOL_SP: "ActCoolSP",
    Role.WARMUP: "WarmUp",
    Role.COOLDOWN: "CoolDown",
}


class ZonesHeatCoolCensus:
    """Fleet census of zones heating while others cool (PNNL Re-tuning Ch.5/7)."""

    name = "zones_heat_cool_census"
    roles_required = (Role.HEAT_VALVE,)
    roles_optional = (Role.AIRFLOW, Role.AIRFLOW_SP, Role.SPACE_TEMP, Role.COOL_SP,
                      Role.WARMUP, Role.COOLDOWN)

    def analyze_fleet(self, frames: dict) -> Finding:
        """Run the diagnostic across the fleet's role-frames; return one aggregate Finding."""
        if not frames:
            return Finding(rule=self.name, equip="<fleet>", severity="info",
                           summary="no zones with reheat valves")
        legacy = {
            equip: f.rename(columns={r: c for r, c in _ROLE_TO_ZONE_COL.items()
                                     if r in f.columns})
            for equip, f in frames.items()
        }
        states = zone_states(legacy)
        if states.empty:
            return Finding(rule=self.name, equip="<fleet>", severity="info",
                           summary="no zone states computed")
        occ = states[occupied_mask(states.index)]
        if occ.empty:
            return Finding(rule=self.name, equip="<fleet>", severity="info",
                           summary="no occupied intervals")
        avg_both = float(occ["n_both"].mean())
        pct_any_both = 100.0 * float((occ["n_both"] > 0).mean())
        avg_heating = float(occ["n_heating"].mean())
        avg_cooling = float(occ["n_cooling"].mean())
        # Fleet is "in fault" when it is essentially never free of simultaneous
        # heat/cool during occupancy.
        severity = "fault" if pct_any_both >= 75.0 else ("warn" if pct_any_both >= 25.0 else "ok")
        # keep the time-of-week profile available for charting downstream
        profile = time_of_week_profile(states, occupied_only=True)
        return Finding(
            rule=self.name,
            equip="<fleet>",
            severity=severity,
            metrics={
                "n_zones": int(occ["n_zones"].max()),
                "avg_zones_heating": round(avg_heating, 2),
                "avg_zones_cooling": round(avg_cooling, 2),
                "avg_zones_both": round(avg_both, 2),
                "pct_hours_any_both": round(pct_any_both, 1),
                "max_zones_both": int(occ["n_both"].max()),
                "timeofweek_profile": profile,  # DataFrame, for charts
            },
            summary=(f"fleet: avg {avg_both:.1f} zones simultaneously heating+cooling; "
                     f"{pct_any_both:.0f}% of occupied hours have >=1 such zone"),
        )
