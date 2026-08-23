"""Tests for the rogue-zone census (which zone monopolizes a G36 reset and drags it)."""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.g36_reset import rogue_zone_census  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.builtin import builtin_registry  # noqa: E402
from camber.rules.rogue_zone_census_rule import RogueZoneCensus  # noqa: E402

_IDX = pd.date_range("2025-07-07", periods=21 * 24, freq="1h")
_HOT = (_IDX.hour >= 10) & (_IDX.hour < 17)
_SAT_COLS = {"temp_col": Role.SPACE_TEMP, "cool_sp_col": Role.COOL_SP}
_STATIC_COLS = {"flow_col": Role.AIRFLOW, "flow_sp_col": Role.AIRFLOW_SP, "damper_col": Role.DAMPER}


def _sat_zone(over_f, *, idx=_IDX):
    """A zone that runs ``over_f`` °F above its cooling setpoint during occupied/hot hours."""
    cool = np.full(len(idx), 74.0)
    hot = (idx.hour >= 10) & (idx.hour < 17)
    temp = cool + np.where(hot, over_f, 0.0)
    return pd.DataFrame({Role.SPACE_TEMP: temp, Role.COOL_SP: cool}, index=idx)


def _static_zone(*, starved, idx=_IDX):
    """A zone that (if starved) runs airflow well below setpoint with the damper wide open."""
    hot = (idx.hour >= 10) & (idx.hour < 17)
    sp = np.full(len(idx), 1000.0)
    if starved:
        flow = np.where(hot, 400.0, 1000.0)  # 40% of sp when hot -> tier-3 requests
        damper = np.where(hot, 98.0, 40.0)
    else:
        flow = np.full(len(idx), 950.0)  # satisfied
        damper = np.full(len(idx), 50.0)
    return pd.DataFrame({Role.AIRFLOW: flow, Role.AIRFLOW_SP: sp, Role.DAMPER: damper}, index=idx)


# ---- pure engine ----


def test_clear_rogue_sat():
    fleet = {"Z1": _sat_zone(6.0), **{f"Z{i}": _sat_zone(0.0) for i in range(2, 6)}}
    r = rogue_zone_census(fleet, reset="sat", **_SAT_COLS)
    assert r.rogues == ["Z1"]
    assert r.worst_zone == "Z1"
    assert r.zone_binding_frac["Z1"] == 1.0
    assert r.worst_zone_share >= 0.9


def test_balanced_fleet_no_rogue():
    # every zone equally hot -> all tie at the binding max, but shares are equal -> no rogue
    fleet = {f"Z{i}": _sat_zone(6.0) for i in range(1, 6)}
    r = rogue_zone_census(fleet, reset="sat", **_SAT_COLS)
    assert r.rogues == []
    assert r.total_requests > 0
    assert round(r.zone_request_share["Z1"], 2) == 0.2


def test_clear_rogue_static():
    fleet = {
        "Z1": _static_zone(starved=True),
        **{f"Z{i}": _static_zone(starved=False) for i in range(2, 6)},
    }
    r = rogue_zone_census(fleet, reset="static", **_STATIC_COLS)
    assert r.rogues == ["Z1"]
    assert r.zone_binding_frac["Z1"] == 1.0


def test_zero_requests_fleetwide():
    fleet = {f"Z{i}": _sat_zone(0.0) for i in range(1, 4)}
    r = rogue_zone_census(fleet, reset="sat", **_SAT_COLS)
    assert r.total_requests == 0
    assert r.rogues == []
    assert any("not demand-bound" in c for c in r.caveats)


def test_single_zone_group_unevaluable():
    # one zone total -> no peer to compare against -> no rogue, group collapsed
    fleet = {"Z1": _sat_zone(6.0)}
    r = rogue_zone_census(fleet, reset="sat", **_SAT_COLS)
    assert r.rogues == []
    assert any("too few zones" in c for c in r.caveats)


def test_grouping_scopes_per_ahu():
    # Z1 is a building-wide rogue; grouping puts it with a balanced peer where it is genuinely
    # dominant on AHU1, while AHU2's zones (all at setpoint) generate no requests.
    fleet = {"Z1": _sat_zone(6.0), **{f"Z{i}": _sat_zone(0.0) for i in range(2, 6)}}
    groups = {"Z1": "AHU1", "Z2": "AHU1", "Z3": "AHU2", "Z4": "AHU2", "Z5": "AHU2"}
    r = rogue_zone_census(fleet, reset="sat", groups=groups, **_SAT_COLS)
    assert r.grouped is True
    assert r.n_groups == 2
    assert r.rogue_by_group == {"AHU1": ["Z1"]}


