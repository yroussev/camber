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

**Billing rows** (provisional, 0.92). When each row is a bill of ``d_j`` days whose value is
energy per day, the projected total is ``sum_j d_j x_j beta``, so ``g = sum_j d_j x_j``; a bill's
per-day value has variance ``s2 * c / d_j`` for a model fitted with day weights (``c`` the fit's
mean bill length, ``weight_scale`` in its record) and ``s2 / d_j`` for one fitted on daily rows
(``c = 1``), so the noise of the measured total is ``V_noise = kappa * s2 * c * sum_j d_j``. A row
without days counts as one day of a weighted model's fit.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .coverage import _as_2d, _design, _FitRecord


@dataclass(frozen=True)
class ProjectionVariance:
    """The exact variance terms of one model's projected total over a set of rows."""

    v_param: float  # kappa * s2 * g'Ag
    v_noise: float  # kappa * s2 * m
    g: np.ndarray  # sum of the design rows
    df: int | None  # residual degrees of freedom of the fit (n - p)
    m: int  # rows projected onto
    s2: float
    kappa: float
    rho: float | None  # the rho used (None = unknown; kappa is then 1 and the band unadjusted)
    total: float  # the projected total, g @ beta where known, else sum of predictions


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
    if spec[0] == "bdd":  # 0.94 (#72): camber.mandv.basetemp.BillingDegreeDayModel
        legs = {"DD-H": ("heating_slope",), "DD-C": ("cooling_slope",)}
        return ("base",) + legs.get(spec[1], ("heating_slope", "cooling_slope"))
    if spec[0] == "cpd":
        from .models import _coef_names

        return _coef_names(spec[1], spec[2]) + tuple(rec.names[1:])
    raise TypeError(f"unknown design spec {spec!r}")


def projection_variance(
    model, drivers, *, rows=None, rho=None, index=None, days=None
) -> ProjectionVariance:
    """Exact ``V_param`` / ``V_noise`` of ``model``'s projected total at ``drivers``.

    ``rows`` (a boolean mask aligned to ``drivers``) selects the rows actually summed -- the
    savings functions pass the rows that entered their sums. ``rho`` overrides the fit record's
    residual autocorrelation; with neither, ``kappa`` is 1 and the result is unadjusted
    (``rho=None``). Raises ``TypeError`` when the model carries no ``(X'X)^-1`` and ``s2`` -- a
    model not fitted by CAMBER, or one built by hand.

    ``days`` (aligned to ``drivers``; provisional) are billing rows' day counts: ``g`` is then the
    day-weighted sum and ``V_noise`` the noise of ``sum(days)`` days (module docstring). ``m``
    stays the number of rows.
    """
    rec = fit_record(model)
    if rec is None or rec.xtx_pinv is None or rec.s2 is None:
        raise TypeError(
            f"kernel='exact' needs a model fitted by CAMBER (it records (X'X)^-1 and s2); "
            f"{type(model).__name__} carries neither"
        )
    X = design_rows(model, drivers, index=index)
    d: np.ndarray | None = None if days is None else np.asarray(days, dtype=float).ravel()
    if d is not None and len(d) != len(X):
        raise ValueError(f"days has {len(d)} values for {len(X)} rows")
    if rows is not None:
        X = X[np.asarray(rows, dtype=bool)]
        d = None if d is None else d[np.asarray(rows, dtype=bool)]
    fin = np.all(np.isfinite(X), axis=1)
    if d is not None:
        fin &= np.isfinite(d)
        d = d[fin]
    X = X[fin]
    m = int(len(X))
    g = X.sum(axis=0) if d is None else (X * d[:, None]).sum(axis=0)
    r = rho if rho is not None else rec.rho
    if r is None or not np.isfinite(r) or r <= 0:
        kappa = 1.0
    elif r >= 1:
        kappa = float("inf")
    else:
        kappa = (1.0 + r) / (1.0 - r)
    s2 = float(rec.s2)
    v_param = float(kappa * s2 * (g @ rec.xtx_pinv @ g))
    if d is None and rec.weight_scale is None:
        v_noise = float(kappa * s2 * m)
    else:
        n_days = float(m) if d is None else float(d.sum())
        v_noise = float(kappa * s2 * float(rec.weight_scale or 1.0) * n_days)
    df = None if rec.n is None or rec.p is None else int(rec.n) - int(rec.p)
    beta = _beta(model)
    total = float(g @ beta) if beta is not None and len(beta) == len(g) else float("nan")
    return ProjectionVariance(
        v_param=v_param,
        v_noise=v_noise,
        g=g,
        df=df,
        m=m,
        s2=s2,
        kappa=kappa,
        rho=None if r is None else float(r),
        total=total,
    )


def _beta(model):
    """The coefficient vector matching :func:`design_rows`, or ``None``."""
    inner, _ = _unwrap(model)
    beta = getattr(inner, "beta", None)
    if beta is not None:  # TOWT
        return np.asarray(beta, dtype=float)
    try:
        names = design_names(inner)
    except TypeError:
        return None
    rec = fit_record(inner)
    kind = rec.design[0] if rec is not None and rec.design else None
    if kind == "cp":
        return np.array([float(inner.coeffs[nm]) for nm in names])
    if kind == "dd":
        vals = {"base": inner.base, "heating_slope": inner.heating_slope}
        vals["cooling_slope"] = inner.cooling_slope
        return np.array([float(vals[nm]) for nm in names])
    if kind in ("affine", "bdd"):
        return np.array([float(inner.intercept), *map(float, inner.coef)])
    if kind == "cpd":
        return inner._beta()
    return None
