"""Fit statistics, model acceptance, and savings uncertainty (ASHRAE Guideline 14).

Goodness-of-fit metrics for a change-point model, the ASHRAE G14 / IPMVP model-
acceptance thresholds, and avoided-energy savings with ASHRAE G14 Annex-B
fractional savings uncertainty. numpy-only.

References: ASHRAE Guideline 14-2014; IPMVP. Metrics: R2, RMSE, CV(RMSE), NMBE /
net determination bias, F-stat, and fractional savings uncertainty (FSU).
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np


@dataclass
class FitStats:
    """Goodness-of-fit metrics plus the G14 model-acceptance verdict."""

    n: int
    p: int                  # number of model parameters
    r2: float
    rmse: float
    cv_rmse: float          # CV(RMSE) as a fraction (0.20 = 20%)
    nmbe: float             # normalized mean bias error (net determination bias), fraction
    f_stat: float
    accept: bool            # meets G14 thresholds
    notes: str

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


def fit_stats(y, yhat, p: int,
              cv_rmse_max: float = 0.20, r2_min: float = 0.75,
              nmbe_max: float = 0.005) -> FitStats:
    """Goodness-of-fit + G14 acceptance for observed ``y`` vs predicted ``yhat``.

    Default thresholds are the common ASHRAE G14 / IPMVP guidance for monthly/
    energy models: CV(RMSE) <= 20%, R^2 >= 0.75, |NMBE| <= 0.5%. Hourly models use
    a looser CV(RMSE) (see ``cv_rmse_max_for``).
    """
    y = np.asarray(y, dtype=float)
    yhat = np.asarray(yhat, dtype=float)
    m = np.isfinite(y) & np.isfinite(yhat)
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

    ok = (np.isfinite(cv_rmse) and cv_rmse <= cv_rmse_max
          and np.isfinite(r2) and r2 >= r2_min
          and np.isfinite(nmbe) and abs(nmbe) <= nmbe_max)
    notes = []
    if not (np.isfinite(cv_rmse) and cv_rmse <= cv_rmse_max):
        notes.append(f"CV(RMSE) {cv_rmse:.1%} > {cv_rmse_max:.0%}")
    if not (np.isfinite(r2) and r2 >= r2_min):
        notes.append(f"R2 {r2:.2f} < {r2_min}")
    if not (np.isfinite(nmbe) and abs(nmbe) <= nmbe_max):
        notes.append(f"|NMBE| {abs(nmbe):.2%} > {nmbe_max:.1%}")
    return FitStats(n=n, p=p, r2=round(r2, 4), rmse=round(rmse, 4),
                    cv_rmse=round(cv_rmse, 4), nmbe=round(nmbe, 5),
                    f_stat=round(f_stat, 2) if np.isfinite(f_stat) else float("nan"),
                    accept=bool(ok), notes="; ".join(notes) or "meets G14 thresholds")


@dataclass
class SavingsResult:
    """Avoided energy with G14 Annex-B fractional savings uncertainty."""

    avoided_energy: float           # baseline-projected minus actual, summed
    baseline_projected: float
    reporting_actual: float
    savings_pct: float              # of projected baseline
    fractional_uncertainty: float   # ASHRAE G14 Annex-B, fraction of savings (at conf)
    confidence: float
    abs_uncertainty: float          # +/- energy at the confidence level
    def as_dict(self):
        """Return as a plain dict."""
        return asdict(self)


# t-values for common two-sided confidence levels (large-sample normal approx)
_T = {0.80: 1.282, 0.90: 1.645, 0.95: 1.960}


def avoided_energy_savings(baseline_model, T_report, y_report, *,
                           cv_rmse: float, n_baseline: int, p_baseline: int,
                           confidence: float = 0.90,
                           rho: float = 0.0) -> SavingsResult:
    """IPMVP Option-C avoided energy use with G14 Annex-B fractional uncertainty.

    Projects the baseline model onto the reporting-period temperatures, subtracts
    actual reporting energy, and sums. Fractional savings uncertainty per ASHRAE
    G14 Annex-B (simplified):

        Delta_E/E_save = t * (1.26 * CV) * sqrt(n'/m * (1 + 2/n')) / F

    where F = savings fraction (savings / baseline), m = reporting points, n' the
    baseline points (autocorrelation-adjusted if rho given), t the conf t-value.
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

    t = _T.get(round(confidence, 2), 1.645)
    F = abs(savings_pct) if np.isfinite(savings_pct) and savings_pct != 0 else float("nan")
    # autocorrelation-adjusted effective baseline sample size
    n_eff = n_baseline * (1 - rho) / (1 + rho) if rho not in (None, 0.0) else n_baseline
    if np.isfinite(F) and F > 0 and m > 0 and n_eff > 0:
        frac_unc = t * (1.26 * cv_rmse) * np.sqrt((n_eff / m) * (1 + 2.0 / n_eff)) / F
    else:
        frac_unc = float("nan")
    abs_unc = abs(avoided) * frac_unc if np.isfinite(frac_unc) else float("nan")

    return SavingsResult(
        avoided_energy=round(avoided, 2),
        baseline_projected=round(base_sum, 2),
        reporting_actual=round(rep_sum, 2),
        savings_pct=round(savings_pct, 4) if np.isfinite(savings_pct) else float("nan"),
        fractional_uncertainty=round(frac_unc, 4) if np.isfinite(frac_unc) else float("nan"),
        confidence=confidence,
        abs_uncertainty=round(abs_unc, 2) if np.isfinite(abs_unc) else float("nan"),
    )
