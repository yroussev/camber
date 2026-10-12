"""Shared pieces of the CalTRACK 2.0 methods (private; 0.105, #156).

The public entry points are :mod:`camber.mandv.caltrack_daily` (Daily and Billing) and
:mod:`camber.mandv.caltrack_hourly` (Hourly). This module holds what they share: the constants,
each with its source; hourly temperature preparation; weighted least squares with the statistics
CalTRACK selects on; the eemeter-convention metrics; the CalTRACK fractional savings uncertainty;
and the result type.

Sources (pinned; see docs/MANDV.md, "CalTRACK 2.0 methods"):

* **M**: CalTRACK Methods 2.0, ``openeemeter/caltrack@6b630ee`` ``docs/methods.rst``.
* **A**: CalTRACK Technical Appendix, same commit, ``docs/technical-appendix.rst``.
* **E**: eemeter 2.10.0 (Apache-2.0), the reference implementation, tag ``eemeter-2.10.0``.

Rule ids (``CT-D-*``, ``CT-H-*``, ``CT-C-*``) and constant ids (``K1``..``K41``) are those of the
CAMBER implementation spec for #156; the tests cite them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------- constants
# Daily / billing (section 1 of the spec)
BASELINE_DAYS = 365  # K1: the 365 days before the intervention [M#L142, M#L241; E max_days=365]
MAX_MISSING_DAYS = 37  # K2: at most 37 days (10 %) missing [M#L146]
EEMETER_MIN_VALID_FRACTION = 0.9  # K2 [DIFF]: eemeter fails when valid/365 < 0.9 (36 missing)
DAY_USAGE_MIN_COVERAGE = 0.5  # K4: a day with <= 50 % of its intervals missing is kept [M#L154]
DAY_TEMP_MIN_FRACTION = 0.5  # K5: a day's mean temperature needs <= 50 % hours missing [M#L158]
EEMETER_TEMP_SUFFICIENCY_FRACTION = 0.9  # K3: eemeter's temperature-valid day, > 0.9 of hours
BILLING_TEMP_COVERAGE = 0.9  # K6: hourly temperature covers >= 90 % of a bill [M#L164]
EEMETER_BILLING_DAY_HOURS = 12  # K5 [EE]: billing path keeps a day with > 12 hourly values
OFF_CYCLE_MIN_DAYS = 25  # K8: a baseline bill under 25 days is dropped [M#L168]
MONTHLY_MAX_DAYS = 35  # K9: a monthly bill over 35 days is dropped [M#L170]
BIMONTHLY_MAX_DAYS = 70  # K10: a bi-monthly bill over 70 days is dropped [M#L170]
ESTIMATED_COMBINE_MAX_DAYS = 70  # K11: estimated reads combined up to 70 days [M#L162]
TEMP_INTERPOLATE_MAX_HOURS = 6  # K12: interpolate temperature gaps of up to 6 h [M#L176]
OUTLIER_IQR_MULTIPLE = 3.0  # K13: flag usage above median + 3 IQR [M#L206]
BP_MIN, BP_MAX = 30, 90  # K15, K16: balance points 30-90 degF [M#L269-L271; A#L300]
BP_STEP_DOC = 3  # K17: "will use a 3 F search increment" [A#L340; M#L285 max gap 3]
BP_STEP_EEMETER = 1  # K17 [DIFF]: eemeter's range(30, 91) [E design_matrices.py#L93]
MIN_NONZERO_DD_DAYS = 10  # K19: >= 10 days with non-zero degree days (daily) [M#L279]
MIN_NONZERO_DD_BILLING = 0  # K20: the 10-day rule does not apply to billing [M#L279]
MIN_TOTAL_DD = 20.0  # K21: >= 20 degree days per year [M#L281]
MAX_P_VALUE = 1.0  # K23: the p-value screen was removed in 2.0 [A#L508, A#L553]
# Hourly (section 2)
SEGMENT_CENTRE_WEIGHT = 1.0  # K26: the model's own month [M#L505]
SEGMENT_NEIGHBOUR_WEIGHT = 0.5  # K26: the months either side [M#L505]
PRELIM_HDD_BP = 50.0  # K27: preliminary occupancy model heating base [M#L531-L549]
PRELIM_CDD_BP = 65.0  # K27: preliminary occupancy model cooling base
OCCUPANCY_THRESHOLD = 0.65  # K28: occupied iff > 65 % of hours under-predicted [M#L551]
BIN_ENDPOINTS = (30.0, 45.0, 55.0, 65.0, 75.0, 90.0)  # K29 [M#L557]
MIN_HOURS_PER_BIN = 20  # K30 [M#L559]
HOURLY_MONTH_COVERAGE = 0.9  # K31: > 90 % of hours in the month and both neighbours [M#L146]
SINGLE_MODEL_NMBE = 0.01  # K32: NMBE above 1 % in at most two months [M#L507]
SINGLE_MODEL_MAX_MONTHS = 2  # K32
SINGLE_MODEL_RANGE_FRACTION = 0.1  # K33: reporting temperature beyond 10 % of the range [M#L513]
# Metrics and uncertainty (section 3)
CV_RMSE_ACCEPT = 1.0  # K34: building-level CV(RMSE) <= 100 % for portfolio use [M#L655]
FSU_POLY = {  # K36, K37: the a, b, d of a*M^2 + b*M + d [M#L699-L701; E derivatives.py#L66-L73]
    "billing": (-0.00022, 0.03306, 0.94054),
    "daily": (-0.00024, 0.03535, 1.00286),
}
FSU_DAYS_PER_MONTH = 30.0  # K38 [EE]: daily M = Q / 30 [E derivatives.py#L74]
HOUR_OF_WEEK_ORIGIN = "Monday 00:00"  # K40 [M#L529]

FUELS = ("electricity", "gas")


def check_fuel(fuel: str) -> str:
    """``fuel`` validated: ``"electricity"`` or ``"gas"`` (CT-D-1.3, CT-D-5.4)."""
    if fuel not in FUELS:
        raise ValueError(f"fuel must be 'electricity' or 'gas', got {fuel!r}")
    return fuel


def mask_meter_values(values: pd.Series, fuel: str) -> tuple:
    """``(values, n_zero_masked, n_negative)``: CT-D-1.3 and CT-D-1.5 on raw meter values.

    For electricity a value of exactly 0 is missing (CT-D-1.3; gas zeros are valid). Negative
    values are counted, not removed: CalTRACK flags them for review (CT-D-1.5).
    """
    v = pd.to_numeric(values, errors="coerce").astype(float)
    n_neg = int((v < 0).sum())
    n_zero = 0
    if fuel == "electricity":
        zero = v == 0
        n_zero = int(zero.sum())
        if n_zero:
            v = v.mask(zero)
    return v, n_zero, n_neg


def outlier_count(values) -> int:
    """Values above ``median + 3 IQR`` (CT-D-1.6): flagged for review, never removed."""
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    if len(v) < 4:
        return 0
    q1, med, q3 = np.percentile(v, [25, 50, 75])
    return int((v > med + OUTLIER_IQR_MULTIPLE * (q3 - q1)).sum())


# --------------------------------------------------------------------------- temperature
def interpolate_short_gaps(s: pd.Series, max_hours: int = TEMP_INTERPOLATE_MAX_HOURS):
    """``(series, n_filled)``: linear interpolation of runs of at most ``max_hours`` missing hours
    between two present values (CT-D-4.2, CT-H-1.2). A longer run stays wholly missing (pandas'
    ``limit=`` would fill its first hours, which the rule does not allow)."""
    if s.empty or max_hours <= 0:
        return s, 0
    na = s.isna().to_numpy()
    if not na.any():
        return s, 0
    run_id = np.cumsum(~na)  # a missing run shares the id of the value before it
    ok = s.notna().to_numpy()
    lengths = pd.Series(na.astype(int)).groupby(run_id).transform("sum").to_numpy()
    before = np.maximum.accumulate(np.where(ok, np.arange(len(s)), -1))
    after = np.minimum.accumulate(np.where(ok, np.arange(len(s)), len(s))[::-1])[::-1]
    inside = (before >= 0) & (after < len(s))
    fill = na & inside & (lengths <= max_hours)
    if not fill.any():
        return s, 0
    interp = s.interpolate(method="time", limit_area="inside")
    out = s.copy()
    out[fill] = interp[fill]
    return out, int(fill.sum())


def hourly_temperature(temp: pd.Series, *, interpolate: bool = True) -> tuple:
    """``(hourly, n_filled)``: an hourly mean temperature series on a complete hourly grid.

    Sub-hourly readings are first spread to minutes by the nearest reading within 60 minutes and
    then averaged per hour (CT-D-4.3, the NOAA ISD rule, used for any sub-hourly series). Hourly
    readings are used as they are. With ``interpolate`` gaps of up to six hours are filled
    linearly (CT-D-4.2 / CT-H-1.2); eemeter 2.10 does not interpolate.
    """
    t = pd.to_numeric(temp, errors="coerce").astype(float).sort_index()
    t = t[~t.index.duplicated(keep="first")] if t.index.has_duplicates else t
    if t.dropna().empty:
        return pd.Series(dtype=float), 0
    from ..timegrid import interval_hours

    step = interval_hours(t.dropna().index)
    if step < 1.0 - 1e-9:
        tt = t.dropna()
        lo = tt.index.min().floor("h")
        hi = tt.index.max().ceil("h")
        grid = pd.date_range(lo, hi, freq="min", tz=tt.index.tz)
        minutes = tt.reindex(grid, method="nearest", tolerance=pd.Timedelta(minutes=60))
        hourly = minutes.resample("1h").mean()
    else:
        hourly = t.resample("1h").mean()
    n_filled = 0
    if interpolate:
        hourly, n_filled = interpolate_short_gaps(hourly)
    return hourly, n_filled


# --------------------------------------------------------------------------- regression
@dataclass
class WLSFit:
    """A weighted least-squares fit (minimum-norm via the pseudo-inverse, as statsmodels does)."""

    params: np.ndarray
    fitted: np.ndarray
    resid: np.ndarray
    rank: int
    n: int
    ss_res: float
    ss_tot: float
    r_squared: float
    r_squared_adj: float
    bse: np.ndarray
    pvalues: np.ndarray


def wls(X: np.ndarray, y: np.ndarray, w: np.ndarray | None = None, *, has_const: bool = True):
    """Weighted least squares with the statistics CalTRACK selects on (CT-D-6.5, CT-D-8.1).

    ``w=None`` is OLS. The fit is ``pinv(sqrt(w) X) sqrt(w) y`` (the minimum-norm solution, the
    way statsmodels handles a rank-deficient design, CT-H-7.1). ``SS_res = sum(w e^2)`` and, with
    a constant, ``SS_tot = sum(w (y - ybar_w)^2)``; ``R2_adj = 1 - (n - 1)/(n - rank) (1 - R2)``,
    which is the CalTRACK formula ``1 - (SS_res/(P - c - 1)) / (SS_tot/(P - 1))`` for a design of
    full rank (statsmodels' ``rsquared_adj``). An undefined statistic is NaN.
    """
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    n = len(y)
    ww = np.ones(n) if w is None else np.asarray(w, dtype=float)
    sw = np.sqrt(ww)
    Xw = X * sw[:, None]
    pinv = np.linalg.pinv(Xw, rcond=1e-15)
    params = pinv @ (y * sw)
    rank = int(np.linalg.matrix_rank(Xw)) if n else 0
    fitted = X @ params
    resid = y - fitted
    ss_res = float((ww * resid**2).sum())
    if has_const:
        ybar = float((ww * y).sum() / ww.sum()) if ww.sum() > 0 else float("nan")
        ss_tot = float((ww * (y - ybar) ** 2).sum())
    else:
        ss_tot = float((ww * y**2).sum())
    df_resid = n - rank
    with np.errstate(divide="ignore", invalid="ignore"):
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
        k_const = 1 if has_const else 0
        adj = 1.0 - (n - k_const) / df_resid * (1.0 - r2) if df_resid > 0 else float("nan")
        sigma2 = ss_res / df_resid if df_resid > 0 else float("nan")
        cov = pinv @ pinv.T * sigma2
        bse = np.sqrt(np.clip(np.diag(cov), 0.0, None))
        tvals = params / bse
    from .stats import _t_two_sided_p

    pvals = np.array(
        [
            _t_two_sided_p(float(t), float(df_resid)) if np.isfinite(t) else float("nan")
            for t in np.atleast_1d(tvals)
        ]
    )
    return WLSFit(
        params=params,
        fitted=fitted,
        resid=resid,
        rank=rank,
        n=n,
        ss_res=ss_res,
        ss_tot=ss_tot,
        r_squared=float(r2),
        r_squared_adj=float(adj),
        bse=bse,
        pvalues=pvals,
    )


# --------------------------------------------------------------------------- metrics
def autocorr_lag1(resid) -> float | None:
    """Lag-1 autocorrelation of the residuals in row order (``pandas.Series.autocorr``, the
    estimator eemeter reports as ``autocorr_resid``); ``None`` when undefined."""
    r = pd.Series(np.asarray(resid, dtype=float)).dropna()
    if len(r) < 3:
        return None
    with np.errstate(invalid="ignore", divide="ignore"):
        rho = r.autocorr(lag=1)
    return None if rho is None or not np.isfinite(rho) else float(rho)


def caltrack_metrics(observed, predicted, num_parameters: int) -> dict:
    """The CalTRACK / eemeter fit metrics (CT-C-1.2 .. CT-C-1.4) over rows where both are present.

    ``num_parameters`` is ``c``, the explanatory variables excluding the intercept for daily and
    billing models (0, 1 or 2) and the parameter count for an hourly segment (CT-H-7.2).

    * ``cvrmse_adj = sqrt(sum e^2 / (n - c)) / ybar`` (CT-C-1.2; the CalTRACK formula);
      ``cvrmse = sqrt(mean e^2) / ybar``;
    * ``nmbe = sum(yhat - y) / sum(y)`` -- **eemeter's sign**, positive when the model
      over-predicts, the opposite of ASHRAE Guideline 14's (CT-C-1.3);
    * ``r_squared = corr(y, yhat)^2``, ``nmae``, ``mape``, ``mape_no_zeros``;
    * ``autocorr_resid`` (lag 1, residuals in row order) and ``n_prime = n (1 - rho)/(1 + rho)``.
    """
    y = np.asarray(observed, dtype=float)
    yhat = np.asarray(predicted, dtype=float)
    m = np.isfinite(y) & np.isfinite(yhat)
    y, yhat = y[m], yhat[m]
    n = len(y)
    out: dict[str, Any] = {"n": n, "num_parameters": int(num_parameters)}
    if n == 0:
        return out
    e = yhat - y  # eemeter's residual convention: predicted - observed
    ybar = float(y.mean())
    sse = float((e**2).sum())
    dof = n - int(num_parameters)
    with np.errstate(divide="ignore", invalid="ignore"):
        rmse = math.sqrt(sse / n)
        rmse_adj = math.sqrt(sse / dof) if dof > 0 else float("nan")
        r2 = float(np.corrcoef(y, yhat)[0, 1] ** 2) if n > 1 and y.std() and yhat.std() else nan()
        nz = y != 0
        out.update(
            {
                "observed_mean": ybar,
                "predicted_mean": float(yhat.mean()),
                "rmse": rmse,
                "rmse_adj": rmse_adj,
                "cvrmse": rmse / ybar if ybar else nan(),
                "cvrmse_adj": rmse_adj / ybar if ybar else nan(),
                "nmbe": float(e.sum() / y.sum()) if y.sum() else nan(),
                "nmae": float(np.abs(e).sum() / y.sum()) if y.sum() else nan(),
                "mape": float(np.mean(np.abs(e / y))) if nz.all() else nan(),
                "mape_no_zeros": float(np.mean(np.abs(e[nz] / y[nz]))) if nz.any() else nan(),
                "r_squared": r2,
            }
        )
    rho = autocorr_lag1(-e)
    out["autocorr_resid"] = rho
    out["n_prime"] = n * (1 - rho) / (1 + rho) if rho is not None and rho > -1 else float(n)
    return {k: (_clean(v) if isinstance(v, float) else v) for k, v in out.items()}


def nan() -> float:
    return float("nan")


def _clean(v: float) -> float | None:
    return float(v) if np.isfinite(v) else None


def caltrack_fsu(
    *,
    granularity: str,
    cvrmse_adj: float | None,
    n_baseline: int,
    num_parameters: int,
    rho: float | None,
    n_reporting: int,
    reporting_months: float,
    savings_fraction: float,
    confidence: float = 0.90,
) -> dict:
    """The CalTRACK fractional savings uncertainty (CT-C-1.5):

        FSU = t * (a M^2 + b M + d) * CV(RMSE) * sqrt( (P/P') (1 + 2/P') (1/Q) ) / F

    ``a, b, d`` are the billing or daily coefficients (K36, K37); ``M`` the reporting months, ``Q``
    the reporting periods (days for daily, bills for billing) and ``F`` the savings fraction. The
    methods document never defines ``P'``: as eemeter does, ``P' = P (1 - rho)/(1 + rho)`` with
    ``rho`` the baseline residuals' lag-1 autocorrelation (``P' = P`` when it is unknown), and
    ``t`` is Student's t at the one-tailed ``1 - (1 - confidence)/2`` on ``P - c`` degrees of
    freedom. Note the ``(1 + 2/P')``: CalTRACK's form, where ASHRAE Guideline 14's (and CAMBER's
    :func:`~camber.mandv.stats.avoided_energy_savings`) has ``(1 + 2/n)`` and the constant
    ``1.26`` in place of the polynomial. Returns the inputs with ``fsu`` (a fraction of the
    savings) and ``t``; ``fsu`` is ``None`` when undefined (no savings, too few points).
    """
    from .stats import _t_quantile_two_sided

    a, b, d = FSU_POLY[granularity]
    M = float(reporting_months)
    poly = a * M * M + b * M + d
    P = float(n_baseline)
    r = 0.0 if rho is None or not np.isfinite(rho) else float(rho)
    p_prime = P * (1 - r) / (1 + r) if r > -1 else P
    dof = n_baseline - int(num_parameters)
    out: dict[str, Any] = {
        "granularity": granularity,
        "confidence": confidence,
        "a": a,
        "b": b,
        "d": d,
        "M": M,
        "Q": int(n_reporting),
        "P": int(n_baseline),
        "P_prime": p_prime,
        "rho": rho,
        "cvrmse_adj": cvrmse_adj,
        "savings_fraction": savings_fraction,
        "polynomial": poly,
        "t": None,
        "fsu": None,
    }
    if dof <= 0 or n_reporting <= 0 or p_prime <= 0:
        return out
    t = _t_quantile_two_sided(confidence, dof)
    out["t"] = t
    F = abs(savings_fraction) if savings_fraction is not None else float("nan")
    if cvrmse_adj is None or not np.isfinite(F) or F == 0:
        return out
    root = math.sqrt((P / p_prime) * (1 + 2 / p_prime) * (1 / n_reporting))
    out["fsu"] = float(t * poly * cvrmse_adj * root / F)
    return out


# --------------------------------------------------------------------------- result
@dataclass
class CalTRACKResult:
    """A CalTRACK 2.0 savings run: the fitted model, the baseline sufficiency and the savings.

    ``savings`` is CAMBER's :class:`~camber.mandv.stats.SavingsResult` (avoided energy summed over
    the reporting periods, coverage, caveats and the ASHRAE Guideline 14 band, as on every CAMBER
    savings path). ``fsu`` is CalTRACK's own fractional savings uncertainty (CT-C-1.5) for Daily
    and Billing; ``None`` for Hourly, for which CalTRACK 2.0 publishes no FSU coefficients.
    ``fit`` is the baseline's :class:`~camber.mandv.stats.FitStats` (Guideline 14 conventions).
    ``reporting`` is the per-period table (observed, predicted, avoided energy) and ``candidates``
    (Daily/Billing) every candidate model with its status; neither is in :meth:`as_dict`.
    """

    method: str
    model: Any
    savings: Any  # camber.mandv.stats.SavingsResult
    sufficiency: dict
    fit: Any = None  # camber.mandv.stats.FitStats
    fsu: dict | None = None
    eemeter_compat: bool = False
    reporting: pd.DataFrame | None = field(default=None, repr=False)
    candidates: list = field(default_factory=list, repr=False)

    def as_dict(self) -> dict:
        """A JSON-safe dict: the model (its own ``as_dict``), sufficiency, savings, fit and FSU."""
        counts: dict = {}
        for c in self.candidates:
            counts[c.status] = counts.get(c.status, 0) + 1
        return {
            "method": self.method,
            "eemeter_compat": self.eemeter_compat,
            "model": self.model.as_dict(),
            "sufficiency": self.sufficiency,
            "savings": self.savings.as_dict(),
            "fit": None if self.fit is None else self.fit.as_dict(),
            "fsu": self.fsu,
            "candidate_status_counts": counts,
        }


class ProjectionView:
    """A fitted CalTRACK model seen by :func:`~camber.mandv.stats.avoided_energy_savings`.

    The savings function calls ``predict(drivers)`` with one array; ``drivers`` here are the
    per-period degree-day columns of the selected model (daily or billing), or the bound hourly
    timestamps' temperatures. ``coverage`` grades the reporting drivers against the baseline's
    (``camber.mandv.coverage``), via the record in ``_fit_record``.
    """

    def __init__(self, predict, fit_record=None):
        self._predict = predict
        self._fit_record = fit_record

    def predict(self, drivers):
        return np.asarray(self._predict(drivers), dtype=float)

    def coverage(self, drivers, *, projected=None, policy=None):
        from .coverage import _linear_coverage

        return _linear_coverage(self, drivers, projected=projected, policy=policy)


def floats_or_none(a) -> list:
    """An array as a JSON list, NaN as ``None``."""
    return [None if not np.isfinite(v) else float(v) for v in np.asarray(a, dtype=float).ravel()]


def floats_from(v) -> np.ndarray:
    """The inverse of :func:`floats_or_none`."""
    return np.array([np.nan if x is None else x for x in v], dtype=float)
