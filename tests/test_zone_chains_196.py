"""0.107 (#196): AHU -> VAV cause chains from the served-by topology.

An air handler's finding (supply air too warm / too cold, duct static too low) roots an issue whose
members are the matching symptom findings of the boxes the topology says it serves. No topology,
or a box the topology does not cover, links nothing; a box served by another air handler stays
separate; the chain's cost is its largest member, never the sum, and its hours the union.
"""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.fault_economics import FaultCost  # noqa: E402
from camber.model.topology import Topology  # noqa: E402
from camber.rules.base import Finding  # noqa: E402
from camber.rules.builtin import builtin_registry  # noqa: E402
from camber.rules.triage import (  # noqa: E402
    ZONE_CHAINS,
    issue_totals,
    link_findings,
    zone_chain_gaps,
)

REG = builtin_registry()
IDX = pd.date_range("2026-07-06", periods=48, freq="h")


def _f(rule, equip, severity="warn", **metrics):
    return Finding(rule=rule, equip=equip, severity=severity, metrics=metrics, summary=rule)


def _warm(equip="AHU-1"):
    return _f("supply_air_control", equip, too_warm_pct=30.0, too_cold_pct=0.0)


def _hot(box, severity="warn"):
    return _f("unmet_setpoint_hours", box, severity, too_hot_pct=20.0, too_cold_pct=0.0)


def _cost(f, usd):
    costed = usd is not None
    return FaultCost(
        rule=f.rule,
        equip=f.equip,
        severity=f.severity,
        electricity_kwh=0.0,
        gas_therms=0.0,
        annual_cost_usd=float(usd or 0.0),
        basis="test-model" if costed else "no cost model for this rule",
        costed=costed,
    )


def _topo(n=4, ahu="AHU-1", **extra):
    parents = {f"VAV-{i}": ahu for i in range(1, n + 1)}
    parents.update(extra)
    return Topology.from_parent_map(parents)


def _mask(hours):
    return pd.Series(IDX.hour.isin(hours), index=IDX)


def test_mapping_table_is_small_and_names_real_rules():
    assert [c[0] for c in ZONE_CHAINS] == ["sat_high", "sat_low", "static_low"]
    names = set(REG.names()) | {"unmet_setpoint_hours", "airflow_tracking"}
    for rule in ("overcooling_min_flow", "reheat_penalty", "reheat_minimization_g36"):
        assert rule in names


def test_linked_fleet_roots_on_the_ahu_and_counts_boxes():
    root = _warm()
    boxes = [_hot(f"VAV-{i}") for i in (1, 2, 3)]
    issues = link_findings([root, *boxes], rules=REG, topology=_topo(4))
    assert len(issues) == 1  # the box symptoms joined the AHU issue
    iss = issues[0]
    assert iss.root is root and iss.equip == "AHU-1"
    assert [f.equip for f in iss.members] == ["AHU-1", "VAV-1", "VAV-2", "VAV-3"]
    (z,) = iss.zone_links
    assert z.chain == "sat_high" and z.ahu == "AHU-1"
    assert z.boxes == ("VAV-1", "VAV-2", "VAV-3") and z.count() == "3 of 4 boxes"
    assert z.not_assessed == z.boxes  # no masks: linked, the overlap not assessed
    assert any("3 of 4 boxes" in w and "zones can't cool" in w for w in iss.why)


def test_box_served_by_another_ahu_stays_separate():
    root = _warm()
    mine, other = _hot("VAV-1"), _hot("VAV-9")
    topo = _topo(2, **{"VAV-9": "AHU-2"})
    issues = link_findings([root, mine, other], rules=REG, topology=topo)
    by_equip = {i.equip: i for i in issues}
    assert set(by_equip) == {"AHU-1", "VAV-9"}
    assert [f.equip for f in by_equip["AHU-1"].members] == ["AHU-1", "VAV-1"]
    assert by_equip["VAV-9"].zone_links == []
    assert by_equip["AHU-1"].zone_links[0].count() == "1 of 2 boxes"
    assert zone_chain_gaps([root, mine, other], topo) == {}  # VAV-9 is covered, just elsewhere


