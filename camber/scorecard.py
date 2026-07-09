"""Building health scorecard — synthesize findings into category scores and a grade.

A fault list is a to-do; a **scorecard** is the one-glance summary an owner or portfolio manager
reads. This rolls the FDD findings up into per-category scores (energy, comfort, ventilation,
maintenance) and an overall grade, deducting for each actionable finding by severity. It's the
synthesis layer on top of the rules — pairs with `camber.actionplan` (what to do) and
`camber.fault_economics` (what it's worth). stdlib only.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, asdict, field

_SEV = {"ok": 0, "info": 0, "warn": 1, "fault": 2}

#: rule name -> category. Rules not listed fall in "other".
RULE_CATEGORY = {
    # energy
    "simultaneous_heat_cool": "energy", "reheat_penalty": "energy",
    "reheat_minimization_g36": "energy", "supply_air_reset": "energy",
    "chiller_efficiency": "energy", "chiller_staging": "energy", "chiller_staging_fleet": "energy",
    "condenser_water_reset": "energy", "chw_plant_reset": "energy", "cooling_tower_approach": "energy",
    "hw_plant_deltat": "energy", "boiler_short_cycle": "energy", "boiler_summer_lockout": "energy",
    "chw_pump_dp_reset": "energy", "hw_pump_dp_reset": "energy", "night_weekend_setback": "energy",
    "outdoor_air_fraction": "energy", "economizer_high_limit": "energy",
    "free_cooling_missed": "energy", "static_pressure_reset": "energy",
    # comfort
    "unmet_setpoint_hours": "comfort", "overcooling_min_flow": "comfort",
    "overcooling_severity": "comfort", "supply_air_control": "comfort",
    "airflow_tracking": "comfort", "zones_heat_cool_census": "comfort",
    "cohort_airflow": "comfort", "cohort_space_temp": "comfort",
    # ventilation
    "co2_ventilation": "ventilation", "dcv_verification": "ventilation",
    # maintenance / controls
    "leaking_valve": "maintenance", "control_hunting": "maintenance", "damper_census": "maintenance",
}

CATEGORIES = ("energy", "comfort", "ventilation", "maintenance")


def category_for(rule: str) -> str:
    return RULE_CATEGORY.get(rule, "other")


def grade_for(score: float) -> str:
    return ("A" if score >= 90 else "B" if score >= 80 else "C" if score >= 70
            else "D" if score >= 60 else "F")


@dataclass
class CategoryScore:
    category: str
    score: float
    grade: str
    n_faults: int
    n_warnings: int

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Scorecard:
    """Overall + per-category building health from the findings."""

    overall_score: float
    overall_grade: str
    n_findings: int
    n_actionable: int
    categories: list = field(default_factory=list)

    def as_dict(self) -> dict:
        d = asdict(self)
        return d


def build_scorecard(findings, *, fault_penalty: float = 15.0, warn_penalty: float = 5.0,
                    category_weights: dict | None = None) -> Scorecard:
    """Roll findings up into per-category scores (100 minus severity penalties, clamped to 0..100)
    and a weighted overall score/grade.

    Each fault deducts ``fault_penalty`` and each warning ``warn_penalty`` from its category's 100.
    ``category_weights`` weights the overall average (default equal across :data:`CATEGORIES`).
    """
    by_cat = defaultdict(list)
    for f in findings:
        by_cat[category_for(getattr(f, "rule", ""))].append(f)

    weights = category_weights or {c: 1.0 for c in CATEGORIES}
    # score the fixed categories AND any extra category present in the findings (e.g. "other" for
    # unmapped/plugin rules) -- so an unmapped fault can never be silently dropped from the grade.
    scored = list(CATEGORIES) + sorted(c for c in by_cat if c not in CATEGORIES)
    cats, n_actionable = [], 0
    for cat in scored:
        fs = by_cat.get(cat, [])
        nf = sum(1 for f in fs if _SEV.get(getattr(f, "severity", ""), 0) == 2)
        nw = sum(1 for f in fs if _SEV.get(getattr(f, "severity", ""), 0) == 1)
        n_actionable += nf + nw
        score = max(0.0, min(100.0, 100.0 - nf * fault_penalty - nw * warn_penalty))
        cats.append(CategoryScore(category=cat, score=round(score, 1), grade=grade_for(score),
                                  n_faults=nf, n_warnings=nw))

    wsum = sum(weights.get(c.category, 1.0) for c in cats)
    overall = (sum(c.score * weights.get(c.category, 1.0) for c in cats) / wsum) if wsum else 100.0
    return Scorecard(overall_score=round(overall, 1), overall_grade=grade_for(overall),
                     n_findings=len(findings), n_actionable=n_actionable, categories=cats)
