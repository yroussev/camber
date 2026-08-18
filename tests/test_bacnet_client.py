"""Tests for the bacpypes3-backed BACnet client (camber.ingest.bacnet_client).

The client is exercised against a **fake async app** that mimics the bacpypes3 shapes pinned
in Phase 0 (ObjectIdentifier `tuple()` → (ObjectType, instance); I-Am `.iAmDeviceIdentifier` /
`.pduSource` / `.vendorID`; RPM rows; ReadRange LogRecords; AnyAtomic.get_value; dashed spelling).
No bacpypes3, no network — the real Application factory is `# pragma: no cover`.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.ingest import bacnet_client as bc  # noqa: E402
from camber.ingest.bacnet import trendlog_to_series  # noqa: E402
from camber.ingest.bacnet_discovery import DiscoveryClient, discover  # noqa: E402
from camber.interop.bacnet import mapping_from_bacnet, normalize_bacnet_unit  # noqa: E402
from camber.model.roles import Role  # noqa: E402


# --- fakes mimicking bacpypes3 response objects (dashed spelling, as verified in Phase 0) ---
class _AnyAtomic:
    def __init__(self, v):
        self._v = v

    def get_value(self):
        return self._v


class _Units:  # EngineeringUnits: str() is dashed, .name is None
    def __init__(self, dashed):
        self._d = dashed

    def __str__(self):
        return self._d


class _IAm:
    def __init__(self, inst, addr, vendor):
        self.iAmDeviceIdentifier = ("device", inst)  # tuple() -> ("device", inst)
        self.pduSource = addr
        self.vendorID = vendor


class _Datum:  # mimics bacpypes3 LogRecordLogDatum (realValue member set; others read as None)
    def __init__(self, real):
        self.realValue = real


class _LogRec:
    def __init__(self, ts, val):
        self.timestamp = ts
        self.logDatum = _Datum(val)


class _FakeApp:
    def __init__(self):
        self.closed = False

    async def who_is(self, low, high, address=None):
        # a directed Who-Is returns the device at the queried address; broadcast uses a default
        return [_IAm(100, str(address) if address is not None else "10.0.0.5", 999)]

    async def read_property(self, address, objid, prop, array_index=None):
        if prop == "object-list":
            return [("analog-input", 1), ("trend-log", 3)]  # tuple() -> (dashed, inst)
        if prop == "present-value":
            return _AnyAtomic(72.5)
        return None

    async def read_property_multiple(self, address, parameter_list, vendor_info=None):
        # bacpypes3's real convention is a FLAT list: [object_id, [prop, ...]]
        oid, props = parameter_list[0], parameter_list[1]
        values = {
            "object-name": "SupplyAirTemp",
            "units": _Units("degrees-fahrenheit"),
            "description": "SA temp",
            "present-value": _AnyAtomic(72.5),
        }
        return [(oid, p, None, values.get(p)) for p in props]

    async def read_range(self, address, objid, prop, arr_index=None, range_params=None):
        return [_LogRec("2025-01-01T00:00:00", 70.0), _LogRec("2025-01-01T01:00:00", 71.5)]

    async def close(self):
        self.closed = True


def _client(**kw):
    return bc.Bacpypes3Client(_FakeApp(), timeout=5.0, **kw)


# --------------------------------------------------------------------------- spelling helpers


def test_object_type_spelling_roundtrips():
    assert bc._to_camel("analog-input") == "analogInput"
    assert bc._to_camel("trend-log-multiple") == "trendLogMultiple"
    assert bc._to_dashed("analogInput") == "analog-input"
    assert bc._to_dashed("trendLog") == "trend-log"
    assert bc._oid_str(("multiStateValue", 4)) == "multi-state-value,4"


def test_normalize_unit_accepts_dashed_bacpypes3_form():
    # the Phase-0 finding: bacpypes3 renders units dashed
    assert normalize_bacnet_unit("degrees-fahrenheit") == "degf"
    assert normalize_bacnet_unit("parts-per-million") == "ppm"
    assert normalize_bacnet_unit("degreesFahrenheit") == "degf"  # camelCase still works
    assert normalize_bacnet_unit(_Units("degrees-celsius")) == "degc"


# --------------------------------------------------------------------------- client methods


def test_who_is_shapes_iam():
    with _client() as c:
        assert c.who_is() == [(100, "10.0.0.5", 999)]


def test_read_object_list_camelcases_types():
    with _client() as c:
        assert c.read_object_list("10.0.0.5", 100) == [("analogInput", 1), ("trendLog", 3)]


def test_read_object_metadata_unwraps_and_keeps_dashed_units():
    with _client() as c:
        meta = c.read_object_metadata("10.0.0.5", ("analogInput", 1))
    assert meta["object-name"] == "SupplyAirTemp"
    assert str(meta["units"]) == "degrees-fahrenheit"
    assert meta["present-value"] == 72.5  # AnyAtomic unwrapped


def test_read_present_value_unwraps_anyatomic():
    with _client(address="10.0.0.5") as c:
        assert c.read_present_value(("analogInput", 1)) == 72.5


def test_read_trend_log_pairs_feed_trendlog_to_series():
    with _client(address="10.0.0.5") as c:
        pairs = c.read_trend_log(("trendLog", 3))
    assert pairs == [("2025-01-01T00:00:00", 70.0), ("2025-01-01T01:00:00", 71.5)]
    s = trendlog_to_series(pairs)  # the CAMBER read path consumes these
    assert list(s.values) == [70.0, 71.5] and len(s) == 2


# --------------------------------------------------------------------------- protocol + end-to-end


def test_client_satisfies_the_discovery_protocol():
    with _client() as c:
        assert isinstance(c, DiscoveryClient)  # runtime_checkable


def test_discover_addresses_uses_directed_who_is():
    from camber.ingest.bacnet_discovery import discover_addresses

    with _client() as c:
        devices = discover_addresses(c, ["10.0.0.9", "10.0.0.10"])
    assert [d.address for d in devices] == ["10.0.0.9", "10.0.0.10"]  # one per known address
    assert [o.object_id for o in devices[0].objects] == [("analogInput", 1), ("trendLog", 3)]


def test_discover_runs_against_the_client_and_bootstraps_a_mapping():
    # the real discover() + real interop, only the network faked
    with _client() as c:
        devices = discover(c)
    assert len(devices) == 1 and devices[0].instance == 100
    objs = [o for d in devices for o in d.objects]
    assert [o.object_id for o in objs] == [("analogInput", 1), ("trendLog", 3)]
    mapping = mapping_from_bacnet(objs)
    # SupplyAirTemp + degrees-fahrenheit (dashed) resolves through the enhanced unit normalization
    assert mapping.role_of("SupplyAirTemp") == Role.SUPPLY_AIR_TEMP


# --------------------------------------------------------------------------- lifecycle


def test_close_shuts_down_worker_and_closes_the_app():
    app = _FakeApp()
    c = bc.Bacpypes3Client(app, timeout=5.0)
    assert c.who_is() == [(100, "10.0.0.5", 999)]
    c.close()
    assert app.closed is True  # the worker awaited the app's async close on shutdown


def test_requires_exactly_one_of_app_or_build_app():
    with pytest.raises(ValueError):
        bc.Bacpypes3Client()  # neither
    with pytest.raises(ValueError):
        bc.Bacpypes3Client(_FakeApp(), build_app=lambda: None)  # both


def test_build_app_failure_surfaces_to_the_constructor():
    async def bad_build():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        bc.Bacpypes3Client(build_app=bad_build, timeout=2.0)


def test_operation_error_propagates_to_the_caller():
    class _Boom:
        async def who_is(self, low, high, address=None):
            raise ValueError("nope")

    with bc.Bacpypes3Client(_Boom(), timeout=2.0) as c:
        with pytest.raises(ValueError, match="nope"):
            c.who_is()


def test_calls_after_close_raise():
    c = bc.Bacpypes3Client(_FakeApp(), timeout=2.0)
    c.close()
    with pytest.raises(RuntimeError, match="closed"):
        c.who_is()


# --------------------------------------------------------------------------- config surface


def test_config_from_mapping_and_defaults():
    cfg = bc.BacnetClientConfig.from_mapping(
        {"local_device_id": 42, "device_range": [1, 100], "unknown_key": "ignored"}
    )
    assert cfg.local_device_id == 42
    assert cfg.device_range == (1, 100)  # list coerced to tuple
    assert cfg.local_object_name == "camber"  # default
    assert cfg.to_mapping()["device_range"] == [1, 100]  # round-trips as a list


def test_config_known_addresses_round_trips():
    cfg = bc.BacnetClientConfig.from_mapping({"known_addresses": ["10.0.0.5", "10.0.0.6"]})
    assert cfg.known_addresses == ("10.0.0.5", "10.0.0.6")  # list -> tuple
    assert cfg.to_mapping()["known_addresses"] == ["10.0.0.5", "10.0.0.6"]  # back to a list


def test_config_from_json_file(tmp_path):
    import json

    p = tmp_path / "bacnet.json"
    p.write_text(json.dumps({"local_address": "0.0.0.0/24:47808", "timeout": 20.0}))
    cfg = bc.BacnetClientConfig.from_file(str(p))
    assert cfg.local_address == "0.0.0.0/24:47808" and cfg.timeout == 20.0


def test_config_from_yaml_file_or_clear_error(tmp_path):
    p = tmp_path / "bacnet.yaml"
    p.write_text("local_device_id: 7\ntimeout: 15\n")
    try:
        import yaml  # noqa: F401
    except Exception:
        with pytest.raises(ImportError, match="PyYAML"):
            bc.BacnetClientConfig.from_file(str(p))
    else:
        cfg = bc.BacnetClientConfig.from_file(str(p))
        assert cfg.local_device_id == 7 and cfg.timeout == 15


# --------------------------------------------------------------------------- import-light


def test_module_imports_without_bacpypes3():
    import importlib

    m = importlib.import_module("camber.ingest.bacnet_client")
    assert hasattr(m, "bacnet_read_client") and hasattr(m, "discover_default")


def test_cli_bacnet_discover_parses():
    # the CLI config channel: parser wiring is testable without touching a network
    from camber.cli import _build_parser

    args = _build_parser().parse_args(
        ["bacnet-discover", "--config", "x.yml", "--range-low", "1", "--range-high", "9"]
    )
    assert args.cmd == "bacnet-discover" and args.config == "x.yml"
    assert args.range_low == 1 and args.range_high == 9 and callable(args.func)


def test_cli_bacnet_discover_device_flag_is_repeatable():
    from camber.cli import _build_parser

    args = _build_parser().parse_args(
        ["bacnet-discover", "--device", "10.0.0.5", "--device", "10.0.0.6"]
    )
    assert args.device == ["10.0.0.5", "10.0.0.6"]
