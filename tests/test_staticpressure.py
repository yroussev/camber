"""Tests for static-pressure reset + damper-distribution census (PNNL Ch.5/Ch.7)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.rules.base import FleetRule  # noqa: E402
from camber.rules.static_rule import DamperCensus  # noqa: E402
from camber.staticpressure import analyze_static_reset, damper_census  # noqa: E402


def _idx(n):
    return pd.date_range("2025-07-07", periods=n, freq="1h")  # Monday


def _boxes(medians):
    n = 24 * 14
    idx = _idx(n)
    return {f"VAV_{i}": pd.DataFrame({"Damper": np.full(n, m)}, index=idx)
            for i, m in enumerate(medians)}


def test_census_static_too_high():
    # most boxes throttling low -> static too high
    res = damper_census(_boxes([10, 15, 20, 8, 25, 12, 18, 30]))
    assert res.pct_boxes_low >= 60
    assert "TOO HIGH" in res.verdict


def test_census_static_too_low():
    # several boxes pinned open -> static too low
    res = damper_census(_boxes([95, 100, 92, 60, 98, 55]))
    assert res.pct_boxes_high >= 25
    assert "TOO LOW" in res.verdict


def test_census_healthy():
    res = damper_census(_boxes([55, 60, 65, 70, 58, 62]))
    assert "healthy" in res.verdict
    assert res.pct_boxes_in_band >= 50


def test_static_reset_flat_detected():
    n = 24 * 14
    idx = _idx(n)
    df = pd.DataFrame({"DuctStaticSP": np.full(n, 1.0)}, index=idx)  # flat
    r = analyze_static_reset(df, "AHU_1")
    assert r.sp_std < 0.05
    assert not r.sp_reset_present


def test_static_reset_present():
    n = 24 * 14
    idx = _idx(n)
    rng = np.random.default_rng(0)
    sp = 1.0 + 0.3 * np.sin(np.arange(n) / 12) + rng.normal(0, 0.05, n)
    df = pd.DataFrame({"DuctStaticSP": sp}, index=idx)
    r = analyze_static_reset(df, "AHU_2")
    assert r.sp_std >= 0.05
    assert r.sp_reset_present


def test_fleet_rule_protocol_and_severity():
    rule = DamperCensus()
    assert isinstance(rule, FleetRule)
    n = 24 * 14
    idx = _idx(n)
    frames = {f"VAV_{i}": pd.DataFrame({Role.DAMPER: np.full(n, m)}, index=idx)
              for i, m in enumerate([10, 12, 15, 8, 20, 18])}
    f = rule.analyze_fleet(frames)
    assert f.severity == "fault"
    assert f.metrics["pct_boxes_low"] >= 60
