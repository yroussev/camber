"""Tests for the leaking coil-valve diagnostic (PNNL Ch.5/Ch.7)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.leakvalve import analyze_leak_valves  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.base import Rule  # noqa: E402
from camber.rules.leakvalve_rule import LeakingValve  # noqa: E402


def _idx(n):
    return pd.date_range("2025-07-07", periods=n, freq="1h")


def test_chw_leak_detected():
    n = 24 * 21
    idx = _idx(n)
    # both valves shut, but supply air 7F BELOW mixed -> cooling coil leaking
    df = pd.DataFrame({
        "CHW_Valve": np.zeros(n), "HHW_Valve": np.zeros(n),
        "MixedAir": np.full(n, 75.0), "SupplyAir": np.full(n, 68.0),
    }, index=idx)
    r = analyze_leak_valves(df, "AHU_1")
    assert r.chw_leak_pct > 95
    assert r.hw_leak_pct < 5


def test_hw_leak_detected():
    n = 24 * 21
    idx = _idx(n)
    # both valves shut, supply air 7F ABOVE mixed -> heating coil leaking
    df = pd.DataFrame({
        "CHW_Valve": np.zeros(n), "HHW_Valve": np.zeros(n),
        "MixedAir": np.full(n, 70.0), "SupplyAir": np.full(n, 77.0),
    }, index=idx)
    r = analyze_leak_valves(df, "AHU_2")
    assert r.hw_leak_pct > 95
    assert r.chw_leak_pct < 5


def test_no_leak_when_sat_tracks_mat():
    n = 24 * 21
    idx = _idx(n)
    # both shut, supply ~ mixed + fan heat -> no leak
    df = pd.DataFrame({
        "CHW_Valve": np.zeros(n), "HHW_Valve": np.zeros(n),
        "MixedAir": np.full(n, 72.0), "SupplyAir": np.full(n, 73.0),
    }, index=idx)
    r = analyze_leak_valves(df, "AHU_3")
    assert r.hw_leak_pct < 5
    assert r.chw_leak_pct < 5


def test_open_valve_hours_excluded():
    n = 24 * 21
    idx = _idx(n)
    # CHW valve open (commanded cooling) -> not a "both closed" hour, excluded
    df = pd.DataFrame({
        "CHW_Valve": np.full(n, 80.0), "HHW_Valve": np.zeros(n),
        "MixedAir": np.full(n, 75.0), "SupplyAir": np.full(n, 55.0),
    }, index=idx)
    r = analyze_leak_valves(df, "AHU_4")
    assert r is None   # no both-closed hours


def test_rule_protocol_and_severity():
    rule = LeakingValve()
    assert isinstance(rule, Rule)
    n = 24 * 21
    idx = _idx(n)
    frame = pd.DataFrame({
        Role.COOL_VALVE: np.zeros(n), Role.HEAT_VALVE: np.zeros(n),
        Role.MIXED_AIR_TEMP: np.full(n, 75.0), Role.SUPPLY_AIR_TEMP: np.full(n, 68.0),
    }, index=idx)
    f = rule.analyze("AHU_1", frame)
    assert f.severity == "fault"
    assert f.metrics["chw_leak_pct"] > 95
