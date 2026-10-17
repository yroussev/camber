"""The excess-outside-air cost estimator (0.107, #195) on synthetic AHUs with a known excess."""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.fault_economics import EnergyPrice, EquipmentLoad, estimate_cost  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.oafraction import excess_oa_penalty, humidity_ratio, moist_air_enthalpy  # noqa: E402
from camber.rules.economizer_lockout_rule import EconomizerHighLimit  # noqa: E402
from camber.rules.oafraction_rule import OutdoorAirFraction  # noqa: E402
from camber.rules.triage import link_findings  # noqa: E402
from camber.schedules import occupied_mask  # noqa: E402

PRICE = EnergyPrice(electricity_per_kwh=0.15, gas_per_therm=1.20)
WEEK = pd.date_range("2026-07-06", periods=168, freq="h")  # a Monday
OCC_H = int(np.asarray(occupied_mask(WEEK)).sum())  # occupied hours in one week (assumed schedule)


def _frame(idx, oat, rat, oaf, sat, **extra):
    """An always-running AHU whose mixed air is exactly ``oaf`` outside air."""
    oat, rat, oaf = (np.broadcast_to(np.asarray(v, float), len(idx)) for v in (oat, rat, oaf))
    cols = {
        Role.OAT: oat,
        Role.RETURN_AIR_TEMP: rat,
        Role.MIXED_AIR_TEMP: rat - oaf * (rat - oat),
        Role.SUPPLY_AIR_TEMP: np.broadcast_to(np.asarray(sat, float), len(idx)),
        Role.OA_DAMPER: np.full(len(idx), 50.0),
        Role.SUPPLY_FAN_STATUS: np.ones(len(idx)),
    }
    cols.update({k: np.broadcast_to(np.asarray(v, float), len(idx)) for k, v in extra.items()})
    return pd.DataFrame(cols, index=idx)


def _hot_week(**extra):
    # 90 F outside, 75 F return, 40 % OA against a declared 20 % minimum, coil cooling to 55 F
    return _frame(WEEK, 90.0, 75.0, 0.40, 55.0, **extra)


def _cooling_usd(cfm, *, observed_h=168.0, hours=OCC_H):
    """Hand computation: 1.08 x cfm x e x (OAT - RAT) Btu/h on the occupied hours, annualised."""
    kbtu = 1.08 * cfm * 0.20 * (90.0 - 75.0) * hours / 1e3
    kwh = kbtu * 8760.0 / observed_h / 3.412 / 3.5
    return kwh, kwh * 0.15


def test_psychrometrics_match_the_ashrae_tables():
    # ASHRAE Fundamentals table values: 75 F / 50 % RH -> W ~0.0093, h ~28.1 Btu/lb
    w = float(humidity_ratio(75.0, 50.0))
    assert w == pytest.approx(0.00924, abs=1e-4)
    assert float(moist_air_enthalpy(75.0, w)) == pytest.approx(28.1, abs=0.15)
    # a dew point gives the same state as the RH it implies (saturated at the dew point)
    assert float(humidity_ratio(60.0, dewpoint_f=60.0)) == pytest.approx(
        float(humidity_ratio(60.0, 100.0)), rel=1e-9
    )


def test_known_excess_costs_the_hand_computed_cooling_penalty():
    f = OutdoorAirFraction(min_oa_pct=20.0).analyze("AHU-1", _hot_week())
    assert f.severity == "fault"  # the verdict is the rule's, unchanged
    assert f.metrics["min_oa_source"] == "configured"
    assert f.metrics["oa_cost_cool_h"] == OCC_H  # occupied fan-on hours only
    assert f.metrics["oa_cost_excess_cool_btu_per_cfm"] == pytest.approx(1.08 * 0.2 * 15 * OCC_H)
    fc = estimate_cost(f, EquipmentLoad(supply_airflow_cfm=10_000.0), PRICE)
    kwh, usd = _cooling_usd(10_000.0)
    assert fc.costed and fc.basis == "excess-OA->cooling"
    assert fc.electricity_kwh == pytest.approx(kwh, abs=0.1) and fc.gas_therms == 0.0
    assert fc.annual_cost_usd == pytest.approx(usd, abs=0.01)
    a = fc.assumptions
    assert a["min_oa_source"] == "configured" and a["airflow_source"].endswith("supply_airflow_cfm")
    assert a["cooling_basis"].startswith("sensible") and a["cool_cop"] == 3.5


