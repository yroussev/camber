"""Regression guards for the G36 / Std-55-comfort vectorization (Task 2a).

These pin the numerical outputs of the per-row routines that were replaced with
vectorized implementations. The golden values were captured from the pre-refactor
(row-loop) code on fixed synthetic inputs; the vectorized code must reproduce them
exactly. This makes the refactor provably behavior-preserving, not a rewrite.
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.comfort import comfort_series, pmv_f  # noqa: E402
from camber.fdd_g36 import run_g36_afdd  # noqa: E402


def test_comfort_series_matches_rowloop_golden():
    r = np.arange(300)
    idx = pd.date_range("2025-07-07", periods=300, freq="1h")
    temps = 68.0 + 6.0 * np.sin(r / 12.0)
    s = pd.Series(temps, index=idx)
    mr = pd.Series(temps + 1.5, index=idx)
    res = comfort_series(s, mrt_f=mr, vel=0.12, rh=45, met=1.1, clo=0.65, equip="Z")
    assert res.n == 300
    assert res.pmv_median == -1.32
    assert res.ppd_median == 41.2
    assert res.pct_cold == 81.0
    assert res.pct_hot == 0.0
    assert res.pct_comfortable == 19.0


def test_pmv_f_scalar_unchanged():
    # the scalar wrapper must stay byte-for-byte stable (high precision)
    got = [round(pmv_f(t, tr_f=t + 1.5, vel=0.12, rh=45, met=1.1, clo=0.65), 6)
           for t in (62.0, 68.0, 74.0)]
    assert got == [-2.293174, -1.322969, -0.331559]


def _g36_golden_frame():
    n = 400
    r = np.arange(n)
    idx = pd.date_range("2025-06-01", periods=n, freq="15min")
    return pd.DataFrame({
        "HC": np.where(r % 7 < 2, 30.0, 0.0),
        "CC": np.where(r % 7 >= 4, 60.0, 0.0),
        "SAT": 55 + 2 * np.sin(r / 15.0),
        "MAT": 60 + 3 * np.cos(r / 15.0),
        "RAT": np.full(n, 72.0),
        "OAT": 60 + 10 * np.sin(r / 20.0),
        "SATSP": np.full(n, 55.0),
        "FS": np.full(n, 95.0),
        "DSP": np.full(n, 1.2),
        "DSPSP": np.full(n, 1.5),
        "OA_Damper": np.where(r % 5 == 0, 90.0, 20.0),
        "pct_oa": np.full(n, 40.0),
        "pct_oa_min": np.full(n, 20.0),
        "CCET": np.full(n, 58.0),
        "CCLT": np.full(n, 52.0),
        "HCET": np.full(n, 60.0),
        "HCLT": np.full(n, 63.0),
    }, index=idx)


def test_g36_matches_rowloop_golden():
    g = run_g36_afdd(_g36_golden_frame(), "AHU_1")
    assert g.n_intervals == 400
    assert g.os_distribution == {1: 115, 2: 114, 3: 34, 4: 137, 5: 0}
    assert g.fault_n_applicable == {
        1: 400, 2: 400, 3: 400, 4: 400, 5: 115, 6: 252, 7: 115, 8: 114,
        9: 114, 10: 34, 11: 34, 12: 285, 13: 171, 14: 229, 15: 285}
    assert g.fault_pct == {
        1: 100.0, 2: 10.75, 3: 0.0, 4: 0.0, 5: 50.43, 6: 0.0, 7: 0.0,
        8: 67.54, 9: 52.63, 10: 38.24, 11: 0.0, 12: 0.0, 13: 0.0,
        14: 0.0, 15: 0.0}
