"""Tests for Haystack tag→role import (camber.interop.haystack_semantic) — closes the round-trip."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role, HAYSTACK_HINT  # noqa: E402
from camber.interop.export import haystack_tags  # noqa: E402
from camber.interop.haystack_semantic import (  # noqa: E402
    role_from_tags, roles_from_haystack, mapping_from_haystack,
)


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
        {"id": "AHU1.SATSP", "discharge": True, "air": True, "temp": True, "sp": True, "point": True},
        ("AHU1.mystery", "foo bar baz"),                      # unresolved -> dropped
    ]
    roles = roles_from_haystack(points)
    assert roles == {"AHU1.SAT": Role.SUPPLY_AIR_TEMP, "AHU1.OAT": Role.OAT,
                     "AHU1.SATSP": Role.SUPPLY_AIR_TEMP_SP}


def test_mapping_from_haystack_builds_provider():
    mp = mapping_from_haystack([("CHW_ret", "chilled water entering temp sensor"),
                                ("kW", "elec power sensor")])
    assert mp.role_of("CHW_ret") is Role.CHW_RETURN_TEMP
    assert mp.role_of("kW") is Role.POWER


def test_dict_marker_detection_ignores_structural_tags():
    # id/dis/point/his are structural, not semantic -> only the marker tags drive the match
    p = {"id": "p1", "dis": "OA Temp", "point": True, "his": True,
         "outside": True, "air": True, "temp": True, "sensor": True}
    assert roles_from_haystack([p]) == {"p1": Role.OAT}
