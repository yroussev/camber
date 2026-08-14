"""Concrete bacpypes3-backed BACnet client — the read-only default for reads + discovery.

This is the "batteries" for the injected-client seams in :mod:`camber.ingest.bacnet` (reads) and
:mod:`camber.ingest.bacnet_discovery` (discovery): a single client that implements **both** the
``DiscoveryClient`` protocol (``who_is`` / ``read_object_list`` / ``read_object_metadata``) and the
read client (``read_present_value`` / ``read_trend_log``), so a caller can point at a device instead
of writing bacpypes3 wiring by hand.

**Two layers, one for testing, one for the wire.**

- :class:`Bacpypes3Client` is a *sync facade* over a bacpypes3 async ``Application``. A background
  thread runs one coroutine that builds/holds the ``Application`` and services operations from a
  queue, so every call runs within the app's own coroutine context (bacpypes3 requires this — an
  ``Application`` built in one coroutine cannot be driven from another). It shapes responses into
  CAMBER's records (dashed→camelCase object types, unit passthrough to
  :func:`camber.interop.bacnet.normalize_bacnet_unit`, Trend-Log records → ``(timestamp, value)``).
  The app (or an async ``build_app`` factory) is *injected*, so all shaping is unit-tested with a
  fake — **no bacpypes3, no network**.
- The ``bacnet_read_client`` / ``bacnet_discovery_client`` / :func:`discover_default` builders
  construct a real bacpypes3 ``Application`` from a :class:`BacnetClientConfig`. That is the only
  network-touching code; it is ``# pragma: no cover`` and validated against a simulated device.

**Read-only by construction.** Only ``who_is`` / ``read_property`` / ``read_property_multiple`` /
``read_range`` are ever called; this module references no ``write_property`` or command service (the
ingest read-only AST guard covers it). The shared ``Application`` *is* a full B-device and exposes
writes — the caller must not use them.

**Posture.** Live polling is a *fallback*; the historian/SQL/Haystack path stays recommended for
production. bacpypes3 is Pre-Alpha (0.0.x) and BACnet/SC is experimental; best-effort. Configure it
three equivalent ways: the :class:`BacnetClientConfig` **API**, a **YAML/JSON file**
(:meth:`BacnetClientConfig.from_file`), or the ``camber bacnet-discover`` **CLI**.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import threading
from dataclasses import asdict, dataclass

from .bacnet_discovery import DISCOVERY_READ_PROPERTIES

# --------------------------------------------------------------------------- object-type spelling


def _to_camel(dashed) -> str:
    """bacpypes3 renders object types dashed (``analog-input``); CAMBER uses camelCase."""
    parts = str(dashed).split("-")
    return parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:])


def _to_dashed(camel) -> str:
    """camelCase CAMBER object type → the dashed ASN.1 name bacpypes3 expects."""
    out = []
    for ch in str(camel):
        if ch.isupper():
            out.append("-")
            out.append(ch.lower())
        else:
            out.append(ch)
    return "".join(out)


def _oid_str(object_id) -> str:
    """A ``(object_type, instance)`` tuple → the ``"analog-input,1"`` string bacpypes3 accepts."""
    otype, inst = object_id
    return f"{_to_dashed(otype)},{int(inst)}"


def _as_bacnet_address(address):
    """Coerce a string address to a bacpypes3 ``Address`` for a directed Who-Is (which, unlike the
    read services, does not accept a bare string). Passes through when it isn't a string, or when
    bacpypes3 isn't importable (the fake/unit-test path, where the injected client takes the raw
    string)."""
    if address is None or not isinstance(address, str):
        return address
    try:
        from bacpypes3.pdu import Address
    except Exception:  # noqa: BLE001 - no bacpypes3: leave the raw string for the injected client
        return address
    return Address(address)


# --------------------------------------------------------------------------- value shaping


def _unwrap(value):
    """Unwrap a bacpypes3 ``AnyAtomic`` to its Python value; pass other values through."""
    getter = getattr(value, "get_value", None)
    if callable(getter):
        try:
            return getter()
        except Exception:  # best-effort unwrap; leave the raw value for the caller
            return value
    return value


def _log_datum_value(datum):
    """Extract a value from a Trend-Log record's ``logDatum`` CHOICE (best-effort).

    Member names are bacpypes3's ``LogRecordLogDatum`` spelling; the inactive choice members read
    back as ``None``, so the first non-``None`` scalar wins.
    """
    if datum is None:
        return None
    if hasattr(datum, "get_value"):
        return _unwrap(datum)
    for attr in ("realValue", "unsignedValue", "signedValue", "enumValue", "booleanValue"):
        v = getattr(datum, attr, None)
        if v is not None:
            return v
    return datum


def _log_record_to_pair(record) -> tuple:
    """One bacpypes3 ``LogRecord`` → ``(timestamp, value)`` for :func:`trendlog_to_series`.

    Duck-typed: reads ``.timestamp`` and ``.logDatum``. Timestamps are stringified and handed to
    CAMBER's tolerant multi-format ``parse_timestamps``; unparseable records are dropped downstream.
    """
    ts = getattr(record, "timestamp", None)
    return (None if ts is None else str(ts), _log_datum_value(getattr(record, "logDatum", None)))


# --------------------------------------------------------------------------- the client


class Bacpypes3Client:
    """Read-only sync facade over a bacpypes3 async ``Application`` (discovery + reads).

    A background thread runs one coroutine that builds/holds the ``Application`` and services
    operations from a queue, so every call executes within the app's own coroutine context — an
    ``Application`` built in one coroutine cannot be driven from another. Pass an already-built
    async ``app`` (a fake, in tests) **or** an async ``build_app`` factory (the real app, built
    inside the worker), never both. ``address`` is the default target for :meth:`read_present_value`
    / :meth:`read_trend_log`; the discovery methods take an address per call. Use it as a context
    manager, or call :meth:`close`.
    """

    def __init__(self, app=None, *, build_app=None, address=None, timeout: float = 10.0):
        if (app is None) == (build_app is None):
            raise ValueError("pass exactly one of app / build_app")
        self._address = address
        self._timeout = timeout
        self._app = None
        self._queue = None
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._start_error = None
        self._closed = False
        self._thread = threading.Thread(
            target=self._thread_main, args=(app, build_app), name="camber-bacnet", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(timeout=timeout + 5):
            raise TimeoutError("BACnet client worker did not start in time")
        if self._start_error is not None:
            raise self._start_error

    # -- worker thread + serve coroutine (all app I/O happens here, in one coroutine) --
    def _thread_main(self, app, build_app):
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve(app, build_app))
        finally:
            self._loop.close()

    async def _serve(self, app, build_app):
        self._queue = asyncio.Queue()
        try:
            self._app = app if build_app is None else await build_app()
        except Exception as e:  # noqa: BLE001 - surface a build failure to the constructor
            self._start_error = e
            self._ready.set()
            return
        self._ready.set()
        while True:
            factory, fut = await self._queue.get()
            if factory is None:  # shutdown sentinel
                await self._close_app()
                return
            try:
                fut.set_result(await factory(self._app))
            except Exception as e:  # noqa: BLE001 - propagate to the caller's future
                fut.set_exception(e)

    async def _close_app(self):
        closer = getattr(self._app, "close", None)
        if callable(closer):
            try:
                res = closer()
                if asyncio.iscoroutine(res):
                    await res
            except Exception:  # best-effort teardown
                pass

    def _run(self, factory):
        """Submit ``factory(app) -> coroutine`` to the worker and block for its result."""
        if self._closed:
            raise RuntimeError("BACnet client is closed")
        fut: concurrent.futures.Future = concurrent.futures.Future()
        self._loop.call_soon_threadsafe(self._queue.put_nowait, (factory, fut))
        return fut.result(timeout=self._timeout)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        """Shut the worker down (closing the app) and join its thread."""
        if self._closed:
            return
        self._closed = True
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, (None, None))
        except Exception:  # loop already stopped
            pass
        self._thread.join(timeout=5)

    # ---- DiscoveryClient protocol ----
    def who_is(self, low=None, high=None, address=None):
        """Who-Is — broadcast, or directed to ``address`` (unicast) when given; return
        ``[(device_instance, address, vendor_id), ...]``."""
        addr = _as_bacnet_address(address)
        i_ams = self._run(lambda app: app.who_is(low, high, addr)) or []
        out = []
        for iam in i_ams:
            dev = tuple(iam.iAmDeviceIdentifier)  # (ObjectType, instance)
            out.append((int(dev[1]), str(iam.pduSource), int(iam.vendorID)))
        return out

    def read_object_list(self, address, device_instance):
        """ReadProperty the device ``object-list`` → camelCase ``[(type, instance), ...]``."""
        oids = self._run(
            lambda app: app.read_property(address, f"device,{device_instance}", "object-list")
        )
        return [(_to_camel(str(tuple(o)[0])), int(tuple(o)[1])) for o in (oids or [])]

    def read_object_metadata(self, address, object_id, props=DISCOVERY_READ_PROPERTIES):
        """ReadPropertyMultiple the allowlisted ``props`` of one object → ``{prop: value}``.

        bacpypes3's parameter list is **flat** — ``[object_id, [prop, ...]]`` — not a list of
        ``(object_id, [prop, ...])`` tuples; the response is ``(oid, prop, arr_index, value)`` rows.
        """
        result = self._run(
            lambda app: app.read_property_multiple(address, [_oid_str(object_id), list(props)])
        )
        meta = {}
        for row in result or []:
            _oid, prop, _arr_index, value = row
            meta[str(prop)] = _unwrap(value)
        return meta

    # ---- read client (BacnetSource) ----
    def read_present_value(self, object_id):
        """ReadProperty the object's ``present-value`` at the configured target address."""
        return _unwrap(
            self._run(
                lambda app: app.read_property(self._address, _oid_str(object_id), "present-value")
            )
        )

    def read_trend_log(self, object_id, *, max_records: int = 1000):
        """ReadRange the object's ``log-buffer`` → ``[(timestamp, value), ...]`` records.

        Reads up to ``max_records`` from the start (RangeByPosition). bacpypes3 requires a
        ``range_params`` 5-tuple ``(range_type, first, date, time, count)``.
        """
        records = self._run(
            lambda app: app.read_range(
                self._address,
                _oid_str(object_id),
                "log-buffer",
                range_params=("p", 1, None, None, max_records),
            )
        )
        return [_log_record_to_pair(r) for r in (records or [])]


