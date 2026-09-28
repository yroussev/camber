"""0.92 (#13): boiler combustion-efficiency drift (gas in per unit heat out), synthetic plants."""

import numpy as np
import pandas as pd

from camber.driftrun import build_drift_suite
from camber.interop.brick import roles_from_triples
from camber.model.roles import Role
from camber.plantdrift import diagnose_boiler_drift
from camber.rules.boiler_efficiency_rule import BoilerEfficiencyDrift
from camber.store.modelstore import BaselineStore


def _boiler(*, eff=0.80, return_bias=0.0, days=60, start="2026-01-01", seed=0, flow=True):
    """Hourly heating-season boiler: heat ~ (60 - OAT), gas = heat / efficiency."""
    idx = pd.date_range(start, periods=days * 24, freq="1h")
    rng = np.random.default_rng(seed)
    oat = 35 + 15 * np.sin(np.arange(len(idx)) / 24 * 2 * np.pi) + rng.normal(0, 3, len(idx))
    heat_kw = np.clip(12.0 * (60 - oat), 0, None) * (1 + rng.normal(0, 0.02, len(idx)))
    gpm = 150.0 + rng.normal(0, 2, len(idx))
    dt = heat_kw * 3412.14 / (500 * gpm)
    sup = 170.0 + rng.normal(0, 0.3, len(idx))
    ret = sup - dt
    gas = np.where(heat_kw > 0, heat_kw / eff, 0.0)
    f = pd.DataFrame(
        {
            Role.GAS_INPUT_RATE: gas,
            Role.HW_SUPPLY_TEMP: sup,
            Role.HW_RETURN_TEMP: ret + return_bias,
            Role.OAT: oat,
        },
        index=idx,
    )
    if flow:
        f[Role.HW_FLOW] = gpm
    else:
        f[Role.HW_PUMP_SPEED] = gpm / 200.0  # a 0-1 speed, flow ~ speed
    return f


def _run(base, cur):
    return BoilerEfficiencyDrift(BaselineStore(), site="S", run_id="R").analyze_periods(
        "B-1", base, cur
    )


def test_fouled_boiler_is_a_fault_and_healthy_is_ok():
    base = _boiler()
    assert _run(base, _boiler(seed=1)).severity == "ok"
    f = _run(base, _boiler(eff=0.68, seed=1))  # +17.6 % gas per unit heat
    assert f.severity == "fault" and f.metrics["attribution"] == "boiler"
    assert 0.15 < f.metrics["ratio_drift_rel"] < 0.20
    assert f.metrics["gas_rise_at_matched_oat"] > 0.1
    mild = _run(base, _boiler(eff=0.755, seed=1))  # +6 %
    assert mild.severity == "warn"


def test_a_return_sensor_bias_is_heat_metering_not_fouling():
    base = _boiler()
    f = _run(base, _boiler(return_bias=4.0, seed=1))  # reads warm: delta-T and heat read low
    assert f.metrics["ratio_drift_rel"] > 0.05  # the ratio rose...
    assert f.severity == "info" and f.metrics["attribution"] == "heat_metering"
    assert "heat-metering" in f.summary
    # a negative bias lowers the ratio: one-sided, ok
    assert _run(base, _boiler(return_bias=-4.0, seed=1)).severity == "ok"


def test_without_oat_a_rise_is_capped_at_warn():
    base, cur = _boiler(), _boiler(eff=0.6, seed=1)
    f = _run(base.drop(columns=[Role.OAT]), cur.drop(columns=[Role.OAT]))
    assert f.severity == "warn" and f.metrics["attribution"] == "uncorroborated"
    assert any("capped at warn" in c for c in f.caveats)


def test_pump_speed_stands_in_for_a_flow_meter():
    base = _boiler(flow=False)
    f = _run(base, _boiler(eff=0.68, seed=1, flow=False))
    assert f.severity == "fault" and f.metrics["heat_source"] == "pump_speed"
    assert any("no hot-water flow meter" in c for c in f.caveats)


def test_declines_without_a_heat_measure_or_a_fittable_baseline():
    base = _boiler().drop(columns=[Role.HW_FLOW])
    f = _run(base, base)
    assert f.metrics["declined"] and f.metrics["reason"] == "no_heat_measure"
    summer = _boiler()
    summer[Role.GAS_INPUT_RATE] = 0.0  # never fired
    g = _run(summer, _boiler())
    assert g.metrics["declined"] and g.metrics["reason"] == "no_baseline"


def test_frozen_baseline_is_reused_and_not_refit():
    store = BaselineStore()
    rule = BoilerEfficiencyDrift(store, site="S", run_id="R")
    rule.analyze_periods("B-1", _boiler(), _boiler(seed=1))
    frozen = store.model_for("S", "B-1", "boiler_efficiency")
    assert frozen is not None and store.model_for("S", "B-1", "boiler_gas_signature") is not None
    # a later run whose "baseline" is already fouled still scores against the frozen reference
    f = rule.analyze_periods("B-1", _boiler(eff=0.6), _boiler(eff=0.6, seed=2))
    assert f.severity == "fault"
    off = BoilerEfficiencyDrift(BaselineStore(), freeze_if_missing=False)
    assert off.analyze_periods("B-1", _boiler(), _boiler()).metrics["declined"]


def test_boiler_family_and_roll_up():
    suite = build_drift_suite("boiler", BaselineStore())
    assert [r.name for r in suite] == ["boiler_efficiency_drift"]
    base = _boiler()
    fault = _run(base, _boiler(eff=0.6, seed=1))
    d = diagnose_boiler_drift([fault], equip="B-1")
    assert d.severity == "fault" and d.locus == "boiler" and d.family == "boiler"
    meter = _run(base, _boiler(return_bias=4.0, seed=1))
    d2 = diagnose_boiler_drift([meter], equip="B-1")
    assert d2.locus == "sensor" and d2.severity == "info"
    ok = diagnose_boiler_drift([_run(base, _boiler(seed=1))], equip="B-1")
    assert ok.locus == "steady"
    assert diagnose_boiler_drift([], equip="B-1").locus == "unknown"
    assert d.as_dict()["signals"]["boiler_efficiency_drift"]["attribution"] == "boiler"


def test_brick_gas_classes():
    types = {"G1": "Gas_Meter", "G2": "Natural_Gas_Flow_Sensor", "G3": "Natural_Gas_Usage_Sensor"}
    roles = roles_from_triples(types, {"Boiler_1": ["G1", "G2", "G3"]})
    assert roles == {"G1": Role.GAS_INPUT_RATE, "G2": Role.GAS_INPUT_RATE}
