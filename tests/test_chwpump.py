"""Tests for the CHW pump DP-reset diagnostic (PNNL Ch.8)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.chwpump import analyze_chw_pump  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.base import Rule  # noqa: E402
from camber.rules.chwpump_rule import CHWPumpDPReset  # noqa: E402


def _idx(n):
    return pd.date_range("2025-07-07", periods=n, freq="1h")


def test_pinned_full_speed_flagged():
    n = 24 * 21
    df = pd.DataFrame(
        {"PumpSpeed": np.full(n, 100.0), "DiffPressSP": np.full(n, 14.0)}, index=_idx(n)
    )
    r = analyze_chw_pump(df, "CHWP")
    assert r.pct_running_near_full > 95
    assert not r.dp_sp_reset_present


def test_modulating_pump_ok():
    n = 24 * 21
    idx = _idx(n)
    rng = np.random.default_rng(0)
    spd = np.clip(55 + 20 * np.sin(np.arange(n) / 12) + rng.normal(0, 3, n), 20, 85)
    sp = 10 + 3 * np.sin(np.arange(n) / 12)  # DP setpoint resets
    df = pd.DataFrame({"PumpSpeed": spd, "DiffPressSP": sp}, index=idx)
    r = analyze_chw_pump(df, "CHWP")
    assert r.pct_running_near_full < 30
    assert r.dp_sp_reset_present


def test_off_hours_excluded():
    n = 24 * 21
    spd = np.where(np.arange(n) % 2 == 0, 100.0, 0.0)  # half off
    df = pd.DataFrame({"PumpSpeed": spd}, index=_idx(n))
    r = analyze_chw_pump(df, "CHWP")
    assert r.median_speed_pct == 100.0  # running hours only
    assert r.pct_running_near_full > 95


def test_pinned_min_speed_oversized():
    n = 24 * 21
    df = pd.DataFrame({"PumpSpeed": np.full(n, 18.0)}, index=_idx(n))  # always near min
    r = analyze_chw_pump(df, "CHWP")
    assert r.pct_running_near_min > 95
    assert r.pct_running_near_full < 5


def test_rule_protocol_and_severity():
    rule = CHWPumpDPReset()
    assert isinstance(rule, Rule)
    n = 24 * 21
    frame = pd.DataFrame({Role.CHW_PUMP_SPEED: np.full(n, 100.0)}, index=_idx(n))
    f = rule.analyze("CHWP1", frame)
    assert f.severity == "fault"
    assert f.metrics["pct_running_near_full"] > 95


def test_rule_oversized_warns():
    rule = CHWPumpDPReset()
    n = 24 * 21
    frame = pd.DataFrame({Role.CHW_PUMP_SPEED: np.full(n, 18.0)}, index=_idx(n))
    f = rule.analyze("CHWP1", frame)
    assert f.severity == "warn"  # pinned at VFD minimum -> oversized
    assert f.metrics["pct_running_near_min"] > 95


# ---- 0.105 (#180): a run status gates "running"; a parked speed is not read as the floor


def _parked(n=24 * 21):
    """A pump that stops every third hour while its speed point holds 34.5 % (a parked DP-loop
    output), and modulates 40-95 % while it runs."""
    rng = np.random.default_rng(1)
    on = np.arange(n) % 3 != 0
    run_spd = np.clip(70 + 20 * np.sin(np.arange(n) / 9) + rng.normal(0, 3, n), 40, 95)
    spd = np.where(on, run_spd, 34.5)
    return pd.DataFrame({"PumpSpeed": spd, "PumpStatus": on.astype(float)}, index=_idx(n))


def test_status_excludes_parked_speed():
    df = _parked()
    without = analyze_chw_pump(df.drop(columns="PumpStatus"), "P", near_min_pct="auto")
    with_st = analyze_chw_pump(df, "P", near_min_pct="auto")
    # speed alone reads the stopped third as running at a 34.5 % floor
    assert without.run_source == "speed" and without.n_stopped_speed_above_run is None
    assert without.vfd_floor_pct == 34.5 and without.pct_running_near_min > 30
    # the status says those hours were stopped: no floor plateau, nothing near the minimum
    assert with_st.run_source == "status"
    assert with_st.n_stopped_speed_above_run == len(df) // 3
    assert with_st.n_running == len(df) - len(df) // 3
    assert with_st.vfd_floor_pct is None and with_st.pct_running_near_min == 0.0
    assert with_st.median_speed_pct > without.median_speed_pct


def test_status_duty_above_half_is_running():
    n = 24 * 21
    duty = np.tile([1.0, 0.75, 0.5, 0.25, 0.0, np.nan], n // 6)
    df = pd.DataFrame({"PumpSpeed": np.full(n, 60.0), "PumpStatus": duty}, index=_idx(n))
    r = analyze_chw_pump(df, "P")
    assert r.n_running == 2 * n // 6  # 1.0 and 0.75; 0.5, 0.25, 0 and missing are not
    assert r.n_stopped_speed_above_run == 4 * n // 6


def test_all_nan_status_falls_back_to_speed():
    df = _parked().assign(PumpStatus=np.nan)
    a = analyze_chw_pump(df, "P", near_min_pct="auto")
    b = analyze_chw_pump(df.drop(columns="PumpStatus"), "P", near_min_pct="auto")
    assert str(a.as_dict()) == str(b.as_dict()) and a.run_source == "speed"


def test_status_limits_dp_setpoint_to_running_hours():
    df = _parked()
    on = df["PumpStatus"] > 0.5
    df["DiffPressSP"] = np.where(on, 12.0, np.linspace(5, 20, len(df)))  # wanders only while off
    assert analyze_chw_pump(df.drop(columns="PumpStatus"), "P").dp_sp_reset_present is True
    assert analyze_chw_pump(df, "P").dp_sp_reset_present is False


def test_rule_reads_pump_status_and_caveats():
    df = _parked()
    rule = CHWPumpDPReset()
    assert Role.PUMP_STATUS in rule.roles_optional
    frame = pd.DataFrame(
        {Role.CHW_PUMP_SPEED: df["PumpSpeed"], Role.PUMP_STATUS: df["PumpStatus"]}, index=df.index
    )
    f = rule.analyze("P", frame)
    assert f.metrics["run_source"] == "status"
    assert f.metrics["n_stopped_speed_above_run"] == len(df) // 3
    assert any("judged as stopped" in c for c in f.caveats)
    g = rule.analyze("P", frame.drop(columns=Role.PUMP_STATUS))
    assert g.metrics["run_source"] == "speed" and g.metrics["n_stopped_speed_above_run"] is None
    assert any("no pump run status" in c for c in g.caveats)
