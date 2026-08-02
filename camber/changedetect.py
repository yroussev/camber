"""Operational change-point detection — find *when* a series shifts level.

Distinct from the change-point *models* in :mod:`camber.mandv.models` (energy vs temperature), this
finds **step changes in time**: the timestamps where a signal's mean shifts. That answers the
monitoring-based-commissioning questions — did a control change actually take effect? did a fixed
measure persist or silently regress? did equipment degrade? — without being told the date to look
at.

The method is transparent binary segmentation: the most likely single change point maximizes the
CUSUM of the mean-centered signal; a two-sample statistic gates significance; recurse on each half.
numpy/pandas only.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

__all__ = [
    "LevelShift",
    "detect_level_shifts",
    "largest_shift",
]


@dataclass
class LevelShift:
    """A detected step change in a series' mean."""

    at: object  # timestamp (or index position) of the shift
    before_mean: float
    after_mean: float
    delta: float  # after − before
    score: float  # standardized two-sample statistic at the split

    def as_dict(self) -> dict:
        d = asdict(self)
        d["at"] = str(self.at)
        return d


def _best_split(x, min_segment: int = 1):
    """Index k (split after position k) maximizing the CUSUM of the mean-centered signal, and the
    standardized two-sample statistic there. The split is constrained so both sides have at least
    ``min_segment`` points — otherwise a lone boundary outlier makes a 1-point segment with a huge
    (near-zero-variance) statistic and fakes a regime change."""
    n = len(x)
    lo, hi = (
        min_segment - 1,
        n - min_segment,
    )  # valid CUSUM positions -> k in [min_segment, n-min_segment]
    if hi <= lo:
        return None, 0.0
    cs = np.cumsum(x - x.mean())
    k = int(np.argmax(np.abs(cs[lo:hi]))) + lo + 1
    a, b = x[:k], x[k:]
    va, vb = a.var(ddof=1) if len(a) > 1 else 0.0, b.var(ddof=1) if len(b) > 1 else 0.0
    se = np.sqrt(va / len(a) + vb / len(b))
    score = abs(b.mean() - a.mean()) / se if se > 0 else (np.inf if b.mean() != a.mean() else 0.0)
    return k, float(score)


def detect_level_shifts(
    series: pd.Series,
    *,
    min_segment: int = 24,
    max_shifts: int = 8,
    z: float = 4.0,
    min_delta: float | None = None,
) -> list:
    """Detect step changes in ``series``' mean (binary segmentation). Returns worst-first
    :class:`LevelShift` list.

    A candidate split is accepted when its standardized two-sample statistic exceeds ``z``; segments
    below ``min_segment`` points are not split further. Once all breakpoints are found, each shift's
    ``before``/``after`` means are computed from the **adjacent** segments (so an early shift's
    level isn't blurred by a later regime), and ``min_delta`` filters small clean level changes. At
    most ``max_shifts`` are returned.
    """
    s = series.dropna()
    vals = s.to_numpy(dtype=float)
    idx = s.index
    n = len(vals)

    # 1) binary segmentation -> breakpoint positions (with the split score)
    breaks: list = []
    stack = [(0, n)]
    while stack and len(breaks) < max_shifts:
        lo, hi = stack.pop()
        if hi - lo < 2 * min_segment:
            continue
        k, score = _best_split(vals[lo:hi], min_segment)
        if k is None or score < z:
            continue
        pos = lo + k
        breaks.append((pos, score))
        stack.append((lo, pos))
        stack.append((pos, hi))

    if not breaks:
        return []
    # 2) clean adjacent-segment means using ALL breakpoints as boundaries
    positions = sorted(p for p, _ in breaks)
    score_at = {p: sc for p, sc in breaks}
    bounds = [0] + positions + [n]
    shifts = []
    for i, p in enumerate(positions):
        before = vals[bounds[i] : p]
        after = vals[p : bounds[i + 2]]
        delta = float(after.mean() - before.mean())
        if min_delta is not None and abs(delta) < min_delta:
            continue
        shifts.append(
            LevelShift(
                at=idx[p],
                before_mean=round(float(before.mean()), 4),
                after_mean=round(float(after.mean()), 4),
                delta=round(delta, 4),
                score=round(score_at[p], 3),
            )
        )
    shifts.sort(key=lambda ls: -ls.score)
    return shifts[:max_shifts]


def largest_shift(series: pd.Series, **kwargs):
    """The single most significant level shift in ``series``, or None."""
    out = detect_level_shifts(series, max_shifts=1, **kwargs)
    return out[0] if out else None
