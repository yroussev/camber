"""Tests for the overcooling / high-min-airflow diagnostic (PNNL Ch.7)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.overcooling import analyze_overcooling  # noqa: E402
from camber.rules.base import Rule  # noqa: E402
from camber.rules.overcooling_rule import OvercoolingMinFlow  # noqa: E402


def _idx(n):
    return pd.date_range("2025-07-07", periods=n, freq="1h")  # Monday


def test_overcooling_flagged():
    n = 24 * 21
    idx = _idx(n)
    # zone always below cooling setpoint (overcooled), airflow stuck at min,
    # damper pinned low, reheat firing -> the textbook high-min-flow fault
    df = pd.DataFrame({
        "SpaceTemp": np.full(n, 70.0),
        "ActCoolSP": np.full(n, 74.0),     # satisfied: 70 <= 74
        "ActFlow": np.full(n, 640.0),
        "ActFlowSP": np.full(n, 630.0),    # at min (640 <= 630*1.15)
        "Damper": np.full(n, 20.0),        # pinned low
        "HWValve": np.full(n, 40.0),       # reheating
    }, index=idx)
    r = analyze_overcooling(df, "VAV_1")
    assert r.satisfied_pct > 95
    assert r.overcool_at_minflow_pct > 95
    assert r.overcool_with_reheat_pct > 95


def test_not_flagged_when_flow_modulates():
    n = 24 * 21
    idx = _idx(n)
    # zone satisfied but airflow well above min (damper open, throttling normally)
    df = pd.DataFrame({
        "SpaceTemp": np.full(n, 70.0),
        "ActCoolSP": np.full(n, 74.0),
        "ActFlow": np.full(n, 1500.0),     # far above min
        "ActFlowSP": np.full(n, 630.0),
        "Damper": np.full(n, 80.0),        # wide open
        "HWValve": np.zeros(n),
    }, index=idx)
    r = analyze_overcooling(df, "VAV_2")
    assert r.overcool_at_minflow_pct < 5


def test_not_flagged_when_zone_needs_cooling():
    n = 24 * 21
    idx = _idx(n)
    # zone above cooling setpoint (genuinely calling for cooling) -> not overcooling
    df = pd.DataFrame({
        "SpaceTemp": np.full(n, 76.0),
        "ActCoolSP": np.full(n, 74.0),     # 76 > 74: not satisfied
        "ActFlow": np.full(n, 640.0),
        "ActFlowSP": np.full(n, 630.0),
        "Damper": np.full(n, 20.0),
        "HWValve": np.zeros(n),
    }, index=idx)
    r = analyze_overcooling(df, "VAV_3")
    assert r.satisfied_pct < 5
    assert r.overcool_at_minflow_pct < 5


def test_satisfied_deadband_default_is_zero():
    # Hardening fix #1: the default satisfied_deadband_f=0.0 is a true zero
    # deadband -- a zone exactly AT the cooling setpoint counts as satisfied.
    n = 24 * 21
    idx = _idx(n)
    df = pd.DataFrame({
        "SpaceTemp": np.full(n, 74.0),     # exactly at setpoint
        "ActCoolSP": np.full(n, 74.0),
        "ActFlow": np.full(n, 640.0),
        "ActFlowSP": np.full(n, 630.0),
        "Damper": np.full(n, 20.0),
        "HWValve": np.full(n, 40.0),
    }, index=idx)
    r = analyze_overcooling(df, "VAV_dead0")
    assert r.satisfied_pct > 95          # at-or-below SP is satisfied at deadband 0


def test_satisfied_deadband_tightens_conservatively():
    # A positive deadband requires the space to be genuinely BELOW the cooling SP
    # to count as satisfied -- it tightens, never loosens. A zone sitting exactly
    # at the setpoint should drop out once a 1 degF deadband is required.
    n = 24 * 21
    idx = _idx(n)
    df = pd.DataFrame({
        "SpaceTemp": np.full(n, 74.0),     # exactly at setpoint
        "ActCoolSP": np.full(n, 74.0),
        "ActFlow": np.full(n, 640.0),
        "ActFlowSP": np.full(n, 630.0),
        "Damper": np.full(n, 20.0),
        "HWValve": np.full(n, 40.0),
    }, index=idx)
    strict = analyze_overcooling(df, "VAV_dead1", satisfied_deadband_f=1.0)
    assert strict.satisfied_pct < 5       # 74 is not >=1 degF below 74
    # a zone 2 degF below the SP still satisfies a 1 degF deadband
    df2 = df.copy()
    df2["SpaceTemp"] = 72.0
    looser = analyze_overcooling(df2, "VAV_dead1b", satisfied_deadband_f=1.0)
    assert looser.satisfied_pct > 95


def test_fallback_only_when_no_heat_valve():
    # Hardening fix #2: with a HEAT_VALVE present but reheat that never overlaps
    # the overcool window, severity must score on overcool_with_reheat_pct (0%),
    # NOT silently fall back to the broader overcool_at_minflow_pct.
    rule = OvercoolingMinFlow()
    n = 24 * 21
    idx = _idx(n)
    frame = pd.DataFrame({
        Role.SPACE_TEMP: np.full(n, 70.0),
        Role.COOL_SP: np.full(n, 74.0),    # overcooled at min flow...
        Role.AIRFLOW: np.full(n, 640.0),
        Role.AIRFLOW_SP: np.full(n, 630.0),
        Role.DAMPER: np.full(n, 20.0),
        Role.HEAT_VALVE: np.zeros(n),      # ...but reheat valve never opens
    }, index=idx)
    f = rule.analyze("VAV_novalveoverlap", frame)
    assert f.metrics["overcool_at_minflow_pct"] > 95
    assert f.metrics["overcool_with_reheat_pct"] < 5
    assert f.severity == "ok"             # scored on reheat overlap, no fallback


def test_fallback_used_when_heat_valve_absent():
    # When there is genuinely no HEAT_VALVE column, severity DOES fall back to the
    # overcool-at-min-flow rate (the broader, valve-free signal).
    rule = OvercoolingMinFlow()
    n = 24 * 21
    idx = _idx(n)
    frame = pd.DataFrame({
        Role.SPACE_TEMP: np.full(n, 70.0),
        Role.COOL_SP: np.full(n, 74.0),
        Role.AIRFLOW: np.full(n, 640.0),
        Role.AIRFLOW_SP: np.full(n, 630.0),
        Role.DAMPER: np.full(n, 20.0),
    }, index=idx)                          # no HEAT_VALVE role at all
    f = rule.analyze("VAV_novalve", frame)
    assert f.metrics["overcool_with_reheat_pct"] < 5
    assert f.metrics["overcool_at_minflow_pct"] > 95
    assert f.severity == "fault"          # fallback to min-flow rate -> >=15%


def test_rule_protocol_and_severity():
    rule = OvercoolingMinFlow()
    assert isinstance(rule, Rule)
    n = 24 * 21
    idx = _idx(n)
    frame = pd.DataFrame({
        Role.SPACE_TEMP: np.full(n, 70.0),
        Role.COOL_SP: np.full(n, 74.0),
        Role.AIRFLOW: np.full(n, 640.0),
        Role.AIRFLOW_SP: np.full(n, 630.0),
        Role.DAMPER: np.full(n, 20.0),
        Role.HEAT_VALVE: np.full(n, 40.0),
    }, index=idx)
    f = rule.analyze("VAV_1", frame)
    assert f.severity == "fault"
    assert f.metrics["overcool_with_reheat_pct"] > 95
