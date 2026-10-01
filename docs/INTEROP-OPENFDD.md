# open-fdd interop: importer, crosswalk and findings JSON

**Provisional (0.99).** This page covers [camber#22](https://github.com/yroussev/camber/issues/22)
item 2 (CAMBER reads open-fdd data), with drafts of item 3 (a shared findings format) and item 4
(a role crosswalk). Names, the crosswalk and the findings schema may change in a minor release.

[open-fdd](https://github.com/bbartling/open-fdd) collects and stores building trends at the
edge. `camber.interop.openfdd` reads that data into a CAMBER store, so CAMBER's drift, M&V and
sensor-trust checks can run on it. Results go back as a JSON file.

## Ground rules

- **Files and processes only.** CAMBER reads open-fdd's documented file formats. It imports no
  open-fdd code, copies no open-fdd code or SQL, and calls no open-fdd API.
- **Read-only.** Nothing is written back to open-fdd. CAMBER writes only its own store,
  workspace and output files.
- **Each engine speaks for itself.** Every record in the findings JSON names the engine and
  version that produced it. Nothing is merged with another engine's verdicts.
- **Nothing is guessed.** The site time zone and the unit system are required inputs. A column
  maps to a CAMBER role only through the versioned crosswalk. Every other column is counted and
  reported with the reason it was left out.

The importer follows open-fdd's public documentation at commit
`07bd84b81902780890b838e0b9c1f01c6747eff1`: *Package schema (ingest contract)*,
`PACKAGE_AUTHORING`, the Rust/DataFusion engine's data-tree contract, the *SQL rules → Haystack
map* and the historian architecture. The pin is recorded in `crosswalk.json` and on every ingested
facility.

## What it reads

### The package (`openfdd_package_v1`)

A folder or a `.zip`:

```text
<building>/
  manifest.json                {"schema_version": "openfdd_package_v1", "grid_minutes": 5, ...}
  column_map.json              optional: {"equipment": {<equip>: {"equipType", "points", ...}}}
  equipment_inventory.json     optional: [{"equip_id", "type", ...}]
  <equip>/                     may be nested, e.g. VAV/VAV_1
    history_wide.csv           timestamp_utc + one column per point
    columns.csv                col (or column), point_role [, unit or units]
    column_map.json            sibling map: equipType + points {Haystack name: column}
    history_wide.json          (the same sibling map, under its other documented name)
  weather/history_wide.csv     web-outside-air-temp, ...
```

- One CAMBER **facility** per building.
- One **equipment** per folder holding a `history_wide.csv`. Its id is the folder name, or the
  joined path when two folders share a name. The `weather/` folder becomes equipment `weather`
  of class `WEATHER`.
- A multi-building package needs `--building`.
- Known open-fdd outputs and UI state (`fdd_events.csv`, `fdd_faults.csv`, `quality.json`,
  `session_config.json`, ...) are not ingested. They are listed in the result as *ignored*.

### The historian Parquet layout

open-fdd's canonical historian root:

```text
<root>/history/building_id=<B>/equipment_id=<E>/year=<YYYY>/month=<MM>/part-*.parquet
<root>/weather/building_id=<B>/year=<YYYY>/month=<MM>/part-*.parquet
```

Columns are `timestamp_utc` plus open-fdd SQL roles (`sat`, `zone_t`, ...). The reader skips
hidden files (`.`- or `_`-prefixed), which covers open-fdd's compaction tombstones. It does not
read the legacy pre-H2 layouts.

The layout carries no equipment types, so pass `--equip-types FILE` with a
`{equipment_id: equipType}` JSON. Without it, every equipment is unclassified.

## Required: the site time zone and the unit system

| Input | Why it is required |
|---|---|
| `--timezone` (IANA, e.g. `America/Chicago`) | Package stamps are UTC and the documented manifest has no site-zone field. CAMBER's schedules, occupancy and hour-of-day rules need local wall-clock time. open-fdd's own occupancy defaults to one zone; CAMBER refuses to assume any. |
| `--units ip` or `--units si` | Applies to every column whose unit the package does not declare. A unit declared in `columns.csv` wins over it. |

Some packages carry a `"timezone"` key in `manifest.json`. That key describes the stamps (`"UTC"`).
CAMBER records it in the provenance but never uses it as the site zone.

### Timestamps

- **`timestamp_utc`.** Stamps are instants (`Z` or `+00:00`). A stamp with no offset is read as
  UTC, as the column name says.
- **`timestamp`** (the historian wide-CSV profile). A stamp with no offset is local wall-clock
  time in `--timezone`. The ambiguous fall-back hour and the skipped spring-forward hour have no
  local reading.
- **Storage.** Everything is stored as naive site wall-clock time, as the rest of CAMBER expects.
- **Bad stamps.** Unparseable stamps are skipped and counted.

### Units and percent

| Quantity | `--units ip` (stored as) | `--units si` default |
|---|---|---|
| temperature | °F | °C |
| air flow | cfm | L/s |
| water flow | gpm | L/s |
| air pressure (duct static) | inH₂O | Pa |
| water pressure (loop DP) | psi | kPa |
| power | kW | kW |

A declared unit (`degF`, `°C`, `in/wc`, `kPa`, `gpm`, `W`, `%`, `fraction`, ...) wins over the
unit system. If a declared unit can't be converted or doesn't fit the point (a `%` on a
temperature), the column is left unmapped (`unit_unsupported`), not converted by assumption.

**Percent and 0–1.** Positions and speeds are stored as 0–100 %:

- A column declared `fraction` is scaled ×100.
- A column declared `%` is left as is.
- An undeclared column goes through CAMBER's usual 0–1 test, and a warning names every column it
  scaled.
- A 0/1 *command* mapped to a speed role (`fan-cmd`, pump and tower-fan commands) becomes 0/100 %,
  and is reported. CAMBER has no separate command role.

## How a column gets its role

1. **The package map is authoritative.** The equipment's sibling `column_map.json` /
   `history_wide.json` wins over the building's root `column_map.json`. The map's `points`
   (Haystack name → exact CSV header) give its columns their names, and those columns claim their
   roles first.
2. **Exact names fill only open roles.** A column the map does not name can take a role only if
   its header *is* an exact open-fdd name (`web-outside-air-humidity`, `sat`), and only for a
   role still open. Its `columns.csv` label is reported but never used, so a vendor label can't
   claim a role the map gave another column.
3. **No map: fall back.** An equipment with no map uses `columns.csv`'s `point_role`, then the
   header. In both cases the name must be one the crosswalk knows exactly; case is the only thing
   ignored.
4. **One column per role.** The first column for a role wins. Any other is reported as
   `duplicate_role`.

Every column ends with one status:

| Status | Meaning |
|---|---|
| `mapped` | became a CAMBER role |
| `no_role` | the package gives the column no point name, or its map leaves it out |
| `unknown_name` | the package names it, but the name is not in the crosswalk |
| `no_camber_role` | in the crosswalk, deliberately not mapped (the row says why) |
| `not_applicable` | the crosswalk row is limited to other equipment types |
| `unit_unsupported` | the declared unit can't be converted, or doesn't fit the point |
| `duplicate_role` | another column already supplies that CAMBER role |
| `metadata` | a row identifier (`equipment_id`, `site_id`, ...), not counted as a point |

`camber interop openfdd inspect` prints the per-status counts and the most common unmapped names.
Its `--json` lists every column with its status and reason. The counts are also recorded on the
facility.

### Equipment types

The class comes from the `equipType` stamp (or `equipment_type`), then from the inventory's
`type`. It is never taken from the equipment id. An unknown or missing stamp leaves the equipment
`UNCLASSIFIED`, which matches open-fdd's own rule.

| open-fdd `equipType` | CAMBER class |
|---|---|
| `ahu` / `rtu` | `AHU` / `RTU` |
| `vav` | `VAV` |
| `chwPlant` | `CHW_PLANT` |
| `boiler` | `BOILER` |
| `heatPump` | `HEAT_PUMP` |
| `meter` | `METER` |
| `weather` | `WEATHER` |
| `fanCoil` / `fcu` | `FCU` |
| `zone_other` | `ZONE_OTHER` (not a CAMBER family, so rules run on roles alone) |

CAMBER also keeps any other stamp its class families recognise (`HEAT_PUMP`, `AHU`, `VAV_BOX`),
upper-cased.

## The crosswalk (version 1)

The crosswalk lives in `camber/interop/openfdd/crosswalk.json`, and
`camber interop openfdd crosswalk [--json]` prints it. It maps open-fdd's documented names to
CAMBER roles: each Haystack name with its SQL role, as listed in open-fdd's *SQL rules → Haystack
map* quick reference. Lookups accept either spelling.

A change to the file bumps `crosswalk_version`, and the version is part of every ingest's content
hash. **50 of the 71 rows map. The other 21 are deliberate non-mappings, each with its reason.**

### Mapped

| open-fdd Haystack name | open-fdd SQL role | CAMBER role | Quantity | Note |
|---|---|---|---|---|
| `outside-air-temp` | `oa_t` | `oat` | temp |  |
| `web-outside-air-temp` | `web_oa_t` | `oat` | temp | web weather; lands on the WEATHER equipment, kept apart from a BAS outside-air sensor |
| `outside-air-humidity` | `oa_h` | `outdoor_rh` | percent |  |
| `web-outside-air-humidity` | `web_oa_h` | `outdoor_rh` | percent |  |
| `web-outside-air-wetbulb` | `web_wb_t` | `wetbulb_temp` | temp |  |
| `discharge-air-temp` | `sat` | `supply_air_temp` | temp |  |
| `vav-discharge-air-temp` | `vav_discharge_t` | `supply_air_temp` | temp | (vav only) |
| `vav-inlet-air-temp` | `vav_inlet_t` | `mixed_air_temp` | temp | at a terminal box CAMBER's mixed_air_temp is the entering primary air (vav only) |
| `ahu-discharge-air-temp` | `ahu_sat` | `mixed_air_temp` | temp | the serving AHU's supply air seen from the box: its entering primary air (vav only) |
| `discharge-air-temp-sp` | `sat_sp` | `supply_air_temp_sp` | temp |  |
| `mixed-air-temp` | `mat` | `mixed_air_temp` | temp |  |
| `return-air-temp` | `rat` | `return_air_temp` | temp |  |
| `zone-air-temp` | `zone_t` | `space_temp` | temp |  |
| `cooling-sp` | `cooling_sp` | `cool_sp` | temp |  |
| `heating-sp` | `heating_sp` | `heat_sp` | temp |  |
| `zone-co2` | `zone_co2` | `co2` | ppm |  |
| `cooling-coil-leaving-temp` | `cooling_coil_leaving_temp` | `cool_coil_leaving_temp` | temp |  |
| `heating-coil-leaving-temp` | `heating_coil_leaving_temp` | `heat_coil_leaving_temp` | temp |  |
| `cooling-valve` | `clg_valve_pct` | `cool_valve` | percent |  |
| `heating-valve` | `htg_valve_pct` | `heat_valve` | percent |  |
| `reheat-valve` | `reheat_valve_pct` | `heat_valve` | percent |  |
| `outside-air-damper` | `oa_damper_pct` | `oa_damper` | percent |  |
| `damper` | `damper_pct` | `damper` | percent |  |
| `duct-static-pressure` | `duct_static` | `duct_static` | air_pressure |  |
| `duct-static-pressure-sp` | `duct_static_sp` | `duct_static_sp` | air_pressure |  |
| `static-reset-request` | `static_reset_request` | `static_pressure_requests` | count |  |
| `zone-airflow` | `zone_flow` | `airflow` | air_flow | actual airflow; open-fdd's packaging guide forbids mapping the airflow setpoint here |
| `fan-status` | `fan_status` | `supply_fan_status` | binary |  |
| `fan-cmd` | `fan_cmd` | `supply_fan_speed` | percent | a 0/1 command becomes 0/100 % (reported); CAMBER has no separate fan-command role |
| `occupied` | `occ_mode` | `occupancy` | binary |  |
| `compressor-status` | `compressor_status` | `compressor_status` | binary |  |
| `chilled-water-supply-temp` | `chw_supply_t` | `chw_supply_temp` | temp |  |
| `chilled-water-return-temp` | `chw_return_t` | `chw_return_temp` | temp |  |
| `chilled-water-supply-temp-sp` | `chw_supply_sp` | `chw_supply_temp_sp` | temp |  |
| `chw-diff-pressure` | `chw_dp` | `chw_diff_press` | water_pressure |  |
| `chw-diff-pressure-sp` | `chw_dp_sp` | `chw_diff_press_sp` | water_pressure |  |
| `chw-flow` | `chw_flow` | `chw_flow` | water_flow |  |
| `chw-pump-cmd` | `chw_pump_cmd` | `chw_pump_speed` | percent | a 0/1 command becomes 0/100 % (reported) |
| `chw-pump-status` | `chw_pump_status` | `pump_status` | binary |  |
| `pump-status` | `pump_status` | `pump_status` | binary |  |
| `hw-pump-status` | `hw_pump_status` | `pump_status` | binary |  |
| `hw-pump-cmd` | `hw_pump_cmd` | `hw_pump_speed` | percent | a 0/1 command becomes 0/100 % (reported) |
| `hot-water-supply-temp` | `hw_supply_t` | `hw_supply_temp` | temp |  |
| `hot-water-return-temp` | `hw_return_t` | `hw_return_temp` | temp |  |
| `condenser-water-supply-temp` | `cw_supply_t` | `cw_supply_temp` | temp |  |
| `condenser-water-return-temp` | `cw_return_t` | `cw_return_temp` | temp |  |
| `tower-fan-cmd` | `tower_fan_cmd` | `tower_fan_speed` | percent | a 0/1 command becomes 0/100 % (reported) |
| `chiller-power` | `chiller_power` | `power` | power |  |
| `elec-power` | `electric_kw` | `power` | power |  |
| `elec-power` | `elec_power` | `power` | power |  |

### Deliberately not mapped

| open-fdd Haystack name | open-fdd SQL role | Why it is not mapped |
|---|---|---|
| `web-outside-air-dewpoint` | `web_oa_dp` | CAMBER has no outdoor dew-point role (it derives what it needs from temperature and RH) |
| `zone-air-temp-sp` | `zone_air_temp_sp` | one zone setpoint does not say whether it is the cooling or the heating setpoint; CAMBER's cool_sp / heat_sp are not guessed from it |
| `zone-air-humidity` | `zone_rh` | CAMBER has no zone humidity role |
| `cooling-coil-entering-temp` | `cooling_coil_entering_temp` | CAMBER has no coil-entering air role (it reads the mixed-air temperature) |
| `heating-coil-entering-temp` | `heating_coil_entering_temp` | CAMBER has no coil-entering air role (it reads the mixed-air temperature) |
| `preheat-leaving-temp` | `preheat_leave_t` | a preheat coil is not the heating coil CAMBER's heat_coil_leaving_temp describes on a unit with both |
| `damper-cmd` | `damper_cmd` | CAMBER has one terminal damper role, the position; a command is kept out so it never stands in for the position |
| `min-flow-sp` | `min_flow_sp` | a minimum-flow setpoint is not the active airflow setpoint CAMBER's airflow_sp is |
| `vav-total-airflow` | `vav_total_flow` | a sum of box flows is not a measured supply airflow |
| `return-fan-cmd` | `return_fan` | CAMBER has no return-fan role |
| `loop-enabled` | `loop_enabled` | CAMBER has no control-loop enable role |
| `clg-available` | `clg_available` | CAMBER has no cooling-availability role |
| `cw-pump-cmd` | `cw_pump_cmd` | CAMBER has no condenser-water pump role |
| `chiller-status` | `chiller_status` | CAMBER has no chiller run-status role (compressor_status is a DX compressor) |
| `chiller-cmd` | `chiller_cmd` | CAMBER has no chiller command role |
| `chiller-amps` | `chiller_amps` | CAMBER has no current role; amps are not converted to kW without voltage and power factor |
| `chiller-current` | `chiller_current` | CAMBER has no current role; amps are not converted to kW without voltage and power factor |
| `building-zone-load-satisfied` | `building_zone_load_satisfied` | CAMBER has no building load-satisfied role |
| `building-ahu-load-satisfied` | `building_ahu_load_satisfied` | CAMBER has no building load-satisfied role |
| `elec-energy` | `kwh` | a cumulative energy reading must be differenced into a rate before CAMBER's power role; not done silently |
| `elec-energy` | `electric_kwh` | a cumulative energy reading must be differenced into a rate before CAMBER's power role; not done silently |

**Gaps found on real packages.** Open-fdd packages sometimes use point names that are not in the
documented vocabulary, such as `cooling-setpoint`, `effective-setpoint`, `entering-water-temp`,
`leaving-water-temp`, `differential-pressure` and `pump-1-speed`. These stay `unknown_name` until
the vocabulary documents them. A weather folder without a points map maps only the headers that
are documented names. open-fdd's own docs list that as a known gap.

## Ingest

```bash
camber interop openfdd ingest PKG --timezone America/Chicago --units ip \
    (--store DIR | --workspace ROOT) [--activate] [--facility-id ID] [--name NAME] \
    [--building B] [--resample 15min|native] [--equip-types FILE] [--reason TEXT] \
    [--force] [--config-out run.json] [--json result.json]
```

- **Facility id.** `--facility-id` defaults to a deterministic id derived from the building id.
- **Workspace.** The facility is registered through the portfolio lifecycle. It starts
  `provisioning` unless you pass `--activate`; `camber run` skips it until it is activated. The
  write takes the workspace lock and appends one `interop.openfdd.ingest` audit record. A
  facility in any other state (suspended, offboarding, ...) is refused.
- **Store.** A new facility is registered `active`, as a store write always has been.
- **Replace semantics.** Data is staged and swapped in, so it replaces the facility's trends.
  Ingest a newer export of the same building to update it.
- **Idempotent.** An unchanged ingest is skipped; `--force` redoes it. "Unchanged" means the
  same files, crosswalk, time zone, units, resample and column decisions.
- **Resample.** The default `--resample 15min` takes the mean, so a status point becomes its duty
  fraction. `native` keeps the package grid; there, the repeated fall-back hour keeps both
  readings.
- **Provenance.** Recorded on the facility's registry entry under `openfdd`:
  - the source (package or historian), the schema version and grid minutes;
  - the manifest's own `timezone` key;
  - the time zone and unit system given;
  - the crosswalk version and its docs commit;
  - the zip's sha256 and the **sha256 of every file read**;
  - the content hash and the coverage counts;
  - each equipment's class, stamp, parent AHU and unmapped columns;
  - the ignored members, the data span and the CAMBER version.
- **Clock.** A store config's `source.timezone` defaults to the ingested zone.

The Python API is `ingest_package(...)`, plus `read_package(...)` / `read_historian(...)` to
read without writing. Each returns frames in CAMBER roles with every column's mapping.

### The starting config

`--config-out` writes a `camber run` config for the facility. It is a starting point to review:

- the facility, its time zone and an hourly resample;
- every class present;
- the package weather as `shared_oat`;
- the built-in rules the mapped roles can run, for classes the rules are written for (drift and
  the 62.1 ventilation rules need their own sections and are left out);
- for each meter class with power, when there is an outdoor temperature, a daily M&V baseline
  over the first year of data.

## The process boundary

open-fdd's data-model ADR keeps external analytics out of the product request path: the product
path is Rust/DataFusion. CAMBER therefore runs **beside** open-fdd, as a separate process that
reads files and writes a JSON file. An open-fdd agent, pipeline or scheduler can call it and read
the result back. CAMBER never runs inside an open-fdd request and never calls an open-fdd write
or command tool.

```text
open-fdd edge ──export──> package dir / zip ──> camber interop openfdd ingest ──> CAMBER store
                                                                                   │
open-fdd agent / pipeline <── findings.json <── camber interop openfdd findings ───┘
```

### Worked example

```bash
# 1. a workspace, once
camber portfolio init ws

# 2. ingest the exported package (re-run after each export; unchanged inputs are skipped)
camber interop openfdd ingest exports/BLDG_1 --timezone America/Chicago --units ip \
    --workspace ws --activate --reason "monthly open-fdd export" --config-out run.json

# 3a. rules, M&V and drift in one call -> one engine-labelled JSON document
camber interop openfdd findings run.json --out findings.json

# 3b. or CAMBER's native outputs
camber run run.json --out out/          # out/findings.json (a list of CAMBER Findings)
camber mv run run.json --out out/       # out/mv_findings.json
camber mv freeze run.json --reason "baseline accepted" --apply   # freeze for later reporting
```

`findings.json` from step 3a looks like this (abridged):

```json
{
  "schema": "findings-exchange",
  "schema_version": "0.1-draft",
  "engine": {"name": "camber", "version": "0.99.0"},
  "facility": "openfdd-bldg-1-…",
  "window": {"start": "2026-01-01T00:00:00", "end": "2026-06-30T23:45:00", "tz": "America/Chicago"},
  "sources": [{"source": "open-fdd package (openfdd_package_v1)", "building_id": "BLDG_1",
               "content_hash": "…", "crosswalk_version": 1, "timezone": "America/Chicago",
               "unit_system": "ip"}],
  "counts": {"fault": 1, "warn": 2, "ok": 8, "info": 12, "declined": 3, "not_evaluated": 2},
  "findings": [
    {"engine": {"name": "camber", "version": "0.99.0"}, "kind": "rule",
     "rule_id": "supply_air_reset", "equip": "AHU_1", "facility": "openfdd-bldg-1-…",
     "window": {"…": "…"}, "status": "warn", "declined_reason": null,
     "magnitude": {"fault_hours": null, "fault_pct": null, "denominator_definition": null},
     "summary": "AHU_1: … NO RESET (SAT pinned low regardless of OAT)", "evidence": [], "caveats": [],
     "root_cause_group": null, "cost": null, "confidence": null, "review": null,
     "native": {"severity": "warn", "metrics": {"…": "…"}}},
    {"kind": "mv", "rule_id": "mv_baseline", "equip": "METER_1", "status": "ok", "…": "…"}
  ]
}
```

## Findings exchange schema (draft 0.1)

This is the draft for #22 item 3. `findings_document(...)` builds it and `run_findings(config)`
runs a config into it. Top level:

| Field | Meaning |
|---|---|
| `schema`, `schema_version` | `findings-exchange`, `0.1-draft` |
| `engine` | `{name, version}` of the producing engine |
| `facility`, `window` | the facility and `{start, end, tz}` analysed: the config's `source.start` / `end`, else the ingested span |
| `sources` | data provenance (the open-fdd package hash, crosswalk version, zone, units) |
| `counts` | records by status |
| `findings` | the records |

Each record:

| Field | Meaning |
|---|---|
| `engine` | repeated on every record so records stay labelled when collected with another engine's |
| `kind` | `rule` or `mv` |
| `rule_id`, `equip`, `facility`, `window` | what was judged, where and over which period |
| `status` | `fault`, `warn`, `ok`, `info`, `declined`, `not_evaluated` |
| `declined_reason` | why a `declined` or `not_evaluated` record has no verdict |
| `magnitude` | `{fault_hours, fault_pct, denominator_definition}`, filled only from metrics that mean exactly that, else `null` |
| `summary`, `evidence`, `caveats` | the finding's text, evidence descriptor and caveats |
| `root_cause_group`, `cost`, `confidence`, `review` | reserved; `null` until CAMBER produces them |
| `native` | the engine's own severity and metrics, unstandardised |

Two statuses keep apart cases that are easy to blur:

- **`declined`** — the rule ran and said it could not judge: not applicable to the equipment
  class, data outside the conditions it needs, or an M&V saving outside the baseline's
  conditions.
- **`not_evaluated`** — the rule applied but never ran: a required input is missing, or there is
  no data.

Neither is a pass, and neither is low-quality evidence. `null` means "not provided", never zero.

**Versioning.**

- Adding a field or a status value bumps the minor version.
- Renaming or removing one bumps the major version.
- Until both projects agree on the shape, the version keeps its `-draft` suffix.

## Questions for open-fdd

These are open, and we'd value the author's view.

1. **Time zone and units.** Is there a stable, documented place for a building's IANA time zone
   and unit system that an external reader may depend on? A manifest field would do. Today
   `manifest.json` documents only `grid_minutes`, and some packages carry a `"timezone"` that
   describes the stamps.
2. **Historian layout.** Is the canonical Parquet layout (`history/building_id=/equipment_id=/
   year=/month=`) stable enough for an external reader, or should readers stay on the package
   export? Could `equipType` be carried alongside it?
3. **Vocabulary.** Names seen in packages but not in the documented quick reference
   (`cooling-setpoint`, `effective-setpoint`, `entering-water-temp`, `leaving-water-temp`,
   `differential-pressure`, `pump-N-speed`): are they meant to be part of the vocabulary? If so,
   which SQL roles do they become?
4. **Weather.** Should a weather folder always carry a points map, or should readers treat
   documented header names as the contract?
5. **Commands.** `fan-cmd` and the pump and tower commands are sometimes 0/1, sometimes %. Is
   there a declared way to tell them apart?
