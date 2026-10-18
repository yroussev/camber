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

# A step longer than this many median sampling steps is a data gap: a shaded run splits there.
_GAP_FACTOR = 2.5


def mask_to_spans(mask: pd.Series, *, gap_factor: float = _GAP_FACTOR):
    """``(start, end)`` timestamp intervals covering the samples where a boolean Series is True.

    0.108 (#262): each flagged sample is shaded over its own interval, from its timestamp to the
    next sample's, so a run of ``n`` flagged hourly samples spans ``n`` hours (the last sample's
    interval is one median step). A run splits at a data gap -- a step longer than
    ``gap_factor`` times the median step -- and a sample just before a gap is shaded over one
    median step, so missing data is never shaded as flagged. A lone sample in a one-sample mask
    has no step to measure and comes back as ``(t, t)``.
    """
    m = pd.Series(mask).fillna(False).astype(bool)
    if not m.any():
        return []
    m.index = pd.DatetimeIndex(m.index)
    m = m.sort_index()
    m = m[~m.index.duplicated(keep="last")]
    idx = pd.DatetimeIndex(m.index)
    vals = m.to_numpy()
    steps = idx[1:] - idx[:-1]
    positive = steps[steps > pd.Timedelta(0)]
    med = positive.median() if len(positive) else pd.Timedelta(0)
    limit = med * gap_factor
    n = len(idx)

    def own_end(i):
        """The end of sample ``i``'s own interval (the next sample, or one step at a gap)."""
        if i + 1 < n and steps[i] <= limit:
            return idx[i + 1]
        return idx[i] + med

    spans, start = [], None
    for i in range(n):
        if not vals[i]:
            continue
        if start is None:
            start = idx[i]
        joined = i + 1 < n and vals[i + 1] and steps[i] <= limit
        if not joined:
            spans.append((start, own_end(i)))
            start = None
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
    ``normalize`` min-max scales each series to 0–1 so disparate units overlay comparably;
    ``normalize="unit"`` (0.108, #258) scales the series that share a unit together, so a space
    temperature and its setpoint, or an airflow and its setpoint, keep their relation.
    """
    import matplotlib.pyplot as plt

    from ._labels import mpl_text, role_label, role_unit

    # default: every numeric column (0.108, #265: a text column such as a mode has no trend line)
    cols = (
        list(columns)
        if columns is not None
        else [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    )
    if ax is None:
        _, ax = plt.subplots(figsize=(13, 4))
    ranges: dict = {}
    if normalize == "unit":
        # one 0-1 range per unit; a column with no stated unit is scaled on its own
        groups: dict = {}
        for c in cols:
            groups.setdefault(role_unit(c) or ("", str(c)), []).append(c)
        for members in groups.values():
            vals = np.concatenate([df[c].astype(float).to_numpy() for c in members])
            finite = vals[np.isfinite(vals)]
            rng = (float(finite.min()), float(finite.max())) if finite.size else (0.0, 0.0)
            ranges.update({c: rng for c in members})
    for c in cols:
        s = df[c].astype(float)
        if normalize:
            if c in ranges:
                lo, hi = ranges[c]
            else:
                finite = s.to_numpy()[np.isfinite(s.to_numpy())]
                lo, hi = (float(finite.min()), float(finite.max())) if finite.size else (0.0, 0.0)
            s = (s - lo) / (hi - lo) if hi > lo else s * 0.0
        ax.plot(df.index, s.to_numpy(), lw=0.9, label=mpl_text(role_label(c)))

    shaded_labels = set()
    idx = df.index.sort_values() if isinstance(df.index, pd.DatetimeIndex) else None
    for label, mask in (spans or {}).items():
        for start, end in mask_to_spans(mask):
            if start == end and idx is not None:
                # 0.102 (#119): a one-sample span (a boiler start, a brief excursion) is drawn
                # over its own interval, to the next sample, rather than as an invisible hairline;
                # since 0.108 (#262) mask_to_spans does this itself, except for a one-sample mask
                pos = idx.searchsorted(end, side="right")
                if pos < len(idx):
                    end = idx[pos]
            ax.axvspan(
                start,
                end,
                color=shade_color,
                alpha=shade_alpha,
                label=mpl_text(label) if label not in shaded_labels else None,
            )
            shaded_labels.add(label)

    ax.set_xlabel("Time")
    # 0.102 (#115): series sharing one unit read it on the axis (each legend entry names its own)
    units = {role_unit(c) for c in cols}
    shared = units.pop() if len(units) == 1 else ""
    ax.set_ylabel("normalized (0–1)" if normalize else mpl_text(shared or "value"))
    n_viol = len(shaded_labels)
    ax.set_title(
        mpl_text(
            title
            or (
                f"Multi-trend — {len(cols)} points"
                + (f", {n_viol} fault overlay(s)" if n_viol else "")
            )
        )
    )
    ax.legend(loc="upper left", fontsize=8, ncol=2)
    return ax
