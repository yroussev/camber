"""Energy units (issue #69): the conversion module -- exact factors, round trips, parsing and
the refusals (unknown and ambiguous units, gas volumes without a heat content)."""

import itertools
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber import energy_units as eu  # noqa: E402
from camber.bps import EUI_FACTORS_KBTU  # noqa: E402

HC = "10.37 therm/Mcf"

# --------------------------------------------------------------------------- factors


def test_exact_factors_and_their_printed_forms():
    assert round(eu.KBTU_PER_KWH, 6) == 3.412142
    assert eu.convert(1.0, "kWh", "kBtu") == pytest.approx(3.41214163, rel=1e-9)
    assert round(eu.convert(1.0, "kBtu", "MJ"), 6) == 1.055056
    assert eu.convert(1.0, "therm", "Btu") == pytest.approx(100_000.0, rel=1e-15)
    assert eu.convert(1.0, "therm", "kBtu") == 100.0
    assert eu.convert(1.0, "MMBtu", "kBtu") == 1000.0
    assert eu.convert(1.0, "dekatherm", "therm") == pytest.approx(10.0)
    assert eu.convert(1.0, "ton-hour", "kBtu") == 12.0
    assert eu.convert(1.0, "kWh", "MJ") == pytest.approx(3.6, rel=1e-15)
    assert eu.convert(1.0, "GJ", "MJ") == 1000.0
    assert eu.convert(1.0, "MWh", "kWh") == 1000.0
    assert eu.convert(1000.0, "Wh", "kWh") == 1.0
    assert eu.power_factor("MBH", "kBtu/h") == 1.0
    assert eu.power_factor("tons", "kBtu/h") == pytest.approx(12.0)
    assert eu.power_factor("kW", "kBtu/h") == pytest.approx(eu.KBTU_PER_KWH)
    # 1 m2 = 10.7639104 ft2 (1 ft = 0.3048 m exactly)
    assert eu.eui_factor("kBtu/ft2/yr", "kWh/m2/yr") == pytest.approx(
        10.7639104167 / eu.KBTU_PER_KWH, rel=1e-9
    )


def test_every_energy_pair_round_trips():
    for a, b in itertools.permutations(eu.ENERGY_UNITS, 2):
        x = 1234.5678
        assert eu.convert(eu.convert(x, a, b), b, a) == pytest.approx(x, rel=1e-12), (a, b)
    for a, b in itertools.permutations(eu.POWER_UNITS, 2):
        assert eu.convert_power(eu.convert_power(7.0, a, b), b, a) == pytest.approx(7.0, rel=1e-12)
    arr = np.array([1.0, 2.0, 3.0])
    assert np.allclose(eu.convert(arr, "therm", "kBtu"), arr * 100.0)


def test_bps_keeps_its_rounded_factor_and_documents_the_gap():
    # the historical 3.412 stays (byte-identical site_eui); the exact factor is 0.004 % higher
    assert EUI_FACTORS_KBTU["electricity"] == 3.412
    assert (eu.KBTU_PER_KWH - 3.412) / eu.KBTU_PER_KWH == pytest.approx(4.15e-5, rel=0.01)
    assert EUI_FACTORS_KBTU["natural_gas"] == eu.convert(1, "therm", "kBtu")
    assert EUI_FACTORS_KBTU["district_chw"] == eu.convert(1, "ton-hour", "kBtu")


# --------------------------------------------------------------------------- parsing and refusals


@pytest.mark.parametrize(
    "text, name",
    [
        ("kWh", "kWh"),
        (" KWH ", "kWh"),
        ("kilowatt-hours", "kWh"),
        ("kW h", "kWh"),
        ("therms", "therm"),
        ("Therm", "therm"),
        ("MMBTU", "MMBtu"),
        ("ton-hr", "ton-hour"),
        ("ton·h", "ton-hour"),
        ("GJ", "GJ"),
        ("kbtu", "kBtu"),
    ],
)
def test_parse_energy_spellings(text, name):
    assert eu.parse_unit(text, kind="energy").name == name


@pytest.mark.parametrize(
    "text, name",
    [("MBH", "kBtu/h"), ("kBtu/hr", "kBtu/h"), ("kbtu per hour", "kBtu/h"), ("Tons", "ton")],
)
def test_parse_power_spellings(text, name):
    assert eu.parse_unit(text, kind="power").name == name


