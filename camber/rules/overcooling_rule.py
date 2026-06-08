"""Rule: high-minimum-airflow / overcooling root cause (PNNL Ch.7).

Flags terminal boxes that overcool because they cannot throttle below their
minimum airflow when already satisfied on cooling -- the root cause behind the
reheat penalty. Adapts :func:`camber.overcooling.analyze_overcooling` to the
role-frame interface.
"""

from __future__ import annotations

import pandas as pd

from ..model.roles import Role
from ..overcooling import analyze_overcooling
from .base import Finding

_ROLE_TO_COL = {
    Role.SPACE_TEMP: "SpaceTemp",
    Role.COOL_SP: "ActCoolSP",
    Role.AIRFLOW: "ActFlow",
    Role.AIRFLOW_SP: "ActFlowSP",
    Role.DAMPER: "Damper",
    Role.HEAT_VALVE: "HWValve",
    Role.WARMUP: "WarmUp",
    Role.COOLDOWN: "CoolDown",
}


class OvercoolingMinFlow:
    """Detects overcooling driven by too-high minimum airflow at terminal boxes (PNNL Re-tuning Ch.7)."""

    name = "overcooling_min_flow"
    roles_required = (Role.SPACE_TEMP, Role.COOL_SP)
    roles_optional = (Role.AIRFLOW, Role.AIRFLOW_SP, Role.DAMPER, Role.HEAT_VALVE,
                      Role.WARMUP, Role.COOLDOWN)

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        cols = {r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns}
        legacy = frame.rename(columns=cols)
        res = analyze_overcooling(legacy, equip)
        if res is None:
            return Finding(rule=self.name, equip=equip, severity="info",
                           summary="insufficient data")
        # Severity from overcooling-at-min-flow that co-occurs with reheat (the
        # actionable, wasteful case). Fall back to overcool-at-min-flow ONLY when
        # there is genuinely no heat-valve data -- not when reheat merely never
        # overlaps (a real 0% reheat overlap is a finding, not a missing input). The
        # old ``a or b`` collapsed those two cases, scoring on the broader metric
        # whenever the valve existed but never co-occurred.
        oc = res.overcool_with_reheat_pct if res.has_heat_valve else res.overcool_at_minflow_pct
        severity = "fault" if oc >= 15.0 else ("warn" if oc >= 5.0 else "ok")
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "satisfied_pct": res.satisfied_pct,
                "overcool_at_minflow_pct": res.overcool_at_minflow_pct,
                "overcool_with_reheat_pct": res.overcool_with_reheat_pct,
                "median_minflow_fraction": res.median_minflow_fraction,
                "n_considered": res.n_considered,
            },
            summary=(f"{equip}: overcools at min flow {res.overcool_at_minflow_pct:.0f}% "
                     f"of occupied hours ({res.overcool_with_reheat_pct:.0f}% with "
                     f"reheat); min-flow ~{res.median_minflow_fraction:.0%} of peak"),
        )
