"""M&V adjustment waterfall: from the baseline to reporting energy, one ledger entry per bar.

Renders an :class:`~camber.mandv.adjustments.AdjustedResult` as a waterfall -- the (optional)
measured baseline period and routine adjustment, the baseline projection, each non-routine and
static-factor adjustment in the order it was applied, the adjusted baseline, the saving, and the
reporting-period energy -- so a reader sees exactly which entries moved the baseline and by how
much. Running totals are solid bars from zero; adjustments float from the running total (blue up,
orange down); the saving is green (or red for an increase) and carries the combined uncertainty
band as an error bar. Material entries (``|effect| >= max(threshold, 2 SE)``) are marked with an
asterisk. matplotlib is lazy-imported.
"""

from __future__ import annotations

import numpy as np

__all__ = ["adjustment_waterfall"]

_TOTAL = "#6b7280"
_UP = "#3366cc"
_DOWN = "#e07b39"
_SAVE = "#2e9e5b"
_EXCESS = "#cc3333"


def adjustment_waterfall(adjusted, *, ax=None, title: str | None = None, ylabel: str = "Energy"):
    """Plot an :class:`~camber.mandv.adjustments.AdjustedResult` as a waterfall; returns ``ax``."""
    import matplotlib.pyplot as plt

    steps = adjusted.waterfall
    if ax is None:
        _, ax = plt.subplots(figsize=(max(6.0, 1.1 * len(steps) + 2.0), 5))
    material = {e["position"]: e.get("material") for e in adjusted.ledger}
    running = 0.0
    labels = []
    for i, st in enumerate(steps):
        label = st.label
        if st.entry is not None and material.get(st.entry) and not label.startswith("baseline"):
            label += " *"
        labels.append(label)
        if st.kind == "total":
            running = float(st.value)
            ax.bar(i, running, color=_TOTAL, width=0.6)
            continue
        v = float(st.value)
        if label == "savings":
            color = _SAVE if v <= 0 else _EXCESS
        else:
            color = _UP if v >= 0 else _DOWN
        ax.bar(i, v, bottom=running, color=color, width=0.6)
        if label == "savings" and adjusted.abs_uncertainty is not None:
            ax.errorbar(
                i,
                running + v / 2.0,
                yerr=adjusted.abs_uncertainty,
                color="#111111",
                capsize=4,
                lw=1.2,
            )
        running += v
    ax.set_xticks(range(len(steps)))
    ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
    ax.axhline(0.0, color="#999999", lw=0.8)
    ax.set_ylabel(ylabel)
    pct = adjusted.savings_pct
    band = adjusted.abs_uncertainty
    head = f"adjusted savings {adjusted.savings:,.0f}"
    if band is not None and np.isfinite(band):
        head += f" ± {band:,.0f} at {adjusted.confidence:.0%}"
    if pct is not None:
        head += f" ({pct:.1%} of adjusted baseline)"
    ax.set_title(title or f"M&V adjustment waterfall — {head}", fontsize=10)
    if any(material.values()):
        ax.text(
            0.99,
            0.98,
            "* material: |effect| ≥ max(threshold, 2·SE)",
            transform=ax.transAxes,
            ha="right",
            va="top",
            fontsize=7,
        )
    return ax