@pytest.mark.parametrize(
    "text, kind, match",
    [
        ("MBtu", "energy", "ambiguous"),
        ("Mlb", ("volume", "mass"), "ambiguous"),
        ("ton", "energy", "ton-hour"),
        ("therm", "power", "energy unit"),
        ("furlongs", None, "unknown unit"),
        ("", "energy", "no unit"),
        ("kWh", "volume", "energy unit"),
    ],
)
def test_parse_refuses_unknown_and_ambiguous(text, kind, match):
    with pytest.raises(ValueError, match=match):
        eu.parse_unit(text, kind=kind)


def test_gas_volume_and_steam_need_explicit_heat_content():
    with pytest.raises(ValueError, match="heat_content"):
        eu.convert(1.0, "Mcf", "kBtu")
    with pytest.raises(ValueError, match="enthalpy"):
        eu.convert(1.0, "klb", "kBtu")
    assert eu.convert(1.0, "Mcf", "therm", heat_content=HC) == pytest.approx(10.37)
    assert eu.convert(1.0, "CCF", "therm", heat_content="1,037 Btu/cf") == pytest.approx(1.037)
    assert eu.convert(1.0, "Mcf", "therm", heat_content={"value": 1.037, "unit": "MMBtu/Mcf"}) == (
        pytest.approx(10.37)
    )
    assert eu.convert(1.0, "m3", "kBtu", heat_content="1 kBtu/ft3") == (
        pytest.approx(35.3146667, rel=1e-8)
    )
    assert eu.convert(1.0, "klb", "kBtu", enthalpy="1000 Btu/lb") == pytest.approx(1000.0)
    with pytest.raises(ValueError, match="per mass"):
        eu.convert(1.0, "Mcf", "kBtu", heat_content="1000 Btu/lb")
    with pytest.raises(ValueError, match="positive"):
        eu.parse_heat_content("-3 therm/Mcf")
    with pytest.raises(ValueError, match="energy.*volume or mass"):
        eu.parse_heat_content("10.37 therm")
    with pytest.raises(ValueError):
        eu.parse_heat_content(10.37)


def test_rates_and_eui_convert_through_the_module():
    assert eu.convert_rate(1.20, "therm", "kBtu") == pytest.approx(0.012)
    assert eu.convert_rate(1.20, "therm", "kWh") == pytest.approx(1.20 / 100 * eu.KBTU_PER_KWH)
    assert eu.convert_rate(10.37, "Mcf", "therm", heat_content=HC) == pytest.approx(1.0)
    # a carbon factor: 5.30 kg CO2e/therm is 53.0 kg/MMBtu
    assert eu.convert_rate(5.30, "therm", "MMBtu") == pytest.approx(53.0)
    assert eu.convert_eui(100.0, "kBtu/ft²·yr", "kWh/m²·yr") == pytest.approx(315.459, rel=1e-5)
    assert eu.energy_unit_of_rate("MBH") == "kBtu" and eu.energy_unit_of_rate("kW") == "kWh"
    assert eu.format_energy(1234.4, "kBtu") == "1,234 kBtu" and eu.format_energy(3.0) == "3"


def test_unit_system_from_config():
    assert eu.UnitSystem.from_config({}) is None
    ip = eu.UnitSystem.from_config({"units": {"system": "IP"}})
    assert (ip.energy, ip.power, ip.eui, ip.area) == ("kBtu", "kBtu/h", "kBtu/ft2/yr", "ft2")
    si = eu.UnitSystem.from_config({"units": "si"})
    assert (si.energy, si.power, si.eui, si.area) == ("kWh", "kW", "kWh/m2/yr", "m2")
    assert eu.UnitSystem.from_config({"units": {"system": "si", "area": "sq ft"}}).area == "ft2"
    assert si.as_dict()["system"] == "si" and si.power_factor("kBtu/h") == pytest.approx(
        0.29307, rel=1e-5
    )
    for bad in ({"system": "metric"}, {"sytem": "si"}, {"system": "si", "extra": 1}):
        with pytest.raises(ValueError):
            eu.UnitSystem.from_config({"units": bad})
