"""Tests for hydronic loop DP drift (camber.rules.loop_dp_rule).

Synthetic data: a loop DP on its system curve plus Gaussian noise, with faults injected as DP-unit
offsets and reset schedules as setpoint offsets. Nothing is drawn from a measured dataset.
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
from camber.rules.loop_dp_rule import LoopDPDrift  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402

_DP0 = 12.0
_SIGMA = 0.5


def _flow(n, seed=0):
    rng = np.random.default_rng(seed)
    h = np.arange(n)
    f = 680 + 300 * np.sin((h % 24 - 8) / 24 * 2 * np.pi) + rng.normal(0, 40, n)
    return np.clip(f, 150.0, 1000.0)


def _frame(n=24 * 30, *, start="2025-05-01", seed=0, dp_offset=0.0, sp_offset=None, inputs=True):
    rng = np.random.default_rng(seed + 100)
    idx = pd.date_range(start, periods=n, freq="1h")
    flow = _flow(n, seed=seed)
    cols = {Role.CHW_FLOW: flow}
    if inputs:
        cols[Role.CHW_DIFF_PRESS] = (
            _DP0 + 0.001 * (flow - 680) + dp_offset + rng.normal(0, _SIGMA, n)
        )
    if sp_offset is not None:
        cols[Role.CHW_DIFF_PRESS_SP] = np.full(n, _DP0 + sp_offset)
    return pd.DataFrame(cols, index=idx)


def _rule(store, **kw):
    return LoopDPDrift(store, site="SITE", run_id="2025-07-01T00:00", **kw)


def _base_and(current_kw, base_kw=None):
    return (
        _frame(start="2025-05-01", seed=1, **(base_kw or {})),
        _frame(start="2025-06-01", seed=2, **current_kw),
    )


# --------------------------------------------------------------------------- role wiring


def test_hw_dp_setpoint_role_is_wired():
    assert Role.HW_DIFF_PRESS_SP.value == "hw_diff_press_sp"
    assert Role.HW_DIFF_PRESS_SP in HAYSTACK_HINT
    assert role_223_quantity(Role.HW_DIFF_PRESS_SP) == ("Pressure", "Water")


# --------------------------------------------------------------------------- interface


def test_it_is_a_period_rule_and_is_not_auto_registered():
    rule = _rule(BaselineStore())
    assert isinstance(rule, PeriodRule)
    assert "loop_dp_drift" not in rule_names()
    assert "loop_dp_drift" not in builtin_registry().names()


# --------------------------------------------------------------------------- the detector


def test_rising_dp_is_rising_resistance():
    f = _rule(BaselineStore()).analyze_periods("L_1", *_base_and({"dp_offset": 8.0}))
    assert f.rule == "loop_dp_drift" and f.severity == "fault"
    assert f.metrics["loop_dp_drift_direction"] == "up"
    assert "rising system resistance" in f.summary
    assert f.metrics["loop_dp_setpoint_driven"] is False


def test_falling_dp_is_a_bypass():
    f = _rule(BaselineStore()).analyze_periods("L_1", *_base_and({"dp_offset": -8.0}))
    assert f.severity == "fault" and f.metrics["loop_dp_drift_direction"] == "down"
    assert "bypass" in f.summary


def test_a_steady_loop_does_not_flag():
    f = _rule(BaselineStore()).analyze_periods("L_1", *_base_and({}))
    assert f.severity == "ok" and abs(f.metrics["loop_dp_drift"]) < 1.5


# --------------------------------------------------------------------------- the reset confound


def test_a_reset_that_fully_explains_the_dp_move_does_not_fault():
    """DP rose because its setpoint rose the same amount -> residual ~0 -> not a fault."""
    base, cur = _base_and({"dp_offset": 4.0, "sp_offset": 4.0}, base_kw={"sp_offset": 0.0})
    f = _rule(BaselineStore()).analyze_periods("L_1", base, cur)
    assert f.severity == "ok"
    assert f.metrics["loop_dp_setpoint_driven"] is True
    assert f.metrics["dp_sp_shift"] > 3.0
    assert any("no independent control fault remains" in c for c in f.caveats)


def test_a_reset_that_only_partly_explains_the_move_leaves_a_fault():
    """DP rose more than its setpoint -> the residual is still a real control fault (demoted)."""
    base, cur = _base_and({"dp_offset": 7.0, "sp_offset": 4.0}, base_kw={"sp_offset": 0.0})
    f = _rule(BaselineStore()).analyze_periods("L_1", base, cur)
    assert f.severity == "warn"  # fault (raw +7) demoted to the residual (+3) tier
    assert f.metrics["loop_dp_setpoint_driven"] is True
    assert any("a control fault remains after the reset" in c for c in f.caveats)


def test_a_dp_drift_beyond_the_reset_is_an_independent_fault():
    """SP shifted, but DP drifted well past it -> stays a fault, flagged as independent."""
    base, cur = _base_and({"dp_offset": 8.0, "sp_offset": 3.0}, base_kw={"sp_offset": 0.0})
    f = _rule(BaselineStore()).analyze_periods("L_1", base, cur)
    assert f.severity == "fault"
    assert any("an independent control fault" in c for c in f.caveats)


# --------------------------------------------------------------------------- declines


def test_it_declines_when_dp_or_flow_is_not_mapped():
    base = _frame(start="2025-05-01", seed=1, inputs=False)
    cur = _frame(start="2025-06-01", seed=2, inputs=False)
    f = _rule(BaselineStore()).analyze_periods("L_1", base, cur)
    assert f.severity == "info" and f.metrics["reason"] == "dp_or_flow_not_mapped"


def test_the_baseline_is_frozen_and_not_refit():
    store = BaselineStore()
    rule = _rule(store)
    rule.analyze_periods("L_1", *_base_and({"dp_offset": 8.0}))
    coeffs = dict(store.get("SITE", "L_1", "loop_dp").coefficients)
    worse = _frame(start="2025-07-01", seed=5, dp_offset=14.0)
    f = rule.analyze_periods("L_1", worse, worse)
    assert store.get("SITE", "L_1", "loop_dp").coefficients == coeffs
    assert f.severity == "fault"
