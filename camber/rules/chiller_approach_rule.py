"""Rule: chiller heat-exchanger fouling via approach-temperature degradation.

The condenser/evaporator *approach* — the gap between the refrigerant saturation temperature and the
water leaving that heat exchanger — widens as tubes foul or (on the evaporator side) as charge/flow
degrade. It is the classic refrigerant-side degradation indicator that needs no refrigerant-pressure
instrumentation beyond the approach temps a chiller controller already reports. Flags a sustained
approach above a design threshold. Thresholds are chiller-specific, so they are constructor parameters.
"""

from __future__ import annotations

import pandas as pd

from ..model.roles import Role
from .base import Finding


class ChillerApproachFouling:
    """Detects condenser/evaporator fouling from an elevated approach temperature."""

    name = "chiller_approach_fouling"
    roles_required = (Role.COND_APPROACH_TEMP,)
    roles_optional = (Role.EVAP_APPROACH_TEMP,)

    def __init__(self, cond_design_f: float = 5.0, evap_design_f: float = 4.0):
        self.cond_design_f = cond_design_f
        self.evap_design_f = evap_design_f

    def _leg(self, frame, role, design):
        if role not in frame.columns:
            return None
        s = frame[role].dropna()
        if s.empty:
            return None
        median = float(s.median())
        return {"median_f": round(median, 2), "design_f": design,
                "ratio": round(median / design, 2) if design else float("nan")}

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        cond = self._leg(frame, Role.COND_APPROACH_TEMP, self.cond_design_f)
        evap = self._leg(frame, Role.EVAP_APPROACH_TEMP, self.evap_design_f)
        if cond is None and evap is None:
            return Finding(rule=self.name, equip=equip, severity="info",
                           summary="insufficient data (need condenser/evaporator approach temp)")
        worst = max((leg["ratio"] for leg in (cond, evap) if leg), default=0.0)
        if worst >= 2.0:
            severity = "fault"
        elif worst >= 1.5:
            severity = "warn"
        else:
            severity = "ok"
        metrics = {"worst_approach_ratio": worst}
        if cond:
            metrics.update(cond_approach_f=cond["median_f"], cond_design_f=cond["design_f"])
        if evap:
            metrics.update(evap_approach_f=evap["median_f"], evap_design_f=evap["design_f"])
        legs = []
        if cond:
            legs.append(f"condenser {cond['median_f']:.1f}°F (design {cond['design_f']:.0f})")
        if evap:
            legs.append(f"evaporator {evap['median_f']:.1f}°F (design {evap['design_f']:.0f})")
        return Finding(
            rule=self.name, equip=equip, severity=severity, metrics=metrics,
            summary=f"{equip}: approach temperature — {'; '.join(legs)} (fouling widens the approach)",
        )
