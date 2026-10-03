"""Tests for carbon accounting (carbon.py)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.carbon import emissions  # noqa: E402


def test_emissions_sums_fuels_with_defaults():
    r = emissions({"electricity_kwh": 1000, "natural_gas_therm": 100})
    assert r.by_fuel["electricity_kwh"] == 400.0  # 1000 * 0.40
    assert r.by_fuel["natural_gas_therm"] == 530.0  # 100 * 5.30
    assert r.total_kg == 930.0
    assert r.total_tonnes == 0.93


def test_emissions_intensity_per_sf():
    r = emissions({"electricity_kwh": 1000}, gross_sf=10000)
    assert r.intensity_kg_sf == 0.04  # 400 / 10000


def test_factor_override():
    r = emissions({"electricity_kwh": 1000}, factors={"electricity_kwh": 0.25})
    assert r.total_kg == 250.0


def test_unknown_fuel_raises():
    with pytest.raises(KeyError):
        emissions({"unobtanium_kg": 5})


def test_per_unit_factors_convert_through_convert_rate():
    """0.93 (#70): a factor in any unit, converted exactly to the unit the fuel key names."""
    import pytest

    from camber.carbon import emissions, factor_per

    assert factor_per({"rate": 53.06, "per": "MMBtu"}, "natural_gas_therm") == pytest.approx(5.306)
    assert factor_per({"rate": 400.0, "per": "MWh"}, "electricity_kwh") == pytest.approx(0.4)
    per_mcf = factor_per(
        {"rate": 55.0, "per": "Mcf", "heat_content": "10.37 therm/Mcf"}, "natural_gas_therm"
    )
    assert per_mcf == pytest.approx(55.0 / 10.37)
    assert factor_per({"rate": 10.21, "per": "gal"}, "fuel_oil_gal") == 10.21
    e = emissions(
        {"natural_gas_therm": 1000.0},
        factors={"natural_gas_therm": {"rate": 53.06, "per": "MMBtu"}},
    )
    assert e.total_kg == pytest.approx(5306.0)
    assert emissions({"natural_gas_therm": 1000.0}).total_kg == 5300.0  # numbers as before
    for spec, key, msg in (
        ({"rate": 1.0, "per": "litre"}, "fuel_oil_gal", "not an energy unit"),
        ({"rate": 1.0, "per": "Mcf"}, "natural_gas_therm", "heat_content"),
        ({"rate": 1.0}, "electricity_kwh", "must be"),
        ({"rate": -1.0, "per": "kWh"}, "electricity_kwh", "non-negative"),
    ):
        with pytest.raises(ValueError, match=msg):
            factor_per(spec, key)
