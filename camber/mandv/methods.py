"""M&V adjustment-model methods: the DOE SEP forecast, backcast, standard conditions and chaining.

The four methods of the *SEP 50001 M&V Protocol, 2019 Edition 2* §6.2, as one result type,
:class:`MethodResult`, plus a CAMBER extension and a method proposer:

* :func:`forecast_savings` -- §6.2.1, Eq 8: the baseline model at reporting conditions minus
  measured reporting energy, ``S = P_b|r - O_r`` (wraps
  :func:`camber.mandv.stats.avoided_energy_savings`).
* :func:`backcast_savings` -- §6.2.2, Eq 9: measured baseline energy minus the reporting model at
  baseline conditions, ``S = O_b - P_r|b``.
* :func:`standard_conditions_savings` -- §6.2.3, Eq 10: both models at one set of standard
  conditions, ``S = P_b|s - P_r|s`` (wraps :func:`camber.mandv.normalized.normalized_savings`).
* :func:`chained_savings` -- §6.2.4, Eq 6 and Eq 11, **exactly**: one intermediate period, of the
  same length as the baseline and reporting periods and lying between them, whose model covers
  both. The chained SEnPI is a product, ``(P_i|b / O_b) x (O_r / P_i|r)``; the saving is a sum,
  ``(O_b - P_i|b) + (P_i|r - O_r)``.
* :func:`sequential_chain` -- a multi-link chain. **A CAMBER extension, not an SEP method**: the
  Protocol defines exactly one intermediate period.
* :func:`select_method` -- proposes a method in SEP's order (forecast, backcast, chaining,
  standard conditions, decline), ranking candidate models by SEP validity then adjusted R² as the
  DOE EnPI tool does. It only **proposes**: a reported saving needs a declared method, and the
  proposal carries a sensitivity table of every valid method instead of a headline figure.

**Uncertainty.** SEP says nothing on uncertainty; the bands are CAMBER's (issue #21 §2.4).
``kernel`` is recorded on every result: the ASHRAE G14 measured-savings kernel is the default for
single-model results (forecast, backcast) and the exact OLS kernel (:mod:`camber.mandv._design`)
for multi-model ones (standard conditions, chaining) -- decision D7. With ``Σ = κ s² A`` the
intermediate model's parameter covariance and ``g`` the design-row sums:

* SEP chain: ``Var S = (g_r - g_b)' Σ (g_r - g_b) + V_noise(b) + V_noise(r)``. Both projections
  share one ``β̂``, and their errors enter with opposite signs, so the independence (B-19) form
  over-states the band whenever ``g_b' Σ g_r > 0``.
* SEnPI, delta method: ``Var ln SEnPI ≈ T_b/P_i|b² + T_r/P_i|r² - 2 g_b' Σ g_r / (P_i|b P_i|r)``
  with ``T = V_param + V_noise`` (noise taken relative to the measured totals).
* Sequential chain: ``Var S = Σ Var S_k`` (IPMVP 2012 B-19) and ``Var ln SEnPI = Σ Var ln
  EnPI_k`` (B-20), which assume the links independent.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import numpy as np

from .coverage import ExtrapolationPolicy, _fsu_factor, _with_used, _worst, assess_coverage
from .sep import sep_range_check
from .stats import (
    _check_kernel,
    _decline,
    _exact_band,
    _fsu_measured,
    _n_effective,
    _t_value,
    _widen,
)

__all__ = [
    "METHODS",
    "SAME_LENGTH_TOLERANCE_DAYS",
    "MethodResult",
    "ChainLink",
    "MethodProposal",
    "forecast_savings",
    "backcast_savings",
    "standard_conditions_savings",
    "chained_savings",
    "sequential_chain",
    "select_method",
]

#: Method names a reported run may declare (``sequential_chain`` is CAMBER's extension).
METHODS = ("forecast", "backcast", "standard_conditions", "chaining", "sequential_chain")


@dataclass
class ChainLink:
    """One link of a chain: a model applied to one period's conditions.

    For the SEP chain the links are the intermediate model backcast to the baseline
    (``enpi = P_i|b / O_b``) and forecast to the reporting period (``enpi = O_r / P_i|r``). For a
    :func:`sequential_chain` they summarise the component results.
    """

    role: str
    method: str
    model_period: str | None
    applied_to: str | None
    projected: float | None
    measured: float | None
    savings: float | None
    enpi: float | None
    abs_uncertainty: float | None = None
    kernel: str | None = None
    coverage_tier: str | None = None
    sep_range_valid: bool | None = None


@dataclass
class MethodResult:
    """Savings from one M&V adjustment-model method, with its basis and uncertainty kernel.

    ``savings`` is the SEP top-down saving of the method (Eq 8-11). ``projected`` and
    ``measured`` are the two totals of a single-model method (a forecast's baseline projection
    and measured reporting energy; a backcast's reporting-model projection and measured baseline
    energy); standard conditions has no measured total (``measured`` is ``None``) and a chain has
    no single projection (``projected`` is ``None``, ``measured`` is the observed reporting
    energy; see ``links``). ``savings_pct`` is the SEP improvement as a fraction, ``1 - enpi``
    (Eq 7 / 100), which for a forecast is the saving over the projected baseline and for a
    backcast the saving over measured baseline energy. ``enpi`` is the method's SEnPI (Eq 5, or
    the Eq 6 product for a chain) and ``enpi_uncertainty`` its ± band at ``confidence`` (delta
    method). A severe extrapolation declines as elsewhere in :mod:`camber.mandv`: the projected
    numbers become ``None`` and ``declined_reason`` says why.

    ``kernel`` records which uncertainty kernel produced the band (``"g14"`` or ``"exact"``; a
    sequential chain records its links' kernel, or ``"mixed"``). ``sep_terms`` are the SEP
    quantities by keyword of :func:`camber.mandv.sep.top_down_savings` (``None`` for the CAMBER
    extension), which :func:`camber.mandv.sep.aggregate_energy_types` sums across energy types.
    ``sep_range_valid`` is the secondary SEP mean-in-range verdict (§6.4.2.1; details in
    ``sep_range``); ``coverage`` stays the primary per-point guard. ``uncertainty_terms`` exposes
    the variance components of multi-model bands. ``baseline_version`` names the stored baseline
    the result used, when there is one.
    """

    method: str
    basis: str
    kernel: str
    savings: float | None
    projected: float | None
    measured: float | None
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
    enpi: float | None = None
    enpi_uncertainty: float | None = None
    links: list = field(default_factory=list)
    sep_terms: dict | None = None
    sep_range_valid: bool | None = None
    sep_range: dict | None = None
    uncertainty_terms: dict | None = None
    baseline_version: str | None = None

    def as_dict(self) -> dict:
        """Return as a plain dict."""
        return asdict(self)


def _r(x, nd):
    return round(float(x), nd) if x is not None and np.isfinite(x) else float("nan")


def _rn(x, nd):
    """Round, or ``None`` for a missing or non-finite number (JSON-safe optional fields)."""
    return round(float(x), nd) if x is not None and np.isfinite(x) else None


def _decline_method(res: MethodResult, reason: str | None) -> None:
    _decline(res, reason)
    res.enpi = res.enpi_uncertainty = None
    if res.sep_terms:
        res.sep_terms = {
            k: v
            for k, v in res.sep_terms.items()
            if k in ("observed_baseline", "observed_reporting")
        }


def _range(model, drivers) -> tuple:
    try:
        rng = sep_range_check(model, drivers)
    except (TypeError, ValueError) as e:
        return None, {"valid": None, "caveats": [f"SEP range rule not evaluated: {e}"]}
    return rng.valid, rng.as_dict()


def _enpi_band(enpi, abs_unc, denom) -> float | None:
    """Delta-method band of a single-ratio SEnPI: ``enpi * band(S) / denominator``."""
    if enpi is None or abs_unc is None or not denom or not np.isfinite(abs_unc):
        return None
    return abs(enpi) * abs_unc / abs(denom)


# --------------------------------------------------------------------------- forecast


def forecast_savings(
    baseline_model,
    T_report,
    y_report,
    *,
    cv_rmse: float,
    n_baseline: int,
    p_baseline: int,
    confidence: float = 0.90,
    rho: float | None = None,
    extrapolation: ExtrapolationPolicy | None = None,
    kernel: str = "g14",
    baseline_version: str | None = None,
) -> MethodResult:
    """SEP forecast savings (§6.2.1, Eq 8): the baseline model at reporting drivers minus measured.

    ``S = P_b|r - O_r``, ``enpi = O_r / P_b|r`` (Eq 5) and ``savings_pct = S / P_b|r``. The
    numbers, coverage and band are exactly those of
    :func:`camber.mandv.stats.avoided_energy_savings` (same arguments; the G14 kernel by default,
    D7); this adds the SEP fields -- ``enpi`` and its delta-method band, ``sep_terms`` and the
    secondary ``sep_range_valid`` verdict of the baseline model against the reporting drivers.
    """
    from .stats import avoided_energy_savings

    sav = avoided_energy_savings(
        baseline_model,
        T_report,
        y_report,
        cv_rmse=cv_rmse,
        n_baseline=n_baseline,
        p_baseline=p_baseline,
        confidence=confidence,
        rho=rho,
        extrapolation=extrapolation,
        kernel=kernel,
    )
    rv, rng = _range(baseline_model, T_report)
    proj = sav.baseline_projected
    meas = sav.reporting_actual
    enpi = meas / proj if proj else None
    df = int(n_baseline) - int(p_baseline)
    if kernel == "exact":
        from ._design import fit_record

        rec = fit_record(baseline_model)
        if rec is not None and rec.n is not None and rec.p is not None:
            df = int(rec.n) - int(rec.p)
    caveats = list(sav.caveats) + list(rng.get("caveats") or [])
    res = MethodResult(
        method="forecast",
        basis="reporting-period conditions",
        kernel=kernel,
        savings=sav.avoided_energy,
        projected=proj,
        measured=meas,
        savings_pct=sav.savings_pct,
        fractional_uncertainty=sav.fractional_uncertainty,
        abs_uncertainty=sav.abs_uncertainty,
        confidence=confidence,
        rho=sav.rho,
        fsu_autocorrelation_adjusted=sav.fsu_autocorrelation_adjusted,
        n_effective=sav.n_effective,
        df=df,
        coverage=sav.coverage,
        caveats=caveats,
        fsu_extrapolation_factor=sav.fsu_extrapolation_factor,
        enpi=_rn(enpi, 6),
        enpi_uncertainty=_rn(_enpi_band(enpi, sav.abs_uncertainty, proj), 6),
        sep_terms={"adjusted_baseline": proj, "observed_reporting": meas},
        sep_range_valid=rv,
        sep_range=rng,
        baseline_version=baseline_version,
    )
    if sav.declined:
        _decline_method(res, sav.declined_reason)
    return res


# --------------------------------------------------------------------------- backcast


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
    baseline_version: str | None = None,
) -> MethodResult:
    """SEP backcast savings: measured baseline energy minus the reporting model at baseline drivers.

    ``reporting_model`` is fitted on the reporting period (``cv_rmse``, ``n_reporting``,
    ``p_reporting`` are its fit statistics and ``rho`` its residual lag-1 autocorrelation);
    ``T_baseline`` / ``y_baseline`` are the baseline period's drivers and measured energy.
    ``S = O_b - P_r|b`` (SEP 2019 Ed. 2 §6.2.2, Eq 9); ``savings_pct`` is ``S / O_b`` and
    ``enpi = P_r|b / O_b``. SEP permits it when the baseline conditions fall within the range of
    validity of the reporting-period model (SEP 2012 §3.6.3.1). IPMVP 2012 calls a saving stated at
    baseline conditions *normalized* savings, and the IPMVP Core Concepts review draft calls a
    backcast *avoided energy*; CAMBER claims neither name and labels the result
    ``method="backcast"``, ``basis="baseline-period conditions"``.

    **Coverage** is the reporting model's support graded against the baseline drivers
    (:func:`~camber.mandv.coverage.assess_coverage`): the backcast is valid only where baseline
    conditions lie inside what the reporting period saw. A moderate extrapolation widens the G14
    band by the reporting model's factor ``k``; a severe one declines under the default
    :class:`~camber.mandv.coverage.ExtrapolationPolicy`. ``sep_range_valid`` adds SEP's
    mean-in-range verdict.

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
    rv, rng = _range(reporting_model, Tb)
    caveats += list(rng.get("caveats") or [])
    enpi = proj_sum / meas_sum if meas_sum else None
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
        enpi=_rn(enpi, 6),
        enpi_uncertainty=_rn(_enpi_band(enpi, abs_unc, meas_sum), 6),
        sep_terms={
            "observed_baseline": round(meas_sum, 2),
            "adjusted_reporting": round(proj_sum, 2),
        },
        sep_range_valid=rv,
        sep_range=rng,
        baseline_version=baseline_version,
    )
    if cov.tier == "severe" and pol.decline:
        _decline_method(res, cov.reason)
    return res


