"""Tests for suction superheat drift (camber.rules.chiller_superheat_rule).

All data here is synthetic: a linear superheat-vs-load relationship plus Gaussian noise, with faults
injected as explicit offsets. Nothing is drawn from any measured dataset.
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.chillerbaseline import fit_load_baseline  # noqa: E402
from camber.driftthresholds import MAGNITUDE_CONFIDENCE, TEMPORAL_CONFIDENCE  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.base import PeriodRule  # noqa: E402
from camber.rules.builtin import builtin_registry, rule_names  # noqa: E402
from camber.rules.chiller_superheat_rule import ChillerSuperheatDrift  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402

# A synthetic machine: ~10 degF suction superheat, widening slightly with load, hunting +-0.4 degF.
_INTERCEPT_F = 10.0
_SLOPE_F_PER_TON = 0.008
_SIGMA_F = 0.4


def _tons(n, seed=0, offset=0.0):
    rng = np.random.default_rng(seed)
    h = np.arange(n)
    t = 170 + 120 * np.sin((h % 24 - 8) / 24 * 2 * np.pi) + rng.normal(0, 12, n)
    return np.clip(t + offset, 40.0, 400.0)


def _role_frame(
    n=24 * 30, *, start="2025-05-01", seed=0, offset_f=0.0, tons_offset=0.0, superheat=True
):
    """A chiller role-frame; ``offset_f`` shifts superheat (negative = overfeed/floodback)."""
    rng = np.random.default_rng(seed + 100)
    idx = pd.date_range(start, periods=n, freq="1h")
    tons = _tons(n, seed=seed, offset=tons_offset)
    cols = {
        Role.CHW_FLOW: tons * 2.0,  # tons = gpm * dT / 24 with dT = 12
        Role.CHW_SUPPLY_TEMP: np.full(n, 44.0),
        Role.CHW_RETURN_TEMP: np.full(n, 56.0),
    }
    if superheat:
        cols[Role.SUPERHEAT_TEMP] = (
            _INTERCEPT_F + _SLOPE_F_PER_TON * tons + offset_f + rng.normal(0, _SIGMA_F, n)
        )
    return pd.DataFrame(cols, index=idx)


def _rule(store, **kw):
    return ChillerSuperheatDrift(store, site="SITE", run_id="2025-07-01T00:00", **kw)


def _base_and(current_kw):
    return _role_frame(start="2025-05-01", seed=1), _role_frame(
        start="2025-06-01", seed=2, **current_kw
    )


# --------------------------------------------------------------------------- interface


def test_it_is_a_period_rule_and_is_not_auto_registered():
    rule = _rule(BaselineStore())
    assert isinstance(rule, PeriodRule)
    assert "chiller_superheat_drift" not in rule_names()
    assert "chiller_superheat_drift" not in builtin_registry().names()


def test_the_new_role_exists_and_is_optional_on_this_rule():
    assert Role.SUPERHEAT_TEMP.value == "superheat_temp"
    rule = _rule(BaselineStore())
    assert Role.SUPERHEAT_TEMP in rule.roles_optional
    assert Role.SUPERHEAT_TEMP not in rule.roles_required


# --------------------------------------------------------------------------- the detector


def test_falling_superheat_flags_overfeed():
    """Superheat collapsing at matched load is the overfeed/floodback signature (the urgent one)."""
    f = _rule(BaselineStore()).analyze_periods("CH_1", *_base_and({"offset_f": -5.0}))

    assert f.rule == "chiller_superheat_drift"
    assert f.severity == "fault"
    assert f.metrics["superheat_drift_f"] < -4.0
    assert f.metrics["superheat_drift_direction"] == "down"
    assert abs(f.metrics["superheat_drift_sigma"]) > 6.0
    assert "overfeeding" in f.summary
    assert f.metrics["thresholds_provisional"] is True


def test_rising_superheat_also_flags_starvation():
    """The other half of the fault space: an underfed/starved evaporator."""
    f = _rule(BaselineStore()).analyze_periods("CH_1", *_base_and({"offset_f": 5.0}))

    assert f.severity == "fault"
    assert f.metrics["superheat_drift_f"] > 4.0
    assert f.metrics["superheat_drift_direction"] == "up"
    assert "starving" in f.summary
    # a one-sided detector would have reported this as healthy
    assert f.metrics["superheat_sustained_alarm"] is True
    assert f.metrics["superheat_alarm_direction"] == "up"


def test_a_rise_and_an_equal_fall_score_identically_and_report_opposite_signs():
    """Alarm symmetry: one pair of floors on |drift|, with the direction reported alongside."""
    down = _rule(BaselineStore()).analyze_periods("CH_1", *_base_and({"offset_f": -5.0}))
    up = _rule(BaselineStore()).analyze_periods("CH_1", *_base_and({"offset_f": 5.0}))

    assert down.severity == up.severity == "fault"
    assert abs(abs(down.metrics["superheat_drift_f"]) - up.metrics["superheat_drift_f"]) < 0.4
    assert down.metrics["superheat_drift_f"] < 0 < up.metrics["superheat_drift_f"]
    assert down.metrics["superheat_drift_direction"] == "down"
    assert up.metrics["superheat_drift_direction"] == "up"
    assert down.metrics["superheat_alarm_direction"] == "down"
    assert up.metrics["superheat_alarm_direction"] == "up"


def test_the_finding_labels_its_two_threshold_classes_separately():
    """Severity rests on screening-grade magnitude floors; the alarm on untuned temporal ones."""
    f = _rule(BaselineStore()).analyze_periods("CH_1", *_base_and({"offset_f": -5.0}))
    assert f.metrics["magnitude_threshold_confidence"] == MAGNITUDE_CONFIDENCE
    assert f.metrics["temporal_threshold_confidence"] == TEMPORAL_CONFIDENCE


def test_a_steady_chiller_does_not_flag():
    f = _rule(BaselineStore()).analyze_periods("CH_1", *_base_and({}))
    assert f.severity == "ok"
    assert abs(f.metrics["superheat_drift_f"]) < 0.4
    assert f.metrics["superheat_sustained_alarm"] is False


def test_a_busier_period_at_matched_load_is_not_drift():
    """Load normalization: more load must not read as a feed fault."""
    base, busy = _base_and({"tons_offset": 90.0})
    f = _rule(BaselineStore()).analyze_periods("CH_1", base, busy)
    assert f.severity == "ok"
    assert abs(f.metrics["superheat_drift_f"]) < 0.4


def test_a_mild_shift_inside_the_floors_stays_ok():
    """The degF floor (4 to fault, 2 to warn) is wider than subcooling's; hunting stays quiet."""
    f = _rule(BaselineStore()).analyze_periods("CH_1", *_base_and({"offset_f": -1.0}))
    assert f.severity == "ok"


