"""Fault-annotated synchronized multi-trend (pattern B): the chart *is* the evidence.

Several points on one time axis, with the spans where rules tripped shaded in place — so a chart
you opened to browse tells you what's wrong, and a finding carries the trend that proves it.
Violation spans are supplied as ``{label: boolean Series}`` (a rule's violating mask), keeping
this a general primitive: any rule that can mark its violating timestamps renders its evidence
here.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def mask_to_spans(mask: pd.Series):
    """Contiguous ``(start, end)`` timestamp intervals where a boolean Series is True."""
    m = pd.Series(mask).fillna(False).astype(bool)
    if not m.any():
        return []
    idx = pd.DatetimeIndex(m.index)
    vals = m.to_numpy()
    spans, start = [], None
    for i, v in enumerate(vals):
        if v and start is None:
            start = idx[i]
        elif not v and start is not None:
            spans.append((start, idx[i - 1]))
            start = None
    if start is not None:
        spans.append((start, idx[-1]))
    return spans


def fault_multitrend(
    df: pd.DataFrame,
    columns=None,
    *,
    spans=None,
    ax=None,
    normalize=False,
    title: str | None = None,
    shade_color: str = "#d62728",
    shade_alpha: float = 0.15,
):
    """Plot ``columns`` on one time axis and shade violation ``spans``. Returns the Axes.

    ``spans`` is ``{label: boolean Series}`` — each True run is shaded once and labeled.
    ``normalize`` min-max scales each series to 0–1 so disparate units overlay comparably.
    """
    import matplotlib.pyplot as plt

    cols = list(columns) if columns is not None else [c for c in df.columns]
    if ax is None:
        _, ax = plt.subplots(figsize=(13, 4))
    for c in cols:
        s = df[c].astype(float)
        if normalize:
            lo, hi = float(np.nanmin(s)), float(np.nanmax(s))
            s = (s - lo) / (hi - lo) if hi > lo else s * 0.0
        ax.plot(df.index, s.to_numpy(), lw=0.9, label=str(c))

    shaded_labels = set()
    for label, mask in (spans or {}).items():
        for start, end in mask_to_spans(mask):
            ax.axvspan(
                start,
                end,
                color=shade_color,
                alpha=shade_alpha,
                label=label if label not in shaded_labels else None,
            )
            shaded_labels.add(label)

    ax.set_xlabel("Time")
    ax.set_ylabel("normalized (0–1)" if normalize else "value")
    n_viol = len(shaded_labels)
    ax.set_title(
        title
        or (
            f"Multi-trend — {len(cols)} points" + (f", {n_viol} fault overlay(s)" if n_viol else "")
        )
    )
    ax.legend(loc="upper left", fontsize=8, ncol=2)
    return ax
