"""Tests for the ASHRAE 223P minimal-profile interop + the broadened Brick role map."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.interop.export import ROLE_TO_BRICK_POINT_CLASS, to_brick  # noqa: E402
from camber.interop.semantic223 import (  # noqa: E402
    role_223_quantity, site_from_223, site_to_223,
)
from camber.model.entities import Equip, Point, Site  # noqa: E402
from camber.model.roles import Role  # noqa: E402


def _site():
    ahu = Equip(id="AHU_1", equip_class="AHU", points=(
        Point(name="AHU_1.supply_air_temp", role=Role.SUPPLY_AIR_TEMP),
        Point(name="AHU_1.oa_airflow", role=Role.OA_AIRFLOW),
        Point(name="AHU_1.co2", role=Role.CO2),
    ))
    vav = Equip(id="VAV_3", equip_class="VAV", points=(
        Point(name="VAV_3.space_temp", role=Role.SPACE_TEMP),
        Point(name="VAV_3.airflow", role=Role.AIRFLOW),
    ))
    return Site(id="DemoSite", equips=(ahu, vav))


def test_role_223_quantity_mapping():
    assert role_223_quantity(Role.SUPPLY_AIR_TEMP) == ("Temperature", "Air")
    assert role_223_quantity(Role.OA_AIRFLOW) == ("VolumeFlowRate", "Air")
    assert role_223_quantity(Role.CHW_FLOW) == ("VolumeFlowRate", "Water")


def test_site_to_223_turtle_shape():
    ttl = site_to_223(_site())
    assert "@prefix s223:" in ttl and "@prefix qk:" in ttl
    assert "bldg:AHU_1 a s223:Equipment" in ttl
    assert 'rdfs:label "AHU"' in ttl
    assert "s223:hasProperty" in ttl
    assert "bldg:AHU_1.supply_air_temp a s223:Property" in ttl
    assert "s223:hasQuantityKind qk:Temperature" in ttl
    assert "s223:ofMedium s223:Air" in ttl


def test_223_roundtrip_preserves_equip_class_and_roles():
    site = _site()
    back = site_from_223(site_to_223(site), site_id="DemoSite")
    by_id = {e.id: e for e in back.equips}
    assert set(by_id) == {"AHU_1", "VAV_3"}
    assert by_id["AHU_1"].equip_class == "AHU" and by_id["VAV_3"].equip_class == "VAV"
    assert by_id["AHU_1"].roles() == {Role.SUPPLY_AIR_TEMP, Role.OA_AIRFLOW, Role.CO2}
    assert by_id["VAV_3"].roles() == {Role.SPACE_TEMP, Role.AIRFLOW}


def test_223_minimal_profile_drops_unmapped_roles():
    # WETBULB_TEMP isn't in the 223P map; minimal profile omits it, full keeps it
    eq = Equip(id="E1", equip_class="AHU",
               points=(Point("E1.wb", role=Role.WETBULB_TEMP),
                       Point("E1.sat", role=Role.SUPPLY_AIR_TEMP)))
    site = Site(id="S", equips=(eq,))
    minimal = site_to_223(site, profile="minimal")
    assert "wetbulb_temp a s223:Property" not in minimal
    assert "supply_air_temp a s223:Property" in minimal
    full = site_to_223(site, profile="full")
    assert "wetbulb_temp a s223:Property" in full           # full keeps it (dimensionless)


def test_223_no_relations_omits_hasproperty():
    ttl = site_to_223(_site(), include_relations=False)
    assert "s223:hasProperty" not in ttl
    assert "a s223:Equipment" in ttl and "a s223:Property" in ttl  # still typed


# --------------------------------------------------------------------------- richer Brick map

def test_brick_map_broadened():
    for role in (Role.CO2, Role.OA_AIRFLOW, Role.OUTDOOR_CO2, Role.OUTDOOR_RH, Role.AIRFLOW_SP):
        assert role in ROLE_TO_BRICK_POINT_CLASS
    ttl = to_brick("AHU_1", "AHU", [Role.CO2, Role.OA_AIRFLOW, Role.SUPPLY_AIR_TEMP])
    assert "CO2_Sensor" in ttl and "Outside_Air_Flow_Sensor" in ttl
