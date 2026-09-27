"""Any confidence level in _t_value, and the monthly coverage threshold (#55)."""

import numpy as np
import pandas as pd
import pytest

from camber.mandv.coverage import ExtrapolationPolicy, assess_coverage
from camber.mandv.methods import forecast_savings
from camber.mandv.models import fit_model
from camber.mandv.stats import _t_two_sided_p, _t_value, avoided_energy_savings


@pytest.mark.parametrize(
    "conf,df,want",
    [
        (0.68, None, 0.99446),  # normal: z_0.84
        (0.68, 10, 1.04642),
        (0.99, 5, 4.03214),
        (0.975, 30, 2.35956),
        (0.50, 3, 0.76489),
    ],
)
def test_t_quantile_matches_reference_values(conf, df, want):
    assert _t_value(conf, df) == pytest.approx(want, abs=2e-5)
    if df:
        assert _t_two_sided_p(_t_value(conf, df), df) == pytest.approx(1 - conf, abs=1e-9)


def test_the_table_stays_the_fast_path():
    assert _t_value(0.90, 30) == 1.697 and _t_value(0.95) == 1.960 and _t_value(0.80, 5) == 1.476


def test_g14_reporting_criterion_at_68_percent_can_be_evaluated():
    T = np.linspace(10, 80, 36)
    y = 300 + 25 * np.clip(60 - T, 0, None) + np.random.default_rng(3).normal(0, 30, 36)
    m = fit_model(T, y, "3PH")
    kw = dict(cv_rmse=0.1, n_baseline=36, p_baseline=3)
    r68 = avoided_energy_savings(m, T[:12], 0.9 * y[:12], confidence=0.68, **kw)
    r90 = avoided_energy_savings(m, T[:12], 0.9 * y[:12], confidence=0.90, **kw)
    assert r68.confidence == 0.68
    assert 0 < r68.fractional_uncertainty < r90.fractional_uncertainty


def _monthly_year(seed=0):
    idx = pd.date_range("2018-01-01", periods=12, freq="MS")
    T = 50 + 25 * np.sin(2 * np.pi * (np.arange(12) - 3.5) / 12)
    y = 60 + 40 * np.clip(60 - T, 0, None) + np.random.default_rng(seed).normal(0, 20, 12)
    return idx, T, y


def test_one_very_cold_bill_is_not_severe_on_its_share_alone():
    idx, T, y = _monthly_year()
    m = fit_model(T, y, "3PH", time_index=idx)
    Tr = T.copy()
    Tr[0] = T.min() - 20.0  # one very cold January, outside the support
    cov = assess_coverage(m, Tr, projected=m.predict(Tr))
    assert cov.n_outside == 1 and cov.share_energy_outside >= 0.25
    assert cov.tier == "moderate"
    assert any("min_points_outside=2" in c for c in cov.caveats)
    # the pre-0.90.1 grading is one policy key away
    old = assess_coverage(
        m, Tr, projected=m.predict(Tr), policy=ExtrapolationPolicy(min_points_outside=1)
    )
    assert old.tier == "severe"
    fs = forecast_savings(m, Tr, 0.9 * y, cv_rmse=0.1, n_baseline=12, p_baseline=3)
    assert not fs.declined
    # two cold bills are still severe
    Tr[1] = T.min() - 20.0
    assert assess_coverage(m, Tr, projected=m.predict(Tr)).tier == "severe"


def test_distance_still_makes_one_point_severe():
    idx, T, y = _monthly_year()
    m = fit_model(T, y, "3PH", time_index=idx)
    Tr = T.copy()
    Tr[0] = T.min() - 0.8 * (T.max() - T.min())  # far beyond the fitted range
    assert assess_coverage(m, Tr, projected=m.predict(Tr)).tier == "severe"


def test_policy_rejects_a_zero_count():
    with pytest.raises(ValueError, match="min_points_outside"):
        ExtrapolationPolicy(min_points_outside=0)
