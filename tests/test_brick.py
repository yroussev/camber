"""Tests for Brick Schema interop (interop.brick)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.interop.brick import (  # noqa: E402
    mapping_from_brick,
    parse_triples,
    roles_from_brick,
)
from camber.model.roles import Role  # noqa: E402

TTL = """
@prefix bldg: <bldg#> .
@prefix brick: <https://brickschema.org/schema/Brick#> .

bldg:AHU a brick:AHU ;
    brick:hasPart bldg:Cooling_Coil,
        bldg:Heating_Coil,
        bldg:Outdoor_Air_Damper,
        bldg:Supply_Air_Fan ;
    brick:hasPoint bldg:MA_TEMP,
        bldg:OA_TEMP .

bldg:MA_TEMP a brick:Mixed_Air_Temperature_Sensor .
bldg:OA_TEMP a brick:Outside_Air_Temperature_Sensor .

bldg:Cooling_Coil a brick:Chilled_Water_Coil ;
    brick:hasPoint bldg:CHWC_VLV,
        bldg:CHWC_VLV_DM .
bldg:CHWC_VLV a brick:Valve_Position_Sensor .
bldg:CHWC_VLV_DM a brick:Valve_Position_Command .

bldg:Heating_Coil a brick:Hot_Water_Coil ;
    brick:hasPoint bldg:HC_VLV .
bldg:HC_VLV a brick:Valve_Position_Sensor .

bldg:Outdoor_Air_Damper a brick:Outside_Damper ;
    brick:hasPoint bldg:OA_DMPR .
bldg:OA_DMPR a brick:Damper_Position_Sensor .

bldg:Supply_Air_Fan a brick:Fan ;
    brick:hasPoint bldg:SF_SPD .
bldg:SF_SPD a brick:Speed_status .
"""


def test_parse_triples_types_and_haspoint():
    types, has_point = parse_triples(TTL)
    assert types["MA_TEMP"] == "Mixed_Air_Temperature_Sensor"
    assert types["Cooling_Coil"] == "Chilled_Water_Coil"
    assert "CHWC_VLV" in has_point["Cooling_Coil"]


def test_direct_class_roles():
    roles = roles_from_brick(TTL)
    assert roles["MA_TEMP"] == Role.MIXED_AIR_TEMP
    assert roles["OA_TEMP"] == Role.OAT


def test_valve_role_from_coil_context():
    roles = roles_from_brick(TTL)
    assert roles["CHWC_VLV"] == Role.COOL_VALVE  # owned by a chilled-water coil
    assert roles["HC_VLV"] == Role.HEAT_VALVE  # owned by a hot-water coil


def test_damper_and_fan_context():
    roles = roles_from_brick(TTL)
    assert roles["OA_DMPR"] == Role.OA_DAMPER  # owned by the outside damper
    assert roles["SF_SPD"] == Role.SUPPLY_FAN_SPEED  # owned by the supply fan


def test_command_points_skipped():
    roles = roles_from_brick(TTL)
    assert "CHWC_VLV_DM" not in roles  # a command, not a measured point


def test_mapping_from_brick_is_usable():
    mp = mapping_from_brick(TTL)
    assert mp.role_of("CHWC_VLV") == Role.COOL_VALVE
    assert mp.role_of("ma_temp") == Role.MIXED_AIR_TEMP  # case-insensitive lookup


# --- rdflib backend (optional [brick] extra) -------------------------------- #

import pytest  # noqa: E402


def test_rdflib_backend_matches_minimal():
    pytest.importorskip("rdflib")
    r = roles_from_brick(TTL, backend="rdflib")
    m = roles_from_brick(TTL, backend="minimal")
    assert r == m
    assert r["CHWC_VLV"] == Role.COOL_VALVE


def test_rdflib_handles_rdf_type_only():
    pytest.importorskip("rdflib")
    ttl = """
