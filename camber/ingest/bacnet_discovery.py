"""BACnet network discovery — read-only device/object enumeration ([bacnet] extra).

CAMBER's BACnet *read* adapter (:mod:`camber.ingest.bacnet`) reads values from a point list you
already have. This module builds that list: it discovers devices (Who-Is / I-Am), enumerates each
device's objects (ReadProperty ``object-list``), and reads each object's descriptive metadata
(``object-name`` / ``units`` / ``description``) — the raw material for a point inventory and a
bootstrapped point→Role mapping (:mod:`camber.interop.bacnet`).

**Read-only by construction.** Discovery uses only Who-Is / I-Am and ReadProperty /
ReadPropertyMultiple of the descriptive properties in :data:`DISCOVERY_READ_PROPERTIES`. It never
imports or wraps WriteProperty or any command/actuation service, so no code path here can change a
controller on an OT network. The read-only intent is asserted structurally by the ingest test suite.

**Injected client.** Like :class:`camber.ingest.bacnet.BacnetSource`, the network I/O is an injected
:class:`DiscoveryClient` — core builds no bacpypes3 app, so the orchestration and record-shaping are
testable without a network or the library. Build the real client per deployment (see
``docs/INGEST-PROTOCOLS.md``); register vendor proprietary-property decoders at *client construction
time* via :mod:`camber.interop.bacnet_vendor` (see the timing note there).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, runtime_checkable

#: Services discovery may use — all read-only or broadcast. Documented and asserted by tests.
DISCOVERY_SERVICES = ("Who-Is", "I-Am", "ReadProperty", "ReadPropertyMultiple")

#: Descriptive object properties discovery may read (never a command/writable target).
DISCOVERY_READ_PROPERTIES = (
    "object-list",
    "object-name",
    "object-identifier",
    "units",
    "description",
    "present-value",
)

#: Object types that carry their own timestamped history (a CAMBER per-point series source).
TREND_OBJECT_TYPES = frozenset({"trendLog", "trendLogMultiple"})


@dataclass
class DiscoveredObject:
    """One BACnet object found on a device, with its descriptive metadata.

    ``object_id`` is ``(object_type, instance)`` — the same shape as
    :class:`camber.ingest.bacnet.BacnetPoint`. ``present_value`` is ``None`` unless discovery ran
    with ``read_present_value=True``. ``is_trend`` marks a Trend-Log object (a history source).
    """

    device_instance: int
    address: str
    object_id: tuple
    object_name: str = ""
    units: str = ""
    description: str = ""
    present_value: Any | None = None
    is_trend: bool = False


@dataclass
class DiscoveredDevice:
    """A BACnet device (from an I-Am) and the objects enumerated on it."""

    instance: int
    address: str
    object_name: str = ""
    vendor_id: int | None = None
    objects: list = field(default_factory=list)


@dataclass
class BacnetPointRecord:
    """A flat inventory row for one discovered object (parallel to
    :class:`camber.inventory.PointFile`, but built from device/object metadata rather than a
    filename)."""

    device_instance: int
    address: str
    object_type: str
    instance: int
    object_name: str
    unit: str
    description: str
    is_trend: bool
    present_value: Any | None = None


@runtime_checkable
class DiscoveryClient(Protocol):
    """The injected, read-only network seam discovery orchestrates against.

    A deployment builds this over bacpypes3 (see ``docs/INGEST-PROTOCOLS.md``); tests inject a fake.
    Each method maps to an allowlisted read/broadcast service — there is intentionally no write.
    """

    def who_is(self, low: int | None = None, high: int | None = None):
        """Broadcast Who-Is; yield I-Am results as ``(device_instance, address[, vendor_id])``
        tuples (or dicts with those keys)."""
        ...

    def read_object_list(self, address: str, device_instance: int):
        """ReadProperty the device's ``object-list``; return ``[(object_type, instance), ...]``."""
        ...

    def read_object_metadata(self, address: str, object_id: tuple, props=DISCOVERY_READ_PROPERTIES):
        """ReadPropertyMultiple the allowlisted ``props``; return ``{prop: value}``."""
        ...


