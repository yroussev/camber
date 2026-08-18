"""Tests for suction- / evaporating-pressure drift (camber.rules.chiller_suction_pressure_rule).

All data is synthetic: a linear suction-pressure-vs-load relationship plus Gaussian noise, with
faults injected as explicit psi offsets and the chilled-water-reset confound as a CHW-supply
offset. Nothing is drawn from any measured dataset.
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.driftthresholds import MAGNITUDE_CONFIDENCE, TEMPORAL_CONFIDENCE  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.base import PeriodRule  # noqa: E402
from camber.rules.builtin import builtin_registry, rule_names  # noqa: E402
from camber.rules.chiller_suction_pressure_rule import ChillerSuctionPressureDrift  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402

# A synthetic low side: ~62 psig suction unloaded, climbing gently with load; 1.5 psi run-to-run.
_INTERCEPT_PSI = 62.0
_SLOPE_PSI_PER_TON = 0.03
_SIGMA_PSI = 1.5


def _tons(n, seed=0, offset=0.0):
    rng = np.random.default_rng(seed)
    h = np.arange(n)
    t = 170 + 120 * np.sin((h % 24 - 8) / 24 * 2 * np.pi) + rng.normal(0, 12, n)
    return np.clip(t + offset, 40.0, 400.0)


def _role_frame(
    n=24 * 30,
    *,
    start="2025-05-01",
    seed=0,
    offset_psi=0.0,
    tons_offset=0.0,
    suction=True,
    chw_offset_f=0.0,
):
    """A chiller role-frame; ``offset_psi`` shifts suction pressure (negative = a low-side fall)."""
    rng = np.random.default_rng(seed + 100)
    idx = pd.date_range(start, periods=n, freq="1h")
    tons = _tons(n, seed=seed, offset=tons_offset)
    cols = {
        Role.CHW_FLOW: tons * 2.0,  # tons = gpm * dT / 24 with dT = 12
        Role.CHW_SUPPLY_TEMP: np.full(n, 44.0 + chw_offset_f),
        Role.CHW_RETURN_TEMP: np.full(n, 56.0),
    }
    if suction:
        cols[Role.SUCTION_PRESSURE] = (
            _INTERCEPT_PSI + _SLOPE_PSI_PER_TON * tons + offset_psi + rng.normal(0, _SIGMA_PSI, n)
        )
    return pd.DataFrame(cols, index=idx)


def _rule(store, **kw):
    return ChillerSuctionPressureDrift(store, site="SITE", run_id="2025-07-01T00:00", **kw)


def _base_and(current_kw):
    return _role_frame(start="2025-05-01", seed=1), _role_frame(
        start="2025-06-01", seed=2, **current_kw
    )


# --------------------------------------------------------------------------- interface


def test_it_is_a_period_rule_and_is_not_auto_registered():
    rule = _rule(BaselineStore())
    assert isinstance(rule, PeriodRule)
    assert "chiller_suction_pressure_drift" not in rule_names()
    assert "chiller_suction_pressure_drift" not in builtin_registry().names()


def test_suction_pressure_is_optional_on_this_rule():
    assert Role.SUCTION_PRESSURE.value == "suction_pressure"
    rule = _rule(BaselineStore())
    assert Role.SUCTION_PRESSURE in rule.roles_optional
    assert Role.SUCTION_PRESSURE not in rule.roles_required


# --------------------------------------------------------------------------- the detector


def test_falling_suction_flags_evaporator_degradation():
    """Suction collapsing at matched load is the heat-transfer-loss / low-charge signature."""
    store = BaselineStore()
    base, cur = _base_and({"offset_psi": -8.0})
    f = _rule(store).analyze_periods("CH_1", base, cur)

    assert f.rule == "chiller_suction_pressure_drift"
    assert f.severity == "fault"
    assert f.metrics["suction_pressure_drift_psi"] < -4.0
    assert f.metrics["suction_pressure_drift_direction"] == "down"
    assert abs(f.metrics["suction_pressure_drift_sigma"]) > 4.0
    assert f.metrics["suction_pressure_sustained_alarm"] is True
    assert f.metrics["suction_pressure_alarm_direction"] == "down"
    assert f.metrics["thresholds_provisional"] is True


def test_rising_suction_also_flags():
    """The other half of the fault space: the evaporator overfed / flooding."""
    store = BaselineStore()
    base, cur = _base_and({"offset_psi": 8.0})
    f = _rule(store).analyze_periods("CH_1", base, cur)

    assert f.severity == "fault"
    assert f.metrics["suction_pressure_drift_psi"] > 4.0
    assert f.metrics["suction_pressure_drift_direction"] == "up"
    assert f.metrics["suction_pressure_alarm_direction"] == "up"


def test_a_fall_and_an_equal_rise_score_identically_and_report_opposite_signs():
    """Two-sided symmetry: one pair of floors on |drift|, with the direction reported alongside."""
    down = _rule(BaselineStore()).analyze_periods("CH_1", *_base_and({"offset_psi": -8.0}))
    up = _rule(BaselineStore()).analyze_periods("CH_1", *_base_and({"offset_psi": 8.0}))

    assert down.severity == up.severity == "fault"
    assert (
        abs(
            abs(down.metrics["suction_pressure_drift_psi"])
            - up.metrics["suction_pressure_drift_psi"]
        )
        < 0.6
    )
    assert down.metrics["suction_pressure_drift_psi"] < 0 < up.metrics["suction_pressure_drift_psi"]
    assert down.metrics["suction_pressure_drift_direction"] == "down"
    assert up.metrics["suction_pressure_drift_direction"] == "up"


def test_a_steady_chiller_does_not_flag():
    f = _rule(BaselineStore()).analyze_periods("CH_1", *_base_and({}))
    assert f.severity == "ok"
    assert abs(f.metrics["suction_pressure_drift_psi"]) < 1.5
    assert f.metrics["suction_pressure_sustained_alarm"] is False


def test_a_busier_period_at_matched_load_is_not_drift():
    """Load normalization: more tons (and so higher raw suction) must not read as a fault."""
    base, busy = _base_and({"tons_offset": 90.0})
    f = _rule(BaselineStore()).analyze_periods("CH_1", base, busy)
    assert f.severity == "ok"
    assert abs(f.metrics["suction_pressure_drift_psi"]) < 1.5


def test_the_finding_labels_its_two_threshold_classes_separately():
    f = _rule(BaselineStore()).analyze_periods("CH_1", *_base_and({"offset_psi": -8.0}))
    assert f.metrics["thresholds_provisional"] is True
    assert f.metrics["magnitude_threshold_confidence"] == MAGNITUDE_CONFIDENCE
    assert f.metrics["temporal_threshold_confidence"] == TEMPORAL_CONFIDENCE


# --------------------------------------------------------------------------- the confound


def test_a_co_moving_chw_reset_is_caveated():
    """Suction up with a co-moving CHW-supply lift -> flag the chilled-water-reset confound."""
    store = BaselineStore()
    base = _role_frame(start="2025-05-01", seed=1)
    cur = _role_frame(start="2025-06-01", seed=2, offset_psi=8.0, chw_offset_f=3.0)
    f = _rule(store).analyze_periods("CH_1", base, cur)

    assert f.severity == "fault"  # the move is still ranked
    assert f.metrics["chw_supply_shift_f"] > 2.0
    assert any("chilled-water-reset" in c for c in f.caveats)


def test_a_flat_chw_supply_does_not_raise_the_confound_caveat():
    store = BaselineStore()
    f = _rule(store).analyze_periods("CH_1", *_base_and({"offset_psi": -8.0}))
    assert f.severity == "fault"
    assert abs(f.metrics["chw_supply_shift_f"]) < 1.0
    assert not any("chilled-water-reset" in c for c in f.caveats)


# --------------------------------------------------------------------------- instrumentation gate


def test_it_declines_loudly_when_suction_pressure_is_not_mapped():
    base = _role_frame(start="2025-05-01", seed=1, suction=False)
    cur = _role_frame(start="2025-06-01", seed=2, suction=False)
    f = _rule(BaselineStore()).analyze_periods("CH_1", base, cur)

    assert f.severity == "info"
    assert f.metrics["declined"] is True
    assert f.metrics["reason"] == "suction_pressure_not_mapped"
    assert any("does not publish" in c for c in f.caveats)


def test_it_declines_when_no_baseline_can_be_frozen():
    f = _rule(BaselineStore(), freeze_if_missing=False).analyze_periods("CH_1", *_base_and({}))
    assert f.severity == "info"
    assert f.metrics["declined"] is True


# --------------------------------------------------------------------------- freeze / storage


def test_the_baseline_is_frozen_and_not_refit_on_a_later_run():
    store = BaselineStore()
    rule = _rule(store)
    base, cur = _base_and({"offset_psi": -8.0})
    rule.analyze_periods("CH_1", base, cur)
    coeffs = dict(store.get("SITE", "CH_1", "chiller_suction_pressure").coefficients)

    worse = _role_frame(start="2025-07-01", seed=5, offset_psi=-14.0)
    f = rule.analyze_periods("CH_1", worse, worse)
    assert store.get("SITE", "CH_1", "chiller_suction_pressure").coefficients == coeffs
    assert f.severity == "fault"


def test_the_suction_pressure_baseline_is_stored_separately():
    store = BaselineStore()
    _rule(store).analyze_periods("CH_1", *_base_and({}))
    assert store.get("SITE", "CH_1", "chiller_suction_pressure") is not None
    # it must not collide with the head-pressure or approach baselines
    assert store.get("SITE", "CH_1", "chiller_head_pressure") is None
    assert store.get("SITE", "CH_1", "chiller_approach_evap") is None
