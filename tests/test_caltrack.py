"""Tests for the CalTRACK/IPMVP NMEC savings workflow (mandv.caltrack)."""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv.caltrack import NMECResult, caltrack_savings  # noqa: E402


def _series(start, days, *, reduction=0.0, seed=0):
    """Hourly energy + temp for `days` days; cooling load with optional reduction."""
    idx = pd.date_range(start, periods=days * 24, freq="1h")
    rng = np.random.default_rng(seed)
    temp = 60 + 18 * np.sin((idx.hour - 9) / 24 * 2 * np.pi) \
        + 8 * np.sin(np.arange(len(idx)) / (24 * 30))           # seasonal drift
    base = 20.0 + np.clip(temp - 65, 0, None) * 1.5             # cooling change-point
    energy = base * (1.0 - reduction) + rng.normal(0, 0.5, len(idx))
    return pd.Series(energy, index=idx), pd.Series(temp, index=idx)


def test_nmec_recovers_known_savings():
    be, bt = _series("2023-01-01", 120, reduction=0.0, seed=1)
    re_, rt = _series("2024-01-01", 120, reduction=0.20, seed=2)   # 20% retrofit
    res = caltrack_savings(be, bt, re_, rt)
    assert isinstance(res, NMECResult)
    assert res.model_kind in ("3PC", "4P", "2P")     # cooling-driven baseline
    assert res.baseline_r2 > 0.9
    assert 0.14 < res.savings.savings_pct < 0.26      # ~20% avoided (fraction)
    assert res.savings.avoided_energy > 0
    assert res.savings.fractional_uncertainty > 0     # FSU reported


def test_no_savings_is_near_zero():
    be, bt = _series("2023-01-01", 120, reduction=0.0, seed=3)
    re_, rt = _series("2024-01-01", 120, reduction=0.0, seed=4)    # no change
    res = caltrack_savings(be, bt, re_, rt)
    assert abs(res.savings.savings_pct) < 0.10        # ~0% within noise (fraction)


def test_requires_enough_baseline_days():
    be, bt = _series("2023-01-01", 10)
    re_, rt = _series("2024-01-01", 120)
    with pytest.raises(ValueError):
        caltrack_savings(be, bt, re_, rt)


def test_result_as_dict_includes_nested_savings():
    be, bt = _series("2023-01-01", 90)
    re_, rt = _series("2024-01-01", 90, reduction=0.1)
    d = caltrack_savings(be, bt, re_, rt).as_dict()
    assert "model_kind" in d and isinstance(d["savings"], dict)
    assert "avoided_energy" in d["savings"]
