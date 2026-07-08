"""Time-grid handling for interval data — interval width, de-duplication, and DST.

BAS trend exports arrive as **naive local time** (:mod:`camber.realio` strips the ``PDT``/``PST``
abbreviation), so daylight-saving transitions leave two artifacts in the index: the **fall-back**
hour repeats (duplicate timestamps) and the **spring-forward** hour is missing (a gap). Concatenated
overlapping exports duplicate timestamps too. This module centralizes robust handling:

- :func:`interval_hours` — modal interval width, immune to duplicate/zero gaps;
- :func:`regularize` — sort and collapse duplicate timestamps on a Series/DataFrame;
- :func:`localize` — tz-localize a naive local index, resolving DST ambiguous/nonexistent times;
- :func:`dst_anomalies` — count duplicate timestamps and (given a tz) the DST fall-back/spring-forward
  transitions in the index.

numpy/pandas only.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def interval_hours(index) -> float:
    """Modal interval width (in hours) of a ``DatetimeIndex``.

    Uses only strictly-positive gaps, so duplicate or out-of-order timestamps don't collapse the
    result to zero. Returns ``1.0`` for fewer than two points or when no positive gap exists.
    """
    idx = pd.DatetimeIndex(index)
    if len(idx) < 2:
        return 1.0
    deltas = np.diff(idx.asi8) / 3.6e12          # ns -> hours; asi8 avoids the deprecated .view
    deltas = deltas[deltas > 0]
    return float(np.median(deltas)) if len(deltas) else 1.0


def regularize(obj, *, dedupe: str = "first", sort: bool = True):
    """Return ``obj`` (a Series or DataFrame) with a clean monotonic, unique time index.

    Sorts the index and collapses duplicate timestamps — the DST fall-back hour and concatenated
    overlapping exports. ``dedupe``: ``"first"`` / ``"last"`` keep one row; ``"mean"`` averages the
    duplicates (numeric); ``None`` leaves duplicates in place.
    """
    if dedupe not in ("first", "last", "mean", None):
        raise ValueError("dedupe must be 'first', 'last', 'mean', or None")
    out = obj.sort_index() if sort else obj
    idx = pd.DatetimeIndex(out.index)
    if dedupe is None or not idx.has_duplicates:
        return out
    if dedupe in ("first", "last"):
        return out[~idx.duplicated(keep=dedupe)]
    return out.groupby(level=0).mean()


def localize(index, tz, *, ambiguous="infer", nonexistent="shift_forward") -> pd.DatetimeIndex:
    """Attach a timezone to a naive local ``index`` (or convert a tz-aware one), resolving DST.

    ``ambiguous`` handles the fall-back repeated hour (default ``"infer"`` — order-based), and
    ``nonexistent`` handles the spring-forward missing hour (default shift the skipped time forward).
    """
    idx = pd.DatetimeIndex(index)
    if idx.tz is not None:
        return idx.tz_convert(tz)
    return idx.tz_localize(tz, ambiguous=ambiguous, nonexistent=nonexistent)


def dst_anomalies(index, tz=None) -> dict:
    """Report time-index anomalies: ``duplicate_timestamps`` always, plus — when ``tz`` is given —
    ``fallback_ambiguous`` (repeated local hours) and ``springforward_nonexistent`` (skipped local
    hours) for that timezone."""
    idx = pd.DatetimeIndex(index)
    out = {"duplicate_timestamps": int(idx.duplicated().sum())}
    if tz is not None:
        u = pd.DatetimeIndex(idx.unique())
        amb = u.tz_localize(tz, ambiguous="NaT", nonexistent="shift_forward")
        ne = u.tz_localize(tz, ambiguous=True, nonexistent="NaT")
        out["fallback_ambiguous"] = int(amb.isna().sum())
        out["springforward_nonexistent"] = int(ne.isna().sum())
    return out
