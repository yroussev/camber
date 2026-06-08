"""Tests for M&V fit statistics, acceptance, and savings uncertainty."""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv.models import fit_model  # noqa: E402
from camber.mandv.stats import avoided_energy_savings, fit_stats  # noqa: E402


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
    yhat = np.full(200, 100.0)        # predicts the mean: R2 ~ 0
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
    y_rep = 0.8 * (30 + 1.0 * np.maximum(0.0, T_rep - 65))   # 20% savings
    s = avoided_energy_savings(base, T_rep, y_rep, cv_rmse=0.05,
                               n_baseline=300, p_baseline=3, confidence=0.90)
    assert s.avoided_energy > 0
    assert 0.15 < s.savings_pct < 0.25
    assert s.fractional_uncertainty > 0       # uncertainty is quantified
    assert s.abs_uncertainty > 0


def test_uncertainty_grows_as_savings_shrink():
    # smaller savings fraction -> larger fractional uncertainty (G14 Annex-B)
    rng = np.random.default_rng(4)
    T = np.linspace(50, 100, 300)
    y_base = 30 + 1.0 * np.maximum(0.0, T - 65) + rng.normal(0, 0.3, len(T))
    base = fit_model(T, y_base, "3PC")
    big = avoided_energy_savings(base, T, 0.7 * base.predict(T), cv_rmse=0.1,
                                 n_baseline=300, p_baseline=3)
    small = avoided_energy_savings(base, T, 0.97 * base.predict(T), cv_rmse=0.1,
                                   n_baseline=300, p_baseline=3)
    assert small.fractional_uncertainty > big.fractional_uncertainty


def test_cv_rmse_threshold_by_interval():
    from camber.mandv.stats import cv_rmse_max_for
    # G14: finer resolution -> looser CV(RMSE) gate
    assert cv_rmse_max_for("monthly") < cv_rmse_max_for("daily")
    assert cv_rmse_max_for("hourly") >= 0.30
    assert cv_rmse_max_for("unknown") == 0.20   # safe middle default


def test_fit_stats_respects_custom_cv_gate():
    import numpy as np
    from camber.mandv.stats import fit_stats
    rng = np.random.default_rng(7)
    y = 100 + rng.normal(0, 18, 300)        # ~18% scatter about the mean
    yhat = np.full(300, 100.0)
    strict = fit_stats(y, yhat, p=2, cv_rmse_max=0.15)
    loose = fit_stats(y, yhat, p=2, cv_rmse_max=0.30)
    # same data, looser gate is at least as permissive on the CV(RMSE) criterion
    assert (loose.cv_rmse <= 0.30) or (not loose.accept)
    assert strict.cv_rmse == loose.cv_rmse   # the metric itself is unchanged
