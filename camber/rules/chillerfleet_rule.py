"""Fleet rule: multi-chiller staging (over-staging census; PNNL Ch.8 / ASHRAE).

Looks across all chillers at once: how often does the plant run more machines than
the load needs? Running two chillers at part-load when one could carry the whole load
wastes energy (each machine sits low on its efficiency curve and pumps/auxiliaries
double up). Adapts :func:`camber.chillerstaging.analyze_chiller_staging_fleet` to the
fleet role-frame interface. This is the plant-level companion to the single-chiller
``chiller_staging`` cycling/idling rule.
"""

from __future__ import annotations

from ..chillerstaging import analyze_chiller_staging_fleet
from ..model.roles import Role
from .base import Finding


class ChillerStagingFleet:
    """Census of chiller staging across the plant to flag over-staging (PNNL Re-tuning Ch.8)."""

    name = "chiller_staging_fleet"
    roles_required = (Role.POWER,)
    roles_optional = ()

    def __init__(self, redundancy_ceiling: float = 0.9):
        # How fully a single chiller can be loaded before the next must stage on.
        self.redundancy_ceiling = redundancy_ceiling

    def analyze_fleet(self, frames: dict) -> Finding:
        """Run across the chillers' role-frames; return one aggregate Finding."""
        if not frames:
            return Finding(rule=self.name, equip="<fleet>", severity="info",
                           summary="no chillers with power points")
        legacy = {e: f.rename(columns={Role.POWER: "Power"}) for e, f in frames.items()}
        res = analyze_chiller_staging_fleet(legacy, redundancy_ceiling=self.redundancy_ceiling)
        if res is None:
            return Finding(rule=self.name, equip="<fleet>", severity="info",
                           summary="need >= 2 chillers with power data to assess staging")
        pct = res.pct_overstaged
        if pct >= 50.0:
            severity = "fault"
        elif pct >= 20.0:
            severity = "warn"
        else:
            severity = "ok"
        return Finding(
            rule=self.name,
            equip="<fleet>",
            severity=severity,
            metrics={
                "n_chillers": res.n_chillers,
                "n_running_hours": res.n_running_hours,
                "n_multi_hours": res.n_multi_hours,
                "pct_overstaged": res.pct_overstaged,
                "median_running_count": res.median_running_count,
                "rep_capacity_kw": res.rep_capacity_kw,
            },
            summary=(f"fleet: {res.n_chillers} chillers, "
                     f"{res.pct_overstaged:.0f}% of multi-chiller hours over-staged "
                     f"(median {res.median_running_count:.0f} running); a redundant "
                     f"chiller could stage off"),
        )
