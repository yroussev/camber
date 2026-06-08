"""Rule: boiler summer-lockout + hot-water-temp reset.

A comfort-only boiler running in cooling weather, or hot-water supply temp not
reset down with load, wastes gas -- the heating side of the overcool-then-reheat
problem (PNNL Re-tuning Ch.8). Adapts :func:`camber.plant.analyze_hw_plant` to
the role-frame interface.

The summer-lockout OAT threshold is climate-dependent and injected at construction
(a climate-zone config supplies the per-zone value); OAT itself comes via the
runner's ``shared`` channel since it is a building-level point.
"""

from __future__ import annotations

import math

import pandas as pd

from ..model.roles import Role
from ..plant import analyze_hw_plant
from .base import Finding

_ROLE_TO_HW_COL = {
    Role.BOILER_STATUS: "BoilerStatus",
    Role.HW_SUPPLY_TEMP: "HWS_Temp",
    Role.HW_RETURN_TEMP: "HWR_Temp",
    Role.HW_DIFF_PRESS: "HW_DiffPress",
    Role.OAT: "OAT",
}


class BoilerSummerLockout:
    """Detects a boiler running in cooling weather / no HWS reset (PNNL Re-tuning Ch.8)."""

    name = "boiler_summer_lockout"
    roles_required = (Role.BOILER_STATUS,)
    roles_optional = (Role.HW_SUPPLY_TEMP, Role.HW_RETURN_TEMP, Role.HW_DIFF_PRESS,
                      Role.OAT)

    def __init__(self, summer_lockout_oat_f: float = 65.0):
        # climate-dependent knob; default generic, override per climate zone
        self.summer_lockout_oat_f = summer_lockout_oat_f

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        cols = {r: c for r, c in _ROLE_TO_HW_COL.items() if r in frame.columns}
        legacy = frame.rename(columns=cols)
        res = analyze_hw_plant(legacy, equip,
                               summer_lockout_oat_f=self.summer_lockout_oat_f)
        if res is None:
            return Finding(rule=self.name, equip=equip, severity="info",
                           summary="insufficient data")
        # Severity from summer-running: a comfort boiler should rarely run hot-weather.
        sp = res.summer_run_pct
        severity = "fault" if sp >= 20.0 else ("warn" if sp >= 5.0 else "ok")
        reset_note = ("HWS reset present" if res.hws_reset_present
                      else "no/weak HWS reset")
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "boiler_running_pct": res.boiler_running_pct,
                "summer_run_pct": res.summer_run_pct,
                "lockout_oat_f": res.lockout_oat_f,
                "hws_median_f": res.hws_median_f,
                "hws_slope_per_F": res.hws_slope_per_F,
                "hws_reset_present": res.hws_reset_present,
                "n_running": res.n_running,
                "n_considered": res.n_considered,
            },
            summary=(f"{equip}: boiler runs {res.boiler_running_pct:.0f}% of occupied "
                     f"hours; {res.summer_run_pct:.0f}% of running hours at "
                     f"OAT>{res.lockout_oat_f:.0f}F; {reset_note} "
                     f"(HWS~OAT slope {res.hws_slope_per_F:+.2f})"),
        )
