"""Tests for percent/position unit normalization (units.py)."""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.units import (  # noqa: E402
    looks_like_fraction, normalize_percent, normalize_percent_frame,
)


def test_looks_like_fraction():
    assert looks_like_fraction(pd.Series([0.0, 0.5, 1.0]))
    assert not looks_like_fraction(pd.Series([0.0, 50.0, 100.0]))
    assert not looks_like_fraction(pd.Series([], dtype="float64"))
    assert not looks_like_fraction(pd.Series([-5.0, 0.5]))   # negatives -> not a fraction


def test_normalize_percent_scales_fraction_only():
    frac = pd.Series([0.1, 0.5, 1.0])
    assert list(normalize_percent(frac)) == [10.0, 50.0, 100.0]
    pct = pd.Series([0.0, 50.0, 100.0])
    assert list(normalize_percent(pct)) == [0.0, 50.0, 100.0]   # unchanged


def test_normalize_frame_touches_only_percent_roles():
    frame = pd.DataFrame({
        Role.COOL_VALVE: [0.1, 0.5, 1.0],     # fraction -> scale
        Role.HEAT_VALVE: [0.0, 50.0, 100.0],  # already percent -> unchanged
        Role.SUPPLY_AIR_TEMP: [0.5, 0.6, 0.7],  # not a percent role -> unchanged
    })
    out = normalize_percent_frame(frame)
    assert list(out[Role.COOL_VALVE]) == [10.0, 50.0, 100.0]
    assert list(out[Role.HEAT_VALVE]) == [0.0, 50.0, 100.0]
    assert list(out[Role.SUPPLY_AIR_TEMP]) == [0.5, 0.6, 0.7]   # temp left alone
