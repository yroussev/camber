"""Rule: leaking coil valve (PNNL Re-tuning Ch.5/Ch.7).

Flags a coil whose valve is commanded closed but still shifts supply-air temp --
uncommanded heating or cooling the simultaneous-H/C and reheat checks miss.
Adapts :func:`camber.leakvalve.analyze_leak_valves` to the role-frame interface.
"""

from __future__ import annotations

import pandas as pd

from ..leakvalve import analyze_leak_valves
from ..model.roles import Role
from .base import Finding

_ROLE_TO_COL = {
    Role.COOL_VALVE: "CHW_Valve",
    Role.HEAT_VALVE: "HHW_Valve",
    Role.MIXED_AIR_TEMP: "MixedAir",
    Role.SUPPLY_AIR_TEMP: "SupplyAir",
}


class LeakingValve:
    """Detects a leaking (passing) coil valve via uncommanded SAT shift (PNNL Re-tuning Ch.5/7)."""

    name = "leaking_valve"
    # the cooling coil + air temps are required; the heating coil is optional, so
    # the rule also runs on cooling-only AHUs (heating-leak signature then n/a)
    roles_required = (Role.COOL_VALVE, Role.MIXED_AIR_TEMP, Role.SUPPLY_AIR_TEMP)
    roles_optional = (Role.HEAT_VALVE,)

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        cols = {r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns}
        legacy = frame.rename(columns=cols)
        res = analyze_leak_valves(legacy, equip)
        if res is None:
            return Finding(rule=self.name, equip=equip, severity="info",
                           summary="insufficient data")
        worst = max(res.hw_leak_pct, res.chw_leak_pct)
        severity = "fault" if worst >= 30.0 else ("warn" if worst >= 10.0 else "ok")
        which = ("HW (heating) coil" if res.hw_leak_pct >= res.chw_leak_pct
                 else "CHW (cooling) coil")
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "hw_leak_pct": res.hw_leak_pct,
                "chw_leak_pct": res.chw_leak_pct,
                "median_delta_f": res.median_delta_f,
                "n_both_closed": res.n_both_closed,
            },
            summary=(f"{equip}: with both valves shut, air shifts (median "
                     f"{res.median_delta_f:+.1f}F); HW-leak {res.hw_leak_pct:.0f}% / "
                     f"CHW-leak {res.chw_leak_pct:.0f}% of closed hours "
                     f"(worst: {which})"),
        )
