"""Tests for VAV reheat-coil drift (camber.rules.vav_reheat_valve_rule).

Synthetic data: an exogenous reheat air-ΔT (heating demand) and a near-min-flow airflow, a reheat
valve linear in the reheat *duty* (airflow × ΔT) plus Gaussian noise, with capacity loss injected as
a valve %-offset at matched duty. Nothing is from a measured dataset; every draw is seeded.
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.rules.base import PeriodRule  # noqa: E402
from camber.rules.builtin import builtin_registry, rule_names  # noqa: E402
from camber.rules.vav_reheat_valve_rule import VavReheatValveDrift  # noqa: E402
from camber.store.modelstore import _MODEL_TYPES, BaselineStore, LoadBaseline  # noqa: E402

_ENTER = 55.0  # cold primary (AHU supply) air feeding the box, degF
_V0 = 10.0  # healthy reheat-valve intercept, %
_V_PER_DUTY = 0.0035  # valve % per cfm·°F of reheat duty (kept below saturation)
_SIGMA = 3.0


def _reheat_frame(
    n=24 * 30,
    *,
    start="2025-05-01",
    seed=0,
    creep_pct=0.0,
    hw_off=0.0,
    airflow_swing=120.0,
    airflow_base=350.0,
    no_reheat_block=0,
    inputs=True,
    drop_enter=False,
    load_basis="duty",
    noiseless=False,
):
    rng = np.random.default_rng(seed + 100)
    idx = pd.date_range(start, periods=n, freq="1h")
    h = np.arange(n)
    dt = 20 + 10 * np.sin((h % 24 - 8) / 24 * 2 * np.pi) + rng.normal(0, 1.5, n)
    dt = np.clip(dt, 5.0, 40.0)
    flow = np.clip(
        airflow_base + airflow_swing * np.sin((h % 24 - 6) / 24 * 2 * np.pi), 250.0, 1500.0
    )
    duty = flow * dt
    noise = 0.0 if noiseless else rng.normal(0, _SIGMA, n)
    valve = _V0 + _V_PER_DUTY * (duty if load_basis == "duty" else dt * 350.0) + creep_pct + noise
    if no_reheat_block:  # a block with the valve near-closed (not reheating -> gated out)
        valve[:no_reheat_block] = 1.0
    cols = {
        Role.SUPPLY_FAN_STATUS: np.ones(n),
        Role.AIRFLOW: flow,
        Role.HW_SUPPLY_TEMP: np.full(n, 180.0 + hw_off),
    }
    if inputs:
        cols[Role.HEAT_VALVE] = np.clip(valve, 0.0, 100.0)
        cols[Role.SUPPLY_AIR_TEMP] = _ENTER + dt  # box discharge (warm)
        if not drop_enter:
            cols[Role.MIXED_AIR_TEMP] = np.full(n, _ENTER)  # entering primary (cool)
    return pd.DataFrame(cols, index=idx)


def _rule(store, **kw):
    return VavReheatValveDrift(store, site="SITE", run_id="2025-07-01T00:00", **kw)


def _base_and(current_kw):
    return _reheat_frame(start="2025-05-01", seed=1), _reheat_frame(
        start="2025-06-01", seed=2, **current_kw
    )


# --------------------------------------------------------------------------- interface


def test_it_is_a_period_rule_and_is_not_auto_registered():
    rule = _rule(BaselineStore())
    assert isinstance(rule, PeriodRule)
    assert "vav_reheat_valve_drift" not in rule_names()
    assert "vav_reheat_valve_drift" not in builtin_registry().names()
    assert rule.roles_required == (
        Role.HEAT_VALVE,
        Role.SUPPLY_AIR_TEMP,
        Role.MIXED_AIR_TEMP,
        Role.AIRFLOW,
    )


def test_bad_load_basis_raises():
    try:
        VavReheatValveDrift(BaselineStore(), load_basis="power")
    except ValueError as exc:
        assert "duty" in str(exc) and "deltat" in str(exc)
    else:
        raise AssertionError("expected ValueError on a bad load_basis")


def test_modelstore_registers_the_kind():
    assert _MODEL_TYPES["vav_reheat_valve"] is LoadBaseline


# --------------------------------------------------------------------------- the detector


def test_reheat_valve_creep_flags_capacity_loss():
    f = _rule(BaselineStore()).analyze_periods("VAV_1", *_base_and({"creep_pct": 16.0}))
    assert f.rule == "vav_reheat_valve_drift" and f.severity == "fault"
    assert f.metrics["vav_reheat_valve_drift_pct"] > 15.0
    assert f.metrics["vav_reheat_valve_drift_direction"] == "up"
    assert f.metrics["vav_reheat_valve_sustained_alarm"] is True
    assert f.metrics["vav_reheat_valve_alarm_direction"] == "up"
    assert f.metrics["vav_reheat_which"] == "reheat"
    assert "reheat-valve creep" in f.summary and "reheat coil fouling" in f.summary


def test_a_healthy_box_does_not_flag():
    f = _rule(BaselineStore()).analyze_periods("VAV_1", *_base_and({}))
    assert f.severity == "ok" and abs(f.metrics["vav_reheat_valve_drift_pct"]) < 8.0


def test_it_is_one_sided_a_valve_drop_is_not_a_fault():
    f = _rule(BaselineStore()).analyze_periods("VAV_1", *_base_and({"creep_pct": -16.0}))
    assert f.severity == "ok" and f.metrics["vav_reheat_valve_drift_direction"] == "down"
    assert "less valve is not a fault" in f.summary


def test_healthy_box_with_big_airflow_swing_stays_ok():
    """The duty-basis proof: airflow variation is normalized, so a healthy box stays ok."""
    base, cur = (
        _reheat_frame(start="2025-05-01", seed=1, airflow_swing=100.0),
        _reheat_frame(start="2025-06-01", seed=2, airflow_swing=500.0),  # much larger flow swing
    )
    f = _rule(BaselineStore()).analyze_periods("VAV_1", base, cur)
    assert f.severity == "ok" and abs(f.metrics["vav_reheat_valve_drift_pct"]) < 8.0


def test_heating_mode_and_cooling_samples_are_gated_out():
    base, cur = (
        _reheat_frame(start="2025-05-01", seed=1),
        _reheat_frame(start="2025-06-01", seed=2, no_reheat_block=24 * 8),  # 8 days not reheating
    )
    f = _rule(BaselineStore()).analyze_periods("VAV_1", base, cur)
    assert f.metrics["vav_reheat_valve_gated_excluded_pct"] > 0.0
    assert f.severity == "ok"


def test_hw_reset_creep_is_caveated():
    base, cur = (
        _reheat_frame(start="2025-05-01", seed=1),
        _reheat_frame(start="2025-06-01", seed=2, creep_pct=16.0, hw_off=-8.0),
    )
    f = _rule(BaselineStore()).analyze_periods("VAV_1", base, cur)
    assert f.severity == "fault"
    assert f.metrics["water_supply_shift_f"] < 0
    assert any("waterside-reset effect" in c for c in f.caveats)


# ----------------------------------------------------------------------- declines / basis / freeze


def test_it_declines_when_inputs_are_not_mapped():
    base = _reheat_frame(start="2025-05-01", seed=1, inputs=False)
    cur = _reheat_frame(start="2025-06-01", seed=2, inputs=False)
    f = _rule(BaselineStore()).analyze_periods("VAV_1", base, cur)
    assert f.severity == "info" and f.metrics["reason"] == "vav_reheat_valve_inputs_not_mapped"


def test_it_declines_when_the_entering_air_is_unmapped():
    base = _reheat_frame(start="2025-05-01", seed=1, drop_enter=True)
    cur = _reheat_frame(start="2025-06-01", seed=2, drop_enter=True)
    f = _rule(BaselineStore()).analyze_periods("VAV_1", base, cur)
    assert f.severity == "info" and f.metrics["reason"] == "vav_reheat_valve_inputs_not_mapped"
    assert any("mixed-air" in c for c in f.caveats)


def test_thresholds_are_screening_grade():
    f = _rule(BaselineStore()).analyze_periods("VAV_1", *_base_and({"creep_pct": 16.0}))
    assert f.metrics["magnitude_threshold_confidence"] == "screening-grade"


def test_the_deltat_basis_is_a_constructor_option():
    store = BaselineStore()
    rule = _rule(store, load_basis="deltat")
    assert rule.roles_required == (Role.HEAT_VALVE, Role.SUPPLY_AIR_TEMP, Role.MIXED_AIR_TEMP)
    base = _reheat_frame(start="2025-05-01", seed=1, load_basis="deltat")
    cur = _reheat_frame(start="2025-06-01", seed=2, creep_pct=16.0, load_basis="deltat")
    f = rule.analyze_periods("VAV_1", base, cur)
    assert f.severity == "fault" and f.metrics["vav_reheat_load_basis"] == "deltat"
    assert any("airflow variation is unmodeled" in c for c in f.caveats)


def test_the_baseline_is_frozen_and_not_refit():
    store = BaselineStore()
    rule = _rule(store)
    rule.analyze_periods("VAV_1", *_base_and({"creep_pct": 16.0}))
    coeffs = dict(store.get("SITE", "VAV_1", "vav_reheat_valve").coefficients)
    worse = _reheat_frame(start="2025-07-01", seed=5, creep_pct=28.0)
    f = rule.analyze_periods("VAV_1", worse, worse)
    assert store.get("SITE", "VAV_1", "vav_reheat_valve").coefficients == coeffs
    assert f.severity == "fault"


def test_no_baseline_scatter_is_judged_on_percent_alone():
    f = _rule(BaselineStore()).analyze_periods(
        "VAV_1",
        _reheat_frame(start="2025-05-01", seed=1, noiseless=True),
        _reheat_frame(start="2025-06-01", seed=2, creep_pct=16.0, noiseless=True),
    )
    assert f.severity == "fault"
    assert any("no residual scatter" in c for c in f.caveats)


def test_it_declines_when_freezing_is_disabled_and_no_baseline():
    rule = _rule(BaselineStore(), freeze_if_missing=False)
    f = rule.analyze_periods("VAV_1", *_base_and({}))
    assert f.severity == "info" and f.metrics.get("declined") is True
    assert any("freezing is disabled" in c for c in f.caveats)


def test_it_declines_when_the_current_period_is_all_no_reheat():
    store = BaselineStore()
    rule = _rule(store)
    rule.analyze_periods("VAV_1", *_base_and({}))  # freeze a baseline
    dead = _reheat_frame(start="2025-07-01", seed=5, no_reheat_block=24 * 30)  # valve all closed
    f = rule.analyze_periods("VAV_1", dead, dead)
    assert f.severity == "info" and f.metrics.get("declined") is True
    assert "nothing scoreable" in f.summary
