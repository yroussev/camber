"""Hot-water plant diagnostics: boiler summer-lockout, HW-temp reset, loop low-deltaT.

Per PNNL Re-tuning Ch.8 ("Boiler Lockout in Summer", "Boiler Trend Data
Analysis"): if a boiler serves only comfort reheat, it should be locked out in hot
weather, and hot-water supply temp should be reset down with load/OAT ("why heat
with 180F water when 80F will do"). Running the boiler in cooling weather is the
gas side of the simultaneous-heat/overcool problem.

Two indicators:
1. **Summer-lockout violation** -- fraction of boiler-running hours at OAT above a
   *climate-dependent* lockout threshold. That threshold is the one genuinely
   climate-sensitive knob here (a mild-coastal zone locks out lower than a hot
   desert), so it is an injected parameter, not a constant -- a climate-zone
   config supplies the per-zone value.
2. **HW-supply-temp reset** -- slope of HW supply temp vs OAT over boiler-running
   hours. A working reset lowers HWS as OAT rises (negative slope); a flat slope
   means no reset (heating with hotter water than needed).

OAT is typically a building-level point, so callers pass it via the rule layer's
``shared`` channel rather than expecting it per-plant.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from .schedules import occupied_mask

# Roles consumed (kept as strings here to avoid a hard import cycle with model;
# the rule wrapper maps Role -> these legacy column names).
HWPLANT_MEASURES = ["BoilerStatus", "HWS_Temp", "HWR_Temp", "HW_DiffPress", "OAT"]


@dataclass
class HWPlantResult:
    """Hot-water plant runtime, summer-lockout, and HWS reset diagnostics."""

    equip: str
    n_considered: int             # occupied hours with boiler data
    boiler_running_pct: float     # % of occupied hours boiler is running
    n_running: int
    summer_run_pct: float         # % of running hours at OAT > lockout threshold
    lockout_oat_f: float          # the (climate-dependent) threshold used
    hws_median_f: float
    hws_slope_per_F: float        # d(HWS)/d(OAT); <0 = reset present
    hws_reset_present: bool
    deltaT_median_f: float        # median loop dT (HWS - HWR) over running hours
    low_deltaT_pct: float         # % running hrs with loop dT < design_deltaT_min_f
    design_deltaT_min_f: float    # the design-minimum loop dT used
    dp_median: float
    coverage_start: str
    coverage_end: str

    def as_dict(self):
        """Return the result as a plain dict."""
        return asdict(self)


def _ols_slope(x: np.ndarray, y: np.ndarray):
    if len(x) < 10 or np.std(x) == 0:
        return float("nan")
    b, _ = np.polyfit(x, y, 1)
    return float(b)


def analyze_hw_plant(
    df: pd.DataFrame,
    equip: str,
    *,
    summer_lockout_oat_f: float = 65.0,   # climate-dependent; inject per climate zone
    hws_reset_slope_flat: float = 0.05,    # |slope| below this = effectively no reset
    design_deltaT_min_f: float = 20.0,     # HW loop dT below this = low-deltaT (overpumping)
    occupied_only: bool = True,
) -> HWPlantResult | None:
    """Diagnose a hot-water plant. ``df`` columns are measure names (see
    HWPLANT_MEASURES); ``BoilerStatus`` is 0/1, ``OAT`` building outdoor temp.

    ``summer_lockout_oat_f`` is the climate-zone knob: above this OAT a comfort-only
    boiler should be off. Default 65F is a generic mild threshold; a hot-desert zone
    (e.g. CA CZ15) would set this higher, a cool zone lower.
    """
    if "BoilerStatus" not in df.columns:
        return None
    work = df.copy()
    if occupied_only:
        work = work[occupied_mask(work.index)]
    work = work.dropna(subset=["BoilerStatus"])
    n = len(work)
    if n == 0:
        return None

    running = work["BoilerStatus"] > 0.5
    n_run = int(running.sum())
    boiler_running_pct = round(100.0 * n_run / n, 2)

    # summer-lockout violation (needs OAT)
    if "OAT" in work.columns and n_run:
        oat_run = work.loc[running, "OAT"].dropna()
        summer_run_pct = round(100.0 * float((oat_run > summer_lockout_oat_f).mean()), 2) \
            if len(oat_run) else 0.0
    else:
        summer_run_pct = 0.0

    # HW-supply-temp reset (slope vs OAT over running hours)
    if "HWS_Temp" in work.columns:
        hws = work["HWS_Temp"].dropna()
        hws_median = round(float(hws.median()), 1) if len(hws) else float("nan")
        if "OAT" in work.columns and n_run:
            r = work.loc[running, ["HWS_Temp", "OAT"]].dropna()
            slope = _ols_slope(r["OAT"].values, r["HWS_Temp"].values) if len(r) >= 10 else float("nan")
        else:
            slope = float("nan")
    else:
        hws_median = slope = float("nan")
    reset_present = (not np.isnan(slope)) and slope < -hws_reset_slope_flat

    # HW loop delta-T (HWS - HWR) over running hours. Low delta-T = overpumping:
    # the loop circulates a lot of water for little heat transfer (PNNL Ch.8, the
    # heating-side analogue of the chilled-water low-deltaT syndrome).
    deltaT_median = float("nan")
    low_dt_pct = float("nan")
    if {"HWS_Temp", "HWR_Temp"} <= set(work.columns) and n_run:
        d = work.loc[running, ["HWS_Temp", "HWR_Temp"]].dropna()
        dt = (d["HWS_Temp"] - d["HWR_Temp"])
        dt = dt[(dt > -2) & (dt < 120)]   # physical-ish guard
        if len(dt) >= 10:
            deltaT_median = round(float(dt.median()), 1)
            low_dt_pct = round(100.0 * float((dt < design_deltaT_min_f).mean()), 1)

    dp_median = round(float(work["HW_DiffPress"].median()), 2) \
        if "HW_DiffPress" in work.columns and work["HW_DiffPress"].notna().any() else float("nan")

    return HWPlantResult(
        equip=equip,
        n_considered=n,
        boiler_running_pct=boiler_running_pct,
        n_running=n_run,
        summer_run_pct=summer_run_pct,
        lockout_oat_f=float(summer_lockout_oat_f),
        hws_median_f=hws_median,
        hws_slope_per_F=round(slope, 3) if not np.isnan(slope) else float("nan"),
        hws_reset_present=bool(reset_present),
        deltaT_median_f=deltaT_median,
        low_deltaT_pct=low_dt_pct,
        design_deltaT_min_f=float(design_deltaT_min_f),
        dp_median=dp_median,
        coverage_start=str(df.index.min()),
        coverage_end=str(df.index.max()),
    )
