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
    """Detects no CHWST reset and/or low loop delta-T at the chilled-water plant
    (PNNL Re-tuning Ch.8)."""

    name = "chw_plant_reset"
    roles_required = (Role.CHW_SUPPLY_TEMP,)
    roles_optional = (Role.CHW_RETURN_TEMP, Role.CHW_SUPPLY_TEMP_SP, Role.OAT)

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        cols = {r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns}
        legacy = frame.rename(columns=cols)
        res = analyze_chw_plant(legacy, equip)
        if res is None:
            return Finding(
                rule=self.name, equip=equip, severity="info", summary="insufficient data"
            )
        caveats = []
        # Low-deltaT sub-check: a NaN pct means no usable CHW return temp -> not evaluated
        # (must not count toward the fault). Reset sub-check: None means OAT was absent/thin
        # -> not evaluated (must not read as a confident "no reset").
        dt_evaluated = res.low_deltaT_pct == res.low_deltaT_pct  # False when NaN
        low_dt = res.low_deltaT_pct if dt_evaluated else 0.0
        if not dt_evaluated:
            caveats.append("loop deltaT not evaluated: no CHW return temp")
        reset = res.chwst_reset_present  # True / False / None
        if reset is None:
            caveats.append("CHWST reset not evaluated: no/insufficient OAT")
        # Severity: only a genuinely-evaluated flat reset (is False) downgrades; None does not.
        if low_dt >= 50.0:
            severity = "fault"
        elif low_dt >= 20.0 or reset is False:
            severity = "warn"
        else:
            severity = "ok"
        reset_note = (
            "CHWST reset present"
            if reset is True
            else "flat CHWST (no reset)"
            if reset is False
            else "CHWST reset not evaluated (no OAT)"
        )
        dt_note = (
            f"loop deltaT median {res.deltaT_median_f:.1f}F "
            f"({res.low_deltaT_pct:.0f}% of running hours < {res.design_deltaT_min_f:.0f}F)"
            if dt_evaluated
            else "loop deltaT not evaluated (no return temp)"
        )
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
            summary=f"{equip}: CHWST median {res.chwst_median_f:.1f}F, {dt_note}; {reset_note}",
            caveats=caveats,
        )
