"""Tests for weather-normalized annual savings (camber.mandv.normalized)."""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv.models import best_model  # noqa: E402
from camber.mandv.normalized import (  # noqa: E402
    NormalizedSavings, normalized_annual_consumption, normalized_savings,
)


def _cooling(T, base=50.0, slope=2.0, cp=65.0):
    return base + slope * np.maximum(0.0, T - cp)


# 12 monthly mean temps for a representative (normal) year, cooling-dominated
_TEMPS = np.array([55, 58, 63, 70, 78, 88, 95, 93, 86, 75, 63, 56], dtype=float)


def test_nac_sums_predictions():
    y = _cooling(_TEMPS)
    m = best_model(_TEMPS, y)
    nac = normalized_annual_consumption(m, _TEMPS)
    assert abs(nac - float(y.sum())) < 5.0          # model reproduces the clean signal


def test_normalized_savings_positive():
    # reporting period uses 15% less than baseline at the same temperatures
    yb = _cooling(_TEMPS)
    yr = 0.85 * yb
    mb, mr = best_model(_TEMPS, yb), best_model(_TEMPS, yr)
    r = normalized_savings(mb, mr, _TEMPS, baseline_cv_rmse=0.03, n_baseline=12)
    assert isinstance(r, NormalizedSavings)
    assert r.normalized_savings > 0
    assert abs(r.savings_pct - 0.15) < 0.02         # ~15% normalized saving
    assert r.abs_uncertainty > 0 and 0 < r.fractional_uncertainty < 1.0
    assert r.n_normal_periods == 12


def test_no_change_near_zero_savings():
    y = _cooling(_TEMPS)
    m = best_model(_TEMPS, y)
    r = normalized_savings(m, m, _TEMPS, baseline_cv_rmse=0.05, n_baseline=12)
    assert abs(r.normalized_savings) < 1e-6
    assert abs(r.savings_pct) < 1e-6


def test_higher_cvrmse_widens_band():
    yb = _cooling(_TEMPS)
    mb, mr = best_model(_TEMPS, yb), best_model(_TEMPS, 0.8 * yb)
    tight = normalized_savings(mb, mr, _TEMPS, baseline_cv_rmse=0.03, n_baseline=12)
    loose = normalized_savings(mb, mr, _TEMPS, baseline_cv_rmse=0.15, n_baseline=12)
    assert loose.fractional_uncertainty > tight.fractional_uncertainty


def test_normalizes_out_weather():
    # baseline fit on a HOT year, reporting fit on a MILD year, same underlying model.
    # normalized to a common year, the saving should be ~0 (weather removed).
    hot = _TEMPS + 6.0
    mild = _TEMPS - 6.0
    mb = best_model(hot, _cooling(hot))
    mr = best_model(mild, _cooling(mild))
    r = normalized_savings(mb, mr, _TEMPS, baseline_cv_rmse=0.03, n_baseline=12)
    assert abs(r.savings_pct) < 0.05                # no real change once weather-normalized
