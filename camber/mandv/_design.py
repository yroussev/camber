"""Design rows and the exact OLS projection variance of a fitted M&V model (private).

Every CAMBER M&V baseline except the RC model is linear in its parameters once its change points,
balance point or temperature breakpoints are fixed: ``E = x(T) beta``. This module rebuilds the
design rows ``x`` of a fitted model at new conditions -- change-point, degree-day, driver (Option
B) and TOWT -- from the model's single fit record, and from them the variance of a projected
total.

For a model fitted on ``n`` points with ``p`` parameters, residual variance ``s2``, lag-1 residual
autocorrelation ``rho`` (``kappa = (1+rho)/(1-rho)``) and ``A = (X'X)^-1`` of its design, applied to
``m`` rows whose design rows sum to ``g``::

    V_param = kappa * s2 * g'Ag        (parameter error of the projected total)
    V_noise = kappa * s2 * m           (residual noise of m measured points)

This is the textbook OLS prediction variance of a sum (see BPA/SBW, *Uncertainty Approaches and
Analyses for Regression Models and ECAM*, 2017, §3.3); ``kappa`` inflates both terms for serial
correlation, the conservative choice (CAMBER decision D3 on issue #21). Unlike the ASHRAE G14
fractional-savings kernel it carries the leverage of the application conditions itself -- a
projection far from the fitted mean has a large ``g'Ag`` -- which is why a savings band computed
with it is never widened again for extrapolation. Change points are treated as known.
"""

from __future__ import annotations

import numpy as np

from .coverage import _as_2d, _design, _FitRecord


def _unwrap(model):
    """``(model, bound_index)`` -- a :class:`~camber.mandv.towt.TOWTAtIndex` exposes its model."""
    inner = getattr(model, "model", None)
    if inner is not None and hasattr(model, "index"):
        return inner, model.index
    return model, None


def fit_record(model) -> _FitRecord | None:
    """The model's private fit record (through a TOWT index binding), or ``None``."""
    inner, _ = _unwrap(model)
    rec = getattr(inner, "_fit_record", None)
    return rec if isinstance(rec, _FitRecord) else None


def design_rows(model, drivers, *, index=None) -> np.ndarray:
    """Design rows of a fitted ``model`` at ``drivers`` (``X`` with ``X @ beta`` = prediction).

    Change-point, degree-day and driver models use their recorded design spec. A TOWT model needs
    the timestamps: pass ``index``, or a :class:`~camber.mandv.towt.TOWTAtIndex` binding.
    """
    inner, bound = _unwrap(model)
    rec = fit_record(model)
    if rec is None:
        raise TypeError(f"{type(model).__name__} carries no fit record to rebuild its design from")
    if rec.towt is not None:
        import pandas as pd

        from .towt import _build_design, hour_of_week

        idx = pd.DatetimeIndex(index if index is not None else bound)
        t = np.asarray(drivers, dtype=float).ravel()
        if len(idx) != len(t):
            raise ValueError(f"index has {len(idx)} timestamps but drivers has {len(t)} values")
        return _build_design(hour_of_week(idx), t, inner.bins, inner.breakpoints, inner.occ_bins)
    if rec.design is None:
        raise TypeError(f"{type(model).__name__} has no linear design")
    return _design(rec.design, _as_2d(drivers))


def design_names(model) -> tuple:
    """Column names of :func:`design_rows` for change-point, degree-day and driver models."""
    rec = fit_record(model)
    spec = None if rec is None else rec.design
    if rec is None or spec is None:
        raise TypeError(f"{type(model).__name__} has no named linear design")
    if spec[0] == "cp":
        from .models import _coef_names

        return _coef_names(spec[1], spec[2])
    if spec[0] == "dd":
        cols = ["base"]
        if spec[1] in ("heating", "both"):
            cols.append("heating_slope")
        if spec[1] in ("cooling", "both"):
            cols.append("cooling_slope")
        return tuple(cols)
    if spec[0] == "affine":
        return ("intercept",) + tuple(rec.names)
    raise TypeError(f"unknown design spec {spec!r}")
