"""0.98 (#93): dcv_verification names below-floor hours with the supply fan off, and an opt-in
duration fault (``below_floor_fault_hours``) catches a concentrated below-floor episode."""

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.aso import recommend  # noqa: E402
from camber.faultlab import _idx, dcv_sim  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.base import Finding  # noqa: E402
from camber.rules.ventilation_rule import (  # noqa: E402
    DcvSystemVerification,
    DemandControlledVentilation,
)
from camber.schedules import occupied_mask  # noqa: E402
from camber.ventilation import assess_dcv  # noqa: E402

FLOOR = 600.0  # the simulated unit takes 720-960 cfm whenever occupied and running


def _unit(*, fan_off_days=(), speed=True, short_days=()):
    """A working DCV unit; on ``fan_off_days`` (day-of-month) the supply fan stays off through the
    occupied hours, on ``short_days`` it runs but OA sits at 300 cfm (a real shortfall)."""
    f = dcv_sim(_idx(21), control="proportional", economizer=False)
    occ = occupied_mask(f.index)
    for day in fan_off_days:
        rows = occ & (f.index.day == day)
        f.loc[rows, [Role.SUPPLY_FAN_STATUS, Role.OA_AIRFLOW]] = 0.0
    for day in short_days:
        rows = occ & (f.index.day == day)
        f.loc[rows, Role.OA_AIRFLOW] = 300.0
    if speed:  # a speed feedback instead of a status point; ~2 % at rest, as on lbnl-b59
        f[Role.SUPPLY_FAN_SPEED] = 2.0 + 58.0 * f[Role.SUPPLY_FAN_STATUS]
        f = f.drop(columns=[Role.SUPPLY_FAN_STATUS])
    return f


def _rule(**kw):
    return DemandControlledVentilation(oa_floor_cfm=FLOOR, **kw)


# ------------------------------------------------------------------ assess_dcv


def test_assess_dcv_splits_fan_off_from_the_shortfall():
    idx = pd.date_range("2025-07-07", periods=48, freq="1h")
    oa = pd.Series(800.0, idx)
    oa.iloc[10:16] = 0.0  # fan off: 6 h
    oa.iloc[30:32] = 300.0  # fan running, OA short: 2 h
    off = pd.Series(False, idx)
    off.iloc[10:16] = True
    co2 = pd.Series(600.0, idx)
    plain = assess_dcv(oa, co2, oa_floor=FLOOR)
    split = assess_dcv(oa, co2, oa_floor=FLOOR, fan_off_mask=off)
    assert plain.below_floor_pct == plain.below_floor_total_pct == pytest.approx(100 * 8 / 48, 0.01)
    assert plain.fan_off_below_floor_pct is None
    assert split.below_floor_total_pct == plain.below_floor_pct  # nothing lost
    assert split.fan_off_below_floor_pct == pytest.approx(100 * 6 / 48, 0.01)
    assert split.below_floor_pct == pytest.approx(100 * 2 / 48, 0.01)
    assert split.below_floor_longest_h == 6.0 and split.below_floor_longest_fan_off_h == 6.0
    assert split.below_floor_longest_start == str(idx[10])
    assert plain.below_floor_longest_fan_off_h == 0.0
    none = assess_dcv(oa, co2)
    assert none.below_floor_pct is None and none.below_floor_longest_h is None


# ------------------------------------------------------------------ the rule


def test_fan_off_days_are_named_and_severity_does_not_move():
    f = _unit(fan_off_days=(8,))  # one weekday: ~7 % of occupied samples, under the 10 % share
    got = _rule().analyze("AHU-1", f)
    m = got.metrics
    assert m["fan_off_source"] == "fan speed"
    assert m["below_floor_pct"] == 0.0  # no shortfall with the fan running
    assert m["fan_off_occupied_pct"] == m["below_floor_total_pct"] > 0.0
    assert m["fan_off_occupied_hours"] == 11.0  # the day's 11 occupied hours
    assert "supply fan off while scheduled occupied for 11 h" in got.summary
    assert any(c.startswith("supply fan off while scheduled occupied: 11 h") for c in got.caveats)
    # the same unit without a fan signal: the pre-0.98 reading, and the same severity
    legacy = _rule().analyze("AHU-1", f.drop(columns=[Role.SUPPLY_FAN_SPEED]))
    assert legacy.metrics["below_floor_pct"] == m["below_floor_total_pct"]
    assert legacy.metrics["fan_off_source"] is None
    assert legacy.metrics["fan_off_occupied_hours"] is None
    assert "fan off" not in legacy.summary
    assert legacy.severity == got.severity


