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
    # BOILER_STATUS is a status signal with no QUDT quantity-kind (in _NO_223_QUANTITY);
    # the minimal profile omits it, the full profile keeps it (dimensionless)
    eq = Equip(id="E1", equip_class="AHU",
               points=(Point("E1.bs", role=Role.BOILER_STATUS),
                       Point("E1.sat", role=Role.SUPPLY_AIR_TEMP)))
    site = Site(id="S", equips=(eq,))
    minimal = site_to_223(site, profile="minimal")
    assert "boiler_status a s223:Property" not in minimal
    assert "supply_air_temp a s223:Property" in minimal
    full = site_to_223(site, profile="full")
    assert "boiler_status a s223:Property" in full          # full keeps it (dimensionless)


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


# --- 0.6: broadened 223P coverage (plant / DX / humidity) -------------------- #

def _plant_site():
    chiller = Equip(id="CH_1", equip_class="Chiller", points=(
        Point(name="CH_1.power", role=Role.POWER),
        Point(name="CH_1.chw_supply_temp", role=Role.CHW_SUPPLY_TEMP),
        Point(name="CH_1.cond_approach_temp", role=Role.COND_APPROACH_TEMP),
    ))
    return Site(id="Plant", equips=(chiller,))


def test_223_covers_plant_and_refrigerant_roles():
    from camber.interop.semantic223 import ROLE_TO_223, _NO_223_QUANTITY
    # every role is either mapped to a quantity or explicitly documented as unmapped (no silent gaps)
    assert set(ROLE_TO_223) | set(_NO_223_QUANTITY) == set(Role)
    assert not (set(ROLE_TO_223) & set(_NO_223_QUANTITY))
    for r in (Role.CHW_SUPPLY_TEMP, Role.HW_PUMP_SPEED, Role.CW_RETURN_TEMP, Role.POWER,
              Role.COND_APPROACH_TEMP, Role.SUPPLY_AIR_HUMIDITY):
        assert role_223_quantity(r) is not None


def test_223_status_roles_intentionally_unmapped():
    for r in (Role.COMPRESSOR_STATUS, Role.BOILER_STATUS, Role.REVERSING_VALVE_CMD):
        assert role_223_quantity(r) is None


def test_223_plant_roles_emit_proper_quantity_and_round_trip():
    ttl = site_to_223(_plant_site())
    # the plant temp emits qk:Temperature + s223:Water, not the Dimensionless/Air default
    assert "qk:Temperature" in ttl and "s223:Water" in ttl and "qk:Power" in ttl
    back = site_from_223(ttl, site_id="Plant")
    roles = {p.role for e in back.equips for p in e.points}
    assert {Role.POWER, Role.CHW_SUPPLY_TEMP, Role.COND_APPROACH_TEMP} <= roles
