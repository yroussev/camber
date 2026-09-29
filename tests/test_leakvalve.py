"""Tests for the leaking coil-valve diagnostic (PNNL Ch.5/Ch.7)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.leakvalve import analyze_leak_valves  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.base import Rule  # noqa: E402
from camber.rules.leakvalve_rule import LeakingValve  # noqa: E402


def _idx(n):
    return pd.date_range("2025-07-07", periods=n, freq="1h")


def test_chw_leak_detected():
    n = 24 * 21
    idx = _idx(n)
    # both valves shut, but supply air 7F BELOW mixed -> cooling coil leaking
    df = pd.DataFrame(
        {
            "CHW_Valve": np.zeros(n),
            "HHW_Valve": np.zeros(n),
            "MixedAir": np.full(n, 75.0),
            "SupplyAir": np.full(n, 68.0),
        },
        index=idx,
    )
    r = analyze_leak_valves(df, "AHU_1")
    assert r.chw_leak_pct > 95
    assert r.hw_leak_pct < 5


def test_hw_leak_detected():
    n = 24 * 21
    idx = _idx(n)
    # both valves shut, supply air 7F ABOVE mixed -> heating coil leaking
    df = pd.DataFrame(
        {
            "CHW_Valve": np.zeros(n),
            "HHW_Valve": np.zeros(n),
            "MixedAir": np.full(n, 70.0),
            "SupplyAir": np.full(n, 77.0),
        },
        index=idx,
    )
    r = analyze_leak_valves(df, "AHU_2")
    assert r.hw_leak_pct > 95
    assert r.chw_leak_pct < 5


def test_no_leak_when_sat_tracks_mat():
    n = 24 * 21
    idx = _idx(n)
    # both shut, supply ~ mixed + fan heat -> no leak
    df = pd.DataFrame(
        {
            "CHW_Valve": np.zeros(n),
            "HHW_Valve": np.zeros(n),
            "MixedAir": np.full(n, 72.0),
            "SupplyAir": np.full(n, 73.0),
        },
        index=idx,
    )
    r = analyze_leak_valves(df, "AHU_3")
    assert r.hw_leak_pct < 5
    assert r.chw_leak_pct < 5


def test_open_valve_hours_excluded():
    n = 24 * 21
    idx = _idx(n)
    # CHW valve open (commanded cooling) -> not a "both closed" hour, excluded
    df = pd.DataFrame(
        {
            "CHW_Valve": np.full(n, 80.0),
            "HHW_Valve": np.zeros(n),
            "MixedAir": np.full(n, 75.0),
            "SupplyAir": np.full(n, 55.0),
        },
        index=idx,
    )
    r = analyze_leak_valves(df, "AHU_4")
    assert r is None  # no both-closed hours


def test_rule_protocol_and_severity():
    rule = LeakingValve()
    assert isinstance(rule, Rule)
    n = 24 * 21
    idx = _idx(n)
    frame = pd.DataFrame(
        {
            Role.COOL_VALVE: np.zeros(n),
            Role.HEAT_VALVE: np.zeros(n),
            Role.MIXED_AIR_TEMP: np.full(n, 75.0),
            Role.SUPPLY_AIR_TEMP: np.full(n, 68.0),
        },
        index=idx,
    )
    f = rule.analyze("AHU_1", frame)
    assert f.severity == "fault"
    assert f.metrics["chw_leak_pct"] > 95


# ------------------------------------------------------ 0.93 (#42): fan heat and coil sensors


def _closed_frame(n=24 * 21, *, mat=60.0, sat=65.5, **extra):
    idx = _idx(n)
    cols = {
        Role.COOL_VALVE: np.zeros(n),
        Role.HEAT_VALVE: np.zeros(n),
        Role.MIXED_AIR_TEMP: np.full(n, mat),
        Role.SUPPLY_AIR_TEMP: np.full(n, sat),
    }
    cols.update({k: (np.full(n, v) if np.isscalar(v) else v) for k, v in extra.items()})
    return pd.DataFrame(cols, index=idx)


def test_fan_heat_is_a_parameter_with_the_g36_default():
    # a 5.5 F rise with both valves shut: a leak beyond 2 F of fan heat, none beyond 3 F
    f = LeakingValve().analyze("AHU", _closed_frame())
    assert f.metrics["fan_heat_f"] == 2.0 and f.severity == "fault"
    assert LeakingValve(fan_heat_f=3.0).analyze("AHU", _closed_frame()).severity == "ok"
    from camber.fdd_g36 import G36Thresholds

    assert LeakingValve().fan_heat_f == G36Thresholds().dT_sf


def test_fan_heat_is_not_credited_to_a_cooling_leak():
    # supply 2.5 F below mixed: fan heat is an allowance for a rise, never an offset that
    # would turn a small drop into a "cooling leak"
    f = LeakingValve().analyze("AHU", _closed_frame(mat=72.0, sat=69.5))
    assert f.metrics["chw_leak_pct"] == 0.0 and f.severity == "ok"


def test_coil_leaving_sensor_is_preferred_over_supply_air():
    # the supply air reads 5.5 F over the mixed air, but the heating coil's own leaving air
    # does not rise: the heat is downstream of the coil (fan / sensor), not a leak
    fr = _closed_frame(**{Role.HEAT_COIL_LEAVING_TEMP: 60.2, Role.COOL_COIL_LEAVING_TEMP: 60.1})
    f = LeakingValve().analyze("AHU", fr)
    assert f.severity == "ok" and f.metrics["hw_leak_pct"] == 0.0
    assert f.metrics["hw_basis"] == "heat_coil_leaving_temp"
    assert f.metrics["chw_basis"] == "cool_coil_leaving_temp"
    assert "judged on its own leaving air" in f.summary
    assert not any("supply air minus mixed air" in c for c in f.caveats)
    # a real leak shows on the coil's own leaving air
    fr[Role.HEAT_COIL_LEAVING_TEMP] = 66.0
    f = LeakingValve().analyze("AHU", fr)
    assert f.severity == "fault" and f.metrics["hw_median_delta_f"] == 6.0
    # a blow-through unit's coil sensors carry the fan heat too
    fr[Role.HEAT_COIL_LEAVING_TEMP] = 64.5
    assert LeakingValve().analyze("AHU", fr).severity == "fault"
    assert LeakingValve(coil_sensor_fan_heat=True).analyze("AHU", fr).severity == "ok"


def test_cooling_leak_on_its_own_coil_sensor():
    fr = _closed_frame(mat=75.0, sat=75.5, **{Role.COOL_COIL_LEAVING_TEMP: 68.0})
    f = LeakingValve().analyze("AHU", fr)
    assert f.severity == "fault" and f.metrics["chw_leak_pct"] == 100.0
    assert "cooling coil judged on its own leaving air" in f.summary


def test_fan_off_samples_are_left_out():
    n = 24 * 21
    on = (np.arange(n) % 24 >= 6) & (np.arange(n) % 24 < 18)
    # fan off: no air moving, the sensors drift apart (a false 8 F "rise"); fan on: clean
    sat = np.where(on, 61.0, 68.0)
    fr = _closed_frame(n, sat=sat, **{Role.SUPPLY_FAN_STATUS: on.astype(float)})
    f = LeakingValve().analyze("AHU", fr)
    assert f.severity == "ok" and f.metrics["fan_gated"] is True
    assert f.metrics["n_both_closed"] == int(on.sum())
    # without a fan signal they are judged (and caveated)
    g = LeakingValve().analyze("AHU", fr.drop(columns=[Role.SUPPLY_FAN_STATUS]))
    assert g.severity == "fault" and any("no fan status" in c for c in g.caveats)
    # a speed signal gates the same way
    fr2 = fr.drop(columns=[Role.SUPPLY_FAN_STATUS])
    fr2[Role.SUPPLY_FAN_SPEED] = np.where(on, 60.0, 0.0)
    assert LeakingValve().analyze("AHU", fr2).severity == "ok"
