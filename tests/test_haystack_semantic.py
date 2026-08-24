"""Tests for Haystack tag→role import (camber.interop.haystack_semantic) — closes the round-trip."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.interop.export import haystack_tags  # noqa: E402
from camber.interop.haystack_semantic import (  # noqa: E402
    mapping_from_haystack,
    role_from_tags,
    roles_from_haystack,
    topology_from_haystack,
)
from camber.model.roles import Role  # noqa: E402


def test_every_role_round_trips_through_haystack_tags():
    # export role -> tags -> import tags -> same role, for every role with a hint
    mismatches = []
    for role in Role:
        tags = haystack_tags(role)
        if not tags:
            continue
        if role_from_tags(tags) is not role:
            mismatches.append(role.value)
    assert mismatches == [], f"roles that did not round-trip: {mismatches}"


def test_most_specific_wins_sp_vs_sensor():
    assert role_from_tags({"discharge", "air", "temp", "sp"}) is Role.SUPPLY_AIR_TEMP_SP
    assert role_from_tags({"discharge", "air", "temp", "sensor"}) is Role.SUPPLY_AIR_TEMP


def test_role_from_tags_accepts_string_or_set():
    assert role_from_tags("outside air temp sensor") is Role.OAT
    assert role_from_tags({"outside", "air", "temp", "sensor"}) is Role.OAT


def test_unmatched_tags_return_none():
    assert role_from_tags({"foo", "bar"}) is None
    assert role_from_tags(set()) is None


def test_roles_from_haystack_pairs_and_dicts():
    points = [
        ("AHU1.SAT", "discharge air temp sensor"),
        ("AHU1.OAT", {"outside", "air", "temp", "sensor"}),
        {
            "id": "AHU1.SATSP",
            "discharge": True,
            "air": True,
            "temp": True,
            "sp": True,
            "point": True,
        },
        ("AHU1.mystery", "foo bar baz"),  # unresolved -> dropped
    ]
    roles = roles_from_haystack(points)
    assert roles == {
        "AHU1.SAT": Role.SUPPLY_AIR_TEMP,
        "AHU1.OAT": Role.OAT,
        "AHU1.SATSP": Role.SUPPLY_AIR_TEMP_SP,
    }


def test_mapping_from_haystack_builds_provider():
    mp = mapping_from_haystack(
        [("CHW_ret", "chilled water entering temp sensor"), ("kW", "elec power sensor")]
    )
    assert mp.role_of("CHW_ret") is Role.CHW_RETURN_TEMP
    assert mp.role_of("kW") is Role.POWER


def test_dict_marker_detection_ignores_structural_tags():
    # id/dis/point/his are structural, not semantic -> only the marker tags drive the match
    p = {
        "id": "p1",
        "dis": "OA Temp",
        "point": True,
        "his": True,
        "outside": True,
        "air": True,
        "temp": True,
        "sensor": True,
    }
    assert roles_from_haystack([p]) == {"p1": Role.OAT}


# ---- served-by topology from Haystack refs ----


def test_topology_from_ahuref():
    ents = [
        {"id": "VAV_1", "equip": True, "ahuRef": "@AHU_1"},
        {"id": "VAV_2", "equip": True, "ahuRef": {"val": "@AHU_1"}},  # Ref as dict
    ]
    t = topology_from_haystack(ents)
    assert set(t.edges) == {("AHU_1", "VAV_1"), ("AHU_1", "VAV_2")}
    assert t.provenance == "semantic"
    assert t.group_map(["VAV_1", "VAV_2"]) == {"VAV_1": "AHU_1", "VAV_2": "AHU_1"}


def test_equipref_only_followed_for_equipment():
    ents = [
        {"id": "AHU_1", "equip": True, "equipRef": "@CHW"},  # equip nesting -> edge
        {"id": "ZoneTemp", "point": True, "equipRef": "@VAV_1"},  # point ownership -> NOT served-by
    ]
    t = topology_from_haystack(ents)
    assert set(t.edges) == {("CHW", "AHU_1")}
    assert "ZoneTemp" not in [c for _, c in t.edges]


def test_topology_skips_unresolvable_entities():
    ents = [
        {"id": "", "equip": True, "ahuRef": "@AHU_9"},  # no id
        {"equip": True, "ahuRef": None},  # no id, null ref
        "not-a-dict",
        {"id": "VAV_3", "equip": True, "ahuRef": "AHU_2"},  # bare id (no @)
    ]
    t = topology_from_haystack(ents)
    assert set(t.edges) == {("AHU_2", "VAV_3")}


def test_topology_empty_input():
    assert topology_from_haystack([]).edges == ()


def test_roles_path_unaffected_by_refs():
    # a point carrying both role markers and an equipRef still resolves its role (refs invisible)
    pt = {
        "id": "ZT",
        "point": True,
        "equipRef": "@VAV_1",
        "zone": True,
        "air": True,
        "temp": True,
        "sensor": True,
    }
    assert roles_from_haystack([pt]) == {"ZT": Role.SPACE_TEMP}
