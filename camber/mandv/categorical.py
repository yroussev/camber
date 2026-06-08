"""Multi-variable (categorical) change-point models.

Energy often depends on a categorical context (day type, occupancy mode) as well
as temperature. Lumping all contexts into one temperature regression mixes
behaviors -- a weekday-occupied building and a weekend-setback building have
different change points and slopes. The standard remedy is to combine the
categorical fields into one grouping key and fit a *separate* continuous
(temperature) change-point model per category combination, then route predictions
by category. (The "continuous x categorical" M&V approach from the ASHRAE
Guideline 14 / IPMVP regression literature.)

This module fits one change-point model per category, predicts by routing each
point to its category's model, and reports pooled fit statistics across the whole
dataset (so a single CV(RMSE)/R2 describes the combined model).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .models import ChangePointModel, best_model, fit_model
from .stats import FitStats, fit_stats
from .models import N_PARAMS


@dataclass
class CategoricalModel:
    """A set of per-category change-point models with combined prediction."""

    models: dict                    # category value -> ChangePointModel
    categories: tuple               # ordered category values
    total_params: int               # sum of params across sub-models
    pooled_stats: FitStats | None = None

    def predict(self, T, cat) -> np.ndarray:
        """Predict energy for arrays ``T`` (temperature) and ``cat`` (category)."""
        T = np.asarray(T, dtype=float)
        cat = np.asarray(cat)
        out = np.full(len(T), np.nan)
        for c, m in self.models.items():
            sel = cat == c
            if sel.any():
                out[sel] = m.predict(T[sel])
        return out


def fit_categorical(T, y, cat, *, kind: str | None = None,
                    kinds=("2P", "3PC", "3PH", "4P", "5P"),
                    objective: str = "sse", min_per_cat: int = 6) -> CategoricalModel:
    """Fit one change-point model per category value.

    ``T``, ``y``, ``cat`` are equal-length arrays/Series. For each category with at
    least ``min_per_cat`` points, fit ``kind`` (if given) or ``best_model`` over
    ``kinds``. Categories with too few points are skipped. Returns a
    :class:`CategoricalModel` with pooled fit statistics over all modeled points.
    """
    T = np.asarray(T, dtype=float)
    y = np.asarray(y, dtype=float)
    cat = np.asarray(cat)
    models = {}
    total_p = 0
    for c in pd.unique(cat):
        sel = cat == c
        if sel.sum() < min_per_cat:
            continue
        Tc, yc = T[sel], y[sel]
        if kind is not None:
            m = fit_model(Tc, yc, kind, objective=objective) \
                if kind in ("3PC", "3PH", "4P") else fit_model(Tc, yc, kind)
        else:
            m = best_model(Tc, yc, kinds=kinds)
        models[c] = m
        total_p += N_PARAMS[m.kind]
    if not models:
        raise ValueError("no category had enough points to model")

    # pooled stats over all modeled points
    modeled = np.isin(cat, list(models.keys()))
    yhat = CategoricalModel(models, tuple(models.keys()), total_p).predict(
        T[modeled], cat[modeled])
    pooled = fit_stats(y[modeled], yhat, p=total_p)
    return CategoricalModel(models=models, categories=tuple(models.keys()),
                            total_params=total_p, pooled_stats=pooled)