# --------------------------------------------------------------------------- standard conditions


def standard_conditions_savings(
    baseline_model,
    reporting_model,
    standard_drivers,
    *,
    confidence: float = 0.90,
    kernel: str = "exact",
    rho_baseline: float | None = None,
    rho_reporting: float | None = None,
    extrapolation: ExtrapolationPolicy | None = None,
    baseline_cv_rmse: float | None = None,
    n_baseline: int | None = None,
    p_baseline: int | None = None,
    reporting_cv_rmse: float | None = None,
    n_reporting: int | None = None,
    p_reporting: int | None = None,
    baseline_version: str | None = None,
) -> MethodResult:
    """SEP standard-conditions savings (§6.2.3, Eq 10): ``S = P_b|s - P_r|s``.

    Both models are projected onto ``standard_drivers`` (a normal year, for instance) through
    :func:`camber.mandv.normalized.normalized_savings`; ``enpi = P_r|s / P_b|s``. Two models,
    so the **exact** kernel is the default (D7): each side's parameter variance
    ``kappa s2 g'Ag``, combined in quadrature (IPMVP 2012 B-19; the models are fitted on disjoint
    periods) at ``t`` on the smaller degrees of freedom, and the SEnPI band by the delta method
    ``Var ln SEnPI = V_r / P_r|s² + V_b / P_b|s²``. ``kernel="g14"`` needs the fit statistics
    (``baseline_cv_rmse``, ``n_baseline``, ``p_baseline`` and the reporting ones, defaulting to
    the baseline's) and uses CAMBER's projected kernel ``CV sqrt(p/n)`` per model; it gives no
    SEnPI band. SEP's own guidance puts this method last (after forecast, backcast and chaining);
    see :func:`select_method`.
    """
    from ._design import fit_record, projection_variance
    from .normalized import normalized_savings

    _check_kernel(kernel)
    S = np.asarray(standard_drivers, dtype=float)
    nb: int
    pb: int
    nr: int
    pr: int
    if kernel == "exact":
        rb, rr = fit_record(baseline_model), fit_record(reporting_model)
        if rb is None or rr is None or None in (rb.n, rb.p, rr.n, rr.p):
            raise TypeError(
                "kernel='exact' needs models fitted by CAMBER (they record (X'X)^-1, s2, n, p)"
            )
        nb, pb, nr, pr = int(rb.n), int(rb.p), int(rr.n), int(rr.p)  # type: ignore[arg-type]
        cvb = cvr = float("nan")
    else:
        if baseline_cv_rmse is None or n_baseline is None or p_baseline is None:
            raise ValueError("kernel='g14' needs baseline_cv_rmse, n_baseline and p_baseline")
        nb, pb, cvb = int(n_baseline), int(p_baseline), baseline_cv_rmse
        nr = int(n_reporting) if n_reporting is not None else nb
        pr = int(p_reporting) if p_reporting is not None else pb
        cvr = reporting_cv_rmse if reporting_cv_rmse is not None else cvb
    ns = normalized_savings(
        baseline_model,
        reporting_model,
        S,
        baseline_cv_rmse=cvb,
        n_baseline=nb,
        reporting_cv_rmse=cvr,
        n_reporting=nr,
        confidence=confidence,
        p_baseline=pb,
        p_reporting=pr,
        rho=rho_baseline,
        extrapolation=extrapolation,
        kernel=kernel,
        rho_reporting=rho_reporting,
    )
    pb_s, pr_s = ns.nac_baseline, ns.nac_reporting
    enpi = pr_s / pb_s if (pb_s and pr_s is not None) else None
    enpi_unc = None
    terms = None
    caveats = list(ns.caveats)
    if kernel == "exact" and enpi is not None and pb_s and pr_s:
        vb = projection_variance(baseline_model, S, rho=rho_baseline).v_param
        vr = projection_variance(reporting_model, S, rho=rho_reporting).v_param
        var_ln = vr / pr_s**2 + vb / pb_s**2
        t = _t_value(confidence, min(nb - pb, nr - pr))
        enpi_unc = t * abs(enpi) * math.sqrt(var_ln) if var_ln >= 0 else None
        terms = {"v_param_baseline": vb, "v_param_reporting": vr, "var_ln_senpi": var_ln}
    elif kernel == "g14":
        caveats.append("no SEnPI band with kernel='g14' for standard conditions; use 'exact'")
    rv_b, rng_b = _range(baseline_model, S)
    rv_r, rng_r = _range(reporting_model, S)
    caveats += [f"baseline model: {c}" for c in rng_b.get("caveats") or []]
    caveats += [f"reporting model: {c}" for c in rng_r.get("caveats") or []]
    tier = _worst(
        (ns.coverage_baseline or {}).get("tier", "not_evaluated"),
        (ns.coverage_reporting or {}).get("tier", "not_evaluated"),
    )
    res = MethodResult(
        method="standard_conditions",
        basis="standard conditions",
        kernel=kernel,
        savings=ns.normalized_savings,
        projected=pb_s,
        measured=None,
        savings_pct=ns.savings_pct,
        fractional_uncertainty=ns.fractional_uncertainty,
        abs_uncertainty=ns.abs_uncertainty,
        confidence=confidence,
        df=int(min(nb - pb, nr - pr)),
        coverage={
            "tier": tier,
            "baseline": ns.coverage_baseline,
            "reporting": ns.coverage_reporting,
        },
        declined=ns.declined,
        declined_reason=ns.declined_reason,
        caveats=caveats,
        fsu_extrapolation_factor=ns.fsu_extrapolation_factor,
        enpi=_rn(enpi, 6),
        enpi_uncertainty=_rn(enpi_unc, 6),
        sep_terms=(
            None if ns.declined else {"adjusted_baseline": pb_s, "adjusted_reporting": pr_s}
        ),
        sep_range_valid=_both(rv_b, rv_r),
        sep_range={"baseline": rng_b, "reporting": rng_r},
        uncertainty_terms=terms,
        baseline_version=baseline_version,
    )
    if ns.declined:
        res.enpi = res.enpi_uncertainty = None
    return res


