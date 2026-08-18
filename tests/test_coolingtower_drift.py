"""Tests for cooling-tower approach drift (camber.rules.coolingtower_drift_rule) and the
`tower_approach_f` metric.

All data is synthetic: a linear approach-vs-load relationship plus Gaussian noise, with faults
injected as explicit offsets. Nothing is drawn from any measured dataset.
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.coolingtower import stull_wetbulb_f, tower_approach_f  # noqa: E402
from camber.driftthresholds import MAGNITUDE_CONFIDENCE, TEMPORAL_CONFIDENCE  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.base import PeriodRule  # noqa: E402
from camber.rules.builtin import builtin_registry, rule_names  # noqa: E402
from camber.rules.coolingtower_drift_rule import CoolingTowerApproachDrift  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402

# A synthetic tower: ~5 degF approach at low load, widening slightly with load.
_INTERCEPT_F = 5.0
_SLOPE_F_PER_TON = 0.005
_SIGMA_F = 0.4


def _tons(n, seed=0, offset=0.0):
    rng = np.random.default_rng(seed)
    h = np.arange(n)
    t = 170 + 120 * np.sin((h % 24 - 8) / 24 * 2 * np.pi) + rng.normal(0, 12, n)
    return np.clip(t + offset, 40.0, 400.0)


def _role_frame(
    n=24 * 30, *, start="2025-05-01", seed=0, approach_off=0.0, tons_offset=0.0, wetbulb="measured"
):
    """A tower role-frame; ``approach_off`` shifts the approach (positive = fouling/widening)."""
    rng = np.random.default_rng(seed + 100)
    idx = pd.date_range(start, periods=n, freq="1h")
    tons = _tons(n, seed=seed, offset=tons_offset)
    oat = 85.0 + rng.normal(0, 3, n)
    rh = np.full(n, 55.0)
    wb = stull_wetbulb_f(oat, rh) if wetbulb == "derived" else 70.0 + rng.normal(0, 0.5, n)
    approach = _INTERCEPT_F + _SLOPE_F_PER_TON * tons + approach_off + rng.normal(0, _SIGMA_F, n)
    cols = {
        Role.CHW_FLOW: tons * 2.0,  # tons = gpm * dT / 24 with dT = 12
        Role.CHW_SUPPLY_TEMP: np.full(n, 44.0),
        Role.CHW_RETURN_TEMP: np.full(n, 56.0),
        Role.CW_SUPPLY_TEMP: wb + approach,
    }
    if wetbulb == "measured":
        cols[Role.WETBULB_TEMP] = wb
    elif wetbulb == "derived":
        cols[Role.OAT] = oat
        cols[Role.OUTDOOR_RH] = rh
    return pd.DataFrame(cols, index=idx)


def _rule(store, **kw):
    return CoolingTowerApproachDrift(store, site="SITE", run_id="2025-07-01T00:00", **kw)


def _base_and(current_kw):
    return _role_frame(start="2025-05-01", seed=1), _role_frame(
        start="2025-06-01", seed=2, **current_kw
    )


# --------------------------------------------------------------------------- metric helper


def test_tower_approach_f_measured_and_derived():
    df = pd.DataFrame({"CWS_Temp": [77.0, 78.0], "WetBulb": [70.0, 70.0]})
    assert list(tower_approach_f(df)) == [7.0, 8.0]
    # derived path: matches CWS - stull(OAT, RH)
    df2 = pd.DataFrame({"CWS_Temp": [80.0], "OAT": [85.0], "RH": [55.0]})
    expected = 80.0 - stull_wetbulb_f(np.array([85.0]), np.array([55.0]))[0]
    assert tower_approach_f(df2).iloc[0] == pytest.approx(expected)


def test_tower_approach_f_needs_a_wetbulb_source():
    with pytest.raises(KeyError):
        tower_approach_f(pd.DataFrame({"CWS_Temp": [77.0]}))  # no wet-bulb and no OAT/RH
    with pytest.raises(KeyError):
        tower_approach_f(pd.DataFrame({"WetBulb": [70.0]}))  # no supply temp


# --------------------------------------------------------------------------- interface


def test_it_is_a_period_rule_and_is_not_auto_registered():
    rule = _rule(BaselineStore())
    assert isinstance(rule, PeriodRule)
    assert "cooling_tower_approach_drift" not in rule_names()
    assert "cooling_tower_approach_drift" not in builtin_registry().names()


# --------------------------------------------------------------------------- the detector


def test_a_widening_approach_flags_fouling():
    f = _rule(BaselineStore()).analyze_periods("CT_1", *_base_and({"approach_off": 4.0}))
    assert f.rule == "cooling_tower_approach_drift"
    assert f.severity == "fault"
    assert f.metrics["tower_approach_drift_f"] > 3.0
    assert f.metrics["tower_approach_drift_direction"] == "up"
    assert f.metrics["tower_approach_sustained_alarm"] is True
    assert "widened" in f.summary


def test_it_is_one_sided_a_narrowing_is_not_a_fault():
    """Fouling only ever widens an approach; a narrowing (better-than-baseline) stays ok."""
    f = _rule(BaselineStore()).analyze_periods("CT_1", *_base_and({"approach_off": -4.0}))
    assert f.severity == "ok"
    assert f.metrics["tower_approach_drift_f"] < -3.0  # it did move, just not the fault direction


def test_a_steady_tower_does_not_flag():
    f = _rule(BaselineStore()).analyze_periods("CT_1", *_base_and({}))
    assert f.severity == "ok"
    assert abs(f.metrics["tower_approach_drift_f"]) < 0.4
    assert f.metrics["tower_approach_sustained_alarm"] is False


def test_a_busier_period_at_matched_load_is_not_drift():
    """Load normalization: more heat rejection must not read as tower degradation."""
    f = _rule(BaselineStore()).analyze_periods("CT_1", *_base_and({"tons_offset": 90.0}))
    assert f.severity == "ok"
    assert abs(f.metrics["tower_approach_drift_f"]) < 0.4


def test_the_derived_wetbulb_path_works():
    """With no measured wet-bulb, the approach is derived from OAT + RH via Stull."""
    base = _role_frame(start="2025-05-01", seed=1, wetbulb="derived")
    cur = _role_frame(start="2025-06-01", seed=2, approach_off=4.0, wetbulb="derived")
    f = _rule(BaselineStore()).analyze_periods("CT_1", base, cur)
    assert f.severity == "fault" and f.metrics["tower_approach_drift_f"] > 3.0


def test_the_finding_labels_its_two_threshold_classes():
    f = _rule(BaselineStore()).analyze_periods("CT_1", *_base_and({"approach_off": 4.0}))
    assert f.metrics["magnitude_threshold_confidence"] == MAGNITUDE_CONFIDENCE
    assert f.metrics["temporal_threshold_confidence"] == TEMPORAL_CONFIDENCE


# --------------------------------------------------------------------------- declines


def test_it_declines_when_no_approach_is_available():
    # a frame with the chiller side but no CW supply temp and no wet-bulb source
    base = _role_frame(start="2025-05-01", seed=1).drop(
        columns=[Role.CW_SUPPLY_TEMP, Role.WETBULB_TEMP]
    )
    cur = _role_frame(start="2025-06-01", seed=2).drop(
        columns=[Role.CW_SUPPLY_TEMP, Role.WETBULB_TEMP]
    )
    f = _rule(BaselineStore()).analyze_periods("CT_1", base, cur)
    assert f.severity == "info"
    assert f.metrics["declined"] is True
    assert f.metrics["reason"] == "tower_approach_not_available"


def test_it_declines_when_no_baseline_can_be_frozen():
    f = _rule(BaselineStore(), freeze_if_missing=False).analyze_periods("CT_1", *_base_and({}))
    assert f.severity == "info" and f.metrics["declined"] is True


# --------------------------------------------------------------------------- freeze / isolation


def test_the_baseline_is_frozen_and_not_refit_on_a_later_run():
    store = BaselineStore()
    rule = _rule(store)
    rule.analyze_periods("CT_1", *_base_and({"approach_off": 4.0}))
    coeffs = dict(store.get("SITE", "CT_1", "cooling_tower_approach").coefficients)
    worse = _role_frame(start="2025-07-01", seed=5, approach_off=6.0)
    f = rule.analyze_periods("CT_1", worse, worse)
    assert store.get("SITE", "CT_1", "cooling_tower_approach").coefficients == coeffs
    assert f.severity == "fault"


def test_the_baseline_is_stored_under_its_own_kind():
    store = BaselineStore()
    _rule(store).analyze_periods("CT_1", *_base_and({}))
    assert store.get("SITE", "CT_1", "cooling_tower_approach") is not None
    assert store.get("SITE", "CT_1", "chiller_cw_range") is None  # no collision
