"""Tests for fault prioritization + lifecycle (rules.triage)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.rules.triage import (  # noqa: E402
    FaultRegister, impact_score, rank_findings,
)


class _F:
    def __init__(self, rule, equip, severity, metrics=None):
        self.rule = rule
        self.equip = equip
        self.severity = severity
        self.metrics = metrics or {}


def test_severity_dominates_magnitude():
    warn_big = _F("a", "e1", "warn", {"pct": 99.0})
    fault_small = _F("b", "e2", "fault", {"pct": 1.0})
    assert impact_score(fault_small, magnitude_key="pct") > \
           impact_score(warn_big, magnitude_key="pct")


def test_rank_orders_worst_first():
    findings = [_F("a", "e1", "warn", {"pct": 10}),
                _F("b", "e2", "fault", {"pct": 5}),
                _F("c", "e3", "fault", {"pct": 50}),
                _F("d", "e4", "info")]
    ranked = rank_findings(findings, magnitude_key="pct")
    assert [r.finding.rule for r in ranked] == ["c", "b", "a", "d"]
    assert ranked[0].rank == 1 and ranked[-1].rank == 4


def test_rank_actionable_only_drops_ok_info():
    findings = [_F("a", "e1", "ok"), _F("b", "e2", "fault"), _F("c", "e3", "info")]
    ranked = rank_findings(findings, actionable_only=True)
    assert [r.finding.rule for r in ranked] == ["b"]


def test_lifecycle_new_ongoing_resolved():
    reg = FaultRegister()
    run1 = reg.update([_F("simul", "AHU_1", "fault"), _F("sat", "AHU_2", "warn")],
                      site="S", run_id=1)
    assert len(run1["new"]) == 2 and not run1["ongoing"] and not run1["resolved"]

    # AHU_1 persists, AHU_2 resolved, a new one on AHU_3 appears
    run2 = reg.update([_F("simul", "AHU_1", "fault"), _F("oa", "AHU_3", "warn")],
                      site="S", run_id=2)
    assert len(run2["ongoing"]) == 1     # AHU_1/simul
    assert len(run2["new"]) == 1         # AHU_3/oa
    assert len(run2["resolved"]) == 1    # AHU_2/sat
    # only AHU_1 and AHU_3 remain open
    assert len(reg.open_faults()) == 2


def test_lifecycle_ignores_non_actionable():
    reg = FaultRegister()
    out = reg.update([_F("x", "e", "ok"), _F("y", "e", "info")], site="S", run_id=1)
    assert out["new"] == [] and reg.open_faults() == {}


# --- root-cause grouping ---------------------------------------------------- #

from camber.rules.triage import group_findings, RootCauseGroup  # noqa: E402


def test_grouping_clusters_causal_chain_on_one_equip():
    findings = [
        _F("supply_air_reset", "AHU_2", "warn"),       # upstream root
        _F("reheat_penalty", "AHU_2", "fault"),        # downstream
        _F("simultaneous_heat_cool", "AHU_2", "warn"), # furthest downstream
        _F("leaking_valve", "AHU_2", "fault"),         # unrelated -> own group
    ]
    groups = group_findings(findings)
    assert isinstance(groups[0], RootCauseGroup)
    chain = [g for g in groups if g.primary_rule == "supply_air_reset"]
    assert len(chain) == 1
    g = chain[0]
    assert len(g.members) == 3                          # the 3 chain findings
    assert g.severity == "fault"                        # worst among members
    assert g.members[0].rule == "supply_air_reset"      # root-first ordering
    # the leak is a separate single-member group
    assert any(x.primary_rule == "leaking_valve" and len(x.members) == 1 for x in groups)


def test_grouping_separates_by_equipment():
    findings = [_F("reheat_penalty", "AHU_1", "fault"),
                _F("reheat_penalty", "AHU_2", "warn")]
    groups = group_findings(findings)
    assert {g.equip for g in groups} == {"AHU_1", "AHU_2"}
    assert all(len(g.members) == 1 for g in groups)


def test_grouping_drops_non_actionable():
    findings = [_F("supply_air_reset", "AHU_3", "ok"),
                _F("reheat_penalty", "AHU_3", "fault")]
    groups = group_findings(findings)
    # only the fault remains, as a one-member group
    assert len(groups) == 1 and len(groups[0].members) == 1
    assert groups[0].primary_rule == "reheat_penalty"
