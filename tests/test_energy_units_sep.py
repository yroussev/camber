"""Energy units in SEP primary energy (#69): delivered physical units are converted to energy before
the Annex B multipliers; IP and SI agree after conversion.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber import energy_units as eu  # noqa: E402
from camber.mandv.methods import MethodResult  # noqa: E402
from camber.mandv.sep import aggregate_energy_types, primary_energy  # noqa: E402

HC = "10.37 therm/Mcf"

# --------------------------------------------------------------------------- SEP


def _mr(terms, band=10.0):
    return MethodResult(
        method="forecast",
        basis="x",
        kernel="g14",
        savings=1.0,
        projected=None,
        measured=None,
        savings_pct=None,
        fractional_uncertainty=None,
        abs_uncertainty=band,
        confidence=0.90,
        df=100,
        sep_terms=terms,
    )


def test_primary_energy_converts_physical_units_before_annex_b():
    d = {"grid_electricity": 1000.0, "natural_gas": 100.0}
    old = primary_energy(d)
    assert set(old.as_dict()) == {"total", "by_type", "multipliers", "caveats"}  # unchanged
    u = {"grid_electricity": "kWh", "natural_gas": "Mcf"}
    hc = {"natural_gas": HC}
    ip = primary_energy(d, units=u, heat_content=hc, energy_unit="kBtu")
    assert ip.unit == "kBtu" and ip.delivered_units == u
    assert ip.by_type["grid_electricity"] == pytest.approx(3.0 * 1000 * eu.KBTU_PER_KWH)
    assert ip.by_type["natural_gas"] == pytest.approx(1.0 * 100 * 1037.0)
    assert any("heat content" in c for c in ip.caveats)
    si = primary_energy(d, units=u, heat_content=hc)  # kWh by default
    assert si.total == pytest.approx(eu.convert(ip.total, "kBtu", "kWh"))
    json.dumps(si.as_dict())
    with pytest.raises(ValueError, match="heat_content"):
        primary_energy(d, units=u)
    with pytest.raises(ValueError, match="no unit"):
        primary_energy(d, units={"grid_electricity": "kWh"})
    with pytest.raises(ValueError, match="ambiguous"):
        primary_energy(d, units={"grid_electricity": "kWh", "natural_gas": "MBtu"})


def test_aggregate_energy_types_ip_and_si_agree():
    elec = _mr({"adjusted_baseline": 1000.0, "observed_reporting": 900.0})
    gas = _mr({"adjusted_baseline": 50.0, "observed_reporting": 48.0}, band=2.0)
    r = {"grid_electricity": elec, "natural_gas": gas}
    u, hc = {"grid_electricity": "kWh", "natural_gas": "Mcf"}, {"natural_gas": HC}
    plain = aggregate_energy_types(r)
    assert "unit" not in plain.as_dict()
    ip = aggregate_energy_types(r, units=u, heat_content=hc, energy_unit="kBtu")
    si = aggregate_energy_types(r, units=u, heat_content=hc, energy_unit="kWh")
    k = eu.convert(1.0, "kBtu", "kWh")
    assert ip.unit == "kBtu" and si.unit == "kWh"
    assert si.esp_td == pytest.approx(ip.esp_td * k)
    assert si.esp_td_uncertainty == pytest.approx(ip.esp_td_uncertainty * k)
    assert si.senpi == pytest.approx(ip.senpi)
    assert ip.multipliers == plain.multipliers  # Annex B multipliers themselves unchanged
    base = 3.0 * 1000 * eu.KBTU_PER_KWH + 50 * 1037.0
    assert ip.terms["adjusted_baseline"] == pytest.approx(base)
