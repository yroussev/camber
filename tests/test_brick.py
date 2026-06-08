"""Tests for Brick Schema interop (interop.brick)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.interop.brick import (  # noqa: E402
    mapping_from_brick, parse_triples, roles_from_brick,
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
    assert roles["CHWC_VLV"] == Role.COOL_VALVE     # owned by a chilled-water coil
    assert roles["HC_VLV"] == Role.HEAT_VALVE       # owned by a hot-water coil


def test_damper_and_fan_context():
    roles = roles_from_brick(TTL)
    assert roles["OA_DMPR"] == Role.OA_DAMPER       # owned by the outside damper
    assert roles["SF_SPD"] == Role.SUPPLY_FAN_SPEED  # owned by the supply fan


def test_command_points_skipped():
    roles = roles_from_brick(TTL)
    assert "CHWC_VLV_DM" not in roles               # a command, not a measured point


def test_mapping_from_brick_is_usable():
    mp = mapping_from_brick(TTL)
    assert mp.role_of("CHWC_VLV") == Role.COOL_VALVE
    assert mp.role_of("ma_temp") == Role.MIXED_AIR_TEMP   # case-insensitive lookup


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
    assert r["CHWC_VLV"] == Role.COOL_VALVE          # resolved via the coil context
    # the minimal parser only understands `a`, not `rdf:type`, so it sees no types
    assert roles_from_brick(ttl, backend="minimal") != r


def test_rdflib_backend_required_when_forced(monkeypatch):
    import camber.interop.brick as b
    monkeypatch.setattr(b, "_have_rdflib", lambda: False)
    with pytest.raises(ImportError):
        b.roles_from_brick("x", backend="rdflib")
