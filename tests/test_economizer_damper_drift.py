"""Tests for economizer OA-delivery drift (camber.rules.economizer_damper_rule).

Synthetic data: an OA-damper command that sweeps its range, a delivered outdoor-air fraction linear
in that command, and the three temperatures back-solved from the mixing relation
``OAF = 100*(RAT-MAT)/(RAT-OAT)`` so they are self-consistent. Mechanical drift is injected as an
OA-fraction offset **at matched command** (+ = leak / stuck-open, - = slipping-closed). Nothing is
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
from camber.rules.economizer_damper_rule import (  # noqa: E402
    ECON_WARN_PCT,
    EconomizerDamperDrift,
)
from camber.store.modelstore import BaselineStore  # noqa: E402

_RAT = 74.0  # steady return-air temperature (degF)
_INTERCEPT = 8.0  # delivered OA fraction (%) at zero command
_SLOPE = 0.82  # delivered OA fraction (%) per % of damper command
_MAT_SIGMA = 0.6  # mixed-air sensor noise (degF) -> the OA-fraction residual scatter


def _cmd(n, seed):
    rng = np.random.default_rng(seed)
    h = np.arange(n)
    c = 55 + 40 * np.sin((h % 24 - 6) / 24 * 2 * np.pi) + rng.normal(0, 4, n)
    return np.clip(c, 15.0, 100.0)


def _econ_frame(
    n=24 * 30,
    *,
    start="2025-04-01",
    seed=0,
    drift_pp=0.0,
    inputs=True,
    no_damper=False,
    degen_block=0,
    flat_cmd=False,
):
    rng = np.random.default_rng(seed + 100)
    idx = pd.date_range(start, periods=n, freq="1h")
    cmd = np.clip(20 + rng.normal(0, 1.0, n), 15.0, 100.0) if flat_cmd else _cmd(n, seed)
    oat = 58 + 9 * np.sin((np.arange(n) % 24 - 15) / 24 * 2 * np.pi) + rng.normal(0, 1.0, n)
    oat = np.clip(oat, 45.0, 70.0)
    rat = np.full(n, _RAT) + rng.normal(0, 0.4, n)
    if degen_block:  # a block where outdoor and return air are too close for a stable ratio
        oat[:degen_block] = _RAT - 2.0
    oaf = _INTERCEPT + _SLOPE * cmd + drift_pp
    mat = rat - (oaf / 100.0) * (rat - oat) + rng.normal(0, _MAT_SIGMA, n)
    cols = {Role.OAT: oat, Role.RETURN_AIR_TEMP: rat}
    if inputs:
        cols[Role.MIXED_AIR_TEMP] = mat
    if not no_damper:
        cols[Role.OA_DAMPER] = cmd
    return pd.DataFrame(cols, index=idx)


def _rule(store, **kw):
    return EconomizerDamperDrift(store, site="SITE", run_id="2025-06-01T00:00", **kw)


def _base_and(**current_kw):
    return _econ_frame(start="2025-04-01", seed=1), _econ_frame(
        start="2025-05-01", seed=2, **current_kw
    )


# --------------------------------------------------------------------------- interface


def test_it_is_a_period_rule_and_is_not_auto_registered():
    rule = _rule(BaselineStore())
    assert isinstance(rule, PeriodRule)
    assert "economizer_damper_drift" not in rule_names()
    assert "economizer_damper_drift" not in builtin_registry().names()
    assert rule.roles_required == (
        Role.OAT,
        Role.RETURN_AIR_TEMP,
        Role.MIXED_AIR_TEMP,
        Role.OA_DAMPER,
    )


# --------------------------------------------------------------------------- the detector


def test_leak_stuck_open_flags_excess_oa():
    f = _rule(BaselineStore()).analyze_periods("AHU_1", *_base_and(drift_pp=24.0))
    assert f.rule == "economizer_damper_drift" and f.severity == "fault"
    assert f.metrics["econ_oa_fraction_drift_pct"] > ECON_WARN_PCT
    assert f.metrics["econ_oa_fraction_drift_direction"] == "up"
    assert f.metrics["econ_oa_fraction_sustained_alarm"] is True
    assert "excess outdoor air" in f.summary and "over-delivering" in f.summary


def test_slipping_closed_flags_lost_free_cooling():
    """Two-sided: less OA than baseline at matched command is a fault too (unlike the coil rule)."""
    f = _rule(BaselineStore()).analyze_periods("AHU_1", *_base_and(drift_pp=-24.0))
    assert f.severity == "fault"
    assert f.metrics["econ_oa_fraction_drift_direction"] == "down"
    assert "lost free cooling" in f.summary and "under-delivering" in f.summary


def test_a_healthy_economizer_does_not_flag():
    f = _rule(BaselineStore()).analyze_periods("AHU_1", *_base_and())
    assert f.severity == "ok"
    assert abs(f.metrics["econ_oa_fraction_drift_pct"]) < ECON_WARN_PCT


# --------------------------------------------------------------------------- gating / conditioning


def test_degenerate_mixing_samples_are_gated_out():
    """Rows where outdoor ~ return air must not manufacture a fault on a healthy economizer."""
    base, cur = (
        _econ_frame(start="2025-04-01", seed=1),
        _econ_frame(
            start="2025-05-01", seed=2, degen_block=24 * 8
        ),  # 8 healthy but ill-conditioned
    )
    f = _rule(BaselineStore()).analyze_periods("AHU_1", base, cur)
    assert f.metrics["econ_degenerate_excluded_pct"] > 0.0
    assert f.severity == "ok"


def test_mixed_air_sensor_caveat_is_always_present():
    f = _rule(BaselineStore()).analyze_periods("AHU_1", *_base_and())
    assert any("stratification error" in c and "Relative Accuracy" in c for c in f.caveats)


def test_screening_grade_label():
    f = _rule(BaselineStore()).analyze_periods("AHU_1", *_base_and(drift_pp=24.0))
    assert f.metrics["magnitude_threshold_confidence"] == "screening-grade"


# --------------------------------------------------------------------------- declines


def test_it_declines_when_inputs_are_not_mapped():
    f = _rule(BaselineStore()).analyze_periods(
        "AHU_1",
        _econ_frame(start="2025-04-01", seed=1, no_damper=True),
        _econ_frame(start="2025-05-01", seed=2, no_damper=True),
    )
    assert f.severity == "info" and f.metrics["reason"] == "economizer_inputs_not_mapped"


def test_narrow_command_span_declines_loudly():
    f = _rule(BaselineStore()).analyze_periods(
        "AHU_1",
        _econ_frame(start="2025-04-01", seed=1, flat_cmd=True),
        _econ_frame(start="2025-05-01", seed=2),
    )
    assert f.severity == "info" and f.metrics.get("declined") is True
    assert any("never sweeps a usable range" in c for c in f.caveats)


def test_the_baseline_is_frozen_and_not_refit():
    store = BaselineStore()
    rule = _rule(store)
    rule.analyze_periods("AHU_1", *_base_and(drift_pp=24.0))
    coeffs = dict(store.get("SITE", "AHU_1", "economizer_damper").coefficients)
    worse = _econ_frame(start="2025-06-01", seed=5, drift_pp=40.0)
    f = rule.analyze_periods("AHU_1", worse, worse)
    assert store.get("SITE", "AHU_1", "economizer_damper").coefficients == coeffs
    assert f.severity == "fault"
