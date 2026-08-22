"""Tests for VAV airflow-tracking drift (camber.rules.vav_airflow_rule).

Synthetic data: an exogenous commanded airflow (zone demand), a damper linear in that command plus
Gaussian noise, with authority loss injected as a damper %-offset at matched command. Nothing is
from a measured dataset; every draw is seeded.
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.rules.base import PeriodRule  # noqa: E402
from camber.rules.builtin import builtin_registry, rule_names  # noqa: E402
from camber.rules.vav_airflow_rule import VavAirflowDrift  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402

_D0 = 15.0  # healthy damper intercept, %
_D_PER_CFM = 0.06  # damper % per cfm of commanded airflow
_SIGMA = 3.0


def _command(n, seed=0):
    rng = np.random.default_rng(seed)
    h = np.arange(n)
    c = 700 + 400 * np.sin((h % 24 - 8) / 24 * 2 * np.pi) + rng.normal(0, 40, n)
    return np.clip(c, 200.0, 1200.0)


def _box_frame(
    n=24 * 30,
    *,
    start="2025-05-01",
    seed=0,
    creep_pct=0.0,
    sp_shift_cfm=0.0,
    duct_static_off=0.0,
    inputs=True,
    flat_cmd=False,
    fan_off_block=0,
    load_role=Role.AIRFLOW_SP,
):
    rng = np.random.default_rng(seed + 100)
    idx = pd.date_range(start, periods=n, freq="1h")
    cmd = np.full(n, 350.0) if flat_cmd else _command(n, seed=seed)  # flat -> no usable load span
    cmd = cmd + sp_shift_cfm  # a pure setpoint move shifts the command (the load axis) only
    damper = _D0 + _D_PER_CFM * cmd + creep_pct + rng.normal(0, _SIGMA, n)
    status = np.ones(n)
    if fan_off_block:
        status[:fan_off_block] = 0.0
    cols = {
        Role.SUPPLY_FAN_STATUS: status,
        Role.DUCT_STATIC: np.full(n, 1.2 + duct_static_off),
    }
    if inputs:
        cols[Role.DAMPER] = np.clip(damper, 0.0, 100.0)
        cols[load_role] = cmd
    return pd.DataFrame(cols, index=idx)


def _rule(store, **kw):
    return VavAirflowDrift(store, site="SITE", run_id="2025-07-01T00:00", **kw)


def _base_and(current_kw):
    return _box_frame(start="2025-05-01", seed=1), _box_frame(
        start="2025-06-01", seed=2, **current_kw
    )


# --------------------------------------------------------------------------- interface


def test_it_is_a_period_rule_and_is_not_auto_registered():
    rule = _rule(BaselineStore())
    assert isinstance(rule, PeriodRule)
    assert "vav_airflow_drift" not in rule_names()
    assert "vav_airflow_drift" not in builtin_registry().names()
    assert rule.roles_required == (Role.DAMPER, Role.AIRFLOW_SP)


# --------------------------------------------------------------------------- the detector


def test_damper_creep_flags_authority_loss():
    f = _rule(BaselineStore()).analyze_periods("VAV_1", *_base_and({"creep_pct": 16.0}))
    assert f.rule == "vav_airflow_drift" and f.severity == "fault"
    assert f.metrics["vav_airflow_drift_pct"] > 15.0
    assert f.metrics["vav_airflow_drift_direction"] == "up"
    assert f.metrics["vav_airflow_sustained_alarm"] is True
    assert f.metrics["vav_airflow_alarm_direction"] == "up"
    assert f.metrics["vav_airflow_which"] == "damper_authority"
    assert "authority loss" in f.summary and "damper creep" in f.summary


def test_a_healthy_box_does_not_flag():
    f = _rule(BaselineStore()).analyze_periods("VAV_1", *_base_and({}))
    assert f.severity == "ok" and abs(f.metrics["vav_airflow_drift_pct"]) < 8.0


def test_it_is_one_sided_a_damper_drop_is_not_a_fault():
    """A box needing less damper for the same command is an authority gain, not a fault."""
    f = _rule(BaselineStore()).analyze_periods("VAV_1", *_base_and({"creep_pct": -16.0}))
    assert f.severity == "ok" and f.metrics["vav_airflow_drift_direction"] == "down"
    assert "less damper is not a fault" in f.summary


def test_a_pure_setpoint_change_is_not_flagged():
    """The setpoint is the load axis, so normal setpoint motion scores ~0 at matched command."""
    f = _rule(BaselineStore()).analyze_periods("VAV_1", *_base_and({"sp_shift_cfm": 150.0}))
    assert f.severity == "ok" and abs(f.metrics["vav_airflow_drift_pct"]) < 8.0
    assert f.metrics["vav_airflow_load_basis"] == "airflow_sp"


def test_upstream_starvation_is_caveated():
    base, cur = (
        _box_frame(start="2025-05-01", seed=1),
        _box_frame(start="2025-06-01", seed=2, creep_pct=16.0, duct_static_off=-0.4),
    )
    f = _rule(BaselineStore()).analyze_periods("VAV_1", base, cur)
    assert f.severity == "fault"
    assert f.metrics["vav_upstream_starvation_suspected"] is True
    assert f.metrics["vav_duct_static_shift_inwc"] < 0
    assert any("plant-side starvation" in c for c in f.caveats)


# ----------------------------------------------------------------------- declines / basis / freeze


def test_it_declines_when_inputs_are_not_mapped():
    base = _box_frame(start="2025-05-01", seed=1, inputs=False)
    cur = _box_frame(start="2025-06-01", seed=2, inputs=False)
    f = _rule(BaselineStore()).analyze_periods("VAV_1", base, cur)
    assert f.severity == "info" and f.metrics["reason"] == "vav_damper_inputs_not_mapped"


def test_it_declines_when_the_command_never_sweeps():
    base = _box_frame(start="2025-05-01", seed=1, flat_cmd=True)
    cur = _box_frame(start="2025-06-01", seed=2, flat_cmd=True)
    f = _rule(BaselineStore()).analyze_periods("VAV_1", base, cur)
    assert f.severity == "info" and f.metrics.get("declined") is True
    assert any("would not support a fit" in c for c in f.caveats)


def test_thresholds_are_screening_grade():
    f = _rule(BaselineStore()).analyze_periods("VAV_1", *_base_and({"creep_pct": 16.0}))
    assert f.metrics["magnitude_threshold_confidence"] == "screening-grade"


def test_the_delivered_airflow_basis_is_a_constructor_option():
    store = BaselineStore()
    rule = _rule(store, load_role=Role.AIRFLOW)
    assert rule.roles_required == (Role.DAMPER, Role.AIRFLOW)
    base = _box_frame(start="2025-05-01", seed=1, load_role=Role.AIRFLOW)
    cur = _box_frame(start="2025-06-01", seed=2, creep_pct=16.0, load_role=Role.AIRFLOW)
    f = rule.analyze_periods("VAV_1", base, cur)
    assert f.severity == "fault" and f.metrics["vav_airflow_load_basis"] == "airflow"


def test_the_baseline_is_frozen_and_not_refit():
    store = BaselineStore()
    rule = _rule(store)
    rule.analyze_periods("VAV_1", *_base_and({"creep_pct": 16.0}))
    coeffs = dict(store.get("SITE", "VAV_1", "vav_damper").coefficients)
    worse = _box_frame(start="2025-07-01", seed=5, creep_pct=28.0)
    f = rule.analyze_periods("VAV_1", worse, worse)
    assert store.get("SITE", "VAV_1", "vav_damper").coefficients == coeffs
    assert f.severity == "fault"


def test_fan_off_samples_are_gated_out():
    base, cur = (
        _box_frame(start="2025-05-01", seed=1),
        _box_frame(start="2025-06-01", seed=2, fan_off_block=24 * 8),  # 8 days fan off, healthy
    )
    f = _rule(BaselineStore()).analyze_periods("VAV_1", base, cur)
    assert f.metrics["vav_airflow_inactive_excluded_pct"] > 0.0
    assert f.severity == "ok"


def test_it_declines_when_freezing_is_disabled_and_no_baseline():
    rule = _rule(BaselineStore(), freeze_if_missing=False)
    f = rule.analyze_periods("VAV_1", *_base_and({}))
    assert f.severity == "info" and f.metrics.get("declined") is True
    assert any("freezing is disabled" in c for c in f.caveats)


def test_it_declines_when_the_current_period_is_all_inactive():
    store = BaselineStore()
    rule = _rule(store)
    rule.analyze_periods("VAV_1", *_base_and({}))  # freeze a baseline first
    # a current period whose commands are all below the min-command floor -> nothing scoreable
    idx = pd.date_range("2025-07-01", periods=24 * 30, freq="1h")
    dead = pd.DataFrame(
        {
            Role.SUPPLY_FAN_STATUS: np.ones(len(idx)),
            Role.DAMPER: np.full(len(idx), 12.0),
            Role.AIRFLOW_SP: np.full(len(idx), 10.0),  # < MIN_COMMAND_CFM
        },
        index=idx,
    )
    f = rule.analyze_periods("VAV_1", dead, dead)
    assert f.severity == "info" and f.metrics.get("declined") is True
    assert "nothing scoreable" in f.summary


def test_no_baseline_scatter_is_judged_on_percent_alone():
    """A perfectly linear baseline (sigma_f = 0) -> drift judged on damper % alone (NaN-sigma)."""

    def _noiseless(start, seed, creep=0.0):
        idx = pd.date_range(start, periods=24 * 30, freq="1h")
        cmd = _command(len(idx), seed=seed)
        return pd.DataFrame(
            {
                Role.SUPPLY_FAN_STATUS: np.ones(len(idx)),
                Role.DAMPER: np.clip(_D0 + _D_PER_CFM * cmd + creep, 0.0, 100.0),  # no noise
                Role.AIRFLOW_SP: cmd,
            },
            index=idx,
        )

    f = _rule(BaselineStore()).analyze_periods(
        "VAV_1", _noiseless("2025-05-01", 1), _noiseless("2025-06-01", 2, creep=16.0)
    )
    assert f.severity == "fault"
    assert any("no residual scatter" in c for c in f.caveats)
