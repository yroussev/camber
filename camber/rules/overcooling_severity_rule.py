"""Rule: Std-55-aligned overcooling severity (depth x duration below setpoint).

A comfort-severity diagnostic, distinct from :class:`OvercoolingMinFlow` (which
scores the min-flow root cause / ECM). This one measures how far a zone sits below
its cooling setpoint -- relative to the comfort deadband when a heating setpoint is
also present -- and for how long, assigning info/warn/fault tiers only when the
excursion is *sustained* (see :func:`camber.overcooling_severity`).

The ``info`` tier is informational only: it is emitted at Finding severity
``"info"``, which the triage layer treats as non-actionable, so it never enters the
ranked/headline fault totals.
"""

from __future__ import annotations

import pandas as pd

from ..model.roles import Role
from ..overcooling_severity import analyze_overcooling_severity
from .base import Finding

_ROLE_TO_COL = {
    Role.SPACE_TEMP: "SpaceTemp",
    Role.COOL_SP: "ActCoolSP",
    Role.HEAT_SP: "ActHeatSP",
    Role.WARMUP: "WarmUp",
    Role.COOLDOWN: "CoolDown",
}


class OvercoolingSeverity:
    """Std-55 overcooling severity: depth x duration below the (deadband-aware) setpoint."""

    name = "overcooling_severity"
    roles_required = (Role.SPACE_TEMP, Role.COOL_SP)
    roles_optional = (Role.HEAT_SP, Role.WARMUP, Role.COOLDOWN)

    def __init__(self, *, tiers: dict | None = None, window_min: float = 60.0,
                 relative_to_deadband: bool = True):
        self.tiers = tiers
        self.window_min = window_min
        self.relative_to_deadband = relative_to_deadband

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the severity diagnostic on an equipment role-frame; return a Finding."""
        cols = {r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns}
        legacy = frame.rename(columns=cols)
        res = analyze_overcooling_severity(
            legacy, equip, tiers=self.tiers, window_min=self.window_min,
            relative_to_deadband=self.relative_to_deadband)
        if res is None:
            return Finding(rule=self.name, equip=equip, severity="info",
                           summary="insufficient data")
        ref = ("heating setpoint" if res.mode == "relative_deadband"
               else "cooling setpoint")
        return Finding(
            rule=self.name,
            equip=equip,
            severity=res.severity,           # ok | info | warn | fault (info non-actionable)
            metrics={
                "mode": res.mode,
                "interval_min": res.interval_min,
                "window_min": res.window_min,
                "median_depth_f": res.median_depth_f,
                "max_depth_f": res.max_depth_f,
                "fault_pct": res.tier_pct.get("fault"),
                "warn_pct": res.tier_pct.get("warn"),
                "info_pct": res.tier_pct.get("info"),
                "fault_minutes": res.tier_minutes.get("fault"),
                "n_considered": res.n_considered,
            },
            summary=(f"{equip}: overcooled below {ref} -- max {res.max_depth_f:.1f} degF, "
                     f"sustained tiers info/warn/fault = "
                     f"{res.tier_pct.get('info'):.0f}/{res.tier_pct.get('warn'):.0f}/"
                     f"{res.tier_pct.get('fault'):.0f}% of occupied samples "
                     f"({res.window_min:.0f}-min persistence @ {res.interval_min:.0f}-min data)"),
        )
