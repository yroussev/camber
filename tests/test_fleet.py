"""Tests for the fleet/portfolio rollup (report.fleet)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.report.fleet import BuildingSummary, build_fleet_report  # noqa: E402


class _F:
    def __init__(self, rule, severity):
        self.rule, self.severity = rule, severity
        self.equip, self.metrics = "", {}


def _fleet():
    return [
        {"site": "A", "eui": 80.0, "findings": [_F("reheat_penalty", "fault")]},
        {
            "site": "B",
            "eui": 120.0,
            "findings": [_F("reheat_penalty", "warn"), _F("oafraction", "fault"), _F("x", "ok")],
        },
        {"site": "C", "eui": 100.0, "findings": []},
    ]


def test_per_building_counts_and_benchmark():
    r = build_fleet_report(_fleet())
    by = {b.site: b for b in r.buildings}
    assert isinstance(by["A"], BuildingSummary)
    assert by["A"].n_fault == 1 and by["A"].n_warn == 0
    assert by["B"].n_fault == 1 and by["B"].n_warn == 1  # the "ok" is dropped
    # median EUI of [80,100,120] = 100
    assert r.peer_median_eui == 100.0
    assert by["A"].eui_percentile == 100  # lowest EUI -> most efficient
    assert by["A"].pct_vs_median == -20  # (80-100)/100
    assert by["B"].pct_vs_median == 20


def test_fleet_top_rules_counts_buildings():
    r = build_fleet_report(_fleet())
    top = dict(r.fleet_top_rules)
    assert top["reheat_penalty"] == 2  # appears in A and B
    assert top["oafraction"] == 1
    assert "x" not in top  # non-actionable excluded


def test_orderings_and_render():
    r = build_fleet_report(_fleet())
    # most efficient first by EUI
    assert [b.site for b in r.by_efficiency()] == ["A", "C", "B"]
    # worst by faults: tie on faults (A,B=1) broken by warnings -> B first
    worst = [b.site for b in r.worst_by_faults()]
    assert worst[0] == "B"
    txt = r.to_text()
    assert "Fleet summary -- 3 buildings" in txt and "reheat_penalty" in txt
    assert "<table" in r.to_html()


def test_peer_median_override():
    r = build_fleet_report(_fleet(), peer_median_eui=90.0)
    by = {b.site: b for b in r.buildings}
    assert r.peer_median_eui == 90.0
    assert by["C"].pct_vs_median == round(100 * (100 - 90) / 90)  # ~11


def test_no_cost_rollup_by_default():
    r = build_fleet_report(_fleet())
    assert r.total_annual_cost_usd is None
    assert all(b.annual_cost_usd is None for b in r.buildings)
    assert r.worst_by_cost() == []


def test_precomputed_cost_rollup():
    fleet = [
        {"site": "A", "eui": 80.0, "findings": [], "annual_cost_usd": 12000.0},
        {"site": "B", "eui": 120.0, "findings": [], "annual_cost_usd": 45000.0},
        {"site": "C", "eui": 100.0, "findings": []},  # no cost -> excluded from $ ranking
    ]
    r = build_fleet_report(fleet)
    assert r.total_annual_cost_usd == 57000.0
    assert [b.site for b in r.worst_by_cost()] == ["B", "A"]  # by dollars, highest first
    assert "recoverable waste" in r.to_text().lower()
    assert "$45,000" in r.to_html()


def test_estimated_cost_via_fault_economics():
    from camber.fault_economics import EnergyPrice
    from camber.rules.base import Finding

    def chiller(equip, kwpt, tons, pct):
        return Finding(
            rule="chiller_efficiency",
            equip=equip,
            severity="warn",
            metrics={
                "kw_per_ton_median": kwpt,
                "design_kw_per_ton": 0.6,
                "tons_median": tons,
                "pct_hours_inefficient": pct,
            },
        )

    fleet = [
        {"site": "A", "eui": 90.0, "findings": [chiller("CH1", 0.9, 300.0, 60.0)]},
        {"site": "B", "eui": 110.0, "findings": [chiller("CH1", 0.7, 100.0, 30.0)]},
    ]
    r = build_fleet_report(fleet, price=EnergyPrice(electricity_per_kwh=0.15))
    by = {b.site: b for b in r.buildings}
    assert by["A"].annual_cost_usd > by["B"].annual_cost_usd > 0  # A's chiller wastes more
    assert r.total_annual_cost_usd == round(by["A"].annual_cost_usd + by["B"].annual_cost_usd, 2)
    assert [b.site for b in r.worst_by_cost()] == ["A", "B"]


def test_precomputed_cost_wins_over_estimate():
    from camber.fault_economics import EnergyPrice
    from camber.rules.base import Finding

    f = Finding(
        rule="chiller_efficiency",
        equip="CH1",
        severity="warn",
        metrics={
            "kw_per_ton_median": 0.9,
            "design_kw_per_ton": 0.6,
            "tons_median": 300.0,
            "pct_hours_inefficient": 60.0,
        },
    )
    fleet = [{"site": "A", "eui": 90.0, "findings": [f], "annual_cost_usd": 999.0}]
    r = build_fleet_report(fleet, price=EnergyPrice())
    assert r.buildings[0].annual_cost_usd == 999.0  # precomputed value wins


def test_fleet_eui_follows_the_unit_system():
    """0.93 (#70): the peer-median EUI and each building's EUI in kWh/m2/yr under SI."""
    from camber.agent.context import facts_from_fleet
    from camber.energy_units import eui_factor

    ip = build_fleet_report(_fleet(), peer_median_eui=90.0)
    assert ip.eui_unit == "kBtu/ft2/yr" and "Peer-median EUI: 90 kBtu/ft2/yr" in ip.to_text()
    assert "eui_unit" not in facts_from_fleet(ip)[0].data  # the default facts are unchanged
    si = build_fleet_report(_fleet(), peer_median_eui=90.0, units="si")
    k = eui_factor("kBtu/ft2/yr", "kWh/m2/yr")
    assert si.eui_unit == "kWh/m2/yr" and si.peer_median_eui == pytest.approx(90.0 * k)
    assert f"{90.0 * k:g} kWh/m2/yr" in si.to_text() and "kWh/m&sup2;/yr" in si.to_html()
    by = {b.site: b for b in si.buildings}
    for b in ip.buildings:
        if b.eui is not None:
            assert by[b.site].eui == pytest.approx(b.eui * k)
            assert by[b.site].eui_percentile == b.eui_percentile
            assert by[b.site].pct_vs_median == b.pct_vs_median
    facts = facts_from_fleet(si)
    assert "kWh/m2/yr" in facts[0].text and facts[0].data["eui_unit"] == "kWh/m2/yr"
    assert all("kBtu" not in f.text for f in facts)
    same = build_fleet_report(_fleet(), peer_median_eui=90.0, units="ip")
    assert same.peer_median_eui == 90.0 and same.to_text() == ip.to_text()
    given_si = build_fleet_report(
        [{"site": "A", "eui": 250.0, "findings": []}], eui_unit="kWh/m2/yr", units="si"
    )
    assert given_si.buildings[0].eui == 250.0
    with pytest.raises(ValueError):
        build_fleet_report(_fleet(), eui_unit="kBtu/furlong")
