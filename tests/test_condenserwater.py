"""Tests for the condenser-water reset diagnostic (CW supply vs wet-bulb)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.condenserwater import analyze_cw_reset  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.base import Rule  # noqa: E402
from camber.rules.condenserwater_rule import CondenserWaterReset  # noqa: E402


def _idx(n):
    return pd.date_range("2025-07-07", periods=n, freq="1h")


def _wetbulb_series(n):
    # diurnal wet-bulb swing 55..78F so there's a range to regress against
    h = np.arange(n) % 24
    return 66.0 + 11.0 * np.sin((h - 9) / 24 * 2 * np.pi)


# --- diagnostic --------------------------------------------------------------- #

def test_reset_detected_when_cws_tracks_wetbulb():
    n = 24 * 14
    wb = _wetbulb_series(n)
    df = pd.DataFrame({
        "CWS_Temp": wb + 7.0,          # full reset: CW supply = wet-bulb + 7F approach
        "CWR_Temp": wb + 17.0,
        "WetBulb": wb,
    }, index=_idx(n))
    r = analyze_cw_reset(df, "CWP")
    assert r.cws_slope_per_wetbulb > 0.8
    assert r.reset_present


def test_flat_setpoint_no_reset():
    n = 24 * 14
    wb = _wetbulb_series(n)
    df = pd.DataFrame({
        "CWS_Temp": np.full(n, 85.0),  # held constant regardless of wet-bulb
        "CWR_Temp": np.full(n, 95.0),
        "WetBulb": wb,
    }, index=_idx(n))
    r = analyze_cw_reset(df, "CWP")
    assert abs(r.cws_slope_per_wetbulb) < 0.1
    assert not r.reset_present


def test_wetbulb_derived_from_oat_rh():
    n = 24 * 14
    oat = 70.0 + _wetbulb_series(n) - 60.0   # something that varies
    df = pd.DataFrame({
        "CWS_Temp": np.full(n, 85.0),
        "CWR_Temp": np.full(n, 95.0),
        "OAT": oat,
        "RH": np.full(n, 40.0),
    }, index=_idx(n))
    r = analyze_cw_reset(df, "CWP")
    assert r.wetbulb_source == "derived"


def test_insufficient_data_returns_none():
    n = 24
    assert analyze_cw_reset(pd.DataFrame({"CWS_Temp": np.full(n, 85.0)}, index=_idx(n)),
                            "CWP") is None


# --- rule wrapper ------------------------------------------------------------- #

def test_rule_is_a_rule_and_severity():
    assert isinstance(CondenserWaterReset(), Rule)
    n = 24 * 14
    wb = _wetbulb_series(n)

    reset = pd.DataFrame({
        Role.CW_SUPPLY_TEMP: wb + 7.0, Role.CW_RETURN_TEMP: wb + 17.0,
        Role.WETBULB_TEMP: wb,
    }, index=_idx(n))
    assert CondenserWaterReset().analyze("CWP", reset).severity == "ok"

    flat = pd.DataFrame({
        Role.CW_SUPPLY_TEMP: np.full(n, 85.0), Role.CW_RETURN_TEMP: np.full(n, 95.0),
        Role.WETBULB_TEMP: wb,
    }, index=_idx(n))
    assert CondenserWaterReset().analyze("CWP", flat).severity == "warn"


def test_rule_missing_roles_reports_info():
    n = 24
    frame = pd.DataFrame({Role.CW_SUPPLY_TEMP: np.full(n, 85.0)}, index=_idx(n))
    assert CondenserWaterReset().analyze("CWP", frame).severity == "info"
