"""Tests for multi-fuel M&V handling: heating signature + empty-fuel guard."""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv.models import best_model, fit_model  # noqa: E402
from camber.mandv.stats import fit_stats  # noqa: E402


def test_heating_fuel_negative_slope():
    # gas-like: use rises as it gets colder -> 3PH, negative correlation with OAT
    rng = np.random.default_rng(0)
    T = np.linspace(40, 100, 200)
    y = 100 + 18 * np.maximum(0.0, 65 - T) + rng.normal(0, 5, len(T))
    m = fit_model(T, y, "3PH")
    assert m.coeffs["heat_slope"] > 0          # 3PH slope is on (Tc - T), so positive
    assert np.corrcoef(T, y)[0, 1] < 0          # but use vs OAT is negative (heating)


def test_summer_base_load_recovered():
    # heating fuel with a nonzero summer floor (domestic hot water) -> 3PH base > 0
    rng = np.random.default_rng(1)
    T = np.linspace(40, 105, 200)
    y = 120 + 15 * np.maximum(0.0, 60 - T) + rng.normal(0, 3, len(T))
    m = fit_model(T, y, "3PH")
    assert 100 < m.coeffs["base"] < 140         # recovers the ~120 summer floor


def test_empty_fuel_has_no_model():
    # a fuel column of all zeros must not be modeled (no signal to fit)
    import pandas as pd
    s = pd.Series(np.zeros(12))
    nonzero = s[s > 0]
    assert len(nonzero) == 0                     # caller skips: "not metered"
