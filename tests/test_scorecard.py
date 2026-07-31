"""Tests for the building health scorecard (camber.scorecard)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.rules.base import Finding  # noqa: E402
from camber.scorecard import (  # noqa: E402
    CATEGORIES,
    Scorecard,
    _category_for,  # internal helpers, exercised directly
    _grade_for,
    build_scorecard,
)


def _f(rule, sev):
    return Finding(rule=rule, equip="E", severity=sev, summary="")


def test_clean_building_scores_100_A():
    sc = build_scorecard([_f("chiller_efficiency", "ok")])
    assert isinstance(sc, Scorecard)
    assert sc.overall_score == 100.0 and sc.overall_grade == "A" and sc.n_actionable == 0
    assert {c.category for c in sc.categories} == set(CATEGORIES)


def test_penalties_by_category_and_severity():
    sc = build_scorecard(
        [
            _f("simultaneous_heat_cool", "fault"),  # energy -15
            _f("unmet_setpoint_hours", "warn"),
        ]
    )  # comfort -5
    by = {c.category: c for c in sc.categories}
    assert by["energy"].score == 85.0 and by["energy"].n_faults == 1
    assert by["comfort"].score == 95.0 and by["comfort"].n_warnings == 1
    assert by["ventilation"].score == 100.0  # untouched


def test_grade_thresholds():
    assert (_grade_for(95), _grade_for(85), _grade_for(72), _grade_for(61), _grade_for(40)) == (
        "A",
        "B",
        "C",
        "D",
        "F",
    )


def test_score_clamped_to_zero():
    many = [_f("simultaneous_heat_cool", "fault") for _ in range(20)]  # 20×15 penalty
    sc = build_scorecard(many)
    energy = next(c for c in sc.categories if c.category == "energy")
    assert energy.score == 0.0 and energy.grade == "F"


def test_category_mapping_and_unknown_is_other():
    assert _category_for("co2_ventilation") == "ventilation"
    assert _category_for("leaking_valve") == "maintenance"
    assert _category_for("totally_unknown_rule") == "other"  # unmapped -> other, not an error


def test_weights_and_jsonable():
    findings = [_f("simultaneous_heat_cool", "fault")]  # only energy hit
    heavy = build_scorecard(
        findings, category_weights={"energy": 10, "comfort": 1, "ventilation": 1, "maintenance": 1}
    )
    light = build_scorecard(findings)
    assert heavy.overall_score < light.overall_score  # weighting energy drags it down
    assert (
        "categories" in heavy.as_dict() and heavy.as_dict()["overall_grade"] == heavy.overall_grade
    )
