"""Tests for carbon accounting (carbon.py)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.carbon import emissions  # noqa: E402


def test_emissions_sums_fuels_with_defaults():
    r = emissions({"electricity_kwh": 1000, "natural_gas_therm": 100})
    assert r.by_fuel["electricity_kwh"] == 400.0      # 1000 * 0.40
    assert r.by_fuel["natural_gas_therm"] == 530.0    # 100 * 5.30
    assert r.total_kg == 930.0
    assert r.total_tonnes == 0.93


def test_emissions_intensity_per_sf():
    r = emissions({"electricity_kwh": 1000}, gross_sf=10000)
    assert r.intensity_kg_sf == 0.04                  # 400 / 10000


def test_factor_override():
    r = emissions({"electricity_kwh": 1000}, factors={"electricity_kwh": 0.25})
    assert r.total_kg == 250.0


def test_unknown_fuel_raises():
    with pytest.raises(KeyError):
        emissions({"unobtanium_kg": 5})
