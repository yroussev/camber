"""Monte Carlo coverage of every M&V uncertainty kernel (#21 phase 21e, #49).

One place that says, for every savings path, which seeded Monte Carlo checks its band. Synthetic
daily change-point data (3PC, AR(1) residuals at rho in {0, 0.4, 0.8}, rho *estimated* from the
fit residuals, never given) with a known true saving; each cell counts how often the nominal-90%
band covers it. The truth is the *expected* saving (the true functions summed), so a band must
carry the model's parameter error and the measured periods' noise.

Cells already checked elsewhere are **referenced, not duplicated**: ``REFERENCED`` below names the
test for each (forecast with the exact kernel, the SEP chain, the indicator NRA band, the adjusted
forecast and adjusted SEP chain, the step detector), and a registry test fails if one is renamed or
removed. The cells tested **here** are the G14 forecast and backcast, the exact backcast and
standard conditions, G14 standard conditions, the sequential chain, a proportional static factor
on a forecast (both kernels) and a backcast adjusted for a reporting-period indicator.

What the new cells measured when written (400-600 runs per cell):

* The **exact** kernel covers 87-91% on backcast and standard conditions, gated at [0.85, 0.95]
  like every exact cell.
* The **G14** kernel *under-covers* on a year of daily data: 82-88% at nominal 90% for forecast
  and backcast. Its ``1.26 sqrt(m (1 + 2/n))`` factor falls short of the ``sqrt(2m)`` that
  parameter plus reporting noise need when ``m = n``. These cells are gated at [0.78, 0.95]: the
  gate catches a regression, and the shortfall is documented, not hidden. The BDG2 placebo in
  ``examples/bdg2/savings_benchmark.py`` measures the same kernel on real meters.
* Standard conditions with ``kernel="g14"`` (``CV sqrt(p/n)`` per model) and the **sequential
  chain** are *conservative*: 99-100% and 93-98%. A sequential chain adds its links' variances as
  independent (IPMVP B-19), but the shared middle year enters one link as measured energy and the
  next as the model's fit data, with opposite signs, so its noise partly cancels.
* An **adjusted backcast** whose reporting period holds an indicator NRA refits the reporting
  model with the indicator (joint Sigma, p + 1), as the baseline side already does, and covers
  on target with the exact kernel; gated at [0.85, 0.95]. Before that decision (#21) the band
  was the unadjusted reporting model's, fitted *through* the event, about 20x the error's
  standard deviation and covering 100%. That conservative band is kept, with a caveat, only
  when nothing can be refitted (no drivers for the summed rows, a chain link, or an indicator
  fitted on another window), and a test pins it.
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv.adjustments import (  # noqa: E402
    StaticFactorAdjustment,
    apply_adjustments,
    estimate_nre_indicator,
)
from camber.mandv.methods import (  # noqa: E402
    backcast_savings,
    forecast_savings,
    sequential_chain,
    standard_conditions_savings,
)
from camber.mandv.models import fit_model  # noqa: E402
from camber.mandv.stats import fit_stats  # noqa: E402

N = 365
IDX = {y: pd.date_range(f"{y}-01-01", periods=N, freq="D") for y in (2019, 2020, 2021)}
RHOS = [(0.0, 1), (0.4, 2), (0.8, 3)]
REPS = 500


def _ar1(rng, n, rho, sd):
    e = np.empty(n)
    innov = rng.normal(0, sd * np.sqrt(1 - rho**2), n)
    e[0] = rng.normal(0, sd)
    for i in range(1, n):
        e[i] = rho * e[i - 1] + innov[i]
    return e


def _temps(rng):
    return 60 + 20 * np.sin(2 * np.pi * (np.arange(N) - 100) / 365) + _ar1(rng, N, 0.7, 5)


def _f(T, a=50.0, b=2.0):
    return a + b * np.maximum(0, T - 65)


def _post(T):  # the measure: a lower base load and a flatter cooling slope
    return _f(T, 45.0, 1.8)


def _fit(T, y, year):
    m = fit_model(T, y, "3PC", time_index=IDX[year])
    st = fit_stats(y, m.predict(T), 3, time_index=IDX[year])
    return m, st


def _years(rng, rho):
    Tb, Tr = _temps(rng), _temps(rng)
    return Tb, _f(Tb) + _ar1(rng, N, rho, 4), Tr, _post(Tr) + _ar1(rng, N, rho, 4)


def _forecast(m, st, T, y, kernel):
    return forecast_savings(m, T, y, cv_rmse=st.cv_rmse, n_baseline=N, p_baseline=3,
                            rho=st.rho_lag1, kernel=kernel)  # fmt: skip


def _rate(one, rho, seed, reps=REPS):
    rng = np.random.default_rng(seed)
    hits = 0
    for _ in range(reps):
        est, band, true = one(rng, rho)
        hits += abs(est - true) <= band
    return hits / reps


# --------------------------------------------------------------------------- single-model cells


def _forecast_g14(rng, rho):
    Tb, yb, Tr, yr = _years(rng, rho)
    m, st = _fit(Tb, yb, 2019)
    r = _forecast(m, st, Tr, yr, "g14")
    return r.savings, r.abs_uncertainty, float((_f(Tr) - _post(Tr)).sum())


def _backcast(kernel):
    def one(rng, rho):
        Tb, yb, Tr, yr = _years(rng, rho)
        m, st = _fit(Tr, yr, 2020)
        r = backcast_savings(m, Tb, yb, cv_rmse=st.cv_rmse, n_reporting=N, p_reporting=3,
                             rho=st.rho_lag1, kernel=kernel)  # fmt: skip
        return r.savings, r.abs_uncertainty, float((_f(Tb) - _post(Tb)).sum())

    return one


def _standard_conditions(kernel):
    def one(rng, rho):
        Tb, yb, Tr, yr = _years(rng, rho)
        mb, sb = _fit(Tb, yb, 2019)
        mr, sr = _fit(Tr, yr, 2020)
        S = _temps(rng)
        kw = {"rho_baseline": sb.rho_lag1, "rho_reporting": sr.rho_lag1, "kernel": kernel}
        if kernel == "g14":
            kw.update(baseline_cv_rmse=sb.cv_rmse, n_baseline=N, p_baseline=3,
                      reporting_cv_rmse=sr.cv_rmse, n_reporting=N, p_reporting=3)  # fmt: skip
        r = standard_conditions_savings(mb, mr, S, **kw)
        return r.savings, r.abs_uncertainty, float((_f(S) - _post(S)).sum())

    return one


@pytest.mark.parametrize("rho,seed", RHOS)
def test_forecast_g14_kernel_under_covers_by_a_documented_margin(rho, seed):
    """G14 at nominal 90% covers 82-88% here (see the module docstring); gate [0.78, 0.95]."""
    rate = _rate(_forecast_g14, rho, seed)
    assert 0.78 <= rate <= 0.95, rate


@pytest.mark.parametrize("rho,seed", RHOS)
@pytest.mark.parametrize("kernel,lo", [("exact", 0.85), ("g14", 0.78)])
def test_backcast_coverage(kernel, lo, rho, seed):
    rate = _rate(_backcast(kernel), rho, seed + 10)
    assert lo <= rate <= 0.95, rate


@pytest.mark.parametrize("rho,seed", RHOS)
def test_standard_conditions_exact_coverage(rho, seed):
    rate = _rate(_standard_conditions("exact"), rho, seed + 20)
    assert 0.85 <= rate <= 0.95, rate


def test_standard_conditions_g14_is_conservative():
    """The projected ``CV sqrt(p/n)`` kernel per model: conservative, 99-100% at nominal 90%."""
    for rho, seed in RHOS:
        rate = _rate(_standard_conditions("g14"), rho, seed + 30, reps=200)
        assert rate >= 0.95, (rho, rate)


# --------------------------------------------------------------------------- chains


def _sequential(kernel):
    def one(rng, rho):
        T1, y1, T2, y2 = _years(rng, rho)
        T3 = _temps(rng)
        y3 = _f(T3, 40.0, 1.6) + _ar1(rng, N, rho, 4)
        m1, s1 = _fit(T1, y1, 2019)
        m2, s2 = _fit(T2, y2, 2020)
        r = sequential_chain([_forecast(m1, s1, T2, y2, kernel), _forecast(m2, s2, T3, y3, kernel)])
        true = (_f(T2) - _post(T2)).sum() + (_post(T3) - _f(T3, 40.0, 1.6)).sum()
        return r.savings, r.abs_uncertainty, float(true)

    return one


@pytest.mark.parametrize("kernel", ["exact", "g14"])
def test_sequential_chain_is_conservative(kernel):
    """The links' bands add as independent (B-19); the shared year's noise partly cancels
    between them, so the chain over-covers (93-98% at nominal 90%). Never under 0.90."""
    for rho, seed in RHOS:
        rate = _rate(_sequential(kernel), rho, seed + 40, reps=300)
        assert 0.90 <= rate <= 1.0, (rho, rate)


# --------------------------------------------------------------------------- adjusted results


def _static(kernel):
    sf = StaticFactorAdjustment(factor="floor_area", method="proportional", start="2020-07-01",
                                reason="wing added", baseline_value=1.0, reporting_value=1.25,
                                affected_share=0.6)  # fmt: skip
    after = np.asarray(IDX[2020] >= pd.Timestamp("2020-07-01"))
    mult = np.where(after, sf.multiplier, 1.0)

    def one(rng, rho):
        Tb, yb, Tr, yr = _years(rng, rho)
        yr = yr * mult
        m, st = _fit(Tb, yb, 2019)
        r = _forecast(m, st, Tr, yr, kernel)
        adj = apply_adjustments(r, [sf], index=IDX[2020], drivers=Tr, measured=yr, model=m)
        return adj.savings, adj.abs_uncertainty, float((mult * (_f(Tr) - _post(Tr))).sum())

    return one


@pytest.mark.parametrize("rho,seed", RHOS)
@pytest.mark.parametrize("kernel,lo", [("exact", 0.85), ("g14", 0.78)])
def test_adjusted_static_factor_coverage(kernel, lo, rho, seed):
    """A mid-year proportional static factor (share stated): the adjusted band covers the
    restated true saving like its kernel's unadjusted band does."""
    rate = _rate(_static(kernel), rho, seed + 50)
    assert lo <= rate <= 0.95, rate


