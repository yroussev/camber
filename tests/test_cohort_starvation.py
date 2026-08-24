"""Tests for the AHU cohort-starvation diagnosis (common-mode twin of the rogue-zone census)."""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.g36_reset import cohort_starvation  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.model.topology import Topology  # noqa: E402
from camber.rules.builtin import builtin_registry  # noqa: E402
from camber.rules.cohort_starvation_rule import CohortStarvation  # noqa: E402
from camber.rules.rogue_zone_census_rule import RogueZoneCensus  # noqa: E402

_IDX = pd.date_range("2025-07-07", periods=21 * 24, freq="1h")
_HOT = (_IDX.hour >= 10) & (_IDX.hour < 17)
_STATIC = {"flow_col": Role.AIRFLOW, "flow_sp_col": Role.AIRFLOW_SP, "damper_col": Role.DAMPER}
_SAT = {"temp_col": Role.SPACE_TEMP, "cool_sp_col": Role.COOL_SP}


def _sz(starved, *, idx=_IDX):
    """A static/airflow zone: starved = airflow well below setpoint with the damper wide open."""
    hot = (idx.hour >= 10) & (idx.hour < 17)
    sp = np.full(len(idx), 1000.0)
    if starved:
        flow = np.where(hot, 400.0, 1000.0)
        damper = np.where(hot, 98.0, 40.0)
    else:
        flow = np.full(len(idx), 950.0)
        damper = np.full(len(idx), 50.0)
    return pd.DataFrame({Role.AIRFLOW: flow, Role.AIRFLOW_SP: sp, Role.DAMPER: damper}, index=idx)


def _tz(over_f, *, idx=_IDX):
    """A SAT zone running ``over_f`` F above its cooling setpoint during hot hours."""
    hot = (idx.hour >= 10) & (idx.hour < 17)
    cool = np.full(len(idx), 74.0)
    return pd.DataFrame(
        {Role.SPACE_TEMP: cool + np.where(hot, over_f, 0.0), Role.COOL_SP: cool}, index=idx
    )


# ---- pure engine ----


def test_starved_cohort_static():
    fleet = {f"Z{i}": _sz(True) for i in range(1, 5)}
    r = cohort_starvation(fleet, reset="static", **_STATIC)
    assert r.starved_groups == ["<fleet>"]
    assert r.group_sustained_frac["<fleet>"] >= 0.9
    assert r.worst_group == "<fleet>"


def test_starved_cohort_sat_has_designday_caveat():
    fleet = {f"Z{i}": _tz(6.0) for i in range(1, 5)}
    r = cohort_starvation(fleet, reset="sat", **_SAT)
    assert r.starved_groups == ["<fleet>"]
    assert any("design-day" in c for c in r.caveats)


def test_lone_rogue_does_not_trip_starvation():
    fleet = {"Z1": _sz(True), **{f"Z{i}": _sz(False) for i in range(2, 5)}}
    r = cohort_starvation(fleet, reset="static", **_STATIC)
    assert r.starved_groups == []  # a single starved zone is the opposite of a starved cohort


def test_healthy_fleet_ok():
    fleet = {f"Z{i}": _sz(False) for i in range(1, 5)}
    r = cohort_starvation(fleet, reset="static", **_STATIC)
    assert r.starved_groups == []
    assert r.total_requests == 0


def test_grouping_scopes_per_ahu():
    fleet = {
        **{f"Z{i}": _sz(True) for i in range(1, 4)},  # AHU1 starved
        **{f"Z{i}": _sz(False) for i in range(4, 7)},  # AHU2 healthy
    }
    groups = {"Z1": "AHU1", "Z2": "AHU1", "Z3": "AHU1", "Z4": "AHU2", "Z5": "AHU2", "Z6": "AHU2"}
    r = cohort_starvation(fleet, reset="static", groups=groups, **_STATIC)
    assert r.starved_groups == ["AHU1"]


def test_small_cohort_below_min_zones_not_flagged():
    fleet = {"Z1": _sz(True), "Z2": _sz(True)}  # 2 < min_zones_per_group (3)
    r = cohort_starvation(fleet, reset="static", **_STATIC)
    assert r.starved_groups == []
    assert any("too few zones" in c for c in r.caveats)


def test_zero_requests_caveat():
    fleet = {f"Z{i}": _sz(False) for i in range(1, 4)}
    r = cohort_starvation(fleet, reset="static", **_STATIC)
    assert r.total_requests == 0
    assert any("not demand-bound" in c for c in r.caveats)


