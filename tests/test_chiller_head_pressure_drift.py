"""Tests for head- / condensing-pressure drift (camber.rules.chiller_head_pressure_rule).

All data is synthetic: a linear head-pressure-vs-load relationship plus Gaussian noise, with faults
injected as explicit psi offsets and the CW-temperature confound injected as a CW-supply offset.
Nothing is drawn from any measured dataset.
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.driftthresholds import MAGNITUDE_CONFIDENCE, TEMPORAL_CONFIDENCE  # noqa: E402
from camber.interop.semantic223 import role_223_quantity  # noqa: E402
from camber.model.roles import HAYSTACK_HINT, Role  # noqa: E402
from camber.rules.base import PeriodRule  # noqa: E402
from camber.rules.builtin import builtin_registry, rule_names  # noqa: E402
from camber.rules.chiller_head_pressure_rule import ChillerHeadPressureDrift  # noqa: E402
from camber.sensorhealth import PHYSICAL_BOUNDS  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402

# A synthetic water-cooled machine: ~120 psig head unloaded, climbing with load; 2 psi run-to-run.
_INTERCEPT_PSI = 120.0
_SLOPE_PSI_PER_TON = 0.15
_SIGMA_PSI = 2.0


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
    discharge=True,
    cw_supply=False,
    cw_offset_f=0.0,
    suction=False,
    suction_offset_psi=0.0,
):
    """A chiller role-frame; ``offset_psi`` shifts head pressure (positive = a high-side rise)."""
    rng = np.random.default_rng(seed + 100)
    idx = pd.date_range(start, periods=n, freq="1h")
    tons = _tons(n, seed=seed, offset=tons_offset)
    cols = {
        Role.CHW_FLOW: tons * 2.0,  # tons = gpm * dT / 24 with dT = 12
        Role.CHW_SUPPLY_TEMP: np.full(n, 44.0),
        Role.CHW_RETURN_TEMP: np.full(n, 56.0),
    }
    if discharge:
        cols[Role.DISCHARGE_PRESSURE] = (
            _INTERCEPT_PSI + _SLOPE_PSI_PER_TON * tons + offset_psi + rng.normal(0, _SIGMA_PSI, n)
        )
    if cw_supply:
        cols[Role.CW_SUPPLY_TEMP] = 85.0 + cw_offset_f + rng.normal(0, 0.5, n)
    if suction:
        cols[Role.SUCTION_PRESSURE] = 60.0 + suction_offset_psi + rng.normal(0, 1.0, n)
    return pd.DataFrame(cols, index=idx)


def _rule(store, **kw):
    return ChillerHeadPressureDrift(store, site="SITE", run_id="2025-07-01T00:00", **kw)


def _base_and(current_kw, base_kw=None):
    base = _role_frame(start="2025-05-01", seed=1, **(base_kw or {}))
    cur = _role_frame(start="2025-06-01", seed=2, **current_kw)
    return base, cur


# --------------------------------------------------------------------------- role wiring


def test_the_new_pressure_roles_are_fully_wired():
    """Slugs are stable, and each pressure role has a hint, a bound, and a 223P quantity/medium."""
    assert Role.DISCHARGE_PRESSURE.value == "discharge_pressure"
    assert Role.SUCTION_PRESSURE.value == "suction_pressure"
    assert Role("discharge_pressure") is Role.DISCHARGE_PRESSURE
    for r in (Role.DISCHARGE_PRESSURE, Role.SUCTION_PRESSURE):
        assert r in HAYSTACK_HINT
        assert r in PHYSICAL_BOUNDS and PHYSICAL_BOUNDS[r][0] < PHYSICAL_BOUNDS[r][1]
        assert role_223_quantity(r) == ("Pressure", "Refrigerant")


# --------------------------------------------------------------------------- interface


def test_it_is_a_period_rule_and_is_not_auto_registered():
    rule = _rule(BaselineStore())
    assert isinstance(rule, PeriodRule)
    assert "chiller_head_pressure_drift" not in rule_names()
    assert "chiller_head_pressure_drift" not in builtin_registry().names()


def test_discharge_pressure_is_required_and_the_confound_roles_are_optional():
    rule = _rule(BaselineStore())
    assert Role.DISCHARGE_PRESSURE in rule.roles_required
    assert Role.CW_SUPPLY_TEMP in rule.roles_optional
    assert Role.SUCTION_PRESSURE in rule.roles_optional


# --------------------------------------------------------------------------- the detector


def test_rising_head_pressure_flags():
    """Head pressure climbing at matched load is the fouling / non-condensables signature."""
    store = BaselineStore()
    base, cur = _base_and({"offset_psi": 10.0})
    f = _rule(store).analyze_periods("CH_1", base, cur)

    assert f.rule == "chiller_head_pressure_drift"
    assert f.severity == "fault"
    assert f.metrics["head_pressure_drift_psi"] > 4.0
    assert f.metrics["head_pressure_drift_direction"] == "up"
    assert f.metrics["head_pressure_drift_sigma"] > 4.0
    assert f.metrics["head_pressure_sustained_alarm"] is True
    assert f.metrics["head_pressure_alarm_direction"] == "up"
    assert f.metrics["thresholds_provisional"] is True


def test_it_is_one_sided_a_falling_head_pressure_does_not_flag():
    """A large *fall* is not a high-side fault: the detector must stay quiet (unlike subcooling)."""
    store = BaselineStore()
    base, cur = _base_and({"offset_psi": -10.0})
    f = _rule(store).analyze_periods("CH_1", base, cur)

    assert f.severity == "ok"
    assert f.metrics["head_pressure_drift_psi"] < -4.0  # a big move...
    assert f.metrics["head_pressure_drift_direction"] == "down"  # ...but the wrong way
    assert f.metrics["head_pressure_sustained_alarm"] is False


def test_a_steady_chiller_does_not_flag():
    f = _rule(BaselineStore()).analyze_periods("CH_1", *_base_and({}))
    assert f.severity == "ok"
    assert abs(f.metrics["head_pressure_drift_psi"]) < 1.5
    assert f.metrics["head_pressure_sustained_alarm"] is False


def test_a_busier_period_at_matched_load_is_not_drift():
    """Load normalization: more tons (and so higher raw head pressure) must not read as a fault."""
    base, busy = _base_and({"tons_offset": 90.0})
    raw = float(busy[Role.DISCHARGE_PRESSURE].median()) - float(
        base[Role.DISCHARGE_PRESSURE].median()
    )
    assert raw > 5.0  # a level-vs-level comparison would see a clear rise

    f = _rule(BaselineStore()).analyze_periods("CH_1", base, busy)
    assert f.severity == "ok"
    assert abs(f.metrics["head_pressure_drift_psi"]) < 1.5


def test_a_rise_that_clears_psi_but_not_sigma_stays_ok():
    """Both a psi and a sigma floor must be cleared; the sigma floor holds a small, noisy rise."""
    f = _rule(BaselineStore()).analyze_periods("CH_1", *_base_and({"offset_psi": 3.0}))
    # +3 psi clears the 2 psi warn floor, but 3/2 = 1.5σ is under the 2.5σ warn floor
    assert f.metrics["head_pressure_drift_psi"] > 2.0
    assert f.metrics["head_pressure_drift_sigma"] < 2.5
    assert f.severity == "ok"


def test_the_finding_labels_its_two_threshold_classes_separately():
    f = _rule(BaselineStore()).analyze_periods("CH_1", *_base_and({"offset_psi": 10.0}))
    assert f.metrics["thresholds_provisional"] is True
    assert f.metrics["magnitude_threshold_confidence"] == MAGNITUDE_CONFIDENCE
    assert f.metrics["temporal_threshold_confidence"] == TEMPORAL_CONFIDENCE


# --------------------------------------------------------------------------- the confound


def test_a_co_moving_cw_supply_rise_is_caveated():
    """Head pressure AND entering CW temp both up -> flag the ambient/heat-rejection confound."""
    store = BaselineStore()
    base, cur = _base_and(
        {"offset_psi": 10.0, "cw_supply": True, "cw_offset_f": 6.0},
        base_kw={"cw_supply": True},
    )
    f = _rule(store).analyze_periods("CH_1", base, cur)

    assert f.severity == "fault"  # the pressure rise is still real and still ranked
    assert f.metrics["cw_supply_shift_f"] > 4.0
    assert any("heat-rejection" in c and "ambient" in c for c in f.caveats)


def test_a_flat_cw_supply_does_not_raise_the_confound_caveat():
    """Head pressure up with entering CW temp flat points at the high side -- no confound."""
    store = BaselineStore()
    base, cur = _base_and(
        {"offset_psi": 10.0, "cw_supply": True, "cw_offset_f": 0.0},
        base_kw={"cw_supply": True},
    )
    f = _rule(store).analyze_periods("CH_1", base, cur)

    assert f.severity == "fault"
    assert abs(f.metrics["cw_supply_shift_f"]) < 1.0
    assert not any("heat-rejection" in c for c in f.caveats)


def test_suction_pressure_adds_lift_context():
    """A mapped suction pressure yields the condensing-over-suction lift shift as context."""
    store = BaselineStore()
    base, cur = _base_and(
        {"offset_psi": 10.0, "suction": True},
        base_kw={"suction": True},
    )
    f = _rule(store).analyze_periods("CH_1", base, cur)

    assert "suction_pressure_shift_psi" in f.metrics
    assert "lift_shift_psi" in f.metrics
    # discharge rose ~10 psi and suction was flat, so the lift widened by ~10 psi
    assert f.metrics["lift_shift_psi"] > 6.0
    assert abs(f.metrics["suction_pressure_shift_psi"]) < 2.0


def test_confound_roles_absent_degrade_gracefully():
    """No CW-supply and no suction point mapped: the detector still works, with no confound keys."""
    f = _rule(BaselineStore()).analyze_periods("CH_1", *_base_and({"offset_psi": 10.0}))
    assert f.severity == "fault"
    assert "cw_supply_shift_f" not in f.metrics
    assert "lift_shift_psi" not in f.metrics


# --------------------------------------------------------------------------- instrumentation gate


def test_it_declines_loudly_when_discharge_pressure_is_not_mapped():
    """A chiller without the point must not read as a chiller with a healthy high side."""
    base = _role_frame(start="2025-05-01", seed=1, discharge=False)
    cur = _role_frame(start="2025-06-01", seed=2, discharge=False)
    f = _rule(BaselineStore()).analyze_periods("CH_1", base, cur)

    assert f.severity == "info"
    assert f.metrics["declined"] is True
    assert f.metrics["reason"] == "discharge_pressure_not_mapped"
    assert any("does not publish" in c for c in f.caveats)


def test_it_declines_when_no_baseline_can_be_frozen():
    f = _rule(BaselineStore(), freeze_if_missing=False).analyze_periods("CH_1", *_base_and({}))
    assert f.severity == "info"
    assert f.metrics["declined"] is True


# --------------------------------------------------------------------------- freeze / storage


def test_the_baseline_is_frozen_and_not_refit_on_a_later_run():
    store = BaselineStore()
    rule = _rule(store)
    base, cur = _base_and({"offset_psi": 10.0})
    rule.analyze_periods("CH_1", base, cur)
    coeffs = dict(store.get("SITE", "CH_1", "chiller_head_pressure").coefficients)

    worse = _role_frame(start="2025-07-01", seed=5, offset_psi=18.0)
    f = rule.analyze_periods("CH_1", worse, worse)
    assert store.get("SITE", "CH_1", "chiller_head_pressure").coefficients == coeffs
    assert f.severity == "fault"


def test_the_head_pressure_baseline_is_stored_separately():
    store = BaselineStore()
    _rule(store).analyze_periods("CH_1", *_base_and({}))
    assert store.get("SITE", "CH_1", "chiller_head_pressure") is not None
    # it must not collide with, or stand in for, the condenser-approach baseline
    assert store.get("SITE", "CH_1", "chiller_approach_cond") is None
