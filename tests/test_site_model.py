"""Tests for the Brick site-model round-trip (interop.site_model).

Uses the minimal (zero-dependency) parser backend so the round-trip is exercised
without requiring the optional ``rdflib`` extra.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.interop.brick import roles_from_brick  # noqa: E402
from camber.interop.site_model import site_from_ttl, site_to_ttl  # noqa: E402
from camber.model.entities import Equip, Point, Site  # noqa: E402
from camber.model.roles import Role  # noqa: E402


def _sample_site() -> Site:
    ahu = Equip(
        id="AHU_1", equip_class="AHU", site="ELC",
        points=(
            Point("AHU_1_SAT", Role.SUPPLY_AIR_TEMP),
            Point("AHU_1_OAT", Role.OAT),
            Point("AHU_1_CCV", Role.COOL_VALVE),
            Point("AHU_1_HCV", Role.HEAT_VALVE),
            Point("AHU_1_SF_SPD", Role.SUPPLY_FAN_SPEED),
        ),
    )
    vav = Equip(
        id="VAV_117", equip_class="VAV", site="ELC",
        points=(
            Point("VAV_117_ZNT", Role.SPACE_TEMP),
            Point("VAV_117_OCC", Role.OCCUPANCY),
        ),
    )
    return Site(id="ELC", climate_zone="CA CZ15", equips=(ahu, vav))


def test_site_to_ttl_emits_site_and_ispartof():
    ttl = site_to_ttl(_sample_site())
    assert "@prefix brick:" in ttl
    assert "bldg:ELC a brick:Site" in ttl
    assert "bldg:AHU_1 a brick:AHU" in ttl
    assert "bldg:VAV_117 a brick:VAV" in ttl
    assert "brick:isPartOf bldg:ELC" in ttl


def test_round_trip_preserves_equipment_and_roles():
    site = _sample_site()
    back = site_from_ttl(site_to_ttl(site), backend="minimal")

    assert back.id == "ELC"
    # equipment ids and classes survive
    assert {e.id for e in back.equips} == {"AHU_1", "VAV_117"}
    by_id = {e.id: e for e in back.equips}
    assert by_id["AHU_1"].equip_class == "AHU"
    assert by_id["VAV_117"].equip_class == "VAV"
    # every equipment is tied back to its site
    assert all(e.site == "ELC" for e in back.equips)

    # the role set on each equipment is recovered exactly
    assert by_id["AHU_1"].roles() == {
        Role.SUPPLY_AIR_TEMP, Role.OAT, Role.COOL_VALVE, Role.HEAT_VALVE,
        Role.SUPPLY_FAN_SPEED,
    }
    assert by_id["VAV_117"].roles() == {Role.SPACE_TEMP, Role.OCCUPANCY}


def test_round_trip_preserves_point_to_role_assignment():
    site = _sample_site()
    back = site_from_ttl(site_to_ttl(site), backend="minimal")
    pairs = {p.name: p.role
             for e in back.equips for p in e.points}
    # original point names map to their original roles after the round-trip
    assert pairs["AHU_1_SAT"] == Role.SUPPLY_AIR_TEMP
    assert pairs["AHU_1_CCV"] == Role.COOL_VALVE
    assert pairs["AHU_1_HCV"] == Role.HEAT_VALVE
    assert pairs["VAV_117_ZNT"] == Role.SPACE_TEMP


def test_site_ttl_reimports_pointwise_via_brick():
    # the document also imports through the existing point-level Brick reader
    ttl = site_to_ttl(_sample_site())
    roles = set(roles_from_brick(ttl, backend="minimal").values())
    assert Role.SPACE_TEMP in roles
    assert Role.COOL_VALVE in roles and Role.HEAT_VALVE in roles
