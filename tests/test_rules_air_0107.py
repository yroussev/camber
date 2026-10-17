"""0.107 regressions for the air-side rules: #198 (airflow_tracking gating), #200 (the CO2 rules
honour a trended occupancy), #203 (static_pressure_reset range from fan-on samples) and #204 (ok
summaries that name no fault). Each test is the issue's repro."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role as R  # noqa: E402
from camber.rules.airflow_rule import AirflowTracking  # noqa: E402
from camber.rules.economizer_lockout_rule import EconomizerHighLimit  # noqa: E402
from camber.rules.iaq_rule import (  # noqa: E402
    _ASSUMED_SCHEDULE_CAVEAT,
    CO2Ventilation,
    CO2VentilationSystem,
)
from camber.rules.leakvalve_rule import LeakingValve  # noqa: E402
from camber.rules.overcooling_severity_rule import OvercoolingSeverity  # noqa: E402
from camber.rules.staticreset_rule import StaticPressureReset  # noqa: E402

# ------------------------------------------------------------------ #198 airflow_tracking


def _box(**extra):
    ix = pd.date_range("2024-07-01", periods=14 * 24, freq="1h")
    occ = pd.Series(((ix.hour >= 7) & (ix.hour < 18) & (ix.dayofweek < 5)).astype(float), index=ix)
    cols = {R.AIRFLOW: 500 * occ, R.AIRFLOW_SP: pd.Series(500.0, index=ix)}
    cols.update({k: (occ if v is None else v) for k, v in extra.items()})
    return pd.DataFrame(cols)


def test_airflow_tracking_judges_fan_on_occupied_hours_only():
    f = AirflowTracking().analyze("VAV-1", _box(**{R.SUPPLY_FAN_STATUS: None, R.OCCUPANCY: None}))
    assert f.severity == "ok", f.summary
    assert f.metrics["off_setpoint_pct"] == 0.0 and f.metrics["n_active"] == 110
    assert f.metrics["fan_gate"] == "fan status"
    assert f.metrics["occupancy_gate"] == "trended occupancy"
    assert "occupied fan-on hours" in f.summary and not f.caveats


def test_airflow_tracking_gates_on_either_signal_alone():
    fan = AirflowTracking().analyze("VAV-1", _box(**{R.SUPPLY_FAN_STATUS: None}))
    occ = AirflowTracking().analyze("VAV-1", _box(**{R.OCCUPANCY: None}))
    assert fan.severity == occ.severity == "ok"
    assert "fan-on hours" in fan.summary and "occupied hours" in occ.summary


def test_airflow_tracking_still_finds_a_starved_box_while_the_fan_runs():
    # measured airflow is never the fan proxy: a starved box reads zero flow, which is the fault
    fr = _box(**{R.SUPPLY_FAN_STATUS: None})
    fr[R.AIRFLOW] = 0.0
    f = AirflowTracking().analyze("VAV-1", fr)
    assert f.severity == "fault" and f.metrics["undershoot_pct"] == 100.0


def test_airflow_tracking_without_a_running_signal_is_caveated():
    f = AirflowTracking().analyze("VAV-1", _box())
    assert f.severity == "fault"  # judged as before, but it says why it may be wrong
    assert f.metrics["fan_gate"].startswith("ungated")
    assert any("SUPPLY_FAN_STATUS or OCCUPANCY" in c for c in f.caveats)


def test_airflow_tracking_absolute_tolerance():
    ix = pd.date_range("2024-07-01", periods=48, freq="1h")
    fr = pd.DataFrame({R.AIRFLOW: pd.Series(65.0, index=ix), R.AIRFLOW_SP: 50.0})  # +30 %, 15 cfm
    assert AirflowTracking().analyze("VAV-1", fr).severity == "fault"
    assert AirflowTracking(abs_tol=20.0).analyze("VAV-1", fr).severity == "ok"


# ------------------------------------------------------------------ #200 co2_ventilation


def _room():
    ix = pd.date_range("2024-07-01", periods=28 * 24, freq="1h")
    o = pd.Series(
        (ix.dayofweek.isin([1, 3]) & (ix.hour >= 9) & (ix.hour < 15)).astype(float), index=ix
    )
    co2 = pd.Series(430.0, index=ix).where(o == 0, 900.0)
    return pd.DataFrame({R.CO2: co2, R.OCCUPANCY: o})


def test_co2_ventilation_judges_the_trended_occupied_hours():
    f = CO2Ventilation().analyze("ZN-1", _room())
    assert f.severity == "ok", f.summary
    assert f.metrics["over_vent_pct"] == 0.0 and f.metrics["n_occupied"] == 48
    assert f.metrics["occupancy_basis"] == "trended occupancy"
    assert _ASSUMED_SCHEDULE_CAVEAT not in f.caveats


def test_co2_ventilation_on_the_assumed_schedule_says_so():
    f = CO2Ventilation().analyze("ZN-1", _room().drop(columns=[R.OCCUPANCY]))
    assert f.severity == "warn" and "over-ventilated" in f.summary
    assert f.metrics["occupancy_basis"].startswith("assumed schedule")
    assert _ASSUMED_SCHEDULE_CAVEAT in f.caveats


def test_co2_ventilation_system_honours_the_zone_occupancy():
    f = CO2VentilationSystem().analyze_fleet({"ZN-1": _room()})
    assert f.severity == "ok", f.summary
    assert f.metrics["per_zone"]["ZN-1"]["occupancy_basis"] == "trended occupancy"


# ------------------------------------------------------------------ #203 static_pressure_reset


def _static(days):
    ix = pd.date_range("2024-07-01", periods=24 * days, freq="1h")
    o = pd.Series(((ix.hour >= 7) & (ix.hour < 18)).astype(float), index=ix)
    return pd.DataFrame({R.DUCT_STATIC_SP: 1.5 * o, R.SUPPLY_FAN_STATUS: o})


def test_static_pressure_reset_range_and_median_are_fan_on():
    for days in (1, 14):
        f = StaticPressureReset().analyze("AHU-1", _static(days))
        assert f.severity == "warn", (days, f.summary)
        assert f.metrics["sp_range_inwc"] == 0.0 and f.metrics["sp_median_inwc"] == 1.5
        assert "range 0.00 inWC" in f.summary and "flat" in f.summary


def test_static_pressure_reset_with_the_fan_never_on_is_info():
    fr = _static(2)
    fr[R.SUPPLY_FAN_STATUS] = 0.0
    f = StaticPressureReset().analyze("AHU-1", fr)
    assert f.severity == "info" and "fan-on" in f.summary


# ------------------------------------------------------------------ #204 ok summaries


def test_overcooling_severity_ok_above_the_heating_setpoint():
    ix = pd.date_range("2024-07-01", periods=14 * 96, freq="15min")
    o = pd.Series(((ix.hour >= 7) & (ix.hour < 18) & (ix.dayofweek < 5)).astype(float), index=ix)
    st = pd.Series(72.0, index=ix)
    fr = pd.DataFrame(
        {R.SPACE_TEMP: st, R.COOL_SP: st * 0 + 74, R.HEAT_SP: st * 0 + 70, R.OCCUPANCY: o}
    )
    f = OvercoolingSeverity().analyze("ZN-1", fr)
    assert f.severity == "ok"
    assert f.metrics["max_depth_f"] == 0.0
    assert "no sustained overcooling below heating setpoint" in f.summary
    assert "overcooled" not in f.summary and "-2.0" not in f.summary


def test_economizer_high_limit_ok_names_no_fault():
    n = 200
    ix = pd.date_range("2024-07-01", periods=n, freq="1h")
    fr = pd.DataFrame(
        {
            R.OAT: pd.Series(np.where(np.arange(n) % 2 == 0, 90.0, 55.0), index=ix),
            R.OA_DAMPER: 0.5,
            R.MIXED_AIR_TEMP: 77.2,  # ~20 % OA when hot: locked out to its minimum
            R.RETURN_AIR_TEMP: 74.0,
        },
        index=ix,
    )
    f = EconomizerHighLimit().analyze("AHU", fr)
    assert f.severity == "ok"
    assert "not locked out" not in f.summary and "locked out above" in f.summary
    stuck = EconomizerHighLimit().analyze("AHU", fr.assign(**{R.MIXED_AIR_TEMP: 86.8}))
    assert stuck.severity == "fault" and "economizer not locked out" in stuck.summary


def test_leaking_valve_ok_names_no_coil():
    n = 24 * 21
    ix = pd.date_range("2025-07-07", periods=n, freq="1h")
    fr = pd.DataFrame(
        {
            R.COOL_VALVE: 0.0,
            R.HEAT_VALVE: 0.0,
            R.MIXED_AIR_TEMP: 72.0,
            R.SUPPLY_AIR_TEMP: 74.0,  # mixed air + the 2 F fan heat
        },
        index=ix,
    )
    f = LeakingValve().analyze("AHU", fr)
    assert f.severity == "ok"
    assert "worst:" not in f.summary and "air shifts" not in f.summary
    assert "no valve leak" in f.summary
    leak = LeakingValve().analyze("AHU", fr.assign(**{R.SUPPLY_AIR_TEMP: 68.0}))
    assert leak.severity == "fault" and "worst: CHW (cooling) coil" in leak.summary
