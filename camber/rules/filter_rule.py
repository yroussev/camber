"""Rule: dirty air filter (high / rising filter differential pressure).

A loaded filter raises the pressure the supply fan must overcome, wasting fan energy and starving
airflow. Flags a filter whose differential pressure sits above the change-out threshold. The alarm
setpoint is filter/fan dependent, so ``change_dp_inwc`` is a constructor parameter (a common MERV-13
final-DP alarm is ~1.0 inH2O).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..model.roles import Role
from .base import Finding


class FilterFouling:
    """Detects a fouled air filter (differential pressure at/above the change-out threshold)."""

    name = "filter_fouling"
    roles_required = (Role.FILTER_DIFF_PRESS,)
    roles_optional = ()

    def __init__(self, change_dp_inwc: float = 1.0):
        self.change_dp_inwc = change_dp_inwc

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        if Role.FILTER_DIFF_PRESS not in frame.columns:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                summary="insufficient data (need filter differential pressure)",
            )
        dp = frame[Role.FILTER_DIFF_PRESS].dropna()
        if dp.empty:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                summary="insufficient data (need filter differential pressure)",
            )
        median = float(dp.median())
        thr = self.change_dp_inwc
        over_pct = float((dp > thr).mean() * 100.0)
        if median >= 1.5 * thr:
            severity = "fault"
        elif median >= thr:
            severity = "warn"
        else:
            severity = "ok"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "filter_dp_median_inwc": round(median, 3),
                "change_dp_inwc": thr,
                "pct_over_threshold": round(over_pct, 1),
                "filter_dp_p95_inwc": round(float(np.nanpercentile(dp, 95)), 3),
            },
            summary=(
                f"{equip}: filter ΔP median {median:.2f} inH2O "
                f"(change-out {thr:.2f}); {over_pct:.0f}% of hours above threshold"
            ),
        )
