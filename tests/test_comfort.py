"""Tests for PMV/PPD thermal comfort (ASHRAE 55 / ISO 7730).

PMV/PPD checks use the worked reference values published in ISO 7730 Annex D /
ASHRAE 55 -- the canonical validation cases for any Fanger implementation.
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.comfort import comfort_series, pmv, ppd, pmv_f  # noqa: E402


# ISO 7730 Annex D worked examples (ta=tr, vel, rh, met, clo -> PMV, PPD)
# Case 1: 22C, 0.1 m/s, 60% RH, 1.2 met, 0.5 clo -> PMV -0.75, PPD 17%
# Case 2: 27C, 0.1 m/s, 60% RH, 1.2 met, 0.5 clo -> PMV +0.77, PPD 17%
# Case 3: 24C, 0.1 m/s, 50% RH, 1.0 met, 0.5 clo -> PMV ~ -0.5, PPD ~10%
def test_pmv_iso_case1_cool():
    v = pmv(22.0, 22.0, 0.1, 60, 1.2, 0.5)
    assert abs(v - (-0.75)) < 0.1


def test_pmv_iso_case2_warm():
    v = pmv(27.0, 27.0, 0.1, 60, 1.2, 0.5)
    assert abs(v - 0.77) < 0.1


def test_ppd_from_pmv():
    assert abs(ppd(0.0) - 5.0) < 0.1          # neutral -> 5% minimum dissatisfied
    assert abs(ppd(-0.75) - 17.0) < 1.5       # ISO case 1
    assert abs(ppd(0.77) - 17.0) < 1.5


def test_pmv_neutral_near_zero():
    # ~24-25C, light clothing, seated -> near-neutral PMV
    v = pmv(24.5, 24.5, 0.1, 50, 1.1, 0.6)
    assert abs(v) < 0.4


def test_pmv_f_degF_wrapper():
    # 72F == 22.2C, similar to ISO case 1 conditions -> mildly cool
    v = pmv_f(72.0, vel=0.1, rh=60, met=1.2, clo=0.5)
    assert -1.0 < v < -0.3


def test_colder_air_drives_pmv_down():
    warm = pmv_f(74, met=1.1, clo=0.6)
    cold = pmv_f(68, met=1.1, clo=0.6)
    assert cold < warm                         # colder -> lower (more dissatisfied-cold)


def test_comfort_series_flags_overcooling():
    # zone held at 68F (cool for office clo/met) -> high cold-side fraction
    idx = pd.date_range("2025-07-07", periods=200, freq="1h")
    s = pd.Series(np.full(200, 68.0), index=idx)
    r = comfort_series(s, met=1.1, clo=0.6, equip="VAV_1")
    assert r.pct_cold > 50                      # mostly cold-uncomfortable
    assert r.pmv_median < -0.5


def test_comfort_series_comfortable_at_neutral():
    idx = pd.date_range("2025-07-07", periods=200, freq="1h")
    s = pd.Series(np.full(200, 74.0), index=idx)
    r = comfort_series(s, met=1.1, clo=0.6, equip="VAV_2")
    assert r.pct_comfortable > 80
