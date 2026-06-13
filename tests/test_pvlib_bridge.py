"""Tests for the optional pvlib bridge (camber.interop.pvlib_bridge).

pvlib is an optional dependency ([pv] extra); these tests skip cleanly when it isn't
installed and otherwise exercise transposition + the temperature-aware yield comparison.
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.interop import pvlib_bridge as pvb  # noqa: E402


def _have():
    try:
        import pvlib  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def test_helpful_error_without_pvlib():
    if _have():
        pytest.skip("pvlib installed; covered by the smoke tests")
    with pytest.raises(ImportError, match=r"camber-toolkit\[pv\]"):
        pvb.pvwatts_expected_kwh(6.0, 100.0)


def test_poa_from_ghi_if_installed():
    pytest.importorskip("pvlib")
    # midday sun, south-facing 20° tilt; transposition returns a sane POA near the GHI scale
    poa = pvb.poa_from_ghi([800.0], [700.0], [120.0], solar_zenith=[35.0],
                           solar_azimuth=[180.0], surface_tilt=20.0, surface_azimuth=180.0)
    assert poa.shape == (1,)
    assert 600.0 < poa[0] < 1100.0
    # a steeper tilt toward the off-zenith sun collects more than a flat panel does
    flat = pvb.poa_from_ghi([800.0], [700.0], [120.0], solar_zenith=[35.0],
                            solar_azimuth=[180.0], surface_tilt=0.0, surface_azimuth=180.0)
    assert poa[0] > flat[0]


def test_compare_expected_temperature_derate_if_installed():
    pytest.importorskip("pvlib")
    out = pvb.compare_expected(6.0, 100.0, cell_temp_c=55.0)   # hot cell -> derated
    assert {"camber_kwh", "pvlib_kwh", "ratio", "cell_temp_c"} <= out.keys()
    assert out["pvlib_kwh"] > 0
    # at 55 °C cell temp the pvlib yield carries a real derate vs the flat-PR estimate
    assert out["ratio"] < 1.0
