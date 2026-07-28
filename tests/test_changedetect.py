"""Tests for operational change-point detection (camber.changedetect)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.changedetect import LevelShift, detect_level_shifts, largest_shift  # noqa: E402


def _idx(n):
    return pd.date_range("2025-01-01", periods=n, freq="1h")


def test_single_step_detected_with_correct_levels():
    rng = np.random.default_rng(0)
    x = np.concatenate([50 + rng.normal(0, 2, 150), 70 + rng.normal(0, 2, 150)])
    s = pd.Series(x, index=_idx(300))
    ls = largest_shift(s)
    assert isinstance(ls, LevelShift)
    assert abs(ls.before_mean - 50) < 2 and abs(ls.after_mean - 70) < 2
    assert abs(ls.delta - 20) < 3
    assert abs((ls.at - s.index[150]).total_seconds()) / 3600 < 24  # near the true break


def test_flat_series_has_no_shift():
    rng = np.random.default_rng(1)
    assert detect_level_shifts(pd.Series(50 + rng.normal(0, 2, 300), index=_idx(300))) == []
    assert largest_shift(pd.Series(50 + rng.normal(0, 2, 300), index=_idx(300))) is None


def test_two_steps_detected():
    rng = np.random.default_rng(2)
    x = np.concatenate(
        [50 + rng.normal(0, 1.5, 100), 70 + rng.normal(0, 1.5, 100), 55 + rng.normal(0, 1.5, 100)]
    )
    shifts = detect_level_shifts(pd.Series(x, index=_idx(300)), max_shifts=5)
    assert len(shifts) == 2
    deltas = sorted(round(s.delta) for s in shifts)
    assert deltas == [-15, 20]  # 70->55 and 50->70


def test_min_delta_filters_small_steps():
    rng = np.random.default_rng(3)
    x = np.concatenate(
        [50 + rng.normal(0, 0.5, 150), 51 + rng.normal(0, 0.5, 150)]
    )  # tiny 1-unit step
    s = pd.Series(x, index=_idx(300))
    assert len(detect_level_shifts(s, min_delta=5.0)) == 0  # below min_delta
    assert len(detect_level_shifts(s, min_delta=0.2)) >= 1  # detected when allowed


def test_min_segment_prevents_tiny_segments():
    rng = np.random.default_rng(4)
    x = np.concatenate([50 + rng.normal(0, 2, 150), 70 + rng.normal(0, 2, 150)])
    s = pd.Series(x, index=_idx(300))
    # a huge min_segment (> half the series) makes splitting impossible
    assert detect_level_shifts(s, min_segment=200) == []


def test_shift_is_jsonable():
    rng = np.random.default_rng(5)
    x = np.concatenate([10 + rng.normal(0, 1, 100), 30 + rng.normal(0, 1, 100)])
    ls = largest_shift(pd.Series(x, index=_idx(200)))
    d = ls.as_dict()
    assert isinstance(d["at"], str) and d["delta"] == ls.delta
