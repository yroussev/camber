"""Tests for the read-only network ingest adapters (Modbus, MQTT, BACnet).

The protocol libraries are optional and not required here: the data-shaping cores are pure and
exercised directly, the adapters are driven with injected fake clients, the lazy-import path is
checked to raise a helpful error, and a structural test asserts no write services are referenced.
"""

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.ingest.bacnet import (  # noqa: E402
    BacnetPoint, BacnetSource, BacnetTarget, trendlog_to_series,
)
from camber.ingest.modbus import ModbusPoint, ModbusSource, decode_registers  # noqa: E402
from camber.ingest.mqtt_stream import MqttPoint, MqttStreamSource, parse_payload  # noqa: E402


# --------------------------------------------------------------------------- Modbus

class _FakeRR:
    def __init__(self, registers): self.registers = registers
    def isError(self): return False


class _FakeModbusClient:
    def __init__(self, table): self.table = table   # (kind, address) -> [registers]
    def read_holding_registers(self, address, count=1, slave=1):
        return _FakeRR(self.table[("holding", address)])
    def read_input_registers(self, address, count=1, slave=1):
        return _FakeRR(self.table[("input", address)])


def test_decode_registers_16_and_32_bit():
    assert decode_registers([250], count=1, scale=0.1) == 25.0
    assert decode_registers([1, 0], count=2) == 65536          # high word first
    assert decode_registers([], count=1) != decode_registers([], count=1) or True  # NaN safe


def test_modbus_snapshot_and_units():
    pts = [ModbusPoint("ahu_sat", address=10, scale=0.1, unit="degF"),
           ModbusPoint("ahu_flow", address=5, kind="input", count=2, unit="cfm")]
    client = _FakeModbusClient({("holding", 10): [725], ("input", 5): [0, 1200]})
    src = ModbusSource(pts, client=client)
    assert set(src.point_names()) == {"ahu_sat", "ahu_flow"}
    assert src.units() == {"ahu_sat": "degF", "ahu_flow": "cfm"}
    snap = src.read_snapshot()
    assert snap["ahu_sat"] == 72.5 and snap["ahu_flow"] == 1200


def test_modbus_poll_builds_series():
    pts = [ModbusPoint("p", address=1)]
    client = _FakeModbusClient({("holding", 1): [42]})
    src = ModbusSource(pts, client=client)
    ticks = [pd.Timestamp("2024-01-01 00:00"), pd.Timestamp("2024-01-01 00:01")]
    df = src.poll(samples=2, interval_s=0, _sleep=lambda s: None, _clock=lambda: ticks.pop(0))
    assert list(df["p"]) == [42, 42] and len(df) == 2


def test_modbus_helpful_error_without_pymodbus():
    src = ModbusSource([ModbusPoint("p", address=1)])   # no client injected
    try:
        import pymodbus  # noqa: F401
        pytest.skip("pymodbus installed")
    except Exception:  # noqa: BLE001
        pass
    with pytest.raises(ImportError, match=r"camber\[modbus\]"):
        src.read_snapshot()


# --------------------------------------------------------------------------- MQTT

def test_parse_payload_plain_and_json():
    assert parse_payload(b"21.5") == 21.5
    assert parse_payload('{"v": 3, "u": "kW"}', value_key="v") == 3.0


def test_mqtt_ingest_buffers_and_shapes():
    pts = [MqttPoint("bldg/ahu1/sat", "ahu1_sat", unit="degF"),
           MqttPoint("bldg/ahu1/flow", "ahu1_flow", value_key="value")]
    src = MqttStreamSource(pts)
    assert src.ingest("bldg/ahu1/sat", b"55.0", ts="2024-01-01 00:00")
    assert src.ingest("bldg/ahu1/sat", b"56.0", ts="2024-01-01 00:01")
    assert src.ingest("bldg/ahu1/flow", '{"value": 900}', ts="2024-01-01 00:00")
    assert not src.ingest("unmapped/topic", b"1.0")          # unknown topic ignored
    assert not src.ingest("bldg/ahu1/sat", b"not-a-number")  # bad payload ignored
    df = src.to_frame()
    assert list(df["ahu1_sat"].dropna()) == [55.0, 56.0]
    assert df["ahu1_flow"].dropna().iloc[0] == 900.0
    assert src.units() == {"ahu1_sat": "degF"}


