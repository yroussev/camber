"""Tests for air-filter loading drift (camber.rules.filter_loading_rule).

Synthetic data: a clean filter's DP rising mildly with airflow plus Gaussian noise, with loading
injected as an inH2O offset at matched airflow. Nothing is drawn from a measured dataset.
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.rules.base import PeriodRule  # noqa: E402
from camber.rules.builtin import builtin_registry, rule_names  # noqa: E402
from camber.rules.filter_loading_rule import FilterLoadingDrift  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402

# A clean filter: ~0.25 inH2O at 1400 cfm, growing gently with airflow; 0.03 inH2O run-to-run.
_DP0 = 0.25
_DP_PER_CFM = 0.00007
_SIGMA = 0.03


def _airflow(n, seed=0):
    rng = np.random.default_rng(seed)
    h = np.arange(n)
    a = 1400 + 700 * np.sin((h % 24 - 8) / 24 * 2 * np.pi) + rng.normal(0, 80, n)
    return np.clip(a, 300.0, 2200.0)


def _frame(n=24 * 30, *, start="2025-05-01", seed=0, loading_inwc=0.0, inputs=True):
    rng = np.random.default_rng(seed + 100)
    idx = pd.date_range(start, periods=n, freq="1h")
    cfm = _airflow(n, seed=seed)
    cols = {Role.AIRFLOW: cfm}
    if inputs:
        cols[Role.FILTER_DIFF_PRESS] = (
            _DP0 + _DP_PER_CFM * (cfm - 1400) + loading_inwc + rng.normal(0, _SIGMA, n)
        )
    return pd.DataFrame(cols, index=idx)


def _rule(store, **kw):
    return FilterLoadingDrift(store, site="SITE", run_id="2025-07-01T00:00", **kw)


def _base_and(current_kw):
    return _frame(start="2025-05-01", seed=1), _frame(start="2025-06-01", seed=2, **current_kw)


# --------------------------------------------------------------------------- interface


def test_it_is_a_period_rule_and_is_not_auto_registered():
    rule = _rule(BaselineStore())
    assert isinstance(rule, PeriodRule)
    assert "filter_loading_drift" not in rule_names()
    assert "filter_loading_drift" not in builtin_registry().names()
    assert rule.roles_required == (Role.FILTER_DIFF_PRESS, Role.AIRFLOW)


# --------------------------------------------------------------------------- the detector


def test_a_loading_filter_flags():
    f = _rule(BaselineStore()).analyze_periods("AHU_1", *_base_and({"loading_inwc": 0.4}))
    assert f.rule == "filter_loading_drift" and f.severity == "fault"
    assert f.metrics["filter_dp_drift_inwc"] > 0.3
    assert f.metrics["filter_dp_drift_direction"] == "up"
    assert f.metrics["filter_dp_sustained_alarm"] is True
    assert f.metrics["filter_dp_alarm_direction"] == "up"
    assert "loading" in f.summary


def test_a_filter_change_is_not_a_fault():
    """A DP *drop* (a fresh filter installed) is a welcome reset, not a fault."""
    f = _rule(BaselineStore()).analyze_periods("AHU_1", *_base_and({"loading_inwc": -0.2}))
    assert f.severity == "ok" and f.metrics["filter_dp_drift_direction"] == "down"
    assert f.metrics["filter_dp_sustained_alarm"] is False


def test_a_steady_filter_does_not_flag():
    f = _rule(BaselineStore()).analyze_periods("AHU_1", *_base_and({}))
    assert f.severity == "ok" and abs(f.metrics["filter_dp_drift_inwc"]) < 0.15


def test_a_busier_period_at_matched_airflow_is_not_loading():
    """Airflow normalization (the paper's point): more air must not read as a dirtier filter."""
    base = _frame(start="2025-05-01", seed=1)
    # a current period that simply runs at higher airflow (raw DP higher, but clean)
    n = 24 * 30
    rng = np.random.default_rng(9)
    idx = pd.date_range("2025-06-01", periods=n, freq="1h")
    cfm = np.clip(_airflow(n, seed=2) + 400, 300.0, 2200.0)
    busy = pd.DataFrame(
        {
            Role.AIRFLOW: cfm,
            Role.FILTER_DIFF_PRESS: _DP0 + _DP_PER_CFM * (cfm - 1400) + rng.normal(0, _SIGMA, n),
        },
        index=idx,
    )
    raw = float(busy[Role.FILTER_DIFF_PRESS].median()) - float(
        base[Role.FILTER_DIFF_PRESS].median()
    )
    assert raw > 0.02  # a level-vs-level comparison would see a rise
    f = _rule(BaselineStore()).analyze_periods("AHU_1", base, busy)
    assert f.severity == "ok"
    assert abs(f.metrics["filter_dp_drift_inwc"]) < 0.15


# --------------------------------------------------------------------------- declines / freeze


def test_it_declines_when_inputs_are_not_mapped():
    base = _frame(start="2025-05-01", seed=1, inputs=False)
    cur = _frame(start="2025-06-01", seed=2, inputs=False)
    f = _rule(BaselineStore()).analyze_periods("AHU_1", base, cur)
    assert f.severity == "info" and f.metrics["reason"] == "filter_dp_or_airflow_not_mapped"


def test_the_clean_baseline_is_frozen_and_not_refit():
    store = BaselineStore()
    rule = _rule(store)
    rule.analyze_periods("AHU_1", *_base_and({"loading_inwc": 0.4}))
    coeffs = dict(store.get("SITE", "AHU_1", "filter_loading").coefficients)
    worse = _frame(start="2025-07-01", seed=5, loading_inwc=0.7)
    f = rule.analyze_periods("AHU_1", worse, worse)
    assert store.get("SITE", "AHU_1", "filter_loading").coefficients == coeffs
    assert f.severity == "fault"
