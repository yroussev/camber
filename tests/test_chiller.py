"""Tests for the chiller-efficiency diagnostic (kW/ton vs design ceiling)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.chiller import analyze_chiller_efficiency  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.base import Rule  # noqa: E402
from camber.rules.chiller_rule import ChillerEfficiency  # noqa: E402


def _idx(n):
    return pd.date_range("2025-07-07", periods=n, freq="1h")  # Monday, summer


def _plant(n, kw_per_ton, tons=200.0, dt=12.0):
    """A steady chiller frame producing `tons` at a given kW/ton (legacy columns).

    gpm is back-solved from tons and dT (tons = gpm*dT/24); power = kW/ton * tons.
    """
    gpm = tons * 24.0 / dt
    return pd.DataFrame({
        "Power": np.full(n, kw_per_ton * tons),
        "CHWS_Temp": np.full(n, 44.0),
        "CHWR_Temp": np.full(n, 44.0 + dt),
        "CHW_Flow": np.full(n, gpm),
    }, index=_idx(n))


# --- diagnostic --------------------------------------------------------------- #

def test_efficient_chiller_not_flagged():
    r = analyze_chiller_efficiency(_plant(24 * 14, 0.58), "CH-1", design_kw_per_ton=0.60)
    assert r is not None
    assert abs(r.kw_per_ton_median - 0.58) < 0.02
    assert r.pct_hours_inefficient < 5


def test_inefficient_chiller_flagged():
    r = analyze_chiller_efficiency(_plant(24 * 14, 1.10), "CH-1", design_kw_per_ton=0.60)
    assert abs(r.kw_per_ton_median - 1.10) < 0.02
    assert r.pct_hours_inefficient > 95


def test_low_load_intervals_excluded():
    # 200-ton load but with a near-zero dT for half the hours -> those drop out as
    # too-low-load (kW/ton would be meaningless there), not counted as inefficient.
    n = 24 * 14
    df = _plant(n, 0.58)
    df.iloc[: n // 2, df.columns.get_loc("CHWR_Temp")] = 44.1  # dT ~0.1F -> excluded
    r = analyze_chiller_efficiency(df, "CH-1", design_kw_per_ton=0.60)
    assert r.n_running <= n // 2 + 1
    assert r.pct_hours_inefficient < 5


def test_insufficient_data_returns_none():
    df = pd.DataFrame({"Power": [100.0] * 5}, index=_idx(5))  # missing CHW columns
    assert analyze_chiller_efficiency(df, "CH-1") is None


# --- rule wrapper (role-frame interface) -------------------------------------- #

def test_rule_is_a_rule_and_severity_scales():
    assert isinstance(ChillerEfficiency(), Rule)

    def role_frame(kw_per_ton):
        n = 24 * 14
        gpm = 200.0 * 24.0 / 12.0
        return pd.DataFrame({
            Role.POWER: np.full(n, kw_per_ton * 200.0),
            Role.CHW_SUPPLY_TEMP: np.full(n, 44.0),
            Role.CHW_RETURN_TEMP: np.full(n, 56.0),
            Role.CHW_FLOW: np.full(n, gpm),
        }, index=_idx(n))

    rule = ChillerEfficiency(design_kw_per_ton=0.60)
    assert rule.analyze("CH-1", role_frame(0.58)).severity == "ok"
    assert rule.analyze("CH-1", role_frame(0.75)).severity == "warn"   # 1.25x design
    assert rule.analyze("CH-1", role_frame(1.00)).severity == "fault"  # 1.67x design


def test_rule_missing_roles_reports_info():
    n = 24
    frame = pd.DataFrame({Role.POWER: np.full(n, 100.0)}, index=_idx(n))
    f = ChillerEfficiency().analyze("CH-1", frame)
    assert f.severity == "info"
