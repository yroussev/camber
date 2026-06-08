"""Tests for the AHU simultaneous-H/C diagnostic using a synthetic frame."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.ahu import analyze_ahu  # noqa: E402


def _frame(n=24 * 14, simul=False):
    idx = pd.date_range("2025-07-01", periods=n, freq="1h")
    chw = np.full(n, 60.0)              # cooling always on
    hhw = np.zeros(n)
    if simul:
        hhw[:] = 30.0                   # heating coil also open -> simultaneous
    return pd.DataFrame({
        "CHW_Valve": chw, "HHW_Valve": hhw,
        "ReturnAir": np.full(n, 74.0), "OSA": np.full(n, 95.0),
        "OA_Damper": np.full(n, 10.0),
    }, index=idx)


def test_no_simultaneous():
    r = analyze_ahu(_frame(simul=False), "AHU_T", occupied_only=False)
    assert r.simultaneous_hc_pct < 1.0
    assert r.chw_open_pct > 99.0


def test_simultaneous_detected():
    r = analyze_ahu(_frame(simul=True), "AHU_T", occupied_only=False)
    assert r.simultaneous_hc_pct > 99.0
    assert r.mean_overlap_when_simul == 30.0


def test_economizer_no_opportunity_when_hot():
    # OSA 95F > return 74F -> no economizer opportunity, so missed must be 0
    r = analyze_ahu(_frame(), "AHU_T", occupied_only=False)
    assert r.econ_opportunity_pct == 0.0
    assert r.econ_missed_pct == 0.0
