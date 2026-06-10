"""Tests for the optional NREL PySAM tariff bridge (camber.interop.tariff_nrel).

PySAM is a large optional dependency ([tariff] extra); these tests skip cleanly when it
isn't installed, and otherwise smoke-test the bridge end to end.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.interop import tariff_nrel  # noqa: E402


def test_helpful_error_without_pysam():
    # importable regardless; calling without PySAM installed must give a clear message
    pytest.importorskip  # noqa: B018
    try:
        import PySAM.Utilityrate5  # noqa: F401
        have_pysam = True
    except Exception:  # noqa: BLE001
        have_pysam = False
    if have_pysam:
        pytest.skip("PySAM installed; covered by the smoke test below")
    with pytest.raises(ImportError, match=r"camber\[tariff\]"):
        tariff_nrel.bill_with_pysam({"label": "x"}, [1.0] * 8760)


def test_pysam_smoke_if_installed():
    pytest.importorskip("PySAM.Utilityrate5")
    urdb = {
        "label": "x", "name": "flat", "fixedchargefirstmeter": 0.0,
        "fixedchargeunits": "$/month",
        "energyratestructure": [[{"rate": 0.12}]],
        "energyweekdayschedule": [[0] * 24 for _ in range(12)],
        "energyweekendschedule": [[0] * 24 for _ in range(12)],
    }
    out = tariff_nrel.bill_with_pysam(urdb, [1.0] * 8760)
    assert "annual_bill" in out and out["annual_bill"] >= 0