def _both(a, b):
    if a is None or b is None:
        return None
    return bool(a and b)


# --------------------------------------------------------------------------- SEP chaining


def _ts(x):
    import pandas as pd

    return pd.Timestamp(x).normalize()


def _period_days(p) -> int:
    if not (isinstance(p, (list, tuple)) and len(p) == 2):
        raise ValueError(f"a period must be a [start, end] pair, got {p!r}")
    start, end = _ts(p[0]), _ts(p[1])
    if end < start:
        raise ValueError(f"period {p!r} ends before it starts")
    return int((end - start).days) + 1


#: The largest difference, in days, between period lengths that still counts as "the same
#: length" for SEP chaining: a leap day. A CAMBER tolerance -- the Protocol states no number.
SAME_LENGTH_TOLERANCE_DAYS = 1


def _check_sep_periods(periods) -> dict:
    """Validate SEP 2019 Ed. 2 §6.2.4's period rule; return the three lengths in days."""
    try:
        b, i, r = periods["baseline"], periods["intermediate"], periods["reporting"]
    except (TypeError, KeyError) as e:
        raise ValueError(
            "periods must map 'baseline', 'intermediate' and 'reporting' to [start, end] pairs"
        ) from e
    lb, li, lr = _period_days(b), _period_days(i), _period_days(r)
    if max(lb, li, lr) - min(lb, li, lr) > SAME_LENGTH_TOLERANCE_DAYS:
        raise ValueError(
            f"SEP chaining needs an intermediate period of the same length as the baseline and "
            f"reporting periods (§6.2.4); got {lb}, {li} and {lr} days. For links of other "
            "lengths use sequential_chain (a CAMBER extension)"
        )
    if not (_ts(b[1]) < _ts(i[0]) and _ts(i[1]) < _ts(r[0])):
        raise ValueError(
            f"SEP chaining needs the intermediate period to lie between the baseline and "
            f"reporting periods (§6.2.4); got baseline {list(b)}, intermediate {list(i)}, "
            f"reporting {list(r)}"
        )
    return {"baseline_days": lb, "intermediate_days": li, "reporting_days": lr}


