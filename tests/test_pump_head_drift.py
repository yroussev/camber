"""Tests for pump head-at-matched-speed drift (camber.rules.pump_head_rule).

Synthetic data: a linear head-vs-speed relationship plus Gaussian noise, with faults injected as psi
deficits and the operating-point confound as a flow rise. Nothing is drawn from a measured dataset.
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.interop.semantic223 import role_223_quantity  # noqa: E402
from camber.model.roles import HAYSTACK_HINT, Role  # noqa: E402
from camber.rules.base import PeriodRule  # noqa: E402
from camber.rules.builtin import builtin_registry, rule_names  # noqa: E402
from camber.rules.pump_head_rule import PumpHeadDrift  # noqa: E402
from camber.sensorhealth import PHYSICAL_BOUNDS  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402

_PSI_PER_PCT = 0.5  # ~34 psi at 68% speed, ~50 psi near full
_SIGMA_PSI = 1.5


def _speed(n, seed=0):
    rng = np.random.default_rng(seed)
    h = np.arange(n)
    s = 68 + 30 * np.sin((h % 24 - 8) / 24 * 2 * np.pi) + rng.normal(0, 4, n)
    return np.clip(s, 15.0, 100.0)


def _frame(
    n=24 * 30,
    *,
    start="2025-05-01",
    seed=0,
    deficit_psi=0.0,
    flow_offset=0.0,
    head=True,
    speed=True,
):
    rng = np.random.default_rng(seed + 100)
    idx = pd.date_range(start, periods=n, freq="1h")
    spd = _speed(n, seed=seed)
    cols = {}
    if speed:
        cols[Role.CHW_PUMP_SPEED] = spd
    if head:
        cols[Role.PUMP_HEAD] = _PSI_PER_PCT * spd + deficit_psi + rng.normal(0, _SIGMA_PSI, n)
    cols[Role.CHW_FLOW] = 10.0 * spd + flow_offset + rng.normal(0, 12, n)
    return pd.DataFrame(cols, index=idx)


def _rule(store, **kw):
    return PumpHeadDrift(store, site="SITE", run_id="2025-07-01T00:00", **kw)


def _base_and(current_kw):
    return _frame(start="2025-05-01", seed=1), _frame(start="2025-06-01", seed=2, **current_kw)


# --------------------------------------------------------------------------- role wiring


def test_pump_head_role_is_fully_wired():
    assert Role.PUMP_HEAD.value == "pump_head"
    assert Role.PUMP_HEAD in HAYSTACK_HINT
    lo, hi = PHYSICAL_BOUNDS[Role.PUMP_HEAD]
    assert lo < hi
    assert role_223_quantity(Role.PUMP_HEAD) == ("Pressure", "Water")


# --------------------------------------------------------------------------- interface


def test_it_is_a_period_rule_and_is_not_auto_registered():
    rule = _rule(BaselineStore())
    assert isinstance(rule, PeriodRule)
    assert "pump_head_drift" not in rule_names()
    assert "pump_head_drift" not in builtin_registry().names()


def test_speed_required_head_optional():
    rule = _rule(BaselineStore())
    assert Role.CHW_PUMP_SPEED in rule.roles_required
    assert Role.PUMP_HEAD in rule.roles_optional and Role.PUMP_HEAD not in rule.roles_required


# --------------------------------------------------------------------------- the detector


def test_a_head_deficit_flags():
    f = _rule(BaselineStore()).analyze_periods("P_1", *_base_and({"deficit_psi": -8.0}))
    assert f.rule == "pump_head_drift" and f.severity == "fault"
    assert f.metrics["pump_head_drift_psi"] < -4.0
    assert f.metrics["pump_head_drift_direction"] == "down"
    assert f.metrics["pump_head_drift_sigma"] < -4.0
    assert f.metrics["pump_head_sustained_alarm"] is True
    assert f.metrics["pump_head_alarm_direction"] == "down"


def test_it_is_one_sided_a_head_surplus_does_not_flag():
    f = _rule(BaselineStore()).analyze_periods("P_1", *_base_and({"deficit_psi": 8.0}))
    assert f.severity == "ok" and f.metrics["pump_head_drift_direction"] == "up"


def test_a_steady_pump_does_not_flag():
    f = _rule(BaselineStore()).analyze_periods("P_1", *_base_and({}))
    assert f.severity == "ok" and abs(f.metrics["pump_head_drift_psi"]) < 1.5


# --------------------------------------------------------------------------- the confound


def test_a_deficit_with_rising_flow_is_caveated_as_curve_ride():
    base, cur = (
        _frame(start="2025-05-01", seed=1),
        _frame(start="2025-06-01", seed=2, deficit_psi=-8.0, flow_offset=120.0),
    )
    f = _rule(BaselineStore()).analyze_periods("P_1", base, cur)
    assert f.severity == "fault"
    assert f.metrics["flow_shift"] > 20.0
    assert any("rides down the pump curve" in c for c in f.caveats)


def test_a_deficit_with_flat_flow_reads_as_wear():
    f = _rule(BaselineStore()).analyze_periods("P_1", *_base_and({"deficit_psi": -8.0}))
    assert f.severity == "fault"
    assert not any("pump curve" in c for c in f.caveats)


# --------------------------------------------------------------------------- instrumentation gate


def test_it_declines_loudly_when_head_is_not_mapped():
    base = _frame(start="2025-05-01", seed=1, head=False)
    cur = _frame(start="2025-06-01", seed=2, head=False)
    f = _rule(BaselineStore()).analyze_periods("P_1", base, cur)
    assert f.severity == "info" and f.metrics["declined"] is True
    assert f.metrics["reason"] == "pump_head_not_mapped"
    assert any("does not publish" in c for c in f.caveats)


def test_it_declines_when_speed_is_not_mapped():
    base = _frame(start="2025-05-01", seed=1, speed=False)
    cur = _frame(start="2025-06-01", seed=2, speed=False)
    f = _rule(BaselineStore()).analyze_periods("P_1", base, cur)
    assert f.severity == "info" and f.metrics["reason"] == "speed_not_mapped"


# --------------------------------------------------------------------------- freeze


def test_the_baseline_is_frozen_and_not_refit():
    store = BaselineStore()
    rule = _rule(store)
    rule.analyze_periods("P_1", *_base_and({"deficit_psi": -8.0}))
    coeffs = dict(store.get("SITE", "P_1", "pump_head").coefficients)
    worse = _frame(start="2025-07-01", seed=5, deficit_psi=-14.0)
    f = rule.analyze_periods("P_1", worse, worse)
    assert store.get("SITE", "P_1", "pump_head").coefficients == coeffs
    assert f.severity == "fault"
