"""SEP backcast savings (#21 phase 21a): S = O_b - P_r|b, judged against the reporting model."""

import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv.coverage import ExtrapolationPolicy  # noqa: E402
from camber.mandv.methods import MethodResult, backcast_savings  # noqa: E402
from camber.mandv.models import N_PARAMS, fit_model  # noqa: E402
from camber.mandv.stats import avoided_energy_savings, fit_stats  # noqa: E402


def _truth(T):
    return 40 + 1.5 * np.maximum(0, 50 - T) + 2.0 * np.maximum(0, T - 70)


def _periods(b=(20, 95), r=(20, 95), saving=0.2, seed=0, n=365):
    rng = np.random.default_rng(seed)
    Tb, Tr = rng.uniform(*b, n), rng.uniform(*r, n)
    yb = _truth(Tb) + rng.normal(0, 2, n)
    yr = (1 - saving) * _truth(Tr) + rng.normal(0, 2, n)
    return Tb, yb, Tr, yr


def _backcast(Tb, yb, Tr, yr, kind="5P", **kw):
    mr = fit_model(Tr, yr, kind)
    st = fit_stats(yr, mr.predict(Tr), N_PARAMS[kind])
    return mr, backcast_savings(
        mr, Tb, yb, cv_rmse=st.cv_rmse, n_reporting=st.n, p_reporting=N_PARAMS[kind], **kw
    )


def test_backcast_recovers_a_known_saving():
    Tb, yb, Tr, yr = _periods()
    true = 0.2 * _truth(Tb).sum()  # the saving at baseline-period conditions
    _, res = _backcast(Tb, yb, Tr, yr)
    assert isinstance(res, MethodResult)
    assert res.method == "backcast" and res.basis == "baseline-period conditions"
    assert res.kernel == "g14" and not res.declined
    assert res.savings == pytest.approx(res.measured - res.projected, abs=0.02)
    assert res.savings == pytest.approx(true, rel=0.05)
    assert 0 < res.abs_uncertainty < 0.05 * res.savings
    assert res.savings_pct == pytest.approx(0.2, abs=0.01)
    assert res.coverage["tier"] == "in_range" and res.df == 365 - 5
    json.dumps(res.as_dict(), allow_nan=False)


def test_exact_kernel_backcast():
    Tb, yb, Tr, yr = _periods(seed=2)
    mr = fit_model(Tr, yr, "5P", time_index=pd.date_range("2024-01-01", periods=365, freq="D"))
    res = backcast_savings(mr, Tb, yb, cv_rmse=0.0, n_reporting=365, p_reporting=5, kernel="exact")
    true = 0.2 * _truth(Tb).sum()
    assert res.kernel == "exact" and res.fsu_extrapolation_factor == 1.0
    assert abs(res.savings - true) <= res.abs_uncertainty
    assert res.rho == pytest.approx(mr._fit_record.rho, abs=1e-4)


def test_coverage_is_the_reporting_models():
    """A mild-season baseline against a full-year reporting period: the forecast extrapolates
    (severe) but the backcast does not -- the reporting model has seen the baseline's weather."""
    Tb, yb, Tr, yr = _periods(b=(55, 70), r=(20, 95))
    mb = fit_model(Tb, yb, "5P")
    fwd = avoided_energy_savings(mb, Tr, yr, cv_rmse=0.05, n_baseline=365, p_baseline=5)
    assert fwd.declined and fwd.coverage["tier"] == "severe"
    _, back = _backcast(Tb, yb, Tr, yr)
    assert back.coverage["tier"] == "in_range" and not back.declined
    assert back.coverage["variables"][0]["fit_min"] == pytest.approx(Tr.min(), abs=1e-3)


def test_backcast_outside_the_reporting_support_declines():
    Tb, yb, Tr, yr = _periods(b=(20, 95), r=(60, 80))
    _, res = _backcast(Tb, yb, Tr, yr, kind="3PC")
    assert res.declined and res.savings is None and res.projected is None
    assert res.measured > 0 and "SEVERE" in res.declined_reason
    _, kept = _backcast(
        Tb, yb, Tr, yr, kind="3PC", extrapolation=ExtrapolationPolicy(decline=False)
    )
    assert not kept.declined and kept.savings is not None


def test_moderate_backcast_widens_the_g14_band():
    Tb, yb, Tr, yr = _periods(b=(20, 95), r=(24, 95), seed=4)
    pol = ExtrapolationPolicy(caveat_share=0.0, caveat_distance=0.0)
    _, res = _backcast(Tb, yb, Tr, yr, extrapolation=pol)
    assert res.coverage["tier"] == "moderate"
    assert res.fsu_extrapolation_factor is not None and res.fsu_extrapolation_factor >= 1.0
    assert any("FSU" in c for c in res.caveats)


def test_rows_without_a_projection_are_counted():
    Tb, yb, Tr, yr = _periods(seed=6)
    yb = yb.copy()
    yb[:5] = np.nan
    _, res = _backcast(Tb, yb, Tr, yr)
    assert res.coverage["n_used"] == 360 and res.coverage["n_report"] == 365
    assert any("5 of 365 baseline rows" in c for c in res.caveats)
    with pytest.raises(ValueError, match="kernel"):
        _backcast(Tb, yb, Tr, yr, kernel="bogus")
