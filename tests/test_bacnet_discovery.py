"""Tests for BACnet discovery (camber.ingest.bacnet_discovery).

All network I/O is a synthetic in-memory fake `DiscoveryClient` — no bacpypes3, no network. The
read-only contract is separately enforced by the AST guard in test_ingest_protocols.py.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.ingest import bacnet_discovery as d  # noqa: E402

# object_id -> descriptive metadata the fake device returns for ReadPropertyMultiple.
_META = {
    ("device", 100): {"object-name": "AHU-Controller-100"},
    ("analogInput", 1): {
        "object-name": "SupplyAirTemp",
        "units": "degreesFahrenheit",
        "description": "SA temp",
    },
    ("analogInput", 2): {"object-name": "ChwFlow", "units": "usGallonsPerMinute"},
    ("binaryInput", 1): {"object-name": "SupplyFanStatus", "units": "noUnits"},
    ("multiStateValue", 1): {"object-name": "CompressorStage", "units": "noUnits"},
    ("trendLog", 3): {"object-name": "ZoneCO2", "units": "partsPerMillion", "present-value": 640},
}


class _FakeDiscoveryClient:
    """Canned Who-Is/object-list/metadata; records nothing but reads."""

    def __init__(self, iam=None):
        self._iam = iam or [(100, "10.0.0.5", 999)]

    def who_is(self, low=None, high=None):
        return list(self._iam)

    def read_object_list(self, address, device_instance):
        return [
            ("device", 100),
            ("analogInput", 1),
            ("analogInput", 2),
            ("binaryInput", 1),
            ("multiStateValue", 1),
            ("trendLog", 3),
        ]

    def read_object_metadata(self, address, object_id, props=d.DISCOVERY_READ_PROPERTIES):
        return dict(_META.get(object_id, {}))


# --------------------------------------------------------------------------- read-only allowlist


def test_discovery_services_are_read_only():
    for svc in d.DISCOVERY_SERVICES:
        assert "Write" not in svc and "write" not in svc
    assert "ReadProperty" in d.DISCOVERY_SERVICES and "Who-Is" in d.DISCOVERY_SERVICES
    # descriptive read properties only — no writable command target
    assert "present-value" in d.DISCOVERY_READ_PROPERTIES
    assert d.TREND_OBJECT_TYPES == frozenset({"trendLog", "trendLogMultiple"})


# --------------------------------------------------------------------------- discovery


def test_discover_builds_devices_and_objects():
    devs = d.discover(_FakeDiscoveryClient())
    assert len(devs) == 1
    dev = devs[0]
    assert dev.instance == 100 and dev.address == "10.0.0.5" and dev.vendor_id == 999
    assert dev.object_name == "AHU-Controller-100"  # picked up from the device object
    assert len(dev.objects) == 6
    trend = [o for o in dev.objects if o.is_trend]
    assert [o.object_id for o in trend] == [
        ("trendLog", 3)
    ]  # only the trendLog is a history source
    assert all(o.present_value is None for o in dev.objects)  # off by default


def test_discover_reads_present_value_when_asked():
    devs = d.discover(_FakeDiscoveryClient(), read_present_value=True)
    co2 = next(o for o in devs[0].objects if o.object_id == ("trendLog", 3))
    assert co2.present_value == 640


def test_discover_accepts_dict_iam():
    client = _FakeDiscoveryClient(iam=[{"device_instance": 7, "address": "a", "vendor_id": 5}])
    devs = d.discover(client)
    assert devs[0].instance == 7 and devs[0].vendor_id == 5


def test_discover_vendor_bridge_runs_once_and_tolerates_a_raise():
    calls = {"n": 0}

    def spy():
        calls["n"] += 1

    d.discover(_FakeDiscoveryClient(), vendor_bridge=spy)
    assert calls["n"] == 1

    def boom():
        raise RuntimeError("bridge failure must not break discovery")

    devs = d.discover(_FakeDiscoveryClient(), vendor_bridge=boom)
    assert len(devs) == 1  # discovery still completed


# --------------------------------------------------------------------------- conversions


def test_discovery_to_points_trend_only_and_all():
    devs = d.discover(_FakeDiscoveryClient())
    trend = d.discovery_to_points(devs)  # trend_only=True by default
    assert [(p.name, p.object_id) for p in trend] == [("ZoneCO2", ("trendLog", 3))]
    assert trend[0].unit == "partsPerMillion"

    allpts = d.discovery_to_points(devs, trend_only=False)
    assert len(allpts) == 6
    # a nameless object would fall back to <type>_<instance>; here all have names
    assert "SupplyAirTemp" in [p.name for p in allpts]


def test_discovery_to_inventory_rows():
    devs = d.discover(_FakeDiscoveryClient())
    rows = d.to_rows(d.discovery_to_inventory(devs))
    assert len(rows) == 6
    sat = next(r for r in rows if r["object_name"] == "SupplyAirTemp")
    assert sat["object_type"] == "analogInput" and sat["unit"] == "degreesFahrenheit"
    assert sat["is_trend"] is False
    assert next(r for r in rows if r["object_name"] == "ZoneCO2")["is_trend"] is True


def test_point_name_falls_back_when_unnamed():
    class _Nameless(_FakeDiscoveryClient):
        def read_object_metadata(self, address, object_id, props=d.DISCOVERY_READ_PROPERTIES):
            return {"units": "degreesFahrenheit"} if object_id == ("trendLog", 3) else {}

    devs = d.discover(_Nameless())
    trend = d.discovery_to_points(devs)
    assert trend[0].name == "trendLog_3"


# --------------------------------------------------------------------------- import-light


def test_module_imports_without_bacpypes3():
    # This module and its conversions must not require the [bacnet] stack to import/shape records.
    import importlib

    m = importlib.import_module("camber.ingest.bacnet_discovery")
    assert hasattr(m, "discover") and hasattr(m, "DiscoveryClient")