def _unpack_iam(iam) -> tuple:
    """Normalize one I-Am result to ``(instance, address, vendor_id | None)``."""
    if isinstance(iam, dict):
        return (
            int(iam["device_instance"]),
            str(iam.get("address", "")),
            iam.get("vendor_id"),
        )
    inst = int(iam[0])
    addr = str(iam[1])
    vid = iam[2] if len(iam) > 2 else None
    return inst, addr, vid


def discover(
    client,
    *,
    device_range: tuple | None = None,
    read_present_value: bool = False,
    vendor_bridge=None,
) -> list:
    """Discover devices and enumerate their objects via an injected :class:`DiscoveryClient`.

    ``device_range`` is an optional ``(low, high)`` device-instance window for Who-Is.
    ``read_present_value`` additionally records each object's present value (extra network load; off
    by default — a snapshot, not a trend). ``vendor_bridge`` is an optional zero-argument callable
    run once before the reads (e.g. ``camber.interop.bacnet_vendor.install_vendor_decoders``); it is
    **best-effort** — decoder registration only takes effect if it happened when the client's
    bacpypes3 app was built, so this hook is a convenience, not a guarantee (see the vendor module).

    Returns a list of :class:`DiscoveredDevice`, each with its :class:`DiscoveredObject` list.
    """
    if vendor_bridge is not None:
        try:
            vendor_bridge()
        except Exception:  # best-effort; a raising bridge must never break discovery
            pass
    low, high = device_range or (None, None)
    devices: list = []
    for iam in client.who_is(low, high):
        inst, addr, vid = _unpack_iam(iam)
        dev = DiscoveredDevice(instance=inst, address=addr, vendor_id=vid)
        for oid in client.read_object_list(addr, inst):
            otype, oinst = oid[0], oid[1]
            meta = (
                client.read_object_metadata(addr, (otype, oinst), DISCOVERY_READ_PROPERTIES) or {}
            )
            name = str(meta.get("object-name") or "")
            dev.objects.append(
                DiscoveredObject(
                    device_instance=inst,
                    address=addr,
                    object_id=(otype, oinst),
                    object_name=name,
                    units=str(meta.get("units") or ""),
                    description=str(meta.get("description") or ""),
                    present_value=(meta.get("present-value") if read_present_value else None),
                    is_trend=otype in TREND_OBJECT_TYPES,
                )
            )
            if otype == "device" and name and not dev.object_name:
                dev.object_name = name
        devices.append(dev)
    return devices


def discovery_to_points(devices, *, trend_only: bool = True) -> list:
    """Convert discovered objects into :class:`camber.ingest.bacnet.BacnetPoint` for the read path.

    ``trend_only=True`` (default) keeps only Trend-Log objects — the history sources
    :class:`~camber.ingest.bacnet.BacnetSource` reads. Set it ``False`` to also include
    analog/binary/multistate objects for present-value snapshots. Point names use ``object-name``,
    falling back to ``<type>_<instance>``.
    """
    from .bacnet import BacnetPoint

    points = []
    for dev in devices:
        for obj in dev.objects:
            if trend_only and not obj.is_trend:
                continue
            otype, oinst = obj.object_id
            name = obj.object_name or f"{otype}_{oinst}"
            points.append(
                BacnetPoint(name=name, object_id=obj.object_id, unit=str(obj.units or ""))
            )
    return points


def discovery_to_inventory(devices) -> list:
    """Flatten discovered devices/objects into :class:`BacnetPointRecord` inventory rows."""
    rows = []
    for dev in devices:
        for obj in dev.objects:
            otype, oinst = obj.object_id
            rows.append(
                BacnetPointRecord(
                    device_instance=dev.instance,
                    address=dev.address,
                    object_type=otype,
                    instance=oinst,
                    object_name=obj.object_name,
                    unit=str(obj.units or ""),
                    description=obj.description,
                    is_trend=obj.is_trend,
                    present_value=obj.present_value,
                )
            )
    return rows


def to_rows(records) -> list:
    """Flatten the inventory records to plain dicts (like :func:`camber.inventory.to_rows`)."""
    return [asdict(r) for r in records]


__all__ = [
    "DISCOVERY_SERVICES",
    "DISCOVERY_READ_PROPERTIES",
    "TREND_OBJECT_TYPES",
    "DiscoveredObject",
    "DiscoveredDevice",
    "BacnetPointRecord",
    "DiscoveryClient",
    "discover",
    "discovery_to_points",
    "discovery_to_inventory",
    "to_rows",
]
