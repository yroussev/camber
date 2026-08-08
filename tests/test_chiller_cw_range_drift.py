"""Tests for condenser-water range drift (camber.rules.chiller_cw_range_rule).

All data here is synthetic. The chiller is a simple energy balance: condenser heat rejection is
proportional to load, and range = Q_cond / (500 * gpm), so a fault is injected by scaling the
condenser-water flow -- fouling/restriction reduces flow and *widens* the range, a bypass raises
flow and *narrows* it. Nothing is drawn from any measured dataset.
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.chillerbaseline import fit_load_baseline  # noqa: E402
from camber.coolingtower import cw_range_f  # noqa: E402
from camber.driftthresholds import MAGNITUDE_CONFIDENCE, TEMPORAL_CONFIDENCE  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.base import PeriodRule  # noqa: E402
from camber.rules.builtin import builtin_registry, rule_names  # noqa: E402
from camber.rules.chiller_cw_range_rule import (  # noqa: E402
    CW_RANGE_WARN_F,
    ChillerCwRangeDrift,
)
from camber.scorecard import RULE_CATEGORY  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402

# A synthetic machine: ~1.25 kW of condenser heat per ton of cooling, on a design CW flow that
# gives roughly a 10 degF range at full load.
_HEAT_RATIO = 1.25  # Q_cond / Q_evap
_DESIGN_GPM = 750.0
_CWS_F = 85.0
_SIGMA_F = 0.25  # sensor noise on each CW temperature


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
    flow_factor=1.0,
    tons_offset=0.0,
    cw=True,
):
    """A chiller role-frame.

    ``flow_factor`` scales condenser-water flow: below 1.0 is a restriction (fouled bundle, failing
    pump, throttled valve) and widens the range; above 1.0 is a bypass and narrows it.
    """
    rng = np.random.default_rng(seed + 100)
    idx = pd.date_range(start, periods=n, freq="1h")
    tons = _tons(n, seed=seed, offset=tons_offset)
    cols = {
        Role.CHW_FLOW: tons * 2.0,  # tons = gpm * dT / 24 with dT = 12
        Role.CHW_SUPPLY_TEMP: np.full(n, 44.0),
        Role.CHW_RETURN_TEMP: np.full(n, 56.0),
    }
    if cw:
        # Q_cond [BTU/hr] = 12000 * tons * heat_ratio = 500 * gpm * range
        rng_f = 12000.0 * tons * _HEAT_RATIO / (500.0 * _DESIGN_GPM * flow_factor)
        cols[Role.CW_SUPPLY_TEMP] = _CWS_F + rng.normal(0, _SIGMA_F, n)
        cols[Role.CW_RETURN_TEMP] = _CWS_F + rng_f + rng.normal(0, _SIGMA_F, n)
    return pd.DataFrame(cols, index=idx)


def _rule(store, **kw):
    return ChillerCwRangeDrift(store, site="SITE", run_id="2025-07-01T00:00", **kw)


def _base_and(current_kw):
    return _role_frame(start="2025-05-01", seed=1), _role_frame(
        start="2025-06-01", seed=2, **current_kw
    )


# --------------------------------------------------------------------------- interface


def test_it_is_a_period_rule_and_is_not_auto_registered():
    rule = _rule(BaselineStore())
    assert isinstance(rule, PeriodRule)
    assert "chiller_cw_range_drift" not in rule_names()
    assert "chiller_cw_range_drift" not in builtin_registry().names()


def test_it_uses_the_existing_cw_roles_and_treats_them_as_optional():
    """No new Role member is needed -- both ends of the range already exist."""
    rule = _rule(BaselineStore())
    assert Role.CW_SUPPLY_TEMP in rule.roles_optional
    assert Role.CW_RETURN_TEMP in rule.roles_optional
    # the chilled-water side is required, because load normalization depends on it
    assert Role.CHW_FLOW in rule.roles_required


def test_it_is_categorized_on_the_scorecard():
    assert RULE_CATEGORY["chiller_cw_range_drift"] == "maintenance"


def test_the_range_helper_is_the_shared_subtraction_with_its_sign_convention():
    frame = pd.DataFrame({"CWS_Temp": [85.0, 86.0], "CWR_Temp": [95.0, 94.5]})
    assert list(cw_range_f(frame)) == [10.0, 8.5]


# --------------------------------------------------------------------------- the detector


def test_restricted_condenser_flow_widens_the_range_and_flags():
    """Fouled bundle / failing pump / throttled valve: less flow, wider range at matched load."""
    store = BaselineStore()
    base, cur = _base_and({"flow_factor": 0.72})
    f = _rule(store).analyze_periods("CH_1", base, cur)

    assert f.rule == "chiller_cw_range_drift"
    assert f.severity == "fault"
    assert f.metrics["cw_range_drift_f"] > 2.0
    assert f.metrics["cw_range_drift_direction"] == "up"
    assert abs(f.metrics["cw_range_drift_sigma"]) > 5.0
    assert f.metrics["cw_range_sustained_alarm"] is True
    assert f.metrics["cw_range_alarm_direction"] == "up"
    assert "less condenser-water flow" in f.summary


def test_a_bypass_narrows_the_range_and_also_flags():
    """The other half of the fault space: flow that never crosses the condenser."""
    f = _rule(BaselineStore()).analyze_periods("CH_1", *_base_and({"flow_factor": 1.65}))

    assert f.severity == "fault"
    assert f.metrics["cw_range_drift_f"] < -2.0
    assert f.metrics["cw_range_drift_direction"] == "down"
    # a one-sided (rise-only) detector would have reported this as healthy
    assert f.metrics["cw_range_sustained_alarm"] is True
    assert f.metrics["cw_range_alarm_direction"] == "down"


def test_a_steady_chiller_does_not_flag():
    f = _rule(BaselineStore()).analyze_periods("CH_1", *_base_and({}))
    assert f.severity == "ok"
    assert abs(f.metrics["cw_range_drift_f"]) < 0.3
    assert f.metrics["cw_range_sustained_alarm"] is False


def _raw_range_median(frame):
    """Median range straight off the role-frame -- the level a naive comparison would use."""
    series = cw_range_f(frame, supply_col=Role.CW_SUPPLY_TEMP, return_col=Role.CW_RETURN_TEMP)
    return float(series.median())


def test_a_busier_period_at_matched_load_is_not_drift():
    """Load normalization: range widens with tons by itself, so more load is not a fault."""
    base, busy = _base_and({"tons_offset": 90.0})
    # a level-vs-level comparison would see a real-looking rise
    assert _raw_range_median(busy) - _raw_range_median(base) > 0.5

    f = _rule(BaselineStore()).analyze_periods("CH_1", base, busy)
    assert f.severity == "ok"
    assert abs(f.metrics["cw_range_drift_f"]) < 0.3
    assert f.metrics["cw_range_sustained_alarm"] is False


def test_a_mild_shift_inside_the_floors_stays_ok():
    """Both a degF and a sigma floor must be cleared, so small moves stay quiet.

    Severity only. The CUSUM does eventually trip on a shift this small held for a whole month --
    that is what an untuned decision interval buys, and why the temporal grade is the weaker one --
    but the reported severity, which is the magnitude claim, stays quiet.
    """
    f = _rule(BaselineStore()).analyze_periods("CH_1", *_base_and({"flow_factor": 0.97}))
    assert f.severity == "ok"
    assert abs(f.metrics["cw_range_drift_f"]) < CW_RANGE_WARN_F


def test_a_widening_and_an_equal_narrowing_score_identically():
    """Symmetric magnitude, reported sign -- both directions are real hydraulic faults."""
    # range is proportional to 1/flow, so these two flow factors are equal and opposite in degF
    restricted = 0.72
    bypassed = 1.0 / (2.0 - 1.0 / restricted)
    wide = _rule(BaselineStore()).analyze_periods("CH_1", *_base_and({"flow_factor": restricted}))
    narrow = _rule(BaselineStore()).analyze_periods("CH_1", *_base_and({"flow_factor": bypassed}))

    assert wide.severity == narrow.severity == "fault"
    assert wide.metrics["cw_range_drift_f"] > 0 > narrow.metrics["cw_range_drift_f"]
    gap = abs(wide.metrics["cw_range_drift_f"] + narrow.metrics["cw_range_drift_f"])
    assert gap < 0.3
    assert wide.metrics["cw_range_drift_direction"] == "up"
    assert narrow.metrics["cw_range_drift_direction"] == "down"


# --------------------------------------------------------------------------- instrumentation gate


def test_it_declines_loudly_when_the_cw_roles_are_not_mapped():
    """A chiller without the points must not read as a chiller with healthy condenser flow."""
    base = _role_frame(start="2025-05-01", seed=1, cw=False)
    cur = _role_frame(start="2025-06-01", seed=2, cw=False)
    f = _rule(BaselineStore()).analyze_periods("CH_1", base, cur)

    assert f.severity == "info"
    assert f.metrics["declined"] is True
    assert f.metrics["reason"] == "cw_range_not_mapped"
    assert any("cw_supply_temp" in c and "cw_return_temp" in c for c in f.caveats)


def test_it_declines_when_only_one_end_of_the_range_is_mapped():
    """Range needs both ends; one temperature is not half a diagnosis."""
    base, cur = _base_and({})
    f = _rule(BaselineStore()).analyze_periods(
        "CH_1", base.drop(columns=[Role.CW_RETURN_TEMP]), cur.drop(columns=[Role.CW_RETURN_TEMP])
    )
    assert f.severity == "info"
    assert f.metrics["reason"] == "cw_range_not_mapped"
    assert any("cw_return_temp" in c for c in f.caveats)


def test_it_declines_when_no_baseline_can_be_frozen():
    f = _rule(BaselineStore(), freeze_if_missing=False).analyze_periods("CH_1", *_base_and({}))
    assert f.severity == "info"
    assert f.metrics["declined"] is True


def test_it_declines_when_the_current_period_has_nothing_to_score():
    base, _ = _base_and({})
    idle = _role_frame(start="2025-06-01", seed=2)
    idle[Role.CHW_FLOW] = 0.5  # unloaded: no heat rejection, no hydraulic information
    f = _rule(BaselineStore()).analyze_periods("CH_1", base, idle)
    assert f.severity == "info"
    assert f.metrics["declined"] is True


# --------------------------------------------------------------------------- freeze / accept


def test_the_baseline_is_frozen_and_not_refit_on_a_later_run():
    store = BaselineStore()
    rule = _rule(store)
    base, cur = _base_and({"flow_factor": 0.72})
    rule.analyze_periods("CH_1", base, cur)
    coeffs = dict(store.get("SITE", "CH_1", "chiller_cw_range").coefficients)

    worse = _role_frame(start="2025-07-01", seed=5, flow_factor=0.65)
    f = rule.analyze_periods("CH_1", worse, worse)
    assert store.get("SITE", "CH_1", "chiller_cw_range").coefficients == coeffs
    assert f.severity == "fault"


def test_accept_new_normal_resets_the_reference_and_silences_the_drift():
    store = BaselineStore()
    rule = _rule(store)
    base, drifted = _base_and({"flow_factor": 0.72})
    assert rule.analyze_periods("CH_1", base, drifted).severity == "fault"

    refit = fit_load_baseline(
        pd.DataFrame(
            {
                "tons": drifted[Role.CHW_FLOW] / 2.0,
                "cw_range_f": cw_range_f(
                    drifted, supply_col=Role.CW_SUPPLY_TEMP, return_col=Role.CW_RETURN_TEMP
                ),
            }
        ),
        metric_col="cw_range_f",
    )
    rec = store.accept_new_normal(
        refit,
        site="SITE",
        equip="CH_1",
        kind="chiller_cw_range",
        accepted_by="plant.operator",
        reason="condenser bundle cleaned and flow rebalanced; post-service performance accepted",
        at="2025-06-30T09:00",
    )
    assert rec.accepted_by == "plant.operator" and len(rec.history) == 1

    after = rule.analyze_periods("CH_1", base, drifted)
    assert after.severity == "ok"
    assert after.metrics["cw_range_sustained_alarm"] is False


def test_the_range_baseline_is_stored_separately_from_the_approach_and_charge_ones():
    store = BaselineStore()
    _rule(store).analyze_periods("CH_1", *_base_and({}))
    assert store.get("SITE", "CH_1", "chiller_cw_range") is not None
    assert store.get("SITE", "CH_1", "chiller_approach_cond") is None
    assert store.get("SITE", "CH_1", "chiller_subcooling") is None
    # and it round-trips through the store's model registry
    assert store.model_for("SITE", "CH_1", "chiller_cw_range").sigma_f > 0


# --------------------------------------------------------------------------- threshold labelling


def test_the_finding_labels_its_two_threshold_classes_separately():
    f = _rule(BaselineStore()).analyze_periods("CH_1", *_base_and({"flow_factor": 0.72}))
    assert f.metrics["thresholds_provisional"] is True
    assert f.metrics["magnitude_threshold_confidence"] == MAGNITUDE_CONFIDENCE
    assert f.metrics["temporal_threshold_confidence"] == TEMPORAL_CONFIDENCE


# --------------------------------------------------------------------------- degenerate baselines


def test_it_declines_when_the_baseline_period_cannot_support_a_fit():
    """A flat-load baseline window identifies no slope, so no baseline is fabricated."""
    base, cur = _base_and({})
    flat = base.copy()
    flat[Role.CHW_FLOW] = 340.0  # constant load: the slope is unidentifiable
    f = _rule(BaselineStore()).analyze_periods("CH_1", flat, cur)

    assert f.severity == "info"
    assert f.metrics["declined"] is True
    assert any("would not support a fit" in c for c in f.caveats)


def test_a_noiseless_baseline_falls_back_to_degF_and_says_so():
    """With zero residual scatter the sigma floor is meaningless; degF alone must carry it."""
    store = BaselineStore()
    base = _role_frame(start="2025-05-01", seed=1)
    cur = _role_frame(start="2025-06-01", seed=2, flow_factor=0.72)
    # a perfectly linear baseline: sigma_f == 0, so drift_sigma is NaN and the CUSUM cannot run
    base[Role.CW_SUPPLY_TEMP] = _CWS_F
    base[Role.CW_RETURN_TEMP] = _CWS_F + 12000.0 * (base[Role.CHW_FLOW] / 2.0) * _HEAT_RATIO / (
        500.0 * _DESIGN_GPM
    )

    f = _rule(store).analyze_periods("CH_1", base, cur)
    assert store.get("SITE", "CH_1", "chiller_cw_range").coefficients["sigma_f"] == 0.0
    assert f.severity == "fault"  # judged on degF alone
    assert f.metrics["cw_range_drift_sigma"] != f.metrics["cw_range_drift_sigma"]  # NaN
    assert any("no residual scatter" in c for c in f.caveats)
    # the sigma-scaled CUSUM cannot run against a zero-sigma baseline, and says so rather than
    # silently reporting "no sustained shift"
    assert "cw_range_sustained_alarm" not in f.metrics
    assert any("could not run the sustained-shift alarm" in c for c in f.caveats)
    assert "temporal_threshold_confidence" not in f.metrics
    assert f.metrics["magnitude_threshold_confidence"] == MAGNITUDE_CONFIDENCE
