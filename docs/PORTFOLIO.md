# Portfolio lifecycle

Facilities join and leave a portfolio. CAMBER's **portfolio workspace** keeps each facility's whole
footprint in one place, gives every facility a lifecycle state, and records every change with who
made it and why. This page describes what ships now (steps 1 to 3 of the lifecycle work: the
workspace, facility identity, and offboarding with export bundles) and what the later steps add.

> **Provisional.** `camber.portfolio` and the `camber portfolio` / `camber facility` commands are
> provisional (see [API-STABILITY.md](API-STABILITY.md)): names may still change in a minor release.

## The workspace

```
<portfolio>/
  _portfolio.json   schema version, retention policy defaults, per-facility overrides, legal holds
  _audit.ndjson     append-only audit log: one JSON line per action
  _lock             single-writer advisory lock
  store/            the ParquetStore root (registry v2, tombstones, a _workspace.json back-pointer)
  rollups/          reserved: downsampled stores (created by a later release)
  state/<fid>/      per facility: faults, drift baselines, migrated originals, manifest.json
  archive/<fid>/    export bundles: <bundle_id>/ with a sha256 manifest (see Offboarding)
```

The reserved directories are created by the releases that use them. `state/<fid>/` is created the
first time a run, a `drift freeze` or a migration writes something for that facility, and
`archive/<fid>/` the first time the facility is exported or offboarded.

```
camber portfolio init ws                     # create it (idempotent)
export CAMBER_PORTFOLIO=$PWD/ws              # or pass --workspace ws to every command
camber facility add "North Campus" --owner "facilities team" --tag east --reason "new contract"
camber facility activate north-campus-1a2b3c --reason "points mapped, data flowing"
camber datasets ingest lbnl-sdahu --store ws/store     # any store writer works unchanged
camber portfolio status
camber portfolio audit
```

The `camber facility` and `camber portfolio status|audit` commands find the workspace in this
order: `--workspace PATH`, then `$CAMBER_PORTFOLIO`, then the current directory.

### Existing stores keep working

A plain `ParquetStore` directory that is not in a workspace works exactly as before: no lock, no
audit log. Registry entries written by older versions have no lifecycle fields. They read as
`state: "active"` with unknown (`null`) dates, and nothing is rewritten until the entry is next
changed.

### Adopting an existing store

```
camber portfolio adopt /data/lake [ROOT] --reason "bring the lake under lifecycle"
```

`adopt` wraps an existing store directory in a workspace **without moving anything**. `ROOT`
defaults to the store's parent directory. It writes `ROOT/_portfolio.json`, which records the
store's path: relative when the store is inside `ROOT`, absolute otherwise. It also writes a small
`_workspace.json` marker inside the store that points back at `ROOT`. From then on the registry
takes the lock and audits, whichever tool writes to the store.

Existing facilities keep their data and read as `active`. Partitions that have data but no
registry entry are listed as unregistered (and treated as active). Nothing is registered on
their behalf until a lifecycle command touches them. Adopting the same store into the same
workspace again changes nothing. Adopting it into a *different* workspace is refused.

If you later move the store directory, update `store` in `_portfolio.json`. The marker's path is
relative, so moving the workspace and its store together needs no edit.

## Facility identity

`facility_id` is the only key. It is path-safe, it never changes, and it is **never reused**:

- **Tombstones.** Removing a facility from the registry (`FacilityRegistry.remove`,
  `ParquetStore.drop_facility(forget=True)`, `camber datasets remove --purge-store`) leaves a
  tombstone in `store/_tombstones.json`. Registering that id again, or writing data under it, is
  refused, so an old building's history can never be joined to a new one. A catalog dataset is
  the one exception: its id is derived from the dataset itself, so re-ingesting the *same*
  dataset reclaims its own tombstone. That reclaim is audited.
- **Case.** A new id that differs from a known one only by letter case (`DemoSite` / `demosite`)
  is refused. On a case-insensitive filesystem the two would share one directory.
  `make_facility_id()` emits lowercase.
- **Names.** `name` is the name the facility was registered under and never changes. The
  existing `register()` collision guard compares it. `display_name` is the editable one:
  `camber facility rename` changes it, and `ParquetStore.facility_name()`, reports and the read
  API's `display_name` show it.

