"""#63: free_cooling_missed vs an integrated economizer; reheat_penalty vs contradictory valve data;
static/SAT reset rules vs one-time steps and flat setpoints. Synthetic fixtures only."""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.fault_economics import EquipmentLoad, estimate_cost  # noqa: E402
from camber.freecooling import integrated_economizer_mask  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.freecoolingmissed_rule import FreeCoolingMissed  # noqa: E402
from camber.rules.reheat_rule import ReheatPenalty  # noqa: E402
from camber.rules.satreset_rule import SupplyAirReset  # noqa: E402
from camber.rules.staticreset_rule import StaticPressureReset  # noqa: E402
from camber.setpoint_reset import classify_setpoint_reset  # noqa: E402


def _idx(days=14, freq="1h"):
    per_day = int(pd.Timedelta("1D") / pd.Timedelta(freq))
    return pd.date_range("2025-06-02", periods=days * per_day, freq=freq)  # a Monday


# ---------------------------------------------------------------- free_cooling_missed + damper


def _cool_mild(idx):
    """OAT 50-58 °F all window; mechanical cooling on all the time."""
    oat = 54 + 4 * np.sin(np.arange(len(idx)) / 24 * 2 * np.pi)
    return pd.Series(oat, index=idx), pd.Series(40.0, index=idx)


def test_integrated_economizer_by_damper_is_not_missed():
    idx = _idx()
    oat, cv = _cool_mild(idx)
    base = {Role.OAT: oat, Role.COOL_VALVE: cv}
    no_damper = FreeCoolingMissed().analyze("AHU", pd.DataFrame(base))
    assert no_damper.severity == "fault"
    assert no_damper.metrics["integrated_economizer_basis"] is None
    assert any("integrated economizer" in c for c in no_damper.caveats)

    integrated = FreeCoolingMissed().analyze(
        "AHU", pd.DataFrame({**base, Role.OA_DAMPER: pd.Series(100.0, index=idx)})
    )
    assert integrated.severity == "ok"
    assert integrated.metrics["missed_pct"] == 0.0
    assert integrated.metrics["integrated_economizer_basis"] == "damper"
    assert integrated.metrics["n_integrated_economizer_samples"] == len(idx)
    assert integrated.metrics["econ_damper_min_pct"] == 90.0
    assert not integrated.caveats
    assert "integrated economizer, not counted" in integrated.summary

    # a 0-1 damper signal is rescaled; at minimum OA the cooling is missed free cooling
    at_min = FreeCoolingMissed().analyze(
        "AHU", pd.DataFrame({**base, Role.OA_DAMPER: pd.Series(0.2, index=idx)})
    )
    assert at_min.severity == "fault" and at_min.metrics["n_integrated_economizer_samples"] == 0


def test_integrated_economizer_by_oa_fraction_overrides_a_stuck_damper():
    idx = _idx()
    oat, cv = _cool_mild(idx)
    rat = pd.Series(72.0, index=idx)
    damper = pd.Series(100.0, index=idx)  # commanded full open ...
    stuck_mat = 0.8 * rat + 0.2 * oat  # ... but the mixed air says ~20 % OA
    stuck = FreeCoolingMissed().analyze(
        "AHU",
        pd.DataFrame(
            {
                Role.OAT: oat,
                Role.COOL_VALVE: cv,
                Role.OA_DAMPER: damper,
                Role.MIXED_AIR_TEMP: stuck_mat,
                Role.RETURN_AIR_TEMP: rat,
            }
        ),
    )
    assert stuck.severity == "fault"
    assert stuck.metrics["integrated_economizer_basis"] == "oa_fraction+damper"
    full_oa = FreeCoolingMissed().analyze(
        "AHU",
        pd.DataFrame(
            {
                Role.OAT: oat,
                Role.COOL_VALVE: cv,
                Role.MIXED_AIR_TEMP: oat + 0.5,
                Role.RETURN_AIR_TEMP: rat,
            }
        ),
    )
    assert full_oa.severity == "ok"
    assert full_oa.metrics["integrated_economizer_basis"] == "oa_fraction"


