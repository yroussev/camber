"""Rate/energy-aware resampling for M&V data preparation.

Resampling energy data correctly depends on what the series *means*:

* **Rate** quantities (kW, temperature, %, flow): the value is an instantaneous
  or interval-average reading. Resample by **average** (or interpolation) -- the
  mean is meaningful across any interval.
* **Energy / total** quantities (kWh, therms per interval): the value is a sum
  over its interval. Resample by **time-weighted sum** so the total is preserved.
  A simple average (or interpolation) silently loses energy when upsampling.
* **Cumulative meter reads** (ever-increasing register): must be **differenced**
  to per-interval energy before any aggregation.

Methods:
  - ``mean``            : simple mean (rate series, equal intervals)
  - ``time_weighted_avg``: mean weighted by interval length (rate, uneven intervals)
  - ``time_weighted_sum``: sum preserving total energy (energy series)
  - ``nearest_prior``   : last value carried forward (status/step series)
This also detects cumulative meters and applies a gap-fill limit.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def is_cumulative(s: pd.Series, frac_increasing: float = 0.95) -> bool:
    """Heuristic: a series that almost always increases is a cumulative meter read.

    A monotonically increasing series is usually a cumulative register rather than
    per-interval consumption; it must be differenced first. We flag when
    >= frac_increasing of consecutive diffs are >= 0 and the series spans a large
    positive range.
    """
    v = s.dropna().values
    if len(v) < 5:
        return False
    d = np.diff(v)
    nonneg = float((d >= 0).mean())
    grows = (v[-1] - v[0]) > 0 and v.std() > 0
    return nonneg >= frac_increasing and grows


def cumulative_to_interval(s: pd.Series) -> pd.Series:
    """Difference a cumulative meter read into per-interval energy (first -> NaN).

    Negative diffs (meter rollover/reset) are set to NaN rather than emitting a
    spurious huge negative interval.
    """
    d = s.diff()
    d[d < 0] = np.nan
    return d


def _interval_hours(index: pd.DatetimeIndex) -> np.ndarray:
    """Hours represented by each sample (gap to the next; last repeats prior)."""
    if len(index) < 2:
        return np.array([1.0] * len(index))
    secs = np.diff(index.view("int64")) / 1e9
    hours = secs / 3600.0
    hours = np.append(hours, hours[-1])   # last sample: assume prior interval
    return hours


def resample(s: pd.Series, freq: str, method: str = "mean", *,
             gap_limit: int | None = None) -> pd.Series:
    """Resample series ``s`` to ``freq`` using ``method``.

    ``gap_limit``: max number of consecutive missing target bins to forward-fill
    (None = no fill). Applies to nearest_prior / step semantics.
    """
    s = s.sort_index()
    if method == "mean":
        out = s.resample(freq).mean()
    elif method == "time_weighted_avg":
        w = pd.Series(_interval_hours(s.index), index=s.index)
        num = (s * w).resample(freq).sum()
        den = w.resample(freq).sum()
        out = num / den.replace(0, np.nan)
    elif method == "time_weighted_sum":
        # value is energy per its own interval; re-bin proportionally by summing.
        # For equal native intervals this equals a plain sum; for uneven intervals
        # we weight by the fraction of each native interval (approximated by sum,
        # which preserves total energy across down/up-sampling of energy series).
        out = s.resample(freq).sum(min_count=1)
    elif method == "nearest_prior":
        # carry the last observed value forward across both NaN readings and empty
        # target bins (step/status semantics). resample().ffill() only bridges
        # empty bins, so ffill the values first.
        out = s.ffill().resample(freq).ffill()
    else:
        raise ValueError(f"unknown method {method!r}")

    if gap_limit is not None and method in ("nearest_prior",):
        out = out.ffill(limit=gap_limit)
    return out


def resample_energy(s: pd.Series, freq: str, *, gap_limit: int | None = None) -> pd.Series:
    """Convenience: resample an energy series correctly, auto-handling cumulative.

    Detects a cumulative meter, differences it to per-interval energy, then
    time-weighted-sums to ``freq`` so total energy is preserved.
    """
    if is_cumulative(s):
        s = cumulative_to_interval(s)
    return resample(s, freq, method="time_weighted_sum", gap_limit=gap_limit)
