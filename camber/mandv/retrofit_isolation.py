"""IPMVP Option B — retrofit isolation by sub-metering (all-parameter measurement).

Option C (whole-facility, :mod:`camber.mandv.stats` / :mod:`~camber.mandv.normalized`) infers
savings from the utility meter and a weather model. **Option B** narrows the measurement
boundary to the *isolated system* a measure touches — a chiller, pump, fan, lighting circuit —
via a sub-meter. With fewer confounders inside the boundary, savings are cleaner, but the
energy must be regressed on *that system's own driver*: runtime hours, load fraction, cooling
tons, production, or (still) outdoor temperature — not necessarily weather.

CAMBER's existing models are temperature-only change-point fits. The missing piece for Option B
is a **generic driver regression** (:func:`fit_driver_model`); given that, the same ASHRAE G14
Annex-B savings/uncertainty machinery (:func:`camber.mandv.stats.avoided_energy_savings`,
:func:`camber.mandv.normalized.normalized_savings`) applies unchanged at the sub-meter boundary,
because both are written against any object exposing ``predict()``.

- :func:`isolation_savings` — reporting-period **avoided energy** at the sub-meter (baseline
  model projected onto the reporting drivers, minus measured reporting energy) + FSU.
- :func:`isolation_normalized_savings` — savings **normalized to a fixed reference** driver set
  (project both periods' models onto a common normal year / normal load profile).
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np

from .stats import avoided_energy_savings, fit_stats
from .normalized import normalized_savings


@dataclass
class DriverModel:
    """An affine least-squares model ``energy = intercept + coef·driver`` (any driver).

    ``coef`` has one entry per driver column; an empty ``coef`` is a constant (mean-only)
    model for the no-independent-variable case. ``predict`` accepts a 1-D driver, a 2-D
    multi-driver array, or — for the constant model — anything length-``n``.
    """

    intercept: float
    coef: tuple
    sse: float
    n: int
    p: int                          # parameters (len(coef) + 1)

    def predict(self, X):
        coef = np.asarray(self.coef, dtype=float)
        a = np.asarray(X, dtype=float)
        if coef.size == 0:                                  # constant model
            n = a.shape[0] if a.ndim >= 1 else int(a)
            return np.full(n, self.intercept)
        A = a[:, None] if a.ndim == 1 else a
        return self.intercept + A @ coef

    def as_dict(self):
        return {"intercept": round(self.intercept, 6), "coef": [round(c, 6) for c in self.coef],
                "sse": round(self.sse, 4), "n": self.n, "p": self.p}


def fit_driver_model(driver, energy) -> DriverModel:
    """Least-squares fit of sub-meter ``energy`` on its ``driver`` (1-D, 2-D, or None).

    ``driver=None`` fits a constant (mean) model. NaN rows are dropped pairwise.
    """
    y = np.asarray(energy, dtype=float)
    if driver is None:
        m = np.isfinite(y)
        y = y[m]
        intercept = float(np.mean(y)) if len(y) else float("nan")
        yhat = np.full(len(y), intercept)
        sse = float(np.sum((y - yhat) ** 2))
        return DriverModel(intercept=intercept, coef=(), sse=sse, n=len(y), p=1)

    X = np.asarray(driver, dtype=float)
    if X.ndim == 1:
        X = X[:, None]
    mask = np.isfinite(y) & np.all(np.isfinite(X), axis=1)
    X, y = X[mask], y[mask]
    A = np.hstack([np.ones((len(X), 1)), X])
    beta, *_ = np.linalg.lstsq(A, y, rcond=None)
    yhat = A @ beta
    sse = float(np.sum((y - yhat) ** 2))
    return DriverModel(intercept=float(beta[0]), coef=tuple(float(c) for c in beta[1:]),
                       sse=sse, n=len(y), p=A.shape[1])


def _baseline_predict_input(model, driver, n):
    """The argument to pass the model's predict for the baseline period."""
    return np.zeros(n) if (driver is None or len(model.coef) == 0) else np.asarray(driver, float)