def test_integrated_economizer_mask_contract():
    idx = _idx(days=1)
    oat = pd.Series(55.0, index=idx)
    assert integrated_economizer_mask(oat) is None
    # |OAT - RAT| < 5 °F: the temperature balance is ill-conditioned, the damper decides
    m = integrated_economizer_mask(
        oat,
        damper=pd.Series(95.0, index=idx),
        mat=pd.Series(56.0, index=idx),
        rat=pd.Series(58.0, index=idx),
    )
    assert m.all()
    m = integrated_economizer_mask(
        oat, mat=pd.Series(56.0, index=idx), rat=pd.Series(58.0, index=idx)
    )
    assert not m.any()


# ---------------------------------------------------------------- reheat_penalty vs discharge rise


def _box(idx, *, valve, dat, primary=None, flow=None):
    cols = {
        Role.HEAT_VALVE: pd.Series(valve, index=idx, dtype=float),
        Role.SUPPLY_AIR_TEMP: pd.Series(dat, index=idx, dtype=float),
        Role.OAT: pd.Series(80.0, index=idx),
    }
    if primary is not None:
        cols[Role.MIXED_AIR_TEMP] = pd.Series(primary, index=idx, dtype=float)
    if flow is not None:
        cols[Role.AIRFLOW] = pd.Series(flow, index=idx, dtype=float)
    return pd.DataFrame(cols)


def test_reheat_valve_open_with_no_rise_is_declined_and_not_costed():
    idx = _idx()
    f = ReheatPenalty().analyze("VAV", _box(idx, valve=100.0, dat=56.0, primary=55.0))
    assert f.severity == "info"
    assert f.metrics["declined"] is True
    assert f.metrics["valve_dat_consistency"] == "open_no_rise"
    assert f.metrics["valve_dat_basis"] == "entering_air"
    assert f.metrics["valve_open_median_rise_f"] == pytest.approx(1.0)
    assert f.metrics["valve_open_pct"] is None and f.metrics["reported_valve_open_pct"] > 0
    assert "declined" in f.summary and f.caveats
    cost = estimate_cost(f, EquipmentLoad(heating_capacity_kbtuh=100.0))
    assert not cost.costed


def test_reheat_valve_open_with_a_real_rise_is_counted():
    idx = _idx()
    f = ReheatPenalty().analyze("VAV", _box(idx, valve=100.0, dat=75.0, primary=55.0))
    assert f.severity == "fault"
    assert f.metrics["valve_dat_consistency"] == "consistent"
    assert "declined" not in f.metrics


def test_reheat_valve_shut_with_a_big_rise_is_caveated():
    idx = _idx()
    f = ReheatPenalty().analyze("VAV", _box(idx, valve=0.0, dat=70.0, primary=55.0))
    assert f.metrics["valve_dat_consistency"] == "closed_with_rise"
    assert f.metrics["valve_closed_big_rise_share"] == 1.0
    assert f.severity == "info"  # not a confident "ok"
    assert any("under-reports" in c for c in f.caveats)


def test_reheat_discharge_only_bases():
    idx = _idx()
    # no entering air, valve always full open: a nominal 55 °F primary air is the reference
    f = ReheatPenalty().analyze("VAV", _box(idx, valve=100.0, dat=56.0))
    assert f.metrics["valve_dat_basis"] == "nominal_primary_55f"
    assert f.metrics["declined"] is True
    assert any("lower confidence" in c for c in f.caveats)
    # open vs its own closed-valve discharge: an inverted valve (discharge colder when "open")
    valve = np.where(np.arange(len(idx)) % 48 < 24, 100.0, 0.0)
    dat = np.where(valve > 50, 55.0, 68.0)
    f = ReheatPenalty().analyze("VAV", _box(idx, valve=valve, dat=dat))
    assert f.metrics["valve_dat_basis"] == "closed_valve_discharge"
    assert f.metrics["valve_dat_consistency"] == "open_no_rise"


def test_reheat_zero_airflow_samples_are_not_judged():
    idx = _idx()
    flow = np.where(np.arange(len(idx)) % 2 == 0, 0.0, 500.0)
    dat = np.where(flow > 0, 56.0, 70.0)  # stagnant box: the sensor reads the ceiling
    f = ReheatPenalty().analyze("VAV", _box(idx, valve=0.0, dat=dat, primary=55.0, flow=flow))
    assert f.metrics["valve_dat_consistency"] == "consistent"


