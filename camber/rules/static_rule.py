"""Fleet rule: VAV damper-distribution census (PNNL Ch.5/Ch.7).

Aggregates box damper positions across the fleet to infer whether duct static is
too high (most dampers throttling low) or too low (boxes starved). Adapts
:func:`camber.staticpressure.damper_census` to the fleet role-frame interface.
"""

from __future__ import annotations

from ..model.roles import Role
from ..staticpressure import damper_census
from .base import Finding

_LEGACY = {
    Role.DAMPER: "Damper",
    Role.OCCUPANCY: "Occupancy",
    Role.WARMUP: "WarmUp",
    Role.COOLDOWN: "CoolDown",
}


class DamperCensus:
    """Fleet census of VAV damper positions to infer mis-set duct static pressure
    (PNNL Re-tuning Ch.5/7)."""

    name = "damper_census"
    roles_required = (Role.DAMPER,)
    #: a trended occupancy point replaces the assumed weekday schedule (0.98, #84); warm-up /
    #: cool-down flags drop prep-mode samples
    roles_optional = (Role.OCCUPANCY, Role.WARMUP, Role.COOLDOWN)

    #: ``occupancy_gate`` values: ``"trended"`` judges each box's occupied samples from its own
    #: trended occupancy point, else the assumed weekday 07-18 schedule; ``"schedule"`` always
    #: uses the schedule (the pre-0.98 behaviour); ``"off"`` judges every sample.
    OCCUPANCY_GATES = ("trended", "schedule", "off")

    def __init__(self, *, occupancy_gate: str = "trended"):
        if occupancy_gate not in self.OCCUPANCY_GATES:
            raise ValueError(
                f"damper_census: occupancy_gate must be one of {self.OCCUPANCY_GATES}, "
                f"got {occupancy_gate!r}"
            )
        self.occupancy_gate = occupancy_gate

    def analyze_fleet(self, frames: dict, *, topology=None) -> Finding:
        """Run the diagnostic across the fleet's role-frames; return one aggregate Finding."""
        if not frames:
            return Finding(
                rule=self.name,
                equip="<fleet>",
                severity="info",
                summary="no boxes with damper points",
            )
        legacy = {e: f.rename(columns=_LEGACY) for e, f in frames.items()}
        res = damper_census(
            legacy,
            occupied_only=self.occupancy_gate != "off",
            use_trended_occupancy=self.occupancy_gate == "trended",
        )
        if res is None:
            return Finding(
                rule=self.name, equip="<fleet>", severity="info", summary="no damper data"
            )
        # fault when static is clearly mis-set (most dampers throttling or starved)
        if res.pct_boxes_low >= 60.0 or res.pct_boxes_high >= 25.0:
            severity = "fault"
        elif res.pct_boxes_in_band < 50.0:
            severity = "warn"
        else:
            severity = "ok"
        return Finding(
            rule=self.name,
            equip="<fleet>",
            severity=severity,
            metrics={
                "n_boxes": res.n_boxes,
                "median_damper_pct": res.median_damper_pct,
                "pct_boxes_low": res.pct_boxes_low,
                "pct_boxes_high": res.pct_boxes_high,
                "pct_boxes_in_band": res.pct_boxes_in_band,
                "occupancy_gate": res.occupancy_gate,
            },
            summary=(
                f"fleet: median damper {res.median_damper_pct:.0f}%; "
                f"{res.pct_boxes_low:.0f}% of boxes throttling low, "
                f"{res.pct_boxes_high:.0f}% pinned open -- {res.verdict}"
            ),
        )
