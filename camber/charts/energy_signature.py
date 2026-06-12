"""Energy-signature plot: energy vs outdoor temperature with the fitted change-point model.

The energy signature — consumption against outdoor air temperature — is the canonical M&V
view. Plotting the measured points with the fitted change-point model overlaid (2P/3P/4P/5P
from :mod:`camber.mandv.models`) shows the balance point(s), the heating and cooling slopes,
and how tightly the model fits — the visual behind a savings baseline.
"""

from __future__ import annotations

import numpy as np

from ..mandv.models import best_model


def _r2(model, T, y):
    y = np.asarray(y, dtype=float)
    sst = float(np.sum((y - y.mean()) ** 2))
    return 1.0 - model.sse / sst if sst > 0 else float("nan")


def energy_signature(T, y, model=None, *, ax=None, title: str | None = None,
                     xlabel: str = "Outdoor air temperature (°F)", ylabel: str = "Energy",
                     kinds=("2P", "3PC", "3PH", "4P", "5P")):
    """Scatter energy vs temperature and overlay the change-point fit. Returns ``(ax, model)``.

    If ``model`` is None, the best model (by :func:`best_model`) is fit and overlaid. Change
    points are drawn as vertical guides.
    """
    import matplotlib.pyplot as plt

    T = np.asarray(T, dtype=float)
    y = np.asarray(y, dtype=float)
    if model is None:
        model = best_model(T, y, kinds=kinds)
    if ax is None:
        _, ax = plt.subplots(figsize=(7, 5))

    ax.scatter(T, y, s=18, alpha=0.6, color="#3366cc", label="measured")
    grid = np.linspace(T.min(), T.max(), 200)
    ax.plot(grid, model.predict(grid), color="#cc3333", lw=2.0,
            label=f"{model.kind} fit")
    for cp in model.change_points:
        ax.axvline(float(cp), color="grey", lw=0.8, ls="--")

    r2 = _r2(model, T, y)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title or f"Energy signature — {model.kind} model, R² {r2:.3f}")
    ax.legend(loc="best", fontsize=8)
    return ax, model
