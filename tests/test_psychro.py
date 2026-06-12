"""Tests for the optional PsychroLib bridge (camber.interop.psychro).

psychrolib is an optional dependency ([psychro] extra); these tests skip cleanly when it
isn't installed and otherwise cross-check CAMBER's Stull wet-bulb against PsychroLib's exact.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.interop import psychro  # noqa: E402


def _have():
    try:
        import psychrolib  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def test_helpful_error_without_psychrolib():
    if _have():
        pytest.skip("psychrolib installed; covered by the cross-check test")
    with pytest.raises(ImportError, match=r"camber\[psychro\]"):
        psychro.wet_bulb_f(95.0, 30.0)


def test_wetbulb_cross_check_if_installed():
    pytest.importorskip("psychrolib")
    out = psychro.compare_wetbulb(95.0, 30.0)            # hot/dry, like CZ15
    assert {"stull_f", "psychrolib_f", "abs_diff_f"} <= out.keys()
    assert 60.0 < out["psychrolib_f"] < 80.0             # physically sane wet-bulb
    assert out["abs_diff_f"] < 2.0                       # Stull tracks exact to ~±1-2 °F


def test_psychrometrics_state_if_installed():
    pytest.importorskip("psychrolib")
    st = psychro.psychrometrics(95.0, 30.0)
    assert {"wet_bulb_f", "dew_point_f", "humidity_ratio", "enthalpy_btu_per_lb"} <= st.keys()
    assert st["dew_point_f"] < 95.0                      # dew point below dry-bulb
    assert 0.0 < st["humidity_ratio"] < 0.05
    assert st["wet_bulb_f"] < 95.0                       # wet-bulb below dry-bulb when RH<100
