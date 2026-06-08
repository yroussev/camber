"""Tests for the hot-water-plant diagnostic (boiler summer-lockout + HW reset)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.plant import analyze_hw_plant  # noqa: E402
from camber.realio import load_status  # noqa: E402
from camber.rules.base import Rule  # noqa: E402
from camber.rules.boiler_rule import BoilerSummerLockout  # noqa: E402


def _idx(n):
    return pd.date_range("2025-07-07", periods=n, freq="1h")  # Monday


def test_summer_running_flagged():
    n = 24 * 21
    idx = _idx(n)
    # boiler running every occupied afternoon, OAT hot then
    hour = idx.hour
    boiler = np.where((hour >= 12) & (hour < 17), 1.0, 0.0)
    oat = np.where((hour >= 12) & (hour < 17), 95.0, 60.0)
    hws = np.full(n, 150.0)
    df = pd.DataFrame({"BoilerStatus": boiler, "OAT": oat, "HWS_Temp": hws}, index=idx)
    r = analyze_hw_plant(df, "HWP", summer_lockout_oat_f=70.0)
    assert r.n_running > 0
    assert r.summer_run_pct > 90        # nearly all running hours are hot-weather
    assert r.lockout_oat_f == 70.0


def test_no_summer_running_when_winter_only():
    n = 24 * 21
    idx = _idx(n)
    hour = idx.hour
    boiler = np.where((hour >= 5) & (hour < 9), 1.0, 0.0)   # morning only
    oat = np.full(n, 45.0)                                  # cold
    df = pd.DataFrame({"BoilerStatus": boiler, "OAT": oat,
                       "HWS_Temp": np.full(n, 150.0)}, index=idx)
    r = analyze_hw_plant(df, "HWP", summer_lockout_oat_f=70.0)
    assert r.summer_run_pct == 0.0


def test_hws_reset_detected():
    n = 24 * 30
    idx = _idx(n)
    rng = np.random.default_rng(0)
    oat = 70 + 20 * np.sin((idx.hour - 9) / 24 * 2 * np.pi) + rng.normal(0, 1, n)
    hws = 180 - 0.6 * (oat - 50) + rng.normal(0, 1, n)     # resets down as OAT rises
    df = pd.DataFrame({"BoilerStatus": np.ones(n), "OAT": oat, "HWS_Temp": hws}, index=idx)
    r = analyze_hw_plant(df, "HWP", summer_lockout_oat_f=70.0)
    assert r.hws_slope_per_F < -0.05
    assert r.hws_reset_present


def test_lockout_threshold_is_climate_param():
    # Same data, two climate thresholds -> different summer_run_pct (the climate knob)
    n = 24 * 21
    idx = _idx(n)
    oat = np.tile(np.linspace(50, 100, 24), 21)[:n]
    df = pd.DataFrame({"BoilerStatus": np.ones(n), "OAT": oat,
                       "HWS_Temp": np.full(n, 150.0)}, index=idx)
    hot = analyze_hw_plant(df, "HWP", summer_lockout_oat_f=85.0)   # desert: high lockout
    mild = analyze_hw_plant(df, "HWP", summer_lockout_oat_f=60.0)  # coastal: low lockout
    assert mild.summer_run_pct > hot.summer_run_pct


def test_boiler_rule_protocol_and_severity():
    rule = BoilerSummerLockout(summer_lockout_oat_f=70.0)
    assert isinstance(rule, Rule)
    n = 24 * 21
    idx = _idx(n)
    hour = idx.hour
    frame = pd.DataFrame({
        Role.BOILER_STATUS: np.where((hour >= 12) & (hour < 17), 1.0, 0.0),
        Role.OAT: np.where((hour >= 12) & (hour < 17), 95.0, 60.0),
        Role.HW_SUPPLY_TEMP: np.full(n, 150.0),
    }, index=idx)
    f = rule.analyze("HWP", frame)
    assert f.severity == "fault"
    assert f.metrics["summer_run_pct"] > 90


def test_load_status_maps_text(tmp_path):
    p = tmp_path / "B1_Sts.csv"
    p.write_text("Timestamp,Value\n"
                 "07-Jul-25 08:00:00 AM PDT,Off\n"
                 "07-Jul-25 10:00:00 AM PDT,Running\n"
                 "07-Jul-25 02:00:00 PM PDT,Off\n")
    s = load_status(str(p), "b", resample="1h")
    # forward-filled step series: 08-09 off, 10-13 running, 14+ off
    assert s.loc[pd.Timestamp("2025-07-07 09:00")] == 0.0
    assert s.loc[pd.Timestamp("2025-07-07 11:00")] == 1.0