@dataclass
class IsolationSavings:
    """IPMVP Option-B savings at a sub-meter boundary (avoided energy + G14 uncertainty)."""

    option: str
    boundary: str                   # the isolated system / sub-meter description
    cv_rmse: float                  # baseline fit CV(RMSE)
    accept: bool                    # baseline model meets the G14 acceptance gate
    adjusted_baseline: float        # baseline model projected onto reporting drivers
    reporting_actual: float
    savings: float                  # adjusted_baseline - reporting_actual
    savings_pct: float
    fractional_uncertainty: float
    abs_uncertainty: float
    confidence: float
    n_baseline: int
    n_reporting: int
    model: DriverModel | None = None

    def as_dict(self):
        d = asdict(self)
        d["model"] = self.model.as_dict() if self.model else None
        return d


def isolation_savings(baseline_energy, reporting_energy, *,
                      baseline_driver=None, reporting_driver=None,
                      boundary: str = "", confidence: float = 0.90,
                      model: DriverModel | None = None,
                      cv_rmse_max: float = 0.20) -> IsolationSavings:
    """Option-B avoided energy for an isolated, sub-metered system.

    Fit (or accept) a baseline model of the sub-meter ``baseline_energy`` on its
    ``baseline_driver``, project it onto ``reporting_driver`` to form the adjusted baseline,
    and subtract measured ``reporting_energy``. Drivers may be None (constant model) when the
    operating conditions don't change. Returns the savings with the ASHRAE G14 Annex-B
    fractional uncertainty and the baseline model-acceptance verdict.
    """
    yb = np.asarray(baseline_energy, dtype=float)
    yr = np.asarray(reporting_energy, dtype=float)
    if model is None:
        model = fit_driver_model(baseline_driver, yb)

    yhat_b = model.predict(_baseline_predict_input(model, baseline_driver, len(yb)))
    fs = fit_stats(yb, yhat_b, model.p, cv_rmse_max=cv_rmse_max)

    rep_input = (np.zeros(len(yr)) if (reporting_driver is None or len(model.coef) == 0)
                 else np.asarray(reporting_driver, float))
    sav = avoided_energy_savings(model, rep_input, yr, cv_rmse=fs.cv_rmse,
                                 n_baseline=model.n, p_baseline=model.p, confidence=confidence)
    return IsolationSavings(
        option="B", boundary=boundary, cv_rmse=fs.cv_rmse, accept=fs.accept,
        adjusted_baseline=sav.baseline_projected, reporting_actual=sav.reporting_actual,
        savings=sav.avoided_energy, savings_pct=sav.savings_pct,
        fractional_uncertainty=sav.fractional_uncertainty, abs_uncertainty=sav.abs_uncertainty,
        confidence=confidence, n_baseline=model.n, n_reporting=int(len(yr)), model=model)


def isolation_normalized_savings(baseline_energy, reporting_energy, normal_driver, *,
                                 baseline_driver, reporting_driver,
                                 confidence: float = 0.90, cv_rmse_max: float = 0.20):
    """Option-B savings normalized to a fixed reference driver set (e.g. a normal year/load).

    Fits a driver model to each period and projects both onto ``normal_driver`` (the common
    reference), differencing the normalized consumption — so a change in operating conditions
    between periods doesn't masquerade as savings. Returns a
    :class:`~camber.mandv.normalized.NormalizedSavings`.
    """
    mb = fit_driver_model(baseline_driver, baseline_energy)
    mr = fit_driver_model(reporting_driver, reporting_energy)
    yb = np.asarray(baseline_energy, dtype=float)
    yr = np.asarray(reporting_energy, dtype=float)
    cvb = fit_stats(yb, mb.predict(_baseline_predict_input(mb, baseline_driver, len(yb))),
                    mb.p, cv_rmse_max=cv_rmse_max).cv_rmse
    cvr = fit_stats(yr, mr.predict(_baseline_predict_input(mr, reporting_driver, len(yr))),
                    mr.p, cv_rmse_max=cv_rmse_max).cv_rmse
    return normalized_savings(mb, mr, normal_driver, baseline_cv_rmse=cvb, n_baseline=mb.n,
                              reporting_cv_rmse=cvr, n_reporting=mr.n, confidence=confidence)
