"""G36 zone-fleet rule edges (0.106, #186): the minimum airflow vs the active airflow setpoint,
the cross-zone heating/cooling census, dual-maximum reheat, slow-but-correct resets, overloaded
static rogues, every starved cohort named, SAT tier-1 requests and the cohort edges."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.g36_reset import (  # noqa: E402
    SAT_TR,
    reset_effectiveness,
    rogue_zone_census,
    tr_simulate,
)
from camber.model.roles import HAYSTACK_HINT, Role  # noqa: E402
from camber.rules._minflow import (  # noqa: E402
    BASIS_INFERRED,
    BASIS_MIN_SP,
    BASIS_SP_AS_MIN,
    airflow_sp_looks_active,
    min_flow_series,
)
from camber.rules.builtin import builtin_registry  # noqa: E402
from camber.rules.cohort import CohortDeviation  # noqa: E402
from camber.rules.cohort_starvation_rule import CohortStarvation  # noqa: E402
from camber.rules.reheat_min_rule import ReheatMinimization  # noqa: E402
from camber.rules.reheat_rule import _with_valve  # noqa: E402
from camber.rules.rogue_zone_census_rule import RogueZoneCensus  # noqa: E402
from camber.rules.zones_rule import ZonesHeatCoolCensus  # noqa: E402

N = 24 * 14


def _idx(n=N):
    return pd.date_range("2025-07-07", periods=n, freq="1h")  # a Monday


def _occupied(idx):
    return (idx.dayofweek < 5) & (idx.hour >= 7) & (idx.hour < 18)


# ---- roles and the minimum airflow -------------------------------------------------------------


def test_new_roles_have_hints():
    for r in (Role.AIRFLOW_MIN_SP, Role.ZONE_HEAT_LOOP, Role.ZONE_COOL_LOOP):
        assert r in HAYSTACK_HINT
    assert Role("airflow_min_sp") is Role.AIRFLOW_MIN_SP


def _active_box(idx):
    """A box whose airflow_sp is the ACTIVE setpoint: it swings 300-900 cfm and the airflow
    tracks it within a few percent."""
    rng = np.random.default_rng(1)
    sp = 300.0 + 600.0 * (0.5 + 0.5 * np.sin(np.arange(len(idx)) / 5.0))
    return pd.DataFrame(
        {Role.AIRFLOW_SP: sp, Role.AIRFLOW: sp * (1.0 + 0.02 * rng.standard_normal(len(idx)))},
        index=idx,
    )


def test_airflow_sp_active_vs_minimum():
    idx = _idx()
    active = _active_box(idx)
    assert airflow_sp_looks_active(active)
    vmin, basis = min_flow_series(active)
    assert basis == BASIS_INFERRED and 300.0 <= float(vmin.iloc[0]) < 330.0
    # a flat minimum, with the airflow riding above it in cooling: read as the minimum
    flat = pd.DataFrame(
        {Role.AIRFLOW_SP: np.full(N, 300.0), Role.AIRFLOW: active[Role.AIRFLOW].to_numpy()},
        index=idx,
    )
    assert not airflow_sp_looks_active(flat)
    assert min_flow_series(flat)[1] == BASIS_SP_AS_MIN
    # airflow_min_sp wins whenever it is mapped
    both = active.assign(**{Role.AIRFLOW_MIN_SP: 250.0})
    s, basis = min_flow_series(both)
    assert basis == BASIS_MIN_SP and (s == 250.0).all()


def test_reheat_penalty_reads_the_minimum_setpoint():
    idx = _idx(4)
    frame = pd.DataFrame(
        {
            Role.HEAT_VALVE: [0.0] * 4,
            Role.AIRFLOW_SP: [900.0] * 4,
            Role.AIRFLOW_MIN_SP: [300.0] * 4,
        },
        index=idx,
    )
    legacy = _with_valve(frame, {Role.HEAT_VALVE: "HWValve", Role.AIRFLOW_SP: "ActFlowSP"}, None)
    assert (legacy["ActFlowSP"] == 300.0).all()
    assert Role.AIRFLOW_MIN_SP not in legacy.columns


# ---- reheat_minimization_g36 -------------------------------------------------------------------


def _reheat_box(idx, *, flow, valve, vmin=300.0, **extra):
    data = {
        Role.HEAT_VALVE: valve,
        Role.AIRFLOW: flow,
        Role.AIRFLOW_SP: np.full(len(idx), vmin),
    }
    data.update(extra)
    return pd.DataFrame(data, index=idx)


def test_dual_maximum_high_heating_loop_is_not_a_violation():
    idx = _idx()
    occ = _occupied(idx)
    # dual maximum: the valve is wide open, then the airflow rises to the heating maximum
    valve = np.where(occ, 100.0, 0.0)
    flow = np.where(occ, 500.0, 300.0)
    f = ReheatMinimization().analyze("VAV_DM", _reheat_box(idx, flow=flow, valve=valve))
    assert f.severity == "ok", f
    assert f.metrics["reheat_above_min_pct"] == 0.0
    assert f.metrics["reheat_high_loop_pct"] == 100.0
    assert f.metrics["high_loop_evidence"] == "valve"
    assert any("high heating loop" in c for c in f.caveats)


def test_single_maximum_part_load_reheat_faults():
    idx = _idx()
    occ = _occupied(idx)
    valve = np.where(occ, 50.0, 0.0)
    flow = np.where(occ, 800.0, 300.0)  # 80 % of a 1000 cfm maximum while reheating
    f = ReheatMinimization().analyze("VAV_SM", _reheat_box(idx, flow=flow, valve=valve))
    assert f.severity == "fault" and f.metrics["reheat_above_min_pct"] == 100.0


def test_heating_loop_decides_when_trended():
    idx = _idx()
    occ = _occupied(idx)
    flow = np.where(occ, 500.0, 300.0)
    valve = np.where(occ, 100.0, 0.0)
    low = ReheatMinimization().analyze(
        "VAV_L",
        _reheat_box(idx, flow=flow, valve=valve, **{Role.ZONE_HEAT_LOOP: np.full(N, 30.0)}),
    )
    assert low.severity == "fault" and low.metrics["high_loop_evidence"] == "zone_heat_loop"
    high = ReheatMinimization().analyze(
        "VAV_H",
        _reheat_box(idx, flow=flow, valve=valve, **{Role.ZONE_HEAT_LOOP: np.full(N, 80.0)}),
    )
    assert high.severity == "ok"


def test_discharge_temperature_is_high_loop_evidence():
    idx = _idx()
    occ = _occupied(idx)
    box = _reheat_box(
        idx,
        flow=np.where(occ, 500.0, 300.0),
        valve=np.where(occ, 70.0, 0.0),  # valve below 90 %, discharge 20 °F above the space
        **{Role.SUPPLY_AIR_TEMP: np.full(N, 90.0), Role.SPACE_TEMP: np.full(N, 70.0)},
    )
    f = ReheatMinimization().analyze("VAV_DAT", box)
    assert (
        f.severity == "ok" and f.metrics["high_loop_evidence"] == "valve or discharge temperature"
    )


def test_active_airflow_sp_infers_the_minimum_and_caps_at_warn():
    idx = _idx()
    occ = _occupied(idx)
    box = _active_box(idx)
    box[Role.HEAT_VALVE] = np.where(occ, 40.0, 0.0)  # reheating at part load, airflow swinging
    f = ReheatMinimization().analyze("VAV_ACT", box)
    assert f.metrics["min_flow_basis"] == BASIS_INFERRED
    assert f.severity == "warn"  # would be a fault on a mapped minimum
    assert any("airflow_min_sp" in c for c in f.caveats)


def test_minimum_setpoint_alone_is_enough_and_occupancy_point_used():
    idx = _idx()
    weekend = idx.dayofweek >= 5
    box = pd.DataFrame(
        {
            Role.HEAT_VALVE: np.where(weekend, 50.0, 0.0),
            Role.AIRFLOW: np.where(weekend, 800.0, 300.0),
            Role.AIRFLOW_MIN_SP: np.full(N, 300.0),
            Role.OCCUPANCY: weekend.astype(float),  # a weekend building
        },
        index=idx,
    )
    reg = builtin_registry()
    rule = reg.get("reheat_minimization_g36")
    f = rule.analyze("VAV_W", box)
    assert f.metrics["occupancy"] == "trended" and f.metrics["min_flow_basis"] == BASIS_MIN_SP
    assert f.severity == "fault"  # the fixed weekday schedule would have seen no reheat at all


# ---- zones_heat_cool_census --------------------------------------------------------------------


def test_census_one_box_above_minimum_is_not_cross_zone():
    idx = _idx()
    a = _reheat_box(idx, flow=np.full(N, 500.0), valve=np.full(N, 40.0))  # heating above min
    b = _reheat_box(idx, flow=np.full(N, 300.0), valve=np.zeros(N))  # at minimum, idle
    f = ZonesHeatCoolCensus().analyze_fleet({"A": a, "B": b})
    assert f.metrics["pct_hours_any_both"] == 100.0  # the old (same-box) footprint
    assert f.metrics["pct_hours_mixed"] == 0.0
    assert f.severity == "ok"


def test_census_different_zones_heating_and_cooling_faults():
    idx = _idx()
    a = _reheat_box(idx, flow=np.full(N, 300.0), valve=np.full(N, 40.0))  # heating at minimum
    b = _reheat_box(idx, flow=np.full(N, 700.0), valve=np.zeros(N))  # cooling
    f = ZonesHeatCoolCensus().analyze_fleet({"A": a, "B": b})
    assert f.metrics["pct_hours_mixed"] == 100.0 and f.severity == "fault"
    assert f.metrics["avg_zones_heating_when_mixed"] == 1.0


# ---- reset effectiveness -----------------------------------------------------------------------


def test_slow_correct_reset_under_modest_demand_is_effective():
    # one net request every other cycle: T&R steps 0.3 °F down and trims 0.2 °F back, so a correct
    # reset creeps off its trim end and stays within 1.5 °F of it on every demand cycle
    req = np.tile([3, 0], 20)
    sp = tr_simulate(req, SAT_TR)
    df = pd.DataFrame({"sp": sp, "req": req}, index=_idx(len(req)))
    res = reset_effectiveness(df, "AHU", sp_col="sp", requests_col="req", params=SAT_TR)
    assert res.effective and res.failure_modes == []
    # before 0.106 the share was taken over every demand cycle: most sit within 1.5 °F of the
    # trim end, so this correct reset read "not responding"
    demand = req - SAT_TR.ignored > 0
    assert (np.abs(sp[demand] - SAT_TR.sp_max) <= 1.5).mean() >= 0.6
    # now only the cycles where the T&R trajectory itself had left the trim end count
    assert res.n_demand_judged < 10 and res.not_responding is None


def test_every_failure_mode_is_listed():
    req = np.full(60, 6)  # heavy demand throughout
    df = pd.DataFrame({"sp": np.full(60, 65.0), "req": req}, index=_idx(60))
    res = reset_effectiveness(df, "AHU", sp_col="sp", requests_col="req", params=SAT_TR)
    assert res.reason == "stuck"
    assert res.failure_modes == ["stuck", "not_responding"]
    f = (
        builtin_registry()
        .get("sat_reset_effectiveness")
        .analyze(
            "AHU",
            pd.DataFrame(
                {Role.SUPPLY_AIR_TEMP_SP: df["sp"], Role.SAT_RESET_REQUESTS: df["req"]},
                index=df.index,
            ),
        )
    )
    assert f.metrics["failure_modes"] == ["stuck", "not_responding"]
    assert "stuck" in f.summary and "not responding" in f.summary


# ---- the censuses ------------------------------------------------------------------------------


def _zone(idx, *, damper, flow=500.0, sp=600.0, temp=74.0, **extra):
    data = {
        Role.DAMPER: np.full(len(idx), damper),
        Role.AIRFLOW: np.full(len(idx), flow),
        Role.AIRFLOW_SP: np.full(len(idx), sp),
        Role.SPACE_TEMP: np.full(len(idx), temp),
        Role.COOL_SP: np.full(len(idx), 75.0),
    }
    data.update(extra)
    return pd.DataFrame(data, index=idx)


def test_overloaded_zone_is_not_a_static_rogue():
    idx = _idx(48)
    frames = {
        "HOT": _zone(idx, damper=100.0, temp=83.0),  # short of cooling, damper wide open
        "Z2": _zone(idx, damper=50.0),
        "Z3": _zone(idx, damper=50.0),
        "Z4": _zone(idx, damper=50.0),
    }
    f = RogueZoneCensus("static").analyze_fleet(frames)
    assert f.metrics["rogues"] == [] and f.metrics["overloaded_zones"] == ["HOT"]
    assert f.metrics["overloaded_share"] == {"HOT": 1.0}
    assert f.severity == "warn" and "overloaded" in f.summary
    # a blocked inlet in a comfortable zone stays a rogue
    frames["HOT"] = _zone(idx, damper=100.0, flow=200.0, temp=74.0)
    g = RogueZoneCensus("static").analyze_fleet(frames)
    assert g.metrics["rogues"] == ["HOT"] and g.metrics["overloaded_zones"] == []


def test_sat_tier1_requests_from_the_cooling_loop():
    idx = _idx(48)
    frames = {
        f"Z{k}": _zone(idx, damper=50.0, **{Role.ZONE_COOL_LOOP: np.full(48, 100.0 if k else 10.0)})
        for k in range(3)
    }
    res = rogue_zone_census(
        frames,
        reset="sat",
        temp_col=Role.SPACE_TEMP,
        cool_sp_col=Role.COOL_SP,
        cool_loop_col=Role.ZONE_COOL_LOOP,
    )
    assert res.total_requests == 2 * 48  # Z1 and Z2 at a loop over 95 %, one request a cycle
    assert not any("tier-1" in c for c in res.caveats)
    rule = RogueZoneCensus("sat")
    assert Role.ZONE_COOL_LOOP in rule.roles_optional
    assert rule.analyze_fleet(frames).metrics["total_requests"] == 96


def test_cohort_starvation_names_every_starved_group():
    idx = _idx(48)
    frames = {}
    groups = {}
    for g in ("AHU_A", "AHU_B"):
        for k in range(3):
            z = f"{g}_VAV{k}"
            frames[z] = _zone(idx, damper=100.0, flow=200.0)
            groups[z] = g
    f = CohortStarvation("static", groups=groups).analyze_fleet(frames)
    assert f.metrics["starved_groups"] == ["AHU_A", "AHU_B"]
    assert "AHU_A" in f.summary and "AHU_B" in f.summary and "2 zone cohort(s)" in f.summary


# ---- the cohort edges --------------------------------------------------------------------------


def _temps(offsets):
    idx = _idx(48)
    return {
        f"VAV{k}": pd.DataFrame(
            {Role.SPACE_TEMP: 72.0 + o + 0.01 * np.arange(48) % 0.05}, index=idx
        )
        for k, o in enumerate(offsets)
    }


def test_cohort_space_temp_ignores_a_small_real_difference():
    rule = builtin_registry().get("cohort_space_temp")
    f = rule.analyze_fleet(_temps([0.0, 0.05, -0.05, 0.02, -0.02, -0.8]))
    assert f.metrics["outliers"] == [] and f.metrics["min_abs_dev"] == 1.0
    g = rule.analyze_fleet(_temps([0.0, 0.05, -0.05, 0.02, -0.02, -3.0]))
    assert g.metrics["outliers"] == ["VAV5"]


def test_cohort_airflow_too_unlike_to_judge():
    idx = _idx(48)
    frames = {
        f"VAV{k}": pd.DataFrame({Role.AIRFLOW: np.full(48, cfm)}, index=idx)
        for k, cfm in enumerate([100.0, 200.0, 400.0, 600.0, 800.0, 1200.0])
    }
    f = builtin_registry().get("cohort_airflow").analyze_fleet(frames)
    assert f.metrics["outliers"] == [] and f.metrics["cohort_spread"] > 0.3
    assert f.severity == "info" and any("spreads widely" in c for c in f.caveats)
    same = CohortDeviation(Role.AIRFLOW, max_spread=5.0).analyze_fleet(frames)
    assert same.severity == "ok"