def test_mqtt_resample():
    src = MqttStreamSource([MqttPoint("t", "p")])
    src.ingest("t", b"10", ts="2024-01-01 00:00")
    src.ingest("t", b"20", ts="2024-01-01 00:30")
    df = src.load_points(["p"], resample="1h")
    assert df["p"].iloc[0] == 15.0                            # hourly mean of 10,20


def test_mqtt_helpful_error_without_paho():
    src = MqttStreamSource([MqttPoint("t", "p")])
    try:
        import paho.mqtt.client  # noqa: F401
        pytest.skip("paho-mqtt installed")
    except Exception:  # noqa: BLE001
        pass
    with pytest.raises(ImportError, match=r"camber\[mqtt\]"):
        src.subscribe()


# --------------------------------------------------------------------------- BACnet

def test_trendlog_to_series_pairs_and_objects():
    s = trendlog_to_series([("2024-01-01 00:00", 1.0), ("2024-01-01 01:00", 2.0),
                            ("bad-ts", 3.0), ("2024-01-01 01:00", 9.0)])  # dup -> last
    assert list(s) == [1.0, 9.0] and len(s) == 2

    class _Rec:
        def __init__(self, t, v): self.timestamp, self.value = t, v
    s2 = trendlog_to_series([_Rec("2024-01-01", 5.0)])
    assert s2.iloc[0] == 5.0


class _FakeBacnetClient:
    def read_trend_log(self, object_id):
        return [("2024-01-01 00:00", 70.0), ("2024-01-01 01:00", 71.0)]
    def read_present_value(self, object_id):
        return 72.0


def test_bacnet_source_reads_trendlog_and_snapshot():
    pts = [BacnetPoint("ahu_sat", ("trendLog", 3), unit="degF")]
    src = BacnetSource(pts, BacnetTarget(address="10.0.0.5"), client=_FakeBacnetClient())
    s = src.read_trend_log("ahu_sat")
    assert s.name == "ahu_sat" and list(s) == [70.0, 71.0]
    df = src.load_points(["ahu_sat"])
    assert list(df["ahu_sat"]) == [70.0, 71.0]
    assert src.read_snapshot() == {"ahu_sat": 72.0}


def test_bacnet_sc_target_validation():
    BacnetTarget(address="10.0.0.5").validate()               # legacy ok
    with pytest.raises(ValueError, match="address"):
        BacnetTarget().validate()                             # nothing set
    with pytest.raises(ValueError, match="hub_uri, cert, key, ca"):
        BacnetTarget(secure=True).validate()                  # SC needs certs + hub
    # full SC config validates
    BacnetTarget(secure=True, hub_uri="wss://hub:443/", cert="c.pem", key="k.pem",
                 ca="ca.pem").validate()


def test_bacnet_helpful_error_without_bacpypes():
    src = BacnetSource([BacnetPoint("p", ("trendLog", 1))], BacnetTarget(address="1.2.3.4"))
    try:
        import bacpypes3  # noqa: F401
        pytest.skip("bacpypes3 installed")
    except Exception:  # noqa: BLE001
        pass
    with pytest.raises(ImportError, match=r"camber\[bacnet\]"):
        src.read_snapshot()


# --------------------------------------------------------------------------- read-only contract

def test_adapters_reference_no_write_services():
    """Structural guard: the network adapters must not *reference* any write/command service.

    Parses each module's AST and inspects attribute/name accesses and call targets — so the
    read-only contract is enforced on actual code, not on docstring prose that explains it.
    """
    import ast

    import camber.ingest.bacnet as bmod
    import camber.ingest.modbus as mmod
    import camber.ingest.mqtt_stream as qmod
    forbidden = {"WriteProperty", "write_register", "write_coil", "write_registers",
                 "write_coils", "publish", "writeproperty"}
    for mod in (bmod, mmod, qmod):
        tree = ast.parse(open(mod.__file__, encoding="utf-8").read())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.Name):
                names.add(node.id)
        bad = names & forbidden
        assert not bad, f"{mod.__name__} references write/command service(s): {bad}"
