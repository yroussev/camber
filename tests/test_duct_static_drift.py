"""Tests for duct static-pressure control drift (camber.rules.duct_static_rule).

Synthetic data: a VAV system holding duct static near setpoint (roughly flat vs airflow) plus noise,
with faults injected as static offsets and resets as setpoint offsets. Nothing is from a dataset.
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.rules.base import PeriodRule  # noqa: E402
from camber.rules.builtin import builtin_registry, rule_names  # noqa: E402
from camber.rules.duct_static_rule import DuctStaticControlDrift  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402

_SP0 = 1.5  # design duct static, inH2O
_SIGMA = 0.03


def _airflow(n, seed=0):
    rng = np.random.default_rng(seed)
    h = np.arange(n)
    a = 1400 + 700 * np.sin((h % 24 - 8) / 24 * 2 * np.pi) + rng.normal(0, 80, n)
    return np.clip(a, 300.0, 2200.0)


def _frame(n=24 * 30, *, start="2025-05-01", seed=0, static_off=0.0, sp_off=0.0, inputs=True):
    rng = np.random.default_rng(seed + 100)
    idx = pd.date_range(start, periods=n, freq="1h")
    cfm = _airflow(n, seed=seed)
    cols = {Role.AIRFLOW: cfm}
    if inputs:
        cols[Role.DUCT_STATIC] = _SP0 + sp_off + static_off + rng.normal(0, _SIGMA, n)
        cols[Role.DUCT_STATIC_SP] = np.full(n, _SP0 + sp_off)
    return pd.DataFrame(cols, index=idx)


def _rule(store, **kw):
    return DuctStaticControlDrift(store, site="SITE", run_id="2025-07-01T00:00", **kw)


def _base_and(current_kw):
    return _frame(start="2025-05-01", seed=1), _frame(start="2025-06-01", seed=2, **current_kw)


# --------------------------------------------------------------------------- interface


def test_it_is_a_period_rule_and_is_not_auto_registered():
    rule = _rule(BaselineStore())
    assert isinstance(rule, PeriodRule)
    assert "duct_static_drift" not in rule_names()
    assert "duct_static_drift" not in builtin_registry().names()
    assert rule.roles_required == (Role.DUCT_STATIC, Role.AIRFLOW)


# --------------------------------------------------------------------------- the detector


def test_rising_static_is_over_pressurization():
    f = _rule(BaselineStore()).analyze_periods("AHU_1", *_base_and({"static_off": 0.4}))
    assert f.rule == "duct_static_drift" and f.severity == "fault"
    assert f.metrics["duct_static_drift_direction"] == "up"
    assert "over-pressurization" in f.summary
    assert f.metrics["duct_static_setpoint_driven"] is False


def test_falling_static_is_the_fan_not_holding():
    f = _rule(BaselineStore()).analyze_periods("AHU_1", *_base_and({"static_off": -0.4}))
    assert f.severity == "fault" and f.metrics["duct_static_drift_direction"] == "down"
    assert "cannot hold setpoint" in f.summary


def test_a_steady_system_does_not_flag():
    f = _rule(BaselineStore()).analyze_periods("AHU_1", *_base_and({}))
    assert f.severity == "ok" and abs(f.metrics["duct_static_drift_inwc"]) < 0.15


# --------------------------------------------------------------------------- the reset confound


def test_a_reset_that_fully_explains_the_move_does_not_fault():
    """Static rose because its setpoint rose the same amount -> residual ~0 -> not a fault."""
    f = _rule(BaselineStore()).analyze_periods(
        "AHU_1", *_base_and({"sp_off": 0.3, "static_off": 0.0})
    )
    assert f.severity == "ok"
    assert f.metrics["duct_static_setpoint_driven"] is True
    assert f.metrics["static_sp_shift"] > 0.2
    assert any("no independent control fault remains" in c for c in f.caveats)


def test_a_reset_that_only_partly_explains_the_move_leaves_a_fault():
    f = _rule(BaselineStore()).analyze_periods(
        "AHU_1", *_base_and({"sp_off": 0.3, "static_off": 0.2})
    )
    assert f.severity == "warn"  # fault (raw +0.5) demoted to the residual (+0.2) tier
    assert f.metrics["duct_static_setpoint_driven"] is True
    assert any("a control fault remains after the reset" in c for c in f.caveats)


def test_a_static_drift_beyond_the_reset_is_an_independent_fault():
    f = _rule(BaselineStore()).analyze_periods(
        "AHU_1", *_base_and({"sp_off": 0.15, "static_off": 0.5})
    )
    assert f.severity == "fault"
    assert any("an independent control fault" in c for c in f.caveats)


# --------------------------------------------------------------------------- declines / freeze


def test_it_declines_when_static_or_airflow_is_not_mapped():
    base = _frame(start="2025-05-01", seed=1, inputs=False)
    cur = _frame(start="2025-06-01", seed=2, inputs=False)
    f = _rule(BaselineStore()).analyze_periods("AHU_1", base, cur)
    assert f.severity == "info" and f.metrics["reason"] == "static_or_airflow_not_mapped"


def test_the_baseline_is_frozen_and_not_refit():
    store = BaselineStore()
    rule = _rule(store)
    rule.analyze_periods("AHU_1", *_base_and({"static_off": 0.4}))
    coeffs = dict(store.get("SITE", "AHU_1", "duct_static").coefficients)
    worse = _frame(start="2025-07-01", seed=5, static_off=0.7)
    f = rule.analyze_periods("AHU_1", worse, worse)
    assert store.get("SITE", "AHU_1", "duct_static").coefficients == coeffs
    assert f.severity == "fault"
