"""Energy units in EUI, prices, cost annotations and the audit report (#69); the defaults are
unchanged.
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber import energy_units as eu  # noqa: E402
from camber.bps import site_eui, site_eui_units  # noqa: E402
from camber.fault_economics import EnergyPrice, annotate_costs  # noqa: E402
from camber.report.audit import AuditReport, Benchmark  # noqa: E402
from camber.rules.base import Finding  # noqa: E402

HC = "10.37 therm/Mcf"

# --------------------------------------------------------------------------- EUI


def test_site_eui_units_ip_and_si_agree():
    e = {"electricity": 1.0e6, "natural_gas": 2.0e4}
    u = {"electricity": "kWh", "natural_gas": "therm"}
    ip = site_eui_units(e, u, 50_000, area_unit="ft2")
    assert ip == pytest.approx((1.0e6 * eu.KBTU_PER_KWH + 2.0e6) / 50_000)
    assert site_eui(e, 50_000) == pytest.approx(108.24)  # the historical 3.412, unchanged
    si = site_eui_units(e, u, 50_000 * 0.09290304, area_unit="m2", system="si")
    assert si == pytest.approx(eu.convert_eui(ip, "kBtu/ft2/yr", "kWh/m2/yr"))
    gas_mcf = {"electricity": 1.0e6, "natural_gas": 2.0e4 / 10.37}
    u2 = {"electricity": "kWh", "natural_gas": "Mcf"}
    assert site_eui_units(
        gas_mcf, u2, 50_000, area_unit="ft2", heat_content={"natural_gas": HC}
    ) == pytest.approx(ip)
    with pytest.raises(ValueError, match="no unit"):
        site_eui_units(e, {"electricity": "kWh"}, 1.0, area_unit="ft2")
    with pytest.raises(ValueError, match="heat_content"):
        site_eui_units(gas_mcf, u2, 1.0, area_unit="ft2")
    assert np.isnan(site_eui_units(e, u, 0.0, area_unit="ft2"))


# --------------------------------------------------------------------------- prices, costs, report


def test_energy_price_per_unit_forms():
    assert EnergyPrice.from_dict({"electricity_per_kwh": 0.2, "other": 1}) == EnergyPrice(0.2)
    p = EnergyPrice.from_dict(
        {"electricity": {"rate": 95.0, "per": "MWh"},
         "gas": {"rate": 8.5, "per": "Mcf", "heat_content": HC}}
    )  # fmt: skip
    assert p.electricity_per_kwh == pytest.approx(0.095)
    assert p.gas_per_therm == pytest.approx(8.5 / 10.37)
    with pytest.raises(ValueError, match="heat_content"):
        EnergyPrice.from_dict({"gas": {"rate": 8.5, "per": "Mcf"}})
    with pytest.raises(ValueError, match="not both"):
        EnergyPrice.from_dict({"gas_per_therm": 1.0, "gas": {"rate": 1.0, "per": "therm"}})
    with pytest.raises(ValueError, match="rate"):
        EnergyPrice.from_dict({"gas": 1.0})


def test_annotate_costs_optionally_reports_site_energy():
    f = Finding(rule="x", equip="AHU-1", severity="warn", metrics={}, summary="")
    annotate_costs([f])
    assert "energy_unit" not in f.metrics
    annotate_costs([f], units="si")
    assert f.metrics["energy_unit"] == "kWh" and f.metrics["waste_energy"] == 0.0


def test_report_benchmark_label_default_and_si():
    rep = AuditReport(building="B", level=2)
    rep.benchmark = Benchmark(80.0, 100.0)
    assert "site EUI 80.0 kBtu/ft2/yr vs 100.0" in rep.to_text()
    assert "site EUI 80.0 kBtu/ft&sup2;/yr " in rep.to_html()
    from camber.config import _benchmark

    b = {"site_eui": 80.0, "peer_median_eui": 100.0}
    assert _benchmark(b, None) == Benchmark(80.0, 100.0)
    si = _benchmark(b, eu.UnitSystem.of("si"))
    assert si.unit == "kWh/m2/yr" and si.site_eui == pytest.approx(252.4, abs=0.05)
    rep.benchmark = si
    assert "kWh/m2/yr" in rep.to_text() and "kWh/m&sup2;/yr" in rep.to_html()
    assert rep.benchmark.pct_over == pytest.approx(-20.0, abs=0.1)
    m2 = _benchmark({**b, "unit": "kWh/m2/yr"}, eu.UnitSystem.of("ip"))
    assert m2.unit == "kBtu/ft2/yr" and m2.site_eui == pytest.approx(25.4, abs=0.05)
    with pytest.raises(ValueError):
        _benchmark({**b, "unit": "kWh/acre"}, None)
