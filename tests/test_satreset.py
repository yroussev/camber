"""Tests for the SAT-reset diagnostic with synthetic AHUs of known behavior."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.satreset import analyze_satreset  # noqa: E402


def _frame(n=24 * 30, reset=False, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-07-01", periods=n, freq="1h")
    hour = idx.hour + idx.minute / 60.0
    oat = 90 + 12 * np.sin((hour - 9) / 24 * 2 * np.pi) + rng.normal(0, 1, n)
    if reset:
        # good reset: SAT rises as OAT falls below design -> positive slope vs OAT
        sat = 55 + 0.25 * (oat - 75) + rng.normal(0, 0.5, n)
        sat = np.clip(sat, 53, 65)
    else:
        # no reset: SAT pinned ~55 regardless of OAT
        sat = 55 + rng.normal(0, 0.4, n)
    return pd.DataFrame({
        "SupplyAir": sat,
        "CHW_Valve": np.full(n, 60.0),
        "OSA": oat,
    }, index=idx)


def test_no_reset_detected():
    r = analyze_satreset(_frame(reset=False), "AHU_T", occupied_only=False)
    assert abs(r.slope_per_F) < 0.10
    assert r.sat_std < 3.0
    assert "NO RESET" in r.verdict
    assert r.pct_sat_below_58 > 90


def test_reset_present_detected():
    r = analyze_satreset(_frame(reset=True), "AHU_T", occupied_only=False)
    assert r.slope_per_F > 0.10
    assert "RESET PRESENT" in r.verdict


def _frame_inverse(n=24 * 30, seed=3):
    # SAT gets colder as OAT rises (load tracking) -> negative slope
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-07-01", periods=n, freq="1h")
    hour = idx.hour + idx.minute / 60.0
    oat = 90 + 12 * np.sin((hour - 9) / 24 * 2 * np.pi) + rng.normal(0, 1, n)
    sat = 56 - 0.15 * (oat - 90) + rng.normal(0, 1.0, n)
    return pd.DataFrame({"SupplyAir": sat, "CHW_Valve": np.full(n, 60.0),
                         "OSA": oat}, index=idx)


def test_inverse_load_tracking_detected():
    r = analyze_satreset(_frame_inverse(), "AHU_T", occupied_only=False)
    assert r.slope_per_F < -0.05
    assert "INVERSE" in r.verdict


def test_returns_none_without_sat():
    df = pd.DataFrame({"CHW_Valve": [50, 60]},
                      index=pd.date_range("2025-07-01", periods=2, freq="1h"))
    assert analyze_satreset(df, "AHU_T", occupied_only=False) is None
