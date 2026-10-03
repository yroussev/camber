"""0.98 (#85): the actuator_stuck rule, its recommender, and the cohort rule's opt-in options."""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber import faultlab  # noqa: E402
from camber.aso import recommend  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.actuator_stuck_rule import ActuatorStuck  # noqa: E402
from camber.rules.base import Finding  # noqa: E402
from camber.rules.builtin import builtin_registry, make_rule  # noqa: E402
from camber.rules.cohort import CohortDeviation  # noqa: E402

DAY = pd.date_range("2023-12-11", periods=96, freq="15min")
H = DAY.hour.to_numpy() + DAY.minute.to_numpy() / 60.0
OCC = (H >= 7) & (H < 22)


def _box(damper, space, *, cool=75.2, heat=69.8, **extra) -> pd.DataFrame:
    cols = {
        Role.DAMPER: damper,
        Role.SPACE_TEMP: space,
        Role.COOL_SP: np.where(OCC, cool, 85.0),
        Role.HEAT_SP: np.where(OCC, heat, 60.0),
        Role.OCCUPANCY: OCC.astype(float),
    }
    cols.update(extra)
    return pd.DataFrame(cols, index=DAY)


def _mild():
    return np.where(OCC, 72.0 + 0.8 * np.sin(H / 3.0), 68.0)


def test_healthy_modulating_box_is_quiet():
    f = ActuatorStuck().analyze("VAV", _box(np.where(OCC, 40 + 10 * np.sin(H), 0.0), _mild()))
    assert f.severity == "ok" and f.metrics["flat_runs"] == 0


def test_long_mid_stroke_hold_in_a_satisfied_zone_is_consistent():
    """A healthy box at its minimum for most of the day (ORNL's neighbours) is not stuck."""
    damper = np.where(OCC & (H < 19), 36.0, np.where(OCC, 38.0 + np.sin(H), 0.0))
    cold = np.where(OCC, 71.0 + 0.5 * np.sin(H), 68.0)
    f = ActuatorStuck().analyze("VAV", _box(damper, cold))
    assert f.severity == "ok", f.summary


def test_closed_through_occupied_hours_is_a_fault_with_a_caveat():
    f = ActuatorStuck().analyze("VAV", _box(np.zeros(96), _mild()))
    assert f.severity == "fault" and f.metrics["reason"] == "closed_occupied"
    assert "shut through occupied hours" in f.summary
    assert any("min_airflow" in c for c in f.caveats)


def test_closed_damper_against_an_airflow_setpoint():
    frame = _box(
        np.zeros(96),
        _mild(),
        **{Role.AIRFLOW: np.full(96, 10.0), Role.AIRFLOW_SP: np.full(96, 300.0)},
    )
    f = ActuatorStuck().analyze("VAV", frame)
    assert f.severity == "fault" and f.metrics["reason"] == "closed_no_flow"
    # air still flows past a "closed" damper: not contradicted by the flow test
    flowing = frame.assign(**{Role.AIRFLOW: np.full(96, 250.0)})
    got = ActuatorStuck().analyze("VAV", flowing)
    assert got.metrics["reason"] != "closed_no_flow"
    # min_airflow stands in for a missing setpoint
    no_sp = frame.drop(columns=[Role.AIRFLOW_SP])
    assert ActuatorStuck(min_airflow=300).analyze("VAV", no_sp).metrics["reason"] == (
        "closed_no_flow"
    )


def test_mid_stroke_while_the_zone_runs_warm():
    warm = np.where(OCC & (H >= 12) & (H < 19), 79.0, _mild())
    f = ActuatorStuck().analyze("VAV", _box(np.full(96, 20.0), warm))
    assert f.severity == "fault" and f.metrics["reason"] == "zone_warm"
    assert "over its cooling setpoint" in f.summary and "20 %" in f.summary


def test_open_while_the_zone_is_satisfied():
    cool = np.where(OCC, 70.5, 68.0)
    f = ActuatorStuck().analyze("VAV", _box(np.full(96, 100.0), cool))
    assert f.severity == "fault" and f.metrics["reason"] == "zone_satisfied"


def test_saturated_open_on_a_hot_day_is_not_stuck():
    hot = np.where(OCC, 76.0 + 0.5 * np.sin(H), 70.0)
    f = ActuatorStuck().analyze("VAV", _box(np.full(96, 100.0), hot))
    assert f.severity == "ok", f.summary


def test_unexplained_whole_day_flat_is_warn_only():
    f = ActuatorStuck().analyze("VAV", _box(np.full(96, 60.0), _mild()))
    assert f.severity == "warn" and f.metrics["tier"] == "unexplained_flat"
    assert f.metrics["driver"] == "space_temp"
    # nothing moved: nothing asked the damper to move
    still = _box(np.full(96, 60.0), np.where(OCC, 72.0, 68.0))
    assert ActuatorStuck().analyze("VAV", still).severity == "ok"