# --------------------------------------------------------------------------- configuration


@dataclass
class BacnetClientConfig:
    """Deployment config for the local BACnet stack the client builds (not the read *target*).

    ``local_address`` pins the interface/IP[:port] to bind on a multi-homed host (and enables
    BBMD/foreign-device use when set to a BBMD); ``local_device_id`` / ``local_object_name`` /
    ``vendor_id`` are this host's own B-device identity; ``timeout`` is the per-request budget;
    ``device_range`` is the ``(low, high)`` device-instance window for Who-Is. Build from the API,
    a mapping, or a YAML/JSON file — all equivalent.
    """

    local_address: str | None = None
    local_device_id: int = 599
    local_object_name: str = "camber"
    vendor_id: int = 555
    timeout: float = 10.0
    device_range: tuple | None = None
    known_addresses: tuple | None = None  # unicast discovery targets (skip broadcast Who-Is)

    @classmethod
    def from_mapping(cls, data: dict) -> BacnetClientConfig:
        """Build from a plain dict (unknown keys ignored; list fields → tuple)."""
        fields = set(cls.__dataclass_fields__)
        kw = {k: v for k, v in dict(data or {}).items() if k in fields}
        for seq_field in ("device_range", "known_addresses"):
            if kw.get(seq_field) is not None:
                kw[seq_field] = tuple(kw[seq_field])
        return cls(**kw)

    @classmethod
    def from_file(cls, path) -> BacnetClientConfig:
        """Load from a ``.json`` file (stdlib) or a ``.yml`` / ``.yaml`` file (needs PyYAML).

        YAML keeps the config human-friendly for ops; JSON always works with no extra dependency.
        """
        text = open(path, encoding="utf-8").read()
        if str(path).endswith((".yml", ".yaml")):
            try:
                import yaml  # optional (pip install pyyaml)
            except Exception as e:  # noqa: BLE001
                raise ImportError(
                    "reading a YAML BACnet config needs PyYAML: pip install pyyaml "
                    "(or pass a .json file)"
                ) from e
            data = yaml.safe_load(text) or {}
        else:
            data = json.loads(text or "{}")
        return cls.from_mapping(data)

    def to_mapping(self) -> dict:
        """The config as a plain dict (tuple fields as lists), for round-trip / CLI echo."""
        d = asdict(self)
        for seq_field in ("device_range", "known_addresses"):
            if d.get(seq_field) is not None:
                d[seq_field] = list(d[seq_field])
        return d


