"""0.108: sensor references for any point (#267), cost-only inputs and money at risk (#268), and
the SAT-reset target named by its source plus the excess-OA airflow on a decline (#269).

The cases run on the synthetic catalog entries built for them (Track C): ``synthetic-ahu-sensor-
bias`` (a supply-air sensor reading 3 F high, with a duct logger) and ``synthetic-ahu-excess-oa``.
"""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pandas as pd
import pytest

from camber import datasets
from camber.config import run_config
from camber.fault_economics import EquipmentLoad, cost_findings, estimate_cost
from camber.model.roles import Role
from camber.report import build_rcx_report
from camber.report.rcx import RcxOptions, _parse_references
from camber.rules.builtin import builtin_registry
from camber.rules.satreset_compliance_rule import SupplyAirResetCompliance
from camber.rules.triage import issue_totals, link_findings
from camber.store import ParquetStore

_BIAS_DS, _OA_DS = "synthetic-ahu-sensor-bias", "synthetic-ahu-excess-oa"


@pytest.fixture(scope="module")
def bias_store(tmp_path_factory):
    root = tmp_path_factory.mktemp("bias")
    datasets.ingest(_BIAS_DS, str(root / "store"), data_dir=str(root / "cache"))
    return str(root / "store")


@pytest.fixture(scope="module")
def oa_store(tmp_path_factory):
    root = tmp_path_factory.mktemp("oa")
    datasets.ingest(_OA_DS, str(root / "store"), data_dir=str(root / "cache"))
    return str(root / "store")


def _cfg(did, store, exercise=None):
    return datasets.config_template(did, store, exercise=exercise)


def _rcx(cfg, store):
    return build_rcx_report(run_config(cfg, base_dir=store))


def _issues(rep):
    return {(i["equip"], tuple(i["rules"])): i for i in rep.to_dict()["issues"]}


# ------------------------------------------------------------------------------------- #267


def test_parse_references_validates_and_aliases_the_site_oat(tmp_path):
    refs, oat = _parse_references(
        {
            "AHU-1": {"supply_air_temp": {"csv": "a.csv", "bias_warn": 1.0}},
            "*": {"oat": {"csv": "w.csv"}},
        },
        str(tmp_path),
    )
    assert refs["AHU-1"]["supply_air_temp"]["csv"] == str(tmp_path / "a.csv")
    assert oat == {"csv": str(tmp_path / "w.csv")}
    bad = [
        {"A": {"not_a_role": {"csv": "x"}}},
        {"A": {"supply_air_temp": {}}},
        {"A": {"supply_air_temp": {"csv": "x", "nope": 1}}},
        {"*": {"supply_air_temp": {"csv": "x"}}},
        {"A": ["supply_air_temp"]},
    ]
    for spec in bad:
        with pytest.raises(ValueError):
            _parse_references(spec, str(tmp_path))
    both = {"rcx": {"oat_reference": {"csv": "a"}, "references": {"*": {"oat": {"csv": "b"}}}}}
    with pytest.raises(ValueError, match="once"):
        RcxOptions.from_config(both)
    alias = RcxOptions.from_config({"rcx": {"references": {"*": {"oat": {"csv": "b.csv"}}}}})
    assert alias.oat_reference == {"csv": "b.csv"} or alias.oat_reference["csv"].endswith("b.csv")
    assert alias.references == {}


def test_a_store_equipment_reference_makes_the_biased_units_findings_conditional(bias_store):
    rep = _rcx(_cfg(_BIAS_DS, bias_store, "data-sensor-conditional"), bias_store)
    k = rep.to_dict()["kpis"]
    assert (k["annual_cost_usd"], k["at_risk_usd"], k["n_conditional"]) == (5230.31, 5249.61, 2)
    iss = _issues(rep)
    drift = iss[("AHU__sat_bias", ("sensor_drift:supply_air_temp",))]
    assert drift["severity"] == "warn"
    assert sorted(drift["dependents"]) == [
        "leaking_valve @ AHU__sat_bias",
        "supply_air_reset_compliance @ AHU__sat_bias",
    ]
    assert not iss[("AHU__healthy", ("supply_air_reset_compliance",))]["conditional_on"]
    html = rep.to_html()
    assert "Sensors vs their configured references" in html and "duct logger" in html