def _backcast_with_event(rng, rho, *, refit=True):
    Tb, yb, Tr, yr = _years(rng, rho)
    yr = yr.copy()
    yr[200:] += 30.0  # a new load in the reporting year
    m, st = _fit(Tr, yr, 2020)
    r = backcast_savings(m, Tb, yb, cv_rmse=st.cv_rmse, n_reporting=N, p_reporting=3,
                         rho=st.rho_lag1, kernel="exact")  # fmt: skip
    nra = estimate_nre_indicator(Tr, yr, IDX[2020], start=IDX[2020][200], fit_period="reporting",
                                 model=m)  # fmt: skip
    rows = {"index": IDX[2019], "drivers": Tb, "measured": yb, "model": m} if refit else {}
    adj = apply_adjustments(r, [nra], reporting_index=IDX[2020], **rows)
    return adj, float((_f(Tb) - _post(Tb)).sum())


@pytest.mark.parametrize("rho", [0.0, 0.4])
def test_adjusted_backcast_with_a_reporting_event_refits_the_reporting_model(rho):
    """The reporting model is refitted with the indicator (joint Sigma, p + 1): the exact band is
    on target, gated at [0.85, 0.95] like every exact cell."""
    rng = np.random.default_rng(60 + int(10 * rho))
    hits, err = [], []
    for _ in range(300):
        adj, true = _backcast_with_event(rng, rho)
        assert adj.kernel == "exact" and adj.ledger[0]["reporting_model_refit"]
        err.append(adj.savings - true)
        hits.append(abs(adj.savings - true) <= adj.abs_uncertainty)
    err_a = np.asarray(err)
    assert abs(err_a.mean()) < 0.5 * err_a.std()  # unbiased
    assert 0.85 <= np.mean(hits) <= 0.95


