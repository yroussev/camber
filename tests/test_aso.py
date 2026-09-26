"""Tests for advisory ASO — diagnosis -> suggested corrective action (camber.aso)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.aso import (  # noqa: E402
    RECOMMENDERS,
    Recommendation,
    recommend,
    recommend_findings,
)
from camber.rules.base import Finding  # noqa: E402


def _f(rule, sev, equip="AHU-1"):
    return Finding(rule=rule, equip=equip, severity=sev, summary="x")


def test_recommend_actionable_finding_is_advisory_and_grounded():
    r = recommend(_f("simultaneous_heat_cool", "fault"))
    assert isinstance(r, Recommendation)
    assert r.advisory is True  # never a command
    assert r.title and r.action and r.standard  # grounded: has an action + a citation
    assert r.parameter and r.suggested
    assert r.equip == "AHU-1" and r.rule == "simultaneous_heat_cool"


def test_recommend_skips_non_actionable():
    assert recommend(_f("supply_air_reset", "ok")) is None
    assert recommend(_f("supply_air_reset", "info")) is None


def test_recommend_unmapped_rule_returns_none():
    assert recommend(_f("damper_census", "fault")) is None  # no recommender -> nothing fabricated


def test_recommend_findings_orders_fault_first_and_filters():
    findings = [
        _f("supply_air_reset", "warn", "AHU-2"),
        _f("simultaneous_heat_cool", "fault", "AHU-1"),
        _f("co2_ventilation", "ok", "Z-1"),  # skipped (not actionable)
        _f("damper_census", "warn", "AHU-4"),  # skipped (unmapped)
    ]
    recs = recommend_findings(findings)
    assert [r.severity for r in recs] == ["fault", "warn"]  # fault before warn
    assert {r.rule for r in recs} == {"simultaneous_heat_cool", "supply_air_reset"}


def test_min_severity_fault_only_excludes_warn():
    findings = [_f("simultaneous_heat_cool", "fault"), _f("supply_air_reset", "warn")]
    recs = recommend_findings(findings, min_severity="fault")
    assert len(recs) == 1 and recs[0].severity == "fault"


def test_params_override_reflected_in_suggestion():
    base = recommend(_f("simultaneous_heat_cool", "fault"))
    over = recommend(_f("simultaneous_heat_cool", "fault"), params={"hc_deadband_F": 8.0})
    assert "5" in base.suggested and "8" in over.suggested


def test_all_recommenders_produce_valid_recommendations():
    for rule, _fn in RECOMMENDERS.items():
        r = recommend(_f(rule, "fault"))
        assert r is not None, rule
        assert r.advisory is True
        assert r.title and r.action and r.standard, rule  # each is grounded + cited
        assert r.confidence in {"high", "medium", "low"}
        assert r.as_dict()["rule"] == rule  # JSON-friendly


# --- outside-air advice follows the failure mode ---------------------------------------------


def test_outdoor_air_fraction_under_ventilation_gets_minimum_oa_advice():
    from camber.aso import recommend
    from camber.rules.base import Finding

    m = {"oaf_median_pct": 4.0, "min_oa_pct": 20.0, "failure_mode": "under_ventilation"}
    f = Finding("outdoor_air_fraction", "DemoAHU", "fault", m)
    r = recommend(f)
    assert r.title == "Restore minimum outside air"
    assert "minimum-OA damper position" in r.action and "4% vs a 20% minimum" in r.action
    assert "high limit" not in r.action
    # an older finding without failure_mode is inferred from its metrics
    old = Finding(
        "outdoor_air_fraction", "DemoAHU", "fault", {"oaf_median_pct": 4, "min_oa_pct": 20}
    )
    assert recommend(old).title == "Restore minimum outside air"


def test_excess_oa_and_high_limit_get_lockout_advice_with_the_configured_limit():
    from camber.aso import recommend
    from camber.rules.base import Finding

    m = {"oaf_median_pct": 55.0, "min_oa_pct": 20.0, "failure_mode": "excess_oa"}
    ex = Finding("outdoor_air_fraction", "DemoAHU", "warn", m)
    assert recommend(ex).title == "Lock out the economizer above the high limit"
    hl = Finding("economizer_high_limit", "DemoAHU", "fault", {"high_limit_f": 68.0})
    r = recommend(hl)
    assert r.title == "Lock out the economizer above the high limit" and "~68°F" in r.action
    fc = recommend(Finding("free_cooling_missed", "DemoAHU", "warn", {}))
    assert fc.title == "Enable economizer free cooling"


def test_oa_fraction_rule_records_its_failure_mode():
    import numpy as np
    import pandas as pd

    from camber.model.roles import Role
    from camber.rules.oafraction_rule import OutdoorAirFraction

    idx = pd.date_range("2026-06-01", periods=24 * 14, freq="h")
    oat = 60 + 25 * np.sin((idx.hour.to_numpy() - 9) / 24 * 2 * np.pi)
    rat = np.full(len(idx), 74.0)

    def frame(oaf):
        return pd.DataFrame(
            {
                Role.OAT: oat,
                Role.RETURN_AIR_TEMP: rat,
                Role.MIXED_AIR_TEMP: rat - oaf * (rat - oat),
            },
            index=idx,
        )

    assert (
        OutdoorAirFraction().analyze("A", frame(0.04)).metrics["failure_mode"]
        == "under_ventilation"
    )
    assert OutdoorAirFraction().analyze("A", frame(0.7)).metrics["failure_mode"] == "excess_oa"
    assert OutdoorAirFraction().analyze("A", frame(0.2)).metrics["failure_mode"] is None