def _side(model, T, y, pol):
    T = np.asarray(T, dtype=float)
    y = np.asarray(y, dtype=float)
    proj_all = np.asarray(model.predict(T), dtype=float)
    mask = np.isfinite(proj_all) & np.isfinite(y)
    if T.ndim > 1:
        mask &= np.all(np.isfinite(T), axis=1)
    else:
        mask &= np.isfinite(T)
    cov = _with_used(
        assess_coverage(model, T, projected=proj_all, policy=pol), len(y), int(mask.sum())
    )
    return T, mask, float(proj_all[mask].sum()), float(y[mask].sum()), cov


def chained_savings(
    intermediate_model,
    T_baseline,
    y_baseline,
    T_reporting,
    y_reporting,
    *,
    periods: dict,
    confidence: float = 0.90,
    rho: float | None = None,
    extrapolation: ExtrapolationPolicy | None = None,
    kernel: str = "exact",
    baseline_version: str | None = None,
) -> MethodResult:
    """SEP chaining (SEP 2019 Ed. 2 §6.2.4, Eq 6 and Eq 11), exactly as the Protocol defines it.

    ``intermediate_model`` is fitted on an intermediate period; it is **backcast** onto the
    baseline drivers and **forecast** onto the reporting drivers. ``periods`` maps
    ``"baseline"``, ``"intermediate"`` and ``"reporting"`` to ``[start, end]`` dates; the
    intermediate period must be "of the same length of, and ... in between the baseline and
    reporting periods" (§6.2.4) -- lengths equal to within :data:`SAME_LENGTH_TOLERANCE_DAYS` -- or
    ``ValueError`` is raised. Its model must cover both (SEP 2012 §3.6.5: its range of validity
    "includes both the baseline and reporting-period years"): coverage is graded on each side and a
    severe extrapolation on either declines; ``sep_range_valid`` requires SEP's mean rule on both.

    * ``savings = (O_b - P_i|b) + (P_i|r - O_r)`` -- Eq 11, additive;
    * ``enpi = (P_i|b / O_b) x (O_r / P_i|r)`` -- Eq 6, a product; ``savings_pct = 1 - enpi``;
    * ``links`` -- the backcast link (baseline to intermediate) and the forecast link
      (intermediate to reporting).

    **Uncertainty** uses the exact kernel only (D7): both projections share ``β̂_i``, so
    ``Var S = (g_r - g_b)' Σ_i (g_r - g_b) + V_noise(b) + V_noise(r)`` with the intermediate
    model's ``κ s²`` standing in for each period's noise, at ``t`` on its ``n - p`` degrees of
    freedom. ``uncertainty_terms`` also reports the independence (IPMVP B-19) variance the shared
    covariance replaces. ``kernel="g14"`` raises ``ValueError``: G14's kernel has no covariance
    term. A multi-link chain is not SEP; see :func:`sequential_chain`.
    """
    from ._design import fit_record, projection_variance

    _check_kernel(kernel)
    if kernel != "exact":
        raise ValueError(
            "chaining shares one model between two applications; only kernel='exact' carries "
            "the covariance of the two projections (issue #21 decision D7)"
        )
    lengths = _check_sep_periods(periods)
    pol = extrapolation or ExtrapolationPolicy()
    Tb, mb, P_ib, O_b, cov_b = _side(intermediate_model, T_baseline, y_baseline, pol)
    Tr, mr, P_ir, O_r, cov_r = _side(intermediate_model, T_reporting, y_reporting, pol)
    savings = (O_b - P_ib) + (P_ir - O_r)
    enpi_b = P_ib / O_b if O_b else float("nan")
    enpi_r = O_r / P_ir if P_ir else float("nan")
    enpi = enpi_b * enpi_r

    pv_b = projection_variance(intermediate_model, Tb, rows=mb, rho=rho)
    pv_r = projection_variance(intermediate_model, Tr, rows=mr, rho=rho)
    rec = fit_record(intermediate_model)
    if rec is None or rec.xtx_pinv is None:  # projection_variance has already refused it
        raise TypeError("chained_savings needs a model fitted by CAMBER")
    A = rec.xtx_pinv
    kappa_s2 = pv_b.kappa * pv_b.s2
    cross = float(kappa_s2 * (pv_b.g @ A @ pv_r.g))
    v_param = pv_b.v_param + pv_r.v_param - 2.0 * cross
    var_s = v_param + pv_b.v_noise + pv_r.v_noise
    var_indep = pv_b.v_param + pv_r.v_param + pv_b.v_noise + pv_r.v_noise
    t = _t_value(confidence, pv_b.df)
    abs_unc = t * math.sqrt(var_s) if np.isfinite(var_s) and var_s >= 0 else float("nan")
    var_ln = (
        pv_b.v_param / P_ib**2
        + pv_r.v_param / P_ir**2
        - 2.0 * cross / (P_ib * P_ir)
        + pv_b.v_noise / O_b**2
        + pv_r.v_noise / O_r**2
        if (P_ib and P_ir and O_b and O_r)
        else float("nan")
    )
    enpi_unc = t * abs(enpi) * math.sqrt(var_ln) if np.isfinite(var_ln) and var_ln >= 0 else None
    frac = abs_unc / abs(savings) if (savings and np.isfinite(abs_unc)) else float("nan")

    rv_b, rng_b = _range(intermediate_model, Tb)
    rv_r, rng_r = _range(intermediate_model, Tr)
    caveats = [f"backcast to the baseline: {c}" for c in cov_b.caveats]
    caveats += [f"forecast to the reporting period: {c}" for c in cov_r.caveats]
    caveats += [f"backcast to the baseline: {c}" for c in rng_b.get("caveats") or []]
    caveats += [f"forecast to the reporting period: {c}" for c in rng_r.get("caveats") or []]
    for name, mask, n in (("baseline", mb, len(mb)), ("reporting", mr, len(mr))):
        if int(mask.sum()) < n:
            caveats.append(
                f"{n - int(mask.sum())} of {n} {name} rows were excluded from the chain sums "
                "(no finite projection, driver or measured energy)"
            )
    tier = _worst(cov_b.tier, cov_r.tier)
    rho_used = pv_b.rho
    n_eff = _n_effective(rec.n, rho_used or 0.0) if rec.n is not None else float("nan")
    links = [
        ChainLink(
            role="baseline to intermediate",
            method="backcast",
            model_period="intermediate",
            applied_to="baseline",
            projected=round(P_ib, 2),
            measured=round(O_b, 2),
            savings=round(O_b - P_ib, 2),
            enpi=_rn(enpi_b, 6),
            kernel="exact",
            coverage_tier=cov_b.tier,
            sep_range_valid=rv_b,
        ),
        ChainLink(
            role="intermediate to reporting",
            method="forecast",
            model_period="intermediate",
            applied_to="reporting",
            projected=round(P_ir, 2),
            measured=round(O_r, 2),
            savings=round(P_ir - O_r, 2),
            enpi=_rn(enpi_r, 6),
            kernel="exact",
            coverage_tier=cov_r.tier,
            sep_range_valid=rv_r,
        ),
    ]
    res = MethodResult(
        method="chaining",
        basis="intermediate-period model at baseline and reporting conditions",
        kernel="exact",
        savings=round(savings, 2),
        projected=None,
        measured=round(O_r, 2),
        savings_pct=_r(1.0 - enpi, 4),
        fractional_uncertainty=_r(frac, 4),
        abs_uncertainty=_r(abs_unc, 2),
        confidence=confidence,
        rho=None if rho_used is None else round(rho_used, 4),
        fsu_autocorrelation_adjusted=bool(rho_used is not None and rho_used > 0),
        n_effective=round(n_eff, 2) if np.isfinite(n_eff) else None,
        df=pv_b.df,
        coverage={
            "tier": tier,
            "baseline": cov_b.as_dict(),
            "reporting": cov_r.as_dict(),
            **lengths,
        },
        caveats=caveats,
        fsu_extrapolation_factor=1.0,
        enpi=_rn(enpi, 6),
        enpi_uncertainty=_rn(enpi_unc, 6),
        links=links,
        sep_terms={
            "observed_baseline": round(O_b, 2),
            "intermediate_at_baseline": round(P_ib, 2),
            "intermediate_at_reporting": round(P_ir, 2),
            "observed_reporting": round(O_r, 2),
        },
        sep_range_valid=_both(rv_b, rv_r),
        sep_range={"baseline": rng_b, "reporting": rng_r},
        uncertainty_terms={
            "v_param_baseline": pv_b.v_param,
            "v_param_reporting": pv_r.v_param,
            "covariance": cross,
            "v_noise_baseline": pv_b.v_noise,
            "v_noise_reporting": pv_r.v_noise,
            "var_savings": var_s,
            "var_savings_independent": var_indep,
            "var_ln_senpi": var_ln if np.isfinite(var_ln) else None,
        },
        baseline_version=baseline_version,
    )
    if tier == "severe" and pol.decline:
        reasons = [
            f"{side}: {c.reason}"
            for side, c in (("backcast to the baseline", cov_b), ("forecast", cov_r))
            if c.tier == "severe"
        ]
        _decline_method(res, " ".join(reasons))
        for ln in res.links:
            ln.projected = ln.savings = ln.enpi = None
    return res


