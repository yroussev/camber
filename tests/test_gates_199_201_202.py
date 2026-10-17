"""0.107 regressions: missing valve samples (#199), DX rules with no running gate (#201) and the
tower approach with no operating signal (#202). Each test is its issue's repro."""

import numpy as np
import pandas as pd
import pytest

from camber.model.roles import Role as R
from camber.rules.coolingtower_rule import CoolingTowerApproach
from camber.rules.dx_charge_rule import DXRefrigerantCharge
from camber.rules.dx_discharge_superheat_rule import DischargeSuperheatDrift
from camber.rules.simul_hc import SimultaneousHeatCool

IX = pd.date_range("2024-07-01", periods=14 * 24, freq="1h")
OCC = pd.Series(((IX.hour >= 7) & (IX.hour < 18) & (IX.dayofweek < 5)).astype(float), index=IX)


# ------------------------------------------------------------------ #199
def test_simul_hc_declines_on_an_all_missing_valve():
    f = SimultaneousHeatCool().analyze(
        "AHU-1", pd.DataFrame({R.HEAT_VALVE: np.nan, R.COOL_VALVE: 40.0}, index=IX)
    )
    assert f.severity == "info"
    assert f.metrics["declined"] is True and f.metrics["reason"] == "no_valve_data"


def test_simul_hc_judges_only_samples_with_both_valves():
    hv = pd.Series(30.0, index=IX)
    hv.iloc[::2] = np.nan
    f = SimultaneousHeatCool().analyze(
        "AHU-1", pd.DataFrame({R.HEAT_VALVE: hv, R.COOL_VALVE: 40.0}, index=IX)
    )
    assert f.metrics["simultaneous_hc_pct"] == 100.0
    assert f.severity == "fault"
    assert f.metrics["valve_coverage_pct"] < 60.0
    assert any("both valves were trended in only" in c for c in f.caveats)


def test_simul_hc_full_coverage_has_no_coverage_caveat():
    f = SimultaneousHeatCool().analyze(
        "AHU-1", pd.DataFrame({R.HEAT_VALVE: 30.0, R.COOL_VALVE: 40.0}, index=IX)
    )
    assert f.metrics["valve_coverage_pct"] == 100.0
    assert not any("both valves were trended" in c for c in f.caveats)


# ------------------------------------------------------------------ #201
def _subcooling():
    return pd.Series(10.0, index=IX).where(OCC > 0, 1.0)


def test_dx_charge_declines_without_a_running_gate():
    f = DXRefrigerantCharge(targets={"subcooling_f": 10}).analyze(
        "RTU-1", pd.DataFrame({R.SUBCOOLING_TEMP: _subcooling()})
    )
    assert f.severity == "info" and f.metrics["reason"] == "no_run_gate"


def test_dx_charge_reversing_valve_alone_is_not_a_running_gate():
    f = DXRefrigerantCharge(targets={"subcooling_f": 10}).analyze(
        "RTU-1",
        pd.DataFrame({R.SUBCOOLING_TEMP: _subcooling(), R.REVERSING_VALVE_CMD: 0.0}),
    )
    assert f.metrics["reason"] == "no_run_gate"


@pytest.mark.parametrize("gate", ["status", "split"])
def test_dx_charge_with_a_running_gate_is_judged(gate):
    df = pd.DataFrame({R.SUBCOOLING_TEMP: _subcooling()})
    if gate == "status":
        df[R.COMPRESSOR_STATUS] = OCC
    else:
        df[R.RETURN_AIR_TEMP] = 75.0
        df[R.SUPPLY_AIR_TEMP] = pd.Series(55.0, index=IX).where(OCC > 0, 75.0)
    f = DXRefrigerantCharge(targets={"subcooling_f": 10}).analyze("RTU-1", df)
    assert f.severity == "ok" and f.metrics["subcooling_median_f"] == 10.0


def _periods(role):
    oat = pd.Series(80 + 10 * np.sin(np.arange(len(IX)) / 24 * 2 * np.pi), index=IX)
    df = pd.DataFrame({role: _subcooling(), R.OAT: oat})
    return df, df.copy()


@pytest.mark.parametrize(
    "rule, role",
    [
        (DXRefrigerantCharge, R.SUBCOOLING_TEMP),
        (DischargeSuperheatDrift, R.DISCHARGE_SUPERHEAT_TEMP),
    ],
)
def test_dx_drift_declines_without_a_running_gate(rule, role):
    base, cur = _periods(role)
    f = rule().analyze_periods("RTU-1", base, cur)
    assert f.severity == "info" and f.metrics["reason"] == "no_run_gate"


# ------------------------------------------------------------------ #202
def _tower():
    wb = pd.Series(65 + 5 * np.sin((IX.hour - 9) / 24 * 2 * np.pi), index=IX)
    return pd.DataFrame({R.CW_SUPPLY_TEMP: (wb + 6).where(OCC > 0, 78.0), R.WETBULB_TEMP: wb})


def test_tower_approach_declines_with_no_operating_signal():
    f = CoolingTowerApproach().analyze("CT-1", _tower())
    assert f.severity == "info" and f.metrics["reason"] == "no_operating_signal"
    assert "operating hours" not in f.summary


@pytest.mark.parametrize(
    "role, value, source",
    [
        (R.PUMP_STATUS, 1.0, "pump_status"),
        (R.COMPRESSOR_STATUS, 1.0, "chiller_status"),
        (R.CW_FLOW, 1200.0, "cw_flow"),
    ],
)
def test_tower_approach_gates_on_a_run_signal(role, value, source):
    df = _tower()
    df[role] = OCC * value
    f = CoolingTowerApproach().analyze("CT-1", df)
    assert f.severity == "ok"
    assert f.metrics["approach_median_f"] == 6.0
    assert f.metrics["n_operating"] == int(OCC.sum())
    assert f.metrics["run_gate"] == source


def test_tower_approach_declines_when_the_plant_never_runs():
    df = _tower()
    df[R.PUMP_STATUS] = 0.0
    f = CoolingTowerApproach().analyze("CT-1", df)
    assert f.severity == "info" and f.metrics["reason"] == "never_running"
