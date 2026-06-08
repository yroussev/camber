"""Tests for the chilled-water plant diagnostic (CHWST reset + low-deltaT)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.chwplant import analyze_chw_plant  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.base import Rule  # noqa: E402
from camber.rules.chwplant_rule import CHWPlantReset  # noqa: E402


def _idx(n):
    return pd.date_range("2025-07-07", periods=n, freq="1h")  # Monday


def test_low_deltaT_flagged():
    n = 24 * 21
    idx = _idx(n)
    # plant running (CHWST ~44F), but loop deltaT only ~3F -> low-deltaT syndrome
    df = pd.DataFrame({
        "CHWS_Temp": np.full(n, 44.0),
        "CHWR_Temp": np.full(n, 47.0),
        "OAT": np.full(n, 90.0),
    }, index=idx)
    r = analyze_chw_plant(df, "CHW")
    assert r.deltaT_median_f == 3.0
    assert r.low_deltaT_pct > 95


def test_healthy_deltaT_not_flagged():
    n = 24 * 21
    idx = _idx(n)
    df = pd.DataFrame({
        "CHWS_Temp": np.full(n, 44.0),
        "CHWR_Temp": np.full(n, 56.0),     # 12F deltaT, healthy
        "OAT": np.full(n, 90.0),
    }, index=idx)
    r = analyze_chw_plant(df, "CHW")
    assert r.deltaT_median_f == 12.0
    assert r.low_deltaT_pct < 5


def test_chwst_reset_detected():
    n = 24 * 30
    idx = _idx(n)
    rng = np.random.default_rng(0)
    oat = 80 + 15 * np.sin((idx.hour - 9) / 24 * 2 * np.pi) + rng.normal(0, 1, n)
    chwst = np.clip(42 + 0.10 * (oat - 70) + rng.normal(0, 0.3, n), 40, 50)  # resets with OAT
    df = pd.DataFrame({"CHWS_Temp": chwst, "CHWR_Temp": chwst + 10, "OAT": oat}, index=idx)
    r = analyze_chw_plant(df, "CHW")
    assert abs(r.chwst_slope_per_F) >= 0.05
    assert r.chwst_reset_present


def test_flat_chwst_no_reset():
    n = 24 * 30
    idx = _idx(n)
    rng = np.random.default_rng(1)
    oat = 80 + 15 * np.sin((idx.hour - 9) / 24 * 2 * np.pi) + rng.normal(0, 1, n)
    chwst = np.full(n, 44.0) + rng.normal(0, 0.1, n)   # pinned flat
    df = pd.DataFrame({"CHWS_Temp": chwst, "CHWR_Temp": chwst + 5, "OAT": oat}, index=idx)
    r = analyze_chw_plant(df, "CHW")
    assert abs(r.chwst_slope_per_F) < 0.05
    assert not r.chwst_reset_present


def test_running_gate_excludes_offhours():
    n = 24 * 21
    idx = _idx(n)
    # half the rows are sensor dropouts at 0F (plant off) -> excluded by the gate
    chwst = np.where(np.arange(n) % 2 == 0, 44.0, 0.0)
    df = pd.DataFrame({"CHWS_Temp": chwst, "CHWR_Temp": chwst + 10,
                       "OAT": np.full(n, 90.0)}, index=idx)
    r = analyze_chw_plant(df, "CHW")
    # only the 44F rows count as running
    assert r.n_running > 0
    assert r.chwst_median_f == 44.0


def test_rule_protocol_and_severity():
    rule = CHWPlantReset()
    assert isinstance(rule, Rule)
    n = 24 * 21
    idx = _idx(n)
    frame = pd.DataFrame({
        Role.CHW_SUPPLY_TEMP: np.full(n, 44.0),
        Role.CHW_RETURN_TEMP: np.full(n, 47.0),   # low deltaT
        Role.OAT: np.full(n, 90.0),
    }, index=idx)
    f = rule.analyze("CHW", frame)
    assert f.severity == "fault"
    assert f.metrics["low_deltaT_pct"] > 95
