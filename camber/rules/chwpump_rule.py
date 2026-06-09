"""Rule: CHW pump differential-pressure reset (PNNL Ch.8).

Flags chilled-water pumps pinned near full speed at part load -- no effective DP
reset, wasting cube-law pump energy. Adapts
:func:`camber.chwpump.analyze_chw_pump` to the role-frame interface.

Note: a plant with multiple parallel pumps exposes several speed points. The
mapping resolves one representative pump-speed series to CHW_PUMP_SPEED;
aggregating across all pumps is a follow-up.
"""

from __future__ import annotations

import pandas as pd

from ..chwpump import analyze_chw_pump
from ..model.roles import Role
from .base import Finding

_ROLE_TO_COL = {
    Role.CHW_PUMP_SPEED: "PumpSpeed",
    Role.CHW_DIFF_PRESS_SP: "DiffPressSP",
}


class CHWPumpDPReset:
    """Detects CHW pumps pinned near full speed at part load / no DP reset (PNNL Re-tuning Ch.8)."""

    name = "chw_pump_dp_reset"
    roles_required = (Role.CHW_PUMP_SPEED,)
    roles_optional = (Role.CHW_DIFF_PRESS_SP,)

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        cols = {r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns}
        legacy = frame.rename(columns=cols)
        res = analyze_chw_pump(legacy, equip)
        if res is None:
            return Finding(rule=self.name, equip=equip, severity="info",
                           summary="insufficient data")
        pf, pm = res.pct_running_near_full, res.pct_running_near_min
        # riding the curve (near full) is the energy fault; pinned at the VFD minimum
        # is an oversizing opportunity (warn).
        if pf >= 60.0:
            severity = "fault"
        elif pf >= 30.0 or pm >= 50.0:
            severity = "warn"
        else:
            severity = "ok"
        reset_note = "DP-SP reset present" if res.dp_sp_reset_present else "flat DP setpoint"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "median_speed_pct": res.median_speed_pct,
                "pct_running_near_full": res.pct_running_near_full,
                "pct_running_near_min": res.pct_running_near_min,
                "median_dp_sp": res.median_dp_sp,
                "dp_sp_reset_present": res.dp_sp_reset_present,
                "n_running": res.n_running,
            },
            summary=(f"{equip}: pump median speed {res.median_speed_pct:.0f}%, "
                     f"{res.pct_running_near_full:.0f}% near full / "
                     f"{res.pct_running_near_min:.0f}% near min; {reset_note}"),
        )
