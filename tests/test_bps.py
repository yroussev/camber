"""Tests for Building Performance Standards compliance (camber.bps)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.bps import (  # noqa: E402
    BPSStandard, assess_bps, emissions_intensity,
)


# --- assess_bps: compliant ---------------------------------------------------- #

def test_compliant_building_has_margin_and_no_penalty():
    std = BPSStandard(name="EUI cap", metric="eui", limit=100.0,
                      unit="kBtu/ft2-yr", penalty_per_unit_over=5.0)
    r = assess_bps(80.0, std)
    assert r.compliant is True
    assert r.verdict == "compliant"
    assert abs(r.margin - 20.0) < 1e-6        # 100 - 80
    assert r.over_amount == 0.0
    assert r.penalty == 0.0
    assert abs(r.pct_of_limit - 80.0) < 1e-6


# --- assess_bps: over --------------------------------------------------------- #

def test_over_building_computes_over_amount_and_penalty():
    std = BPSStandard(name="Emissions cap", metric="emissions", limit=10.0,
                      unit="kgCO2e/ft2-yr", penalty_per_unit_over=268.0)
    r = assess_bps(13.0, std)
    assert r.compliant is False
    assert r.verdict == "over"
    assert abs(r.over_amount - 3.0) < 1e-6        # 13 - 10
    assert abs(r.margin - (-3.0)) < 1e-6
    # penalty = 3 units over * $268/unit = $804
    assert abs(r.penalty - 804.0) < 1e-6


def test_pct_of_limit_at_and_over_limit():
    std = BPSStandard(name="cap", metric="eui", limit=50.0)
    at = assess_bps(50.0, std)                     # exactly at the limit -> compliant
    assert at.compliant is True
    assert abs(at.pct_of_limit - 100.0) < 1e-6
    assert at.over_amount == 0.0
    over = assess_bps(75.0, std)
    assert abs(over.pct_of_limit - 150.0) < 1e-6   # 75 / 50


def test_bad_limit_returns_none():
    assert assess_bps(80.0, BPSStandard(name="x", metric="eui", limit=0.0)) is None
    assert assess_bps(80.0, BPSStandard(name="x", metric="eui", limit=-5.0)) is None


# --- emissions_intensity ------------------------------------------------------ #

def test_emissions_intensity_two_fuel():
    # 100,000 kWh electricity @ 0.4 kgCO2e/kWh = 40,000 kgCO2e
    # 5,000 therms gas @ 5.3 kgCO2e/therm = 26,500 kgCO2e
    # total 66,500 kgCO2e over 10,000 ft2 = 6.65 kgCO2e/ft2-yr
    energy = {"electricity": 100_000.0, "natural_gas": 5_000.0}
    factors = {"electricity": 0.4, "natural_gas": 5.3}
    ei = emissions_intensity(energy, factors, area_sqft=10_000.0)
    assert abs(ei - 6.65) < 1e-6


def test_emissions_intensity_missing_factor_contributes_zero():
    energy = {"electricity": 1_000.0, "steam": 500.0}   # no steam factor supplied
    factors = {"electricity": 0.4}
    ei = emissions_intensity(energy, factors, area_sqft=1_000.0)
    assert abs(ei - 0.4) < 1e-6                          # only electricity counts


def test_emissions_intensity_nonpositive_area_is_nan():
    import math
    ei = emissions_intensity({"electricity": 1.0}, {"electricity": 0.4}, area_sqft=0.0)
    assert math.isnan(ei)


def test_emissions_intensity_feeds_assess_bps():
    energy = {"electricity": 100_000.0, "natural_gas": 5_000.0}
    factors = {"electricity": 0.4, "natural_gas": 5.3}
    ei = emissions_intensity(energy, factors, area_sqft=10_000.0)   # 6.65
    std = BPSStandard(name="cap", metric="emissions", limit=5.0,
                      unit="kgCO2e/ft2-yr", penalty_per_unit_over=268.0)
    r = assess_bps(ei, std)
    assert r.verdict == "over"
    assert abs(r.over_amount - 1.65) < 1e-6
