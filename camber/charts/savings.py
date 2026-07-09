"""Pattern H — M&V baseline, savings & continuous tracking.

A savings number without uncertainty isn't defensible, and savings erode silently. This renders the
IPMVP Option-C picture: project the baseline model onto the reporting period, plot **cumulative
baseline-projected vs cumulative actual** energy, shade the avoided energy between them, and carry
the **ASHRAE G14 Annex-B fractional savings uncertainty** as a ± band on the running total — so the
chart shows both the savings and how confident we are in it. Fit quality (CV(RMSE), NMBE) annotates
the baseline's credibility.

Reuses `mandv.stats.avoided_energy_savings` (numbers) and any `predict()`-able baseline model
(`mandv.models.best_model`). matplotlib lazy-imported; numpy/pandas.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

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


def savings_chart(baseline_model, t_report, y_report, *, n_baseline: int, p_baseline: int,
                  cv_rmse: float, confidence: float = 0.90, rho: float = 0.0, ax=None,
                  title: str | None = None, ylabel: str = "Energy"):
    """Plot cumulative M&V savings with a G14 uncertainty band. Returns ``(ax, SavingsResult)``.

    ``baseline_model`` is any ``predict(T)``-able baseline (e.g. `mandv.models.best_model`);
    ``t_report`` / ``y_report`` are the reporting-period driver + actual energy (``y_report`` may be
    a Series to get a time axis). ``cv_rmse`` / ``n_baseline`` / ``p_baseline`` come from the
    baseline fit and drive the fractional savings uncertainty (`avoided_energy_savings`).
    """
    import matplotlib.pyplot as plt

    idx, cum_base, cum_act, cum_avoided = cumulative_savings(baseline_model, t_report, y_report)
    res = avoided_energy_savings(baseline_model, t_report, y_report, cv_rmse=cv_rmse,
                                 n_baseline=n_baseline, p_baseline=p_baseline,
                                 confidence=confidence, rho=rho)
    if ax is None:
        _, ax = plt.subplots(figsize=(9, 5))

    x = list(idx)
    ax.plot(x, cum_base, color="#3366cc", lw=1.8, ls="--", label="baseline (projected)")
    ax.plot(x, cum_act, color="#111111", lw=1.8, label="actual")
    # avoided energy = area between baseline and actual (green = saved, red = excess)
    ax.fill_between(x, cum_act, cum_base, where=(cum_base >= cum_act), interpolate=True,
                    color="#8fd19e", alpha=0.5, label="avoided")
    ax.fill_between(x, cum_act, cum_base, where=(cum_base < cum_act), interpolate=True,
                    color="#f2a6a6", alpha=0.5, label="excess")
    # G14 uncertainty as a ± band on the running total (scaled to the cumulative avoided fraction)
    if len(cum_avoided) and np.isfinite(res.abs_uncertainty) and cum_avoided[-1] != 0:
        frac = cum_avoided / cum_avoided[-1]
        band = np.abs(frac) * res.abs_uncertainty
        ax.fill_between(x, cum_avoided - band + cum_act, cum_avoided + band + cum_act,
                        color="#cccccc", alpha=0.35, label=f"±{int(confidence * 100)}% band")

    tot = f"{res.avoided_energy:,.0f}"
    unc = f" ± {res.abs_uncertainty:,.0f}" if np.isfinite(res.abs_uncertainty) else ""
    pct = f"{res.savings_pct:.1%}" if np.isfinite(res.savings_pct) else "n/a"
    fit = f", CV(RMSE) {cv_rmse:.1%}" if np.isfinite(cv_rmse) else ""
    ax.set_ylabel(f"Cumulative {ylabel.lower()}")
    ax.set_title(title or f"M&V savings — {tot}{unc} ({pct} of baseline{fit})")
    ax.legend(loc="best", fontsize=8)
    return ax, res
