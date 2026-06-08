"""Tests for Brick/Haystack export (interop.export) incl. an import round-trip."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.interop.brick import roles_from_brick  # noqa: E402
from camber.interop.export import haystack_tags, to_brick  # noqa: E402
from camber.model.roles import Role  # noqa: E402


def test_haystack_tags_from_hint():
    assert haystack_tags(Role.OAT) == frozenset({"outside", "air", "temp", "sensor"})
    assert haystack_tags(Role.COOL_VALVE) == frozenset({"cooling", "valve", "cmd"})


def test_to_brick_emits_prefixes_and_typing():
    ttl = to_brick("AHU_1", "AHU", [Role.OAT, Role.SUPPLY_AIR_TEMP])
    assert "@prefix brick:" in ttl
    assert "bldg:AHU_1 a brick:AHU" in ttl
    assert "a brick:Outside_Air_Temperature_Sensor" in ttl


def test_round_trip_direct_and_context_roles():
    roles_in = {
        Role.OAT, Role.MIXED_AIR_TEMP, Role.RETURN_AIR_TEMP, Role.SUPPLY_AIR_TEMP,
        Role.SPACE_TEMP, Role.OCCUPANCY,
        Role.COOL_VALVE, Role.HEAT_VALVE, Role.OA_DAMPER,
        Role.SUPPLY_FAN_SPEED, Role.SUPPLY_FAN_STATUS,
    }
    ttl = to_brick("AHU_2", "AHU", roles_in)
    roles_out = set(roles_from_brick(ttl).values())
    assert roles_out == roles_in       # export -> import recovers every role


def test_shared_supply_fan_part_groups_points():
    # both fan signals attach to one Supply_Air_Fan part, not two
    ttl = to_brick("AHU_3", "AHU", [Role.SUPPLY_FAN_SPEED, Role.SUPPLY_FAN_STATUS])
    assert ttl.count("a brick:Fan ;") == 1
    out = set(roles_from_brick(ttl).values())
    assert out == {Role.SUPPLY_FAN_SPEED, Role.SUPPLY_FAN_STATUS}
