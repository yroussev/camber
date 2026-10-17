"""0.106 (#183, #184): plant run gates, the chiller fleet's membership and capacity, tower load
with a variable condenser flow, and label scores over the equipment a rule applies to."""

import numpy as np
import pandas as pd
import pytest

from camber.chillerstaging import analyze_chiller_staging_fleet
from camber.datasets._labels import records_from_findings, score_records
from camber.eval import benchmark
from camber.model.equipclass import equip_family, is_chw_loop
from camber.model.roles import HAYSTACK_HINT, Role
from camber.rules.base import Finding
from camber.rules.chiller_approach_rule import ChillerApproachFouling
from camber.rules.chillerfleet_rule import ChillerStagingFleet
from camber.rules.chwplant_rule import (
    LOOP_FLOW_GATE_CAVEAT,
    LOOP_POWER_GATE_CAVEAT,
    POWER_GATE_CAVEAT,
    CHWSupplyTracking,
)
from camber.rules.coolingtower_drift_rule import CoolingTowerApproachDrift
from camber.rules.tower_fan_effort_rule import CoolingTowerFanEffortDrift
from camber.store.modelstore import BaselineStore


def _idx(n, freq="1h"):
    return pd.date_range("2025-07-07", periods=n, freq=freq)


def _with_class(frame, cls):
    frame.attrs["camber_equip_class"] = cls
    return frame


# --------------------------------------------------------------------------- loop classes


@pytest.mark.parametrize(
    "cls, loop",
    [
        ("CHW_LOOP", True),
        ("ChilledWaterLoop", True),
        ("CHW_SEC_LOOP", True),
        ("SECLOOP", True),
        ("SECONDARY_LOOP", True),
        ("CHILLER", False),
        ("CHW_PLANT", False),
        ("CH1", False),
        ("HPLOOP", False),  # a heat-pump source loop, not chilled water
        ("", False),
        (None, False),
    ],
)
def test_is_chw_loop(cls, loop):
    assert is_chw_loop(cls) is loop


def test_loop_spellings_are_chilled_water_family():
    assert equip_family("CHWLOOP") == "chw_plant"
    assert equip_family("CHILLED_WATER_LOOP") == "chw_plant"


# --------------------------------------------------------------------------- #183: the fleet


def _always_on(n, kw, freq="1h"):
    return pd.DataFrame({Role.POWER: np.full(n, float(kw))}, index=_idx(n, freq))


def test_fleet_leaves_a_chilled_water_loop_out():
    n = 24 * 7
    frames = {
        "CH1": _with_class(_always_on(n, 100.0), "CHILLER"),
        "CH2": _with_class(_always_on(n, 100.0), "CHILLER"),
        "SECLOOP": _with_class(_always_on(n, 12.0), "CHW_LOOP"),
    }
    f = ChillerStagingFleet().analyze_fleet(frames)
    assert f.metrics["n_chillers"] == 2 and f.metrics["n_loops_excluded"] == 1
    assert any("SECLOOP" in c and "pumps'" in c for c in f.caveats)


def test_fleet_with_only_loops_says_so():
    n = 24 * 7
    frames = {"SECLOOP": _with_class(_always_on(n, 12.0), "CHW_LOOP")}
    f = ChillerStagingFleet().analyze_fleet(frames)
    assert f.severity == "info" and f.metrics["n_loops_excluded"] == 1


def test_declared_capacity_replaces_the_observed_one():
    # two chillers always on at 100 kW: their own p95 says each is a 100 kW machine, so 200 kW
    # cannot fit in one -- but they are 250 kW machines, and 200 kW fits in one at 90 %
    n = 24 * 7
    frames = {"CH1": _always_on(n, 100.0), "CH2": _always_on(n, 100.0)}
    observed = ChillerStagingFleet().analyze_fleet(frames)
    assert observed.metrics["capacity_source"] == "observed_p95"
    assert observed.metrics["rep_capacity_kw"] == 100.0
    assert observed.severity == "ok" and observed.metrics["pct_overstaged"] == 0.0
    assert any("capacity_kw" in c for c in observed.caveats)

    declared = ChillerStagingFleet(capacity_kw=250.0).analyze_fleet(frames)
    assert declared.metrics["capacity_source"] == "declared"
    assert declared.metrics["rep_capacity_kw"] == 250.0
    assert declared.severity == "fault" and declared.metrics["pct_overstaged"] == 100.0
    assert not any("capacity_kw" in c for c in declared.caveats)


