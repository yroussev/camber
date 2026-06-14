"""Data-quality dashboard (pattern I): the gate before analytics.

Analytics on bad data is worse than none. This renders a points×metrics heatmap of the
:mod:`camber.ingest.quality` report — coverage, composite score, flatline and outlier fractions
— so the trustworthiness of every input is visible at a glance and can act as a hard gate (a
rule that can't trust its inputs declines to fire; see the sensor-health gate).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..ingest.quality import assess

# Metric -> (higher-is-better?, label). Heatmap is colored so green = good for each.
_METRICS = {
    "coverage": (True, "coverage"),
    "score": (True, "score"),
    "flatline_frac": (False, "flatline"),
    "outlier_frac": (False, "outliers"),
}


def quality_matrix(df: pd.DataFrame, *, metrics=("coverage", "score", "flatline_frac",
                                                 "outlier_frac"), expected_freq=None):
    """Return ``(values, goodness, points, metric_labels)`` from per-column quality reports.

    ``values`` is points×metrics raw; ``goodness`` is the same mapped to 0–1 where 1 is good
    (so a single colormap reads correctly across higher- and lower-is-better metrics).
    """
    points = [c for c in df.columns]
    raw = np.full((len(points), len(metrics)), np.nan)
    good = np.full((len(points), len(metrics)), np.nan)
    for i, c in enumerate(points):
        rep = assess(df[c], expected_freq=expected_freq)
        for j, m in enumerate(metrics):
            v = float(getattr(rep, m))
            raw[i, j] = v
            higher_better = _METRICS.get(m, (True, m))[0]
            good[i, j] = v if higher_better else 1.0 - v
    labels = [_METRICS.get(m, (True, m))[1] for m in metrics]
    return raw, good, points, labels


def quality_dashboard(df: pd.DataFrame, *, ax=None, metrics=("coverage", "score",
                      "flatline_frac", "outlier_frac"), expected_freq=None,
                      title: str | None = None):
    """Heatmap of data-quality metrics (rows=points, cols=metrics). Returns the Axes."""
    import matplotlib.pyplot as plt

    raw, good, points, labels = quality_matrix(df, metrics=metrics, expected_freq=expected_freq)
    if ax is None:
        _, ax = plt.subplots(figsize=(1.6 * len(labels) + 3, 0.4 * max(len(points), 1) + 1.2))
    if raw.size == 0:
        ax.set_title(title or "Data quality — no data")
        return ax

    ax.imshow(good, aspect="auto", cmap="RdYlGn", vmin=0, vmax=1)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_yticks(range(len(points)))
    ax.set_yticklabels([str(p) for p in points], fontsize=8)
    for i in range(len(points)):                       # annotate the raw value in each cell
        for j in range(len(labels)):
            ax.text(j, i, f"{raw[i, j]:.2f}", ha="center", va="center", fontsize=7,
                    color="black")
    ax.set_title(title or "Data-quality dashboard (green = good)")
    return ax
