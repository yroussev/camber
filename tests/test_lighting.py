"""Tests for lighting operational efficiency (lighting.py)."""

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.lighting import lighting_summary, operational_efficiency  # noqa: E402


def test_operational_efficiency_ratio():
    idx = pd.date_range("2024-01-01", periods=1, freq="h")
    eff = operational_efficiency(pd.Series([50.0], index=idx), installed_kw=100.0)
    assert eff.iloc[0] == 0.5


def test_operational_efficiency_requires_positive_installed():
    with pytest.raises(ValueError):
        operational_efficiency(pd.Series([1.0]), installed_kw=0.0)


def test_flags_failed_unoccupied_setback():
    idx = pd.date_range("2024-01-01", periods=4, freq="h")
    metered = pd.Series([80.0, 80.0, 90.0, 90.0], index=idx)   # eff 0.8/0.8/0.9/0.9
    occupied = [True, True, False, False]
    s = lighting_summary(metered, 100.0, occupied=occupied)
    assert s.occupied_mean == 0.8
    assert s.unoccupied_mean == 0.9
    assert "failed_unoccupied_setback" in s.flags        # 0.9 > 0.30 unoccupied
    assert "no_turndown" not in s.flags                  # min 0.8 < 0.90


def test_flags_no_turndown():
    idx = pd.date_range("2024-01-01", periods=2, freq="h")
    metered = pd.Series([95.0, 95.0], index=idx)
    s = lighting_summary(metered, 100.0, occupied=[True, False])
    assert "no_turndown" in s.flags                      # never drops below 0.90