def test_nan_and_short_zones_excluded():
    short = _sat_zone(6.0).iloc[:5]  # < min_active_cycles
    allnan = _sat_zone(6.0).copy()
    allnan[Role.SPACE_TEMP] = np.nan
    fleet = {"Z1": _sat_zone(6.0), "Z2": _sat_zone(0.0), "ZS": short, "ZN": allnan}
    r = rogue_zone_census(fleet, reset="sat", **_SAT_COLS)
    assert "ZS" in r.unevaluable_zones and "ZN" in r.unevaluable_zones
    assert r.n_zones_evaluated == 2


def test_ragged_index_alignment():
    a = _sat_zone(6.0)
    b = _sat_zone(0.0, idx=_IDX[100:])  # partially disjoint index
    r = rogue_zone_census({"Z1": a, "Z2": b}, reset="sat", **_SAT_COLS)
    assert r.rogues == ["Z1"]  # alignment on the union index, no crash, no false rogue in Z2


def test_empty_fleet_returns_none():
    assert rogue_zone_census({}, reset="sat", **_SAT_COLS) is None


def test_bad_reset_raises():
    with pytest.raises(ValueError):
        rogue_zone_census({"Z1": _sat_zone(6.0)}, reset="bogus", **_SAT_COLS)


# ---- fleet rule ----


def test_rule_sat_rogue_warns_and_names_zone():
    fleet = {"Z1": _sat_zone(6.0), **{f"Z{i}": _sat_zone(0.0) for i in range(2, 6)}}
    f = RogueZoneCensus(reset="sat").analyze_fleet(fleet)
    assert f.severity == "warn"
    assert "Z1" in f.summary
    assert f.metrics["worst_zone"] == "Z1"
    assert f.metrics["rogues"] == ["Z1"]


def test_rule_balanced_ok():
    fleet = {f"Z{i}": _sat_zone(6.0) for i in range(1, 6)}
    f = RogueZoneCensus(reset="sat").analyze_fleet(fleet)
    assert f.severity == "ok"
    assert "no rogue" in f.summary


def test_rule_empty_fleet_info():
    f = RogueZoneCensus(reset="sat").analyze_fleet({})
    assert f.severity == "info"


def test_rule_no_topology_confound_caveat():
    fleet = {"Z1": _sat_zone(6.0), **{f"Z{i}": _sat_zone(0.0) for i in range(2, 6)}}
    f = RogueZoneCensus(reset="sat").analyze_fleet(fleet)
    assert any("pooled building-wide" in c for c in f.caveats)


def test_rule_grouped_drops_confound_caveat():
    fleet = {"Z1": _sat_zone(6.0), **{f"Z{i}": _sat_zone(0.0) for i in range(2, 6)}}
    groups = {"Z1": "AHU1", "Z2": "AHU1", "Z3": "AHU2", "Z4": "AHU2", "Z5": "AHU2"}
    f = RogueZoneCensus(reset="sat", groups=groups).analyze_fleet(fleet)
    assert not any("pooled building-wide" in c for c in f.caveats)
    assert f.metrics["grouped"] is True


def test_rule_inconclusive_demotes_to_info():
    # no rogue and unevaluable zones present -> ok demoted to info
    short = _sat_zone(6.0).iloc[:5]
    fleet = {"Z1": _sat_zone(0.0), "Z2": _sat_zone(0.0), "ZS": short}
    f = RogueZoneCensus(reset="sat").analyze_fleet(fleet)
    assert f.severity == "info"
    assert "inconclusive" in f.summary


def test_rule_names_and_roles_differ_by_family():
    sat = RogueZoneCensus(reset="sat")
    static = RogueZoneCensus(reset="static")
    assert sat.name == "sat_rogue_zone_census"
    assert static.name == "static_rogue_zone_census"
    assert sat.roles_required == (Role.SPACE_TEMP, Role.COOL_SP)
    assert static.roles_required == (Role.AIRFLOW, Role.AIRFLOW_SP, Role.DAMPER)


def test_rule_bad_reset_raises():
    with pytest.raises(ValueError):
        RogueZoneCensus(reset="bogus")


def test_both_families_registered_as_builtins():
    names = builtin_registry().names()
    assert "sat_rogue_zone_census" in names
    assert "static_rogue_zone_census" in names