def test_adjusted_backcast_without_the_rows_keeps_the_conservative_band():
    """Without the summed rows' drivers nothing can be refitted: the band stays the unadjusted
    reporting model's (fitted through the event, about 20x too wide), with a caveat."""
    rng = np.random.default_rng(60)
    err, band = [], []
    for _ in range(60):
        adj, true = _backcast_with_event(rng, 0.0, refit=False)
        err.append(adj.savings - true)
        band.append(adj.abs_uncertainty)
    assert any("not refitted" in c and "conservative" in c for c in adj.caveats)
    err_a, band_a = np.asarray(err), np.asarray(band)
    assert np.mean(np.abs(err_a) <= band_a) >= 0.95
    assert np.median(band_a) > 5 * 1.645 * err_a.std()  # far wider than the error needs


# --------------------------------------------------------------------------- the G14 caveat


def test_every_g14_result_carries_the_calibration_caveat():
    """G14 stays the default kernel (a maintainer decision on #21), and every result using it
    says what the cells above measured: the measured kernel under-covers, CAMBER's projected
    kernel is conservative, and kernel='exact' is on target. An exact result carries neither."""
    from camber.mandv.stats import _G14_CAVEAT, _G14_PROJECTED_CAVEAT

    rng = np.random.default_rng(99)
    Tb, yb, Tr, yr = _years(rng, 0.0)
    mb, sb = _fit(Tb, yb, 2019)
    mr, sr = _fit(Tr, yr, 2020)
    for kernel in ("g14", "exact"):
        want = kernel == "g14"
        fc = _forecast(mb, sb, Tr, yr, kernel)
        bc = backcast_savings(mr, Tb, yb, cv_rmse=sr.cv_rmse, n_reporting=N, p_reporting=3,
                              rho=sr.rho_lag1, kernel=kernel)  # fmt: skip
        assert (_G14_CAVEAT in fc.caveats) is want and (_G14_CAVEAT in bc.caveats) is want
        adj = apply_adjustments(fc, [])
        assert (_G14_CAVEAT in adj.caveats) is want
        kw = {"kernel": kernel}
        if want:
            kw.update(baseline_cv_rmse=sb.cv_rmse, n_baseline=N, p_baseline=3,
                      reporting_cv_rmse=sr.cv_rmse, n_reporting=N, p_reporting=3)  # fmt: skip
        sc = standard_conditions_savings(mb, mr, _temps(rng), **kw)
        assert (_G14_PROJECTED_CAVEAT in sc.caveats) is want
        assert _G14_CAVEAT not in sc.caveats


