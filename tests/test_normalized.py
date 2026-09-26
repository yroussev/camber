"""Tests for weather-normalized annual savings (camber.mandv.normalized)."""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv.coverage import ExtrapolationPolicy  # noqa: E402
from camber.mandv.models import best_model  # noqa: E402
from camber.mandv.normalized import (  # noqa: E402
    NormalizedSavings,
    normalized_annual_consumption,
    normalized_savings,
)


def _cooling(T, base=50.0, slope=2.0, cp=65.0):
    return base + slope * np.maximum(0.0, T - cp)


# 12 monthly mean temps for a representative (normal) year, cooling-dominated
_TEMPS = np.array([55, 58, 63, 70, 78, 88, 95, 93, 86, 75, 63, 56], dtype=float)


def test_nac_sums_predictions():
    y = _cooling(_TEMPS)
    m = best_model(_TEMPS, y)
    nac = normalized_annual_consumption(m, _TEMPS)
    assert abs(nac - float(y.sum())) < 5.0  # model reproduces the clean signal


def test_normalized_savings_positive():
    # reporting period uses 15% less than baseline at the same temperatures
    yb = _cooling(_TEMPS)
    yr = 0.85 * yb
    mb, mr = best_model(_TEMPS, yb), best_model(_TEMPS, yr)
    r = normalized_savings(mb, mr, _TEMPS, baseline_cv_rmse=0.03, n_baseline=12)
    assert isinstance(r, NormalizedSavings)
    assert r.normalized_savings > 0
    assert abs(r.savings_pct - 0.15) < 0.02  # ~15% normalized saving
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
    # each model is 6F off the normal year at one end: 3 of 12 normal months lie outside the
    # hot-year fit, which the coverage guard (issue #20) grades severe and declines by default
    flagged = normalized_savings(mb, mr, _TEMPS, baseline_cv_rmse=0.03, n_baseline=12)
    assert flagged.declined and flagged.coverage_baseline["tier"] == "severe"
    r = normalized_savings(
        mb,
        mr,
        _TEMPS,
        baseline_cv_rmse=0.03,
        n_baseline=12,
        extrapolation=ExtrapolationPolicy(decline=False),
    )
    assert abs(r.savings_pct) < 0.05  # no real change once weather-normalized


# --------------------------------------------------------------------------- #21 (21a) fixes


def _two_models(n=200, seed=3):
    from camber.mandv.models import fit_model

    rng = np.random.default_rng(seed)
    Tb, Tr = rng.uniform(50, 100, n), rng.uniform(50, 100, n)
    mb = fit_model(Tb, 50 + 2 * np.maximum(0, Tb - 65) + rng.normal(0, 2, n), "3PC")
    mr = fit_model(Tr, 45 + 1.6 * np.maximum(0, Tr - 65) + rng.normal(0, 2, n), "3PC")
    return mb, mr


_TEMPS = np.array([55, 58, 63, 70, 78, 88, 95, 93, 86, 75, 63, 56], dtype=float)


def test_the_band_uses_t_on_the_smaller_fit_dof():
    """A 12-point monthly fit (df = 9) needs t = 1.812 at 90%, not the large-sample 1.645."""
    from camber.mandv.stats import _rel_unc_projected

    mb, mr = _two_models()
    kw = dict(baseline_cv_rmse=0.05, reporting_cv_rmse=0.06, p_baseline=3, p_reporting=3)
    big = normalized_savings(mb, mr, _TEMPS, n_baseline=1000, n_reporting=1000, **kw)
    small = normalized_savings(mb, mr, _TEMPS, n_baseline=1000, n_reporting=12, **kw)
    rb = _rel_unc_projected(0.05, n_fit=1000, p_fit=3) * big.nac_baseline
    rr = _rel_unc_projected(0.06, n_fit=12, p_fit=3) * big.nac_reporting
    assert small.abs_uncertainty == pytest.approx(1.812 * np.hypot(rb, rr), abs=0.01)
    rr_big = _rel_unc_projected(0.06, n_fit=1000, p_fit=3) * big.nac_reporting
    assert big.abs_uncertainty == pytest.approx(1.645 * np.hypot(rb, rr_big), abs=0.01)


def test_rho_is_per_model():
    from camber.mandv.stats import _rel_unc_projected

    mb, mr = _two_models()
    kw = dict(baseline_cv_rmse=0.05, n_baseline=200, reporting_cv_rmse=0.06, n_reporting=200,
              p_baseline=3, p_reporting=3)  # fmt: skip
    both = normalized_savings(mb, mr, _TEMPS, rho=0.2, rho_reporting=0.6, **kw)
    rb = _rel_unc_projected(0.05, n_fit=200, p_fit=3, rho=0.2) * both.nac_baseline
    rr = _rel_unc_projected(0.06, n_fit=200, p_fit=3, rho=0.6) * both.nac_reporting
    assert both.abs_uncertainty == pytest.approx(1.645 * np.hypot(rb, rr), abs=0.01)
    assert not any("substituted" in c for c in both.caveats)
    # only the baseline's rho known: it is substituted, and the substitution is disclosed
    sub = normalized_savings(mb, mr, _TEMPS, rho=0.2, **kw)
    assert any("baseline's (rho=0.20) is substituted" in c for c in sub.caveats)
    assert sub.abs_uncertainty < both.abs_uncertainty
    # none known: unadjusted, as before
    none = normalized_savings(mb, mr, _TEMPS, **kw)
    assert none.abs_uncertainty < sub.abs_uncertainty and not none.caveats


def test_rho_falls_back_to_each_fit_record():
    import pandas as pd

    from camber.mandv.models import fit_model

    rng = np.random.default_rng(5)
    n = 365
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    e = np.zeros(n)
    for i in range(1, n):
        e[i] = 0.7 * e[i - 1] + rng.normal(0, 2)
    T = 75 + 15 * np.sin(np.arange(n) / 58.0)
    mb = fit_model(T, 50 + 2 * np.maximum(0, T - 65) + e, "3PC", time_index=idx)
    mr = fit_model(T, 45 + 1.6 * np.maximum(0, T - 65) + rng.normal(0, 2, n), "3PC",
                   time_index=idx)  # fmt: skip
    assert mb._fit_record.rho > 0.5 > (mr._fit_record.rho or 0.0)
    kw = dict(baseline_cv_rmse=0.05, n_baseline=n, p_baseline=3, p_reporting=3)
    rec = normalized_savings(mb, mr, _TEMPS, **kw)
    exp = normalized_savings(mb, mr, _TEMPS, rho=mb._fit_record.rho,
                             rho_reporting=mr._fit_record.rho or 0.0, **kw)  # fmt: skip
    assert rec.abs_uncertainty == exp.abs_uncertainty
