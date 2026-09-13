"""Tests for M&V fit statistics, acceptance, and savings uncertainty."""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

from camber.mandv.models import fit_model  # noqa: E402
from camber.mandv.stats import (  # noqa: E402
    _fsu_measured,
    _n_effective,
    _rel_unc_projected,
    _t_value,
    avoided_energy_savings,
    fit_stats,
    lag1_autocorrelation,
)


def test_perfect_fit_stats():
    y = np.array([10.0, 20, 30, 40, 50])
    yhat = y.copy()
    s = fit_stats(y, yhat, p=2)
    assert s.r2 == 1.0
    assert s.cv_rmse == 0.0
    assert s.accept


def test_poor_fit_rejected():
    rng = np.random.default_rng(0)
    y = rng.normal(100, 30, 200)
    yhat = np.full(200, 100.0)  # predicts the mean: R2 ~ 0
    s = fit_stats(y, yhat, p=2)
    assert s.r2 < 0.1
    assert not s.accept
    assert "R2" in s.notes


def test_good_model_accepted():
    rng = np.random.default_rng(1)
    T = np.linspace(45, 105, 300)
    y = 20 + 0.8 * np.maximum(0.0, T - 65) + rng.normal(0, 0.5, len(T))
    m = fit_model(T, y, "3PC")
    s = fit_stats(y, m.predict(T), p=3)
    assert s.r2 > 0.9
    assert s.cv_rmse < 0.20
    assert s.accept


def test_cv_rmse_uses_dof():
    # CV(RMSE) should use (n-p) normalization; check it's computed and positive
    rng = np.random.default_rng(2)
    y = 100 + rng.normal(0, 10, 50)
    yhat = 100 + rng.normal(0, 10, 50)
    s = fit_stats(y, yhat, p=2)
    assert s.cv_rmse > 0
    assert s.rmse > 0


def test_savings_positive_when_post_below_baseline():
    rng = np.random.default_rng(3)
    # baseline: cooling model; reporting period uses 20% less at same temps
    T = np.linspace(50, 100, 300)
    y_base = 30 + 1.0 * np.maximum(0.0, T - 65) + rng.normal(0, 0.3, len(T))
    base = fit_model(T, y_base, "3PC")
    T_rep = np.linspace(50, 100, 300)
    y_rep = 0.8 * (30 + 1.0 * np.maximum(0.0, T_rep - 65))  # 20% savings
    s = avoided_energy_savings(
        base, T_rep, y_rep, cv_rmse=0.05, n_baseline=300, p_baseline=3, confidence=0.90
    )
    assert s.avoided_energy > 0
    assert 0.15 < s.savings_pct < 0.25
    assert s.fractional_uncertainty > 0  # uncertainty is quantified
    assert s.abs_uncertainty > 0


def test_uncertainty_grows_as_savings_shrink():
    # smaller savings fraction -> larger fractional uncertainty (G14 Annex-B)
    rng = np.random.default_rng(4)
    T = np.linspace(50, 100, 300)
    y_base = 30 + 1.0 * np.maximum(0.0, T - 65) + rng.normal(0, 0.3, len(T))
    base = fit_model(T, y_base, "3PC")
    big = avoided_energy_savings(
        base, T, 0.7 * base.predict(T), cv_rmse=0.1, n_baseline=300, p_baseline=3
    )
    small = avoided_energy_savings(
        base, T, 0.97 * base.predict(T), cv_rmse=0.1, n_baseline=300, p_baseline=3
    )
    assert small.fractional_uncertainty > big.fractional_uncertainty


def test_cv_rmse_threshold_by_interval():
    from camber.mandv.stats import cv_rmse_max_for

    # G14: finer resolution -> looser CV(RMSE) gate
    assert cv_rmse_max_for("monthly") < cv_rmse_max_for("daily")
    assert cv_rmse_max_for("hourly") >= 0.30
    assert cv_rmse_max_for("unknown") == 0.20  # safe middle default


def test_fit_stats_respects_custom_cv_gate():
    import numpy as np

    from camber.mandv.stats import fit_stats

    rng = np.random.default_rng(7)
    y = 100 + rng.normal(0, 18, 300)  # ~18% scatter about the mean
    yhat = np.full(300, 100.0)
    strict = fit_stats(y, yhat, p=2, cv_rmse_max=0.15)
    loose = fit_stats(y, yhat, p=2, cv_rmse_max=0.30)
    # same data, looser gate is at least as permissive on the CV(RMSE) criterion
    assert (loose.cv_rmse <= 0.30) or (not loose.accept)
    assert strict.cv_rmse == loose.cv_rmse  # the metric itself is unchanged


# --- the G14 fractional-savings-uncertainty kernel -------------------------- #
#
# These are magnitude tests on purpose. Before 0.80.0 the bracket was written
# `(n'/m)(1+2/n')` instead of `(n/n')(1+2/n)(1/m)`, which made every band a factor of sqrt(n) too
# wide (19x for a year of daily data) AND made the `rho` correction move it the wrong way. Every
# test in this file passed both before and after that fix, because they all asserted `> 0` or
# monotonicity in F. Only a pinned magnitude and a pinned direction can catch a recurrence.


def test_fsu_matches_the_published_worked_example():
    """t*1.26*CV*sqrt((n/n')(1+2/n)/m)/F for CV=2.87%, n=365, m=90, F=0.20, 90% conf."""
    got = _fsu_measured(0.0287, n_fit=365, m_report=90, savings_fraction=0.20, confidence=0.90)
    assert got == pytest.approx(0.0314, abs=5e-4)
    # the pre-0.80.0 expression gave 0.6036 -- sqrt(365) = 19.1x too wide
    assert got < 0.05