# --------------------------------------------------------------------------- sequential chain

_EXTENSION = (
    "CAMBER extension, not an SEP method: SEP 2019 Ed. 2 §6.2.4 chains through exactly one "
    "intermediate period, and this chain has {n} links"
)


def sequential_chain(links, *, baseline_version: str | None = None) -> MethodResult:
    """A multi-link chain of method results -- a **CAMBER extension**, not an SEP method.

    ``links`` are consecutive :class:`MethodResult` s (for example year-over-year forecasts, each
    against its own rebaselined model), in order. The chained SEnPI is the product of the links'
    ``enpi`` (generalising Eq 6) and the saving the sum of theirs (generalising Eq 11);
    ``savings_pct = 1 - enpi``. The bands combine as independent components (IPMVP 2012 Appendix
    B-5): ``Var S = Σ Var S_k`` (B-19) and ``Var ln SEnPI = Σ Var ln EnPI_k`` (B-20), each link's
    variance recovered from its band at its own ``t``, the whole at ``t`` on the smallest
    ``df``. Independence holds only approximately -- a link whose model was fitted on a period
    another link measures shares that period's noise -- and a caveat says so. The SEP chain with
    its shared model is :func:`chained_savings`.

    Every link must be undeclined and share one ``confidence``; a declined link declines the
    chain. The result has no ``sep_terms``: it cannot be aggregated as an SEP SEnPI.
    """
    links = list(links)
    if len(links) < 2:
        raise ValueError("a sequential chain needs at least two links")
    for k, ln in enumerate(links):
        if not isinstance(ln, MethodResult):
            raise TypeError(f"link {k} is a {type(ln).__name__}, not a MethodResult")
    confs = {ln.confidence for ln in links}
    if len(confs) != 1:
        raise ValueError(f"links are at different confidence levels {sorted(confs)}")
    (confidence,) = confs
    kernels = {ln.kernel for ln in links}
    kernel = kernels.pop() if len(kernels) == 1 else "mixed"
    rows = [
        ChainLink(
            role=f"link {k + 1}",
            method=ln.method,
            model_period=None,
            applied_to=ln.basis,
            projected=ln.projected,
            measured=ln.measured,
            savings=ln.savings,
            enpi=ln.enpi,
            abs_uncertainty=ln.abs_uncertainty,
            kernel=ln.kernel,
            coverage_tier=(ln.coverage or {}).get("tier"),
            sep_range_valid=ln.sep_range_valid,
        )
        for k, ln in enumerate(links)
    ]
    caveats = [
        _EXTENSION.format(n=len(links)),
        "the links' bands are combined as independent (IPMVP 2012 B-19 / B-20); a link whose "
        "model was fitted on a period another link measures is not independent of it",
    ]
    for k, ln in enumerate(links):
        caveats += [f"link {k + 1}: {c}" for c in ln.caveats]
    declined = [k + 1 for k, ln in enumerate(links) if ln.declined]
    tiers = [r.coverage_tier for r in rows if r.coverage_tier]
    res = MethodResult(
        method="sequential_chain",
        basis="each link at its own conditions (CAMBER extension)",
        kernel=kernel,
        savings=None,
        projected=None,
        measured=None,
        savings_pct=None,
        fractional_uncertainty=None,
        abs_uncertainty=None,
        confidence=confidence,
        df=min((ln.df for ln in links if ln.df is not None), default=None),
        coverage={"tier": _worst(*tiers) if tiers else "not_evaluated", "links": tiers},
        caveats=caveats,
        links=rows,
        sep_terms=None,
        sep_range_valid=(
            None
            if any(ln.sep_range_valid is None for ln in links)
            else all(ln.sep_range_valid for ln in links)
        ),
        baseline_version=baseline_version,
    )
    if declined:
        res.declined = True
        res.declined_reason = f"link(s) {declined} declined"
        return res
    enpi = float(np.prod([ln.enpi for ln in links]))
    savings = float(sum(ln.savings for ln in links))
    var_s = var_ln = 0.0
    for ln in links:
        tk = _t_value(ln.confidence, ln.df)
        if ln.abs_uncertainty is None or not np.isfinite(ln.abs_uncertainty):
            var_s = float("nan")
        else:
            var_s += (ln.abs_uncertainty / tk) ** 2
        if ln.enpi_uncertainty is None or not ln.enpi:
            var_ln = float("nan")
        else:
            var_ln += (ln.enpi_uncertainty / (tk * abs(ln.enpi))) ** 2
    t = _t_value(confidence, res.df)
    abs_unc = t * math.sqrt(var_s) if np.isfinite(var_s) else float("nan")
    res.savings = round(savings, 2)
    res.savings_pct = _r(1.0 - enpi, 4)
    res.enpi = _rn(enpi, 6)
    res.enpi_uncertainty = (
        _rn(t * abs(enpi) * math.sqrt(var_ln), 6) if np.isfinite(var_ln) else None
    )
    res.abs_uncertainty = _r(abs_unc, 2)
    res.fractional_uncertainty = _r(abs_unc / abs(savings), 4) if savings else float("nan")
    res.uncertainty_terms = {
        "combination": "IPMVP 2012 B-19 (savings) and B-20 (SEnPI), links independent",
        "var_savings": var_s if np.isfinite(var_s) else None,
        "var_ln_senpi": var_ln if np.isfinite(var_ln) else None,
    }
    return res


