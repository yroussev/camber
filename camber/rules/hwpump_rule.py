"""Rule: hot-water pump operation (riding-the-curve / VFD-minimum; PNNL Ch.8).

The heating-loop counterpart to the CHW pump rule: flags a variable-speed HW pump
pinned near full speed (no DP reset, wasting cube-law energy) or pinned near its
minimum most of the time (oversized pump / DP setpoint, an impeller-trim or downsize
opportunity). Reuses the loop-agnostic pump-speed diagnostic
(:func:`camber.chwpump.analyze_pump`).
"""

from __future__ import annotations

import pandas as pd

from ..chwpump import analyze_pump
from ..model.roles import Role
from .base import Finding

_ROLE_TO_COL = {Role.HW_PUMP_SPEED: "PumpSpeed"}


class HWPumpDPReset:
    """Detects HW pumps riding the curve or pinned at the VFD minimum (PNNL Re-tuning Ch.8)."""

    name = "hw_pump_dp_reset"
    roles_required = (Role.HW_PUMP_SPEED,)
    roles_optional = ()

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        cols = {r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns}
        legacy = frame.rename(columns=cols)
        res = analyze_pump(legacy, equip)
        if res is None:
            return Finding(rule=self.name, equip=equip, severity="info",
                           summary="insufficient data")
        pf, pm = res.pct_running_near_full, res.pct_running_near_min
        if pf >= 60.0:
            severity = "fault"
        elif pf >= 30.0 or pm >= 50.0:
            severity = "warn"
        else:
            severity = "ok"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "median_speed_pct": res.median_speed_pct,
                "pct_running_near_full": res.pct_running_near_full,
                "pct_running_near_min": res.pct_running_near_min,
                "n_running": res.n_running,
            },
            summary=(f"{equip}: HW pump median speed {res.median_speed_pct:.0f}%, "
                     f"{res.pct_running_near_full:.0f}% near full / "
                     f"{res.pct_running_near_min:.0f}% near min"),
        )
