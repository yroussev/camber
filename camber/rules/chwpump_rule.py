"""Rule: CHW pump differential-pressure reset (PNNL Ch.8).

Flags chilled-water pumps pinned near full speed at part load -- no effective DP
reset, wasting cube-law pump energy. Adapts
:func:`camber.chwpump.analyze_chw_pump` to the role-frame interface.

"Near the minimum" is judged against the pump's own VFD floor (0.98, #86): ``near_min_pct="auto"``
learns it from a plateau in the running speeds (:func:`camber.chwpump.learn_vfd_floor`); a number
fixes the band instead.

Note: a plant with multiple parallel pumps exposes several speed points. The
mapping resolves one representative pump-speed series to CHW_PUMP_SPEED;
aggregating across all pumps is a follow-up.
"""

from __future__ import annotations

import pandas as pd

from ..chwpump import _check_near_min, _floor_note, analyze_chw_pump
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

    def __init__(self, *, near_min_pct: float | str = "auto", floor_tol_pct: float = 1.0):
        # 0.98 (#86): "auto" learns the VFD floor (camber.chwpump.learn_vfd_floor); a number
        # fixes the near-minimum band (25 was the only behaviour before 0.98)
        self.near_min_pct = _check_near_min(near_min_pct)
        self.floor_tol_pct = float(floor_tol_pct)

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        cols = {r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns}
        legacy = frame.rename(columns=cols)
        res = analyze_chw_pump(
            legacy, equip, near_min_pct=self.near_min_pct, floor_tol_pct=self.floor_tol_pct
        )
        if res is None:
            return Finding(
                rule=self.name, equip=equip, severity="info", summary="insufficient data"
            )
        pf, pm = res.pct_running_near_full, res.pct_running_near_min
        # riding the curve (near full) is the energy fault; pinned at the VFD minimum
        # is an oversizing opportunity (warn).
        if pf >= 60.0:
            severity = "fault"
        elif pf >= 30.0 or pm >= 50.0:
            severity = "warn"
        else:
            severity = "ok"
        # DP-setpoint reset is a separate sub-check; None means no DP setpoint was
        # available to evaluate it -> caveat + honest note, never a confident "flat DP
        # setpoint". (Severity here rides on speed distribution, not on reset.)
        caveats = []
        reset = res.dp_sp_reset_present  # True / False / None
        if reset is None:
            caveats.append("DP-setpoint reset not evaluated: no DP setpoint")
        reset_note = (
            "DP-SP reset present"
            if reset is True
            else "flat DP setpoint"
            if reset is False
            else "DP-SP reset not evaluated (no DP setpoint)"
        )
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "median_speed_pct": res.median_speed_pct,
                "pct_running_near_full": res.pct_running_near_full,
                "pct_running_near_min": res.pct_running_near_min,
                "near_min_band_pct": res.near_min_band_pct,
                "near_min_source": res.near_min_source,
                "vfd_floor_pct": res.vfd_floor_pct,
                "median_dp_sp": res.median_dp_sp,
                "dp_sp_reset_present": res.dp_sp_reset_present,
                "n_running": res.n_running,
            },
            summary=(
                f"{equip}: pump median speed {res.median_speed_pct:.0f}%, "
                f"{res.pct_running_near_full:.0f}% near full / "
                f"{res.pct_running_near_min:.0f}% near min (<= {res.near_min_band_pct:g}%"
                f"{_floor_note(res)}); {reset_note}"
            ),
            caveats=caveats,
        )
