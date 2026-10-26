"""0.111 (#298): rules whose finding_detail had no flagged-hour spans now hand their evidence a
mask of the samples the finding counts (heat-pump mode and capacity, G36 reheat minimization,
overcooling severity, SAT reset compliance and the T&R reset-effectiveness rules);
supply_air_reset, which judges a shape, says why it has none.

Every frame is a faultlab scenario (offline, synthetic); each test checks that a faulty unit's
mask is non-empty and agrees with the finding's own hours or share, and that analyze() is
unchanged by the hook.
"""

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber import faultlab  # noqa: E402
from camber.rules.heatpump_ops_rule import (  # noqa: E402
    HPCapacityShortfall,
    HPModeVsNeed,
    _step_hours,
)
from camber.rules.overcooling_severity_rule import OvercoolingSeverity  # noqa: E402
from camber.rules.reheat_min_rule import ReheatMinimization  # noqa: E402
from camber.rules.reset_effectiveness_rule import ResetEffectiveness  # noqa: E402
from camber.rules.satreset_compliance_rule import SupplyAirResetCompliance  # noqa: E402
from camber.rules.satreset_rule import SupplyAirReset  # noqa: E402


def _frame(gen, faulty=True):
    return gen(faultlab._idx(), faulty=faulty)


def _mask(rule, frame):
    ev = rule.evidence("U", frame)
    assert ev is not None and ev.mask is not None
    m = pd.Series(ev.mask).fillna(False).astype(bool)
    assert m.index.isin(frame.index).all()
    return m


def _hours(m, frame):
    return round(float(m.sum()) * _step_hours(frame.index), 2)


def test_hp_mode_vs_need_mask_is_the_wrong_mode_hours():
    frame = _frame(faultlab._hp_mode_need)
    rule = HPModeVsNeed()
    f = rule.analyze("U", frame)
    assert f.severity in ("warn", "fault")
    m = _mask(rule, frame)
    want = f.metrics["cooling_while_cold_hours"] + f.metrics["heating_while_warm_hours"]
    assert m.any() and _hours(m, frame) == pytest.approx(want)


def test_hp_capacity_mask_is_the_outside_band_hours_of_its_side():
    frame = _frame(faultlab._hp_capacity)
    rule = HPCapacityShortfall()
    f = rule.analyze("U", frame)
    assert f.severity in ("warn", "fault")
    side = f.metrics["side"]
    m = _mask(rule, frame)
    assert m.any() and _hours(m, frame) == pytest.approx(f.metrics[f"{side}_outside_hours"])


def test_hp_healthy_unit_has_an_empty_mask():
    frame = _frame(faultlab._hp_mode_need, faulty=False)
    rule = HPModeVsNeed()
    assert rule.analyze("U", frame).severity == "ok"
    assert not _mask(rule, frame).any()
    assert HPCapacityShortfall().evidence("U", _frame(faultlab._hp_capacity, False)) is None


def test_reheat_minimization_mask_is_the_violating_reheat_samples():
    frame = _frame(faultlab._reheat_min)
    rule = ReheatMinimization()
    f = rule.analyze("U", frame)
    assert f.severity in ("warn", "fault")
    m = _mask(rule, frame)
    share = 100.0 * float(m.sum()) / f.metrics["n_reheat_samples"]
    assert m.any() and round(share, 1) == f.metrics["reheat_above_min_pct"]


def test_overcooling_severity_mask_is_the_sustained_tier_samples():
    frame = _frame(faultlab._overcool_sev)
    rule = OvercoolingSeverity()
    f = rule.analyze("U", frame)
    assert f.severity in ("warn", "fault")
    m = _mask(rule, frame)
    tier = "fault" if f.metrics["fault_pct"] else "warn"
    share = round(100.0 * float(m.sum()) / f.metrics["n_considered"], 2)
    assert m.any() and share == f.metrics[f"{tier}_pct"]


def test_overcooling_severity_analyze_is_unchanged_by_the_private_scorer():
    from camber.overcooling_severity import _score, analyze_overcooling_severity

    frame = _frame(faultlab._overcool_sev)
    legacy = frame.rename(columns={r: c for r, c in _legacy_cols().items() if r in frame.columns})
    masks: dict = {}
    a = analyze_overcooling_severity(legacy, "U")
    b = _score(legacy, "U", masks=masks)
    assert a == b and set(masks) == {"info", "warn", "fault"}


def _legacy_cols():
    from camber.rules.overcooling_severity_rule import _ROLE_TO_COL

    return _ROLE_TO_COL


def test_supply_air_reset_says_why_it_has_no_spans():
    """A reset-shape verdict flags no single hour (cold supply air on a hot day is normal), and
    counting cold hours as violations would move the RCx week; the rule says so instead."""
    frame = _frame(faultlab._sat_reset)
    rule = SupplyAirReset()
    assert rule.analyze("U", frame).severity == "warn"
    ev = rule.evidence("U", frame)
    assert ev is not None and ev.mask is None
    assert "not individual hours" in rule.no_spans_reason


def test_supply_air_reset_compliance_mask_is_the_below_target_share():
    frame = _frame(faultlab._sat_reset_compliance)
    rule = SupplyAirResetCompliance()
    f = rule.analyze("U", frame)
    assert f.severity == "warn"
    m = _mask(rule, frame)
    assert m.any() and len(m) == f.metrics["n"]
    assert round(100.0 * float(m.mean()), 1) == f.metrics["pct_below_target"]


@pytest.mark.parametrize(
    "reset,gen",
    [
        ("sat", faultlab._sat_reset_effectiveness),
        ("static", faultlab._static_reset_effectiveness),
    ],
)
def test_reset_effectiveness_masks_name_each_failure_mode(reset, gen):
    frame = _frame(gen)
    rule = ResetEffectiveness(reset=reset)
    f = rule.analyze("U", frame)
    assert f.severity == "warn"
    ev = rule.evidence("U", frame)
    assert ev is not None and ev.mask is not None and ev.mask.any()
    assert sorted(ev.masks) == sorted(f.metrics["failure_modes"])
    if "not_responding" in ev.masks:
        m = ev.masks["not_responding"]
        share = round(100.0 * float(m.sum()) / f.metrics["n_demand_judged"], 1)
        assert share == f.metrics["pct_high_demand_unresponsive"]


def test_reset_effectiveness_effective_reset_has_no_mask():
    rule = ResetEffectiveness(reset="sat")
    frame = _frame(faultlab._sat_reset_effectiveness, faulty=False)
    assert rule.analyze("U", frame).severity == "ok"
    ev = rule.evidence("U", frame)
    assert ev is not None and ev.mask is None and ev.masks is None