# --------------------------------------------------------------------------- method selection


@dataclass
class MethodProposal:
    """What :func:`select_method` proposes -- never a reported saving.

    ``proposed`` is the first method in SEP's order whose conditions hold (``None`` when it
    declines). ``steps`` records every step's verdict and reasons; ``models`` the candidate model
    chosen for each period (kind, SEP validity, adjusted R², G14 acceptance); ``sensitivity`` one
    row per **valid** method with its saving, SEnPI and band, so the spread between valid methods
    is visible (Chen & Therkelsen, LBNL-2001209, 2019: all four SEP methods valid on one facility,
    SEnPI 0.93-1.00). ``results`` holds the full :class:`MethodResult` of each valid method.
    """

    proposed: str | None
    reason: str
    steps: list
    sensitivity: list
    models: dict
    intermediate_period: list | None = None
    declined: bool = False
    declined_reason: str | None = None
    caveats: list = field(default_factory=list)
    results: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        """Return as a plain dict."""
        return asdict(self)


_PROPOSAL_CAVEAT = (
    "a proposal, not a reported result: a reported saving needs a declared method, frozen with "
    "the baseline; choosing among valid methods after seeing their numbers is method shopping"
)


@dataclass
class _Candidate:
    model: object
    kind: str
    sep_valid: bool
    adj_r2: float
    failures: list
    cv_rmse: float
    n: int
    p: int
    rho: float | None
    accept: bool

    def summary(self) -> dict:
        return {
            "kind": self.kind,
            "sep_valid": self.sep_valid,
            "adj_r2": _rn(self.adj_r2, 4),
            "sep_failures": list(self.failures),
            "g14_accept": self.accept,
            "cv_rmse": _rn(self.cv_rmse, 4),
            "n": self.n,
        }


