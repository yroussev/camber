"""Energy units in config M&V on bills (#69): IP and SI agree after conversion, the fits stay in the
meter's unit, gas volumes need a heat content, and without a ``units`` block nothing changes.

Synthetic bills only.
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber import energy_units as eu  # noqa: E402
from camber.config import run_config  # noqa: E402
from camber.mandv.billing import BillingSeries  # noqa: E402

HC = "10.37 therm/Mcf"

# --------------------------------------------------------------------------- billing


def _bills(tmp_path, *, unit="therm", scale=1.0, name="gas.csv", mixed=False, seed=3):
    rng = np.random.default_rng(seed)
    days = pd.date_range("2020-01-01", "2023-12-31", freq="D")
    T = 52 - 24 * np.cos(2 * np.pi * (days.dayofyear - 15) / 365.25) + rng.normal(0, 5, len(days))
    use = 30 + 5.0 * np.maximum(0, 60 - T) + rng.normal(0, 10, len(days))
    use = np.where(days >= "2023-01-01", use * 0.85, use)
    rows, i = [], 0
    while i < len(days):
        j = min(i + int(rng.integers(28, 34)), len(days))
        rows.append(
            {
                "start": days[i].date(),
                "end": days[j - 1].date(),
                "energy": round(float(use[i:j].sum()) * scale, 3),
                "units": unit,
            }
        )
        i = j
    if mixed:
        rows[3]["units"] = "kWh"
    pd.DataFrame(rows).to_csv(tmp_path / name, index=False)
    pd.DataFrame({"timestamp": days, "oat": np.round(T, 2)}).to_csv(
        tmp_path / "oat.csv", index=False
    )


def _cfg(units=None, bills=None, **entry):
    e = {
        "bills": bills or {"file": "gas.csv"},
        "name": "Gas",
        "period": ["2020-01-01", "2022-12-31"],
        "reporting_period": ["2023-01-01", "2023-12-31"],
        "method": "forecast",
        "adjustments": [
            {
                "kind": "nra",
                "method": "engineering",
                "start": "2023-06-01",
                "amount": 90.0,
                "se": 20.0,
                "reason": "a new load",
                "evidence": "log",
            },
        ],  # fmt: skip
    }
    e.update(entry)
    cfg = {"site": "Demo", "shared_oat": {"file": "oat.csv"}, "mv": [e]}
    if units is not None:
        cfg["units"] = units
    return cfg


def _sav(res):
    return next(f for f in res.findings if f.rule == "mv_savings")


def test_billing_default_output_is_unchanged(tmp_path):
    _bills(tmp_path)
    res = run_config(_cfg(), base_dir=str(tmp_path))
    for f in res.findings:
        assert not {"energy_unit", "unit_system", "meter_unit"} & set(f.metrics)
    s = _sav(res)
    assert "kBtu" not in s.summary and "kWh" not in s.summary
    assert s.summary.startswith(f"Gas: avoided energy {s.metrics['avoided_energy']:,.0f} (")


def test_billing_ip_and_si_agree_after_conversion(tmp_path):
    _bills(tmp_path)
    ip = _sav(run_config(_cfg({"system": "ip"}), base_dir=str(tmp_path)))
    si = _sav(run_config(_cfg({"system": "si"}), base_dir=str(tmp_path)))
    plain = _sav(run_config(_cfg(), base_dir=str(tmp_path)))
    assert ip.metrics["energy_unit"] == "kBtu" and si.metrics["energy_unit"] == "kWh"
    assert ip.metrics["meter_unit"] == "therm" and ip.metrics["unit_system"] == "ip"
    k = eu.convert(1.0, "kBtu", "kWh")
    for key in ("avoided_energy", "baseline_projected", "reporting_actual", "abs_uncertainty"):
        assert ip.metrics[key] == pytest.approx(plain.metrics[key] * 100.0, abs=0.01)
        assert si.metrics[key] == pytest.approx(ip.metrics[key] * k, rel=1e-6)
    for key in ("adjusted_savings", "adjusted_abs_uncertainty", "adjusted_baseline"):
        assert si.metrics[key] == pytest.approx(ip.metrics[key] * k, rel=1e-6)
    assert ip.metrics["savings_pct"] == si.metrics["savings_pct"] == plain.metrics["savings_pct"]
    assert ip.metrics["waterfall"][0]["value"] == pytest.approx(
        plain.metrics["waterfall"][0]["value"] * 100.0
    )
    led = ip.metrics["adjustments"][0]
    assert led["resolved_amount"] == pytest.approx(
        plain.metrics["adjustments"][0]["resolved_amount"] * 100.0
    )
    assert f"{ip.metrics['avoided_energy']:,.0f} kBtu (" in ip.summary
    assert f"± {si.metrics['abs_uncertainty']:,.0f} kWh at" in si.summary
    assert si.summary.endswith(f"± {si.metrics['adjusted_abs_uncertainty']:,.0f} kWh")


def test_billing_other_methods_and_proposal_convert(tmp_path):
    _bills(tmp_path)
    kw = dict(method="chaining", intermediate_period=["2022-01-01", "2022-12-31"],
              period=["2021-01-01", "2021-12-31"], adjustments=[])  # fmt: skip
    plain = _sav(run_config(_cfg(**kw), base_dir=str(tmp_path)))
    si = _sav(run_config(_cfg({"system": "si"}, **kw), base_dir=str(tmp_path)))
    k = eu.convert(1.0, "therm", "kWh")
    assert si.metrics["savings"] == pytest.approx(plain.metrics["savings"] * k, abs=0.01)
    ln0, pl0 = si.metrics["links"][0], plain.metrics["links"][0]
    assert ln0["savings"] == pytest.approx(pl0["savings"] * k, abs=0.01)
    for t, v in pl0["sep_terms"].items():
        assert ln0["sep_terms"][t] == pytest.approx(v * k, abs=0.01)
    assert f"chaining savings {si.metrics['savings']:,.0f} kWh" in si.summary
    res = run_config(_cfg({"system": "ip"}, method="auto", adjustments=[]), base_dir=str(tmp_path))
    p = next(f for f in res.findings if f.rule == "mv_method_proposal")
    res0 = run_config(_cfg(method="auto", adjustments=[]), base_dir=str(tmp_path))
    p0 = next(f for f in res0.findings if f.rule == "mv_method_proposal")
    assert p.metrics["energy_unit"] == "kBtu"
    assert p.metrics["sensitivity"][0]["savings"] == pytest.approx(
        p0.metrics["sensitivity"][0]["savings"] * 100.0, abs=0.01
    )


def test_billing_gas_in_mcf_needs_heat_content_and_matches_therms(tmp_path):
    _bills(tmp_path)  # therms
    _bills(tmp_path, unit="Mcf", scale=1 / 10.37, name="gas_mcf.csv")
    therm = _sav(run_config(_cfg({"system": "ip"}), base_dir=str(tmp_path)))
    with pytest.raises(ValueError, match="heat_content"):
        run_config(_cfg({"system": "ip"}, bills={"file": "gas_mcf.csv"}), base_dir=str(tmp_path))
    b = {"file": "gas_mcf.csv", "heat_content": HC}
    mcf = _sav(run_config(_cfg({"system": "ip"}, bills=b, adjustments=[]), base_dir=str(tmp_path)))
    therm0 = _sav(run_config(_cfg({"system": "ip"}, adjustments=[]), base_dir=str(tmp_path)))
    assert mcf.metrics["meter_unit"] == "Mcf"
    assert mcf.metrics["avoided_energy"] == pytest.approx(
        therm0.metrics["avoided_energy"], rel=1e-3
    )
    assert mcf.metrics["savings_pct"] == pytest.approx(therm0.metrics["savings_pct"], abs=1e-3)
    assert therm.metrics["meter_unit"] == "therm"
    # without a unit system the Mcf bills run in Mcf, as before (heat content only validated)
    raw = _sav(run_config(_cfg(bills=b, adjustments=[]), base_dir=str(tmp_path)))
    assert "energy_unit" not in raw.metrics
    with pytest.raises(ValueError, match="heat_content"):
        run_config(
            _cfg(bills={"file": "gas_mcf.csv", "heat_content": "10 therm"}), base_dir=str(tmp_path)
        )


def test_billing_refusals(tmp_path):
    _bills(tmp_path, mixed=True, name="mixed.csv")
    with pytest.raises(ValueError, match="mixes energy units"):
        run_config(_cfg({"system": "si"}, bills={"file": "mixed.csv"}), base_dir=str(tmp_path))
    _bills(tmp_path, unit="MBtu", name="amb.csv")
    with pytest.raises(ValueError, match="ambiguous"):
        run_config(_cfg({"system": "si"}, bills={"file": "amb.csv"}), base_dir=str(tmp_path))
    _bills(tmp_path)
    pd.read_csv(tmp_path / "gas.csv").drop(columns="units").to_csv(
        tmp_path / "nou.csv", index=False
    )
    with pytest.raises(ValueError, match="name no unit"):
        run_config(_cfg({"system": "si"}, bills={"file": "nou.csv"}), base_dir=str(tmp_path))
    ok = run_config(_cfg({"system": "si"}, bills={"file": "nou.csv", "units": "therms"}),
                    base_dir=str(tmp_path))  # fmt: skip
    assert _sav(ok).metrics["meter_unit"] == "therm"
    with pytest.raises(ValueError, match="units.system"):
        run_config(_cfg({"system": "metric"}), base_dir=str(tmp_path))


def test_billing_series_converted():
    f = pd.DataFrame({"start": ["2024-01-01"], "end": ["2024-02-01"], "energy": [10.0]})
    bs = BillingSeries(f, units="Mcf")
    out = bs.converted("kBtu", heat_content=HC)
    assert out.units == "kBtu" and out.frame["energy"].iloc[0] == pytest.approx(10370.0)
    with pytest.raises(ValueError, match="heat_content"):
        bs.converted("kBtu")
    with pytest.raises(ValueError, match="name no unit"):
        BillingSeries(f).converted("kWh")


def test_trended_mv_entry_must_name_its_rate_unit_under_a_system():
    from camber.config import _mv_trended_units

    us = eu.UnitSystem.of("ip")
    assert _mv_trended_units({"class": "Meter"}, None) is None
    with pytest.raises(ValueError, match="rate unit"):
        _mv_trended_units({"class": "Meter"}, us)
    k, unit, meter, system = _mv_trended_units({"class": "Meter", "units": "kW"}, us)
    assert (unit, meter, system) == ("kBtu", "kWh", "ip") and k == pytest.approx(eu.KBTU_PER_KWH)
    with pytest.raises(ValueError, match="rate"):
        _mv_trended_units({"class": "Meter", "units": "kWh"}, us)
    with pytest.raises(ValueError):
        _mv_trended_units({"class": "Meter", "units": "kWh"}, None)


# --------------------------------------------------------------------------- trended gas volume


def _gas_store(tmp_path, *, scale=1.0):
    """A trended gas meter in cfh (x ``scale``) on a store, with its outdoor temperature."""
    import json

    from camber.model.roles import Role
    from camber.portfolio import Portfolio

    ws = str(tmp_path / "ws")
    pf = Portfolio.init(ws)
    pf.add_facility("Gas site", facility_id="g1", reason="test", activate=True)
    rng = np.random.default_rng(5)
    idx = pd.date_range("2021-01-01", "2022-12-31 23:00", freq="1h")
    T = 52 - 24 * np.cos(2 * np.pi * (idx.dayofyear - 15) / 365.25) + rng.normal(0, 3, len(idx))
    cfh = 200 + 12.0 * np.maximum(0, 60 - T) + rng.normal(0, 20, len(idx))
    cfh = np.where(idx >= "2022-01-01", cfh * 0.9, cfh)
    frame = pd.DataFrame({Role.ENERGY_RATE: cfh * scale, Role.OAT: T}, index=idx)
    pf.store.write_role_frame(frame, facility_id="g1", equip="gas", equip_class="GAS_METER")
    cfg = {
        "source": {"kind": "store", "store": os.path.join(ws, "store"), "facility_id": "g1"},
        "equipment": [{"class": "GAS_METER"}],
        "mv": [
            {
                "class": "GAS_METER",
                "period": ["2021-01-01", "2021-12-31"],
                "reporting_period": ["2022-01-01", "2022-12-31"],
                "method": "forecast",
            }
        ],
    }
    return json.loads(json.dumps(cfg))


def _gas_saving(cfg, system=None, **entry):
    c = {**cfg, "mv": [{**cfg["mv"][0], **entry}]}
    if system:
        c["units"] = system
    return next(f for f in run_config(c).findings if f.rule == "mv_savings")


def test_trended_gas_volume_flow_converts_with_its_heat_content(tmp_path):
    cfg = _gas_store(tmp_path)
    raw = _gas_saving(cfg, None, units="cfh", heat_content="1037 Btu/ft3")
    assert "energy_unit" not in raw.metrics  # no system: ft3, exactly as before
    ip = _gas_saving(cfg, {"system": "ip"}, units="cfh", heat_content="1037 Btu/ft3")
    si = _gas_saving(cfg, {"system": "si"}, units="cfh", heat_content="1037 Btu/ft3")
    assert ip.metrics["meter_unit"] == "ft3" and ip.metrics["energy_unit"] == "kBtu"
    assert ip.metrics["avoided_energy"] == pytest.approx(
        raw.metrics["avoided_energy"] * 1.037, 1e-4
    )
    k = eu.energy_factor("kBtu", "kWh")
    assert si.metrics["avoided_energy"] == pytest.approx(ip.metrics["avoided_energy"] * k, 1e-4)
    assert any("1037 Btu/ft3" in c for c in ip.caveats)
    assert ip.metrics["savings_pct"] == raw.metrics["savings_pct"]


def test_trended_gas_in_m3h_matches_cfh(tmp_path):
    ft3 = _gas_store(tmp_path / "a")
    m3 = _gas_store(tmp_path / "b", scale=0.3048**3)  # the same gas, metered in m3/h
    a = _gas_saving(ft3, {"system": "si"}, units="cfh", heat_content="1037 Btu/ft3")
    b = _gas_saving(m3, {"system": "si"}, units="m3/h", heat_content="1037 Btu/ft3")
    assert b.metrics["meter_unit"] == "m3"
    assert b.metrics["avoided_energy"] == pytest.approx(a.metrics["avoided_energy"], rel=1e-6)


def test_trended_gas_from_a_factor_set(tmp_path):
    cfg = _gas_store(tmp_path)
    system = {"system": "ip", "factor_set": "energy_star_thermal_2015", "region": "US"}
    got = _gas_saving(cfg, system, units="cfh")
    assert got.metrics["energy_factor"]["meter_type"] == "natural_gas"
    raw = _gas_saving(cfg, None, units="cfh")
    assert got.metrics["avoided_energy"] == pytest.approx(
        raw.metrics["avoided_energy"] * 1.026, 1e-4
    )
    assert any("energy_star_thermal_2015" in c for c in got.caveats)


@pytest.mark.parametrize(
    "units,entry,msg",
    [
        ({"system": "ip"}, {"units": "cfh"}, "gas volume flow: give mv.heat_content"),
        (None, {"units": "kW", "heat_content": "1037 Btu/ft3"}, "for a gas meter"),
        (None, {"units": "cfh", "heat_content": "1000 Btu/lb"}, "per mass"),
        (None, {"units": "cfh", "meter_type": "natural_gas"}, "only with a units.factor_set"),
        ({"system": "si"}, {"units": "kWh"}, "a trended meter's units are its rate"),
    ],
)
def test_trended_gas_refusals(units, entry, msg):
    from camber.config import _mv_trended_conversion

    us = None if units is None else eu.UnitSystem.from_config({"units": units})
    with pytest.raises(ValueError, match=msg):
        _mv_trended_conversion({"class": "M", **entry}, us)
