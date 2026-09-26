"""Weather-normalized annual savings (IPMVP "normalized savings" / ASHRAE G14).

CAMBER's :func:`camber.mandv.stats.avoided_energy_savings` answers "how much did we avoid
given the *actual* reporting weather" (IPMVP avoided energy use). This module answers the
complementary "what is the saving in a *typical* year" -- IPMVP **normalized savings** --
by projecting **both** the baseline and the reporting change-point models onto a single
normal-year (e.g. TMY) temperature set and differencing their normalized annual
consumption (NAC). That removes weather from the before/after comparison entirely, so a
hot reporting year doesn't flatter or penalize the result.

    normalized savings = NAC(baseline model) - NAC(reporting model)   over the normal year

Uncertainty follows ASHRAE Guideline 14 Annex B: each projected NAC carries a fractional
uncertainty 1.26 * CV(RMSE) * sqrt((n/M) * (1 + 2/n)) (M normal-year periods, n model-fit
points), and the two are combined in quadrature at the chosen confidence. numpy only;
operates on any model with a ``predict(temps)`` method -- a change-point model directly, or a TOWT
model wrapped in :class:`camber.mandv.towt.TOWTAtIndex` (its own ``predict`` takes an index *and*
temperatures, so it cannot be passed here bare).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

from .coverage import ExtrapolationPolicy, _fsu_factor, _worst, assess_coverage
from .stats import _rel_unc_projected, _t_value, _widen


def normalized_annual_consumption(model, temps) -> float:
    """Normalized annual consumption: the model's predicted energy summed over ``temps``.

    ``temps`` is a normal-year temperature set at the model's period granularity (e.g. 12
    monthly means for a monthly model, or 8760 hourly values for an hourly one); the sum of
    the per-period predictions is the weather-normalized annual energy.

    This returns a bare number and does not check that ``temps`` lie inside the range the model
    was fitted on; use :func:`camber.mandv.coverage.assess_coverage` for that, or
    :func:`normalized_savings`, which does.
    """
    return float(np.asarray(model.predict(np.asarray(temps, dtype=float)), dtype=float).sum())


@dataclass
class NormalizedSavings:
    """Weather-normalized annual savings with a G14 uncertainty band.

    Both models are projected onto the normal year, so each carries its own coverage
    (``coverage_baseline`` / ``coverage_reporting``). A severe extrapolation of either declines
    the result under the default policy: the savings fields and the extrapolated side's NAC become
    ``None``, with ``declined_reason`` saying why.
    """

    nac_baseline: float | None
    nac_reporting: float | None
    normalized_savings: float | None  # nac_baseline - nac_reporting
    savings_pct: float | None  # of normalized baseline
    fractional_uncertainty: float | None  # of the savings, at ``confidence``
    abs_uncertainty: float | None  # +/- energy at ``confidence``
    confidence: float
    n_normal_periods: int
    coverage_baseline: dict | None = None
    coverage_reporting: dict | None = None
    declined: bool = False
    declined_reason: str | None = None
    caveats: list = field(default_factory=list)
    fsu_extrapolation_factor: float | None = None

    def as_dict(self) -> dict:
        """Return the result as a plain dict."""
        return asdict(self)


def normalized_savings(
    baseline_model,
    reporting_model,
    normal_temps,
    *,
    baseline_cv_rmse: float,
    n_baseline: int,
    reporting_cv_rmse: float | None = None,
    n_reporting: int | None = None,
    confidence: float = 0.90,
    p_baseline: int = 2,
    p_reporting: int | None = None,
    rho: float | None = None,
    extrapolation: ExtrapolationPolicy | None = None,
) -> NormalizedSavings:
    """Weather-normalized annual savings between two fitted models over a normal year.

    Projects ``baseline_model`` and ``reporting_model`` onto ``normal_temps`` (the typical
    year) and differences their NAC. Provide each model's fit CV(RMSE) and number of fit
    points for the uncertainty band; if the reporting fit stats are omitted they default to the
    baseline's. ``confidence`` selects the t-multiplier.

    **The band does not depend on how many periods you normalize onto.** Both sides here are model
    *projections*, so there is no measured energy and no residual noise to average down over
    ``normal_temps`` -- only parameter error, which every projected period shares. Measured
    directly, the spread of a projected total is flat across a 730x range in the number of periods.
    That is why this uses :func:`camber.mandv.stats._rel_unc_projected` (OLS average leverage) and
    not the G14 measured-savings kernel, which carries a ``1/m`` term and would be several times too
    narrow at hourly resolution. ``p_baseline`` / ``p_reporting`` are the models' parameter counts;
    ``rho`` is the residual lag-1 autocorrelation (see
    :func:`camber.mandv.stats.lag1_autocorrelation`), widening the band when supplied.

    **Extrapolation.** A normal year is often wider than the period a model was fitted on (a TMY
    cold snap, a reporting model fitted on one season). Each model's coverage of ``normal_temps``
    is graded (``extrapolation``, :class:`~camber.mandv.coverage.ExtrapolationPolicy`); a moderate
    one widens that side's term by the projected-kernel factor ``k = sqrt(s'As / s_c'As_c)``, and a
    severe one declines by default.
    """
    pol = extrapolation or ExtrapolationPolicy()
    temps = np.asarray(normal_temps, dtype=float)
    m = int(len(temps))
    nac_b = normalized_annual_consumption(baseline_model, temps)
    nac_r = normalized_annual_consumption(reporting_model, temps)
    savings = nac_b - nac_r
    pct = savings / nac_b if nac_b else float("nan")

    r_cv = reporting_cv_rmse if reporting_cv_rmse is not None else baseline_cv_rmse
    r_n = n_reporting if n_reporting is not None else n_baseline
    r_p = p_reporting if p_reporting is not None else p_baseline
    rho_used = 0.0 if rho is None or not np.isfinite(rho) else float(rho)
    rel_b = _rel_unc_projected(baseline_cv_rmse, n_fit=n_baseline, p_fit=p_baseline, rho=rho_used)
    rel_r = _rel_unc_projected(r_cv, n_fit=r_n, p_fit=r_p, rho=rho_used)
    t = _t_value(confidence)
    if rel_b == rel_b and rel_r == rel_r:
        abs_unc = t * float(np.sqrt((rel_b * nac_b) ** 2 + (rel_r * nac_r) ** 2))
    else:
        abs_unc = float("nan")

    cov_b = assess_coverage(baseline_model, temps, policy=pol)
    cov_r = assess_coverage(reporting_model, temps, policy=pol)
    caveats = [f"baseline model: {c}" for c in cov_b.caveats]
    caveats += [f"reporting model: {c}" for c in cov_r.caveats]
    tier = _worst(cov_b.tier, cov_r.tier)
    factor = None
    if tier in ("moderate", "severe"):
        kb = kr = 1.0
        for side, cov, model in (
            ("baseline", cov_b, baseline_model),
            ("reporting", cov_r, reporting_model),
        ):
            if cov.tier not in ("moderate", "severe"):
                continue
            k = _fsu_factor(model, temps, m=m, projected_kernel=True, policy=pol)
            widened, note = _widen(1.0, k, pol, towt="unit" in cov.info)
            caveats.append(f"{side} model: {note}")
            if side == "baseline":
                kb = widened
            else:
                kr = widened
        if (kb > 1.0 or kr > 1.0) and abs_unc == abs_unc:
            new = t * float(np.sqrt((rel_b * nac_b * kb) ** 2 + (rel_r * nac_r * kr) ** 2))
            factor = new / abs_unc if abs_unc > 0 else None
            abs_unc = new
        elif abs_unc == abs_unc:
            factor = 1.0
    frac = abs_unc / abs(savings) if (savings and abs_unc == abs_unc) else float("nan")

    res = NormalizedSavings(
        nac_baseline=round(nac_b, 2),
        nac_reporting=round(nac_r, 2),
        normalized_savings=round(savings, 2),
        savings_pct=round(pct, 4) if pct == pct else float("nan"),
        fractional_uncertainty=round(frac, 4) if frac == frac else float("nan"),
        abs_uncertainty=round(abs_unc, 2) if abs_unc == abs_unc else float("nan"),
        confidence=confidence,
        n_normal_periods=m,
        coverage_baseline=cov_b.as_dict(),
        coverage_reporting=cov_r.as_dict(),
        caveats=caveats,
        fsu_extrapolation_factor=None if factor is None else round(factor, 4),
    )
    if tier == "severe" and pol.decline:
        reasons = [
            f"{side} model: {c.reason}"
            for side, c in (("baseline", cov_b), ("reporting", cov_r))
            if c.tier == "severe"
        ]
        res.declined = True
        res.declined_reason = " ".join(reasons)
        res.normalized_savings = res.savings_pct = None
        res.fractional_uncertainty = res.abs_uncertainty = None
        if cov_b.tier == "severe":
            res.nac_baseline = None
        if cov_r.tier == "severe":
            res.nac_reporting = None
    return res