def _rank_models(T, y, index, kinds) -> list:
    """Fit each kind and rank by SEP validity, then adjusted R² (the DOE EnPI tool's order)."""
    from .models import N_PARAMS, fit_model
    from .stats import fit_stats, logical_signs, model_regression_tests, sep_validity

    out = []
    for kind in kinds:
        try:
            m = fit_model(T, y, kind, time_index=index)
            p = N_PARAMS[kind]
            st = fit_stats(y, m.predict(T), p, time_index=index)
            tests = model_regression_tests(m, T, y, time_index=index)
            v = sep_validity(tests, signs=logical_signs(m))
        except (ValueError, TypeError, np.linalg.LinAlgError):
            continue
        adj = tests.adj_r2 if tests.adj_r2 is not None else float("-inf")
        out.append(
            _Candidate(
                m, kind, v.sep_valid, adj, v.failures, st.cv_rmse, st.n, p, st.rho_lag1, st.accept
            )
        )
    out.sort(key=lambda c: (c.sep_valid, c.adj_r2), reverse=True)
    return out


def _slice(frame, win, driver, energy):
    sub = frame.loc[str(win[0]) : str(win[1]), [driver, energy]].dropna()
    return sub[driver].to_numpy(float), sub[energy].to_numpy(float), sub.index


def _covers(model, T, pol) -> tuple:
    """``(ok, tier, sep_range_valid)``: coverage not severe and the SEP mean rule holds."""
    cov = assess_coverage(model, T, policy=pol)
    rv, _ = _range(model, T)
    return (cov.tier != "severe" and rv is not False), cov.tier, rv


def _intermediate_windows(baseline, reporting, step: str):
    """Candidate intermediate windows: the baseline's length, strictly between the periods.

    Starts every ``step`` in between, plus the two boundary-aligned starts -- the day after the
    baseline ends and the latest start that still ends the day before the reporting period -- so
    periods that do not begin on a ``step`` boundary (one starting mid-month) still get the
    window that abuts them.
    """
    import pandas as pd

    b0, b1 = _ts(baseline[0]), _ts(baseline[1])
    r0 = _ts(reporting[0])
    span = b1 - b0
    first, last = b1 + pd.Timedelta(days=1), r0 - span - pd.Timedelta(days=1)
    if last < first:
        return []
    starts = {first, last, *pd.date_range(first, last, freq=step)}
    return [(s, s + span) for s in sorted(starts)]