# --------------------------------------------------------------------------- the registry


REFERENCED = {
    "forecast, exact kernel": (
        "test_mandv_exact_kernel.py",
        "test_monte_carlo_coverage_of_the_exact_band",
    ),
    "SEP chain, exact": ("test_mandv_methods.py", "test_monte_carlo_chain_covariance"),
    "indicator NRA band": (
        "test_mandv_adjustments.py",
        "test_monte_carlo_coverage_of_the_indicator_band",
    ),
    "adjusted forecast, baseline indicator": (
        "test_mandv_adjustments.py",
        "test_monte_carlo_coverage_of_the_adjusted_saving",
    ),
    "adjusted SEP chain, reporting indicator": (
        "test_mandv_adjustments_methods.py",
        "test_monte_carlo_coverage_of_the_adjusted_sep_chain",
    ),
    "step detector, planted steps": (
        "test_mandv_steps.py",
        "test_moderate_steps_under_strong_autocorrelation",
    ),
    "step detector, false positives": ("test_mandv_steps.py", "test_no_steps_on_clean_ar1_data"),
}


def test_every_referenced_monte_carlo_test_exists():
    """Anti-rot: the cells this module references must still be tested where it says."""
    here = os.path.dirname(os.path.abspath(__file__))
    for cell, (fname, test) in REFERENCED.items():
        src = open(os.path.join(here, fname), encoding="utf-8").read()
        assert f"def {test}(" in src, f"{cell}: {fname}::{test} is gone"