def test_no_topology_links_nothing_and_reports_the_gap():
    root = _warm()
    boxes = [_hot("VAV-1"), _hot("VAV-2")]
    issues = link_findings([root, *boxes], rules=REG)
    assert len(issues) == 3 and all(not i.zone_links for i in issues)
    gaps = zone_chain_gaps([root, *boxes])
    assert gaps == {
        "topology": "none",
        "boxes": ["VAV-1", "VAV-2"],
        "causes": ["supply air too warm"],
    }
    # a box the topology does not cover is a gap too; a naming guess is never used
    part = zone_chain_gaps([root, *boxes], _topo(1))
    assert part["boxes"] == ["VAV-2"] and part["topology"] == "explicit"
    guess = Topology.from_parent_map({"VAV-1": "AHU-1"}, provenance="heuristic")
    assert all(not i.zone_links for i in link_findings([root, *boxes], topology=guess))
    assert zone_chain_gaps([root, *boxes], guess)["topology"] == "heuristic"
    # no root on the site -> nothing to say
    assert zone_chain_gaps(boxes) == {}


def test_chain_cost_is_the_largest_member_never_the_sum_and_hours_union():
    root = _f("supply_air_control", "AHU-1", too_warm_pct=0.0, too_cold_pct=40.0)
    r1 = _f("reheat_penalty", "VAV-1")
    r2 = _f("reheat_minimization_g36", "VAV-2", severity="fault")
    findings = [root, r1, r2]
    costs = [_cost(root, None), _cost(r1, 1200.0), _cost(r2, 3400.0)]
    masks = {
        id(root): _mask(range(8, 18)),
        id(r1): _mask(range(8, 12)),
        id(r2): _mask([16, 17, 20]),
    }
    issues = link_findings(
        findings,
        rules=REG,
        costs=costs,
        topology=_topo(3),
        mask_for=lambda f: masks.get(id(f)),
    )
    (iss,) = issues
    assert iss.cost == 3400.0  # largest member, not 4600
    assert "largest shown" in iss.cost_basis_note
    assert iss.severity == "fault"  # the worst member
    # union of 8-17 and 20 over two days = 11 h/day
    assert iss.hours_union == 22.0
    assert issue_totals(issues)["annual_cost_usd"] == 3400.0
    assert iss.zone_links[0].chain == "sat_low"
    # without the topology the boxes are separate issues and their dollars add
    apart = link_findings(findings, rules=REG, costs=costs)
    assert issue_totals(apart)["annual_cost_usd"] == 4600.0


def test_symptom_out_of_the_roots_hours_stays_separate():
    root, near, far = _warm(), _hot("VAV-1"), _hot("VAV-2")
    masks = {id(root): _mask(range(12, 17)), id(near): _mask(range(13, 16)), id(far): _mask([2, 3])}
    issues = link_findings(
        [root, near, far], rules=REG, topology=_topo(2), mask_for=lambda f: masks.get(id(f))
    )
    by_equip = {i.equip: i for i in issues}
    assert set(by_equip) == {"AHU-1", "VAV-2"}
    (z,) = by_equip["AHU-1"].zone_links
    assert z.boxes == ("VAV-1",) and z.out_of_hours == ("VAV-2",) and z.not_assessed == ()


def test_sensor_conditional_box_is_kept_out_and_a_conditional_root_roots_nothing():
    root = _warm()
    ok, shaky = _hot("VAV-1"), _hot("VAV-2")
    drift = _f("sensor_drift:space_temp", "VAV-2", severity="fault")
    issues = link_findings([root, ok, shaky, drift], rules=REG, topology=_topo(2))
    by_equip = {}
    for i in issues:
        by_equip.setdefault(i.equip, []).append(i)
    ahu = by_equip["AHU-1"][0]
    assert [f.equip for f in ahu.members] == ["AHU-1", "VAV-1"] and not ahu.conditional
    assert ahu.zone_links[0].conditional == ("VAV-2",)
    assert any(i.conditional and i.root is shaky for i in by_equip["VAV-2"])
    # an AHU finding leaning on a distrusted sensor roots no chain
    sat_drift = _f("sensor_drift:supply_air_temp", "AHU-1", severity="fault")
    issues = link_findings([root, ok, sat_drift], rules=REG, topology=_topo(2))
    assert all(not i.zone_links for i in issues) and len(issues) == 3


