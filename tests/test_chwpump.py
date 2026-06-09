"""Tests for the CHW pump DP-reset diagnostic (PNNL Ch.8)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.chwpump import analyze_chw_pump  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.base import Rule  # noqa: E402
from camber.rules.chwpump_rule import CHWPumpDPReset  # noqa: E402


def _idx(n):
    return pd.date_range("2025-07-07", periods=n, freq="1h")


def test_pinned_full_speed_flagged():
    n = 24 * 21
    df = pd.DataFrame({"PumpSpeed": np.full(n, 100.0),
                       "DiffPressSP": np.full(n, 14.0)}, index=_idx(n))
    r = analyze_chw_pump(df, "CHWP")
    assert r.pct_running_near_full > 95
    assert not r.dp_sp_reset_present


def test_modulating_pump_ok():
    n = 24 * 21
    idx = _idx(n)
    rng = np.random.default_rng(0)
    spd = np.clip(55 + 20 * np.sin(np.arange(n) / 12) + rng.normal(0, 3, n), 20, 85)
    sp = 10 + 3 * np.sin(np.arange(n) / 12)   # DP setpoint resets
    df = pd.DataFrame({"PumpSpeed": spd, "DiffPressSP": sp}, index=idx)
    r = analyze_chw_pump(df, "CHWP")
    assert r.pct_running_near_full < 30
    assert r.dp_sp_reset_present


def test_off_hours_excluded():
    n = 24 * 21
    spd = np.where(np.arange(n) % 2 == 0, 100.0, 0.0)  # half off
    df = pd.DataFrame({"PumpSpeed": spd}, index=_idx(n))
    r = analyze_chw_pump(df, "CHWP")
    assert r.median_speed_pct == 100.0   # running hours only
    assert r.pct_running_near_full > 95


def test_pinned_min_speed_oversized():
    n = 24 * 21
    df = pd.DataFrame({"PumpSpeed": np.full(n, 18.0)}, index=_idx(n))  # always near min
    r = analyze_chw_pump(df, "CHWP")
    assert r.pct_running_near_min > 95
    assert r.pct_running_near_full < 5


def test_rule_protocol_and_severity():
    rule = CHWPumpDPReset()
    assert isinstance(rule, Rule)
    n = 24 * 21
    frame = pd.DataFrame({Role.CHW_PUMP_SPEED: np.full(n, 100.0)}, index=_idx(n))
    f = rule.analyze("CHWP1", frame)
    assert f.severity == "fault"
    assert f.metrics["pct_running_near_full"] > 95


def test_rule_oversized_warns():
    rule = CHWPumpDPReset()
    n = 24 * 21
    frame = pd.DataFrame({Role.CHW_PUMP_SPEED: np.full(n, 18.0)}, index=_idx(n))
    f = rule.analyze("CHWP1", frame)
    assert f.severity == "warn"                       # pinned at VFD minimum -> oversized
    assert f.metrics["pct_running_near_min"] > 95