- **State.** Fault history, drift baselines, CMMS ticket fingerprints and findings exports are
  keyed by `facility_id` inside a workspace (see [Per-facility state](#per-facility-state)), so a
  rename never orphans them. State written before 0.86 is keyed by the free-text site name; move
  it with [`camber portfolio migrate`](#migrating-site-keyed-state).

Registry v2 entries add `state`, `created_at`, `state_changed_at`, `display_name`, `owner`,
`portfolio` (tags) and `notes`. `register()` keeps its old meaning for existing callers. A
facility it creates (a store write with `name=`, a dataset ingest) starts `active`, and
`register()` cannot change the lifecycle fields.

Since 0.94 an entry may also carry `private: true` (`camber facility add --private`, or `camber
facility private <id> --reason R`; audited as `facility.private`). A private facility's weather and
price requests default to `offline`, and it can opt in to `coarse` but never to `public`. The field
is written only when set. See [WEATHER.md](WEATHER.md#private-facilities).

## Lifecycle states

```
provisioning -> active <-> suspended -> offboarding -> archived -> purged (tombstone)
```

| Action | From | To | Deletes data | Since |
|---|---|---|---|---|
| `activate` | provisioning | active | no | 0.86 |
| `suspend` | active | suspended | no | 0.86 |
| `resume` | suspended | active | no | 0.86 |
| `offboard` | active, suspended | offboarding | no (30-day reversible grace, automatic export bundle) | 0.95 |
| `restore` | offboarding, archived | active | no | 0.95 |
| `archive` | offboarding | archived | **yes**: hot data (the verified bundle is kept) | 0.95 |
| `purge` | archived | purged | **yes**: everything but the tombstone and audit record | 0.95 |

- `camber facility add` creates a facility as `provisioning`, or as `active` with `--activate`.
  Automated paths (store writes, `camber datasets ingest`) create facilities as `active`.
- An invalid transition is refused, and the error lists the actions allowed from the current
  state. `purged` is final.
- A **legal hold** blocks every transition that deletes data (`archive`, `purge`). Holds are
  stored in `_portfolio.json`.
- `offboard`, `restore`, `archive` and `purge` are described in
  [Offboarding, archiving and purging](#offboarding-archiving-and-purging).

### What `suspended` means for analyses

Analyses run on **active** facilities only.

- `ParquetStore.active_facilities()` lists the facilities in the store whose state is `active`.
  An unregistered facility counts as active.
- `camber.resolve.discover_store(...)` returns nothing for a non-active facility and emits a
  `UserWarning`. Pass `include_inactive=True` to analyse it anyway.
- A store-backed config (`"source": {"kind": "store", ...}`) whose facility is not active runs
  with no equipment and warns once. Set `"include_inactive": true` in the source to override.
- `camber mv` skips a non-active facility with a message: nothing is read, frozen or
  rebaselined while it is suspended. Its frozen M&V baselines stay as they are.

Suspending changes nothing else. Data is kept, and writes (dataset ingest, edge landing) still
succeed. Quarantining edge uploads for non-active facilities is a later step. An **archived**
facility refuses store writes: its data lives in its bundle, and new rows would never be in it.
Restore it first.

The read-only API (`camber serve`) shows each facility's `state` and `display_name` on
`/facilities`, and the `/ui` selector marks non-active facilities. The API stays GET-only.
Lifecycle changes are CLI or library only.

## Per-facility state

Inside a workspace, everything CAMBER keeps *about* a facility, other than its trend data, lives
under a path derived from its id, or is listed in its manifest:

```
state/<fid>/
  faults.json        fault lifecycle (FaultLifecycle), fingerprint = sha1(facility_id, equip, rule)
  baselines.json     frozen drift baselines (BaselineStore), sha1(facility_id, equip, kind)
  mv_baselines.json  versioned M&V baselines (MVBaselineStore), every version kept, with provenance
                     (bill-based M&V baselines too, as kind mv_bills)
  weather_audit.ndjson  every request to a weather or price service (WEATHER.md#privacy)
  migrated/          the original records each migrated legacy file held for this facility
  manifest.json      every file above with sha256 and size, plus external artifacts
```

A config run is **inside a workspace** when its store source's store belongs to one, or when a
folder config names one with `"workspace": "<root>"`. Then:

- The config's facility is its `source.facility_id` (store source) or its top-level
  `"facility_id"` (folder source). A folder config without one falls back to
  `make_facility_id(site)` and warns, because a derived id changes whenever the site label does.
  The facility must be registered (`camber facility add NAME --id ID`), and a tombstoned id is
  refused. As with store sources, a non-active facility is skipped unless `include_inactive` is set.
- The drift baseline store and the fault history are opened **for that facility**. `site` is only
  a label on new records. `drift.store` and `faults.store` default to `state/<fid>/baselines.json`
  and `state/<fid>/faults.json` when the config names no path. An explicit path still works; the
  file is then listed in the manifest as external.
- The optional `faults` section (`{"store": ..., "run_id": ..., "auto_resolve_absent": false}`)
  folds each run's actionable findings into the fault history. `run_id` defaults to the current UTC
  time.
- Reports and outputs a run writes (`report.out_html` / `out_text`, `camber run --out`,
  `camber report --out`, `camber drift run|report --out`) stay where the config or command put
  them, and are listed in the manifest as external artifacts (they are regenerable).
- `camber drift freeze` and `camber drift accept` take the lock without waiting and are audited
  (`drift.freeze`, `drift.accept`); `freeze` needs `--reason` inside a workspace.
- The M&V baseline store is `state/<fid>/mv_baselines.json` (`{"schema": 1, "mv_baselines":
  [...]}`). `camber mv freeze`, `camber mv rebaseline` and `camber mv adjust` are its only
  writers: each needs `--reason`, is a dry run unless `--apply`, takes the lock without waiting
  and is audited (`mv.freeze`, `mv.rebaseline`, `mv.adjust`). Runs only read it. A config naming
  a store elsewhere (top-level `"mv_store"`) is migrated like a drift store: `camber portfolio
  migrate` moves every version into `state/<fid>/` and leaves a redirect stub.

`camber facility show <id>` prints the manifest. Outside a workspace nothing changes: stores stay
keyed by the site string, and folder configs do not warn about a missing `facility_id`.

Tickets and exports take `facility_id=` too (`finding_to_ticket(f, site=..., facility_id=...)`,
`findings_to_frame(..., facility_id=...)`). The ticket's `fingerprint` is then keyed by the
facility, and a `legacy_fingerprint` field carries the old site-keyed value. A CMMS adapter can
use it to match a ticket opened before the migration: an outbound ticket cannot be re-keyed after
it is sent.

## Migrating site-keyed state

```
camber portfolio migrate [FILE ...] [--config CFG ...] [--map "SITE=ID" ...]    # dry run (default)
camber portfolio migrate ... --apply --reason "0.86 identity migration"
```

- **Inputs.** `FILE`s are legacy fault or baseline stores (the JSON files `FaultLifecycle` and
  `BaselineStore` write). `--config` adds the files a config names (`drift.store`,
  `faults.store`). Records in them with a blank site, or with the config's own `site`, belong to
  the config's facility. The config's report outputs are listed in that facility's manifest.
- **Mapping.** Each record's `site` is mapped to a facility id through the registry: the id, the
  registered `name`, the current `display_name`, and every earlier display name (from the audit
  log's `facility.rename` records). Labels are compared exactly first, then loosely: Unicode NFC,
  case-folded, whitespace collapsed. A label whose `make_facility_id()` is a known facility also
  maps, since that is how an unpinned folder config derives its id. Records that already carry a
  `facility_id` keep it.
- **Refusals.** A label that matches **more than one facility** is ambiguous and is never guessed.
  The same goes for a label that matches only a **tombstoned** facility, or a live facility and a
  tombstoned one. So does an unknown label, a blank label with no config to attribute it, or a
  missing or unrecognised file. The plan lists every such label with its candidates, and
  `--apply` then writes nothing (exit code 1). Resolve a label explicitly with
  `--map "SITE=FACILITY_ID"`. A mapping to a tombstoned id is refused too.
- **Apply** (under the lock, audited). For each facility, the migration does four things. It
  keeps the original records under `state/<fid>/migrated/<kind>-<sha12>.json`. It merges the
  re-keyed records into `state/<fid>/faults.json` / `baselines.json`, keeping each old fingerprint
  in the record's `aliases`. It rewrites the manifest with a sha256 per file and the source file's
  hash. And it replaces the legacy file with a **redirect stub**. A config or script that still
  names the old path keeps working: a store opened for a facility reads and writes that facility's
  `state/<fid>/` file. A store opened without a facility gets a read-only merged view, and saving
  it is refused. The stub stays in the facility's manifest as a `redirect` entry, and runs
  through it record the real `state/<fid>/` file. The audit log gets one `portfolio.migrate`
  record, plus one `facility.migrate` record per facility with its counts.
- **Merges.** When one facility's history was split across two labels (a rename before 0.86
  orphaned it), the records are combined deterministically. For faults: the earliest
  `first_seen`, the latest `last_seen`, summed occurrences, the later record's workflow state, and
  both notes. For baselines: the reference keyed under the current display name stays live (it is
  what runs were reading), else the later `frozen_at`. The other baseline is filed under
  `history`, never discarded. The plan lists every merge.
- **Idempotent.** A stub is recognised as already migrated. A record whose old fingerprint a
  target already carries is skipped, so an apply interrupted halfway can simply be re-run.
  Re-applying a finished migration changes nothing and writes no audit line.

**Compatibility read path (deprecated).** You don't have to migrate before upgrading. A store
opened for a facility (every config run inside a workspace) also reads site-keyed records whose
`site` is one of the facility's unambiguous labels. It re-keys them in memory, keeps the old
fingerprint as an alias (`FaultLifecycle.get(old_fp)` still resolves), and emits a
`DeprecationWarning`. The path is deprecated since 0.86 and will be removed in 2.0; see
[API-STABILITY.md](API-STABILITY.md#deprecated).

## Offboarding, archiving and purging

A facility leaves in three steps. Each step is a separate, audited admin command, and each
deletes less than the next:

```
camber facility offboard <id>                                     # dry run: what would happen
camber facility offboard <id> --apply --reason R --yes            # export, start the grace period
camber facility restore  <id> --apply --reason R --yes            # changed your mind: back to active
camber facility archive  <id> --apply --reason R --yes            # after the grace period
camber facility restore  <id> --apply --reason R --yes            # archived -> active, from the bundle
camber facility purge    <id> --apply --reason R --confirm <id>   # irreversible
camber facility export   <id> --reason R                          # a bundle now, nothing else changes
camber facility bundles  <id> [--verify]                          # list (and re-hash) the bundles
```

- **Offboard** (`active`/`suspended` -> `offboarding`) first writes a verified export bundle, then
  records the state with a grace deadline (`offboarding_grace_days` in `_portfolio.json`, default
  30). Nothing is deleted. Analyses skip the facility, as for `suspended`.
- **Archive** (`offboarding` -> `archived`) deletes the **hot data** and keeps the bundle. It is
  refused before the grace period ends unless `--skip-grace` is given (audited), and refused
  under a legal hold. The latest bundle is reused only if it still matches the data (its
  fingerprint) and verifies; anything written during the grace period triggers a fresh
  `archive` bundle first. Hot data is: the store partition `store/facility_id=<fid>/`, the
  rollup partitions under `rollups/<freq>/`, the whole `state/<fid>/` directory (faults, drift
  and M&V baselines including billing baselines, the weather audit log, migrated originals,
  reports, the manifest), and **report** files the manifest lists outside the workspace, but
  only if they are unchanged since CAMBER wrote them. Other external artifacts (a fault or
  baseline store at a path a config chose, which could be shared) are bundled but left in place,
  and the plan lists them. The registry entry stays, marked `archived` with the bundle id.
- **Restore** (`offboarding`/`archived` -> `active`). From `offboarding` it only changes the
  state. From `archived` it verifies the bundle (the one archive recorded, or `--bundle ID`),
  puts every tree back, re-hashes every restored file against the manifest, and only then marks
  the facility `active`. External report files come back only where their path is free.
- **Purge** (`archived` -> `purged`) deletes the bundles and anything left of the facility, and
  its retention override. What remains is the tombstone in `store/_tombstones.json` (id,
  registered and display names, `state: purged`, `purged_at`, reason) and the audit log. The id
  is never reused. Purge accepts only the typed id (`--confirm <id>`, or typing it at the prompt),
  never `--yes`.

### Safety rules

- **Dry run by default.** Without `--apply`, each command prints the plan (what it would export
  and delete, the bundle it would use) and changes nothing. `--json` prints it as JSON.
- **Confirmation.** `--apply` needs `--reason` (audited) and a confirmation: `--yes`, or the
  typed facility id (`--confirm <id>`, or an interactive prompt when stdin is a terminal). Purge
  needs the typed id.
- **Lock and audit.** An applied command takes the workspace lock without waiting and writes one
  audit record with the OS user, host, reason, the bundle id, its fingerprint and counts.
- **Legal hold.** A hold blocks archive and purge (dry runs included). Offboard and restore delete
  nothing, so a hold allows them.

### Export bundles

```
archive/<fid>/<bundle_id>/            <bundle_id> = <UTC yyyymmddThhmmssZ>-<offboard|archive|manual>
  manifest.json                       every file below with sha256 and size, counts, fingerprint
  manifest.sha256                     sha256 of manifest.json itself
  registry.json                       the registry entry, catalog keys, retention override, hold
  audit.ndjson                        the facility's audit records at export time (a copy)
  store/facility_id=<fid>/...         raw trend partitions, byte for byte
  rollups/<freq>/facility_id=<fid>/.. rollup partitions
  state/...                           everything under state/<fid>/
  external/<n>-<name>                 artifacts the manifest lists outside state/<fid>/
```

A bundle is a plain directory: readable without CAMBER, and copyable to cold storage. The manifest
(`"schema": "camber.bundle/1"`) records the facility, the kind, who made it and why, the CAMBER
version, row and file counts, the original path of each external artifact, and a **fingerprint**:
a sha256 over the (path, sha256) list of the hot files. `camber facility bundles <id> --verify`
re-hashes a bundle: the manifest against `manifest.sha256`, every file's size and sha256, and no
file the manifest does not list. Restore refuses a bundle that fails, before touching anything.

### Crash safety

A crash (or `kill -9`, or a power cut) at any point leaves a state the next lifecycle or
retention command finishes or rolls back, before doing anything else (`portfolio.recover` in the
audit log):

- **Replacing a tree** (a bundle being written, a partition being restored) builds the new
  content in `_swap-<name>.new` beside it and swaps it in only after a `_swap-<name>.ready`
  marker is fsynced. Recovery rolls a ready swap forward and discards an unready one, so a tree
  holds exactly the old or exactly the new content.
- **Deleting a tree** renames it to `_trash-<name>-<token>` in one atomic step, then removes it.
  Readers never see a half-deleted partition; recovery removes the leftovers. Names starting
  with `_` are invisible to the store's readers.
- **Ordering.** Offboard exports before it changes the state. Archive records `archived` with
  `hot_deleted: false` before deleting, and recovery finishes the deletion only after
  re-verifying the bundle (a damaged bundle blocks it and is reported). Restore changes the state
  last. Purge writes the tombstone with `purge_pending` first, and recovery finishes it.

The tests simulate a crash at each of these points, including a child process killed mid-archive.

## The audit log

`_audit.ndjson` gets one JSON object per action:

```json
{"ts": "2026-09-26T20:17:19.833Z", "actor": "jdoe", "host": "ops-01", "action": "facility.suspend",
 "facility_id": "north-campus-1a2b3c", "from_state": "active", "to_state": "suspended",
 "reason": "contract paused", "details": {}}
```

- `actor` is the OS user (`getpass.getuser()`) and `host` is the machine. See
  [SECURITY.md](SECURITY.md#8-portfolio-administration) for what that does and does not prove.
- Every mutating command requires `--reason`, which is recorded.
- Each line is written with a single `O_APPEND` write and `fsync`ed before the command returns.
  CAMBER never rewrites or truncates the file. A torn trailing line (a crash mid-write) is
  skipped when reading.
- Actions: `portfolio.init`, `portfolio.adopt`, `portfolio.migrate`, `portfolio.recover`,
  `facility.add`, `facility.register` (created by a store write or ingest),
  `facility.activate|suspend|resume`, `facility.rename`, `facility.migrate`, `facility.remove`
  (tombstoned), `facility.reclaim`, `facility.export`,
  `facility.offboard|archive|restore|purge`, `drift.freeze`, `drift.accept`.
- Routine analysis runs are not audited. A run that folds faults or writes reports updates the
  facility's manifest, not the audit log.

`camber portfolio audit [--facility ID] [--json]` prints it.

## The lock

Only one writer changes the workspace at a time. The lock is `fcntl.flock` on POSIX and
`msvcrt.locking` on Windows, taken on `_lock`.

- Admin commands (`camber facility ...`, `adopt`) do not wait. A second concurrent command fails
  at once with `portfolio is locked by <pid>@<host> since <ts>`.
- `camber portfolio migrate --apply`, `camber drift freeze` and `camber drift accept` inside a
  workspace behave like admin commands: they fail at once. A dry run takes no lock.
- Automated writes inside a workspace wait up to 30 seconds before giving the same error. These
  are: a store write that registers a facility, a dataset ingest, and a config run's fault fold
  and manifest update.
- The lock is re-entrant within one process.
- **Stale locks.** The operating system drops the lock when the holding process exits, including
  after a crash or `kill -9`. A leftover `_lock` file therefore never blocks anyone. Its holder
  line is just stale text, overwritten by the next writer. If the refusal names a pid on the same
  host that no longer exists, retry: the error says so.
- **Filesystems.** Advisory locks are unreliable on some network filesystems (older NFS, some SMB
  configurations). Keep the workspace on a local disk, or on a filesystem with working `flock`.

Read paths (`camber serve`, `ReadAPI`, a `camber run` that writes no per-facility state) never
take the lock.

## Retention policy (stored now, enforced later)

`_portfolio.json` holds the agreed defaults. Precedence is **legal hold > facility override >
portfolio default**, and the audit log is never deleted whatever an override says.

| Data class | Key | Default |
|---|---|---|
| Raw trends | `raw_trends` | 25 months |
| Hourly rollups | `hourly_rollups` | 7 years |
| Daily rollups | `daily_rollups` | indefinite |
| Findings / fault history | `findings` | 7 years |
| Drift baselines | `drift_baselines` | life of the equipment, last 10 accepted versions |
| M&V baselines | `mv_baselines` | indefinite, **all versions** (past reported savings depend on superseded ones) |
| Reports / outputs | `reports` | last 12 per facility |
| Audit log | `audit` | never deleted |

`camber facility show <id>` prints the effective policy for one facility, with where each rule
comes from.

## Library use

```python
from camber.portfolio import Portfolio

pf = Portfolio.init("ws")
fid = pf.add_facility("North Campus", reason="new contract")["facility_id"]
pf.transition(fid, "activate", reason="data flowing")
pf.transition(fid, "suspend", reason="contract paused")
pf.store.active_facilities()  # -> [] while suspended
pf.audit_log(facility_id=fid)

pf.state_dir(fid)  # <root>/state/<fid>
pf.manifest(fid)  # files with sha256, external artifacts, migrations
plan = pf.migrate(["old/faults.json"], configs=["site.json"])  # dry run
pf.migrate(["old/faults.json"], mapping={"Annex": fid}, apply=True, reason="0.86 migration")

from camber.faultlifecycle import FaultLifecycle

lc = FaultLifecycle.load(pf.state_dir(fid) + "/faults.json", facility_id=fid)
lc.update(findings, run_id="2026-09-26T06:00", site="any label")  # keyed by fid
```

`camber.portfolio.transition(state, action, legal_hold=False)` is the pure state machine, with no
I/O. `allowed_actions(state)` lists what may follow.

```python
pf.offboard(fid, reason="contract ended")  # the plan (a dry run)
pf.offboard(fid, reason="contract ended", apply=True)  # bundle, then offboarding
pf.archive(fid, reason="grace period over", apply=True)  # hot data deleted, bundle kept
pf.restore(fid, reason="client returned", apply=True)  # verified round trip
pf.purge(fid, reason="retention over", apply=True, confirm=fid)  # tombstone + audit only
pf.bundles(fid, verify=True)
pf.recover()  # finish whatever a crash interrupted (every command runs it first)
```

`Portfolio.transition(fid, "offboard" | "archive" | "restore", reason=...)` runs the same
cascade with `apply=True`; `purge` must go through `Portfolio.purge(..., confirm=fid)`.

## What the later steps add

2. ~~**Identity migration.**~~ Shipped: see [Per-facility state](#per-facility-state) and
   [Migrating site-keyed state](#migrating-site-keyed-state).
3. ~~**Offboard, archive, restore and purge.**~~ Shipped in 0.95: see
   [Offboarding, archiving and purging](#offboarding-archiving-and-purging).
4. **Retention.** Month partitions, `camber retention show|set|override|hold|release|apply
   [--dry-run]` (roll up, verify row counts, then prune, under the lock).
5. **Edge and cloud.** Reconciliation of landed objects against the registry, quarantine of
   uploads for non-active facilities, edge decommission, and bucket lifecycle rules generated
   from the policy.