def test_nan_and_short_zones_excluded():
    short = _sz(True).iloc[:5]
    allnan = _sz(True).copy()
    allnan[Role.AIRFLOW] = np.nan
    fleet = {f"Z{i}": _sz(True) for i in range(1, 4)}
    fleet.update({"ZS": short, "ZN": allnan})
    r = cohort_starvation(fleet, reset="static", **_STATIC)
    assert "ZS" in r.unevaluable_zones and "ZN" in r.unevaluable_zones
    assert r.n_zones_evaluated == 3


def test_ragged_index_alignment():
    fleet = {"Z1": _sz(True), "Z2": _sz(True), "Z3": _sz(True, idx=_IDX[100:])}
    r = cohort_starvation(fleet, reset="static", **_STATIC)
    assert r.starved_groups == ["<fleet>"]  # aligns on the union, no crash


def test_empty_fleet_returns_none():
    assert cohort_starvation({}, reset="static", **_STATIC) is None


def test_bad_reset_raises():
    with pytest.raises(ValueError):
        cohort_starvation({"Z1": _sz(True)}, reset="bogus", **_STATIC)


# ---- fleet rule ----


def test_rule_starved_warns_names_ahu_look_upstream():
    fleet = {
        **{f"Z{i}": _sz(True) for i in range(1, 4)},
        **{f"Z{i}": _sz(False) for i in range(4, 7)},
    }
    groups = {"Z1": "AHU1", "Z2": "AHU1", "Z3": "AHU1", "Z4": "AHU2", "Z5": "AHU2", "Z6": "AHU2"}
    topo = Topology.from_parent_map(groups, provenance="semantic")
    f = CohortStarvation(reset="static").analyze_fleet(fleet, topology=topo)
    assert f.severity == "warn"
    assert "AHU1" in f.summary
    assert "upstream" in f.summary
    assert f.metrics["starved_groups"] == ["AHU1"]
    assert not any("inferred" in c for c in f.caveats)  # semantic -> no heuristic caveat


def test_rule_heuristic_topology_softens_caveat():
    fleet = {f"Z{i}": _sz(True) for i in range(1, 4)}
    groups = {"Z1": "AHU1", "Z2": "AHU1", "Z3": "AHU1"}
    topo = Topology.from_parent_map(groups, provenance="heuristic")
    f = CohortStarvation(reset="static").analyze_fleet(fleet, topology=topo)
    assert f.metrics["starved_groups"] == ["AHU1"]
    assert any("inferred from equipment naming" in c for c in f.caveats)


def test_rule_partial_coverage_pools_and_caveats():
    fleet = {f"Z{i}": _sz(True) for i in range(1, 6)}
    topo = Topology.from_parent_map(
        {"Z1": "AHU1", "Z2": "AHU1", "Z3": "AHU1"}, provenance="semantic"
    )
    f = CohortStarvation(reset="static").analyze_fleet(fleet, topology=topo)
    assert f.metrics["n_zones_ungrouped"] == 2
    assert any("not covered by the served-by model" in c for c in f.caveats)


def test_rule_no_topology_building_wide():
    fleet = {f"Z{i}": _sz(True) for i in range(1, 4)}
    f = CohortStarvation(reset="static").analyze_fleet(fleet)
    assert f.metrics["grouped"] is False
    assert any("no zone->AHU topology supplied" in c for c in f.caveats)


def test_rule_empty_fleet_info():
    assert CohortStarvation(reset="static").analyze_fleet({}).severity == "info"


def test_rule_names_and_roles_differ_by_family():
    assert CohortStarvation("sat").name == "sat_cohort_starvation"
    assert CohortStarvation("static").name == "static_cohort_starvation"
    assert CohortStarvation("static").roles_required == (Role.AIRFLOW, Role.AIRFLOW_SP, Role.DAMPER)


def test_rule_bad_reset_raises():
    with pytest.raises(ValueError):
        CohortStarvation(reset="bogus")


def test_both_families_registered_as_builtins():
    names = builtin_registry().names()
    assert "sat_cohort_starvation" in names
    assert "static_cohort_starvation" in names


# ---- cross-family invariant: the two shapes are distinct at the rule layer ----


def test_starved_cohort_warns_only_cohort_rule():
    fleet = {f"Z{i}": _sz(True) for i in range(1, 5)}  # all zones starved together
    assert CohortStarvation("static").analyze_fleet(fleet).severity == "warn"
    assert RogueZoneCensus("static").analyze_fleet(fleet).severity != "warn"  # not a rogue


def test_lone_rogue_warns_only_rogue_rule():
    fleet = {"Z1": _sz(True), **{f"Z{i}": _sz(False) for i in range(2, 5)}}  # one dominant zone
    assert RogueZoneCensus("static").analyze_fleet(fleet).severity == "warn"
    assert CohortStarvation("static").analyze_fleet(fleet).severity != "warn"  # not a cohort
