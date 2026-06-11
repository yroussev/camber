"""Demand & peak analytics: peak drivers, load factor, baseload, peak-shave value.

Demand charges and peak load are their own cost driver, distinct from total energy. This
module characterizes the *shape* of an electrical load:

- **peak demand and its drivers** -- the monthly peaks, when they occur (hour-of-day,
  day-of-week), and how "peaky" the load is (how few intervals set the peak),
- **load factor** -- average / peak, the classic measure of how efficiently the
  connected capacity is used (low = spiky, demand-charge-heavy),
- **baseload anomaly** -- the unoccupied (night/weekend) load relative to the occupied
  load; a high ratio means equipment isn't setting back and runs around the clock,
- **peak-shave value** -- the demand-charge dollars recoverable by capping the monthly
  peak at a target kW (the business case for load-shifting / batteries / controls).

Operates on an interval kW series (a meter or a piece of equipment). pandas + numpy only.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from .schedules import occupied_mask


@dataclass
class DemandResult:
    """Peak/shape characterization of an interval kW load."""

    n: int
    peak_kw: float
    peak_time: str                # when the overall peak occurred
    peak_hour: int                # hour-of-day of the overall peak
    peak_dayofweek: int           # 0=Mon .. 6=Sun
    avg_kw: float
    load_factor: float            # avg / peak (0..1; low = spiky)
    baseload_kw: float            # 5th-percentile load
    baseload_frac: float          # baseload / peak
    pct_intervals_near_peak: float  # % of intervals within near_peak_frac of the peak
    coincident_peak_hour: int     # modal hour-of-day across the monthly peaks
    monthly_peak_kw: dict         # {"YYYY-MM": peak kW}

    def as_dict(self) -> dict:
        """Return the result as a plain dict."""
        return asdict(self)


def analyze_demand(load_kw: pd.Series, *, near_peak_frac: float = 0.9,
                   baseload_q: float = 0.05) -> DemandResult | None:
    """Characterize an interval kW load's peak and shape."""
    s = load_kw.dropna()
    if len(s) < 10:
        return None
    peak = float(s.max())
    peak_t = s.idxmax()
    monthly = s.resample("MS").max()
    monthly_peaks = {d.strftime("%Y-%m"): round(float(v), 2)
                     for d, v in monthly.items() if v == v}
    peak_hours = [s.loc[mstart:mstart + pd.offsets.MonthEnd(0)].idxmax().hour
                  for mstart in monthly.index if monthly.loc[mstart] == monthly.loc[mstart]]
    coincident = int(pd.Series(peak_hours).mode().iloc[0]) if peak_hours else int(peak_t.hour)
    avg = float(s.mean())
    base = float(s.quantile(baseload_q))
    near = float((s >= near_peak_frac * peak).mean())

    return DemandResult(
        n=int(len(s)),
        peak_kw=round(peak, 2),
        peak_time=str(peak_t),
        peak_hour=int(peak_t.hour),
        peak_dayofweek=int(peak_t.dayofweek),
        avg_kw=round(avg, 2),
        load_factor=round(avg / peak, 3) if peak else float("nan"),
        baseload_kw=round(base, 2),
        baseload_frac=round(base / peak, 3) if peak else float("nan"),
        pct_intervals_near_peak=round(100.0 * near, 2),
        coincident_peak_hour=coincident,
        monthly_peak_kw=monthly_peaks,
    )


@dataclass
class BaseloadResult:
    """Unoccupied (night/weekend) load vs occupied load -- a setback check."""

    occupied_avg_kw: float
    unoccupied_avg_kw: float
    baseload_ratio: float         # unoccupied avg / occupied avg
    baseload_kw: float            # 5th-percentile load
    severity: str                 # "ok" | "warn" | "fault"
    summary: str

    def as_dict(self) -> dict:
        """Return the result as a plain dict."""
        return asdict(self)


def baseload_anomaly(load_kw: pd.Series, *, start_hour: int = 7, end_hour: int = 18,
                     warn_ratio: float = 0.6, fault_ratio: float = 0.8) -> BaseloadResult | None:
    """Flag a high unoccupied/occupied load ratio (equipment not setting back).

    Occupied = weekday ``start_hour``..``end_hour``; everything else is unoccupied. A
    healthy building drops substantially at night/weekend; a ratio near 1 means it runs
    around the clock. Thresholds are our judgment (PNNL Re-tuning night/weekend scheduling).
    """
    s = load_kw.dropna()
    if len(s) < 10:
        return None
    occ = occupied_mask(s.index, start_hour=start_hour, end_hour=end_hour)
    occ_avg = float(s[occ].mean()) if occ.any() else float("nan")
    unocc_avg = float(s[~occ].mean()) if (~occ).any() else float("nan")
    ratio = unocc_avg / occ_avg if occ_avg else float("nan")
    if ratio == ratio and ratio >= fault_ratio:
        severity = "fault"
    elif ratio == ratio and ratio >= warn_ratio:
        severity = "warn"
    else:
        severity = "ok"
    return BaseloadResult(
        occupied_avg_kw=round(occ_avg, 2),
        unoccupied_avg_kw=round(unocc_avg, 2),
        baseload_ratio=round(ratio, 3) if ratio == ratio else float("nan"),
        baseload_kw=round(float(s.quantile(0.05)), 2),
        severity=severity,
        summary=(f"unoccupied load is {100 * ratio:.0f}% of occupied "
                 f"({unocc_avg:.0f} vs {occ_avg:.0f} kW avg)"),
    )


@dataclass
class PeakShaveResult:
    """Demand-charge dollars recoverable by capping the monthly peak at a target."""

    target_kw: float
    demand_rate: float
    annual_savings: float
    n_months: int
    monthly: dict                 # {"YYYY-MM": {"peak_kw", "shaved_kw", "savings"}}

    def as_dict(self) -> dict:
        """Return the result as a plain dict."""
        return asdict(self)


def peak_shave_savings(load_kw: pd.Series, target_kw: float, *,
                       demand_rate: float) -> PeakShaveResult | None:
    """Demand savings from capping each month's peak at ``target_kw`` (at ``demand_rate``).

    A first-order business case for peak shaving: it assumes the peak can be held at the
    target (by load-shifting, storage, or controls) and prices the avoided demand charge;
    it does not model how the shaving is achieved.
    """
    s = load_kw.dropna()
    if s.empty:
        return None
    monthly, total = {}, 0.0
    for d, peak in s.resample("MS").max().items():
        if peak != peak:
            continue
        shaved = max(0.0, float(peak) - target_kw)
        save = shaved * demand_rate
        total += save
        monthly[d.strftime("%Y-%m")] = {"peak_kw": round(float(peak), 2),
                                        "shaved_kw": round(shaved, 2),
                                        "savings": round(save, 2)}
    return PeakShaveResult(target_kw=float(target_kw), demand_rate=float(demand_rate),
                           annual_savings=round(total, 2), n_months=len(monthly),
                           monthly=monthly)
