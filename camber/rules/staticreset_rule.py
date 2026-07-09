"""Rule: duct static-pressure setpoint not resetting.

A fixed duct-static setpoint runs the supply fan harder than the zones need. ASHRAE G36 trim-and-
respond resets static down when no zone is starved. A setpoint that never moves across a range of
load is a missed reset (fan energy left on the table). Flags a static setpoint whose range over the
window is below a threshold. numpy/pandas.
"""

from __future__ import annotations

import pandas as pd

from ..model.roles import Role
from .base import Finding


class StaticPressureReset:
    """Flags a duct static-pressure setpoint that doesn't reset (stays flat)."""

    name = "static_pressure_reset"
    roles_required = (Role.DUCT_STATIC_SP,)
    roles_optional = (Role.AIRFLOW,)

    def __init__(self, *, min_range_inwc: float = 0.15):
        self.min_range_inwc = min_range_inwc

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        sp = frame[Role.DUCT_STATIC_SP].dropna()
        if len(sp) < 3:
            return Finding(rule=self.name, equip=equip, severity="info",
                           summary=f"{equip}: insufficient static-pressure-setpoint data")
        rng = float(sp.max() - sp.min())
        resets = rng >= self.min_range_inwc
        sev = "ok" if resets else "warn"           # a flat setpoint is an advisory opportunity
        return Finding(
            rule=self.name, equip=equip, severity=sev,
            metrics={"sp_range_inwc": round(rng, 4), "sp_median_inwc": round(float(sp.median()), 4),
                     "min_range_inwc": self.min_range_inwc, "resets": resets},
            summary=(f"{equip}: static-pressure setpoint range {rng:.2f} inWC — "
                     + ("resets with demand" if resets else "flat (no trim-and-respond reset)")))
