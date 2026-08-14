"""Tests for the BACnet role-mapping adapter (camber.interop.bacnet) and the optional
ace-bacnet-devices bridge (camber.interop.bacnet_vendor).

Discovered objects are duck-typed stand-ins; the vendor catalog is an injected fake, so nothing here
needs bacpypes3 or ace-bacnet-devices installed.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.interop import bacnet as ib  # noqa: E402
from camber.interop import bacnet_vendor as bv  # noqa: E402
from camber.model.mapping import MappingProvider  # noqa: E402
from camber.model.roles import Role  # noqa: E402


class _Obj:
    """Minimal discovered-object stand-in (object_name / object_id / units)."""

    def __init__(self, name, object_type, instance=1, units=""):
        self.object_name = name
        self.object_id = (object_type, instance)
        self.units = units


# --------------------------------------------------------------------------- unit normalization


def test_normalize_bacnet_unit_name_int_and_unknown():
    assert ib.normalize_bacnet_unit("degreesFahrenheit") == "degf"
    assert ib.normalize_bacnet_unit("partsPerMillion") == "ppm"
    assert ib.normalize_bacnet_unit(64) == "degf"  # standard EngineeringUnits int code
    assert ib.normalize_bacnet_unit("64") == "degf"  # digit string

    class _Enum:
        name = "kilowatts"

    assert ib.normalize_bacnet_unit(_Enum()) == "kw"  # enum object exposing .name
    assert ib.normalize_bacnet_unit("noUnits") == ""  # unknown -> empty, no raise
    assert ib.normalize_bacnet_unit(None) == "" and ib.normalize_bacnet_unit(True) == ""


# --------------------------------------------------------------------------- name -> role


def test_roles_from_bacnet_by_name_and_object_type():
    objs = [
        _Obj("SupplyAirTemp", "analogInput", 1, "degreesFahrenheit"),
        _Obj("SupplyFanStatus", "binaryInput", 1),
        _Obj("CompressorStage", "multiStateValue", 1),
        _Obj("ZoneCO2", "analogInput", 2, "partsPerMillion"),
    ]
    roles = ib.roles_from_bacnet(objs)
    assert roles["SupplyAirTemp"] == Role.SUPPLY_AIR_TEMP
    assert roles["SupplyFanStatus"] == Role.SUPPLY_FAN_STATUS  # binary -> status family
    assert roles["CompressorStage"] == Role.COMPRESSOR_STAGE  # multistate -> stage family
    assert roles["ZoneCO2"] == Role.CO2  # ppm unit constraint


def test_object_type_family_prefix_fallbacks():
    # non-standard object-type strings still classify by prefix; an unknown container (trendLog)
    # has no family, so the unit carries it.
    objs = [
        _Obj("SupplyAirTemp", "analogCustom", 1, "degreesFahrenheit"),  # analog* -> numeric
        _Obj("SupplyFanStatus", "binaryCustom", 1),  # binary* -> status
        _Obj("CompressorStage", "multistateCustom", 1),  # multistate* -> stage
        _Obj("ZoneCO2", "trendLog", 1, "partsPerMillion"),  # container, no family -> unit decides
    ]
    roles = ib.roles_from_bacnet(objs)
    assert roles["SupplyAirTemp"] == Role.SUPPLY_AIR_TEMP
    assert roles["SupplyFanStatus"] == Role.SUPPLY_FAN_STATUS
    assert roles["CompressorStage"] == Role.COMPRESSOR_STAGE
    assert roles["ZoneCO2"] == Role.CO2


def test_unit_constrains_an_ambiguous_analog():
    # "Flow" alone is ambiguous; the gpm unit points it at chilled-water flow.
    objs = [_Obj("Flow", "analogInput", 1, "usGallonsPerMinute")]
    roles = ib.roles_from_bacnet(objs, min_confidence=0.4)
    assert roles.get("Flow") == Role.CHW_FLOW


def test_operator_mapping_override_wins():
    mapping = MappingProvider.from_dict({"aliases": {"WeirdTag": "oat"}})
    roles = ib.roles_from_bacnet(
        [_Obj("WeirdTag", "analogInput", 1, "degreesFahrenheit")], mapping=mapping
    )
    assert roles["WeirdTag"] == Role.OAT  # operator alias beats the suggester


def test_low_confidence_objects_are_left_unresolved():
    roles = ib.roles_from_bacnet([_Obj("XZ9", "analogInput", 1, "noUnits")], min_confidence=0.9)
    assert "XZ9" not in roles  # nothing confident -> omitted, like roles_from_haystack


def test_nameless_object_is_skipped():
    assert ib.roles_from_bacnet([_Obj("", "analogInput", 1, "degreesFahrenheit")]) == {}


def test_mapping_from_bacnet_roundtrips():
    objs = [_Obj("SupplyAirTemp", "analogInput", 1, "degreesFahrenheit")]
    mp = ib.mapping_from_bacnet(objs)
    assert isinstance(mp, MappingProvider)
    assert mp.role_of("SupplyAirTemp") == Role.SUPPLY_AIR_TEMP


# --------------------------------------------------------------------------- review (unmapped)


def test_review_bacnet_suggests_by_unit():
    objs = [_Obj("Meter42", "analogInput", 1, "kilowatts")]
    out = ib.review_bacnet(objs, MappingProvider())
    assert out["n_unmapped"] == 1
    top = out["suggestions"]["Meter42"][0]
    assert top.role == Role.POWER.value and "unit" in top.rationale


def test_review_bacnet_accepts_optional_series():
    import numpy as np
    import pandas as pd

    idx = pd.date_range("2025-01-01", periods=48, freq="1h")
    series = {"RoomTemp": pd.Series(np.full(48, 72.0), index=idx)}
    objs = [_Obj("RoomTemp", "analogInput", 1, "degreesFahrenheit")]
    out = ib.review_bacnet(objs, MappingProvider(), series_by_name=series)
    assert out["n_unmapped"] == 1  # unmapped, but now scored with the series too


# --------------------------------------------------------------------------- import-light


def test_interop_bacnet_imports_without_optional_libs():
    import importlib

    importlib.import_module("camber.interop.bacnet")
    importlib.import_module("camber.interop.bacnet_vendor")


# --------------------------------------------------------------------------- vendor bridge


def test_vendor_bridge_graceful_without_the_library():
    # the ace-bacnet-devices extra is not installed in this environment
    assert bv.available_vendors() == ()
    assert bv.install_vendor_decoders(required=False) == []
    with pytest.raises(ImportError, match=r"bacnet-vendor"):
        bv.install_vendor_decoders(required=True)


def _fake_catalog():
    def prim(n):
        return type("Prim", (), {"name": n})()

    def prop(name, desc, primitive):
        return type(
            "P",
            (),
            {
                "name": name,
                "description": desc,
                "datatype": type("DS", (), {"primitive": prim(primitive)})(),
            },
        )()

    return type(
        "Cat",
        (),
        {
            "object_extensions": [
                type(
                    "OT",
                    (),
                    {
                        "properties": [
                            prop("serial-number", "Device serial number", "CHARACTER_STRING"),
                            prop("product-type", "Product type code", "UNSIGNED"),
                            prop("BoilerStatus", "Proprietary boiler run status", "BOOLEAN"),
                        ]
                    },
                )()
            ],
            "object_types": [],
        },
    )()


def test_vendor_hint_tokens_from_injected_catalog():
    hints = bv.vendor_hint_tokens(catalog=_fake_catalog())
    assert hints["serial-number"] == "Device serial number"
    assert set(hints) == {"serial-number", "product-type", "BoilerStatus"}


def test_vendor_aliases_are_strict():
    aliases = bv.vendor_aliases(catalog=_fake_catalog())
    # only the clearly-named status property maps; serial-number / product-type map to nothing
    assert aliases == {"BoilerStatus": Role.BOILER_STATUS.value}


def test_vendor_aliases_feed_roles_from_bacnet():
    aliases = bv.vendor_aliases(catalog=_fake_catalog())
    obj = _Obj("BoilerStatus", "binaryInput", 1)
    roles = ib.roles_from_bacnet([obj], vendor_aliases=aliases)
    assert roles["BoilerStatus"] == Role.BOILER_STATUS
