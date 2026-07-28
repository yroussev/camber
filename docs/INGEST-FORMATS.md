# Ingest formats — timestamps, data types, vendor profiles

Every BAS export tool formats trend data differently, and there is **no public archive of raw
per-vendor exports** to test against (they're proprietary and site-specific). So CAMBER handles the
*variety* directly: two shared parsers swallow the common timestamp/value encodings, and named
**vendor profiles** capture each tool's documented CSV conventions. All ingest adapters share these,
so a new format is handled once, everywhere.

## Timestamps — `camber.tsparse.parse_timestamps`

One parser behind every adapter. It strips a trailing timezone abbreviation (`PDT`/`GMT` — but not an
`AM`/`PM` meridiem), tries an **ordered list of explicit formats** (the common BAS/ISO ones first, so
existing data parses identically), detects **epoch** (seconds/millis) and **Excel serial** numbers, and
falls back to pandas inference last. It never raises — unparseable entries become `NaT`.

Handled out of the box: ISO 8601 (incl. offsets), US `MM/DD/YYYY` (12- and 24-hour), European
`DD/MM/YYYY` (`dayfirst=True`), the BAS `21-Apr-23 8:30:03 AM PDT`, LBNL `yyyymmdd hh:mm`, epoch
seconds/millis, and Excel serial dates. Naive-local by default (CAMBER's downstream convention); tz is
preserved only with `naive=False`/`assume_tz`. Two silent traps are now fixed: European `03/04/2025`
(was read as US March 4) and a non-BAS per-point format (previously yielded a silently empty series).

## Data types — `camber.coerce`

- `coerce_numeric` normalizes a **null/quality-token** vocabulary (`N/A`, `---`, `Bad`, `Comm Fail`, …)
  to NaN and strips **thousands separators** / a **European decimal comma** before `to_numeric` — so a
  stray text cell can't poison a column and grouped numbers (`1,234`) parse. Clean numerics are
  unchanged.
- `coerce_status` maps status/command **text** to 0/1 with an extensible vocabulary: On/Off,
  Running/Stopped, **Open/Closed, Fault/Alarm/Normal, Override/Hand/Manual, Auto**, plus a
  numeric-nonzero fallback. Used for the `STATUS_ROLES`.

## Vendor profiles — `camber.ingest.profiles`

An `IngestProfile` captures a tool's delimiter, encoding, header rows to skip, timestamp format,
day-first flag, and decimal/thousands separators. Named presets (starting points from public vendor
docs; every field overridable):

| Profile | Convention |
|---|---|
| `generic` (default) | comma, UTF-8 (BOM-tolerant), auto-detect timestamp |
| `niagara_n4` | Tridium Niagara N4 history export — BAS 12-hour timestamp |
| `metasys` | JCI Metasys — US month-first 12-hour |
| `webctrl` | Automated Logic WebCTRL — US month-first |
| `tracer` | Trane Tracer — ISO-ish |
| `desigo` | Siemens Desigo (European) — semicolon, decimal comma, `DD.MM.YYYY`, day-first |

```python
from camber.io import load_csv

load_csv("export.csv", profile="desigo")  # semicolon + decimal comma + DD.MM.YYYY
load_csv("export.csv", profile="metasys", skiprows=2)  # skip a 2-row export preamble
load_csv("export.csv", delimiter="|", dayfirst=True)  # or override fields directly
```

Profiles thread through `io.load_csv` and `WideCsvAdapter`; defaults resolve to `generic` (fully
backward compatible).

## CSV shapes

- **Wide** — `io.load_csv` / `WideCsvAdapter`: one timestamp column + one column per point.
- **Per-point** — `PerPointCsvAdapter`: a folder of `<name>.csv`, each `Timestamp,Value(<unit>)`.
- **Long/tall** — `LongCsvAdapter` (**new**): one row per `timestamp,point,value[,unit]` (the historian
  export shape), pivoted to the standard wide frame.

## Guarantee

A synthetic per-vendor corpus (`tests/test_ingest_formats.py`) writes the **same** data in each vendor's
format and asserts they all normalize to the **identical** frame — the equivalence proof, since a raw
per-vendor archive can't be published. See **[INGEST-PROTOCOLS.md](INGEST-PROTOCOLS.md)** for the
read-only network adapters (Modbus/MQTT/BACnet/OPC-UA), which share these same parsers.