def select_method(
    frame,
    *,
    baseline,
    reporting,
    driver: str = "oat",
    energy: str = "energy",
    kinds=("2P", "3PC", "3PH", "4P", "5P"),
    standard_conditions=None,
    confidence: float = 0.90,
    extrapolation: ExtrapolationPolicy | None = None,
    window_step: str = "MS",
) -> MethodProposal:
    """Propose an SEP adjustment-model method, in the Protocol's order, with a sensitivity table.

    ``frame`` is a time-indexed table (daily, typically) with a ``driver`` and an ``energy``
    column; ``baseline`` and ``reporting`` are ``[start, end]`` windows of it. For each period the
    candidate models (``kinds``) are fitted and ranked by SEP validity (§6.4.1), then adjusted R²
    -- the DOE EnPI tool's ranking (EnPI V5 manual, Step 7). Then, in SEP's order:

    1. **forecast** -- the baseline model is SEP-valid and covers the reporting period (per-point
       coverage not severe, and SEP's mean-in-range rule);
    2. **backcast** -- the reporting model is valid and covers the baseline period
       (SEP 2012 §3.6.3.1);
    3. **chaining** -- among intermediate windows of the baseline's length lying strictly between
       the periods (starting every ``window_step``), the best-ranked valid model that covers both
       (SEP 2019 Ed. 2 §6.2.4; SEP 2012 §3.6.5);
    4. **standard conditions** -- both models valid and both covering ``standard_conditions``
       (driver values; the step is skipped without them); SEP's Guidance puts it last;
    5. otherwise **decline** (SEP 2012 §3.6.6: an alternative method needs the program
       administrator's approval).

    **It only proposes.** A reported saving needs a declared ``method`` (the method-shopping
    guard: valid methods can disagree widely, Chen & Therkelsen 2019). The proposal therefore
    carries no headline figure: ``sensitivity`` lists every valid method's saving, SEnPI and band
    side by side. Forecast and backcast use the G14 kernel, chaining and standard conditions the
    exact kernel (D7).
    """
    pol = extrapolation or ExtrapolationPolicy()
    Tb, yb, ib = _slice(frame, baseline, driver, energy)
    Tr, yr, ir = _slice(frame, reporting, driver, energy)
    steps: list = []
    results: dict = {}
    models: dict = {}
    cand_b = _rank_models(Tb, yb, ib, kinds) if len(yb) > 5 else []
    cand_r = _rank_models(Tr, yr, ir, kinds) if len(yr) > 5 else []
    best_b = cand_b[0] if cand_b else None
    best_r = cand_r[0] if cand_r else None
    models["baseline"] = best_b.summary() if best_b else None
    models["reporting"] = best_r.summary() if best_r else None
    caveats = [_PROPOSAL_CAVEAT]

    # 1. forecast
    reasons = []
    valid = False
    if best_b is None:
        reasons.append("no baseline model could be fitted")
    elif not best_b.sep_valid:
        reasons.append(f"baseline model not SEP-valid: {'; '.join(best_b.failures)}")
    else:
        ok, tier, rv = _covers(best_b.model, Tr, pol)
        reasons.append(f"baseline coverage of the reporting period: {tier}; SEP range rule {rv}")
        valid = ok
    if valid and best_b is not None:
        results["forecast"] = forecast_savings(
            best_b.model,
            Tr,
            yr,
            cv_rmse=best_b.cv_rmse,
            n_baseline=best_b.n,
            p_baseline=best_b.p,
            confidence=confidence,
            rho=best_b.rho,
            extrapolation=pol,
        )
    steps.append({"method": "forecast", "valid": valid, "reasons": reasons})

    # 2. backcast
    reasons, valid = [], False
    if best_r is None:
        reasons.append("no reporting-period model could be fitted")
    elif not best_r.sep_valid:
        reasons.append(f"reporting model not SEP-valid: {'; '.join(best_r.failures)}")
    else:
        ok, tier, rv = _covers(best_r.model, Tb, pol)
        reasons.append(f"reporting-model coverage of the baseline: {tier}; SEP range rule {rv}")
        valid = ok
    if valid and best_r is not None:
        results["backcast"] = backcast_savings(
            best_r.model,
            Tb,
            yb,
            cv_rmse=best_r.cv_rmse,
            n_reporting=best_r.n,
            p_reporting=best_r.p,
            confidence=confidence,
            rho=best_r.rho,
            extrapolation=pol,
        )
    steps.append({"method": "backcast", "valid": valid, "reasons": reasons})

    # 3. chaining
    reasons, valid, inter = [], False, None
    lb: int | None = None
    lr: int | None = None
    try:
        lb, lr = _period_days(baseline), _period_days(reporting)
    except ValueError as e:
        reasons.append(str(e))
    if lb is None or lr is None:
        pass
    elif abs(lb - lr) > SAME_LENGTH_TOLERANCE_DAYS:
        reasons.append(f"baseline ({lb} days) and reporting ({lr} days) differ in length")
    else:
        windows = _intermediate_windows(baseline, reporting, window_step)
        if not windows:
            reasons.append("no intermediate window of the baseline's length fits between them")
        found = []
        for w in windows:
            Ti, yi, ii = _slice(frame, w, driver, energy)
            if len(yi) < 0.9 * lb:
                continue
            cands = _rank_models(Ti, yi, ii, kinds)
            for c in cands:
                if not c.sep_valid:
                    break
                ok_b, _, _ = _covers(c.model, Tb, pol)
                ok_r, _, _ = _covers(c.model, Tr, pol)
                if ok_b and ok_r:
                    found.append((c, w))
                    break
        if windows and not found:
            reasons.append(
                f"none of {len(windows)} intermediate windows has a valid model covering both"
            )
        if found:
            found.sort(key=lambda cw: (cw[0].sep_valid, cw[0].adj_r2), reverse=True)
            c, w = found[0]
            inter = [str(w[0].date()), str(w[1].date())]
            reasons.append(
                f"intermediate {inter[0]}..{inter[1]}: {c.kind}, adj. R2 {c.adj_r2:.3f} "
                f"(best of {len(found)} covering window(s))"
            )
            models["intermediate"] = c.summary()
            valid = True
            results["chaining"] = chained_savings(
                c.model,
                Tb,
                yb,
                Tr,
                yr,
                periods={
                    "baseline": list(baseline),
                    "intermediate": inter,
                    "reporting": list(reporting),
                },
                confidence=confidence,
                rho=c.rho,
                extrapolation=pol,
            )
    steps.append({"method": "chaining", "valid": valid, "reasons": reasons})

    # 4. standard conditions
    reasons, valid = [], False
    if standard_conditions is None:
        reasons.append("no standard conditions supplied")
    elif best_b is None or best_r is None or not (best_b.sep_valid and best_r.sep_valid):
        reasons.append("needs SEP-valid baseline and reporting models")
    else:
        S = np.asarray(standard_conditions, dtype=float)
        ok_b, tb, _ = _covers(best_b.model, S, pol)
        ok_r, tr, _ = _covers(best_r.model, S, pol)
        reasons.append(f"coverage of the standard conditions: baseline {tb}, reporting {tr}")
        valid = ok_b and ok_r
        if valid:
            results["standard_conditions"] = standard_conditions_savings(
                best_b.model,
                best_r.model,
                S,
                confidence=confidence,
                rho_baseline=best_b.rho,
                rho_reporting=best_r.rho,
                extrapolation=pol,
            )
    steps.append({"method": "standard_conditions", "valid": valid, "reasons": reasons})

    proposed: str | None = next((str(s["method"]) for s in steps if s["valid"]), None)
    sensitivity = [
        {
            "method": name,
            "savings": r.savings,
            "savings_pct": _rn(r.savings_pct, 4),
            "enpi": r.enpi,
            "enpi_uncertainty": r.enpi_uncertainty,
            "abs_uncertainty": _rn(r.abs_uncertainty, 2),
            "kernel": r.kernel,
            "coverage_tier": (r.coverage or {}).get("tier"),
            "sep_range_valid": r.sep_range_valid,
            "declined": r.declined,
        }
        for name, r in results.items()
    ]
    enpis = [row["enpi"] for row in sensitivity if row["enpi"] is not None]
    if len(enpis) > 1:
        caveats.append(
            f"{len(enpis)} valid methods give SEnPI from {min(enpis):.3f} to {max(enpis):.3f}; "
            "report the declared method, and this spread alongside it"
        )
    if proposed is None:
        why = "no SEP method's conditions hold (SEP 2012 §3.6.6: another method needs approval)"
        return MethodProposal(
            proposed=None,
            reason=why,
            steps=steps,
            sensitivity=sensitivity,
            models=models,
            intermediate_period=inter,
            declined=True,
            declined_reason=why,
            caveats=caveats,
            results=results,
        )
    return MethodProposal(
        proposed=proposed,
        reason=f"{proposed}: the first method in SEP's order whose conditions hold",
        steps=steps,
        sensitivity=sensitivity,
        models=models,
        intermediate_period=inter,
        caveats=caveats,
        results=results,
    )
