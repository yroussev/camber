"""Load-profile and peak-demand metrics from interval meter data.

Quantifies the shape of a load: how peaky vs flat, how high the base load sits,
and how often the peak is approached. Drives demand-charge, scheduling, and
equipment-sizing analysis. Works for any interval series (kW, GPM, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class LoadMetrics:
    """Shape metrics for an interval load series."""

    peak: float
    near_peak: float          # 3rd-highest (robust to single spikes)
    base: float
    near_base: float          # 3rd-lowest (robust to single dropouts)
    mean: float
    base_to_peak: float       # near_base / near_peak (->1 is flat)
    load_factor: float        # mean / peak (->1 is flat, efficient use of capacity)
    n: int


def load_metrics(series: pd.Series) -> LoadMetrics:
    """Peak/base, base-to-peak ratio, and load factor for an interval series.

    ``near_peak``/``near_base`` use the 3rd most extreme value to shrug off lone
    spikes/dropouts, per common load-profiling practice. A high base-to-peak ratio
    (>~0.75) flags an excessive base load worth investigating; a low load factor
    means capacity is sized for brief peaks.
    """
    s = series.dropna().to_numpy(dtype="float64")
    if len(s) < 3:
        raise ValueError("need >= 3 points for load metrics")
    ordered = np.sort(s)
    peak, base, mean = float(s.max()), float(s.min()), float(s.mean())
    near_peak = float(ordered[-3])
    near_base = float(ordered[2])
    b2p = near_base / near_peak if near_peak else float("nan")
    lf = mean / peak if peak else float("nan")
    return LoadMetrics(peak=round(peak, 4), near_peak=round(near_peak, 4),
                       base=round(base, 4), near_base=round(near_base, 4),
                       mean=round(mean, 4), base_to_peak=round(b2p, 4),
                       load_factor=round(lf, 4), n=int(len(s)))


def load_duration(series: pd.Series) -> np.ndarray:
    """Load-duration curve: values sorted high-to-low (x = % of hours, y = load)."""
    return np.sort(series.dropna().to_numpy(dtype="float64"))[::-1]


def daily_profile(series: pd.Series) -> pd.Series:
    """Average load by hour-of-day (0-23) -- the mean daily load shape."""
    s = series.dropna()
    return s.groupby(pd.DatetimeIndex(s.index).hour).mean()


def weekday_weekend_profiles(series: pd.Series):
    """(weekday, weekend) average hour-of-day profiles, to expose schedule gaps."""
    s = series.dropna()
    idx = pd.DatetimeIndex(s.index)
    wk = s[idx.dayofweek < 5].groupby(pd.DatetimeIndex(s[idx.dayofweek < 5].index).hour).mean()
    we = s[idx.dayofweek >= 5].groupby(pd.DatetimeIndex(s[idx.dayofweek >= 5].index).hour).mean()
    return wk, we
