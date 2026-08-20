"""Tests for supply-fan efficiency drift (camber.rules.fan_efficiency_rule)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.driftthresholds import MAGNITUDE_CONFIDENCE, TEMPORAL_CONFIDENCE  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.base import PeriodRule  # noqa: E402
from camber.rules.builtin import builtin_registry, rule_names  # noqa: E402
from camber.rules.fan_efficiency_rule import FanEfficiencyDrift  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402

_KW_PER_CFM = 0.004  # ~4 kW at 1000 cfm
_SIGMA_KW = 0.25


def _airflow(n, seed=0):
    rng = np.random.default_rng(seed)
    h = np.arange(n)
    a = 1400 + 700 * np.sin((h % 24 - 8) / 24 * 2 * np.pi) + rng.normal(0, 80, n)
    return np.clip(a, 300.0, 2200.0)


def _frame(n=24 * 30, *, start="2025-05-01", seed=0, excess_kw=0.0, static_offset=0.0, inputs=True):
    rng = np.random.default_rng(seed + 100)
    idx = pd.date_range(start, periods=n, freq="1h")
    cfm = _airflow(n, seed=seed)
    cols = {Role.AIRFLOW: cfm, Role.DUCT_STATIC: 1.2 + static_offset + rng.normal(0, 0.03, n)}
    if inputs:
        cols[Role.POWER] = _KW_PER_CFM * cfm + excess_kw + rng.normal(0, _SIGMA_KW, n)
    return pd.DataFrame(cols, index=idx)


def _rule(store, **kw):
    return FanEfficiencyDrift(store, site="SITE", run_id="2025-07-01T00:00", **kw)


def _base_and(current_kw):
    return _frame(start="2025-05-01", seed=1), _frame(start="2025-06-01", seed=2, **current_kw)


# --------------------------------------------------------------------------- interface


def test_it_is_a_period_rule_and_is_not_auto_registered():
    rule = _rule(BaselineStore())
    assert isinstance(rule, PeriodRule)
    assert "fan_efficiency_drift" not in rule_names()
    assert "fan_efficiency_drift" not in builtin_registry().names()
    assert rule.roles_required == (Role.POWER, Role.AIRFLOW)


# --------------------------------------------------------------------------- the detector


def test_excess_fan_power_flags():
    f = _rule(BaselineStore()).analyze_periods("AHU_1", *_base_and({"excess_kw": 1.5}))
    assert f.rule == "fan_efficiency_drift" and f.severity == "fault"
    assert f.metrics["fan_power_drift_kw"] > 1.0
    assert f.metrics["fan_power_drift_direction"] == "up"
    assert f.metrics["fan_power_sustained_alarm"] is True
    assert f.metrics["fan_power_alarm_direction"] == "up"
    assert f.metrics["thresholds_provisional"] is True


def test_it_is_one_sided_less_power_does_not_flag():
    f = _rule(BaselineStore()).analyze_periods("AHU_1", *_base_and({"excess_kw": -1.5}))
    assert f.severity == "ok" and f.metrics["fan_power_drift_direction"] == "down"


def test_a_steady_fan_does_not_flag():
    f = _rule(BaselineStore()).analyze_periods("AHU_1", *_base_and({}))
    assert f.severity == "ok" and abs(f.metrics["fan_power_drift_kw"]) < 0.5


def test_the_finding_labels_its_two_threshold_classes():
    f = _rule(BaselineStore()).analyze_periods("AHU_1", *_base_and({"excess_kw": 1.5}))
    assert f.metrics["magnitude_threshold_confidence"] == MAGNITUDE_CONFIDENCE
    assert f.metrics["temporal_threshold_confidence"] == TEMPORAL_CONFIDENCE


# --------------------------------------------------------------------------- the confound


def test_excess_with_rising_static_is_caveated():
    base, cur = (
        _frame(start="2025-05-01", seed=1),
        _frame(start="2025-06-01", seed=2, excess_kw=1.5, static_offset=0.5),
    )
    f = _rule(BaselineStore()).analyze_periods("AHU_1", base, cur)
    assert f.severity == "fault"
    assert f.metrics["duct_static_shift"] > 0.2
    assert any("higher static" in c for c in f.caveats)


def test_excess_with_flat_static_reads_as_efficiency_loss():
    f = _rule(BaselineStore()).analyze_periods("AHU_1", *_base_and({"excess_kw": 1.5}))
    assert f.severity == "fault"
    assert not any("higher static" in c for c in f.caveats)


# --------------------------------------------------------------------------- declines / freeze


def test_it_declines_when_power_or_airflow_is_not_mapped():
    base = _frame(start="2025-05-01", seed=1, inputs=False)
    cur = _frame(start="2025-06-01", seed=2, inputs=False)
    f = _rule(BaselineStore()).analyze_periods("AHU_1", base, cur)
    assert f.severity == "info" and f.metrics["reason"] == "power_or_airflow_not_mapped"


def test_the_baseline_is_frozen_and_not_refit():
    store = BaselineStore()
    rule = _rule(store)
    rule.analyze_periods("AHU_1", *_base_and({"excess_kw": 1.5}))
    coeffs = dict(store.get("SITE", "AHU_1", "fan_efficiency").coefficients)
    worse = _frame(start="2025-07-01", seed=5, excess_kw=2.5)
    f = rule.analyze_periods("AHU_1", worse, worse)
    assert store.get("SITE", "AHU_1", "fan_efficiency").coefficients == coeffs
    assert f.severity == "fault"
