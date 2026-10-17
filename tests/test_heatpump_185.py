"""Heat-pump rule fixes (0.106, #185): coarse-trend handling in ``heatpump_defrost`` and
``compressor_staging``, stage changes counted only while running, a defrost read as heating by the
mode rules, the capacity "control" wording, and a fighting room's capacity findings."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.rules.base import Finding  # noqa: E402
from camber.rules.compressor_stage_rule import (  # noqa: E402
    CompressorStaging,
    _binary_changes,
    _running_stage_changes,
)
from camber.rules.heatpump_ops_rule import (  # noqa: E402
    HPCapacityShortfall,
    HPModeVsNeed,
    HPRoomImbalance,
    _defer_capacity_to_room_fight,
)
from camber.rules.heatpump_rule import HeatPumpDefrost  # noqa: E402

_IDX = pd.date_range("2026-01-05", periods=4 * 24 * 12, freq="5min")  # four days, Monday start


def _defrosting_valve(every: int) -> pd.Series:
    """Heating, with a one-sample (5-minute) defrost every ``every`` samples."""
    v = np.ones(len(_IDX))
    v[every // 2 :: every] = 0.0
    return pd.Series(v, index=_IDX)


def _resample(s: pd.Series, rule: str = "15min") -> pd.Series:
    return s.resample(rule).mean()


# --------------------------------------------------------------------------- the counters


def test_binary_changes_counts_the_fewest_changes_a_resampled_command_allows():
    s = pd.Series([1, 1, 2 / 3, 1, 1], index=_IDX[:5], dtype=float)
    assert _binary_changes(s)[0] == 2  # a defrost inside heating: out and back
    s = pd.Series([1, 0.5, 0.5, 0, 0], index=_IDX[:5], dtype=float)
    assert _binary_changes(s)[0] == 1  # a changeover across two bins
    s = pd.Series([0.5, 1, 1, 0, 0.5], index=_IDX[:5], dtype=float)
    assert _binary_changes(s)[0] == 3  # part-way at either end holds one change each
    s = pd.Series([1, 0, 1, 0], index=_IDX[:4], dtype=float)
    assert _binary_changes(s)[:1] == (3,) and _binary_changes(s)[3] == 0  # plain 0/1
    s = pd.Series([0, 2, 2, 0], index=_IDX[:4], dtype=float)
    assert _binary_changes(s)[0] == 2  # not a 0/1 command: the rounded level count


def test_stage_changes_count_only_while_running():
    s = pd.Series([0, 1, 0, 1, 2, 1, 2, 0, 2, 0], index=_IDX[:10], dtype=float)
    n, _per_day, _span, partial = _running_stage_changes(s)
    assert n == 3 and partial == 0  # 1->2, 2->1, 1->2; every start and stop is left out


# --------------------------------------------------------------------------- compressor_staging


def test_a_unit_cycling_stage_one_is_not_hunting():
    on_off = pd.Series(np.tile([0.0] * 4 + [1.0] * 4, len(_IDX) // 8), index=_IDX)  # 3 cycles/h
    f = CompressorStaging().analyze("WSHP-1", pd.DataFrame({Role.COMPRESSOR_STAGE: on_off}))
    assert f.severity == "ok" and f.metrics["n_changes"] == 0, f.metrics
    assert f.metrics["interval_min"] == 5.0 and not f.caveats


def test_staging_on_a_coarse_trend_declines_below_the_limit_and_stands_above_it():
    hunt = pd.Series(np.tile([1.0, 1.0, 2.0], len(_IDX) // 3), index=_IDX)  # 2 changes / 15 min
    fine = CompressorStaging().analyze("WSHP-1", pd.DataFrame({Role.COMPRESSOR_STAGE: hunt}))
    assert fine.severity == "fault" and not fine.caveats
    coarse = _resample(hunt).to_frame(Role.COMPRESSOR_STAGE)  # every bin reads 1.33
    f = CompressorStaging().analyze("WSHP-1", coarse)
    assert f.severity == "info" and f.metrics["n_partial"] == len(coarse), f.metrics
    assert "cannot confirm on a 15-minute trend" in f.summary
    assert "max_changes_per_day" in f.caveats[0]
    held = pd.Series(np.tile([1.0, 2.0], len(_IDX) // 6), index=_IDX[::3])  # whole stages
    g = CompressorStaging().analyze("WSHP-1", held.to_frame(Role.COMPRESSOR_STAGE))
    assert g.severity == "fault" and "floor" in g.caveats[0]  # at the limit: stands, caveated
    calm = pd.Series(1.0, index=_IDX[::3])
    h = CompressorStaging().analyze("WSHP-1", calm.to_frame(Role.COMPRESSOR_STAGE))
    assert h.severity == "ok" and len(h.caveats) == 1  # no part-way samples: ok with the caveat


# --------------------------------------------------------------------------- heatpump_defrost


def test_a_coarse_trend_keeps_a_defrost_fault_and_says_the_count_is_a_floor():
    valve = _defrosting_valve(6)  # a defrost every 30 minutes: 96 changes a day
    fine = HeatPumpDefrost().analyze("HP-1", valve.to_frame(Role.REVERSING_VALVE_CMD))
    assert fine.severity == "fault" and not fine.caveats
    f = HeatPumpDefrost().analyze("HP-1", _resample(valve).to_frame(Role.REVERSING_VALVE_CMD))
    assert f.severity == "fault", f.metrics
    # each 0.67 bin is two changes; the last bin ends the trend, so it can vouch for only one
    assert f.metrics["n_reversals"] == fine.metrics["n_reversals"] - 1
    assert f.metrics["interval_min"] == 15.0 and "floor" in f.caveats[0]


def test_a_coarse_trend_cannot_vouch_for_a_healthy_defrost_count():
    valve = _defrosting_valve(36)  # a defrost every 3 hours: 16 changes a day
    fine = HeatPumpDefrost().analyze("HP-1", valve.to_frame(Role.REVERSING_VALVE_CMD))
    assert fine.severity == "ok" and not fine.caveats
    f = HeatPumpDefrost().analyze("HP-1", _resample(valve).to_frame(Role.REVERSING_VALVE_CMD))
    assert f.severity == "info" and "cannot confirm on a 15-minute trend" in f.summary
    assert f.metrics["n_partial"] > 0 and "max_reversals_per_day" in f.caveats[0]
    # a coarser limit lets the 15-minute trend vouch for it again
    g = HeatPumpDefrost(max_interval_min=15.0).analyze(
        "HP-1", _resample(valve).to_frame(Role.REVERSING_VALVE_CMD)
    )
    assert g.severity == "ok" and not g.caveats


# --------------------------------------------------------------------------- the mode rules


def _ashp(valve: np.ndarray, zat: float = 64.0) -> pd.DataFrame:
    """An air-source unit heating a cold room all day, its valve given (1 = heating)."""
    n = len(_IDX)
    occ = (_IDX.dayofweek < 5) & (_IDX.hour >= 7) & (_IDX.hour < 18)
    return pd.DataFrame(
        {
            Role.SPACE_TEMP: np.full(n, zat),
            Role.SUPPLY_AIR_TEMP: np.full(n, 95.0),
            Role.OCCUPANCY: occ.astype(float),
            Role.COMPRESSOR_STATUS: np.ones(n),
            Role.REVERSING_VALVE_CMD: valve,
        },
        index=_IDX,
    )


def test_a_defrost_is_not_cooling_a_cold_room():
    valve = np.ones(len(_IDX))
    valve[6::12] = 0.0  # a 5-minute defrost every hour
    valve[7::24] = 0.0  # every other one 10 minutes
    f = HPModeVsNeed().analyze("ASHP-1", _ashp(valve))
    assert f.severity == "ok" and f.metrics["cooling_while_cold_hours"] == 0.0, f.metrics
    assert f.metrics["defrost_hours"] > 0 and f.metrics["mode_source"] == "status"
    # read without the defrost reading, the same unit cools a cold room
    g = HPModeVsNeed(max_defrost_min=0).analyze("ASHP-1", _ashp(valve))
    assert g.severity == "warn" and g.metrics["cooling_while_cold_hours"] > 2, g.metrics
    assert g.metrics["defrost_hours"] == 0.0


def test_a_long_reversal_is_still_cooling():
    valve = np.ones(len(_IDX))
    for start in range(6, len(_IDX), 48):
        valve[start : start + 4] = 0.0  # 20 minutes reversed: longer than a defrost
    f = HPModeVsNeed().analyze("ASHP-1", _ashp(valve))
    assert f.severity in ("warn", "fault") and f.metrics["defrost_hours"] == 0.0, f.metrics


def test_a_defrost_counts_as_heating_for_capacity():
    valve = np.ones(len(_IDX))
    valve[6::6] = 0.0  # a defrost every 30 minutes
    f = HPCapacityShortfall().analyze("ASHP-1", _ashp(valve))
    assert f.metrics["heating_working_pct"] == 100.0 and f.metrics["verdict"] == "capacity"


def test_the_control_verdict_says_when_the_unit_ran_the_other_way():
    n = len(_IDX)
    occ = (_IDX.dayofweek < 5) & (_IDX.hour >= 7) & (_IDX.hour < 18)
    heating = np.where(_IDX.minute < 25, 100.0, 79.0)  # heats part of every hour, else idle
    frame = pd.DataFrame(
        {
            Role.SPACE_TEMP: np.full(n, 80.0),
            Role.SUPPLY_AIR_TEMP: heating,
            Role.OCCUPANCY: occ.astype(float),
        },
        index=_IDX,
    )
    f = HPCapacityShortfall().analyze("WSHP-1", frame)
    assert f.metrics["side"] == "cooling" and f.metrics["verdict"] == "control", f.metrics
    assert f.metrics["cooling_opposite_pct"] > 30
    assert "mostly idle" not in f.summary
    assert "cooling only 0% of that time, heating 42% and idle 58%" in f.summary, f.summary
    idle = frame.assign(**{Role.SUPPLY_AIR_TEMP: 79.0})
    assert "mostly idle" in HPCapacityShortfall().analyze("WSHP-1", idle).summary


# --------------------------------------------------------------------------- a fighting room


def _cap(equip, severity, verdict, side="heating"):
    return Finding(
        "hp_capacity_shortfall",
        equip,
        severity,
        {"verdict": verdict, "side": side, f"{side}_outside_pct": 27.4},
        "capacity",
    )


def test_a_fighting_room_takes_the_cause_off_capacity():
    fleet = Finding(
        HPRoomImbalance.name,
        "fleet",
        "fault",
        {
            "rooms": {
                "R1": {"units": ["A", "B"], "fight_pct": 62.1, "fights": True},
                "R2": {"units": ["C", "D"], "fight_pct": 0.0, "fights": False},
            }
        },
    )
    a, b = _cap("A", "fault", "capacity"), _cap("B", "warn", "control", "cooling")
    c, d = _cap("C", "fault", "capacity"), _cap("D", "ok", "ok")
    findings = [a, b, c, d, fleet]
    assert _defer_capacity_to_room_fight(findings) == 1
    assert a.severity == "fault" and a.metrics["verdict"] == "room_fight"
    assert a.metrics["verdict_alone"] == "capacity" and a.metrics["room"] == "R1"
    assert a.metrics["deferred_to"] == "hp_room_imbalance"
    assert "fight" in a.summary and "not capacity" in a.summary and a.caveats
    assert b.metrics["verdict"] == "control"  # a control verdict stands
    assert c.metrics["verdict"] == "capacity"  # R2 does not fight
    assert _defer_capacity_to_room_fight([_cap("A", "fault", "capacity")]) == 0  # no fleet
