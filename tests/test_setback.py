"""Tests for the AHU night/weekend setback diagnostic (PNNL Ch.5)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.rules.base import Rule  # noqa: E402
from camber.rules.setback_rule import NightWeekendSetback  # noqa: E402
from camber.setback import analyze_setback  # noqa: E402


def _two_weeks():
    # hourly index spanning 14 days so both weekday/weekend + day/night appear
    return pd.date_range("2025-07-07", periods=24 * 14, freq="1h")  # Monday start


def test_no_setback_fan_runs_always():
    idx = _two_weeks()
    df = pd.DataFrame({"SupplyFanStatus": np.ones(len(idx))}, index=idx)  # 24/7
    r = analyze_setback(df, "AHU_1")
    assert r.fan_run_unoccupied_pct > 95
    assert not r.setback_effective


def test_good_setback_fan_off_unoccupied():
    idx = _two_weeks()
    hour = idx.hour
    occ = (idx.dayofweek < 5) & (hour >= 7) & (hour < 18)
    df = pd.DataFrame({"SupplyFanStatus": np.where(occ, 1.0, 0.0)}, index=idx)
    r = analyze_setback(df, "AHU_2")
    assert r.fan_run_unoccupied_pct < 5
    assert r.fan_run_occupied_pct > 95
    assert r.setback_effective


def test_speed_fallback_when_no_status():
    idx = _two_weeks()
    df = pd.DataFrame({"SupplyFanSpeed": np.full(len(idx), 60.0)}, index=idx)  # always on
    r = analyze_setback(df, "AHU_3")
    assert r.fan_run_unoccupied_pct > 95
    assert not r.setback_effective


def test_rule_protocol_and_severity():
    rule = NightWeekendSetback()
    assert isinstance(rule, Rule)
    idx = _two_weeks()
    frame = pd.DataFrame({Role.SUPPLY_FAN_STATUS: np.ones(len(idx))}, index=idx)
    f = rule.analyze("AHU_1", frame)
    assert f.severity == "fault"
    assert f.metrics["fan_run_unoccupied_pct"] > 95


def test_rule_ok_when_setback_effective():
    rule = NightWeekendSetback()
    idx = _two_weeks()
    hour = idx.hour
    occ = (idx.dayofweek < 5) & (hour >= 7) & (hour < 18)
    frame = pd.DataFrame({Role.SUPPLY_FAN_STATUS: np.where(occ, 1.0, 0.0)}, index=idx)
    f = rule.analyze("AHU_2", frame)
    assert f.severity == "ok"
