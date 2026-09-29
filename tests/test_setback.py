"""Tests for the AHU night/weekend setback diagnostic (PNNL Ch.5)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.rules.base import Rule  # noqa: E402
from camber.rules.setback_rule import NightWeekendSetback  # noqa: E402
from camber.setback import analyze_setback  # noqa: E402


def _two_weeks():
    # hourly index spanning 14 days so both weekday/weekend + day/night appear
    return pd.date_range("2025-07-07", periods=24 * 14, freq="1h")  # Monday start


def test_no_setback_fan_runs_always():
    idx = _two_weeks()
    df = pd.DataFrame({"SupplyFanStatus": np.ones(len(idx))}, index=idx)  # 24/7
    r = analyze_setback(df, "AHU_1")
    assert r.fan_run_unoccupied_pct > 95
    assert not r.setback_effective


def test_good_setback_fan_off_unoccupied():
    idx = _two_weeks()
    hour = idx.hour
    occ = (idx.dayofweek < 5) & (hour >= 7) & (hour < 18)
    df = pd.DataFrame({"SupplyFanStatus": np.where(occ, 1.0, 0.0)}, index=idx)
    r = analyze_setback(df, "AHU_2")
    assert r.fan_run_unoccupied_pct < 5
    assert r.fan_run_occupied_pct > 95
    assert r.setback_effective


def test_speed_fallback_when_no_status():
    idx = _two_weeks()
    df = pd.DataFrame({"SupplyFanSpeed": np.full(len(idx), 60.0)}, index=idx)  # always on
    r = analyze_setback(df, "AHU_3")
    assert r.fan_run_unoccupied_pct > 95
    assert not r.setback_effective


def test_rule_protocol_and_severity():
    rule = NightWeekendSetback()
    assert isinstance(rule, Rule)
    idx = _two_weeks()
    frame = pd.DataFrame({Role.SUPPLY_FAN_STATUS: np.ones(len(idx))}, index=idx)
    f = rule.analyze("AHU_1", frame)
    assert f.severity == "fault"
    assert f.metrics["fan_run_unoccupied_pct"] > 95


def test_rule_ok_when_setback_effective():
    rule = NightWeekendSetback()
    idx = _two_weeks()
    hour = idx.hour
    occ = (idx.dayofweek < 5) & (hour >= 7) & (hour < 18)
    frame = pd.DataFrame({Role.SUPPLY_FAN_STATUS: np.where(occ, 1.0, 0.0)}, index=idx)
    f = rule.analyze("AHU_2", frame)
    assert f.severity == "ok"


# ---------------------------------------------------------------- real-data regressions (0.82.0)


def _cycling_fan_csv(tmp_path):
    """Event-logged fan status over two weeks: on 07-22 every day (the real schedule), and at night
    a short-cycling fan -- 20 min on, 40 min off -- i.e. a ~33 % unoccupied duty."""
    rows = []
    for day in pd.date_range("2025-07-07", periods=14, freq="D"):
        rows.append((day + pd.Timedelta(hours=7), "On"))
        rows.append((day + pd.Timedelta(hours=22), "Off"))
        for h in list(range(22, 24)) + list(range(24, 31)):
            t = day + pd.Timedelta(hours=h, minutes=10)
            if t.hour >= 7 and h >= 24:
                continue
            rows.append((t, "On"))
            rows.append((t + pd.Timedelta(minutes=20), "Off"))
    rows.sort()
    p = tmp_path / "AHU_SF_Sts.csv"
    p.write_text("Timestamp,Value\n" + "".join(f"{t:%Y-%m-%d %H:%M:%S},{v}\n" for t, v in rows))
    return str(p)


def test_setback_verdict_is_stable_across_resample_intervals(tmp_path):
    # real case: a cycling fan read ok at 1-min/15-min but "fault, 50.8 % unoccupied" at hourly --
    # max-per-bin resampling called every hour the fan touched "on for the whole hour"
    from camber.realio import load_status

    path = _cycling_fan_csv(tmp_path)
    rule = NightWeekendSetback(start_hour=7, end_hour=22, occupied_days=range(7))
    got = {}
    for rs in ("1min", "15min", "1h"):
        s = load_status(path, "fan", resample=rs)
        f = rule.analyze("AHU", pd.DataFrame({Role.SUPPLY_FAN_STATUS: s}))
        got[rs] = (f.severity, f.metrics["fan_run_unoccupied_pct"])
    sevs = {v[0] for v in got.values()}
    assert len(sevs) == 1, got
    pcts = [v[1] for v in got.values()]
    assert max(pcts) - min(pcts) < 2.0, got
    assert 30.0 < pcts[0] < 36.0  # the true ~33 % night duty
    # the old "any-on" aggregation is still available -- and shows the inflation it caused
    anyon = load_status(path, "fan", resample="1h", how="any")
    f = rule.analyze("AHU", pd.DataFrame({Role.SUPPLY_FAN_STATUS: anyon}))
    assert f.metrics["fan_run_unoccupied_pct"] > 90


def test_duty_resample_preserves_on_time(tmp_path):
    from camber.realio import load_status

    path = _cycling_fan_csv(tmp_path)
    fine = load_status(path, "fan", resample="1min")
    coarse = load_status(path, "fan", resample="1h")
    assert coarse.between(0, 1).all()
    assert abs(fine.mean() - coarse.mean()) < 0.01
    import pytest

    with pytest.raises(ValueError):
        load_status(path, "fan", resample="1h", how="max")


def test_trended_occupancy_replaces_the_default_schedule():
    # a 07-22 every-day building: the default weekday 07-18 window calls its evenings and
    # weekends "unoccupied" and books a correctly scheduled fan as running 44 % of unoccupied
    # time; a trended OCCUPANCY point replaces the schedule
    idx = _two_weeks()
    occ = ((idx.hour >= 7) & (idx.hour < 22)).astype(float)
    frame = pd.DataFrame({Role.SUPPLY_FAN_STATUS: occ, Role.OCCUPANCY: occ}, index=idx)
    with_occ = NightWeekendSetback().analyze("AHU", frame)
    assert with_occ.metrics["fan_run_unoccupied_pct"] == 0.0 and not with_occ.caveats
    no_occ = frame.drop(columns=[Role.OCCUPANCY])
    f = NightWeekendSetback().analyze("AHU", no_occ)
    assert f.metrics["fan_run_unoccupied_pct"] > 40
    assert any("assumed schedule" in c for c in f.caveats)
    cfg = NightWeekendSetback(start_hour=7, end_hour=22, occupied_days=range(7))
    assert cfg.analyze("AHU", no_occ).metrics["fan_run_unoccupied_pct"] == 0.0


# ------------------------------------------------ 0.93 (#43): a fan cycling to hold the setback


def _held_frame(*, night_duty=0.6, night_zone=62.0, day_zone=70.0, minutes=False):
    """07-22 every day: fan on, zone at ``day_zone``; at night the fan runs ``night_duty`` of
    each hour and the zone sits at ``night_zone`` (the ORNL heating-setback-test shape)."""
    freq = "1min" if minutes else "1h"
    idx = pd.date_range("2025-01-06", periods=(14 * 24 * (60 if minutes else 1)), freq=freq)
    day = (idx.hour >= 7) & (idx.hour < 22)
    if minutes:
        night_on = idx.minute < round(60 * night_duty)
    else:
        night_on = np.full(len(idx), night_duty)
    fan = np.where(day, 1.0, night_on).astype(float)
    zone = np.where(day, day_zone, night_zone)
    return pd.DataFrame({Role.SUPPLY_FAN_STATUS: fan, Role.SPACE_TEMP: zone}, index=idx)


def _rule(**kw):
    return NightWeekendSetback(start_hour=7, end_hour=22, occupied_days=range(7), **kw)


def test_fan_cycling_to_hold_setback_is_effective():
    for minutes in (False, True):
        f = _rule().analyze("RTU", _held_frame(minutes=minutes))
        assert f.severity == "ok", f.summary
        assert f.metrics["setback_basis"] == "held_setback"
        assert f.metrics["held_side"] == "heating"
        assert f.metrics["zone_temp_source"] == "space_temp"
        assert 55.0 < f.metrics["unoccupied_duty_when_running_pct"] < 65.0
        assert "effective (fan cycling to hold)" in f.summary
        # no setpoint anywhere: the caveat says the setback was inferred from the zone alone
        assert any("no unoccupied setpoint" in c for c in f.caveats)
        assert f.metrics["fan_run_unoccupied_pct"] > 50  # the runtime is still reported


def test_fan_running_all_night_is_still_missing_even_if_the_zone_cools():
    # a fan that runs every unoccupied minute is not cycling to hold anything (the pre-heat and
    # baseline tests): the runtime verdict stands
    f = _rule().analyze("RTU", _held_frame(night_duty=1.0))
    assert f.severity == "fault" and f.metrics["setback_basis"] == "runtime"
    assert "MISSING" in f.summary


def test_cycling_fan_with_the_zone_at_comfort_is_missing():
    # the fan cycles at night but the zone stays at its occupied temperature: no setback
    f = _rule().analyze("RTU", _held_frame(night_zone=69.5))
    assert f.severity in ("warn", "fault") and f.metrics["setback_basis"] == "runtime"


def test_trended_setpoint_decides_and_vetoes():
    fr = _held_frame(night_zone=64.0)
    day = (fr.index.hour >= 7) & (fr.index.hour < 22)
    # a trended heating setpoint that sets back to 60 F: the zone at 64 F sits below the 65 F
    # midpoint between 70 and 60, though only 6 F under comfort
    fr[Role.HEAT_SP] = np.where(day, 70.0, 60.0)
    f = _rule(min_setback_depth_f=8.0).analyze("RTU", fr)
    assert f.severity == "ok" and f.metrics["unoccupied_heat_sp_f"] == 60.0
    assert not any("no unoccupied setpoint" in c for c in f.caveats)
    # a trended setpoint that never sets back vetoes the test, configured value or not
    fr[Role.HEAT_SP] = 70.0
    f = _rule(unoccupied_heat_sp_f=60.0).analyze("RTU", fr)
    assert f.metrics["setback_basis"] == "runtime" and f.severity != "ok"


def test_configured_setpoint_and_return_air_stand_in():
    fr = _held_frame(night_zone=63.5, day_zone=67.0).rename(
        columns={Role.SPACE_TEMP: Role.RETURN_AIR_TEMP}
    )
    # 3.5 F under the occupied return air passes the depth test on its own ...
    f = _rule().analyze("RTU", fr)
    assert f.severity == "ok" and f.metrics["zone_temp_source"] == "return_air_temp"
    assert any("return air" in c for c in f.caveats)
    # ... a deeper depth fails it, and a configured 60 F setback (midpoint 63.5 F) rescues it
    assert _rule(min_setback_depth_f=4.0).analyze("RTU", fr).severity != "ok"
    f = _rule(min_setback_depth_f=4.0, unoccupied_heat_sp_f=60.0).analyze("RTU", fr)
    assert f.severity == "ok" and "against a 60.0F setback" in f.summary


def test_cooling_setup_held():
    fr = _held_frame(night_zone=80.0, day_zone=74.0)
    f = _rule(unoccupied_cool_sp_f=82.0).analyze("RTU", fr)
    assert f.severity == "ok" and f.metrics["held_side"] == "cooling"


def test_no_zone_signal_caveats_the_runtime_verdict():
    fr = _held_frame().drop(columns=[Role.SPACE_TEMP])
    f = _rule().analyze("RTU", fr)
    assert f.severity in ("warn", "fault")
    assert any("runtime alone cannot tell" in c for c in f.caveats)
    assert f.metrics["zone_temp_source"] is None


def test_runtime_floor_from_57_is_kept():
    # below the 5 % absolute floor the verdict is effective on runtime, never re-judged
    fr = _held_frame(night_duty=0.02)
    f = _rule().analyze("RTU", fr)
    assert f.severity == "ok" and f.metrics["setback_basis"] == "runtime"
    assert "materiality floor" in f.summary


def test_cooling_side_needs_a_setpoint():
    # a zone warmer at night while the fan cycles may be a unit heating it at night: without an
    # unoccupied cooling setpoint the cooling side is not inferred
    fr = _held_frame(night_zone=80.0, day_zone=74.0)
    f = _rule().analyze("RTU", fr)
    assert f.metrics["setback_basis"] == "runtime" and f.severity != "ok"