# --------------------------------------------------------------------------- real-app builders
# Everything below constructs a real bacpypes3 Application and talks to a network: pragma: no cover.


def _real_client(config, *, address=None):  # pragma: no cover - needs bacpypes3 + a network
    """Build a client backed by a real bacpypes3 ``Application`` (lazy import behind ``[bacnet]``).

    The ``Application`` is built by an async factory *inside* the client's worker coroutine, because
    ``Application.from_args`` requires a running loop and the app can only be driven from the
    coroutine that built it.
    """
    try:
        from bacpypes3.app import Application
        from bacpypes3.argparse import SimpleArgumentParser
    except Exception as e:  # noqa: BLE001
        raise ImportError(
            "the bacpypes3 BACnet client needs the optional extra: "
            'pip install "camber-toolkit[bacnet]"'
        ) from e
    argv = ["--instance", str(config.local_device_id), "--name", config.local_object_name]
    if config.local_address:
        argv += ["--address", config.local_address]

    async def _build():
        return Application.from_args(SimpleArgumentParser().parse_args(argv))

    return Bacpypes3Client(build_app=_build, address=address, timeout=config.timeout)


def bacnet_read_client(
    target, config: BacnetClientConfig | None = None
) -> Bacpypes3Client:  # pragma: no cover - real network
    """A read client bound to ``target`` (a validated :class:`camber.ingest.bacnet.BacnetTarget`).

    Pass to ``BacnetSource(points, target, client=bacnet_read_client(target))``.
    """
    config = config or BacnetClientConfig()
    target.validate()
    return _real_client(config, address=target.address)