def test_design_oa_cfm_implies_the_supply_airflow():
    f = OutdoorAirFraction(min_oa_pct=20.0).analyze("AHU-1", _hot_week())
    # 2,000 cfm of design OA at a 20 % minimum = 10,000 cfm of supply air
    fc = estimate_cost(f, EquipmentLoad(design_oa_cfm=2_000.0), PRICE)
    assert fc.annual_cost_usd == pytest.approx(_cooling_usd(10_000.0)[1], abs=0.01)
    assert fc.assumptions["supply_airflow_cfm"] == 10_000.0


def test_trended_supply_airflow_needs_no_sizing():
    f = OutdoorAirFraction(min_oa_pct=20.0).analyze("AHU-1", _hot_week(**{Role.AIRFLOW: 8_000.0}))
    assert f.metrics["oa_cost_airflow"] == "trended supply airflow"
    fc = estimate_cost(f, None, PRICE)
    assert fc.costed and fc.annual_cost_usd == pytest.approx(_cooling_usd(8_000.0)[1], abs=0.01)


def test_enthalpy_penalty_when_humidity_is_trended():
    f = OutdoorAirFraction(min_oa_pct=20.0).analyze(
        "AHU-1",
        _hot_week(**{Role.OUTDOOR_RH: 50.0, Role.RETURN_AIR_HUMIDITY: 50.0}),
    )
    assert f.metrics["oa_cost_cool_basis"] == "enthalpy"
    dh = float(moist_air_enthalpy(90.0, humidity_ratio(90.0, 50.0))) - float(
        moist_air_enthalpy(75.0, humidity_ratio(75.0, 50.0))
    )
    want = 4.5 * 0.2 * dh * OCC_H
    assert f.metrics["oa_cost_excess_cool_btu_per_cfm"] == pytest.approx(want, rel=1e-4)
    # latent load: the enthalpy penalty is larger than the sensible one
    assert want > 1.08 * 0.2 * 15 * OCC_H


def test_heating_penalty_in_heating_hours_with_boiler_or_heat_pump():
    # week 1 hot (flags the unit); week 2 at 30 F, heating to 70 F with the same 40 % OA
    idx = pd.date_range("2026-07-06", periods=336, freq="h")
    hot = np.arange(336) < 168
    fr = _frame(idx, np.where(hot, 90.0, 30.0), np.where(hot, 75.0, 72.0), 0.40,
                np.where(hot, 55.0, 70.0))  # fmt: skip
    f = OutdoorAirFraction(min_oa_pct=20.0).analyze("AHU-1", fr)
    assert f.metrics["oa_cost_heat_h"] == OCC_H and f.metrics["oa_cost_heat_basis"] == (
        "supply_air_temp"
    )
    load = EquipmentLoad(supply_airflow_cfm=10_000.0)
    fc = estimate_cost(f, load, PRICE)
    # heating: 1.08 x 10,000 x 0.2 x (72 - 30) Btu/h on the cold week's occupied hours (the coil's
    # 70 - 55.2 = 14.8 F rise is above the 8.4 F the excess needs, so no cap)
    heat_kbtu_yr = 1.08 * 10_000 * 0.2 * 42.0 * OCC_H / 1e3 * 8760 / 336
    cool_kwh = _cooling_usd(10_000.0, observed_h=336.0)[0]
    assert fc.basis == "excess-OA->cooling+heating"
    assert fc.gas_therms == pytest.approx(heat_kbtu_yr / 100 / 0.80, abs=0.1)
    assert fc.electricity_kwh == pytest.approx(cool_kwh, abs=0.1)
    # a heat pump heats this unit: the heating is electricity at its COP, no gas
    hp = estimate_cost(f, load, PRICE, params={"heat_pump_cop": 2.5})
    assert hp.gas_therms == 0.0
    assert hp.electricity_kwh == pytest.approx(cool_kwh + heat_kbtu_yr / 3.412 / 2.5, abs=0.1)
    per_unit = EquipmentLoad(supply_airflow_cfm=10_000.0, heat_pump_cop=2.5)
    assert estimate_cost(f, per_unit, PRICE).annual_cost_usd == hp.annual_cost_usd


