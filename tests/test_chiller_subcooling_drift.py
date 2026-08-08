"""Tests for liquid-line subcooling drift (camber.rules.chiller_subcooling_rule).

All data here is synthetic: a linear subcooling-vs-load relationship plus Gaussian noise, with
faults injected as explicit offsets. Nothing is drawn from any measured dataset.
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.chillerbaseline import fit_load_baseline, fit_subcooling_baseline  # noqa: E402
from camber.chillerdrift import ApproachDriftMonitor  # noqa: E402
from camber.driftthresholds import MAGNITUDE_CONFIDENCE, TEMPORAL_CONFIDENCE  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.base import PeriodRule  # noqa: E402
from camber.rules.builtin import builtin_registry, rule_names  # noqa: E402
from camber.rules.chiller_subcooling_rule import ChillerSubcoolingDrift  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402

# A synthetic machine: ~5 degF subcooling unloaded, widening slightly with load.
_INTERCEPT_F = 5.0
_SLOPE_F_PER_TON = 0.009
_SIGMA_F = 0.3


def _tons(n, seed=0, offset=0.0):
    rng = np.random.default_rng(seed)
    h = np.arange(n)
    t = 170 + 120 * np.sin((h % 24 - 8) / 24 * 2 * np.pi) + rng.normal(0, 12, n)
    return np.clip(t + offset, 40.0, 400.0)


def _role_frame(
    n=24 * 30, *, start="2025-05-01", seed=0, offset_f=0.0, tons_offset=0.0, subcooling=True
):
    """A chiller role-frame; ``offset_f`` shifts subcooling (negative = undercharge)."""
    rng = np.random.default_rng(seed + 100)
    idx = pd.date_range(start, periods=n, freq="1h")
    tons = _tons(n, seed=seed, offset=tons_offset)
    cols = {
        Role.CHW_FLOW: tons * 2.0,  # tons = gpm * dT / 24 with dT = 12
        Role.CHW_SUPPLY_TEMP: np.full(n, 44.0),
        Role.CHW_RETURN_TEMP: np.full(n, 56.0),
    }
    if subcooling:
        cols[Role.SUBCOOLING_TEMP] = (
            _INTERCEPT_F + _SLOPE_F_PER_TON * tons + offset_f + rng.normal(0, _SIGMA_F, n)
        )
    return pd.DataFrame(cols, index=idx)


def _rule(store, **kw):
    return ChillerSubcoolingDrift(store, site="SITE", run_id="2025-07-01T00:00", **kw)


def _base_and(current_kw):
    return _role_frame(start="2025-05-01", seed=1), _role_frame(
        start="2025-06-01", seed=2, **current_kw
    )


# --------------------------------------------------------------------------- interface


def test_it_is_a_period_rule_and_is_not_auto_registered():
    rule = _rule(BaselineStore())
    assert isinstance(rule, PeriodRule)
    assert "chiller_subcooling_drift" not in rule_names()
    assert "chiller_subcooling_drift" not in builtin_registry().names()


def test_the_new_role_exists_and_is_optional_on_this_rule():
    assert Role.SUBCOOLING_TEMP.value == "subcooling_temp"
    rule = _rule(BaselineStore())
    assert Role.SUBCOOLING_TEMP in rule.roles_optional
    assert Role.SUBCOOLING_TEMP not in rule.roles_required


# --------------------------------------------------------------------------- the detector


def test_falling_subcooling_flags_undercharge():
    """Subcooling collapsing at matched load is the undercharge/leak signature."""
    store = BaselineStore()
    base, cur = _base_and({"offset_f": -2.5})
    f = _rule(store).analyze_periods("CH_1", base, cur)

    assert f.rule == "chiller_subcooling_drift"
    assert f.severity == "fault"
    assert f.metrics["subcooling_drift_f"] < -2.0
    assert f.metrics["subcooling_drift_direction"] == "down"
    assert abs(f.metrics["subcooling_drift_sigma"]) > 6.0
    assert f.metrics["thresholds_provisional"] is True


def test_rising_subcooling_also_flags():
    """The other half of the fault space: liquid backing up in the condenser."""
    store = BaselineStore()
    base, cur = _base_and({"offset_f": 2.5})
    f = _rule(store).analyze_periods("CH_1", base, cur)

    assert f.severity == "fault"
    assert f.metrics["subcooling_drift_f"] > 2.0
    assert f.metrics["subcooling_drift_direction"] == "up"
    # a one-sided detector would have reported this as healthy
    assert f.metrics["subcooling_sustained_alarm"] is True
    assert f.metrics["subcooling_alarm_direction"] == "up"


def test_a_rise_and_an_equal_fall_score_identically_and_report_opposite_signs():
    """Alarm symmetry: one pair of floors on |drift|, with the direction reported alongside.

    Neither half of the charge fault space is known to deserve a tighter floor than the other, so
    the magnitude scoring is symmetric and only the *sign* distinguishes them. If per-direction
    floors are ever introduced, this test is the one that has to change deliberately.
    """
    down = _rule(BaselineStore()).analyze_periods("CH_1", *_base_and({"offset_f": -2.5}))
    up = _rule(BaselineStore()).analyze_periods("CH_1", *_base_and({"offset_f": 2.5}))

    # identical in magnitude, in degF and in sigma
    assert down.severity == up.severity == "fault"
    assert abs(abs(down.metrics["subcooling_drift_f"]) - up.metrics["subcooling_drift_f"]) < 0.3
    down_sigma = abs(down.metrics["subcooling_drift_sigma"])
    assert abs(down_sigma - up.metrics["subcooling_drift_sigma"]) < 1.0

    # opposite in sign, and the sign is reported both by the period statistic and by the CUSUM
    assert down.metrics["subcooling_drift_f"] < 0 < up.metrics["subcooling_drift_f"]
    assert down.metrics["subcooling_drift_direction"] == "down"
    assert up.metrics["subcooling_drift_direction"] == "up"
    assert down.metrics["subcooling_sustained_alarm"] is up.metrics["subcooling_sustained_alarm"]
    assert down.metrics["subcooling_alarm_direction"] == "down"
    assert up.metrics["subcooling_alarm_direction"] == "up"


def test_the_finding_labels_its_two_threshold_classes_separately():
    """Severity rests on screening-grade magnitude floors; the alarm on untuned temporal ones."""
    f = _rule(BaselineStore()).analyze_periods("CH_1", *_base_and({"offset_f": -2.5}))
    assert f.metrics["thresholds_provisional"] is True
    assert f.metrics["magnitude_threshold_confidence"] == MAGNITUDE_CONFIDENCE
    assert f.metrics["temporal_threshold_confidence"] == TEMPORAL_CONFIDENCE


def test_a_steady_chiller_does_not_flag():
    f = _rule(BaselineStore()).analyze_periods("CH_1", *_base_and({}))
    assert f.severity == "ok"
    assert abs(f.metrics["subcooling_drift_f"]) < 0.3
    assert f.metrics["subcooling_sustained_alarm"] is False


def test_a_busier_period_at_matched_load_is_not_drift():
    """Load normalization: more load must not read as a charge fault."""
    base, busy = _base_and({"tons_offset": 90.0})
    raw = float(busy[Role.SUBCOOLING_TEMP].median()) - float(base[Role.SUBCOOLING_TEMP].median())
    assert raw > 0.5  # a level-vs-level comparison would see a rise

    f = _rule(BaselineStore()).analyze_periods("CH_1", base, busy)
    assert f.severity == "ok"
    assert abs(f.metrics["subcooling_drift_f"]) < 0.3
    assert f.metrics["subcooling_sustained_alarm"] is False


def test_a_mild_shift_inside_the_floors_stays_ok():
    """Both a degF and a sigma floor must be cleared, so small moves stay quiet."""
    f = _rule(BaselineStore()).analyze_periods("CH_1", *_base_and({"offset_f": -0.5}))
    assert f.severity == "ok"


# --------------------------------------------------------------------------- instrumentation gate


def test_it_declines_loudly_when_subcooling_is_not_mapped():
    """A chiller without the point must not read as a chiller with good charge."""
    base = _role_frame(start="2025-05-01", seed=1, subcooling=False)
    cur = _role_frame(start="2025-06-01", seed=2, subcooling=False)
    f = _rule(BaselineStore()).analyze_periods("CH_1", base, cur)

    assert f.severity == "info"
    assert f.metrics["declined"] is True
    assert f.metrics["reason"] == "subcooling_not_mapped"
    assert any("does not publish" in c for c in f.caveats)


def test_it_declines_when_no_baseline_can_be_frozen():
    f = _rule(BaselineStore(), freeze_if_missing=False).analyze_periods("CH_1", *_base_and({}))
    assert f.severity == "info"
    assert f.metrics["declined"] is True


# --------------------------------------------------------------------------- freeze / accept


def test_the_baseline_is_frozen_and_not_refit_on_a_later_run():
    store = BaselineStore()
    rule = _rule(store)
    base, cur = _base_and({"offset_f": -2.5})
    rule.analyze_periods("CH_1", base, cur)
    coeffs = dict(store.get("SITE", "CH_1", "chiller_subcooling").coefficients)

    worse = _role_frame(start="2025-07-01", seed=5, offset_f=-4.0)
    f = rule.analyze_periods("CH_1", worse, worse)
    assert store.get("SITE", "CH_1", "chiller_subcooling").coefficients == coeffs
    assert f.severity == "fault"


def test_accept_new_normal_resets_the_reference_and_silences_the_drift():
    store = BaselineStore()
    rule = _rule(store)
    base, drifted = _base_and({"offset_f": -2.5})
    assert rule.analyze_periods("CH_1", base, drifted).severity == "fault"

    refit = fit_subcooling_baseline(
        pd.DataFrame(
            {
                "tons": drifted[Role.CHW_FLOW] / 2.0,
                Role.SUBCOOLING_TEMP: drifted[Role.SUBCOOLING_TEMP],
            }
        )
    )
    rec = store.accept_new_normal(
        refit,
        site="SITE",
        equip="CH_1",
        kind="chiller_subcooling",
        accepted_by="plant.operator",
        reason="charge corrected and verified; post-service performance accepted",
        at="2025-06-30T09:00",
    )
    assert rec.accepted_by == "plant.operator" and len(rec.history) == 1

    after = rule.analyze_periods("CH_1", base, drifted)
    assert after.severity == "ok"
    assert after.metrics["subcooling_sustained_alarm"] is False


def test_the_subcooling_baseline_is_stored_separately_from_the_approach_one():
    store = BaselineStore()
    _rule(store).analyze_periods("CH_1", *_base_and({}))
    assert store.get("SITE", "CH_1", "chiller_subcooling") is not None
    # it must not collide with, or stand in for, the condenser-approach baseline
    assert store.get("SITE", "CH_1", "chiller_approach_cond") is None


# --------------------------------------------------------------------------- two-sided monitor


def test_the_monitor_default_stays_one_sided():
    """Approach detectors must keep their exact behaviour: only a rising signal alarms."""
    n = 24 * 20
    frame = pd.DataFrame(
        {
            "tons": _tons(n, seed=2),
            "approach_f": _INTERCEPT_F
            + _SLOPE_F_PER_TON * _tons(n, seed=2)
            - 4.0 * _SIGMA_F  # a sustained *fall*
            + np.random.default_rng(3).normal(0, _SIGMA_F, n),
        },
        index=pd.date_range("2025-06-01", periods=n, freq="1h"),
    )
    baseline = fit_load_baseline(
        metric_col="approach_f",
        frame=pd.DataFrame(
            {
                "tons": _tons(n, seed=1),
                "approach_f": _INTERCEPT_F
                + _SLOPE_F_PER_TON * _tons(n, seed=1)
                + np.random.default_rng(4).normal(0, _SIGMA_F, n),
            },
            index=pd.date_range("2025-05-01", periods=n, freq="1h"),
        ),
    )
    assert baseline is not None
    assert not ApproachDriftMonitor(baseline).run(frame).alarmed  # default: one-sided
    two_sided = ApproachDriftMonitor(baseline, direction="both").run(frame)
    assert two_sided.alarmed and two_sided.alarm_direction == "down"
