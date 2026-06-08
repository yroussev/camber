"""Tests for the TMY/EPW weather loader (M&V normalization)."""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv.weather import (  # noqa: E402
    c_to_f,
    load_epw,
    monthly_normals,
    normalized_annual_from_monthly,
)

EPW = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "weather", "tmy",
                   "example_tmyx_cz15.epw")

HAVE_EPW = os.path.exists(EPW)
skip = __import__("pytest").mark.skipif(not HAVE_EPW, reason="example EPW not present")


def test_c_to_f():
    assert c_to_f(0) == 32.0
    assert c_to_f(100) == 212.0


@skip
def test_load_epw_shape():
    s = load_epw(EPW)
    assert len(s) == 8760                 # one typical year, hourly
    assert s.index.is_monotonic_increasing
    # CZ15 hot desert: annual mean comfortably warm, summer hot
    assert 65 < s.mean() < 80
    assert s.max() > 105                  # summer peak
    assert s.min() < 45                   # winter night


@skip
def test_monthly_normals():
    mm = monthly_normals(load_epw(EPW))
    assert list(mm.index) == list(range(1, 13))
    # July (7) should be much hotter than January (1)
    assert mm.loc[7] > mm.loc[1] + 30


@skip
def test_normalized_annual_from_monthly():
    # a trivial linear model: 1000 kWh/month + 50 kWh per deg F
    class _M:
        def predict(self, T):
            return 1000.0 + 50.0 * np.asarray(T, dtype=float)
    annual = normalized_annual_from_monthly(_M(), load_epw(EPW))
    mm = monthly_normals(load_epw(EPW))
    expected = float(np.sum(1000.0 + 50.0 * mm.values))
    assert abs(annual - expected) < 1e-6
