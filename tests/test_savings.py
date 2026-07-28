"""Tests for pattern H — M&V baseline/savings viz with uncertainty (camber.charts.savings).
Rendering runs headless on Agg."""

import os
import sys

import matplotlib

matplotlib.use("Agg")  # headless, before pyplot is imported anywhere

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def _close_figs():
    yield
    plt.close("all")


from camber.charts.savings import cumulative_savings, savings_chart  # noqa: E402
from camber.mandv.models import best_model  # noqa: E402
from camber.mandv.stats import SavingsResult, fit_stats  # noqa: E402


def _baseline(seed=0):
    rng = np.random.default_rng(seed)
    Tb = rng.uniform(20, 90, 200)
    yb = 2.0 * Tb + 50 + rng.normal(0, 4, 200)
    model = best_model(Tb, yb)
    cv = fit_stats(yb, model.predict(Tb), p=2).cv_rmse
    return model, cv, rng


def _report(model, rng, factor, n=120):
    idx = pd.date_range("2025-01-01", periods=n, freq="D")
    Tr = rng.uniform(20, 90, n)
    yr = pd.Series(model.predict(Tr) * factor + rng.normal(0, 3, n), index=idx)
    return Tr, yr


def test_cumulative_savings_shapes_and_monotone_baseline():
    model, cv, rng = _baseline()
    Tr, yr = _report(model, rng, 0.85)
    idx, cum_base, cum_act, cum_avoided = cumulative_savings(model, Tr, yr)
    assert len(idx) == len(cum_base) == len(cum_act) == len(cum_avoided) == len(yr)
    assert np.all(np.diff(cum_base) >= 0)  # positive baseline energy accumulates
    assert cum_avoided[-1] > 0  # actual ran below baseline -> saved


def test_savings_chart_positive_savings():
    model, cv, rng = _baseline()
    Tr, yr = _report(model, rng, 0.85)  # ~15% savings
    ax, res = savings_chart(model, Tr, yr, n_baseline=200, p_baseline=2, cv_rmse=cv)
    assert isinstance(res, SavingsResult)
    assert res.avoided_energy > 0 and 0.12 < res.savings_pct < 0.18
    assert ax.get_lines() and ax.collections  # cumulative curves + shaded areas
    assert "M&V savings" in ax.get_title()


def test_savings_chart_excess_when_actual_above_baseline():
    model, cv, rng = _baseline()
    Tr, yr = _report(model, rng, 1.12)  # used MORE than baseline
    _, res = savings_chart(model, Tr, yr, n_baseline=200, p_baseline=2, cv_rmse=cv)
    assert res.avoided_energy < 0  # excess, not savings


def test_savings_chart_reports_uncertainty_band():
    model, cv, rng = _baseline()
    Tr, yr = _report(model, rng, 0.85)
    ax, res = savings_chart(
        model, Tr, yr, n_baseline=200, p_baseline=2, cv_rmse=cv, confidence=0.90
    )
    assert np.isfinite(res.abs_uncertainty) and res.abs_uncertainty > 0
    labels = [c.get_label() for c in ax.collections]
    assert any("band" in str(lbl) for lbl in labels)  # the ± band was drawn


def test_savings_chart_accepts_plain_array_report():
    model, cv, rng = _baseline()
    Tr = rng.uniform(20, 90, 60)
    yr = model.predict(Tr) * 0.9  # a numpy array, no index
    ax, res = savings_chart(model, Tr, yr, n_baseline=200, p_baseline=2, cv_rmse=cv)
    assert res.avoided_energy > 0 and ax.get_lines()