def test_without_references_nothing_is_conditional(bias_store):
    rep = _rcx(_cfg(_BIAS_DS, bias_store, "data-sensor-conditional--no-reference"), bias_store)
    k = rep.to_dict()["kpis"]
    assert (k["annual_cost_usd"], k["at_risk_usd"], k["n_conditional"]) == (10479.92, None, 0)
    assert "Sensors vs their configured references" not in rep.to_html()


def test_a_csv_reference_and_custom_thresholds(bias_store, tmp_path):
    logger = ParquetStore(bias_store).read_role_frame(
        facility_id="ds-synthetic-ahu-sensor-bias", equip="LOGGER__sat_bias"
    )[Role.SUPPLY_AIR_TEMP]
    pd.DataFrame({"time": logger.index, "f": logger.to_numpy()}).dropna().to_csv(
        tmp_path / "logger.csv", index=False
    )
    cfg = _cfg(_BIAS_DS, bias_store, "data-sensor-conditional--no-reference")
    cfg["report"]["rcx"]["references"] = {
        "AHU__sat_bias": {
            "supply_air_temp": {"csv": str(tmp_path / "logger.csv"), "bias_fault": 2.5}
        }
    }
    rep = _rcx(cfg, bias_store)
    drift = _issues(rep)[("AHU__sat_bias", ("sensor_drift:supply_air_temp",))]
    assert drift["severity"] == "fault"  # 3.0 F against a 2.5 F fault line
    assert rep.to_dict()["kpis"]["at_risk_usd"] == 5249.61


def test_a_reference_that_cannot_load_is_noted_and_raises_nothing(bias_store):
    cfg = _cfg(_BIAS_DS, bias_store, "data-sensor-conditional--no-reference")
    cfg["report"]["rcx"]["references"] = {
        "AHU__sat_bias": {"supply_air_temp": {"equip": "LOGGER__missing"}},
        "AHU__nope": {"supply_air_temp": {"equip": "LOGGER__sat_bias"}},
    }
    rep = _rcx(cfg, bias_store)
    assert rep.to_dict()["kpis"]["n_conditional"] == 0
    html = rep.to_html()
    assert "could not be loaded" in html and "no such equipment in this run" in html


# ------------------------------------------------------------------------------------- #268


def _oa_issue(oa_store, trust):
    run = run_config(_cfg(_OA_DS, oa_store, "air-excess-oa-cost"), base_dir=oa_store)
    issues = link_findings(run.findings, rules=builtin_registry(), trust=trust)
    return next(i for i in issues if i.equip == "AHU__excess_oa"), issues


def test_a_distrusted_cost_only_input_puts_the_dollars_at_risk_not_the_verdict(oa_store):
    bad = SimpleNamespace(verdict="untrusted", trust=0.2, flags=["stuck"])
    iss, issues = _oa_issue(oa_store, {"AHU__excess_oa": {"return_air_humidity": bad}})
    assert iss.cost == 730.51 and not iss.conditional and iss.cost_at_risk
    assert [c.roles for c in iss.cost_conditional_on] == [("return_air_humidity",)]
    assert iss.confidence != "L" and "Cost at risk" in iss.why[-1]
    t = issue_totals(issues)
    assert (t["annual_cost_usd"], t["at_risk_usd"], t["n_cost_conditional"]) == (0, 730.51, 1)
    assert t["n_conditional"] == 0


def test_a_trusted_cost_input_leaves_the_dollars_firm(oa_store):
    iss, issues = _oa_issue(oa_store, None)
    assert not iss.cost_conditional_on and not iss.cost_at_risk
    assert issue_totals(issues)["annual_cost_usd"] == 730.51


