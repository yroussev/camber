"""Rule: G36 reheat-minimization compliance at VAV reheat boxes (§5.6.5).

G36's dual-max reheat sequence minimizes reheat energy: when a zone needs heat,
the box should FIRST raise the discharge-air temperature while holding airflow at
the heating *minimum* (heating loop 0-50%), and only raise airflow toward the
heating maximum at high heating demand (loop 51-100%). Raising airflow while
reheating but not deep in heating demand wastes energy -- you reheat more air than
necessary.

Without the heating-loop signal in trend data, we detect the observable footprint:
the reheat valve is open (heating active) while airflow is meaningfully ABOVE the
box's minimum-flow setpoint. Per §5.6.5 that should only happen at high heating
demand; persistent high-flow reheat indicates the dual-max minimization is not in
effect (or the minimum flow is set too high -- see the overcooling diagnostic).

Clean-room: implements the §5.6.5 logic; G36 text/tables not reproduced.
"""

from __future__ import annotations

import pandas as pd

from ..model.roles import Role
from ..schedules import occupied_mask
from .base import Finding


class ReheatMinimization:
    """Detects high-flow reheat that violates G36 dual-max reheat minimization (ASHRAE Guideline 36 §5.6.5)."""

    name = "reheat_minimization_g36"
    roles_required = (Role.HEAT_VALVE, Role.AIRFLOW, Role.AIRFLOW_SP)
    roles_optional = (Role.WARMUP, Role.COOLDOWN)

    def __init__(self, valve_thr: float = 5.0, flow_margin: float = 1.20):
        # airflow above (1 + margin-1) x its minimum setpoint counts as "above min"
        self.valve_thr = valve_thr
        self.flow_margin = flow_margin

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        need = (Role.HEAT_VALVE, Role.AIRFLOW, Role.AIRFLOW_SP)
        if any(r not in frame.columns for r in need):
            return Finding(rule=self.name, equip=equip, severity="info",
                           summary="needs reheat valve + airflow + airflow setpoint")
        w = frame.copy()
        warm = w[Role.WARMUP] if Role.WARMUP in w.columns else None
        cool = w[Role.COOLDOWN] if Role.COOLDOWN in w.columns else None
        w = w[occupied_mask(w.index, warmup=warm, cooldown=cool)]
        w = w.dropna(subset=[Role.HEAT_VALVE, Role.AIRFLOW, Role.AIRFLOW_SP])
        n = len(w)
        if n == 0:
            return Finding(rule=self.name, equip=equip, severity="info",
                           summary="no occupied data")
        reheating = w[Role.HEAT_VALVE] > self.valve_thr
        n_reheat = int(reheating.sum())
        if n_reheat == 0:
            return Finding(rule=self.name, equip=equip, severity="ok",
                           metrics={"reheat_hours_pct": 0.0, "n_considered": n},
                           summary=f"{equip}: no reheating in occupied hours")
        above_min = w[Role.AIRFLOW] > w[Role.AIRFLOW_SP] * self.flow_margin
        # G36 violation: reheating AND airflow above minimum (should be at min until
        # deep heating demand)
        violation = reheating & above_min
        viol_pct = round(100.0 * int(violation.sum()) / n_reheat, 1)  # of reheat hours
        severity = "fault" if viol_pct >= 40.0 else ("warn" if viol_pct >= 15.0 else "ok")
        return Finding(
            rule=self.name, equip=equip, severity=severity,
            metrics={
                "reheat_hours_pct": round(100.0 * n_reheat / n, 1),
                "reheat_above_min_pct": viol_pct,   # of reheating hours
                "n_reheat_hours": n_reheat,
                "n_considered": n,
            },
            summary=(f"{equip}: reheating {100.0*n_reheat/n:.0f}% of occupied hours; "
                     f"of those, {viol_pct:.0f}% with airflow above minimum "
                     f"(G36 §5.6.5 would hold min flow until deep heating demand)"),
        )
