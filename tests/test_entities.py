"""Tests for the semantic entity model + completeness validation (model/entities)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.entities import (  # noqa: E402
    Completeness, Equip, Point, Runnable, Site, TEMPLATES,
    completeness, runnable_rules, template_for,
)
from camber.model.roles import Role  # noqa: E402


# --- entities -------------------------------------------------------------- #

def test_equip_roles_from_points():
    e = Equip("AHU_1", "AHU", points=(
        Point("AHU_1_HHW_Valve", Role.HEAT_VALVE),
        Point("AHU_1_CHW_Valve", Role.COOL_VALVE),
    ))
    assert e.roles() == frozenset({Role.HEAT_VALVE, Role.COOL_VALVE})


def test_equip_from_roles_roundtrips():
    e = Equip.from_roles("VAV_117", "VAV", {Role.SPACE_TEMP, Role.DAMPER})
    assert e.roles() == frozenset({Role.SPACE_TEMP, Role.DAMPER})
    assert all(p.name.startswith("VAV_117:") for p in e.points)


def test_site_lookup_and_class_filter():
    a1 = Equip("AHU_1", "AHU")
    a2 = Equip("AHU_2", "AHU")
    v = Equip("VAV_117", "VAV")
    s = Site("DemoSite", climate_zone="CA CZ15", equips=(a1, a2, v))
    assert s.of_class("AHU") == (a1, a2)
    assert s.equip("VAV_117") is v
    assert s.equip("nope") is None


# --- templates ------------------------------------------------------------- #

def test_terminal_box_classes_share_vav_template():
    assert template_for("CAV").required == template_for("VAV").required
    assert template_for("FCAV").optional == template_for("VAV").optional


def test_unknown_class_has_no_template():
    assert template_for("MYSTERY") is None


# --- completeness ---------------------------------------------------------- #

def test_completeness_ready_when_required_present():
    c = completeness("AHU", {Role.SUPPLY_AIR_TEMP, Role.HEAT_VALVE, Role.COOL_VALVE})
    assert isinstance(c, Completeness)
    assert c.ready
    assert c.missing_required == frozenset()
    assert c.score > 0.0


def test_completeness_not_ready_flags_missing_required():
    c = completeness("AHU", {Role.SUPPLY_AIR_TEMP})   # missing both valves
    assert not c.ready
    assert c.missing_required == frozenset({Role.HEAT_VALVE, Role.COOL_VALVE})


def test_completeness_score_full_is_one():
    full = TEMPLATES["VAV"].expected()
    c = completeness("VAV", full)
    assert c.score == 1.0
    assert c.missing_required == frozenset()
    assert c.missing_optional == frozenset()


def test_completeness_unexpected_roles_reported():
    c = completeness("Meter", {Role.POWER, Role.SPACE_TEMP})
    assert Role.SPACE_TEMP in c.unexpected
    assert c.ready          # POWER (the only required role) is present


def test_completeness_unknown_class_not_ready():
    c = completeness("MYSTERY", {Role.POWER})
    assert not c.has_template
    assert not c.ready
    assert c.score == 0.0
    assert c.unexpected == frozenset({Role.POWER})


# --- runnable rules (duck-typed) ------------------------------------------- #

class _FakeRule:
    def __init__(self, name, required, optional=()):
        self.name = name
        self.roles_required = tuple(required)
        self.roles_optional = tuple(optional)


def test_runnable_splits_on_required_roles():
    present = {Role.HEAT_VALVE, Role.COOL_VALVE}
    rules = [
        _FakeRule("simul_hc", (Role.HEAT_VALVE, Role.COOL_VALVE), (Role.OAT,)),
        _FakeRule("sat_reset", (Role.SUPPLY_AIR_TEMP,)),
    ]
    res = runnable_rules(present, rules)
    assert [r.rule for r in res] == ["simul_hc", "sat_reset"]
    simul, sat = res
    assert isinstance(simul, Runnable)
    assert simul.can_run
    assert simul.missing_optional == frozenset({Role.OAT})   # degrades, not blocks
    assert not sat.can_run
    assert sat.missing_required == frozenset({Role.SUPPLY_AIR_TEMP})


def test_runnable_handles_rule_without_optional_attr():
    class Bare:
        name = "bare"
        roles_required = (Role.POWER,)
    res = runnable_rules({Role.POWER}, [Bare()])
    assert res[0].can_run
    assert res[0].missing_optional == frozenset()