def test_the_report_shows_the_cost_at_risk_banner(oa_store, monkeypatch):
    import camber.sensorhealth as SH

    real = SH.frame_sensor_health

    def fake(frame, **kw):
        out = real(frame, **kw)
        for role in list(out):
            if role == Role.RETURN_AIR_HUMIDITY:
                t = copy.copy(out[role])
                t.verdict, t.trust = "untrusted", 0.2
                out[role] = t
        return out

    monkeypatch.setattr("camber.sensorhealth.frame_sensor_health", fake)
    rep = _rcx(_cfg(_OA_DS, oa_store, "air-excess-oa-cost"), oa_store)
    d = rep.to_dict()
    assert d["kpis"]["n_cost_conditional"] == 1 and d["kpis"]["n_conditional"] == 0
    assert d["kpis"]["at_risk_usd"] == 730.51 and d["kpis"]["annual_cost_usd"] is None
    i = _issues(rep)[("AHU__excess_oa", ("economizer_high_limit", "outdoor_air_fraction"))]
    assert i["cost_conditional_on"] and not i["conditional_on"]
    assert "Cost at risk pending a sensor fix (the verdict stands)" in rep.to_html()


# ------------------------------------------------------------------------------------- #269


def _frame():
    idx = pd.date_range("2025-05-12", periods=24 * 14, freq="1h")
    oat = pd.Series(62.0 + 8.0 * ((idx.hour - 6) % 24) / 24.0, index=idx)
    return pd.DataFrame({Role.OAT: oat, Role.SUPPLY_AIR_TEMP: 55.0}, index=idx)


@pytest.mark.parametrize(
    ("kwargs", "kind", "label"),
    [
        ({}, "g36", "G36"),
        ({"t_max": 62.0}, "configured", "configured"),
        ({"reset_source": "site sequence (report.rcx.sequence)"}, "site_sequence", "site-sequence"),
    ],
)
def test_the_reset_target_is_named_by_its_source(kwargs, kind, label):
    f = SupplyAirResetCompliance(fan_gate=False, occupied_only=False, **kwargs).analyze(
        "AHU", _frame()
    )
    m = f.metrics
    assert m["target_source"] == kind and f"vs {label} target" in f.summary
    # 0.109 (#272): the pre-0.108 *_g36_target aliases are gone
    assert {"pct_below_g36_target", "g36_target_median", "pct_above_g36_target"}.isdisjoint(m)
    load = EquipmentLoad(heating_capacity_kbtuh=400)
    c = estimate_cost(f, load)
    assert c.basis == f"sat-below-{kind.replace('_', '-')}-target->reheat", c.basis


def test_an_old_finding_with_only_the_removed_alias_does_not_price():
    """0.109 (#272): the pre-0.108 ``pct_below_g36_target`` key is no longer read."""
    f = SimpleNamespace(
        rule="supply_air_reset_compliance",
        equip="A",
        severity="warn",
        metrics={"pct_below_g36_target": 60.0, "mean_gap_f": 5.0},
    )
    c = estimate_cost(f, EquipmentLoad(heating_capacity_kbtuh=400))
    assert not c.costed


def test_appendix_b_names_the_airflow_on_a_missing_minimum_decline(oa_store):
    rep = _rcx(_cfg(_OA_DS, oa_store, "air-excess-oa-cost--no-min"), oa_store)
    sec = next(s for s in rep.to_dict()["sections"] if s["id"] == "appendix-b")
    rows = next(b for b in sec["blocks"] if b["kind"] == "table" and "Minimum OA" in b["header"])
    by = {r[0]: r for r in rows["rows"]}
    assert by["AHU__excess_oa"][3] == "trended supply airflow"
    assert by["AHU__excess_oa_no_flow"][3] == "—"
    run = run_config(_cfg(_OA_DS, oa_store, "air-excess-oa-cost--no-min"), base_dir=oa_store)
    f = next(f for f in run.findings if f.equip == "AHU__excess_oa_no_flow")
    (c,) = cost_findings([f], {"AHU__excess_oa_no_flow": EquipmentLoad(supply_airflow_cfm=9000)})
    assert not c.costed and c.assumptions["airflow_source"] == "EquipmentLoad.supply_airflow_cfm"
    assert json.dumps(c.assumptions)  # plain data
