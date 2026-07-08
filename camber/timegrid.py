"""Shared time-grid helper: the interval width of a DatetimeIndex.

Consolidates the ``_interval_hours`` logic that several modules had each re-implemented, and hardens
it against duplicate / zero / negative gaps (concatenated overlapping exports) that would otherwise
yield a zero interval and silently zero out any energy computed from it.
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
