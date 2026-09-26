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
