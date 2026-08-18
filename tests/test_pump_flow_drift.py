"""Tests for pump flow-at-matched-speed drift (camber.rules.pump_flow_rule) + the down-CUSUM.

Synthetic data: a linear flow-vs-speed relationship (affinity Q∝N) plus Gaussian noise, with faults
injected as explicit gpm deficits. Nothing is drawn from any measured dataset.
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.chillerbaseline import fit_load_baseline  # noqa: E402
from camber.chillerdrift import ApproachDriftMonitor  # noqa: E402
from camber.driftthresholds import MAGNITUDE_CONFIDENCE, TEMPORAL_CONFIDENCE  # noqa: E402
from camber.interop.semantic223 import role_223_quantity  # noqa: E402
from camber.model.roles import HAYSTACK_HINT, STATUS_ROLES, Role  # noqa: E402
from camber.rules.base import PeriodRule  # noqa: E402
from camber.rules.builtin import builtin_registry, rule_names  # noqa: E402
from camber.rules.pump_flow_rule import PumpFlowDrift  # noqa: E402
from camber.sensorhealth import PHYSICAL_BOUNDS  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402

# A synthetic CHW pump: ~10 gpm per % speed, so ~1000 gpm near full; 12 gpm run-to-run.
_GPM_PER_PCT = 10.0
_SIGMA_GPM = 12.0


def _speed(n, seed=0, offset=0.0):
    rng = np.random.default_rng(seed)
    h = np.arange(n)
    # DP-control duty: speed swings 35-100% over the day
    s = 68 + 30 * np.sin((h % 24 - 8) / 24 * 2 * np.pi) + rng.normal(0, 4, n)
    return np.clip(s + offset, 15.0, 100.0)


def _frame(
    n=24 * 30,
    *,
    start="2025-05-01",
    seed=0,
    deficit_gpm=0.0,
    dp_offset=0.0,
    flow=True,
    speed=True,
    status=False,
):
    rng = np.random.default_rng(seed + 100)
    idx = pd.date_range(start, periods=n, freq="1h")
    spd = _speed(n, seed=seed)
    cols = {}
    if speed:
        cols[Role.CHW_PUMP_SPEED] = spd
    if flow:
        cols[Role.CHW_FLOW] = _GPM_PER_PCT * spd + deficit_gpm + rng.normal(0, _SIGMA_GPM, n)
    cols[Role.CHW_DIFF_PRESS] = 12.0 + dp_offset + rng.normal(0, 0.4, n)
    if status:
        cols[Role.PUMP_STATUS] = np.ones(n)
    return pd.DataFrame(cols, index=idx)


def _rule(store, **kw):
    return PumpFlowDrift(store, site="SITE", run_id="2025-07-01T00:00", **kw)


def _base_and(current_kw):
    return _frame(start="2025-05-01", seed=1), _frame(start="2025-06-01", seed=2, **current_kw)


# --------------------------------------------------------------------------- the engine change


def test_monitor_accepts_direction_down():
    n = 24 * 20
    rng = np.random.default_rng(3)
    spd = _speed(n, seed=2)
    base = pd.DataFrame(
        {
            "tons": _speed(n, seed=1),
            "approach_f": _GPM_PER_PCT * _speed(n, seed=1)
            + np.random.default_rng(4).normal(0, _SIGMA_GPM, n),
        },
        index=pd.date_range("2025-05-01", periods=n, freq="1h"),
    )
    baseline = fit_load_baseline(base, metric_col="approach_f", metric_range=(0.0, 1e6))
    assert baseline is not None
    # a sustained DEFICIT
    frame = pd.DataFrame(
        {
            "tons": spd,
            "approach_f": _GPM_PER_PCT * spd - 5.0 * _SIGMA_GPM + rng.normal(0, _SIGMA_GPM, n),
        },
        index=pd.date_range("2025-06-01", periods=n, freq="1h"),
    )
    rng2 = (0.0, 1e6)
    assert (
        not ApproachDriftMonitor(baseline, direction="up").run(frame, approach_range=rng2).alarmed
    )
    down = ApproachDriftMonitor(baseline, direction="down").run(frame, approach_range=rng2)
    assert down.alarmed and down.alarm_direction == "down"


def test_monitor_rejects_bad_direction():
    n = 24 * 20
    base = pd.DataFrame(
        {
            "tons": _speed(n, seed=1),
            "approach_f": _GPM_PER_PCT * _speed(n, seed=1)
            + np.random.default_rng(4).normal(0, _SIGMA_GPM, n),
        },
        index=pd.date_range("2025-05-01", periods=n, freq="1h"),
    )
    baseline = fit_load_baseline(base, metric_col="approach_f", metric_range=(0.0, 1e6))
    try:
        ApproachDriftMonitor(baseline, direction="sideways")
    except ValueError as exc:
        assert "up" in str(exc) and "down" in str(exc)
    else:
        raise AssertionError("expected ValueError on a bad direction")


# --------------------------------------------------------------------------- role wiring


def test_the_new_roles_are_fully_wired():
    assert Role.HW_FLOW.value == "hw_flow" and Role.PUMP_STATUS.value == "pump_status"
    for r in (Role.HW_FLOW, Role.PUMP_STATUS):
        assert r in HAYSTACK_HINT
    assert (
        Role.HW_FLOW in PHYSICAL_BOUNDS
        and PHYSICAL_BOUNDS[Role.HW_FLOW][0] < PHYSICAL_BOUNDS[Role.HW_FLOW][1]
    )
    assert role_223_quantity(Role.HW_FLOW) == ("VolumeFlowRate", "Water")
    assert Role.PUMP_STATUS in STATUS_ROLES  # loaded as a step series
    assert role_223_quantity(Role.PUMP_STATUS) is None  # status carries no QUDT quantity


# --------------------------------------------------------------------------- interface


def test_it_is_a_period_rule_and_is_not_auto_registered():
    rule = _rule(BaselineStore())
    assert isinstance(rule, PeriodRule)
    assert "pump_flow_drift" not in rule_names()
    assert "pump_flow_drift" not in builtin_registry().names()


def test_defaults_to_chilled_water_and_reparameterizes_to_hot_water():
    chw = _rule(BaselineStore())
    assert chw.roles_required == (Role.CHW_FLOW, Role.CHW_PUMP_SPEED)
    hw = _rule(BaselineStore(), flow_role=Role.HW_FLOW, speed_role=Role.HW_PUMP_SPEED)
    assert hw.roles_required == (Role.HW_FLOW, Role.HW_PUMP_SPEED)


# --------------------------------------------------------------------------- the detector


def test_a_flow_deficit_flags():
    store = BaselineStore()
    f = _rule(store).analyze_periods("P_1", *_base_and({"deficit_gpm": -70.0}))
    assert f.rule == "pump_flow_drift"
    assert f.severity == "fault"
    assert f.metrics["pump_flow_drift_gpm"] < -30.0
    assert f.metrics["pump_flow_drift_direction"] == "down"
    assert f.metrics["pump_flow_drift_sigma"] < -4.0
    assert f.metrics["pump_flow_sustained_alarm"] is True
    assert f.metrics["pump_flow_alarm_direction"] == "down"
    assert f.metrics["thresholds_provisional"] is True


def test_it_is_one_sided_a_flow_surplus_does_not_flag():
    store = BaselineStore()
    f = _rule(store).analyze_periods("P_1", *_base_and({"deficit_gpm": 70.0}))
    assert f.severity == "ok"
    assert f.metrics["pump_flow_drift_gpm"] > 30.0  # a big move...
    assert f.metrics["pump_flow_drift_direction"] == "up"  # ...but the harmless way
    assert f.metrics["pump_flow_sustained_alarm"] is False


def test_a_steady_pump_does_not_flag():
    f = _rule(BaselineStore()).analyze_periods("P_1", *_base_and({}))
    assert f.severity == "ok"
    assert abs(f.metrics["pump_flow_drift_gpm"]) < 12.0


def test_the_finding_labels_its_two_threshold_classes():
    f = _rule(BaselineStore()).analyze_periods("P_1", *_base_and({"deficit_gpm": -70.0}))
    assert f.metrics["magnitude_threshold_confidence"] == MAGNITUDE_CONFIDENCE
    assert f.metrics["temporal_threshold_confidence"] == TEMPORAL_CONFIDENCE


# --------------------------------------------------------------------------- the confound


def test_a_deficit_with_rising_dp_is_caveated_as_resistance():
    store = BaselineStore()
    base, cur = (
        _frame(start="2025-05-01", seed=1),
        _frame(start="2025-06-01", seed=2, deficit_gpm=-70.0, dp_offset=4.0),
    )
    f = _rule(store).analyze_periods("P_1", base, cur)
    assert f.severity == "fault"
    assert f.metrics["dp_shift"] > 2.0
    assert any("system resistance" in c for c in f.caveats)


def test_a_deficit_with_flat_dp_reads_as_pump_wear():
    f = _rule(BaselineStore()).analyze_periods("P_1", *_base_and({"deficit_gpm": -70.0}))
    assert f.severity == "fault"
    assert abs(f.metrics["dp_shift"]) < 1.0
    assert not any("system resistance" in c for c in f.caveats)


# --------------------------------------------------------------------------- status gating


def test_status_gating_drops_off_samples():
    """With a pump-status point, off (status=0) samples are masked out before scoring."""
    rng = np.random.default_rng(9)
    n = 24 * 30
    idx = pd.date_range("2025-06-01", periods=n, freq="1h")
    spd = _speed(n, seed=2)
    running = np.ones(n)
    running[: n // 2] = 0.0  # first half "off"
    # while off, flow reads garbage that would wreck an ungated fit
    flow = np.where(running > 0.5, _GPM_PER_PCT * spd, 0.0) + rng.normal(0, _SIGMA_GPM, n)
    cur = pd.DataFrame(
        {Role.CHW_PUMP_SPEED: spd, Role.CHW_FLOW: flow, Role.PUMP_STATUS: running}, index=idx
    )
    base = _frame(start="2025-05-01", seed=1, status=True)
    f = _rule(BaselineStore()).analyze_periods("P_1", base, cur)
    assert f.severity == "ok"  # masking to running samples keeps it steady


# --------------------------------------------------------------------------- declines


def test_it_declines_when_flow_or_speed_is_not_mapped():
    base = _frame(start="2025-05-01", seed=1, flow=False)
    cur = _frame(start="2025-06-01", seed=2, flow=False)
    f = _rule(BaselineStore()).analyze_periods("P_1", base, cur)
    assert f.severity == "info" and f.metrics["declined"] is True
    assert f.metrics["reason"] == "flow_or_speed_not_mapped"


def test_it_declines_when_no_baseline_can_be_frozen():
    f = _rule(BaselineStore(), freeze_if_missing=False).analyze_periods("P_1", *_base_and({}))
    assert f.severity == "info" and f.metrics["declined"] is True


# --------------------------------------------------------------------------- freeze / storage


def test_the_baseline_is_frozen_and_not_refit():
    store = BaselineStore()
    rule = _rule(store)
    rule.analyze_periods("P_1", *_base_and({"deficit_gpm": -70.0}))
    coeffs = dict(store.get("SITE", "P_1", "pump_flow").coefficients)
    worse = _frame(start="2025-07-01", seed=5, deficit_gpm=-110.0)
    f = rule.analyze_periods("P_1", worse, worse)
    assert store.get("SITE", "P_1", "pump_flow").coefficients == coeffs
    assert f.severity == "fault"
