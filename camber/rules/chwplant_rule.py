"""Rule: chilled-water plant CHWST reset + low-deltaT (PNNL Ch.8).

Flags a chilled-water plant holding supply temp low at part load (no reset) and/or
running at low loop delta-T. Adapts :func:`camber.chwplant.analyze_chw_plant` to
the role-frame interface. OAT (the reset regressor) comes via the runner's
``shared`` channel since it is building-level.
"""

from __future__ import annotations

import pandas as pd

from ..chwplant import analyze_chw_plant
from ..model.roles import Role
from .base import Finding

_ROLE_TO_COL = {
    Role.CHW_SUPPLY_TEMP: "CHWS_Temp",
    Role.CHW_RETURN_TEMP: "CHWR_Temp",
    Role.CHW_SUPPLY_TEMP_SP: "CHWS_SP",
    Role.OAT: "OAT",
}


class CHWPlantReset:
    """Detects no CHWST reset and/or low loop delta-T at the chilled-water plant (PNNL Re-tuning Ch.8)."""

    name = "chw_plant_reset"
    roles_required = (Role.CHW_SUPPLY_TEMP,)
    roles_optional = (Role.CHW_RETURN_TEMP, Role.CHW_SUPPLY_TEMP_SP, Role.OAT)

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        cols = {r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns}
        legacy = frame.rename(columns=cols)
        res = analyze_chw_plant(legacy, equip)
        if res is None:
            return Finding(rule=self.name, equip=equip, severity="info",
                           summary="insufficient data")
        # Low-deltaT is the clearest part-load fault; no-reset compounds it.
        low_dt = res.low_deltaT_pct if res.low_deltaT_pct == res.low_deltaT_pct else 0.0
        if low_dt >= 50.0:
            severity = "fault"
        elif low_dt >= 20.0 or not res.chwst_reset_present:
            severity = "warn"
        else:
            severity = "ok"
        reset_note = "CHWST reset present" if res.chwst_reset_present else "flat CHWST (no reset)"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "chwst_median_f": res.chwst_median_f,
                "chwst_slope_per_F": res.chwst_slope_per_F,
                "chwst_reset_present": res.chwst_reset_present,
                "pct_chwst_low": res.pct_chwst_low,
                "deltaT_median_f": res.deltaT_median_f,
                "low_deltaT_pct": res.low_deltaT_pct,
                "n_running": res.n_running,
            },
            summary=(f"{equip}: CHWST median {res.chwst_median_f:.1f}F, "
                     f"loop deltaT median {res.deltaT_median_f:.1f}F "
                     f"({res.low_deltaT_pct:.0f}% of running hours < "
                     f"{res.design_deltaT_min_f:.0f}F); {reset_note}"),
        )
