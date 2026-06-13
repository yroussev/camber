"""Tests for the optional LBNL BETTER bridge (camber.interop.better).

better-lbnl-os is an optional dependency ([better] extra); these tests skip cleanly when
it isn't installed and otherwise cross-check CAMBER's change-point against BETTER's.
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.interop import better  # noqa: E402


def _heat_cool(n=24):
    """A monthly-ish 5P signal: baseload + heating below 55F + cooling above 65F."""
    rng = np.random.default_rng(0)
    T = np.linspace(20, 100, n)
    y = 50 + 1.5 * np.maximum(0, 55 - T) + 1.2 * np.maximum(0, T - 65) + rng.normal(0, 1, n)
    return T, y


def test_helpful_error_without_better():
    try:
        import better_lbnl_os  # noqa: F401
        have = True
    except Exception:  # noqa: BLE001
        have = False
    if have:
        pytest.skip("better-lbnl-os installed; covered by the smoke test")
    with pytest.raises(ImportError, match=r"camber-toolkit\[better\]"):
        better.fit_changepoint(*_heat_cool())


def test_cross_check_if_installed():
    pytest.importorskip("better_lbnl_os")
    T, y = _heat_cool()
    out = better.compare_changepoint(T, y)
    assert "camber" in out and "better" in out and "agreement" in out
    assert out["camber"]["kind"]                      # CAMBER always fits
    assert "order_match" in out["agreement"]
