"""Tests for naming/space-inferred served-by topology (screening-grade heuristic)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.entities import Equip, Site  # noqa: E402
from camber.topology_infer import topology_from_naming  # noqa: E402


def _eq(id, cls, space=""):
    return Equip(id=id, equip_class=cls, space=space)


def test_id_prefix_links_terminal_to_ahu():
    equips = [_eq("AHU_1", "AHU"), _eq("AHU_1_VAV_3", "VAV"), _eq("AHU_1_VAV_4", "VAV")]
    t = topology_from_naming(equips)
    assert set(t.edges) == {("AHU_1", "AHU_1_VAV_3"), ("AHU_1", "AHU_1_VAV_4")}
    assert t.provenance == "heuristic"


def test_shared_space_label_links():
    equips = [_eq("AHU_2", "AHU", space="Wing-A"), _eq("VAV_9", "VAV", space="Wing-A")]
    t = topology_from_naming(equips)
    assert set(t.edges) == {("AHU_2", "VAV_9")}


def test_space_matches_ahu_id():
    equips = [_eq("AHU_3", "AHU"), _eq("VAV_7", "VAV", space="AHU_3")]
    assert set(topology_from_naming(equips).edges) == {("AHU_3", "VAV_7")}


def test_ambiguous_prefix_is_skipped():
    # two AHUs whose ids both prefix the terminal -> no guess
    equips = [_eq("AHU", "AHU"), _eq("AHU_1", "AHU"), _eq("AHU_1_VAV_2", "VAV")]
    t = topology_from_naming(equips)
    # "AHU_1_VAV_2" starts with "AHU_" and "AHU_1_" -> ambiguous -> skipped
    assert t.edges == ()


def test_ambiguous_space_is_skipped():
    # two AHUs share the terminal's space label -> conflicting evidence -> no guess (rule 1)
    equips = [
        _eq("AHU_1", "AHU", space="Wing-A"),
        _eq("AHU_2", "AHU", space="Wing-A"),
        _eq("VAV_5", "VAV", space="Wing-A"),
    ]
    assert topology_from_naming(equips).edges == ()


def test_unmatched_terminal_omitted():
    equips = [_eq("AHU_1", "AHU"), _eq("VAV_99", "VAV")]  # no shared prefix / space
    assert topology_from_naming(equips).edges == ()


def test_accepts_a_site():
    site = Site(id="S", equips=(_eq("AHU_1", "AHU"), _eq("AHU_1_VAV_1", "VAV")))
    assert set(topology_from_naming(site).edges) == {("AHU_1", "AHU_1_VAV_1")}


def test_empty_input():
    assert topology_from_naming([]).edges == ()
    assert topology_from_naming(Site(id="S")).edges == ()


def test_custom_classes():
    equips = [_eq("RTU_1", "RTU"), _eq("RTU_1_ZONE_A", "FCU")]
    assert set(topology_from_naming(equips).edges) == {("RTU_1", "RTU_1_ZONE_A")}