def test_position_flat_while_its_demand_moves():
    frame = _box(
        np.full(96, 40.0),
        _mild(),
        **{
            Role.HEAT_VALVE_POSITION: np.zeros(96),
            Role.HEAT_VALVE: np.where(OCC, 50 + 50 * np.sin(H), 0.0),
        },
    )
    f = ActuatorStuck(roles=["heat_valve_position"]).analyze("VAV", frame)
    assert f.severity == "fault" and f.metrics["reason"] == "demand_moved"
    assert f.metrics["role"] == "heat_valve_position"


def test_heat_valve_position_falls_back_to_the_demand():
    frame = _box(np.full(96, 40.0), np.where(OCC, 66.0, 62.0), **{Role.HEAT_VALVE: np.zeros(96)})
    f = ActuatorStuck(roles=("heat_valve_position",)).analyze("VAV", frame)
    assert f.metrics["role"] == "heat_valve" and f.metrics["reason"] == "zone_cold"


def test_fan_off_samples_are_not_judged():
    frame = _box(np.zeros(96), _mild(), **{Role.SUPPLY_FAN_STATUS: np.zeros(96)})
    f = ActuatorStuck().analyze("FPB", frame)
    assert f.severity == "info" and "no active" in f.summary


def test_schedule_fallback_is_caveated_and_shares_grade_severity():
    idx = pd.date_range("2025-07-07", periods=24 * 14, freq="1h")
    occ = (idx.dayofweek < 5) & (idx.hour >= 7) & (idx.hour < 18)
    damper = np.where(occ, 40 + 20 * np.sin(idx.hour.to_numpy()), 0.0)
    first = idx < idx[0] + pd.Timedelta(days=1)
    damper = np.where(first, 0.0, damper)  # one stuck-shut day in two weeks
    frame = pd.DataFrame(
        {
            Role.DAMPER: damper,
            Role.SPACE_TEMP: np.where(occ, 73.0 + np.sin(idx.hour.to_numpy()), 70.0),
            Role.COOL_SP: np.full(len(idx), 75.0),
        },
        index=idx,
    )
    f = ActuatorStuck().analyze("VAV", frame)
    assert f.metrics["flat_runs"] == 1 and f.severity == "warn"  # 10 % of active samples
    assert any("assumed schedule" in c for c in f.caveats)
    assert ActuatorStuck(warn_pct=20).analyze("VAV", frame).severity == "info"


def test_no_actuator_with_data_and_param_validation():
    frame = _box(np.full(96, np.nan), _mild())
    assert ActuatorStuck().analyze("VAV", frame).severity == "info"
    with pytest.raises(ValueError, match="roles"):
        ActuatorStuck(roles=["oa_damper"])
    with pytest.raises(ValueError):
        ActuatorStuck(roles=[])
    with pytest.raises(ValueError, match="warn_pct"):
        ActuatorStuck(warn_pct=60, fault_pct=50)
    with pytest.raises(ValueError, match="whole_day_share"):
        ActuatorStuck(whole_day_share=0)
    assert ActuatorStuck(roles="damper").roles == (Role.DAMPER,)


def test_registration_and_config_params():
    reg = builtin_registry()
    rule = reg.get("actuator_stuck")
    assert rule.roles_required == () and Role.DAMPER in rule.roles_any_of[0]
    assert Role.HEAT_VALVE in rule.roles_any_of[0]  # the position's fallback
    r = make_rule("actuator_stuck", roles=["damper"], min_flat_hours=6)
    assert r.roles == (Role.DAMPER,) and r.min_flat_hours == 6.0


def test_faultlab_scenario_scores_the_rule():
    idx = faultlab._idx(21)
    rule = ActuatorStuck()
    assert rule.analyze("E", faultlab._actuator_stuck(idx, faulty=True)).severity == "fault"
    assert rule.analyze("E", faultlab._actuator_stuck(idx, faulty=False)).severity == "ok"
    assert "actuator_stuck" in faultlab.SCENARIOS


# --------------------------------------------------------------------------- recommenders


def test_actuator_stuck_recommendation():
    f = ActuatorStuck().analyze(
        "VAV", _box(np.full(96, 20.0), np.where(OCC & (H >= 12) & (H < 19), 79.0, _mild()))
    )
    rec = recommend(f)
    assert rec is not None and rec.rule == "actuator_stuck"
    assert rec.cause.startswith("Damper stuck at 20 %") and "warm" in rec.cause
    assert rec.references == ["pnnl-retuning-ch7"] and rec.confidence == "medium"
    warn = Finding("actuator_stuck", "VAV", "warn", {"tier": "unexplained_flat"})
    assert recommend(warn).confidence == "low"


def test_reheat_divergence_recommends_a_repair():
    m = {"valve_divergence_share": 0.6}
    rec = recommend(Finding("reheat_penalty", "FPU", "warn", m))
    assert rec.title == "Repair the reheat valve or actuator"
    assert rec.cause == (
        "reheat valve stuck or failed shut (the controller calls for heat the valve does not "
        "deliver)"
    )
    assert rec.references == ["pnnl-retuning-ch7"]
    low = recommend(Finding("reheat_penalty", "FPU", "warn", {"valve_divergence_share": 0.1}))
    assert low.title.startswith("Minimize reheat")
    none = recommend(Finding("reheat_penalty", "FPU", "warn", {"valve_divergence_share": None}))
    assert none.title.startswith("Minimize reheat")