def test_declared_capacity_per_chiller_and_mixed():
    n = 24 * 7
    legacy = {
        "CH1": pd.DataFrame({"Power": np.full(n, 100.0)}, index=_idx(n)),
        "CH2": pd.DataFrame({"Power": np.full(n, 100.0)}, index=_idx(n)),
    }
    both = analyze_chiller_staging_fleet(legacy, capacity_kw={"CH1": 240.0, "CH2": 260.0})
    assert both.capacity_source == "declared" and both.rep_capacity_kw == 250.0
    one = analyze_chiller_staging_fleet(legacy, capacity_kw={"CH1": 300.0})
    assert one.capacity_source == "mixed" and one.rep_capacity_kw == 200.0
    with pytest.raises(ValueError):
        ChillerStagingFleet(capacity_kw=0)


@pytest.mark.parametrize("freq, hours_per_sample", [("1h", 1.0), ("15min", 0.25)])
def test_fleet_counts_samples_and_hours(freq, hours_per_sample):
    n = 96 * 3
    a = pd.DataFrame({"Power": np.full(n, 100.0)}, index=_idx(n, freq))
    b = pd.DataFrame({"Power": np.where(np.arange(n) % 2 == 0, 100.0, 0.0)}, index=_idx(n, freq))
    r = analyze_chiller_staging_fleet({"CH1": a, "CH2": b})
    assert r.n_running_samples == n and r.n_multi_samples == n // 2
    assert r.running_hours == pytest.approx(n * hours_per_sample)
    assert r.multi_hours == pytest.approx(n // 2 * hours_per_sample)
    assert r.sample_interval_min == pytest.approx(60.0 * hours_per_sample)


# --------------------------------------------------------------------------- #184: loop gate


def _loop_frame(n=24 * 14, *, status=False, flow=True, power=True, cls="CHW_LOOP"):
    """A loop that runs 06-19, holding 44 F; off hours read warm and its pump power holds 0."""
    idx = _idx(n)
    on = (idx.hour >= 6) & (idx.hour < 19)
    f = pd.DataFrame(
        {
            Role.CHW_SUPPLY_TEMP: np.where(on, 44.0, 60.0),
            Role.CHW_SUPPLY_TEMP_SP: 44.0,
        },
        index=idx,
    )
    if status:
        f[Role.PUMP_STATUS] = on.astype(float)
    if flow:
        f[Role.CHW_FLOW] = np.where(on, 400.0, 0.0)
    if power:
        f[Role.POWER] = np.where(on, 15.0, 0.0)
    return _with_class(f, cls)


@pytest.mark.parametrize(
    "kw, source, caveat",
    [
        ({"status": True}, "pump_status", None),
        ({}, "flow", LOOP_FLOW_GATE_CAVEAT),
        ({"flow": False}, "pump_power", LOOP_POWER_GATE_CAVEAT),
    ],
)
def test_tracking_gates_a_loop_on_its_pumps(kw, source, caveat):
    f = CHWSupplyTracking().analyze("SECLOOP", _loop_frame(**kw))
    assert f.metrics["run_source"] == source
    assert f.severity == "ok" and f.metrics["above_pct"] == 0.0
    assert POWER_GATE_CAVEAT not in f.caveats  # its power is never called a chiller's
    if caveat:
        assert caveat in f.caveats


def test_tracking_still_reads_a_chillers_power_as_a_chiller():
    f = CHWSupplyTracking().analyze("CH1", _loop_frame(flow=False, cls="CHILLER"))
    assert f.metrics["run_source"] == "power" and POWER_GATE_CAVEAT in f.caveats


# --------------------------------------------------------------------------- #184: approach gate


def _approach_frame(*, status=True, power=False, n=24 * 14):
    idx = _idx(n)
    on = (idx.hour >= 6) & (idx.hour < 19)
    # running: a clean 3 F condenser approach; off: the controller holds a stale 12 F
    f = pd.DataFrame({Role.COND_APPROACH_TEMP: np.where(on, 3.0, 12.0)}, index=idx)
    if status:
        f[Role.COMPRESSOR_STATUS] = on.astype(float)
    if power:
        f[Role.POWER] = np.where(on, 150.0, 0.0)
    return f, int(on.sum())


def test_approach_reads_running_samples_only():
    frame, n_on = _approach_frame()
    f = ChillerApproachFouling().analyze("CH1", frame)
    assert f.metrics["run_source"] == "status" and f.metrics["n_judged"] == n_on
    assert f.metrics["cond_approach_f"] == 3.0 and f.severity == "ok"

    frame, n_on = _approach_frame(status=False, power=True)
    f = ChillerApproachFouling().analyze("CH1", frame)
    assert f.metrics["run_source"] == "power" and f.metrics["cond_approach_f"] == 3.0
    assert any("compressor_status" in c for c in f.caveats)


def test_approach_without_a_gate_reads_everything_and_says_so():
    frame, _ = _approach_frame(status=False)
    f = ChillerApproachFouling().analyze("CH1", frame)
    assert f.metrics["run_source"] == "none" and f.metrics["n_judged"] == len(frame)
    assert any("every trended" in c for c in f.caveats)


def test_approach_of_a_chiller_that_never_ran_is_not_judged():
    frame, _ = _approach_frame()
    frame[Role.COMPRESSOR_STATUS] = 0.0
    f = ChillerApproachFouling().analyze("CH1", frame)
    assert f.severity == "info" and f.metrics["n_judged"] == 0


# --------------------------------------------------------------------------- #184: tower load


def _tower(*, staged=False, seed=0, days=30):
    """A tower rejecting 100-300 tons; its fan follows the heat and the wet-bulb. ``staged``: half
    the samples run a second chiller and condenser pump, so the flow doubles and the range halves
    for the same heat."""
    idx = pd.date_range("2026-07-01", periods=days * 24, freq="1h")
    rng = np.random.default_rng(seed)
    n = len(idx)
    wb = 66 + 6 * np.sin(np.arange(n) / 24 * 2 * np.pi) + rng.normal(0, 1, n)
    heat = 100 + 200 * rng.random(n)  # tons rejected
    flow = np.where(staged & (np.arange(n) % 2 == 1), 1200.0, 600.0)
    rng_f = heat * 24.0 / flow
    fan = np.clip(20 + 0.25 * heat - 0.8 * (wb - 66) + rng.normal(0, 2, n), 0, 100)
    leave = wb + 7.0
    return pd.DataFrame(
        {
            Role.TOWER_FAN_SPEED: fan,
            Role.CW_SUPPLY_TEMP: leave,
            Role.CW_RETURN_TEMP: leave + rng_f,
            Role.CW_FLOW: flow,
            Role.WETBULB_TEMP: wb,
        },
        index=idx,
    )


def _fan(base, cur):
    rule = CoolingTowerFanEffortDrift(BaselineStore(), site="S", run_id="R")
    return rule.analyze_periods("CT-1", base, cur)


def test_fan_effort_on_heat_rejection_is_not_fooled_by_staging():
    base, cur = _tower(), _tower(staged=True, seed=1)
    f = _fan(base, cur)
    assert f.metrics["load_source"] == "cw_heat_rejection" and f.severity == "ok"


def test_fan_effort_on_the_range_reads_staging_as_effort_and_says_why():
    drop = [Role.CW_FLOW]
    base, cur = _tower().drop(columns=drop), _tower(staged=True, seed=1).drop(columns=drop)
    f = _fan(base, cur)
    assert f.metrics["load_source"] == "cw_range" and f.severity in ("warn", "fault")
    assert any("constant condenser flow" in c for c in f.caveats)


def test_tower_approach_drift_runs_on_a_tower_of_its_own():
    rule = CoolingTowerApproachDrift(BaselineStore(), site="S", run_id="R")
    assert Role.CHW_FLOW not in rule.roles_required
    base, cur = _tower(), _tower(seed=1)
    f = rule.analyze_periods("CT-1", base, cur)
    assert f.metrics["tower_approach_load_source"] == "cw_heat_rejection"
    assert f.severity == "ok"
    widened = cur.copy()
    widened[Role.CW_SUPPLY_TEMP] += 5.0
    widened[Role.CW_RETURN_TEMP] += 5.0
    rule = CoolingTowerApproachDrift(BaselineStore(), site="S", run_id="R")
    f = rule.analyze_periods("CT-1", base, widened)
    assert f.severity == "fault"
    no_flow = [Role.CW_FLOW]
    rule = CoolingTowerApproachDrift(BaselineStore(), site="S", run_id="R")
    f = rule.analyze_periods("CT-1", base.drop(columns=no_flow), widened.drop(columns=no_flow))
    assert f.metrics["tower_approach_load_source"] == "cw_range" and f.severity == "fault"
    assert any("constant condenser flow" in c for c in f.caveats)


def test_tower_approach_drift_declines_without_a_load():
    rule = CoolingTowerApproachDrift(BaselineStore(), site="S", run_id="R")
    base = _tower().drop(columns=[Role.CW_RETURN_TEMP, Role.CW_FLOW])
    f = rule.analyze_periods("CT-1", base, base)
    assert f.severity == "info" and f.metrics["reason"] == "no_load"


def test_cw_flow_is_a_role_with_a_hint():
    assert Role("cw_flow") is Role.CW_FLOW and "condenser" in HAYSTACK_HINT[Role.CW_FLOW]


# --------------------------------------------------------------------------- #184: scoring


def _f(rule, equip, severity="ok", **metrics):
    return Finding(rule=rule, equip=equip, severity=severity, metrics=metrics)


def test_a_rule_is_scored_only_on_the_equipment_it_ran_on():
    labels = {"CH1": "", "CH2": "", "TOWER_A": "tower_fouled", "TOWER_B": "", "TOWER_C": ""}
    findings = [
        _f("tower_rule", "TOWER_A", "fault"),
        _f("tower_rule", "TOWER_B", "warn"),
        _f("tower_rule", "TOWER_C"),
        _f("chiller_rule", "CH1"),
        _f("chiller_rule", "CH2"),
        # a class decline is not "ran here"
        _f(
            "tower_rule",
            "CH1",
            "info",
            declined=True,
            applies_to_classes=["cooling_tower"],
        ),
    ]
    targets = {"tower_rule": "tower_fouled", "chiller_rule": "condenser_fouled"}
    recs = records_from_findings(findings, labels, suite=sorted(targets))
    by = {r["equip"]: r for r in recs}
    assert by["CH1"]["scored"] == ["chiller_rule"]
    assert by["TOWER_B"]["scored"] == ["tower_rule"]
    s = score_records(recs, targets)
    tower = s["per_detector"]["tower_rule"]
    assert (tower["n"], tower["tp"], tower["fp"], tower["tn"]) == (3, 1, 1, 1)
    assert tower["fpr"] == 0.5  # 1 of the 2 fault-free towers, not 1 of 4 pieces of equipment
    assert s["overall"]["n"] == 5  # the overall confusion still counts every record


def test_a_rule_that_ran_nowhere_is_scored_everywhere():
    labels = {"CH1": "condenser_fouled", "CH2": ""}
    recs = records_from_findings([_f("other", "CH1")], labels, suite=["chiller_rule"])
    assert all("chiller_rule" in r["scored"] for r in recs)
    s = score_records(recs, {"chiller_rule": "condenser_fouled"})
    assert s["per_detector"]["chiller_rule"]["fn"] == 1


def test_benchmark_without_scored_counts_every_record():
    recs = [{"truth": "x", "fired": {"d"}}, {"truth": "", "fired": set()}]
    rep = benchmark(recs, {"d": "x"})
    assert rep.per_detector["d"].total == 2
