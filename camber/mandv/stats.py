"""Fit statistics, model acceptance, and savings uncertainty (ASHRAE Guideline 14).

Goodness-of-fit metrics for a change-point model, the ASHRAE G14 / IPMVP model-
acceptance thresholds, and avoided-energy savings with fractional savings
uncertainty (FSU). numpy-only.

**Two uncertainty kernels, and the boundary is physical, not modular.** What matters is whether the
savings difference contains *measured* reporting energy:

* ``measured - projected`` (Option C avoided energy, Option B isolation) carries the reporting
  period's own residual noise, which averages down over ``m`` reporting points. This is the ASHRAE
  G14 Annex-B / Reddy & Claridge (2000) form, :func:`_fsu_measured`.
* ``projected - projected`` (normalized annual consumption, Option D) contains **no measured
  energy at all** -- only parameter error, which is common to every projected period and therefore
  does *not* average down. Its uncertainty is independent of how many periods you project onto, so
  applying the measured kernel there would be badly wrong at long horizons. This is
  :func:`_rel_unc_projected`.

The two have **different provenance**, deliberately. The measured kernel is the published G14
expression, constant and all. The projected kernel is plain OLS average leverage
(``CV * sqrt(p/n)``) -- textbook regression theory, cited as such, rather than carrying G14's
empirical ``1.26`` across to a formula it was never derived for.

Serial correlation inflates both identically, via the effective sample size
``n' = n(1-rho)/(1+rho)`` (:func:`_n_effective`) that G14 Annex-B recommends, with ``rho`` the lag-1
autocorrelation of the model residuals (:func:`lag1_autocorrelation`).

References: ASHRAE Guideline 14-2014 Annex B and Reddy & Claridge (2000), as reproduced in the
public BPA/LBNL/NYSERDA M&V guides (the ASHRAE text itself is paywalled and was not consulted);
IPMVP. Metrics: R2, RMSE, CV(RMSE), NMBE / net determination bias, F-stat, and FSU.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np


@dataclass
class FitStats:
    """Goodness-of-fit metrics plus the G14 model-acceptance verdict."""

    n: int
    p: int  # number of model parameters
    r2: float
    rmse: float
    cv_rmse: float  # CV(RMSE) as a fraction (0.20 = 20%)
    nmbe: float  # normalized mean bias error (net determination bias), fraction
    f_stat: float
    accept: bool  # meets G14 thresholds
    notes: str
    # Lag-1 autocorrelation of the residuals; None when it could not be estimated (too few adjacent
    # pairs, or no time_index was supplied). Feeds the FSU effective-sample-size correction.
    rho_lag1: float | None = None

    def as_dict(self):
        """Return as a plain dict."""
        return asdict(self)


# ASHRAE Guideline 14 CV(RMSE) acceptance thresholds by modeling interval. Finer
# resolution carries more non-temperature scatter, so the threshold is looser:
# monthly ~15%, daily/hourly ~30% (per ASHRAE Guideline 14-2014).
_CV_RMSE_MAX = {"monthly": 0.15, "daily": 0.30, "hourly": 0.30}


def cv_rmse_max_for(interval: str) -> float:
    """G14 CV(RMSE) acceptance threshold for a modeling interval.

    ``interval`` in {"monthly","daily","hourly"}; unknown -> 0.20 (a middle value).
    Judging an hourly model by the monthly gate is incorrect -- use this.
    """
    return _CV_RMSE_MAX.get(interval, 0.20)


def fit_stats(
    y,
    yhat,
    p: int,
    cv_rmse_max: float = 0.20,
    r2_min: float = 0.75,
    nmbe_max: float = 0.005,
    *,
    time_index=None,
) -> FitStats:
    """Goodness-of-fit + G14 acceptance for observed ``y`` vs predicted ``yhat``.

    Default thresholds are the common ASHRAE G14 / IPMVP guidance for monthly/
    energy models: CV(RMSE) <= 20%, R^2 >= 0.75, |NMBE| <= 0.5%. Hourly models use
    a looser CV(RMSE) (see ``cv_rmse_max_for``).

    Pass ``time_index`` (aligned to ``y``) to also estimate the residuals' lag-1 autocorrelation
    into ``rho_lag1``, which the savings-uncertainty kernels need. It is required rather than
    optional-in-spirit: this function drops non-finite rows, which compresses the array and would
    otherwise make non-adjacent residuals look neighbouring. Without it ``rho_lag1`` stays ``None``
    and any band computed from this fit is reported as unadjusted.
    """
    y = np.asarray(y, dtype=float)
    yhat = np.asarray(yhat, dtype=float)
    m = np.isfinite(y) & np.isfinite(yhat)
    idx = None if time_index is None else np.asarray(time_index)[m]
    y, yhat = y[m], yhat[m]
    n = len(y)
    if n <= p:
        raise ValueError("need n > p for statistics")
    resid = y - yhat
    sse = float(resid @ resid)
    ybar = float(y.mean())
    sst = float(((y - ybar) ** 2).sum())
    r2 = 1.0 - sse / sst if sst > 0 else float("nan")
    # RMSE with the regression dof correction (n - p), per G14
    rmse = float(np.sqrt(sse / (n - p)))
    cv_rmse = rmse / ybar if ybar != 0 else float("nan")
    nmbe = float(resid.sum() / ((n - p) * ybar)) if ybar != 0 else float("nan")
    # overall F: (explained/p-1) / (residual/(n-p))
    ssr = sst - sse
    f_stat = (ssr / (p - 1)) / (sse / (n - p)) if (p > 1 and sse > 0) else float("nan")

    ok = (
        np.isfinite(cv_rmse)
        and cv_rmse <= cv_rmse_max
        and np.isfinite(r2)
        and r2 >= r2_min
        and np.isfinite(nmbe)
        and abs(nmbe) <= nmbe_max
    )
    notes = []
    if not (np.isfinite(cv_rmse) and cv_rmse <= cv_rmse_max):
        notes.append(f"CV(RMSE) {cv_rmse:.1%} > {cv_rmse_max:.0%}")
    if not (np.isfinite(r2) and r2 >= r2_min):
        notes.append(f"R2 {r2:.2f} < {r2_min}")
    if not (np.isfinite(nmbe) and abs(nmbe) <= nmbe_max):
        notes.append(f"|NMBE| {abs(nmbe):.2%} > {nmbe_max:.1%}")
    return FitStats(
        n=n,
        p=p,
        r2=round(r2, 4),
        rmse=round(rmse, 4),
        cv_rmse=round(cv_rmse, 4),
        nmbe=round(nmbe, 5),
        f_stat=round(f_stat, 2) if np.isfinite(f_stat) else float("nan"),
        accept=bool(ok),
        notes="; ".join(notes) or "meets G14 thresholds",
        rho_lag1=(None if idx is None else lag1_autocorrelation(resid, index=idx)),
    )


@dataclass
class SavingsResult:
    """Avoided energy with G14 Annex-B fractional savings uncertainty."""

    avoided_energy: float  # baseline-projected minus actual, summed
    baseline_projected: float
    reporting_actual: float
    savings_pct: float  # of projected baseline
    fractional_uncertainty: float  # ASHRAE G14 Annex-B, fraction of savings (at conf)
    confidence: float
    abs_uncertainty: float  # +/- energy at the confidence level
    # None = autocorrelation could not be estimated (never 0.0, which would assert independence)
    rho: float | None = None
    fsu_autocorrelation_adjusted: bool = False
    n_effective: float | None = None  # effective independent baseline points after the rho

    def as_dict(self):
        """Return as a plain dict."""
        return asdict(self)


# Two-sided Student-t critical values by degrees of freedom, for the confidence levels the M&V
# layer supports. The large-sample (normal) limit is the `None` row. A monthly model has df as low
# as 7, where the normal approximation understates t by ~15% -- in the *dishonest* direction -- so
# the table is df-aware rather than a single large-sample constant.
_T_BY_DF: dict = {
    0.80: {5: 1.476, 10: 1.372, 20: 1.325, 30: 1.310, 60: 1.296, 120: 1.289, None: 1.282},
    0.90: {5: 2.015, 10: 1.812, 20: 1.725, 30: 1.697, 60: 1.671, 120: 1.658, None: 1.645},
    0.95: {5: 2.571, 10: 2.228, 20: 2.086, 30: 2.042, 60: 2.000, 120: 1.980, None: 1.960},
}


def _t_value(confidence: float, df: int | None = None) -> float:
    """Two-sided t critical value at ``confidence``, for ``df`` degrees of freedom.

    Raises ``ValueError`` on an unsupported confidence level rather than silently substituting
    another one -- the previous behaviour returned a 90% value for any unrecognised level, so
    asking for 99% quietly got you a 90% band.
    """
    row = _T_BY_DF.get(round(confidence, 2))
    if row is None:
        raise ValueError(f"unsupported confidence {confidence!r}; use one of {sorted(_T_BY_DF)}")
    if df is None or df <= 0:
        return row[None]
    for cut in (5, 10, 20, 30, 60, 120):  # conservative: round df *down* to a table row
        if df <= cut:
            return row[cut]
    return row[None]


def _n_effective(n: float, rho: float) -> float:
    """Effective independent sample size ``n(1-rho)/(1+rho)`` (G14 Annex-B, lag-1 autocorrelation).

    ``rho`` at or above 1 leaves no independent information; the caller reports ``nan`` rather than
    an arbitrarily large band.
    """
    if not np.isfinite(rho) or rho <= 0.0:
        return float(n)
    if rho >= 1.0:
        return 0.0
    return float(n) * (1.0 - rho) / (1.0 + rho)


def _fsu_measured(
    cv_rmse: float,
    *,
    n_fit: int,
    m_report: int,
    savings_fraction: float,
    confidence: float = 0.90,
    rho: float = 0.0,
    p_fit: int | None = None,
) -> float:
    """G14 Annex-B FSU where the savings difference contains **measured** reporting energy.

        FSU = t * 1.26 * CV * sqrt( (n/n') * (1 + 2/n) * (1/m) ) / F

    ``(n / n')`` is written as a literal factor rather than pre-simplified to ``(1+rho)/(1-rho)`` so
    the expression reads term-for-term against the published one. Note the direction: more
    autocorrelation means a smaller ``n'``, a larger ``n/n'``, and a **wider** band.

    The ``1.26`` is the published Reddy & Claridge empirical constant and is deliberately not
    revisited here -- changing it would decouple this from the citable form. (Sun & Baltazar's
    later refinement replaces it with a polynomial in the number of reporting months; out of scope.)
    """
    n_eff = _n_effective(n_fit, rho)
    F = abs(savings_fraction) if np.isfinite(savings_fraction) else float("nan")
    if not (np.isfinite(F) and F > 0 and m_report > 0 and n_eff > 0 and np.isfinite(cv_rmse)):
        return float("nan")
    df = None if p_fit is None else int(n_fit) - int(p_fit)
    t = _t_value(confidence, df)
    bracket = (n_fit / n_eff) * (1.0 + 2.0 / n_fit) * (1.0 / m_report)
    return float(t * 1.26 * cv_rmse * np.sqrt(bracket) / F)


def _rel_unc_projected(cv_rmse: float, *, n_fit: int, p_fit: int, rho: float = 0.0) -> float:
    """Relative uncertainty of a **projected** total -- no measured energy on either side.

        rel = CV * sqrt( (n/n') * (p/n) )

    A normalized-year or calibrated-simulation total is model output only, so it carries parameter
    error alone. That error is shared by every projected period, which is why this does **not**
    depend on how many periods are projected onto -- measured directly, the spread of a projected
    total is flat across m over a 730x range. Applying the measured kernel here would be roughly 4x
    too narrow at 8760 hourly periods, i.e. overconfident.

    ``p/n`` is the average leverage of an OLS fit: plain regression theory, cited as such. G14's
    empirical ``1.26`` is deliberately **not** carried over -- it was derived for the measured case,
    and borrowing it here would be an uncited constant. This form runs ~1.3x conservative against
    simulation, which is the right side of the line.
    """
    n_eff = _n_effective(n_fit, rho)
    if not (np.isfinite(cv_rmse) and n_fit > 0 and p_fit > 0 and n_eff > 0):
        return float("nan")
    return float(cv_rmse * np.sqrt((n_fit / n_eff) * (p_fit / n_fit)))


def lag1_autocorrelation(residuals, *, index=None, min_points: int = 30):
    """Lag-1 autocorrelation of model residuals, or ``None`` when it cannot be estimated.

    ``rho`` feeds the effective-sample-size correction both FSU kernels use. Returning ``None``
    rather than ``0.0`` is deliberate: ``0.0`` *asserts* that residuals are independent, which is
    exactly the kind of untested negative the honesty convention in :mod:`camber.rules.base`
    forbids. A caller that gets ``None`` reports the band as unadjusted and says so.

    Only genuinely adjacent pairs are used. A non-finite residual invalidates the pairs on **both**
    sides of it, so a gap never silently glues its neighbours together; pass ``index`` (a
    DatetimeIndex) and only pairs one modal sampling interval apart are admitted, which matters
    because the M&V paths drop days routinely (``dropna``, non-routine exclusion).

    Without ``index`` the residuals are assumed to be in time order and evenly spaced -- true for
    the aggregated daily/hourly frames the M&V layer fits, but the caller owns that.

    A negative estimate is clamped to ``0.0``: it would *narrow* the band, and narrowing on the
    strength of a noisy negative is the overconfident direction. The estimator is also biased
    slightly toward zero in small samples (at n=30, about -0.09 at a true rho of 0.4), i.e. toward
    under-correcting, which is why ``min_points`` defaults to 30.
    """
    r = np.asarray(residuals, dtype=float)
    if r.ndim != 1 or len(r) < 2:
        return None
    ok = np.isfinite(r)
    pair = ok[:-1] & ok[1:]  # a NaN kills the pairs on both sides of it
    if index is not None:
        idx = np.asarray(index)
        if len(idx) != len(r):
            raise ValueError("index must be the same length as residuals")
        try:
            deltas = np.diff(idx.astype("datetime64[ns]")).astype("float64")
        except (TypeError, ValueError):
            deltas = None
        if deltas is not None and len(deltas):
            finite = deltas[np.isfinite(deltas) & (deltas > 0)]
            if len(finite):
                step = float(np.median(finite))
                pair &= np.abs(deltas - step) <= 0.01 * step
    if int(pair.sum()) < max(3, int(min_points)):
        return None
    a, b = r[:-1][pair], r[1:][pair]
    if a.std() == 0 or b.std() == 0:
        return None
    rho = float(np.corrcoef(a, b)[0, 1])
    if not np.isfinite(rho):
        return None
    return max(0.0, rho)


def avoided_energy_savings(
    baseline_model,
    T_report,
    y_report,
    *,
    cv_rmse: float,
    n_baseline: int,
    p_baseline: int,
    confidence: float = 0.90,
    rho: float | None = None,
) -> SavingsResult:
    """IPMVP Option-C avoided energy use with G14 Annex-B fractional uncertainty.

    Projects the baseline model onto the reporting-period temperatures, subtracts
    actual reporting energy, and sums. Fractional savings uncertainty per ASHRAE
    G14 Annex-B (see :func:`_fsu_measured`):

        Delta_E/E_save = t * (1.26 * CV) * sqrt( (n/n') * (1 + 2/n) * (1/m) ) / F

    where F = savings fraction, m = reporting points, n the baseline points and
    n' = n(1-rho)/(1+rho) the effective independent count. More autocorrelation means a **wider**
    band.

    ``rho`` is the lag-1 autocorrelation of the residuals; :func:`lag1_autocorrelation` estimates
    it, and :class:`~camber.mandv.stats.FitStats` carries it as ``rho_lag1``. Strictly the
    *reporting*-period residuals are wanted, but those are contaminated by the saving itself, so the
    baseline fit's rho is used -- the standard substitution, stated here rather than left implicit.
    Passing ``None`` leaves the band unadjusted and records that on the result.
    """
    T_report = np.asarray(T_report, dtype=float)
    y_report = np.asarray(y_report, dtype=float)
    proj = baseline_model.predict(T_report)
    mask = np.isfinite(proj) & np.isfinite(y_report)
    proj, y_report = proj[mask], y_report[mask]
    m = len(y_report)
    base_sum = float(proj.sum())
    rep_sum = float(y_report.sum())
    avoided = base_sum - rep_sum
    savings_pct = avoided / base_sum if base_sum != 0 else float("nan")

    rho_used = 0.0 if rho is None or not np.isfinite(rho) else float(rho)
    frac_unc = _fsu_measured(
        cv_rmse,
        n_fit=n_baseline,
        m_report=m,
        savings_fraction=savings_pct if savings_pct != 0 else float("nan"),
        confidence=confidence,
        rho=rho_used,
        p_fit=p_baseline,
    )
    abs_unc = abs(avoided) * frac_unc if np.isfinite(frac_unc) else float("nan")
    n_eff = _n_effective(n_baseline, rho_used)

    return SavingsResult(
        avoided_energy=round(avoided, 2),
        baseline_projected=round(base_sum, 2),
        reporting_actual=round(rep_sum, 2),
        savings_pct=round(savings_pct, 4) if np.isfinite(savings_pct) else float("nan"),
        fractional_uncertainty=round(frac_unc, 4) if np.isfinite(frac_unc) else float("nan"),
        confidence=confidence,
        abs_uncertainty=round(abs_unc, 2) if np.isfinite(abs_unc) else float("nan"),
        rho=None if rho is None else round(rho_used, 4),
        fsu_autocorrelation_adjusted=bool(rho is not None and rho_used > 0.0),
        n_effective=round(n_eff, 2) if np.isfinite(n_eff) else None,
    )