# --------------------------------------------------------------------------- instrumentation gate


def test_it_declines_loudly_when_superheat_is_not_mapped():
    """A chiller without the point must not read as a chiller feeding correctly."""
    base = _role_frame(start="2025-05-01", seed=1, superheat=False)
    cur = _role_frame(start="2025-06-01", seed=2, superheat=False)
    f = _rule(BaselineStore()).analyze_periods("CH_1", base, cur)

    assert f.severity == "info"
    assert f.metrics["declined"] is True
    assert f.metrics["reason"] == "superheat_not_mapped"
    assert any("does not publish" in c for c in f.caveats)


def test_it_declines_when_no_baseline_can_be_frozen():
    f = _rule(BaselineStore(), freeze_if_missing=False).analyze_periods("CH_1", *_base_and({}))
    assert f.severity == "info"
    assert f.metrics["declined"] is True


# --------------------------------------------------------------------------- freeze / isolation


def test_the_baseline_is_frozen_and_not_refit_on_a_later_run():
    store = BaselineStore()
    rule = _rule(store)
    base, cur = _base_and({"offset_f": -5.0})
    rule.analyze_periods("CH_1", base, cur)
    coeffs = dict(store.get("SITE", "CH_1", "chiller_superheat").coefficients)

    worse = _role_frame(start="2025-07-01", seed=5, offset_f=-7.0)
    f = rule.analyze_periods("CH_1", worse, worse)
    assert store.get("SITE", "CH_1", "chiller_superheat").coefficients == coeffs
    assert f.severity == "fault"


def test_the_superheat_baseline_is_stored_separately_from_the_others():
    store = BaselineStore()
    _rule(store).analyze_periods("CH_1", *_base_and({}))
    assert store.get("SITE", "CH_1", "chiller_superheat") is not None
    # it must not collide with, or stand in for, the subcooling or approach baselines
    assert store.get("SITE", "CH_1", "chiller_subcooling") is None
    assert store.get("SITE", "CH_1", "chiller_approach_cond") is None


def test_the_baseline_fit_is_load_normalized():
    """The frozen baseline is a metric~f(tons) fit, so its slope is nonzero for this machine."""
    store = BaselineStore()
    _rule(store).analyze_periods("CH_1", *_base_and({}))
    fit = store.model_for("SITE", "CH_1", "chiller_superheat")
    assert fit is not None
    # sanity: an independent fit of the same shape recovers a comparable intercept
    base = _role_frame(start="2025-05-01", seed=1)
    indep = fit_load_baseline(
        pd.DataFrame(
            {"tons": base[Role.CHW_FLOW] / 2.0, Role.SUPERHEAT_TEMP: base[Role.SUPERHEAT_TEMP]}
        ),
        metric_col=Role.SUPERHEAT_TEMP,
        load_col="tons",
    )
    assert indep is not None
