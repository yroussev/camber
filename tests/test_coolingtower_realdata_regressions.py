"""Regressions for cooling-tower defects found by running CAMBER on open real plant datasets.

Each test reproduces one defect on **synthetic** data shaped like what the real data showed (5
samples at high fan, a site above sea level). Nothing here is drawn from a measured dataset; the
before/after numbers on the real data are recorded in docs/VALIDATION.md.
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.coolingtower import (  # noqa: E402
    pressure_psia_at_elevation,
    psychrometric_wetbulb_f,
    stull_wetbulb_f,
)
from camber.model.roles import Role  # noqa: E402
from camber.rules.coolingtower_rule import CoolingTowerApproach  # noqa: E402

# =========================================================================== 6. cooling tower


def _tower_frame(n_high):
    n = 100
    idx = pd.date_range("2025-07-01", periods=n, freq="1h")
    fan = np.full(n, 50.0)
    fan[:n_high] = 95.0
    return pd.DataFrame(
        {
            Role.CW_SUPPLY_TEMP: 80.0,
            Role.CW_RETURN_TEMP: 90.0,
            Role.WETBULB_TEMP: 72.0,
            Role.TOWER_FAN_SPEED: fan,
        },
        index=idx,
    )


def test_tower_decline_says_what_happened():
    few = CoolingTowerApproach().analyze("CT", _tower_frame(5))
    assert few.metrics["declined"] is True and few.metrics["n_high_effort"] == 5
    assert "never reached" not in few.summary and "only 5" in few.summary
    none = CoolingTowerApproach().analyze("CT", _tower_frame(0))
    assert "never reached" in none.summary


def test_tower_fan_as_fraction_is_normalized_in_the_rule():
    fr = _tower_frame(50)
    fr[Role.TOWER_FAN_SPEED] = fr[Role.TOWER_FAN_SPEED] / 100.0
    f = CoolingTowerApproach().analyze("CT", fr)
    assert f.metrics.get("declined") is not True and f.metrics["n_operating"] == 50


def test_wetbulb_elevation_correction():
    assert pressure_psia_at_elevation(0) == pytest.approx(14.696)
    sea = float(stull_wetbulb_f(95.0, 20.0))
    at_1600m = float(stull_wetbulb_f(95.0, 20.0, elevation_ft=1600 / 0.3048))
    # the psychrometric wet-bulb at 1600 m (~64.1 F) sits ~2.5 F below the sea-level Stull value
    assert sea - at_1600m == pytest.approx(2.6, abs=0.3)
    assert float(stull_wetbulb_f(95.0, 20.0, pressure_psia=14.696)) == pytest.approx(
        float(psychrometric_wetbulb_f(95.0, 20.0))
    )
    # default (no elevation) is unchanged: Stull at sea level
    assert float(stull_wetbulb_f(95.0, 20.0)) == pytest.approx(66.74, abs=0.01)


def test_tower_rule_caveats_a_sea_level_derived_wetbulb():
    n = 50
    fr = pd.DataFrame(
        {
            Role.CW_SUPPLY_TEMP: 80.0,
            Role.OAT: 95.0,
            Role.OUTDOOR_RH: 20.0,
            Role.TOWER_FAN_SPEED: 95.0,
        },
        index=pd.date_range("2025-07-01", periods=n, freq="1h"),
    )
    sea = CoolingTowerApproach().analyze("CT", fr)
    site = CoolingTowerApproach(elevation_ft=5249).analyze("CT", fr)
    assert sea.metrics["wetbulb_pressure_basis"] == "sea-level"
    assert any("sea-level" in c for c in sea.caveats)
    assert site.metrics["wetbulb_pressure_basis"] == "site"
    assert site.metrics["approach_median_f"] > sea.metrics["approach_median_f"] + 2