def test_economizing_excess_costs_zero():
    # excess OA (60 % against 20 %) at 55 F outside, 75 F return, coil cooling to 55 F: free cooling
    fr = _frame(WEEK, 55.0, 75.0, 0.60, 55.0)
    rule = EconomizerHighLimit(high_limit_f=50.0, min_oa_pct=20.0, differential=False)
    f = rule.analyze("AHU-1", fr)
    assert f.severity == "fault"  # not locked out above a 50 F limit: the verdict stands
    assert f.metrics["oa_cost_economizing_h"] == OCC_H
    fc = estimate_cost(f, EquipmentLoad(supply_airflow_cfm=10_000.0), PRICE)
    assert not fc.costed and fc.annual_cost_usd == 0.0
    assert "no penalty" in fc.basis


def test_economizing_week_adds_nothing_to_a_hot_week():
    idx = pd.date_range("2026-07-06", periods=336, freq="h")
    hot = np.arange(336) < 168
    fr = _frame(idx, np.where(hot, 90.0, 55.0), 75.0, np.where(hot, 0.40, 0.60), 55.0)
    f = OutdoorAirFraction(min_oa_pct=20.0).analyze("AHU-1", fr)
    assert f.metrics["oa_cost_economizing_h"] == OCC_H
    fc = estimate_cost(f, EquipmentLoad(supply_airflow_cfm=10_000.0), PRICE)
    assert fc.annual_cost_usd == pytest.approx(
        _cooling_usd(10_000.0, observed_h=336.0)[1], abs=0.01
    )


def test_helper_without_heating_evidence_does_not_guess():
    idx = WEEK[:20]
    cond = pd.DataFrame({"OAT": 40.0, "ReturnAir": 72.0}, index=idx)
    out = excess_oa_penalty(
        cond, pd.Series(50.0, index=idx), pd.Series(20.0, index=idx), margin_pct=5.0, observed_h=20
    )
    assert out["oa_cost_excess_heat_btu_per_cfm"] == 0.0 and out["oa_cost_heat_unjudged_h"] == 20
    assert out["oa_cost_heat_basis"].startswith("not judged")


def test_missing_inputs_decline_with_the_named_input():
    f = OutdoorAirFraction(min_oa_pct=20.0).analyze("AHU-1", _hot_week())
    fc = estimate_cost(f, None, PRICE)
    assert not fc.costed and fc.annual_cost_usd == 0.0
    assert fc.basis == "needs EquipmentLoad.supply_airflow_cfm or EquipmentLoad.design_oa_cfm"
    (issue,) = link_findings([f], costs=[fc])
    assert issue.cost is None
    assert issue.cost_basis_note == "uncosted — needs supply_airflow_cfm or design_oa_cfm"
    # the rule's generic 20 % minimum is judged on, never priced
    g = OutdoorAirFraction().analyze("AHU-1", _hot_week())
    assert g.severity == f.severity and g.metrics["min_oa_source"] == "default"
    gc = estimate_cost(g, EquipmentLoad(supply_airflow_cfm=10_000.0), PRICE)
    assert not gc.costed and gc.basis.startswith("needs min_oa_pct")
    # the lockout rule with no design minimum, or judged on damper position, declines too
    h = EconomizerHighLimit().analyze("AHU-1", _hot_week())
    assert estimate_cost(h, EquipmentLoad(supply_airflow_cfm=1e4)).basis.startswith(
        "needs min_oa_pct"
    )
    damper = _hot_week().drop(columns=[Role.MIXED_AIR_TEMP])
    d = EconomizerHighLimit(min_oa_pct=20.0).analyze("AHU-1", damper)
    assert d.metrics["basis"] == "damper position"
    assert "needs mixed_air_temp" in estimate_cost(d, EquipmentLoad(supply_airflow_cfm=1e4)).basis