def test_fsu_widens_with_autocorrelation():
    """The direction that was inverted. Impossible to write against the old code."""
    fsu = [
        _fsu_measured(0.15, n_fit=365, m_report=90, savings_fraction=0.20, rho=r)
        for r in (0.0, 0.3, 0.5, 0.8)
    ]
    assert fsu == sorted(fsu), fsu
    assert fsu[-1] > 2 * fsu[0]  # rho=0.8 must be materially wider, not marginally


def test_fsu_tightens_as_the_reporting_period_lengthens():
    """Measuring longer must reduce uncertainty. The old bracket increased it."""
    long_ = _fsu_measured(0.15, n_fit=365, m_report=365, savings_fraction=0.20)
    short = _fsu_measured(0.15, n_fit=365, m_report=90, savings_fraction=0.20)
    assert long_ < short


def test_n_effective_and_its_degenerate_end():
    assert _n_effective(365, 0.0) == 365
    assert _n_effective(365, 0.5) == pytest.approx(365 / 3)
    assert _n_effective(365, 1.0) == 0.0  # no independent information left
    assert np.isnan(_fsu_measured(0.15, n_fit=365, m_report=90, savings_fraction=0.2, rho=1.0))


def test_projected_kernel_is_independent_of_how_many_periods():
    """NAC/Option D project onto model output: parameter error only, nothing to average down.

    Pins the boundary between the two kernels so nobody "simplifies" them back into one -- the
    measured kernel's 1/m term would be ~4x too narrow here at hourly resolution.
    """
    rel = _rel_unc_projected(0.05, n_fit=365, p_fit=2)
    assert rel == pytest.approx(0.05 * np.sqrt(2 / 365), rel=1e-9)  # CV * sqrt(p/n), no 1.26
    assert _rel_unc_projected(0.05, n_fit=365, p_fit=2, rho=0.5) > rel  # rho still widens it


def test_unknown_confidence_raises_rather_than_silently_substituting():
    """It used to fall back to 1.645, so asking for 99% quietly returned a 90% band."""
    assert _t_value(0.90) == pytest.approx(1.645)
    assert _t_value(0.90, df=7) > _t_value(0.90)  # df-aware: small samples need a bigger t
    with pytest.raises(ValueError, match="unsupported confidence"):
        _t_value(0.99)


# --- the lag-1 estimator ---------------------------------------------------- #


def _ar1(n, rho, seed=0, sigma=1.0):
    rng = np.random.default_rng(seed)
    e = np.zeros(n)
    for i in range(1, n):
        e[i] = rho * e[i - 1] + rng.normal(0, sigma)
    return e


def test_lag1_recovers_a_known_autocorrelation():
    idx = pd.date_range("2024-01-01", periods=365, freq="1D")
    assert lag1_autocorrelation(_ar1(365, 0.7), index=idx) == pytest.approx(0.7, abs=0.1)
    assert lag1_autocorrelation(_ar1(365, 0.0, seed=3), index=idx) == pytest.approx(0.0, abs=0.12)


def test_lag1_returns_none_rather_than_zero_when_it_cannot_be_estimated():
    """`0.0` would assert independence; `None` says the question wasn't answerable."""
    assert lag1_autocorrelation([1.0, 2.0]) is None  # too short
    assert lag1_autocorrelation(np.full(100, 5.0)) is None  # no variance
    assert lag1_autocorrelation(_ar1(10, 0.7)) is None  # below min_points


def test_lag1_does_not_pair_across_a_gap():
    """A 5-day hole must not make the residuals either side of it look adjacent."""
    resid = _ar1(120, 0.8)
    idx = list(pd.date_range("2024-01-01", periods=60, freq="1D"))
    idx += list(pd.date_range("2024-03-10", periods=60, freq="1D"))  # a jump
    gap_aware = lag1_autocorrelation(resid, index=pd.DatetimeIndex(idx))
    naive = lag1_autocorrelation(resid)  # no index -> assumes even spacing
    assert gap_aware is not None and naive is not None
    assert gap_aware != naive


def test_negative_autocorrelation_is_clamped():
    """A noisy negative rho would narrow the band -- the overconfident direction."""
    alternating = np.array([1.0, -1.0] * 100)
    assert lag1_autocorrelation(alternating) == 0.0


def test_fit_stats_reports_rho_only_when_it_can():
    y = 100 + _ar1(365, 0.7)
    yhat = np.full(365, 100.0)
    assert fit_stats(y, yhat, p=2).rho_lag1 is None  # no index -> honest None
    idx = pd.date_range("2024-01-01", periods=365, freq="1D")
    assert fit_stats(y, yhat, p=2, time_index=idx).rho_lag1 == pytest.approx(0.7, abs=0.1)


def test_savings_result_records_whether_the_band_was_adjusted():
    class _Flat:
        def predict(self, T):
            return np.full(len(np.asarray(T)), 100.0)

    T, y = np.linspace(40, 90, 90), np.full(90, 80.0)
    plain = avoided_energy_savings(_Flat(), T, y, cv_rmse=0.03, n_baseline=365, p_baseline=2)
    assert plain.rho is None and plain.fsu_autocorrelation_adjusted is False

    adj = avoided_energy_savings(_Flat(), T, y, cv_rmse=0.03, n_baseline=365, p_baseline=2, rho=0.5)
    assert adj.fsu_autocorrelation_adjusted is True
    assert adj.n_effective == pytest.approx(365 / 3, abs=0.1)
    assert adj.fractional_uncertainty > plain.fractional_uncertainty
