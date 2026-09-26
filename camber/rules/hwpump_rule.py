"""Rule: hot-water pump operation (riding-the-curve / VFD-minimum / DP reset; PNNL Ch.8).

The heating-loop counterpart to the CHW pump rule: flags a variable-speed HW pump
pinned near full speed (no DP reset, wasting cube-law energy) or pinned near its
minimum most of the time (oversized pump / DP setpoint, an impeller-trim or downsize
opportunity). Reuses the loop-agnostic pump-speed diagnostic
(:func:`camber.chwpump.analyze_pump`).

**The DP-setpoint reset is evaluated, not just named.** Until 0.82.0 this rule was called
``hw_pump_dp_reset`` but never looked at a DP setpoint. It now maps
:attr:`~camber.model.roles.Role.HW_DIFF_PRESS_SP` exactly as the CHW rule maps its setpoint, reports
whether the setpoint is reset, and -- when no setpoint is mapped -- says the reset was *not
evaluated* (an honest ``None`` plus a caveat) rather than implying one. As in the CHW rule, severity
rides on the speed distribution; the reset result is reported alongside it. The flat-setpoint test
(``dp_sp_flat_std``) is in the setpoint's own units.

**Speed units.** A 0-1 fraction speed is rescaled to percent here as well as upstream
(:data:`camber.units.PERCENT_ROLES` covers ``HW_PUMP_SPEED``, so :meth:`Registry.run
<camber.rules.base.Registry.run>` already normalizes it); calling :meth:`analyze` directly on a raw
0-1 frame no longer reads every sample as "not running".
"""

from __future__ import annotations

import pandas as pd

from ..chwpump import analyze_pump
from ..model.roles import Role
from ..units import normalize_percent
from .base import Finding

_ROLE_TO_COL = {
    Role.HW_PUMP_SPEED: "PumpSpeed",
    Role.HW_DIFF_PRESS_SP: "DiffPressSP",
}


class HWPumpDPReset:
    """Detects HW pumps riding the curve or pinned at the VFD minimum, and checks DP reset
    (PNNL Re-tuning Ch.8)."""

    name = "hw_pump_dp_reset"
    roles_required = (Role.HW_PUMP_SPEED,)
    roles_optional = (Role.HW_DIFF_PRESS_SP,)

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        cols = {r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns}
        legacy = frame.rename(columns=cols)
        if "PumpSpeed" in legacy.columns:
            legacy = legacy.assign(PumpSpeed=normalize_percent(legacy["PumpSpeed"]))
        res = analyze_pump(legacy, equip)
        if res is None:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                summary="insufficient data",
                caveats=["could not evaluate HW pump operation: no running pump-speed samples"],
            )
        pf, pm = res.pct_running_near_full, res.pct_running_near_min
        if pf >= 60.0:
            severity = "fault"
        elif pf >= 30.0 or pm >= 50.0:
            severity = "warn"
        else:
            severity = "ok"
        caveats = []
        reset = res.dp_sp_reset_present  # True / False / None
        if reset is None:
            caveats.append(
                "DP-setpoint reset not evaluated: no usable hot-water DP setpoint "
                "(HW_DIFF_PRESS_SP) mapped"
            )
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
                "median_dp_sp": res.median_dp_sp,
                "dp_sp_reset_present": res.dp_sp_reset_present,
                "n_running": res.n_running,
            },
            summary=(
                f"{equip}: HW pump median speed {res.median_speed_pct:.0f}%, "
                f"{res.pct_running_near_full:.0f}% near full / "
                f"{res.pct_running_near_min:.0f}% near min; {reset_note}"
            ),
            caveats=caveats,
        )
