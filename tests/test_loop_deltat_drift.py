"""Tests for hydronic loop delta-T drift (camber.rules.loop_deltat_rule).

Synthetic data: a loop ΔT with a mild flow dependence plus Gaussian noise, with faults injected as
degF offsets (collapse = low-ΔT syndrome, widen = starvation). Nothing is from a measured dataset.
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.rules.base import PeriodRule  # noqa: E402
from camber.rules.builtin import builtin_registry, rule_names  # noqa: E402
from camber.rules.loop_deltat_rule import LoopDeltaTDrift  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402

_DT0 = 12.0  # design chilled-water ΔT
_SIGMA_F = 0.5


def _flow(n, seed=0):
    rng = np.random.default_rng(seed)
    h = np.arange(n)
    f = 680 + 300 * np.sin((h % 24 - 8) / 24 * 2 * np.pi) + rng.normal(0, 40, n)
    return np.clip(f, 150.0, 1000.0)


def _frame(n=24 * 30, *, start="2025-05-01", seed=0, offset_f=0.0, inputs=True):
    rng = np.random.default_rng(seed + 100)
    idx = pd.date_range(start, periods=n, freq="1h")
    flow = _flow(n, seed=seed)
    dt = _DT0 - 0.001 * (flow - 680) + offset_f + rng.normal(0, _SIGMA_F, n)
    cols = {Role.CHW_FLOW: flow}
    if inputs:
        cols[Role.CHW_SUPPLY_TEMP] = np.full(n, 44.0)
        cols[Role.CHW_RETURN_TEMP] = 44.0 + dt
    return pd.DataFrame(cols, index=idx)


def _rule(store, **kw):
    return LoopDeltaTDrift(store, site="SITE", run_id="2025-07-01T00:00", **kw)


def _base_and(current_kw):
    return _frame(start="2025-05-01", seed=1), _frame(start="2025-06-01", seed=2, **current_kw)


# --------------------------------------------------------------------------- interface


def test_it_is_a_period_rule_and_is_not_auto_registered():
    rule = _rule(BaselineStore())
    assert isinstance(rule, PeriodRule)
    assert "loop_deltat_drift" not in rule_names()
    assert "loop_deltat_drift" not in builtin_registry().names()


def test_roles_required_are_the_temperature_pair_and_the_normalizer():
    rule = _rule(BaselineStore())
    assert rule.roles_required == (Role.CHW_RETURN_TEMP, Role.CHW_SUPPLY_TEMP, Role.CHW_FLOW)


# --------------------------------------------------------------------------- the detector


def test_a_collapsing_deltat_is_low_deltat_syndrome():
    f = _rule(BaselineStore()).analyze_periods("L_1", *_base_and({"offset_f": -3.0}))
    assert f.rule == "loop_deltat_drift" and f.severity == "fault"
    assert f.metrics["loop_deltat_drift_f"] < -2.0
    assert f.metrics["loop_deltat_drift_direction"] == "down"
    assert "low-ΔT syndrome" in f.summary
    assert f.metrics["loop_deltat_sustained_alarm"] is True
    assert f.metrics["loop_deltat_alarm_direction"] == "down"


def test_a_widening_deltat_is_starvation():
    f = _rule(BaselineStore()).analyze_periods("L_1", *_base_and({"offset_f": 3.0}))
    assert f.severity == "fault"
    assert f.metrics["loop_deltat_drift_direction"] == "up"
    assert "starvation" in f.summary


def test_a_steady_loop_does_not_flag():
    f = _rule(BaselineStore()).analyze_periods("L_1", *_base_and({}))
    assert f.severity == "ok"
    assert abs(f.metrics["loop_deltat_drift_f"]) < 1.0
    assert f.metrics["loop_deltat_sustained_alarm"] is False


def test_a_collapse_and_an_equal_widen_score_the_same_magnitude():
    down = _rule(BaselineStore()).analyze_periods("L_1", *_base_and({"offset_f": -3.0}))
    up = _rule(BaselineStore()).analyze_periods("L_1", *_base_and({"offset_f": 3.0}))
    assert down.severity == up.severity == "fault"
    assert down.metrics["loop_deltat_drift_f"] < 0 < up.metrics["loop_deltat_drift_f"]


# --------------------------------------------------------------------------- hot-water loop


def test_it_reparameterizes_to_a_hot_water_loop():
    """HW convention: warm=supply, cool=return; ΔT still positive, and a collapse still flags."""
    n = 24 * 30
    rng = np.random.default_rng(7)
    idx = pd.date_range("2025-06-01", periods=n, freq="1h")
    flow = _flow(n, seed=2)
    dt = 25.0 + rng.normal(0, _SIGMA_F, n)  # healthy HW ΔT ~25°F

    def hw(dt_off):
        d = dt + dt_off
        return pd.DataFrame(
            {Role.HW_FLOW: flow, Role.HW_SUPPLY_TEMP: 180.0, Role.HW_RETURN_TEMP: 180.0 - d},
            index=idx,
        )

    base = pd.DataFrame(
        {
            Role.HW_FLOW: _flow(n, seed=1),
            Role.HW_SUPPLY_TEMP: 180.0,
            Role.HW_RETURN_TEMP: 180.0 - (25.0 + np.random.default_rng(4).normal(0, _SIGMA_F, n)),
        },
        index=pd.date_range("2025-05-01", periods=n, freq="1h"),
    )
    rule = _rule(
        BaselineStore(),
        warm_role=Role.HW_SUPPLY_TEMP,
        cool_role=Role.HW_RETURN_TEMP,
        load_role=Role.HW_FLOW,
    )
    assert rule.roles_required == (Role.HW_SUPPLY_TEMP, Role.HW_RETURN_TEMP, Role.HW_FLOW)
    f = rule.analyze_periods("HW_1", base, hw(-4.0))
    assert f.severity == "fault" and f.metrics["loop_deltat_drift_direction"] == "down"


# --------------------------------------------------------------------------- declines


def test_it_declines_when_inputs_are_not_mapped():
    base = _frame(start="2025-05-01", seed=1, inputs=False)
    cur = _frame(start="2025-06-01", seed=2, inputs=False)
    f = _rule(BaselineStore()).analyze_periods("L_1", base, cur)
    assert f.severity == "info" and f.metrics["declined"] is True
    assert f.metrics["reason"] == "deltat_inputs_not_mapped"


def test_the_baseline_is_frozen_and_not_refit():
    store = BaselineStore()
    rule = _rule(store)
    rule.analyze_periods("L_1", *_base_and({"offset_f": -3.0}))
    coeffs = dict(store.get("SITE", "L_1", "loop_deltat").coefficients)
    worse = _frame(start="2025-07-01", seed=5, offset_f=-5.0)
    f = rule.analyze_periods("L_1", worse, worse)
    assert store.get("SITE", "L_1", "loop_deltat").coefficients == coeffs
    assert f.severity == "fault"