def test_an_ok_verdict_is_not_priced():
    # winter only: heating excess, but no cooling-weather excess -> the rule reads ok
    fr = _frame(WEEK, 30.0, 72.0, 0.40, 70.0)
    f = OutdoorAirFraction(min_oa_pct=20.0).analyze("AHU-1", fr)
    assert f.severity == "ok" and f.metrics["oa_cost_heat_h"] > 0
    fc = estimate_cost(f, EquipmentLoad(supply_airflow_cfm=10_000.0), PRICE)
    assert not fc.costed and fc.annual_cost_usd == 0.0


def test_chain_cost_is_the_largest_member_not_the_sum():
    # hot afternoons only above the 85 F limit: the lockout prices fewer hours than the OA fraction
    oat = 80.0 + 10.0 * np.sin((WEEK.hour.to_numpy() - 9) / 24 * 2 * np.pi)
    fr = _frame(WEEK, oat, 75.0, 0.40, 55.0)
    a = OutdoorAirFraction(min_oa_pct=20.0).analyze("AHU-1", fr)
    b = EconomizerHighLimit(high_limit_f=85.0, min_oa_pct=20.0).analyze("AHU-1", fr)
    assert a.severity in ("warn", "fault") and b.severity in ("warn", "fault")
    load = EquipmentLoad(supply_airflow_cfm=10_000.0)
    ca, cb = estimate_cost(a, load, PRICE), estimate_cost(b, load, PRICE)
    assert ca.costed and cb.costed and ca.annual_cost_usd > cb.annual_cost_usd > 0
    (issue,) = link_findings([b, a], costs=[cb, ca])
    assert issue.chain == "econ"
    assert issue.cost == ca.annual_cost_usd != ca.annual_cost_usd + cb.annual_cost_usd
    assert "largest shown" in issue.cost_basis_note
    assert "outdoor_air_fraction" in issue.cost_basis_note


def test_rcx_report_prices_the_econ_issue_and_lists_its_assumptions(tmp_path):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _rcx_fixture as fx

    from camber.config import run_config
    from camber.report.rcx import build_rcx_report

    fx.make_store(tmp_path)
    cfg = fx.config(loads={"DemoAHU": {"supply_airflow_cfm": 10_000}})
    oaf = {"name": "outdoor_air_fraction", "params": {"min_oa_pct": 30.0}}
    cfg["rules"] = [oaf if r == "outdoor_air_fraction" else r for r in cfg["rules"]]
    run = run_config(cfg, base_dir=str(tmp_path))
    rep = build_rcx_report(run)
    (econ,) = [
        i for i in rep.to_dict()["issues"] if i["chain"] == "econ" and i["equip"] == "DemoAHU"
    ]
    load = EquipmentLoad(supply_airflow_cfm=10_000.0)
    member = [
        estimate_cost(f, load).annual_cost_usd
        for f in run.findings
        if f.equip == "DemoAHU" and f.rule in econ["rules"]
    ]
    assert len(member) >= 2 and econ["cost"] == max(member) > 0
    html = rep.to_html()
    assert "Excess outside air (0.107)" in html
    assert "30% (configured)" in html and "EquipmentLoad.supply_airflow_cfm (10,000 cfm)" in html
