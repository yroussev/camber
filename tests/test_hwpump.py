"""Tests for the HW pump operation rule (riding-the-curve / VFD-minimum)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.rules.base import Rule  # noqa: E402
from camber.rules.hwpump_rule import HWPumpDPReset  # noqa: E402


def _idx(n):
    return pd.date_range("2025-01-06", periods=n, freq="1h")  # winter / heating season


def test_rule_is_a_rule():
    assert isinstance(HWPumpDPReset(), Rule)


def test_riding_the_curve_fault():
    n = 24 * 21
    frame = pd.DataFrame({Role.HW_PUMP_SPEED: np.full(n, 100.0)}, index=_idx(n))
    f = HWPumpDPReset().analyze("HWP-1", frame)
    assert f.severity == "fault"
    assert f.metrics["pct_running_near_full"] > 95


def test_oversized_min_speed_warn():
    n = 24 * 21
    frame = pd.DataFrame({Role.HW_PUMP_SPEED: np.full(n, 18.0)}, index=_idx(n))
    f = HWPumpDPReset().analyze("HWP-1", frame)
    assert f.severity == "warn"
    assert f.metrics["pct_running_near_min"] > 95


def test_modulating_ok():
    n = 24 * 21
    rng = np.random.default_rng(0)
    spd = np.clip(55 + 18 * np.sin(np.arange(n) / 12) + rng.normal(0, 3, n), 30, 80)
    frame = pd.DataFrame({Role.HW_PUMP_SPEED: spd}, index=_idx(n))
    assert HWPumpDPReset().analyze("HWP-1", frame).severity == "ok"


def test_missing_roles_info():
    n = 24
    frame = pd.DataFrame({Role.HW_SUPPLY_TEMP: np.full(n, 140.0)}, index=_idx(n))
    assert HWPumpDPReset().analyze("HWP-1", frame).severity == "info"


def test_status_gates_running_0105():
    # 0.105 (#180): a stopped pump whose speed parks at 30 % is not read as running at it
    n = 24 * 21
    on = np.arange(n) % 2 == 0
    spd = np.where(on, 100.0, 30.0)
    frame = pd.DataFrame(
        {Role.HW_PUMP_SPEED: spd, Role.PUMP_STATUS: on.astype(float)}, index=_idx(n)
    )
    f = HWPumpDPReset().analyze("HWP-1", frame)
    assert Role.PUMP_STATUS in HWPumpDPReset.roles_optional
    assert f.metrics["run_source"] == "status" and f.metrics["n_running"] == n // 2
    assert f.metrics["pct_running_near_full"] == 100.0 and f.severity == "fault"
    g = HWPumpDPReset().analyze("HWP-1", frame.drop(columns=Role.PUMP_STATUS))
    assert g.metrics["pct_running_near_full"] == 50.0
    assert any("no pump run status" in c for c in g.caveats)