@prefix bldg: <urn:bldg#> .
@prefix brick: <https://brickschema.org/schema/Brick#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
bldg:AHU rdf:type brick:AHU ; brick:hasPoint bldg:OA_TEMP .
bldg:OA_TEMP rdf:type brick:Outside_Air_Temperature_Sensor .
bldg:Cooling_Coil rdf:type brick:Chilled_Water_Coil ;
    brick:hasPoint bldg:CHWC_VLV .
bldg:CHWC_VLV rdf:type brick:Valve_Position_Sensor .
"""
    r = roles_from_brick(ttl, backend="rdflib")
    assert r["OA_TEMP"] == Role.OAT
    assert r["CHWC_VLV"] == Role.COOL_VALVE  # resolved via the coil context
    # the minimal parser only understands `a`, not `rdf:type`, so it sees no types
    assert roles_from_brick(ttl, backend="minimal") != r


def test_rdflib_backend_required_when_forced(monkeypatch):
    import camber.interop.brick as b

    monkeypatch.setattr(b, "_have_rdflib", lambda: False)
    with pytest.raises(ImportError):
        b.roles_from_brick("x", backend="rdflib")


# --- hydronic plant classes ------------------------------------------------------------------ #

from camber.interop.brick import brick_mapping_report  # noqa: E402

PLANT_TTL = """
@prefix bldg: <urn:plant#> .
@prefix brick: <https://brickschema.org/schema/Brick#> .

bldg:HW_Plant a brick:Hot_Water_System ;
    brick:hasPart bldg:Boiler_A, bldg:Boiler_B, bldg:HW_Pump_A, bldg:Pump_X, bldg:Chiller_A ;
    brick:hasPoint bldg:LOOP_HWS, bldg:LOOP_HWR, bldg:LOOP_DP, bldg:LOOP_DPSP .
bldg:LOOP_HWS a brick:Hot_Water_Supply_Temperature_Sensor .
bldg:LOOP_HWR a brick:Hot_Water_Return_Temperature_Sensor .
bldg:LOOP_DP a brick:Hot_Water_Differential_Pressure_Sensor .
bldg:LOOP_DPSP a brick:Hot_Water_Differential_Pressure_Setpoint .

bldg:Boiler_A a brick:Natural_Gas_Boiler ;
    brick:hasPoint bldg:BA_FLOW, bldg:BA_ENABLE, bldg:BA_LVG .
bldg:BA_FLOW a brick:Hot_Water_Flow_Sensor .
bldg:BA_ENABLE a brick:On_Off_Status .
bldg:BA_LVG a brick:Leaving_Hot_Water_Temperature_Sensor .

bldg:Boiler_B a brick:Boiler ;
    brick:hasPoint bldg:BB_RUN, bldg:BB_EN .
bldg:BB_RUN a brick:Run_Status .
bldg:BB_EN a brick:Enable_Status .

bldg:HW_Pump_A a brick:Hot_Water_Pump ;
    brick:hasPoint bldg:PA_SPD, bldg:PA_STA, bldg:PA_KW .
bldg:PA_SPD a brick:Speed_Status .
bldg:PA_STA a brick:Pump_On_Off_Status .
bldg:PA_KW a brick:Electrical_Power_Sensor .

bldg:Pump_X a brick:Pump ;
    brick:hasPoint bldg:PX_SPD .
bldg:PX_SPD a brick:Speed_Status .

bldg:Chiller_A a brick:Chiller ;
    brick:hasPoint bldg:CH_CHWS, bldg:CH_CHWR, bldg:CH_CHWSP, bldg:CH_FLOW, bldg:CH_ECW,
        bldg:CH_LCW, bldg:CH_LVG .
bldg:CH_CHWS a brick:Chilled_Water_Supply_Temperature_Sensor .
bldg:CH_CHWR a brick:Chilled_Water_Return_Temperature_Sensor .
bldg:CH_CHWSP a brick:Chilled_Water_Supply_Temperature_Setpoint .
bldg:CH_FLOW a brick:Chilled_Water_Flow_Sensor .
bldg:CH_ECW a brick:Entering_Condenser_Water_Temperature_Sensor .
bldg:CH_LCW a brick:Leaving_Condenser_Water_Temperature_Sensor .
bldg:CH_LVG a brick:Leaving_Chilled_Water_Temperature_Sensor .

