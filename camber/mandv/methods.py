"""M&V adjustment-model methods beyond the forecast: the DOE SEP backcast (provisional).

The forecast -- the baseline model projected onto reporting-period conditions, minus measured
reporting energy -- is :func:`camber.mandv.stats.avoided_energy_savings`. The **backcast** runs the
other way (SEP 50001 M&V Protocol 2019 Ed. 2 §6.2.2, Eq 9): a model fitted on the *reporting*
period is projected back onto the *baseline* period's conditions and subtracted from the measured
baseline energy::

    S = O_b - P_r|b

SEP permits it when the baseline conditions fall within the range of validity of the reporting-
period model (SEP 2012 §3.6.3.1) -- typically when the baseline period cannot support a valid
model but the reporting period can. IPMVP 2012 calls a saving stated at baseline conditions
*normalized* savings, and the IPMVP Core Concepts review draft calls a backcast *avoided energy*;
CAMBER claims neither name and labels the result ``method="backcast"``, ``basis="baseline-period
conditions"``.

Everything follows from the roles swapping: coverage is the **reporting** model's support graded
against the baseline drivers, and the uncertainty is the reporting model's -- the ASHRAE G14
measured-savings kernel by default, or the exact OLS kernel (``kernel="exact"``). Chaining,
standard conditions and method selection are later phases of #21.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

from .coverage import ExtrapolationPolicy, _fsu_factor, _with_used, assess_coverage
from .stats import _check_kernel, _decline, _exact_band, _fsu_measured, _n_effective, _widen

__all__ = ["MethodResult", "backcast_savings"]


@dataclass
class MethodResult:
    """Savings from one SEP adjustment-model method, with its basis and uncertainty kernel.

    ``savings`` is ``measured - projected`` or ``projected - measured`` as the method defines it
    (for a backcast, measured baseline energy minus the reporting model at baseline conditions);
    ``projected`` and ``measured`` are the two totals, and ``savings_pct`` the saving as a fraction
    of the pre-change energy on the stated ``basis``. A severe extrapolation declines as elsewhere
    in :mod:`camber.mandv`: the projected numbers become ``None`` and ``declined_reason`` says why.
    ``kernel`` records which uncertainty kernel produced the band.
    """

    method: str
    basis: str
    kernel: str
    savings: float | None
    projected: float | None
    measured: float
    savings_pct: float | None
    fractional_uncertainty: float | None
    abs_uncertainty: float | None
    confidence: float
    rho: float | None = None
    fsu_autocorrelation_adjusted: bool = False
    n_effective: float | None = None
    df: int | None = None
    coverage: dict | None = None
    declined: bool = False
    declined_reason: str | None = None
    caveats: list = field(default_factory=list)
    fsu_extrapolation_factor: float | None = None

    def as_dict(self) -> dict:
        """Return as a plain dict."""
        return asdict(self)


def _r(x, nd):
    return round(float(x), nd) if x is not None and np.isfinite(x) else float("nan")


def backcast_savings(
    reporting_model,
    T_baseline,
    y_baseline,
    *,
    cv_rmse: float,
    n_reporting: int,
    p_reporting: int,
    confidence: float = 0.90,
    rho: float | None = None,
    extrapolation: ExtrapolationPolicy | None = None,
    kernel: str = "g14",
) -> MethodResult:
    """SEP backcast savings: measured baseline energy minus the reporting model at baseline drivers.

    ``reporting_model`` is fitted on the reporting period (``cv_rmse``, ``n_reporting``,
    ``p_reporting`` are its fit statistics and ``rho`` its residual lag-1 autocorrelation);
    ``T_baseline`` / ``y_baseline`` are the baseline period's drivers and measured energy.
    ``S = O_b - P_r|b``; ``savings_pct`` is ``S / O_b``.

    **Coverage** is the reporting model's support graded against the baseline drivers
    (:func:`~camber.mandv.coverage.assess_coverage`): the backcast is valid only where baseline
    conditions lie inside what the reporting period saw (SEP 2012 §3.6.3.1). A moderate
    extrapolation widens the G14 band by the reporting model's factor ``k``; a severe one declines
    under the default :class:`~camber.mandv.coverage.ExtrapolationPolicy`.

    **Uncertainty** is the reporting model's: ``kernel="g14"`` (default) applies the ASHRAE G14
    measured-savings kernel of :func:`~camber.mandv.stats.avoided_energy_savings` with the roles
    swapped (``m`` baseline points, ``n`` reporting-fit points); ``kernel="exact"`` uses the OLS
    projection variance ``kappa*s2*(g'Ag + m)`` of the reporting model at the baseline drivers
    (it needs a CAMBER-fitted model), which already contains the leverage of extrapolating, so
    ``fsu_extrapolation_factor`` is 1.0. With ``rho=None`` the G14 band is unadjusted; the exact
    kernel falls back to the rho recorded when the model was fitted with a ``time_index``.
    """
    _check_kernel(kernel)
    pol = extrapolation or ExtrapolationPolicy()
    Tb = np.asarray(T_baseline, dtype=float)
    yb = np.asarray(y_baseline, dtype=float)
    n_b = int(len(yb))
    proj_all = np.asarray(reporting_model.predict(Tb), dtype=float)
    mask = np.isfinite(proj_all) & np.isfinite(yb)
    proj, meas = proj_all[mask], yb[mask]
    m = int(len(meas))
    proj_sum = float(proj.sum())
    meas_sum = float(meas.sum())
    savings = meas_sum - proj_sum
    pct = savings / meas_sum if meas_sum != 0 else float("nan")

    cov = _with_used(assess_coverage(reporting_model, Tb, projected=proj_all, policy=pol), n_b, m)
    caveats = list(cov.caveats)
    if m < n_b:
        caveats.append(
            f"{n_b - m} of {n_b} baseline rows were excluded from the savings sums "
            "(no finite reporting-model projection or measured energy)"
        )
    k = None
    df = int(n_reporting) - int(p_reporting)
    if kernel == "exact":
        abs_unc, rho_used, df = _exact_band(reporting_model, Tb, mask, rho, confidence)
        k = 1.0
    else:
        rho_used = None if rho is None or not np.isfinite(rho) else float(rho)
        frac_proj = savings / proj_sum if proj_sum != 0 else float("nan")
        fsu_proj = _fsu_measured(
            cv_rmse,
            n_fit=n_reporting,
            m_report=m,
            savings_fraction=frac_proj if frac_proj != 0 else float("nan"),
            confidence=confidence,
            rho=rho_used or 0.0,
            p_fit=p_reporting,
        )
        abs_unc = abs(savings) * fsu_proj if np.isfinite(fsu_proj) else float("nan")
        if cov.tier in ("moderate", "severe"):
            k = _fsu_factor(reporting_model, Tb[mask], m=m, projected_kernel=False, policy=pol)
            widened, note = _widen(1.0, k, pol, towt="unit" in cov.info)
            if note:
                caveats.append(note)
            abs_unc = abs_unc * widened if np.isfinite(abs_unc) else abs_unc
    frac = abs_unc / abs(savings) if (savings and np.isfinite(abs_unc)) else float("nan")
    r = 0.0 if rho_used is None else rho_used
    n_eff = _n_effective(n_reporting, r)
    res = MethodResult(
        method="backcast",
        basis="baseline-period conditions",
        kernel=kernel,
        savings=round(savings, 2),
        projected=round(proj_sum, 2),
        measured=round(meas_sum, 2),
        savings_pct=_r(pct, 4),
        fractional_uncertainty=_r(frac, 4),
        abs_uncertainty=_r(abs_unc, 2),
        confidence=confidence,
        rho=None if rho_used is None else round(rho_used, 4),
        fsu_autocorrelation_adjusted=bool(r > 0.0),
        n_effective=round(n_eff, 2) if np.isfinite(n_eff) else None,
        df=df,
        coverage=cov.as_dict(),
        caveats=caveats,
        fsu_extrapolation_factor=None if k is None else round(k, 4),
    )
    if cov.tier == "severe" and pol.decline:
        _decline(res, cov.reason)
    return res
