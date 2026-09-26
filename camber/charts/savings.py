"""Pattern H — M&V baseline, savings & continuous tracking.

A savings number without uncertainty isn't defensible, and savings erode silently. This renders the
IPMVP Option-C picture: project the baseline model onto the reporting period, plot **cumulative
baseline-projected vs cumulative actual** energy, shade the avoided energy between them, and carry
the **ASHRAE G14 Annex-B fractional savings uncertainty** as a ± band on the running total — so the
chart shows both the savings and how confident we are in it. Fit quality (CV(RMSE), NMBE) annotates
the baseline's credibility.

Reuses `mandv.stats.avoided_energy_savings` (numbers) and any `predict()`-able baseline model
(`mandv.models.best_model`). matplotlib lazy-imported; numpy/pandas.

The chart follows the baseline's coverage of the reporting period (`mandv.coverage`): reporting
points outside the baseline support are rug-marked along the x-axis; a **moderate** extrapolation
adds a title suffix; a **declined** (severe) one draws only the actual line and the projection,
dashed and labelled "extrapolated — not a saving", with no avoided-energy shading and no band.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..mandv.coverage import ExtrapolationPolicy, assess_coverage
from ..mandv.stats import avoided_energy_savings


def cumulative_savings(baseline_model, t_report, y_report):
    """Cumulative baseline-projected, actual, and avoided energy over the reporting period.

    Returns ``(index, cum_baseline, cum_actual, cum_avoided)`` as aligned arrays; ``index`` is the
    Series index of ``y_report`` if it has one, else a RangeIndex.
    """
    idx = y_report.index if isinstance(y_report, pd.Series) else pd.RangeIndex(len(y_report))
    proj = np.asarray(baseline_model.predict(np.asarray(t_report, dtype=float)), dtype=float)
    act = np.asarray(y_report, dtype=float)
    m = np.isfinite(proj) & np.isfinite(act)
    proj, act, idx = proj[m], act[m], idx[m.nonzero()[0]] if hasattr(idx, "__getitem__") else idx
    cum_base = np.cumsum(proj)
    cum_act = np.cumsum(act)
    return idx, cum_base, cum_act, cum_base - cum_act


def savings_chart(
    baseline_model,
    t_report,
    y_report,
    *,
    n_baseline: int,
    p_baseline: int,
    cv_rmse: float,
    confidence: float = 0.90,
    rho: float | None = None,
    ax=None,
    title: str | None = None,
    ylabel: str = "Energy",
    extrapolation: ExtrapolationPolicy | None = None,
):
    """Plot cumulative M&V savings with a G14 uncertainty band. Returns ``(ax, SavingsResult)``.

    ``baseline_model`` is any ``predict(T)``-able baseline (e.g. `mandv.models.best_model`);
    ``t_report`` / ``y_report`` are the reporting-period driver + actual energy (``y_report`` may be
    a Series to get a time axis). ``cv_rmse`` / ``n_baseline`` / ``p_baseline`` come from the
    baseline fit and drive the fractional savings uncertainty (`avoided_energy_savings`).
    ``extrapolation`` is the coverage policy passed through to it.
    """
    import matplotlib.pyplot as plt

    idx, cum_base, cum_act, cum_avoided = cumulative_savings(baseline_model, t_report, y_report)
    res = avoided_energy_savings(
        baseline_model,
        t_report,
        y_report,
        cv_rmse=cv_rmse,
        n_baseline=n_baseline,
        p_baseline=p_baseline,
        confidence=confidence,
        rho=rho,
        extrapolation=extrapolation,
    )
    if ax is None:
        _, ax = plt.subplots(figsize=(9, 5))

    x = list(idx)
    _rug_outside(ax, baseline_model, t_report, y_report, x, extrapolation)
    if res.declined:
        ax.plot(x, cum_base, color="#999999", lw=1.6, ls="--", label="extrapolated — not a saving")
        ax.plot(x, cum_act, color="#111111", lw=1.8, label="actual")
        ax.set_ylabel(f"Cumulative {ylabel.lower()}")
        ax.set_title(title or "M&V savings declined — severe extrapolation of the baseline")
        ax.legend(loc="best", fontsize=8)
        return ax, res

    ax.plot(x, cum_base, color="#3366cc", lw=1.8, ls="--", label="baseline (projected)")
    ax.plot(x, cum_act, color="#111111", lw=1.8, label="actual")
    # avoided energy = area between baseline and actual (green = saved, red = excess)
    ax.fill_between(
        x,
        cum_act,
        cum_base,
        where=(cum_base >= cum_act),
        interpolate=True,
        color="#8fd19e",
        alpha=0.5,
        label="avoided",
    )
    ax.fill_between(
        x,
        cum_act,
        cum_base,
        where=(cum_base < cum_act),
        interpolate=True,
        color="#f2a6a6",
        alpha=0.5,
        label="excess",
    )
    # G14 uncertainty as a ± band on the running total (scaled to the cumulative avoided fraction)
    abs_unc = res.abs_uncertainty if res.abs_uncertainty is not None else float("nan")
    if len(cum_avoided) and np.isfinite(abs_unc) and cum_avoided[-1] != 0:
        frac = cum_avoided / cum_avoided[-1]
        band = np.abs(frac) * abs_unc
        ax.fill_between(
            x,
            cum_avoided - band + cum_act,
            cum_avoided + band + cum_act,
            color="#cccccc",
            alpha=0.35,
            label=f"±{int(confidence * 100)}% band",
        )

    avoided = res.avoided_energy if res.avoided_energy is not None else float("nan")
    spct = res.savings_pct if res.savings_pct is not None else float("nan")
    tot = f"{avoided:,.0f}"
    unc = f" ± {abs_unc:,.0f}" if np.isfinite(abs_unc) else ""
    pct = f"{spct:.1%}" if np.isfinite(spct) else "n/a"
    fit = f", CV(RMSE) {cv_rmse:.1%}" if np.isfinite(cv_rmse) else ""
    cov = res.coverage or {}
    extra = ""
    if cov.get("tier") in ("moderate", "severe") and cov.get("share_points_outside") is not None:
        share = cov["share_points_outside"]
        extra = f"\nextrapolated: {share:.0%} of points outside the baseline range"
    ax.set_ylabel(f"Cumulative {ylabel.lower()}")
    ax.set_title(title or f"M&V savings — {tot}{unc} ({pct} of baseline{fit}){extra}")
    ax.legend(loc="best", fontsize=8)
    return ax, res


def _rug_outside(ax, baseline_model, t_report, y_report, x, extrapolation) -> int:
    """Rug-mark (along the x-axis) the plotted reporting points outside the baseline support."""
    cov = assess_coverage(baseline_model, t_report, policy=extrapolation)
    mask = cov.outside_mask()
    if mask is None or not mask.any():
        return 0
    proj = np.asarray(baseline_model.predict(np.asarray(t_report, dtype=float)), dtype=float)
    used = np.isfinite(proj) & np.isfinite(np.asarray(y_report, dtype=float))
    pos = np.flatnonzero(mask[used])
    if not len(pos):
        return 0
    xs = [x[i] for i in pos]
    ax.plot(
        xs,
        [0.0] * len(xs),
        "|",
        color="#cc3333",
        ms=10,
        transform=ax.get_xaxis_transform(),
        label="outside baseline range",
    )
    return len(xs)