bldg:AHU_1 a brick:AHU ; brick:hasPart bldg:HW_Coil, bldg:Supply_Fan_1 .
bldg:HW_Coil a brick:Hot_Water_Coil ;
    brick:hasPoint bldg:HC_CMD, bldg:HC_LVG .
bldg:HC_CMD a brick:Valve_Command .
bldg:HC_LVG a brick:Leaving_Hot_Water_Temperature_Sensor .
bldg:Supply_Fan_1 a brick:Supply_Fan ;
    brick:hasPoint bldg:SF1_KW .
bldg:SF1_KW a brick:Electrical_Power_Sensor .
"""


def test_hydronic_direct_classes():
    roles = roles_from_brick(PLANT_TTL, backend="minimal")
    assert roles["LOOP_HWS"] == Role.HW_SUPPLY_TEMP
    assert roles["LOOP_HWR"] == Role.HW_RETURN_TEMP
    assert roles["LOOP_DP"] == Role.HW_DIFF_PRESS
    assert roles["LOOP_DPSP"] == Role.HW_DIFF_PRESS_SP
    assert roles["BA_FLOW"] == Role.HW_FLOW
    assert roles["CH_CHWS"] == Role.CHW_SUPPLY_TEMP
    assert roles["CH_CHWR"] == Role.CHW_RETURN_TEMP
    assert roles["CH_CHWSP"] == Role.CHW_SUPPLY_TEMP_SP
    assert roles["CH_FLOW"] == Role.CHW_FLOW
    assert roles["CH_ECW"] == Role.CW_SUPPLY_TEMP  # entering the condenser = leaving the tower
    assert roles["CH_LCW"] == Role.CW_RETURN_TEMP


def test_pump_context_roles():
    roles = roles_from_brick(PLANT_TTL, backend="minimal")
    assert roles["PA_SPD"] == Role.HW_PUMP_SPEED
    assert roles["PA_STA"] == Role.PUMP_STATUS
    assert roles["PA_KW"] == Role.POWER  # a pump's own power is its equip-frame POWER
    assert "PX_SPD" not in roles  # a generic Pump: hot or chilled water is not stated


def test_boiler_enable_is_never_mapped_to_firing_status():
    roles = roles_from_brick(PLANT_TTL, backend="minimal")
    # a boiler On_Off_Status is, in published models, often the enable (on all season);
    # mapping it to BOILER_STATUS reads every enabled-but-idle hour as firing
    assert "BA_ENABLE" not in roles
    assert "BB_EN" not in roles  # Enable_Status is never a running status
    assert roles["BB_RUN"] == Role.BOILER_STATUS  # Run_Status is proof of operation
    rep = brick_mapping_report(PLANT_TTL, backend="minimal")
    amb = {p.point: p for p in rep.with_status("ambiguous")}
    assert "enable" in amb["BA_ENABLE"].note and "enable" in amb["BB_EN"].note


def test_leaving_water_depends_on_the_owner():
    roles = roles_from_brick(PLANT_TTL, backend="minimal")
    assert roles["BA_LVG"] == Role.HW_SUPPLY_TEMP  # a boiler's leaving water is supply
    assert roles["CH_LVG"] == Role.CHW_SUPPLY_TEMP
    assert "HC_LVG" not in roles  # a coil's leaving water is its return, not plant supply


def test_command_is_a_fallback_only_when_no_feedback():
    roles = roles_from_brick(PLANT_TTL, backend="minimal")
    assert roles["HC_CMD"] == Role.HEAT_VALVE  # the coil has no position sensor
    assert "CHWC_VLV_DM" not in roles_from_brick(TTL)  # ...but a sensor sibling wins


def test_component_fan_power_is_not_equipment_power():
    rep = brick_mapping_report(PLANT_TTL, backend="minimal")
    p = next(x for x in rep.points if x.point == "SF1_KW")
    assert p.role is None and p.status == "ambiguous"


# --- non-standard classes seen in published models ------------------------------------------- #

QUIRK_TTL = """
@prefix bldg: <urn:quirk#> .
@prefix brick1: <https://brickschema.org/schema/1.1/Brick#> .

