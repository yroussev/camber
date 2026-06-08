"""Chilled-water plant diagnostics: CHWST reset + low-deltaT (PNNL Ch.8).

Two part-load efficiency faults on a chilled-water plant:

1. **No chilled-water supply-temp (CHWST) reset.** Holding CHWST low at part load
   wastes chiller energy (~2% per degF it could be raised). A working reset raises
   CHWST when load/OAT falls; a flat CHWST vs OAT (and CHWST pinned low) = no reset.
2. **Low loop delta-T.** Loop deltaT (return - supply) well below design indicates
   degraded plant efficiency (low-deltaT syndrome): the loop moves a lot of water
   for little heat transfer. PNNL design example ~11.5F, bad < ~8F.

Method: restrict to *plant-running* hours (CHWST in a plausible chilled range, so
sensor dropouts at 0F and off-hours ambient readings are excluded), then regress
CHWST on OAT and summarize deltaT. OAT is typically building-level (pass via the
rule layer's ``shared`` channel).

Note: a CHW flow point is intentionally NOT required -- on some buildings it is
dead/untrended, so this diagnostic relies on temperatures, not flow.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from .schedules import occupied_mask

CHWPLANT_MEASURES = ["CHWS_Temp", "CHWR_Temp", "CHWS_SP", "OAT", "PumpSpeed"]


@dataclass
class CHWPlantResult:
    """Chilled-water plant CHWST reset and delta-T diagnostics for one plant."""

    equip: str
    n_running: int                # hours plant judged running
    chwst_median_f: float
    chwst_slope_per_F: float      # d(CHWST)/d(OAT); ~0 = no reset
    chwst_reset_present: bool
    pct_chwst_low: float          # % running hrs CHWST below low threshold
    deltaT_median_f: float
    low_deltaT_pct: float         # % running hrs deltaT < design_min
    design_deltaT_min_f: float
    coverage_start: str
    coverage_end: str

    def as_dict(self):
        """Return the result as a plain dict."""
        return asdict(self)


def _ols_slope(x, y):
    if len(x) < 10 or np.std(x) == 0:
        return float("nan")
    b, _ = np.polyfit(x, y, 1)
    return float(b)


def analyze_chw_plant(
    df: pd.DataFrame,
    equip: str,
    *,
    chwst_running_lo_f: float = 38.0,   # plausible chilled-water supply range...
    chwst_running_hi_f: float = 58.0,   # ...used to gate "plant running" (flow is dead)
    chwst_low_f: float = 46.0,          # CHWST at/below this = held low
    chwst_reset_slope_flat: float = 0.05,
    design_deltaT_min_f: float = 8.0,   # PNNL low-deltaT threshold
    occupied_only: bool = True,
) -> CHWPlantResult | None:
    """Diagnose a chilled-water plant. ``df`` columns are measure names.

    Thresholds are OUR engineering judgment / PNNL Ch.8 guidance:
      running gate 38-58F  -- CHWST outside this is plant-off or sensor dropout.
      chwst_low_f=46F      -- typical low CHWST design; at/below = "held low".
      reset_slope_flat=.05 -- |d(CHWST)/d(OAT)| below this = effectively no reset.
      design_deltaT_min=8F -- loop deltaT below this = low-deltaT syndrome (Ch.8).
    """
    if "CHWS_Temp" not in df.columns:
        return None
    work = df.copy()
    if occupied_only:
        work = work[occupied_mask(work.index)]
    # gate on plant-running via plausible CHWST (flow point is unreliable)
    sup = work["CHWS_Temp"]
    running = sup.between(chwst_running_lo_f, chwst_running_hi_f)
    work = work[running]
    n = len(work)
    if n == 0:
        return None
    sup = work["CHWS_Temp"]

    chwst_median = round(float(sup.median()), 1)
    pct_low = round(100.0 * float((sup <= chwst_low_f).mean()), 1)

    if "OAT" in work.columns:
        r = work[["CHWS_Temp", "OAT"]].dropna()
        slope = _ols_slope(r["OAT"].values, r["CHWS_Temp"].values) if len(r) >= 10 else float("nan")
    else:
        slope = float("nan")
    # reset present if CHWST varies materially with OAT (either sign of modulation)
    reset_present = (not np.isnan(slope)) and abs(slope) >= chwst_reset_slope_flat

    if "CHWR_Temp" in work.columns:
        dt = (work["CHWR_Temp"] - work["CHWS_Temp"]).dropna()
        dt = dt[dt.between(-2, 40)]  # drop nonsense from sensor dropouts
        dt_median = round(float(dt.median()), 1) if len(dt) else float("nan")
        low_dt_pct = round(100.0 * float((dt < design_deltaT_min_f).mean()), 1) if len(dt) else float("nan")
    else:
        dt_median = low_dt_pct = float("nan")

    return CHWPlantResult(
        equip=equip,
        n_running=n,
        chwst_median_f=chwst_median,
        chwst_slope_per_F=round(slope, 3) if not np.isnan(slope) else float("nan"),
        chwst_reset_present=bool(reset_present),
        pct_chwst_low=pct_low,
        deltaT_median_f=dt_median,
        low_deltaT_pct=low_dt_pct,
        design_deltaT_min_f=float(design_deltaT_min_f),
        coverage_start=str(df.index.min()),
        coverage_end=str(df.index.max()),
    )
