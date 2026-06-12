"""Load carpet (carpet plot / heatmap): hour-of-day x date, colored by load.

A carpet plot lays every day side by side as a vertical strip of 24 hourly cells, so a
whole year of operation reads at a glance: occupied/unoccupied bands, weekend setback (or
its absence), seasonal swing, schedule drift, and overnight baseload all show up as visual
structure that a time-series line buries. The classic companion to the night/weekend
baseload check in :mod:`camber.demand`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def carpet_matrix(load: pd.Series, *, agg: str = "mean"):
    """Reshape a datetime-indexed Series into an (hour x date) matrix.

    Returns ``(matrix, hours, dates)`` where ``matrix[h, d]`` aggregates all samples in hour
    ``h`` of day ``d`` (rows 0..23 ascending). Missing hour/day cells are NaN.
    """
    s = pd.Series(load).dropna()
    if s.empty:
        return np.empty((0, 0)), np.array([]), np.array([])
    idx = pd.DatetimeIndex(s.index)
    dates = idx.normalize()
    pivot = (s.groupby([idx.hour, dates]).agg(agg)
             .unstack().reindex(range(24)).sort_index())
    return pivot.to_numpy(dtype=float), pivot.index.to_numpy(), pivot.columns


def load_carpet(load: pd.Series, *, agg: str = "mean", cmap: str = "viridis",
                ax=None, title: str | None = None, label: str = "Load (kW)",
                max_xticks: int = 12):
    """Draw a load carpet (hour-of-day on y, date on x, color = load). Returns the Axes."""
    import matplotlib.pyplot as plt

    mat, hours, dates = carpet_matrix(load, agg=agg)
    if ax is None:
        _, ax = plt.subplots(figsize=(13, 4))
    if mat.size == 0:
        ax.set_title(title or "Load carpet — no data")
        return ax

    im = ax.imshow(mat, aspect="auto", origin="lower", cmap=cmap,
                   extent=(0, mat.shape[1], 0, 24), interpolation="nearest")
    ax.figure.colorbar(im, ax=ax, label=label)
    ax.set_ylabel("Hour of day")
    ax.set_yticks(range(0, 25, 6))
    ax.set_xlabel("Date")

    n = len(dates)
    step = max(1, n // max_xticks)
    pos = list(range(0, n, step))
    ax.set_xticks([p + 0.5 for p in pos])
    ax.set_xticklabels([pd.Timestamp(dates[p]).strftime("%Y-%m-%d") for p in pos],
                       rotation=45, ha="right", fontsize=8)
    ax.set_title(title or f"Load carpet — {n} days, hourly {agg}")
    return ax