# --------------------------------------------------------------------------- cohort options


def _fleet():
    """Two air handlers, four boxes each; box B2 never moves and B2's twin ref is healthy."""
    idx = pd.date_range("2025-07-07", periods=48, freq="1h")
    t = np.arange(48)
    frames = {}
    for ahu, scale in (("A", 1.0), ("B", 3.0)):
        for i in range(4):
            eq = f"{ahu}_VAV_{i}"
            wave = 40 + (8 + i) * np.sin(t / 3.0 + i)
            if eq == "B_VAV_2":
                wave = np.full(48, 40.0)
            frames[eq] = pd.DataFrame(
                {Role.AIRFLOW: scale * 10 * wave, Role.DAMPER: wave}, index=idx
            )
    return frames


class _Topo:
    provenance = "semantic"

    def group_map(self, keys):
        return {k: k.split("_")[0] for k in keys if not k.startswith("REF")}


def test_cohort_defaults_unchanged():
    frames = _fleet()
    a = CohortDeviation(Role.AIRFLOW, name="cohort_airflow").analyze_fleet(frames)
    assert set(a.metrics) == {"n", "summary", "k", "outliers", "z", "median", "mad"}
    assert not hasattr(CohortDeviation(Role.AIRFLOW), "wants_topology")


def test_cohort_grouping_and_low_tail_variability():
    frames = _fleet()
    rule = CohortDeviation(
        Role.DAMPER, group_by_topology=True, summary="variability", tail="low", k=2.0
    )
    assert rule.wants_topology
    f = rule.analyze_fleet(frames, topology=_Topo())
    assert f.metrics["outliers"] == ["B_VAV_2"] and f.metrics["n_groups"] == 2
    assert f.metrics["outliers_by_group"] == {"B": ["B_VAV_2"]}
    high = CohortDeviation(Role.DAMPER, summary="variability", tail="high", k=2.0)
    assert high.analyze_fleet(frames).metrics["outliers"] == []


def test_cohort_small_groups_are_unscored_and_no_topology_pools():
    frames = {k: v for k, v in _fleet().items() if k.startswith("A") or k == "B_VAV_0"}
    f = CohortDeviation(Role.AIRFLOW, group_by_topology=True).analyze_fleet(
        frames, topology=_Topo()
    )
    assert f.metrics["unscored_small_groups"] == ["B_VAV_0"]
    pooled = CohortDeviation(Role.AIRFLOW, group_by_topology=True).analyze_fleet(frames)
    assert pooled.metrics["n"] == 5 and any("no zone->AHU" in c for c in pooled.caveats)
    tiny = CohortDeviation(Role.AIRFLOW, group_by_topology=True, min_cohort=9)
    assert tiny.analyze_fleet(frames).severity == "info"


def test_cohort_normalise_design_max_and_reference():
    frames = _fleet()
    design = {eq: (1200.0 if eq.startswith("B") else 400.0) for eq in frames}
    design.pop("A_VAV_0")
    f = CohortDeviation(Role.AIRFLOW, normalise="design_max", design_max=design).analyze_fleet(
        frames
    )
    assert "A_VAV_0" in f.metrics["left_out"] and f.metrics["normalise"] == "design_max"
    assert any("cannot by itself single out" in c for c in f.caveats)
    # the AIRFLOW_SP peak stands in for a missing design value
    frames["A_VAV_0"] = frames["A_VAV_0"].assign(**{Role.AIRFLOW_SP: 400.0})
    g = CohortDeviation(Role.AIRFLOW, normalise="design_max", design_max=design).analyze_fleet(
        frames
    )
    assert "A_VAV_0" not in g.metrics["left_out"]
    # reference: each unit against itself on a known-good period
    frames = _fleet()
    good = _fleet()
    good["B_VAV_2"] = good["B_VAV_1"]  # B_VAV_2 moved like its neighbour before it stuck
    for eq, fr in good.items():
        frames[f"REF_{eq}"] = fr
    ref = {eq: f"REF_{eq}" for eq in good}
    r = CohortDeviation(
        Role.DAMPER, summary="variability", normalise="reference", reference=ref, k=2.0
    ).analyze_fleet(frames)
    assert not any(k.startswith("REF") for k in r.metrics["z"])
    assert r.metrics["outliers"] == ["B_VAV_2"]


def test_cohort_option_validation_and_make_rule():
    for bad in ({"summary": "median"}, {"normalise": "size"}, {"tail": "left"}):
        with pytest.raises(ValueError):
            CohortDeviation(Role.AIRFLOW, **bad)
    with pytest.raises(ValueError, match="reference"):
        CohortDeviation(Role.AIRFLOW, normalise="reference")
    r = make_rule("cohort_airflow", group_by_topology=True, tail="low", summary="variability")
    assert r.name == "cohort_airflow" and r.wants_topology and r.role is Role.AIRFLOW
