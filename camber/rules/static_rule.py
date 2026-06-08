"""Fleet rule: VAV damper-distribution census (PNNL Ch.5/Ch.7).

Aggregates box damper positions across the fleet to infer whether duct static is
too high (most dampers throttling low) or too low (boxes starved). Adapts
:func:`camber.staticpressure.damper_census` to the fleet role-frame interface.
"""

from __future__ import annotations

from ..model.roles import Role
from ..staticpressure import damper_census
from .base import Finding


class DamperCensus:
    """Fleet census of VAV damper positions to infer mis-set duct static pressure (PNNL Re-tuning Ch.5/7)."""

    name = "damper_census"
    roles_required = (Role.DAMPER,)
    roles_optional = (Role.WARMUP, Role.COOLDOWN)

    def analyze_fleet(self, frames: dict) -> Finding:
        """Run the diagnostic across the fleet's role-frames; return one aggregate Finding."""
        if not frames:
            return Finding(rule=self.name, equip="<fleet>", severity="info",
                           summary="no boxes with damper points")
        legacy = {e: f.rename(columns={Role.DAMPER: "Damper"}) for e, f in frames.items()}
        res = damper_census(legacy)
        if res is None:
            return Finding(rule=self.name, equip="<fleet>", severity="info",
                           summary="no damper data")
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
            },
            summary=(f"fleet: median damper {res.median_damper_pct:.0f}%; "
                     f"{res.pct_boxes_low:.0f}% of boxes throttling low, "
                     f"{res.pct_boxes_high:.0f}% pinned open -- {res.verdict}"),
        )
