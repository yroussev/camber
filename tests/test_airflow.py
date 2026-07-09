"""Tests for the VAV airflow-tracking rule (camber.rules.airflow_rule) + ASO/registration."""

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


from camber.model.roles import Role  # noqa: E402
from camber.rules.airflow_rule import AirflowTracking  # noqa: E402
from camber.rules.builtin import rule_names  # noqa: E402
from camber.charts.evidence import finding_evidence  # noqa: E402
from camber.aso import recommend  # noqa: E402


def _frame(flow, sp=1000.0, n=240):
    idx = pd.date_range("2024-07-01", periods=n, freq="1h")
    flow = flow if isinstance(flow, pd.Series) else pd.Series(flow, index=idx)
    return pd.DataFrame({Role.AIRFLOW: flow, Role.AIRFLOW_SP: pd.Series(sp, index=idx)})


def test_tracking_setpoint_is_ok():
    idx = pd.date_range("2024-07-01", periods=240, freq="1h")
    flow = pd.Series(1000 + np.random.default_rng(0).normal(0, 20, 240), index=idx)
    f = AirflowTracking().analyze("VAV-1", _frame(flow))
    assert f.severity == "ok" and f.metrics["off_setpoint_pct"] < 5


def test_starved_box_faults_as_undershoot():
    f = AirflowTracking().analyze("VAV-2", _frame(600.0))       # 40% below the 1000 setpoint
    assert f.severity == "fault" and f.metrics["undershoot_pct"] > 90
    assert f.metrics["mean_abs_rel_error"] > 0.2


def test_overshoot_faults():
    f = AirflowTracking().analyze("VAV-3", _frame(1400.0))      # 40% above setpoint
    assert f.severity == "fault" and f.metrics["overshoot_pct"] > 90


def test_zero_setpoint_intervals_ignored():
    # setpoint 0 (box commanded closed) -> those intervals aren't judged, no divide-by-zero
    idx = pd.date_range("2024-07-01", periods=240, freq="1h")
    sp = pd.Series(np.where(idx.hour < 12, 1000.0, 0.0), index=idx)
    flow = pd.Series(np.where(idx.hour < 12, 1000.0, 0.0), index=idx)
    f = AirflowTracking().analyze("VAV-4", pd.DataFrame({Role.AIRFLOW: flow, Role.AIRFLOW_SP: sp}))
    assert f.severity == "ok" and f.metrics["n_active"] == int((idx.hour < 12).sum())


def test_evidence_recommendation_and_registration():
    frame = _frame(600.0)
    f = AirflowTracking().analyze("VAV-2", frame)
    ev = finding_evidence(AirflowTracking(), "VAV-2", frame)
    assert ev is not None and ev.renderer == "multitrend"
    rec = recommend(f)
    assert rec is not None and "starved" in rec.action        # undershoot -> starved lean
    assert "airflow_tracking" in rule_names()
