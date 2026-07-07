"""Pattern D — X-Y-vs-OAT "cloud shape" scatter with shape classification + brush-back.

The energy-signature plot (:mod:`camber.charts.energy_signature`) is the specific
energy-vs-temperature case. This generalizes it to *any* point plotted against outdoor-air
temperature (airflow, valve %, kW, ΔT), because the **shape of the cloud** against OAT reveals
control behavior a time-series buries: a clean line, a hockey-stick at a balance point, a V (both
heating and cooling legs), or a scattered blob (no OAT dependence → erratic control).

Two things beyond drawing it:

- **`classify_shape`** labels the cloud (``linear`` / ``hockey-stick`` / ``v`` / ``scattered``) from
  the fitted change-point model + goodness of fit — a machine-readable summary, no chart required.
- **`brush_back`** maps a selected region of the cloud (an x/y box) back to the **timestamps** that
  produced it — the primitive the interactive-linking layer uses to filter the time views to a
  selection ("when did this cluster happen?").

Reuses :func:`camber.mandv.models.best_model`; matplotlib is lazy-imported. numpy/pandas only.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from ..mandv.models import best_model

_KINDS = ("2P", "3PC", "3PH", "4P", "5P")


def _r2(model, T, y) -> float:
    y = np.asarray(y, dtype=float)
    sst = float(np.sum((y - y.mean()) ** 2))
    return 1.0 - model.sse / sst if sst > 0 else float("nan")


@dataclass
class CloudShape:
    """Classification of an X-vs-OAT cloud."""

    shape: str                   # "linear" | "hockey-stick" | "v" | "scattered" | "insufficient"
    model_kind: str              # the fitted change-point kind ("" if not fit)
    r2: float                    # goodness of fit of that model
    change_points: tuple         # the fitted balance point(s)
    n: int                       # points classified

    def as_dict(self) -> dict:
        return asdict(self)


def classify_shape(series, oat, *, r2_floor: float = 0.3, min_points: int = 5,
                   kinds=_KINDS, _model=None) -> CloudShape:
    """Label an X-vs-OAT cloud from its change-point fit. No chart required.

    A weak fit (``r2 < r2_floor``) means no clear OAT dependence → ``scattered``. Otherwise the
    fitted model kind maps to a shape: single slope → ``linear``; one balance point (flat one side)
    → ``hockey-stick``; two balance points / both legs → ``v``.
    """
    df = pd.DataFrame({"y": series, "T": oat}).dropna()
    if len(df) < min_points:
        return CloudShape("insufficient", "", float("nan"), (), len(df))
    T, y = df["T"].to_numpy(float), df["y"].to_numpy(float)
    model = _model if _model is not None else best_model(T, y, kinds=kinds)
    r2 = _r2(model, T, y)
    if not (r2 == r2) or r2 < r2_floor:
        shape = "scattered"
    elif model.kind == "2P":
        shape = "linear"
    elif model.kind in ("3PC", "3PH"):
        shape = "hockey-stick"
    else:                                       # 4P / 5P — heating and cooling legs
        shape = "v"
    return CloudShape(shape, model.kind, round(r2, 3) if r2 == r2 else float("nan"),
                      tuple(float(c) for c in model.change_points), len(df))


def brush_back(series, oat, *, x_range=None, y_range=None) -> pd.DatetimeIndex:
    """Timestamps whose (OAT, value) point falls in the given box — the brush-back primitive.

    ``x_range`` / ``y_range`` are ``(lo, hi)`` bounds (inclusive); either may be omitted. Returns the
    index of the selected points, so a region brushed in the scatter maps back to *when* it happened.
    """
    df = pd.DataFrame({"y": series, "T": oat}).dropna()
    sel = pd.Series(True, index=df.index)
    if x_range is not None:
        lo, hi = x_range
        sel &= df["T"].between(lo, hi)
    if y_range is not None:
        lo, hi = y_range
        sel &= df["y"].between(lo, hi)
    return pd.DatetimeIndex(df.index[sel.to_numpy()])


def oat_scatter(series, oat, *, ax=None, classify: bool = True, changepoint="auto",
                by=None, cmap: str = "viridis", min_max_avg=None, title: str | None = None,
                xlabel: str = "Outdoor air temperature (°F)", ylabel: str = "Value",
                kinds=_KINDS):
    """Scatter any ``series`` against ``oat``; overlay a change-point fit and classify the cloud.

    Returns ``(ax, CloudShape | None)``. Options:

    - ``changepoint`` — ``"auto"``/``True`` fits the best change-point model and overlays it (with
      balance-point guides); a kind string forces that kind; ``False``/``None`` skips the overlay.
    - ``by`` — an aligned categorical Series (e.g. season, occupied/unoccupied) to colour points by,
      with a legend; otherwise a single colour.
    - ``min_max_avg`` — an optional ``(low, high)`` pair of Series aligned to ``series`` drawing a
      min–max whisker per point (provenance: the average alone hides excursions).
    - ``classify`` — also return a :class:`CloudShape` label for the cloud.
    """
    import matplotlib.pyplot as plt

    df = pd.DataFrame({"y": series, "T": oat}).dropna()
    T, y = df["T"].to_numpy(float), df["y"].to_numpy(float)
    if ax is None:
        _, ax = plt.subplots(figsize=(7, 5))

    if min_max_avg is not None:
        low, high = min_max_avg
        low = pd.Series(low).reindex(df.index)
        high = pd.Series(high).reindex(df.index)
        ax.vlines(T, low.to_numpy(float), high.to_numpy(float), color="#bbbbbb", lw=0.8, zorder=1)

    if by is not None:
        cats = pd.Series(by).reindex(df.index).astype("category")
        codes = cats.cat.codes.to_numpy()
        sc = ax.scatter(T, y, c=codes, cmap=cmap, s=18, alpha=0.7, zorder=2)
        handles = [plt.Line2D([], [], marker="o", ls="", color=sc.cmap(sc.norm(c)),
                              label=str(lbl)) for c, lbl in enumerate(cats.cat.categories)]
        if handles:
            ax.legend(handles=handles, loc="best", fontsize=8, title=getattr(by, "name", None))
    else:
        ax.scatter(T, y, s=18, alpha=0.6, color="#3366cc", zorder=2, label="measured")

    model = None
    if changepoint and len(df) >= 5:
        model = best_model(T, y, kinds=kinds) if changepoint in ("auto", True) else \
            best_model(T, y, kinds=(changepoint,))
        grid = np.linspace(T.min(), T.max(), 200)
        ax.plot(grid, model.predict(grid), color="#cc3333", lw=2.0, zorder=3,
                label=f"{model.kind} fit")
        for cp in model.change_points:
            ax.axvline(float(cp), color="grey", lw=0.8, ls="--", zorder=1)

    shape = classify_shape(df["y"], df["T"], kinds=kinds, _model=model) if classify else None

    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    if title is None and shape is not None:
        title = f"OAT cloud — {shape.shape}" + (f" ({shape.model_kind}, R² {shape.r2:.2f})"
                                                if shape.model_kind else "")
    ax.set_title(title or "OAT scatter")
    if by is None or model is not None:
        ax.legend(loc="best", fontsize=8)
    return ax, shape
