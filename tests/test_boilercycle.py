"""Tests for the boiler short-cycling diagnostic (firing starts per day)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.boilercycle import analyze_boiler_cycling  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.base import Rule  # noqa: E402
from camber.rules.boilercycle_rule import BoilerShortCycle  # noqa: E402


def _idx(n):
    return pd.date_range("2025-01-06", periods=n, freq="1h")  # winter, heating season


# --- diagnostic --------------------------------------------------------------- #

def test_steady_firing_low_cycling():
    n = 24 * 14
    df = pd.DataFrame({"BoilerStatus": np.ones(n)}, index=_idx(n))  # fires continuously
    r = analyze_boiler_cycling(df, "BLR-1")
    assert r.starts_per_day < 0.2
    assert r.runtime_pct > 99


def test_short_cycling_flagged():
    n = 24 * 14
    status = np.tile([1.0, 0.0], n // 2)   # on/off every hour -> 12 starts/day
    r = analyze_boiler_cycling(pd.DataFrame({"BoilerStatus": status}, index=_idx(n)), "BLR-1")
    assert r.starts_per_day >= 11.5
    assert abs(r.runtime_pct - 50.0) < 1.0


def test_insufficient_data_returns_none():
    assert analyze_boiler_cycling(
        pd.DataFrame({"BoilerStatus": [1.0] * 4}, index=_idx(4)), "BLR-1") is None


# --- rule wrapper ------------------------------------------------------------- #

def test_rule_is_a_rule_and_severity():
    assert isinstance(BoilerShortCycle(), Rule)
    n = 24 * 14

    steady = pd.DataFrame({Role.BOILER_STATUS: np.ones(n)}, index=_idx(n))
    assert BoilerShortCycle(max_starts_per_day=6).analyze("BLR-1", steady).severity == "ok"

    cycling = pd.DataFrame({Role.BOILER_STATUS: np.tile([1.0, 0.0], n // 2)}, index=_idx(n))
    assert BoilerShortCycle(max_starts_per_day=6).analyze("BLR-1", cycling).severity == "fault"


def test_rule_missing_roles_reports_info():
    n = 24
    frame = pd.DataFrame({Role.HW_SUPPLY_TEMP: np.full(n, 140.0)}, index=_idx(n))
    assert BoilerShortCycle().analyze("BLR-1", frame).severity == "info"
