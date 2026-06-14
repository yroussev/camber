"""Ingest-readiness ribbon (pattern A): show what the data looks like before analytics.

A raw BAS export has gaps, clock drift, and mixed intervals; a bare average hides all of it.
This draws a per-point **presence ribbon** over the time axis — green where samples land in a
time bin, blank where they're missing — with each point's coverage % in the label, so you can
*see* the data's readiness before trusting a number computed from it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _as_frame(data) -> pd.DataFrame:
    return data.to_frame() if isinstance(data, pd.Series) else data


def presence_matrix(data, *, max_bins: int = 240):
    """Return ``(matrix, bin_starts, coverage)``: a points×bins 0/1 presence grid.

    Each column is resampled into ``≈ max_bins`` equal time bins; a cell is 1 if any sample of
    that point falls in the bin. ``coverage`` is each point's non-null fraction.
    """
    df = _as_frame(data)
    idx = pd.DatetimeIndex(df.index)
    cols = list(df.columns)
    if df.empty or len(idx) < 2:
        return np.zeros((len(cols), 0)), pd.DatetimeIndex([]), [0.0] * len(cols)
    span = idx.max() - idx.min()
    bin_w = max(pd.Timedelta(span / max_bins), pd.Timedelta("1min"))
    rows, coverage = [], []
    for c in cols:
        s = df[c]
        present = s.notna().resample(bin_w).max().astype(float)
        rows.append(present)
        coverage.append(float(s.notna().mean()))
    grid = pd.concat(rows, axis=1).fillna(0.0)
    return grid.to_numpy().T, pd.DatetimeIndex(grid.index), coverage


def readiness_ribbon(data, *, ax=None, max_bins: int = 240, title: str | None = None,
                     max_xticks: int = 10):
    """Draw the per-point presence ribbon (points on y, time on x). Returns the Axes."""
    import matplotlib.pyplot as plt

    mat, bins, coverage = presence_matrix(data, max_bins=max_bins)
    cols = list(_as_frame(data).columns)
    if ax is None:
        _, ax = plt.subplots(figsize=(12, 0.4 * max(len(cols), 1) + 1.2))
    if mat.size == 0:
        ax.set_title(title or "Readiness ribbon — no data")
        return ax

    ax.imshow(mat, aspect="auto", cmap="RdYlGn", vmin=0, vmax=1, interpolation="nearest")
    ax.set_yticks(range(len(cols)))
    ax.set_yticklabels([f"{c}  ({coverage[i] * 100:.0f}%)" for i, c in enumerate(cols)], fontsize=8)
    n = len(bins)
    step = max(1, n // max_xticks)
    pos = list(range(0, n, step))
    ax.set_xticks(pos)
    ax.set_xticklabels([pd.Timestamp(bins[p]).strftime("%Y-%m-%d") for p in pos],
                       rotation=45, ha="right", fontsize=8)
    ax.set_xlabel("Time")
    ax.set_title(title or f"Ingest readiness — {len(cols)} points, green = data present")
    return ax
