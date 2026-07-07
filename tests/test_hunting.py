"""Tests for the control-hunting rule + cohort/hunting registration in the builtin registry."""

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
from camber.rules.hunting_rule import ControlHunting, reversals_per_hour  # noqa: E402
from camber.rules.builtin import builtin_registry, rule_names  # noqa: E402
from camber.charts.evidence import finding_evidence  # noqa: E402


def _series(vals, start="2024-07-01", freq="2min"):
    return pd.Series(vals, index=pd.date_range(start, periods=len(vals), freq=freq))


def test_reversals_per_hour_counts_direction_changes():
    # alternating every 2-min sample -> a reversal each step -> ~30/hr
    alt = _series([0.2, 0.8] * 60)
    rate, n = reversals_per_hour(alt, deadband=0.05)
    assert rate > 20 and n == 118          # 120 samples -> 119 diffs -> 118 sign-change comparisons
    # a monotone ramp has no reversals
    rate2, n2 = reversals_per_hour(_series(np.linspace(0, 1, 100)), deadband=0.01)
    assert rate2 == 0.0 and n2 == 0


def test_hunting_valve_faults_stable_is_ok():
    rule = ControlHunting()
    hunt = ControlHunting().analyze("VAV-1", pd.DataFrame({Role.COOL_VALVE: _series([0.2, 0.8] * 60)}))
    stable = rule.analyze("VAV-2", pd.DataFrame({Role.COOL_VALVE: _series(np.linspace(0.2, 0.5, 120))}))
    assert hunt.severity == "fault" and hunt.metrics["reversals_per_hr"] >= 12
    assert hunt.metrics["worst_signal"] == Role.COOL_VALVE.value
    assert stable.severity == "ok"


def test_hunting_info_when_no_modulating_output():
    rule = ControlHunting()
    f = rule.analyze("VAV-3", pd.DataFrame({Role.OAT: _series([70.0] * 50)}))
    assert f.severity == "info"


def test_hunting_picks_the_worst_of_several_signals():
    rule = ControlHunting()
    frame = pd.DataFrame({
        Role.HEAT_VALVE: _series(np.linspace(0, 0.3, 120)),      # calm
        Role.COOL_VALVE: _series([0.1, 0.9] * 60),               # hunting
    })
    f = rule.analyze("VAV-4", frame)
    assert f.metrics["worst_signal"] == Role.COOL_VALVE.value and f.severity == "fault"


def test_hunting_evidence_hook():
    rule = ControlHunting()
    frame = pd.DataFrame({Role.COOL_VALVE: _series([0.2, 0.8] * 60)})
    ev = finding_evidence(rule, "VAV-1", frame)
    assert ev is not None and ev.renderer == "multitrend"


def test_new_rules_registered_in_builtin():
    names = set(rule_names())
    assert {"control_hunting", "cohort_airflow", "cohort_space_temp"} <= names
    reg = builtin_registry()
    assert reg.get("control_hunting").name == "control_hunting"
    assert reg.get("cohort_airflow").role == Role.AIRFLOW      # a ready-made cohort fleet rule
