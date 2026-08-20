"""Tests for coil heat-transfer drift (camber.rules.coil_valve_rule).

Synthetic data: an exogenous weather-driven demanded air-ΔT, a valve linear in ΔT plus Gaussian
noise, with fouling injected as a valve %-offset at matched ΔT. Nothing is from a measured dataset.
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.rules.base import PeriodRule  # noqa: E402
from camber.rules.builtin import builtin_registry, rule_names  # noqa: E402
from camber.rules.coil_valve_rule import CoilValveDrift  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402

_SAT = 55.0  # cooling supply-air setpoint
_VALVE_INTERCEPT = 10.0
_VALVE_PER_F = 3.5
_SIGMA = 3.0


def _deltat(n, seed=0):
    rng = np.random.default_rng(seed)
    h = np.arange(n)
    dt = 12 + 8 * np.sin((h % 24 - 8) / 24 * 2 * np.pi) + rng.normal(0, 1.5, n)
    return np.clip(dt, 4.0, 22.0)


def _cool_frame(
    n=24 * 30, *, start="2025-05-01", seed=0, foul_pct=0.0, water_off=0.0, inputs=True, econ_block=0
):
    rng = np.random.default_rng(seed + 100)
    idx = pd.date_range(start, periods=n, freq="1h")
    dt = _deltat(n, seed=seed)
    valve = _VALVE_INTERCEPT + _VALVE_PER_F * dt + foul_pct + rng.normal(0, _SIGMA, n)
    econ = np.zeros(n)
    if econ_block:
        # a block of economizing samples: cool valve near-closed while ΔT is (freely) high
        econ[:econ_block] = 1.0
        valve[:econ_block] = 8.0 + rng.normal(0, 1.0, econ_block)
    cols = {
        Role.ECON_CMD: econ,
        Role.OA_DAMPER: np.where(econ > 0.5, 80.0, 15.0),
        Role.CHW_SUPPLY_TEMP: np.full(n, 44.0 + water_off),
    }
    if inputs:
        cols[Role.COOL_VALVE] = np.clip(valve, 0.0, 100.0)
        cols[Role.SUPPLY_AIR_TEMP] = np.full(n, _SAT)
        cols[Role.MIXED_AIR_TEMP] = _SAT + dt
    return pd.DataFrame(cols, index=idx)


def _rule(store, **kw):
    return CoilValveDrift(store, site="SITE", run_id="2025-07-01T00:00", **kw)


def _base_and(current_kw):
    return _cool_frame(start="2025-05-01", seed=1), _cool_frame(
        start="2025-06-01", seed=2, **current_kw
    )


# --------------------------------------------------------------------------- interface


def test_it_is_a_period_rule_and_is_not_auto_registered():
    rule = _rule(BaselineStore())
    assert isinstance(rule, PeriodRule)
    assert "coil_valve_drift" not in rule_names()
    assert "coil_valve_drift" not in builtin_registry().names()
    assert rule.roles_required == (Role.COOL_VALVE, Role.MIXED_AIR_TEMP, Role.SUPPLY_AIR_TEMP)


def test_bad_coil_raises():
    try:
        CoilValveDrift(BaselineStore(), coil="reheat")
    except ValueError as exc:
        assert "cooling" in str(exc) and "heating" in str(exc)
    else:
        raise AssertionError("expected ValueError on a bad coil")


# --------------------------------------------------------------------------- the detector


def test_valve_creep_flags_fouling():
    f = _rule(BaselineStore()).analyze_periods("AHU_1", *_base_and({"foul_pct": 16.0}))
    assert f.rule == "coil_valve_drift" and f.severity == "fault"
    assert f.metrics["coil_valve_drift_pct"] > 15.0
    assert f.metrics["coil_valve_drift_direction"] == "up"
    assert f.metrics["coil_valve_sustained_alarm"] is True
    assert f.metrics["coil_valve_alarm_direction"] == "up"
    assert "fouling" in f.summary and "cooling-coil" in f.summary


def test_a_healthy_coil_does_not_flag():
    f = _rule(BaselineStore()).analyze_periods("AHU_1", *_base_and({}))
    assert f.severity == "ok" and abs(f.metrics["coil_valve_drift_pct"]) < 8.0


def test_it_is_one_sided_a_valve_drop_is_not_a_fault():
    """A cleaned coil (less valve for the same ΔT) is a gain, not a fault."""
    f = _rule(BaselineStore()).analyze_periods("AHU_1", *_base_and({"foul_pct": -16.0}))
    assert f.severity == "ok" and f.metrics["coil_valve_drift_direction"] == "down"
    assert "less valve is not a fault" in f.summary


# --------------------------------------------------------------------------- heating coil


def test_it_reparameterizes_to_a_heating_coil_under_a_distinct_kind():
    n = 24 * 30
    rng = np.random.default_rng(7)

    def heat(start, seed, foul=0.0):
        idx = pd.date_range(start, periods=n, freq="1h")
        dt = (
            25
            + 8 * np.sin((np.arange(n) % 24 - 8) / 24 * 2 * np.pi)
            + np.random.default_rng(seed).normal(0, 1.5, n)
        )
        dt = np.clip(dt, 8.0, 40.0)
        mat = np.full(n, 60.0)
        valve = 10 + 2.0 * dt + foul + rng.normal(0, _SIGMA, n)
        return pd.DataFrame(
            {
                Role.HEAT_VALVE: np.clip(valve, 0.0, 100.0),
                Role.SUPPLY_AIR_TEMP: mat + dt,  # warm = supply for heating
                Role.MIXED_AIR_TEMP: mat,  # cool = mixed
                Role.HW_SUPPLY_TEMP: np.full(n, 180.0),
            },
            index=idx,
        )

    store = BaselineStore()
    rule = _rule(store, coil="heating")
    assert rule.roles_required == (Role.HEAT_VALVE, Role.SUPPLY_AIR_TEMP, Role.MIXED_AIR_TEMP)
    f = rule.analyze_periods("AHU_1", heat("2025-05-01", 1), heat("2025-06-01", 2, foul=16.0))
    assert f.severity == "fault" and f.metrics["coil_valve_drift_direction"] == "up"
    assert "heating-coil" in f.summary
    # heating freezes under its own kind, never colliding with a cooling coil on the same equip
    assert store.get("SITE", "AHU_1", "coil_valve_heat") is not None
    assert store.get("SITE", "AHU_1", "coil_valve_cool") is None


# --------------------------------------------------------------------------- the economizer gate


def test_economizer_samples_are_gated_out():
    """Free-cooling samples (valve driven by mixed-air control) must not corrupt the fit."""
    base, cur = (
        _cool_frame(start="2025-05-01", seed=1),
        _cool_frame(start="2025-06-01", seed=2, econ_block=24 * 10),  # 10 days economizing, healthy
    )
    f = _rule(BaselineStore()).analyze_periods("AHU_1", base, cur)
    assert f.metrics["coil_valve_econ_excluded_pct"] > 0.0
    assert f.severity == "ok"  # the econ samples were excluded, so a healthy coil stays steady


def test_no_econ_point_mapped_adds_a_caveat():
    def bare(start, seed):
        n = 24 * 30
        idx = pd.date_range(start, periods=n, freq="1h")
        dt = _deltat(n, seed=seed)
        return pd.DataFrame(
            {
                Role.COOL_VALVE: np.clip(
                    _VALVE_INTERCEPT
                    + _VALVE_PER_F * dt
                    + np.random.default_rng(seed + 5).normal(0, _SIGMA, n),
                    0,
                    100,
                ),
                Role.SUPPLY_AIR_TEMP: np.full(n, _SAT),
                Role.MIXED_AIR_TEMP: _SAT + dt,
            },
            index=idx,
        )

    f = _rule(BaselineStore()).analyze_periods(
        "AHU_1", bare("2025-05-01", 1), bare("2025-06-01", 2)
    )
    assert any("free-cooling samples could not be excluded" in c for c in f.caveats)


# --------------------------------------------------------------------------- the waterside confound


def test_a_creep_with_a_warmer_chw_supply_is_caveated():
    base, cur = (
        _cool_frame(start="2025-05-01", seed=1),
        _cool_frame(start="2025-06-01", seed=2, foul_pct=16.0, water_off=4.0),
    )
    f = _rule(BaselineStore()).analyze_periods("AHU_1", base, cur)
    assert f.severity == "fault"
    assert f.metrics["water_supply_shift_f"] > 2.0
    assert any("waterside-reset effect" in c for c in f.caveats)


# --------------------------------------------------------------------------- declines / freeze


def test_it_declines_when_inputs_are_not_mapped():
    base = _cool_frame(start="2025-05-01", seed=1, inputs=False)
    cur = _cool_frame(start="2025-06-01", seed=2, inputs=False)
    f = _rule(BaselineStore()).analyze_periods("AHU_1", base, cur)
    assert f.severity == "info" and f.metrics["reason"] == "coil_valve_inputs_not_mapped"


def test_the_baseline_is_frozen_and_not_refit():
    store = BaselineStore()
    rule = _rule(store)
    rule.analyze_periods("AHU_1", *_base_and({"foul_pct": 16.0}))
    coeffs = dict(store.get("SITE", "AHU_1", "coil_valve_cool").coefficients)
    worse = _cool_frame(start="2025-07-01", seed=5, foul_pct=28.0)
    f = rule.analyze_periods("AHU_1", worse, worse)
    assert store.get("SITE", "AHU_1", "coil_valve_cool").coefficients == coeffs
    assert f.severity == "fault"
