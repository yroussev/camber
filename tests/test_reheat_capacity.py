"""Tests for the VAV 'zone below heating setpoint, reheat saturated' rule (0.93, #44)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber import faultlab  # noqa: E402
from camber.eval import benchmark  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.applicability import rule_equip_classes  # noqa: E402
from camber.rules.base import Rule  # noqa: E402
from camber.rules.builtin import builtin_registry, make_rule  # noqa: E402
from camber.rules.reheat_capacity_rule import ReheatCapacityShortfall  # noqa: E402
from camber.scorecard import RULE_CATEGORY  # noqa: E402


def _box(*, zone=67.0, valve=100.0, sp=70.0, days=21, occupancy=True, **extra):
    idx = pd.date_range("2025-01-06", periods=24 * days, freq="1h")  # a Monday
    occ = (idx.dayofweek < 5) & (idx.hour >= 7) & (idx.hour < 18)
    cols = {
        Role.SPACE_TEMP: np.where(occ, zone, 64.0),
        Role.HEAT_VALVE: np.where(occ, valve, 0.0),
    }
    if sp is not None:
        cols[Role.HEAT_SP] = np.where(occ, sp, 60.0)
    if occupancy:
        cols[Role.OCCUPANCY] = occ.astype(float)
    cols.update(extra)
    return pd.DataFrame(cols, index=idx)


def test_protocol_registration_and_gating():
    rule = ReheatCapacityShortfall()
    assert isinstance(rule, Rule)
    assert "reheat_capacity_shortfall" in builtin_registry().names()
    assert rule_equip_classes(rule) == ("terminal",)  # boxes only; heat pumps are #40
    assert RULE_CATEGORY["reheat_capacity_shortfall"] == "comfort"
    assert make_rule("reheat_capacity_shortfall", heat_sp_f=70).heat_sp_f == 70


def test_saturated_and_cold_is_a_fault():
    f = ReheatCapacityShortfall().analyze("VAV-1", _box())
    assert f.severity == "fault", f.summary
    m = f.metrics
    assert m["setpoint_source"] == "data"
    assert m["median_deficit_f"] == 3.0
    assert m["shortfall_hours"] >= 10
    assert 80.0 < m["shortfall_pct"] <= 100.0
    assert m["underheated_unsaturated_pct"] == 0.0


def test_cold_with_reheat_to_spare_is_not_this_rule():
    # the valve is only half open: a control problem, reported but not flagged here
    f = ReheatCapacityShortfall().analyze("VAV-1", _box(valve=50.0))
    assert f.severity == "ok" and f.metrics["shortfall_pct"] == 0.0
    assert f.metrics["underheated_pct"] > 80.0
    assert "with reheat to spare" in f.summary


def test_saturated_but_holding_setpoint_is_ok():
    f = ReheatCapacityShortfall().analyze("VAV-1", _box(zone=69.8))
    assert f.severity == "ok" and f.metrics["valve_saturated_pct"] > 80.0


def test_morning_recovery_is_excluded():
    # the zone recovers from setback in the first two occupied hours with the valve wide open,
    # then holds setpoint: expected warm-up, not a shortfall
    fr = _box(zone=70.0, valve=40.0)
    first = (fr.index.hour >= 7) & (fr.index.hour < 9) & (fr.index.dayofweek < 5)
    fr.loc[first, Role.SPACE_TEMP] = 65.0
    fr.loc[first, Role.HEAT_VALVE] = 100.0
    f = ReheatCapacityShortfall().analyze("VAV-1", fr)
    assert f.severity == "ok" and f.metrics["shortfall_pct"] == 0.0
    assert f.metrics["n_recovery_excluded"] > 0
    # with no recovery allowance the same warm-up reads as a shortfall
    g = ReheatCapacityShortfall(recovery_hours=0).analyze("VAV-1", fr)
    assert g.metrics["shortfall_pct"] > 10.0


def test_setpoint_from_config_or_declined():
    fr = _box(sp=None)
    f = ReheatCapacityShortfall().analyze("VAV-1", fr)
    assert f.severity == "info" and "no zone heating setpoint" in f.summary
    f = ReheatCapacityShortfall(heat_sp_f=70.0).analyze("VAV-1", fr)
    assert f.severity == "fault" and f.metrics["setpoint_source"] == "config"
    assert any("from the config" in c for c in f.caveats)
    # a per-box mapping: only the named box is judged
    per = ReheatCapacityShortfall(heat_sp_f={"VAV-1": 70.0})
    assert per.analyze("VAV-1", fr).severity == "fault"
    assert per.analyze("VAV-2", fr).severity == "info"


def test_schedule_fallback_is_caveated():
    f = ReheatCapacityShortfall().analyze("VAV-1", _box(occupancy=False))
    assert f.severity == "fault"
    assert any("assumed schedule" in c for c in f.caveats)


def test_airflow_tells_capacity_from_airflow():
    n = 24 * 21
    starved = _box(**{Role.AIRFLOW: np.full(n, 200.0), Role.AIRFLOW_SP: np.full(n, 400.0)})
    f = ReheatCapacityShortfall().analyze("VAV-1", starved)
    assert f.metrics["likely_cause"] == "airflow" and f.metrics["airflow_to_sp_ratio"] == 0.5
    assert "an airflow problem" in f.summary
    full = _box(**{Role.AIRFLOW: np.full(n, 400.0), Role.AIRFLOW_SP: np.full(n, 400.0)})
    f = ReheatCapacityShortfall().analyze("VAV-1", full)
    assert f.metrics["likely_cause"] == "capacity" and "heating capacity problem" in f.summary
    assert not any("not told apart" in c for c in f.caveats)
    dat = _box(**{Role.SUPPLY_AIR_TEMP: np.full(n, 82.0)})
    f = ReheatCapacityShortfall().analyze("VAV-1", dat)
    assert f.metrics["discharge_temp_f"] == 82.0
    assert any("not told apart" in c for c in f.caveats)


def test_minimum_hours_and_missing_inputs():
    # a real shortfall that lasted only a few hours is not flagged
    fr = _box(zone=70.0, valve=40.0, days=21)
    cold = (fr.index.day == 15) & (fr.index.hour >= 12) & (fr.index.hour < 16)
    fr.loc[cold, Role.SPACE_TEMP] = 66.0
    fr.loc[cold, Role.HEAT_VALVE] = 100.0
    f = ReheatCapacityShortfall(warn_pct=1.0).analyze("VAV-1", fr)
    assert f.metrics["shortfall_hours"] == 4.0 and f.severity == "ok"
    no_valve = _box().drop(columns=[Role.HEAT_VALVE])
    assert ReheatCapacityShortfall().analyze("VAV-1", no_valve).severity == "info"
    empty = _box(occupancy=True)
    empty[Role.OCCUPANCY] = 0.0
    assert ReheatCapacityShortfall().analyze("VAV-1", empty).severity == "info"


def test_evidence_masks_the_shortfall():
    rule = ReheatCapacityShortfall()
    ev = rule.evidence("VAV-1", _box())
    assert ev.roles == [Role.SPACE_TEMP, Role.HEAT_SP] and ev.mask.any()
    assert rule.evidence("VAV-1", _box(sp=None)) is None
    assert ReheatCapacityShortfall(heat_sp_f=70).evidence("VAV-1", _box(sp=None)).roles == [
        Role.SPACE_TEMP
    ]
    assert rule.evidence("VAV-1", _box().drop(columns=[Role.HEAT_VALVE])) is None


def test_faultlab_scenario_scores_clean():
    # promoted from PENDING_SCENARIOS to the gated SCENARIOS at the 0.93 sign-off
    assert "reheat_capacity_shortfall" in faultlab.SCENARIOS
    assert "reheat_capacity_shortfall" not in faultlab.PENDING_SCENARIOS
    sc = {"reheat_capacity_shortfall": faultlab.SCENARIOS["reheat_capacity_shortfall"]}
    recs = faultlab.labeled_records(scenarios=sc)
    rep = benchmark(recs, faultlab.targets(sc))
    c = rep.per_detector["reheat_capacity_shortfall"]
    assert c.true_positive_rate == 1.0 and c.false_positive_rate == 0.0


def test_warmup_flag_and_fan_off_are_excluded():
    fr = _box(zone=70.0, valve=40.0)
    mid = (fr.index.hour >= 12) & (fr.index.hour < 16) & (fr.index.dayofweek < 5)
    fr.loc[mid, Role.SPACE_TEMP] = 65.0
    fr.loc[mid, Role.HEAT_VALVE] = 100.0
    assert ReheatCapacityShortfall().analyze("VAV-1", fr).severity == "fault"
    # the same hours flagged as warm-up by the BAS
    w = fr.copy()
    w[Role.WARMUP] = mid.astype(float)
    assert ReheatCapacityShortfall().analyze("VAV-1", w).metrics["shortfall_pct"] == 0.0
    # ... or with the box's own fan off (a fan-powered box delivering no air)
    for role, off in ((Role.SUPPLY_FAN_STATUS, 0.0), (Role.SUPPLY_FAN_SPEED, 0.0)):
        g = fr.copy()
        g[role] = np.where(mid, off, 1.0 if role == Role.SUPPLY_FAN_STATUS else 50.0)
        assert ReheatCapacityShortfall().analyze("VAV-1", g).metrics["shortfall_pct"] == 0.0