bldg:RTU_1 a brick1:Rooftop_Unit ;
    brick1:hasPoint bldg:rtu_1_oa_fr, bldg:rtu_1_oa_damper, bldg:rtu_1_econ_stpt,
        bldg:rtu_1_sf_spd, bldg:oat_1 .
bldg:rtu_1_oa_fr a brick1:Outdoor_Air_Flow_Rate .
bldg:rtu_1_oa_damper a brick1:Outdoor_Air_Damper .
bldg:rtu_1_econ_stpt a brick1:Economizer_Setpoint .
bldg:rtu_1_sf_spd a brick1:Supply_Air_Fan_Speed .
bldg:oat_1 a brick1:Outdoor_Air_Temperature_Sensor .

bldg:zone_1 a brick1:Zone ;
    brick1:hasPoint bldg:zone_1_fan_spd, bldg:zone_1_heating_sp, bldg:zone_1_cooling_sp,
        bldg:zone_1_temp, bldg:zone_1_flow, bldg:zone_1_count .
bldg:zone_1_fan_spd a brick1:Supply_Air_Flow_Sensor .
bldg:zone_1_flow a brick1:Supply_Air_Flow_Sensor .
bldg:zone_1_heating_sp a brick1:Heating_Temperature_Setpoint .
bldg:zone_1_cooling_sp a brick1:Cooling_Temperature_Setpoint .
bldg:zone_1_temp a brick1:Zone_Air_Temperature_Sensor .
bldg:zone_1_count a brick1:Occupant_Count .
"""


@pytest.mark.parametrize("backend", ["minimal", "rdflib"])
def test_obvious_aliases_are_accepted_with_a_caveat(backend):
    if backend == "rdflib":
        pytest.importorskip("rdflib")
    rep = brick_mapping_report(QUIRK_TTL, backend=backend)
    by = {p.point: p for p in rep.points}
    assert by["rtu_1_oa_fr"].role == Role.OA_AIRFLOW and by["rtu_1_oa_fr"].status == "alias"
    assert by["rtu_1_oa_damper"].role == Role.OA_DAMPER and by["rtu_1_oa_damper"].note
    assert by["rtu_1_sf_spd"].role == Role.SUPPLY_FAN_SPEED
    assert by["oat_1"].role == Role.OAT and "Outdoor_" in by["oat_1"].note
    assert by["rtu_1_econ_stpt"].status == "unmapped"  # no CAMBER role -- listed, not guessed
    assert by["zone_1_heating_sp"].role == Role.HEAT_SP  # a zone-owned heating setpoint
    assert by["zone_1_cooling_sp"].role == Role.COOL_SP
    assert by["zone_1_count"].role is None  # a head count is not binary occupancy
    assert rep.roles == roles_from_brick(QUIRK_TTL, backend=backend)


def test_percent_named_flow_point_is_never_mapped_to_a_cfm_role():
    roles = roles_from_brick(QUIRK_TTL, backend="minimal")
    assert "zone_1_fan_spd" not in roles  # typed a flow sensor, named a fan speed (%)
    assert roles["zone_1_flow"] == Role.AIRFLOW  # the same class with a flow-like name maps
    rep = brick_mapping_report(QUIRK_TTL, backend="minimal")
    p = next(x for x in rep.points if x.point == "zone_1_fan_spd")
    assert p.status == "ambiguous" and "speed/percent" in p.note


def test_report_counts_and_summary():
    rep = brick_mapping_report(QUIRK_TTL, backend="minimal")
    c = rep.counts()
    assert c["points"] == c["mapped"] + c["alias"] + c["ambiguous"] + c["unmapped"] == 11
    assert c["alias"] == 4 and c["ambiguous"] == 2 and c["unmapped"] == 1
    text = rep.summary()
    assert text.startswith("8 of 11 points mapped (4 via non-standard aliases)")
    assert "[ambiguous] Supply_Air_Flow_Sensor" in text and "[unmapped] Economizer_Setpoint" in text
    d = rep.as_dict()
    assert d["counts"] == c and {x["status"] for x in d["points"]} == {
        "mapped",
        "alias",
        "ambiguous",
        "unmapped",
    }


def test_summary_collapses_repeated_points():
    ttl = "@prefix b: <urn:x#> .\n@prefix brick: <https://brickschema.org/schema/Brick#> .\n"
    ttl += "".join(f"b:z{i}_fan_spd a brick:Supply_Air_Flow_Sensor .\n" for i in range(5))
    text = brick_mapping_report(ttl, backend="minimal").summary()
    assert "x5 (z0_fan_spd, z1_fan_spd, z2_fan_spd and 2 more)" in text


# --- real published LBNL models (only when the gitignored data is present) -------------------- #

_DATA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples", "_data"
)
_BOILER_ZIP = os.path.join(_DATA, "lbnl_boiler", "LBNL_FDD_Data_Sets_Boiler_Plant_ttl.zip")


@pytest.mark.skipif(not os.path.exists(_BOILER_ZIP), reason="LBNL boiler-plant Brick model absent")
def test_real_lbnl_boiler_plant_model():
    import zipfile

    with zipfile.ZipFile(_BOILER_ZIP) as z:
        ttl = z.read(z.namelist()[0]).decode()
    rep = brick_mapping_report(ttl, backend="minimal")
    assert rep.counts() == {"mapped": 17, "alias": 0, "ambiguous": 2, "unmapped": 3, "points": 22}
    assert all(p.brick_class == "On_Off_Status" for p in rep.with_status("ambiguous"))
    assert Role.BOILER_STATUS not in rep.roles.values()  # the enable is not firing status


def test_context_edges_resolve_or_explain():
    from camber.interop.brick import _context_role

    assert _context_role("p", "Valve_Position_Sensor", "Valve", "V")[0] is None
    assert _context_role("p", "Valve_Position_Sensor", "Chilled_Water_Valve", "V")[0] == (
        Role.COOL_VALVE
    )
    assert _context_role("p", "Damper_Position_Sensor", "Return_Damper", "RD") == (None, "")
    assert _context_role("p", "Speed_Status", "Chilled_Water_Pump", "P")[0] == Role.CHW_PUMP_SPEED
    assert _context_role("p", "Speed_Status", "Cooling_Tower_Fan", "F")[0] == Role.TOWER_FAN_SPEED
    assert _context_role("p", "Speed_Status", "Fan", "Return_Fan") == (None, "")
    assert _context_role("p", "Speed_Status", "Supply_Fan", "SF")[0] == Role.SUPPLY_FAN_SPEED
    assert _context_role("p", "Fan_On_Off_Status", "Fan", "Return_Fan") == (None, "")
    assert _context_role("p", "Run_Status", "Pump", "P")[0] == Role.PUMP_STATUS
    assert _context_role("p", "On_Off_Status", "Light", "L") == (None, "")
    assert _context_role("p", "Heating_Temperature_Setpoint", "Boiler", "B")[0] is None
    assert _context_role("p", "Heating_Temperature_Setpoint", "VAV", "V")[0] == Role.HEAT_SP
    assert _context_role("p", "Entering_Chilled_Water_Temperature_Sensor", "Chiller", "C")[0] == (
        Role.CHW_RETURN_TEMP
    )
    assert _context_role("p", "Entering_Hot_Water_Temperature_Sensor", "Boiler", "B")[0] == (
        Role.HW_RETURN_TEMP
    )
    assert _context_role("p", "Some_Other_Class", "AHU", "A") == (None, "")
