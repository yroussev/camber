# Security posture

CAMBER is a **read-only** building-analytics tool: it ingests time-series trend data and runs
fault detection / M&V. It does not — and by construction *cannot* — actuate equipment. This
document states the threat model, the design rules that follow from it, and how those rules are
enforced in the network ingest adapters.

It is written against the OT-security guidance that governs building automation systems:
**NIST SP 800-82r3** (*Guide to Operational Technology Security*), **ISA/IEC 62443** (the
ICS zones-and-conduits lifecycle), the **NIST Cybersecurity Framework**, and **ANSI/ASHRAE 135**
including BACnet/SC.

## Threat model

A read-only analytics tool that *touches* an OT/BAS network still introduces real risk:

1. **Pivot risk.** A host with a route into the control VLAN is a bridge from IT (or the
   internet) toward controllers. Compromise of the analytics host = a foothold next to OT.
2. **Credential / certificate exposure.** Historian passwords, Haystack tokens, and BACnet/SC
   operational certificates + private keys are exfiltration targets if stored on the host.
3. **Accidental writes.** Any library that exposes a write/command service can fire one through
   a bug or misuse. On OT, an unintended write can move equipment.
4. **Discovery side-effects.** Legacy BACnet `Who-Is` broadcasts and Modbus scans can flood
   segments or upset fragile devices.
5. **Sensitive data.** Occupancy, schedules and setpoint telemetry carry physical-security and
   privacy implications even though they are "just trends."

## Design rules

### 1. Prefer the historian / SQL / Haystack tier — this is the default posture

NIST SP 800-82 places historians at a low trust level reached through **one-way** conduits
(data diodes), pushing data *upward*. For an analytics tool operating on trends, pulling from a
**historian, SQL database, or Haystack API** (`camber.ingest.sql`, `camber.ingest.haystack`) is
almost always the right source: it matches the one-way architecture, the data is already trended,
and it avoids both discovery-broadcast load and the certificate burden of joining a live network.
This is the **recommended and default** ingest path. Live protocol polling is the exception.

Direct live polling (Modbus/BACnet) is justified only when there is no historian, it lacks the
needed points/resolution, or for commissioning/discovery — and even then, prefer reading through
a read-only gateway or protocol-to-historian bridge rather than polling controllers directly.

### 2. Read-only by construction, not by convention

The network adapters (`camber.ingest.modbus`, `mqtt_stream`, `bacnet`) only ever call read
services — Modbus `read_holding_registers` / `read_input_registers`, MQTT subscribe, BACnet
`ReadProperty` / `ReadPropertyMultiple` / Trend-Log reads. They **do not import or wrap any
write/command service at all**, so no code path can actuate equipment. This is enforced by a
unit test (`tests/test_ingest_protocols.py::test_adapters_reference_no_write_services`) that
parses each adapter's AST and fails if it references `WriteProperty`, any `write_*register/coil`,
or MQTT `publish`.

### 3. Network segmentation & least privilege

Run CAMBER in IT/DMZ, never inside the control VLAN; cross the boundary through a jump host or
data diode. Use read-only DB accounts, monitoring-scoped BACnet/SC certificates, and read-only
Modbus register maps where the gateway supports them.

### 4. Secrets and TLS

No secrets in the repository — credentials come from environment variables or a secret manager
(e.g. `OPENEI_API_KEY`), and TLS private keys stay off source control and shared volumes. Use
TLS everywhere it exists: `wss://` + TLS 1.3 for BACnet/SC, TLS for MQTT (`tls=True`), and
OPC-UA security policies. Validate peer certificates; never disable verification.

### 5. Certificate management for BACnet/SC

BACnet/SC requires an **operational certificate** issued for the target network plus the hub
URI; there is no IP-only shortcut. Treat these certs as managed assets with a renewal/revocation
lifecycle. See [INGEST-PROTOCOLS.md](INGEST-PROTOCOLS.md) for how the config is plumbed and why
SC support is currently labelled experimental.

### 6. Audit logging

Log every connection and every point/property read with source and timestamp so the tool's
footprint on the OT network is auditable.

## References

- NIST SP 800-82r3 — Guide to OT Security — https://csrc.nist.gov/News/2023/nist-publishes-sp-800-82-revision-3
- ISA/IEC 62443 (ICS security, zones & conduits)
- NIST Cybersecurity Framework (CSF)
- ANSI/ASHRAE 135 incl. Addendum 135-2016bj (BACnet/SC) — https://bacnetinternational.org/bacnetsc/
