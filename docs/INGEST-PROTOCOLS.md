# Network ingest protocols

CAMBER's primary ingest path is the **historian / SQL / Haystack** tier (see
[SECURITY.md](SECURITY.md)). For sites where direct acquisition is required, it also ships
**read-only** network adapters for Modbus, MQTT, and BACnet. Each:

- implements the `SourceAdapter` shape (`point_names()` / `load_points()` / `units()`),
- imports its protocol library **lazily** behind an optional extra (the core stays
  dependency-light), and
- is **read-only by construction** — it references no write/command service (enforced by test).

| Protocol | Module | Extra | Library | License | Shape |
|---|---|---|---|---|---|
| Modbus TCP | `camber.ingest.modbus` | `[modbus]` | pymodbus | BSD-3 | snapshot / poll |
| MQTT (+ Sparkplug) | `camber.ingest.mqtt_stream` | `[mqtt]` | paho-mqtt | EDL-1.0 / EPL-2.0 | streaming buffer |
| BACnet (+ SC) | `camber.ingest.bacnet` | `[bacnet]` | bacpypes3 | MIT | Trend-Log / present value |
| OPC-UA | `camber.ingest.opcua` | `[opcua]` | asyncua | **LGPL-3.0** † | history / current value |

† asyncua is LGPL-3.0 — kept as an optional, dynamically-imported dependency only (never
vendored/bundled), so CAMBER's own code stays Apache-2.0.

```
pip install "camber-toolkit[modbus]"   # or [mqtt], [bacnet], [opcua]
```

## Modbus — `camber.ingest.modbus`

Modbus has no history: a read returns the *current* register. `ModbusSource.read_snapshot()`
reads each mapped `ModbusPoint` (holding/input register, slave id, 16- or 32-bit, scale/offset);
`poll()` samples repeatedly into a short series; `load_points()` returns a one-row snapshot. For
long trends use a historian. Modbus has **no authentication or encryption** — keep the host out
of the control VLAN. The client is injectable (pymodbus-style `read_holding_registers` /
`read_input_registers`) for testing.

## MQTT — `camber.ingest.mqtt_stream`

MQTT is *push*. `MqttStreamSource.subscribe()` connects (TLS via `tls=True`), subscribes to the
mapped topics, and routes each message into the pure `ingest()` handler, which parses the payload
(a bare value, or a JSON field via `value_key` — including Sparkplug-B metric fields) and buffers
a timestamped sample. `to_frame()` / `load_points()` shape the buffer into per-point series. The
adapter only subscribes; it never publishes.

## BACnet — `camber.ingest.bacnet`

The analytics-friendly BACnet source is a **Trend Log** object, which already holds timestamped
records; `BacnetSource.read_trend_log()` / `load_points()` shape them into series, and
`read_snapshot()` reads present values. The adapter uses only read services
(`READ_SERVICES = ReadProperty, ReadPropertyMultiple, ReadRange`).

### BACnet/SC (Secure Connect) — experimental, certificate-gated

Legacy BACnet/IP is UDP, cleartext, broadcast-based, and unauthenticated. **BACnet/SC**
(ANSI/ASHRAE Addendum 135-2016bj, now in ASHRAE 135) replaces that datalink with secure
WebSockets (`wss://` over TLS 1.3), a hub-and-spoke topology (no broadcasts), and **mutual X.509
certificate authentication**. `BacnetTarget(secure=True, hub_uri=…, cert=…, key=…, ca=…)` carries
the SC config and `validate()` rejects an incomplete one.

Two honesty caveats, both deliberate:

1. **Joining an SC network requires an operational certificate** issued for that network plus the
   hub URI — there is no IP-only path. This is an administrative onboarding step CAMBER cannot
   ship around.
2. **Production-grade SC mutual-auth in the open-source Python stack (bacpypes3) is still
   maturing.** bacpypes3 (MIT, actively maintained) has SC code and ships `websockets` as an
   extra, but its own docs describe SC as in development. So CAMBER's claim is deliberately
   scoped: *BACnet/SC-capable (experimental)*, not an unqualified "BACnet/SC compatible."

Accordingly, the default BACnet client is **not** auto-constructed: `BacnetSource` expects an
injected client exposing `read_trend_log(object_id)` and `read_present_value(object_id)` (a thin
bacpypes3 wrapper configured per deployment), or — recommended for production — reach BACnet data
through a **historian/gateway that already speaks SC** on the OT side and read it via the SQL or
Haystack adapter.

## OPC-UA — `camber.ingest.opcua`

The analytics-friendly OPC-UA source is a **historizing node**, whose retained timestamped values
map onto a series; `OpcUaSource.read_history()` / `load_points(start=…, end=…)` shape them, and
`read_snapshot()` (or `load_points()` with no window) reads current values. The adapter uses only
the read services (`READ_SERVICES = Read, HistoryRead`).

OPC-UA is secure-by-design: pass an `OpcUaSecurity` with asyncua's security string (policy, mode,
client cert/key) and/or username/password, and connect to an encrypted, authenticated endpoint —
not a `None`-security one — on a production network. The client is injectable (any object with
`read_value(node_id)` and `read_history(node_id, start, end)`); the default wraps asyncua's
synchronous client and needs a `url`.

**Licensing:** asyncua is **LGPL-3.0**, so it is an optional, dynamically-imported dependency
only — never vendored or statically bundled — which keeps CAMBER's own code Apache-2.0.

## Other protocols considered

- **VOLTTRON** (Eclipse, Apache-2.0) — a full ZMQ/gevent agent platform, not a light library; its
  BACnet driver even needs a separate proxy process. CAMBER treats VOLTTRON as a **data source**
  (point the SQL adapter at its SQLite/PostgreSQL historian, or the MQTT adapter at forwarded
  telemetry) and a design reference — not a dependency. See [ECOSYSTEM.md](ECOSYSTEM.md).

## References

- ASHRAE Addendum 135-2016bj — https://www.ashrae.org/File%20Library/Technical%20Resources/Standards%20and%20Guidelines/Standards%20Addenda/135_2016_bj_20191118.pdf
- BACnet International — BACnet/SC — https://bacnetinternational.org/bacnetsc/
- How Digital Certificates are Used in BACnet/SC — https://www.automatedbuildings.com/2026/02/how-digital-certificates-are-used-in-bacnet-sc/
- bacpypes3 — https://github.com/JoelBender/BACpypes3 · pymodbus — https://www.pymodbus.org/ · paho-mqtt — https://github.com/eclipse-paho/paho.mqtt.python
