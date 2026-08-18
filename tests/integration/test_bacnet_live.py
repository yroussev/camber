"""End-to-end integration test: the bacpypes3 BACnet client vs. an in-memory simulated device.

Drives the REAL :class:`camber.ingest.bacnet_client.Bacpypes3Client` (its worker thread + a real
bacpypes3 ``Application``) against a bacpypes3 ``VirtualNetwork`` device — validating the live wire
path the fake-based unit tests cannot: Who-Is broadcast, ReadProperty ``object-list`` enumeration,
ReadPropertyMultiple shaping, and unit normalization against genuine ``EngineeringUnits``. Fully
in-memory (no sockets, no broadcast interface), so it is deterministic.

**Skipped unless** bacpypes3 is importable **and** ``CAMBER_BACNET_LIVE=1`` — so CI never runs it.
To run it::

    pip install "camber-toolkit[bacnet]"
    CAMBER_BACNET_LIVE=1 pytest tests/integration/test_bacnet_live.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

pytest.importorskip("bacpypes3")
if os.environ.get("CAMBER_BACNET_LIVE") != "1":
    pytest.skip(
        "set CAMBER_BACNET_LIVE=1 (with bacpypes3 installed) to run the live integration test",
        allow_module_level=True,
    )

from bacpypes3.app import Application  # noqa: E402
from bacpypes3.local.analog import AnalogValueObject  # noqa: E402
from bacpypes3.local.device import DeviceObject  # noqa: E402
from bacpypes3.pdu import Address  # noqa: E402
from bacpypes3.vlan import Node, VirtualNetwork  # noqa: E402

from camber.ingest.bacnet_client import Bacpypes3Client  # noqa: E402
from camber.ingest.bacnet_discovery import discover  # noqa: E402
from camber.interop.bacnet import mapping_from_bacnet  # noqa: E402
from camber.model.roles import Role  # noqa: E402

_KEEP = []  # keep each simulated device + network alive for the client's lifetime


def _build_scanner_over_vlan():
    """An async ``build_app`` that wires a sim device + a scanner onto one in-memory VLAN.

    Both apps live on the client's worker loop (bacpypes3 requires the app to be driven from the
    coroutine that built it). Returns the scanner ``Application``; the device answers Who-Is/reads.
    """

    async def build():
        net = VirtualNetwork(f"camber-test-{len(_KEEP)}")
        sensor = AnalogValueObject(
            objectIdentifier=("analog-value", 1),
            objectName="SupplyAirTemp",
            presentValue=72.5,
            units="degrees-fahrenheit",
        )
        device = Application.from_object_list(
            [
                DeviceObject(
                    objectIdentifier=("device", 1000), objectName="sim-device", vendorIdentifier=999
                ),
                sensor,
            ]
        )
        device.nsap.bind(Node(Address(2), lan=net), address=Address(2))
        scanner = Application.from_object_list(
            [
                DeviceObject(
                    objectIdentifier=("device", 599), objectName="camber", vendorIdentifier=555
                )
            ]
        )
        scanner.nsap.bind(Node(Address(1), lan=net), address=Address(1))
        _KEEP.append((net, device))
        return scanner

    return build


@pytest.fixture
def client():
    c = Bacpypes3Client(build_app=_build_scanner_over_vlan(), address="2", timeout=8)
    try:
        yield c
    finally:
        c.close()


def test_who_is_finds_the_simulated_device(client):
    # real Who-Is broadcast (in-memory) + I-Am shaping against a genuine bacpypes3 PDU
    assert client.who_is() == [(1000, "2", 999)]


def test_discover_enumerates_objects_and_bootstraps_a_mapping(client):
    devices = discover(client)  # real object-list + ReadPropertyMultiple (the flat-list fix)
    assert len(devices) == 1 and devices[0].instance == 1000
    objs = [o for d in devices for o in d.objects]
    assert ("analogValue", 1) in [o.object_id for o in objs]  # dashed 'analog-value' -> camelCase
    mapping = mapping_from_bacnet(objs)
    # a real EngineeringUnits (dashed 'degrees-fahrenheit') normalizes and resolves the role
    assert mapping.role_of("SupplyAirTemp") == Role.SUPPLY_AIR_TEMP


def test_discover_addresses_by_directed_who_is(client):
    # the segmented/cloud-network path: a directed (unicast) Who-Is to a known address, no broadcast
    from camber.ingest.bacnet_discovery import discover_addresses

    devices = discover_addresses(client, ["2"])
    assert len(devices) == 1 and devices[0].instance == 1000
    assert ("analogValue", 1) in [o.object_id for o in devices[0].objects]


def test_read_present_value_round_trips(client):
    assert float(client.read_present_value(("analogValue", 1))) == 72.5


def test_trend_log_records_shape_into_a_series():
    """Validate Trend-Log shaping against **real** bacpypes3 ``LogRecord`` objects.

    bacpypes3 ships no ``local.trendlog``, so a generic ``TrendLogObject`` won't answer ByPosition
    ReadRange over the VLAN — the ReadRange *transport* is validated against a real
    trend-log-capable device (see docs/INGEST-PROTOCOLS.md). This covers the risky CAMBER-side part:
    the odd real timestamp format (``'2025-1-1 wed 00:00:00.00'``) and ``logDatum`` extraction.
    """
    from bacpypes3.basetypes import Date, DateTime, LogRecord, LogRecordLogDatum, Time

    from camber.ingest.bacnet import trendlog_to_series
    from camber.ingest.bacnet_client import _log_record_to_pair

    records = [
        LogRecord(
            timestamp=DateTime(date=Date("2025-01-01"), time=Time("00:00:00")),
            logDatum=LogRecordLogDatum(realValue=70.0),
        ),
        LogRecord(
            timestamp=DateTime(date=Date("2025-01-01"), time=Time("01:00:00")),
            logDatum=LogRecordLogDatum(realValue=71.5),
        ),
    ]
    series = trendlog_to_series([_log_record_to_pair(r) for r in records])
    assert list(series.values) == [70.0, 71.5] and len(series) == 2