def test_low_static_starves_boxes_and_g36_reads_fc1():
    g36 = _f("g36_afdd", "AHU-1", flagged_fcs=["FC1"])
    starved = _f("airflow_tracking", "VAV-1", undershoot_pct=30.0, overshoot_pct=1.0)
    over = _f("airflow_tracking", "VAV-2", undershoot_pct=1.0, overshoot_pct=30.0)
    issues = link_findings([g36, starved, over], rules=REG, topology=_topo(2))
    by_equip = {i.equip: i for i in issues}
    assert by_equip["AHU-1"].zone_links[0].chain == "static_low"
    assert [f.equip for f in by_equip["AHU-1"].members] == ["AHU-1", "VAV-1"]
    assert "VAV-2" in by_equip  # an overshooting box is not a starved one


def test_rogue_census_is_listed_on_the_ahu_and_kept_as_its_own_issue():
    reset = _f(
        "static_reset_effectiveness", "AHU-1", failure_modes=["not_trimming"], reason="not_trimming"
    )
    census = _f("static_rogue_zone_census", "<fleet>", rogues=["VAV-2", "VAV-9"])
    topo = _topo(3, **{"VAV-9": "AHU-2"})
    issues = link_findings([reset, census], rules=REG, topology=topo)
    by_equip = {i.equip: i for i in issues}
    assert set(by_equip) == {"AHU-1", "<fleet>"}  # nothing absorbed
    (z,) = by_equip["AHU-1"].zone_links
    assert z.boxes == ("VAV-2",) and not z.absorbed and z.chain == "static_rogue"
    assert by_equip["<fleet>"].zone_links == [z]
    assert any("VAV-2" in w and "AHU-1" in w for w in by_equip["<fleet>"].why)


def test_report_shows_the_chain_and_appendix_a_notes_a_missing_topology(tmp_path, monkeypatch):
    import _rcx_fixture as fx
    import matplotlib.pyplot as plt

    from camber.config import run_config
    from camber.report import rcx as rcx_mod
    from camber.report.rcx import build_rcx_report

    def fake(fig, fmt="png", dpi=150):
        plt.close(fig)
        return f"data:image/{fmt};base64,AAAA"

    monkeypatch.setattr(rcx_mod, "_render", fake)
    fx.make_store(tmp_path)
    run = run_config(fx.config(), base_dir=str(tmp_path))
    root = _f("supply_air_control", "DemoAHU", too_warm_pct=40.0, too_cold_pct=0.0)
    run.findings += [root, _hot("VAV-1"), _hot("VAV-2")]

    html = build_rcx_report(run).to_html()
    assert "AHU-to-VAV cause chains not linked (missing topology)" in html
    assert "VAV-1, VAV-2" in html

    run.topology = Topology.from_parent_map(
        {"VAV-1": "DemoAHU", "VAV-2": "DemoAHU", "VAV-3": "DemoAHU"}
    )
    rep = build_rcx_report(run)
    html = rep.to_html()
    assert "missing topology" not in html
    assert "Cause chain: supply air too warm at DemoAHU" in html and "2 of 3 boxes" in html
    d = next(i for i in rep.to_dict()["issues"] if i["zone_chains"])
    assert d["zone_chains"][0]["n_boxes"] == 2 and d["zone_chains"][0]["n_served"] == 3


def test_compliance_above_its_target_is_not_supply_air_too_cold():
    """0.108: ``supply_air_reset_compliance`` also warns when supply air runs above the G36
    target (a cooling shortfall). Only its below-target side roots the "supply air too cold"
    chain; the reheat at the boxes then stays separate."""
    above = _f(
        "supply_air_reset_compliance",
        "AHU-1",
        pct_below_g36_target=5.0,
        mean_gap_f=-5.1,
        warn_pct_threshold=40.0,
        warn_gap_floor_f=1.0,
        pct_above_g36_target=60.0,
    )
    below = _f(
        "supply_air_reset_compliance",
        "AHU-1",
        pct_below_g36_target=55.0,
        mean_gap_f=3.0,
        warn_pct_threshold=40.0,
        warn_gap_floor_f=1.0,
    )
    reheat = _f("reheat_minimization_g36", "VAV-1", severity="fault")
    linked = link_findings([below, reheat], rules=REG, topology=_topo(2))
    assert [z.chain for i in linked for z in i.zone_links] == ["sat_low"]
    apart = link_findings([above, reheat], rules=REG, topology=_topo(2))
    assert all(not i.zone_links for i in apart) and len(apart) == 2
