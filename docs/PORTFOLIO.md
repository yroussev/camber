# Portfolio lifecycle

Facilities join and leave a portfolio. CAMBER's **portfolio workspace** keeps each facility's whole
footprint in one place, gives every facility a lifecycle state, and records every change with who
made it and why. This page describes what ships now (step 1 of the lifecycle work) and what the
later steps add.

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
  state/<fid>/      reserved: faults, drift baselines, reports per facility (later release)
  archive/<fid>/    reserved: export bundles (later release)
```

The reserved directories are created by the releases that use them.

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

Registry v2 entries add `state`, `created_at`, `state_changed_at`, `display_name`, `owner`,
`portfolio` (tags) and `notes`. `register()` keeps its old meaning for existing callers. A
facility it creates (a store write with `name=`, a dataset ingest) starts `active`, and
`register()` cannot change the lifecycle fields.

## Lifecycle states

```
provisioning -> active <-> suspended -> offboarding -> archived -> purged (tombstone)
```

| Action | From | To | Deletes data | Available |
|---|---|---|---|---|
| `activate` | provisioning | active | no | now |
| `suspend` | active | suspended | no | now |
| `resume` | suspended | active | no | now |
| `offboard` | active, suspended | offboarding | no (30-day reversible grace, automatic export bundle) | later release |
| `restore` | offboarding, archived | active | no | later release |
| `archive` | offboarding | archived | **yes**: hot data (the bundle is kept) | later release |
| `purge` | archived | purged | **yes**: everything but the tombstone and audit record | later release |

- `camber facility add` creates a facility as `provisioning`, or as `active` with `--activate`.
  Automated paths (store writes, `camber datasets ingest`) create facilities as `active`.
- An invalid transition is refused, and the error lists the actions allowed from the current
  state. `purged` is final.
- A **legal hold** blocks every transition that deletes data (`archive`, `purge`). Holds are
  stored in `_portfolio.json` now. The commands to set and release them arrive with retention.
- `offboard`, `restore`, `archive` and `purge` are already in the state machine and tested, but
  the CLI answers "available in a later release" (exit code 2) until the export bundle and the
  deletion cascade exist.

### What `suspended` means for analyses

Analyses run on **active** facilities only.

- `ParquetStore.active_facilities()` lists the facilities in the store whose state is `active`.
  An unregistered facility counts as active.
- `camber.resolve.discover_store(...)` returns nothing for a non-active facility and emits a
  `UserWarning`. Pass `include_inactive=True` to analyse it anyway.
- A store-backed config (`"source": {"kind": "store", ...}`) whose facility is not active runs
  with no equipment and warns once. Set `"include_inactive": true` in the source to override.

Suspending changes nothing else. Data is kept, and writes (dataset ingest, edge landing) still
succeed. Quarantining edge uploads for non-active facilities is a later step.

The read-only API (`camber serve`) shows each facility's `state` and `display_name` on
`/facilities`, and the `/ui` selector marks non-active facilities. The API stays GET-only.
Lifecycle changes are CLI or library only.

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
- Actions: `portfolio.init`, `portfolio.adopt`, `facility.add`, `facility.register` (created by a
  store write or ingest), `facility.activate|suspend|resume`, `facility.rename`,
  `facility.remove` (tombstoned), `facility.reclaim`.

`camber portfolio audit [--facility ID] [--json]` prints it.

## The lock

Only one writer changes the workspace at a time. The lock is `fcntl.flock` on POSIX and
`msvcrt.locking` on Windows, taken on `_lock`.

- Admin commands (`camber facility ...`, `adopt`) do not wait. A second concurrent command fails
  at once with `portfolio is locked by <pid>@<host> since <ts>`.
- Automated registry writes inside a workspace (a store write that registers a facility, a
  dataset ingest) wait up to `FacilityRegistry.lock_timeout` seconds (30 by default) before
  giving the same error.
- The lock is re-entrant within one process.
- **Stale locks.** The operating system drops the lock when the holding process exits, including
  after a crash or `kill -9`. A leftover `_lock` file therefore never blocks anyone. Its holder
  line is just stale text, overwritten by the next writer. If the refusal names a pid on the same
  host that no longer exists, retry: the error says so.
- **Filesystems.** Advisory locks are unreliable on some network filesystems (older NFS, some SMB
  configurations). Keep the workspace on a local disk, or on a filesystem with working `flock`.

Read paths (`camber run`, `camber serve`, `ReadAPI`) never take the lock.

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
```

`camber.portfolio.transition(state, action, legal_hold=False)` is the pure state machine, with no
I/O. `allowed_actions(state)` lists what may follow.

## What the later steps add

2. **Identity migration.** Fault-lifecycle fingerprints and drift baselines move from the
   free-text `site` to `facility_id`, with a migration command. After that, renaming a facility
   can no longer orphan its history.
3. **Offboard, archive, restore and purge**, with export bundles (`archive/<fid>/`: parquet,
   registry entry, state and a sha256 manifest), the 30-day grace period, and typed-id
   confirmation for purges.
4. **Retention.** Month partitions, `camber retention show|set|override|hold|release|apply
   [--dry-run]` (roll up, verify row counts, then prune, under the lock).
5. **Edge and cloud.** Reconciliation of landed objects against the registry, quarantine of
   uploads for non-active facilities, edge decommission, and bucket lifecycle rules generated
   from the policy.