def test_share_fault_still_reads_every_below_floor_sample():
    f = _unit(fan_off_days=(7, 8, 9, 10, 11))  # a whole week of 3: ~33 % of occupied samples
    got = _rule().analyze("AHU-1", f)
    legacy = _rule().analyze("AHU-1", f.drop(columns=[Role.SUPPLY_FAN_SPEED]))
    assert got.severity == legacy.severity == "fault"
    assert got.metrics["below_floor_pct"] == 0.0 and got.metrics["below_floor_total_pct"] > 30
    assert "supply fan off while scheduled occupied" in got.summary
    assert "OA short with the fan running 0.0%" in got.summary


def test_a_running_shortfall_stays_a_shortfall():
    f = _unit(short_days=(8,))
    got = _rule().analyze("AHU-1", f)
    assert got.metrics["fan_off_occupied_hours"] == 0.0
    assert got.metrics["below_floor_pct"] == got.metrics["below_floor_total_pct"] > 0.0
    assert "fan off" not in got.summary


def test_status_point_counts_fan_off_hours_apart():
    f = _unit(fan_off_days=(8,), speed=False)
    got = _rule().analyze("AHU-1", f)
    m = got.metrics
    # the fan gate already kept them out of the floor check; now they are counted too
    assert m["fan_off_source"] == "fan status"
    assert m["below_floor_pct"] == m["below_floor_total_pct"] == 0.0
    assert m["fan_off_occupied_hours"] == 11.0 and m["fan_off_occupied_pct"] > 0.0
    assert any("read from the fan status" in c for c in got.caveats)


def test_fan_off_speed_threshold_is_tunable():
    f = _unit(fan_off_days=(8,))
    assert _rule(fan_off_speed_pct=1.0).analyze("AHU-1", f).metrics["fan_off_occupied_hours"] == 0
    assert _rule().analyze("AHU-1", f).metrics["fan_off_occupied_hours"] == 11.0


# ------------------------------------------------------------------ the opt-in duration fault


def test_duration_fault_is_opt_in():
    f = _unit(short_days=(8,))  # 11 h in a row, ~7 % of occupied samples: under the 10 % share
    off = _rule().analyze("AHU-1", f)
    assert off.metrics["below_floor_longest_h"] == 11.0
    assert off.severity != "fault"
    on = _rule(below_floor_fault_hours=4.0).analyze("AHU-1", f)
    assert on.severity == "fault"
    assert "OA below the floor for 11 occupied h in a row" in on.summary
    assert "supply fan off" not in on.summary
    assert _rule(below_floor_fault_hours=12.0).analyze("AHU-1", f).severity != "fault"


def test_duration_fault_names_a_fan_off_episode():
    f = _unit(fan_off_days=(8, 9))  # consecutive days: one 22 h run across the night
    got = _rule(below_floor_fault_hours=4.0).analyze("AHU-1", f)
    assert got.severity == "fault"
    assert got.metrics["below_floor_longest_h"] == 22.0
    assert "22 occupied h in a row" in got.summary
    assert "(supply fan off while scheduled occupied)" in got.summary


def test_system_rule_passes_the_new_params():
    rule = DcvSystemVerification(below_floor_fault_hours=4.0, fan_off_speed_pct=3.0)
    assert rule._judge_rule.below_floor_fault_hours == 4.0
    assert rule._judge_rule.fan_off_speed_pct == 3.0


# ------------------------------------------------------------------ the recommender


def test_recommender_reads_a_fan_off_cause():
    f = Finding(
        rule="dcv_verification",
        equip="AHU-1",
        severity="fault",
        metrics={
            "status": "insufficient",
            "below_floor_pct": 0.0,
            "below_floor_total_pct": 33.0,
            "fan_off_occupied_pct": 33.0,
            "fan_off_occupied_hours": 55.0,
        },
        summary="",
    )
    rec = recommend(f)
    assert rec.title == "Run the supply fan whenever the space is occupied"
    assert rec.cause == "Supply fan off while scheduled occupied" and "55 h" in rec.action
