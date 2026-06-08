"""Tests for the G36 reheat-minimization compliance rule (§5.6.5)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.rules.base import Rule  # noqa: E402
from camber.rules.reheat_min_rule import ReheatMinimization  # noqa: E402


def _frame(n=24 * 21, hw=0.0, flow=600.0, flow_sp=600.0):
    idx = pd.date_range("2025-07-07", periods=n, freq="1h")  # Monday
    return pd.DataFrame({
        Role.HEAT_VALVE: np.full(n, hw),
        Role.AIRFLOW: np.full(n, flow),
        Role.AIRFLOW_SP: np.full(n, flow_sp),
    }, index=idx)


def test_rule_protocol():
    assert isinstance(ReheatMinimization(), Rule)


def test_violation_reheat_at_high_flow():
    # reheating with airflow well above minimum -> G36 violation
    f = _frame(hw=40, flow=1200, flow_sp=600)
    r = ReheatMinimization().analyze("VAV_1", f)
    assert r.metrics["reheat_above_min_pct"] > 95
    assert r.severity == "fault"


def test_compliant_reheat_at_min_flow():
    # reheating but holding minimum airflow -> compliant
    f = _frame(hw=40, flow=620, flow_sp=600)   # within 20% margin of min
    r = ReheatMinimization().analyze("VAV_2", f)
    assert r.metrics["reheat_above_min_pct"] < 5
    assert r.severity == "ok"


def test_no_reheat_is_ok():
    f = _frame(hw=0, flow=1200, flow_sp=600)   # high flow but no reheat -> not a reheat fault
    r = ReheatMinimization().analyze("VAV_3", f)
    assert r.severity == "ok"
    assert r.metrics["reheat_hours_pct"] == 0.0


def test_missing_inputs_info():
    idx = pd.date_range("2025-07-07", periods=50, freq="1h")
    f = pd.DataFrame({Role.HEAT_VALVE: np.full(50, 40.0)}, index=idx)  # no airflow
    r = ReheatMinimization().analyze("VAV_4", f)
    assert r.severity == "info"
