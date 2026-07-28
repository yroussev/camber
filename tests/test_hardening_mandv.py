"""Hardening: M&V calibration must degrade (accept=False), not raise, on degenerate energy."""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv.rc_model import calibrate, daily_schedule, option_d_savings  # noqa: E402


def _inputs(n):
    idx = pd.date_range("2024-01-01", periods=n, freq="1h")
    return np.linspace(30, 50, n), daily_schedule(idx)


@pytest.mark.parametrize(
    "energy",
    [
        np.array([1.0, 2.0, 3.0]),  # < 4 points
        np.full(48, np.nan),  # all NaN
        np.full(48, 5.0),  # constant (rank-deficient)
        np.concatenate([np.abs(np.linspace(1, 5, 30)), np.full(18, np.nan)]),  # gapped
    ],
)
def test_calibrate_degrades_not_raises(energy):
    oat, sched = _inputs(len(energy))
    cal = calibrate(oat, sched, energy)  # must not raise
    if len(energy) < 4 or not np.isfinite(energy).all() or np.ptp(energy[np.isfinite(energy)]) == 0:
        assert not cal.accept  # thin/degenerate -> not accepted


def test_degraded_calibration_claims_no_saving():
    oat, sched = _inputs(3)
    cal = calibrate(oat, sched, np.array([1.0, 2.0, 3.0]))
    sv = option_d_savings(cal, oat, sched, sched)
    assert not sv.valid and sv.avoided_energy is None


def test_length_mismatch_raises_clear_error():
    oat, sched = _inputs(48)
    with pytest.raises(ValueError):
        calibrate(oat[:10], sched, np.arange(48.0))  # oat/schedule/energy length mismatch
