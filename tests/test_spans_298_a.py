"""0.111 (#298): rules that judge samples return the samples they flagged as an evidence mask, so
finding_detail can give flagged-hour spans; event-count rules also mark each counted event.

Each test builds a tiny faulty frame, runs the rule and checks the hook's mask against the
finding's own count or share. No store, no network.
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.rules.actuator_stuck_rule import ActuatorStuck  # noqa: E402
from camber.rules.boiler_rule import BoilerSummerLockout  # noqa: E402
from camber.rules.chillerstaging_rule import ChillerStaging  # noqa: E402
from camber.rules.compressor_stage_rule import CompressorStaging  # noqa: E402
from camber.rules.filter_rule import FilterFouling  # noqa: E402
from camber.rules.heatpump_rule import HeatPumpDefrost  # noqa: E402
from camber.rules.hunting_rule import ControlHunting  # noqa: E402
from camber.rules.hwplant_deltat_rule import HWPlantDeltaT  # noqa: E402
from camber.rules.hwpump_rule import HWPumpDPReset  # noqa: E402
from camber.rules.source_loop_rule import SourceLoopDeltaT  # noqa: E402


def _idx(n, freq="15min", start="2024-07-01"):  # a Monday
    return pd.date_range(start, periods=n, freq=freq)


def _check(rule, frame):
    f = rule.analyze("EQ", frame)
    ev = rule.evidence("EQ", frame)
    assert ev is not None and ev.mask is not None, rule.name
    mask = pd.Series(ev.mask).astype(bool)
    assert mask.any(), rule.name
    return f, ev, mask


def test_actuator_stuck_mask_is_the_flagged_runs():
    idx = _idx(96 * 2)
    h = idx.hour.to_numpy() + idx.minute.to_numpy() / 60.0
    occ = (h >= 7) & (h < 22)
    frame = pd.DataFrame(
        {
            Role.DAMPER: np.zeros(len(idx)),
            Role.SPACE_TEMP: np.where(occ, 72.0 + 0.8 * np.sin(h / 3.0), 68.0),
            Role.COOL_SP: np.where(occ, 75.2, 85.0),
            Role.HEAT_SP: np.where(occ, 69.8, 60.0),
            Role.OCCUPANCY: occ.astype(float),
        },
        index=idx,
    )
    f, ev, mask = _check(ActuatorStuck(), frame)
    assert f.severity == "fault"
    assert round(100.0 * mask.sum() / f.metrics["n_active"], 2) == f.metrics["stuck_share"]
    assert not mask[~occ].any()  # only active (occupied) samples


def test_control_hunting_marks_each_reversal():
    idx = _idx(6 * 60, freq="min")
    sig = 50 + 20 * np.sign(np.sin(np.arange(len(idx)) * np.pi / 2 + 0.1))
    frame = pd.DataFrame({Role.COOL_VALVE: sig, Role.SUPPLY_FAN_STATUS: 1.0}, index=idx)
    f, ev, mask = _check(ControlHunting(), frame)
    assert f.severity in ("warn", "fault")
    assert int(pd.Series(ev.events).sum()) == f.metrics["reversals"]
    assert mask.iloc[
        5:-5
    ].all()  # it hunts the whole trend: hours are the stretch, not reversals x interval


def test_control_hunting_hours_skip_slow_stretches():
    idx = _idx(6 * 60, freq="min")
    n = np.arange(len(idx))
    fast = 50 + 20 * np.sign(np.sin(n * np.pi / 2 + 0.1))
    slow = 50 + 20 * np.sign(np.sin(n * np.pi / 60 + 0.1))  # a reversal every hour
    sig = np.where(n < 120, fast, slow)
    frame = pd.DataFrame({Role.COOL_VALVE: sig, Role.SUPPLY_FAN_STATUS: 1.0}, index=idx)
    rule = ControlHunting()
    ev = rule.evidence("EQ", frame)
    mask = pd.Series(ev.mask)
    assert mask.iloc[5:115].all() and not mask.iloc[130:].any()


def test_filter_fouling_marks_fan_on_samples_over_change_out():
    idx = _idx(96)
    fan = (np.arange(96) % 2).astype(float)
    dp = np.where(np.arange(96) < 72, 1.8, 0.5)
    frame = pd.DataFrame({Role.FILTER_DIFF_PRESS: dp, Role.SUPPLY_FAN_STATUS: fan}, index=idx)
    f, ev, mask = _check(FilterFouling(), frame)
    assert f.severity == "fault"
    pct = 100.0 * mask.sum() / f.metrics["n_judged"]
    assert round(pct, 1) == f.metrics["pct_over_threshold"]
    assert not mask[fan == 0].any()


def _plant(n, *, hws=140.0, hwr=130.0, oat=80.0, status=1.0):
    idx = _idx(n, freq="h")
    return pd.DataFrame(
        {
            Role.BOILER_STATUS: status,
            Role.HW_SUPPLY_TEMP: hws,
            Role.HW_RETURN_TEMP: hwr,
            Role.OAT: oat,
        },
        index=idx,
    )


def test_hw_plant_deltat_marks_occupied_running_low_delta_t():
    f, ev, mask = _check(HWPlantDeltaT(), _plant(24 * 7))
    assert f.severity == "fault"
    assert int(mask.sum()) == f.metrics["n_running"]  # every running sample is low


def test_boiler_summer_lockout_marks_firing_in_warm_weather():
    frame = _plant(24 * 7)
    frame[Role.OAT] = np.where(np.arange(len(frame)) % 2 == 0, 80.0, 50.0)
    f, ev, mask = _check(BoilerSummerLockout(), frame)
    assert f.severity == "fault"
    assert round(100.0 * mask.sum() / f.metrics["n_running"], 2) == f.metrics["summer_run_pct"]


def test_hw_pump_marks_running_samples_near_full_speed():
    idx = _idx(200)
    frame = pd.DataFrame({Role.HW_PUMP_SPEED: np.where(np.arange(200) < 150, 98.0, 60.0)}, idx)
    f, ev, mask = _check(HWPumpDPReset(), frame)
    assert f.severity == "fault"
    pct = round(100.0 * mask.sum() / f.metrics["n_running"], 1)
    assert pct == f.metrics["pct_running_near_full"]


def test_source_loop_marks_pumping_samples_with_a_flat_delta_t():
    idx = _idx(24 * 4, freq="h")
    ret = np.where(np.arange(len(idx)) < 92, 70.3, 75.0)
    frame = pd.DataFrame(
        {
            Role.SOURCE_LOOP_SUPPLY_TEMP: 70.0,
            Role.SOURCE_LOOP_RETURN_TEMP: ret,
            Role.PUMP_STATUS: 1.0,
        },
        index=idx,
    )
    f, ev, mask = _check(SourceLoopDeltaT(), frame)
    assert f.severity == "fault"
    assert round(100.0 * mask.sum() / len(idx), 1) == f.metrics["flat_pct"]


def test_heatpump_defrost_marks_each_reversal_and_its_excursion():
    idx = _idx(24 * 12, freq="5min")
    valve = np.ones(len(idx))
    valve[3::6] = 0.0  # a one-sample reversal out of heating every 30 minutes
    frame = pd.DataFrame({Role.REVERSING_VALVE_CMD: valve}, index=idx)
    f, ev, mask = _check(HeatPumpDefrost(), frame)
    assert f.severity == "fault"
    assert int(pd.Series(ev.events).sum()) == f.metrics["n_reversals"]
    assert int(mask.sum()) == int((valve == 0).sum())  # the excursions, not starts x interval


def test_compressor_staging_marks_each_change_while_running():
    idx = _idx(24 * 12, freq="5min")
    stage = np.where(np.arange(len(idx)) % 2 == 0, 1.0, 2.0)
    stage[:12] = 0.0  # a start is not a stage change
    frame = pd.DataFrame({Role.COMPRESSOR_STAGE: stage}, index=idx)
    f, ev, mask = _check(CompressorStaging(), frame)
    assert f.severity == "fault"
    assert int(pd.Series(ev.events).sum()) == f.metrics["n_changes"]
    assert not mask.iloc[:12].any()


def test_chiller_staging_marks_starts_and_the_runs_they_began():
    idx = _idx(24 * 4 * 3)
    on = (np.arange(len(idx)) % 8) < 2  # 30 minutes on in every two hours
    frame = pd.DataFrame({Role.POWER: np.where(on, 200.0, 0.0)}, index=idx)
    f, ev, mask = _check(ChillerStaging(), frame)
    assert f.severity == "fault"
    starts = int(pd.Series(ev.events).sum())
    assert starts == len(idx) // 8  # one per on-run
    assert abs(starts / f.metrics["n_days"] - f.metrics["starts_per_day"]) < 0.1
    assert int(mask.sum()) == int(on.sum())  # the running samples, not starts x interval