def test_reheat_without_discharge_air_is_not_checked():
    idx = _idx()
    frame = _box(idx, valve=100.0, dat=70.0).drop(columns=[Role.SUPPLY_AIR_TEMP])
    f = ReheatPenalty().analyze("VAV", frame)
    assert f.metrics["valve_dat_consistency"] == "not_checked"
    assert f.severity == "fault"


# ---------------------------------------------------------------- setpoint classifier


def _tr(idx, lo, hi, period_h=24, phase=0.0):
    t = np.arange(len(idx))
    return lo + (hi - lo) * 0.5 * (1 + np.sin(2 * np.pi * (t / period_h) + phase))


def test_classifier_kinds():
    idx = _idx(days=21)
    n = len(idx)
    flat = classify_setpoint_reset(
        pd.Series(1.5, index=idx), min_range=0.15, move_min=0.05, units="inWC"
    )
    assert flat.kind == "flat" and not flat.is_reset
    step = classify_setpoint_reset(
        pd.Series(np.where(np.arange(n) < n // 2, 1.2, 1.65), index=idx),
        min_range=0.15,
        move_min=0.05,
    )
    assert step.kind == "step" and step.levels[:2] == [1.2, 1.65]
    assert "one-time step" in step.label
    drv = pd.Series(_tr(idx, 0, 6), index=idx)
    reset = classify_setpoint_reset(
        pd.Series(_tr(idx, 0.6, 1.6), index=idx),
        drv,
        driver_label="requests",
        min_range=0.15,
        move_min=0.05,
    )
    assert reset.kind == "reset" and reset.is_reset and reset.driver_rho > 0.9
    unrelated = classify_setpoint_reset(
        pd.Series(_tr(idx, 0.6, 1.6), index=idx),
        pd.Series(_tr(idx, 0, 6, phase=np.pi / 2), index=idx),
        driver_label="requests",
        min_range=0.15,
        move_min=0.05,
    )
    assert unrelated.kind == "varies" and unrelated.driver_checked
    no_driver = classify_setpoint_reset(
        pd.Series(_tr(idx, 0.6, 1.6), index=idx), min_range=0.15, move_min=0.05
    )
    assert no_driver.kind == "varies" and not no_driver.driver_checked
    few = classify_setpoint_reset(
        pd.Series([1.0, 2.0], index=idx[:2]), min_range=0.1, move_min=0.05
    )
    assert few.kind == "insufficient"


def test_classifier_short_window_uses_cycles():
    idx = _idx(days=2, freq="15min")
    osc = classify_setpoint_reset(
        pd.Series(_tr(idx, 0.6, 1.6, period_h=16), index=idx), min_range=0.15, move_min=0.05
    )
    assert osc.n_days == 2 and osc.n_cycles >= 3 and osc.kind == "varies"
    ramp = classify_setpoint_reset(
        pd.Series(np.linspace(0.6, 1.6, len(idx)), index=idx), min_range=0.15, move_min=0.05
    )
    assert ramp.kind == "unclear"


# ---------------------------------------------------------------- static_pressure_reset


def test_static_one_time_step_is_not_a_reset():
    idx = _idx(days=21)
    n = len(idx)
    sp = pd.Series(np.where(np.arange(n) < n // 2, 1.2, 1.65), index=idx)
    f = StaticPressureReset().analyze("AHU", pd.DataFrame({Role.DUCT_STATIC_SP: sp}))
    assert f.severity == "warn"
    assert f.metrics["sp_behaviour"] == "step" and f.metrics["resets"] is False
    assert "not reset" in f.summary and "step" in f.summary
    assert f.metrics["sp_range_inwc"] == pytest.approx(0.45)


def test_static_reset_with_requests_is_ok_and_unrelated_is_not_confirmed():
    idx = _idx(days=21)
    sp = pd.Series(_tr(idx, 0.6, 1.6), index=idx)
    req = pd.Series(_tr(idx, 0, 6), index=idx)
    ok = StaticPressureReset().analyze(
        "AHU", pd.DataFrame({Role.DUCT_STATIC_SP: sp, Role.STATIC_PRESSURE_REQUESTS: req})
    )
    assert ok.severity == "ok" and ok.metrics["sp_behaviour"] == "reset"
    assert ok.metrics["sp_driver"] == "static-pressure requests"
    off = pd.Series(_tr(idx, 0, 6, phase=np.pi / 2), index=idx)
    nc = StaticPressureReset().analyze(
        "AHU", pd.DataFrame({Role.DUCT_STATIC_SP: sp, Role.STATIC_PRESSURE_REQUESTS: off})
    )
    assert nc.severity == "info" and nc.metrics["resets"] is False
    assert "not confirmed" in nc.summary
    nd = StaticPressureReset().analyze("AHU", pd.DataFrame({Role.DUCT_STATIC_SP: sp}))
    assert nd.severity == "ok" and nd.caveats  # repeated movement, driver not trended


def test_static_judged_on_fan_on_samples():
    idx = _idx(days=21)
    on = (idx.hour >= 6) & (idx.hour < 18)
    # occupied/unoccupied swap while the fan is off: flat while it runs
    sp = pd.Series(np.where(on, 1.5, 0.5), index=idx)
    fan = pd.Series(on.astype(float), index=idx)
    f = StaticPressureReset().analyze(
        "AHU", pd.DataFrame({Role.DUCT_STATIC_SP: sp, Role.SUPPLY_FAN_STATUS: fan})
    )
    assert f.metrics["sp_behaviour"] == "flat" and f.severity == "warn"
    assert f.metrics["fan_gate"] == "fan status"


def test_static_few_samples_fall_back_to_range():
    idx = _idx(days=1)[:5]
    f = StaticPressureReset().analyze(
        "AHU", pd.DataFrame({Role.DUCT_STATIC_SP: pd.Series([1.0, 1.2, 1.4, 1.2, 1.0], index=idx)})
    )
    assert f.severity == "ok" and f.metrics["sp_behaviour"] == "insufficient" and f.caveats


# ---------------------------------------------------------------- supply_air_reset


def _ahu(idx, *, sat, sp=None, oat=None):
    oat = pd.Series(
        oat if oat is not None else 60 + 15 * np.sin(np.arange(len(idx)) / 24 * 2 * np.pi),
        index=idx,
    )
    cols = {
        Role.SUPPLY_AIR_TEMP: pd.Series(sat, index=idx, dtype=float),
        Role.COOL_VALVE: pd.Series(60.0, index=idx),
        Role.OAT: oat,
    }
    if sp is not None:
        cols[Role.SUPPLY_AIR_TEMP_SP] = pd.Series(sp, index=idx, dtype=float)
    return pd.DataFrame(cols), oat


def test_capacity_shortfall_with_flat_setpoint_is_not_a_reset():
    idx = _idx(days=21)
    base, oat = _ahu(idx, sat=0.0)
    sat = 52.0 + np.clip(oat - 62.0, 0, None) * 0.5  # coil can't hold 52 °F on hot hours
    frame, _ = _ahu(idx, sat=sat, sp=52.0, oat=oat)
    no_sp = SupplyAirReset().analyze("AHU", frame.drop(columns=[Role.SUPPLY_AIR_TEMP_SP]))
    # #65: without a setpoint, SAT rising with OAT is no longer read as a reset
    assert no_sp.severity == "warn" and "RESET PRESENT" not in no_sp.summary
    assert "possible capacity shortfall" in no_sp.summary
    assert no_sp.metrics["reset_direction"] == "rising_with_load"
    assert any("capacity" in c for c in no_sp.caveats)
    f = SupplyAirReset().analyze("AHU", frame)
    assert f.severity == "warn"
    assert "NOT RESET (setpoint flat" in f.summary and "SAT deviates" in f.summary
    assert "RESET PRESENT" not in f.summary
    assert f.metrics["sp_behaviour"] == "flat"
    assert f.metrics["sat_minus_sp_mean_f"] > 0


def test_sat_setpoint_step_is_reported_as_step():
    idx = _idx(days=21)
    n = len(idx)
    base, oat = _ahu(idx, sat=0.0)
    sp = np.where(np.arange(n) < n // 2, 55.0, 50.0)
    frame, _ = _ahu(idx, sat=sp + 0.2, sp=sp, oat=oat)
    f = SupplyAirReset().analyze("AHU", frame)
    assert f.metrics["sp_behaviour"] == "step"
    assert "one-time step or manual change" in f.summary
    assert f.severity in ("warn", "info")


def test_sat_setpoint_reset_with_oat_stands():
    idx = _idx(days=21)
    base, oat = _ahu(idx, sat=0.0)
    # #65: falls as OAT rises -- the G36 OAT-reset direction the rule rewards
    sp = np.clip(65 - 0.4 * (oat - 60), 53, 65)
    frame, _ = _ahu(idx, sat=sp + 0.1, sp=sp, oat=oat)
    f = SupplyAirReset().analyze("AHU", frame)
    assert f.metrics["sp_behaviour"] == "reset" and f.metrics["sp_driver"] == "OAT"
    assert f.metrics["sp_driver_rho"] < 0 and f.metrics["reset_direction"] == "reset"
    assert f.severity == "ok" and "setpoint resets with OAT" in f.summary
    assert not f.caveats


def test_sat_setpoint_rising_with_oat_is_not_confirmed_as_a_reset():
    # #65: a setpoint that rises with OAT moves with its driver, but in the wrong direction
    idx = _idx(days=21)
    base, oat = _ahu(idx, sat=0.0)
    sp = np.clip(55 + 0.4 * (oat - 60), 53, 65)
    frame, _ = _ahu(idx, sat=sp + 0.1, sp=sp, oat=oat)
    f = SupplyAirReset().analyze("AHU", frame)
    assert f.metrics["sp_behaviour"] == "reset" and f.metrics["sp_driver_rho"] > 0
    assert f.metrics.get("sp_wrong_direction") is True
    assert f.severity != "ok" and "wrong direction" in f.summary
    assert "RESET PRESENT" not in f.summary


def test_sat_setpoint_reset_with_requests_needs_the_negative_sign():
    # #65: trim-and-respond lowers the SAT setpoint for each request beyond the ignored ones
    from camber.g36_reset import SAT_TR, tr_simulate

    idx = _idx(days=21)
    base, oat = _ahu(idx, sat=0.0)
    req = np.where(((np.arange(len(idx)) // 24) // 2) % 2 == 0, 6.0, 0.0)
    sp = tr_simulate(req, SAT_TR)
    sat = np.clip(65 - 0.4 * (oat - 60), 53, 65)  # the SAT shape of a reset
    frame, _ = _ahu(idx, sat=sat, sp=sp, oat=oat)
    frame[Role.SAT_RESET_REQUESTS] = pd.Series(req, index=idx)
    f = SupplyAirReset().analyze("AHU", frame)
    assert f.metrics["sp_driver"] == "SAT reset requests"
    assert f.metrics["sp_driver_rho"] < 0
    assert f.metrics["sp_behaviour"] == "reset" and not f.metrics.get("sp_wrong_direction")
    assert f.severity == "ok"
    inverted = frame.copy()
    inverted[Role.SUPPLY_AIR_TEMP_SP] = 120.0 - pd.Series(sp, index=idx)  # rises with requests
    g = SupplyAirReset().analyze("AHU", inverted)
    assert g.metrics.get("sp_wrong_direction") is True and g.severity == "info"


def test_sat_setpoint_unrelated_to_driver_downgrades_ok_to_info():
    idx = _idx(days=21)
    base, oat = _ahu(idx, sat=0.0)
    sat = np.clip(65 - 0.4 * (oat - 60), 53, 65)  # #65: the reset direction
    req = pd.Series(_tr(idx, 0, 6, phase=np.pi / 2), index=idx)
    sp = 58 + 3 * np.sin(np.arange(len(idx)) / 24 * 2 * np.pi)  # moves daily, not with requests
    frame, _ = _ahu(idx, sat=sat, sp=sp, oat=oat)
    frame[Role.SAT_RESET_REQUESTS] = req
    f = SupplyAirReset().analyze("AHU", frame)
    assert f.metrics["sp_driver"] == "SAT reset requests"
    assert f.metrics["sp_behaviour"] == "varies"
    assert f.severity == "info" and any("not confirmed" in c for c in f.caveats)