def bacnet_discovery_client(
    config: BacnetClientConfig | None = None,
) -> Bacpypes3Client:  # pragma: no cover - real network
    """A discovery client (no fixed target — Who-Is broadcasts, reads per discovered address)."""
    return _real_client(config or BacnetClientConfig())


def discover_default(
    config: BacnetClientConfig | None = None, *, read_present_value: bool = False
):  # pragma: no cover - real network
    """Build a default discovery client and run :func:`camber.ingest.bacnet_discovery.discover`.

    Returns the discovered devices; feed them to ``mapping_from_bacnet`` / ``review_bacnet`` to
    bootstrap a Role mapping. When ``config.known_addresses`` is set, devices are enumerated by
    **directed** Who-Is to those addresses (segmented/cloud networks); otherwise a broadcast Who-Is
    is used. The client is closed before returning.
    """
    from .bacnet_discovery import discover, discover_addresses

    config = config or BacnetClientConfig()
    with bacnet_discovery_client(config) as client:
        if config.known_addresses:
            return discover_addresses(
                client, config.known_addresses, read_present_value=read_present_value
            )
        return discover(
            client, device_range=config.device_range, read_present_value=read_present_value
        )


__all__ = [
    "Bacpypes3Client",
    "BacnetClientConfig",
    "bacnet_read_client",
    "bacnet_discovery_client",
    "discover_default",
]
