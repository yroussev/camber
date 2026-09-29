"""The cloud-landing side of the edge: reconcile landed objects against the portfolio registry.

**Provisional (0.95, #18 step 5).** Edge devices land Parquet parts at
``facility_id=<id>/year=<yyyy>/month=<m>/part-<sha16>.parquet`` (forwarders before 0.95 wrote
the year-only ``facility_id=<id>/year=<yyyy>/part-<sha16>.parquet``, still accepted; see
docs/EDGE-DEPLOY.md). This
module is the central counterpart: it classifies every landed object against the portfolio's
facility registry, so nothing lands silently for a facility that has left, was never registered,
or cannot be addressed at all.

Each object falls in one category:

``ok``
    a valid store key for a facility that accepts data (``provisioning`` or ``active``).
``orphaned``
    no facility can claim it: the key is not a store object key (bad layout, invalid facility id,
    an unknown extension).
``unknown_facility``
    a valid key for a facility id the registry does not know, or one that was retired (tombstoned).
``unregistered``
    a facility with data in the store but no registry entry (a pre-portfolio store). It reads as
    ``active``; register it with ``camber facility add``. Reported, never moved.
``inactive``
    a registered facility that does not accept data -- ``suspended``, ``offboarding``,
    ``archived`` or ``purged`` (or any state this CAMBER does not know, failing closed).
``quarantined``
    (key listings only) an object under the bucket's ``_quarantine/`` prefix.
``duplicate``
    a year-only object whose name and content ``camber store migrate-partitions`` already moved
    into month partitions (see ``ParquetStore.migrated_files``): an older forwarder re-sent it.
    Storing it again would count its rows twice, so it is quarantined (then discarded).

Lifecycle state is read **only** through the :mod:`camber.portfolio` API (``Portfolio.facilities``
and the registry's tombstones), so the states a later release adds are handled by name.

Reconciliation is **read-only** by default. Its sources: the workspace store (objects that landed
straight into it), a local landing directory (an inbox or a mounted/synced bucket), or a key
listing exported from the cloud (``aws s3api list-objects-v2``, ``gcloud storage objects list``,
``az storage blob list`` JSON, or one key per line) -- CAMBER never calls a cloud API here.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
from dataclasses import dataclass

__all__ = [
    "ACCEPTING_STATES",
    "NON_ACCEPTING_STATES",
    "QUARANTINE_PREFIX",
    "CATEGORIES",
    "LandedKey",
    "parse_landed_key",
    "already_migrated",
    "facility_status",
    "route_key",
    "read_key_listing",
    "reconcile",
]

# States whose uploads land in the store; every other state (known or not) is quarantined.
ACCEPTING_STATES = ("provisioning", "active")
# The non-accepting states by name. offboarding / archived / purged arrive with the lifecycle
# cascade (#18 steps 3-4); they are matched by name so no code change is needed when they do.
NON_ACCEPTING_STATES = ("suspended", "offboarding", "archived", "purged")
# The bucket-side quarantine prefix a presigned-URL broker routes non-accepted uploads to. A
# leading underscore keeps Hive / pyarrow dataset discovery from ever reading it as data.
QUARANTINE_PREFIX = "_quarantine/"
CATEGORIES = (
    "ok",
    "orphaned",
    "unknown_facility",
    "unregistered",
    "inactive",
    "quarantined",
    "duplicate",
)

_EXTS = ("parquet", "ndjson")
_PART_RE = re.compile(r"^part-([0-9a-f]{16})\.(parquet|ndjson)$")


@dataclass(frozen=True)
class LandedKey:
    """A parsed landed-object key (``facility_id=/year=/[month=/]<name>``)."""

    key: str
    facility_id: str
    year: int
    month: int | None
    name: str
    ext: str
    sha16: str | None


def parse_landed_key(key: str):
    """``(LandedKey, None)`` for a valid store key, else ``(None, "why it is not one")``."""
    from ..store.facilities import valid_facility_id

    parts = [p for p in str(key).replace("\\", "/").split("/") if p]
    if len(parts) not in (3, 4):
        return None, "not a facility_id=/year=/[month=/]part key"
    if not parts[0].startswith("facility_id="):
        return None, "first segment is not facility_id=<id>"
    fid = parts[0][len("facility_id=") :]
    if not valid_facility_id(fid):
        return None, f"invalid facility id {fid!r}"
    m = re.fullmatch(r"year=(\d{4})", parts[1])
    if not m:
        return None, "second segment is not year=<yyyy>"
    year = int(m.group(1))
    month = None
    if len(parts) == 4:
        mm = re.fullmatch(r"month=(\d{1,2})", parts[2])
        if not mm or not 1 <= int(mm.group(1)) <= 12:
            return None, "third segment is not month=<1-12>"
        month = int(mm.group(1))
    name = parts[-1]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in _EXTS:
        return None, f"not a landed data object ({name!r}; expected .parquet or .ndjson)"
    pm = _PART_RE.match(name)
    return (
        LandedKey(
            key="/".join(parts),
            facility_id=fid,
            year=year,
            month=month,
            name=name,
            ext=ext,
            sha16=pm.group(1) if pm else None,
        ),
        None,
    )


def already_migrated(store_root: str, lk: LandedKey, path: str) -> bool:
    """True when ``path`` (the object at year-only key ``lk``) is a legacy part the store already
    migrated into month partitions: same name, same sha256 (see ``ParquetStore.migrated_files``).
    Month keys are never migrated, so they are never duplicates of a migration."""
    if lk.month is not None:
        return False
    from ..store import ParquetStore

    sha = ParquetStore(store_root).migrated_files(lk.facility_id, lk.year).get(lk.name)
    if not sha or not os.path.isfile(path):
        return False
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest() == sha


# ---------------------------------------------------------------------- the registry adapter


def _parse_ts(value):
    """An aware UTC datetime from an ISO-ish string / datetime / epoch number (``None`` if not)."""
    if value is None or value == "":
        return None
    if isinstance(value, _dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=_dt.timezone.utc)
    if isinstance(value, (int, float)):
        return _dt.datetime.fromtimestamp(float(value), tz=_dt.timezone.utc)
    s = str(value).strip().replace("Z", "+00:00")
    try:
        d = _dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=_dt.timezone.utc)


class _Registry:
    """A one-shot snapshot of the facilities and tombstones, read through the portfolio API.

    ``portfolio`` is a :class:`~camber.portfolio.Portfolio` or any object with ``facilities()``
    (``{fid: {"state", "state_changed_at", "registered", ...}}``) and ``registry.tombstones()``
    -- tests pass a stub carrying states this CAMBER's lifecycle does not implement yet.
    """

    def __init__(self, portfolio):
        self.entries = portfolio.facilities()
        self.tombstones = portfolio.registry.tombstones()

    def status(self, fid: str) -> dict:
        e = self.entries.get(fid)
        if e is None:
            if fid in self.tombstones:
                return {
                    "status": "unknown_facility",
                    "state": None,
                    "changed_at": None,
                    "detail": "a retired facility id (tombstoned); ids are never reused",
                }
            return {
                "status": "unknown_facility",
                "state": None,
                "changed_at": None,
                "detail": "not in the facility registry",
            }
        state = str(e.get("state") or "active")
        changed = e.get("state_changed_at")
        if not e.get("registered", True):
            return {
                "status": "unregistered",
                "state": state,
                "changed_at": changed,
                "detail": "store data with no registry entry (register it: camber facility add)",
            }
        if state in ACCEPTING_STATES:
            return {"status": "ok", "state": state, "changed_at": changed, "detail": ""}
        known = state in NON_ACCEPTING_STATES
        return {
            "status": "inactive",
            "state": state,
            "changed_at": changed,
            "detail": f"facility is {state}"
            + ("" if known else " (a state this CAMBER does not know; treated as not accepting)"),
        }


def facility_status(portfolio, facility_id: str) -> dict:
    """``{"status", "state", "changed_at", "detail"}`` for one facility id (provisional).

    ``status`` is ``ok`` (accepts uploads), ``unregistered`` (store data, no entry -- accepted,
    reported), ``inactive`` or ``unknown_facility`` (both quarantined on landing).
    """
    return _Registry(portfolio).status(facility_id)


def route_key(portfolio, key: str) -> str:
    """Where an upload for ``key`` should land: ``key`` itself, or ``_quarantine/<key>``.

    For a presigned-URL broker: sign the returned key, so a facility that does not accept data
    (or an unknown one) can never write into the store's layout. An unparseable key raises
    ``ValueError`` -- a broker should refuse it outright.
    """
    lk, why = parse_landed_key(key)
    if lk is None:
        raise ValueError(f"not a landed-object key: {why}")
    st = facility_status(portfolio, lk.facility_id)["status"]
    return lk.key if st in ("ok", "unregistered") else QUARANTINE_PREFIX + lk.key


# ---------------------------------------------------------------------- sources


def _walk(root: str):
    """``(key, mtime, size)`` for every data-looking file under ``root``.

    Path components starting with ``_`` or ``.`` (registry files, markers, temp files) are
    skipped, as Hive / pyarrow discovery skips them.
    """
    out: list = []
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(("_", ".")))
        for f in sorted(filenames):
            if f.startswith(("_", ".")):
                continue
            path = os.path.join(dirpath, f)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            try:
                st = os.stat(path)
            except OSError:  # pragma: no cover - vanished mid-walk
                continue
            mtime = _dt.datetime.fromtimestamp(st.st_mtime, tz=_dt.timezone.utc)
            out.append((rel, mtime, st.st_size))
    return out


_LS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}\S*)\s+(\d+)\s+(.+)$")


def _strip(key: str, prefix: str) -> str:
    key = re.sub(r"^[a-z][a-z0-9+.-]*://[^/]+/", "", key.strip())  # s3://bucket/, gs://bucket/
    p = prefix.strip("/")
    if p and (key == p or key.startswith(p + "/")):
        key = key[len(p) + 1 :]
    return key


def read_key_listing(path: str, *, prefix: str = "") -> list:
    """Parse a cloud listing into ``[(key, last_modified or None, size or None)]``.

    Accepts the JSON of ``aws s3api list-objects-v2`` (``Contents[].Key/LastModified/Size``),
    ``gcloud storage objects list --format=json`` (``name`` / ``updated`` / ``size``),
    ``az storage blob list`` (``name`` / ``properties.lastModified`` / ``contentLength``), a JSON
    list of keys, ``aws s3 ls --recursive`` text, or one key per line. ``s3://bucket/``-style
    prefixes and ``prefix`` (the sink's key prefix) are stripped. Timestamps without an offset are
    read as UTC.
    """
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    out: list = []
    try:
        doc = json.loads(raw)
    except ValueError:
        doc = None
    if doc is not None:
        items = doc.get("Contents", doc.get("items", [])) if isinstance(doc, dict) else doc
        if not isinstance(items, list):
            raise ValueError(f"{path}: unrecognised JSON listing (expected a list of objects)")
        for it in items:
            if isinstance(it, str):
                out.append((_strip(it, prefix), None, None))
                continue
            if not isinstance(it, dict):
                continue
            props: dict = it["properties"] if isinstance(it.get("properties"), dict) else {}
            key = it.get("Key") or it.get("key") or it.get("name")
            if not key:
                continue
            ts = (
                it.get("LastModified")
                or it.get("last_modified")
                or it.get("updated")
                or it.get("update_time")
                or it.get("timeCreated")
                or it.get("creation_time")
                or props.get("lastModified")
            )
            size = it.get("Size", it.get("size", props.get("contentLength")))
            out.append((_strip(str(key), prefix), _parse_ts(ts), _int_or_none(size)))
        return out
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _LS_RE.match(line)
        if m:
            out.append((_strip(m.group(3), prefix), _parse_ts(m.group(1)), int(m.group(2))))
        else:
            out.append((_strip(line, prefix), None, None))
    return out


def _int_or_none(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------- classification


def _classify(reg: _Registry, key: str, mtime, *, source: str, base=None, store_root=None) -> dict:
    """One object's row: key, facility, category, state, detail and the suggested action."""
    row: dict = {
        "key": key,
        "facility_id": None,
        "category": "ok",
        "state": None,
        "detail": "",
        "landed_at": mtime.isoformat() if mtime else None,
        "action": None,
    }
    if key.startswith(QUARANTINE_PREFIX):
        lk, _why = parse_landed_key(key[len(QUARANTINE_PREFIX) :])
        row.update(
            category="quarantined",
            facility_id=lk.facility_id if lk else None,
            detail="under the bucket quarantine prefix (release or discard it)",
        )
        return row
    lk, why = parse_landed_key(key)
    if lk is None:
        row.update(category="orphaned", detail=str(why))
        return row
    st = reg.status(lk.facility_id)
    row.update(facility_id=lk.facility_id, state=st["state"], detail=st["detail"])
    if base is not None and store_root is not None:
        if already_migrated(store_root, lk, os.path.join(base, *key.split("/"))):
            row.update(
                category="duplicate",
                detail="a year-only part the store already migrated to month partitions "
                "(re-sent by an older forwarder); storing it again would double its rows",
                action="quarantine",
            )
            return row
    if st["status"] == "ok":
        return row
    row["category"] = st["status"]
    if st["status"] == "unregistered":
        return row
    if source == "store" and st["status"] == "inactive":
        # Data that landed while the facility was active is legitimate history (an offboarding
        # facility's export needs it); only objects that arrived *after* the state change are
        # late uploads to quarantine.
        changed = _parse_ts(st["changed_at"])
        if mtime is None or changed is None:
            row["detail"] += "; landing time unknown, reported only"
            return row
        if mtime <= changed:
            row["detail"] += "; landed before the state change (history), reported only"
            return row
        row["detail"] += "; landed after the state change"
    if source == "store" and st["status"] == "unknown_facility":
        row["detail"] += "; store data of a retired id is the lifecycle cascade's to remove"
        return row
    row["action"] = "quarantine"
    return row


def reconcile(portfolio, *, landing=None, keys=None, prefix: str = "") -> dict:
    """Classify landed objects against the registry; read-only (provisional, 0.95).

    Sources (at most one of ``landing`` / ``keys``): the workspace store (default), a local
    landing directory, or a key-listing file (see :func:`read_key_listing`). Returns a JSON-ready
    report: ``source``, ``counts`` by category, ``objects`` (every row that is not ``ok``, with the
    suggested ``action``), and per-facility counts. Nothing is moved -- see
    :func:`camber.edge.quarantine.quarantine_findings` for the audited ``--apply``.
    """
    if landing is not None and keys is not None:
        raise ValueError("give at most one of landing= and keys=")
    reg = _Registry(portfolio)
    if keys is not None:
        source = {"kind": "keys", "path": os.fspath(keys)}
        items = read_key_listing(os.fspath(keys), prefix=prefix)
        kind = "keys"
    elif landing is not None:
        source = {"kind": "landing", "path": os.path.abspath(os.fspath(landing))}
        if not os.path.isdir(source["path"]):
            raise FileNotFoundError(f"landing directory {source['path']} does not exist")
        items = _walk(source["path"])
        kind = "landing"
    else:
        source = {"kind": "store", "path": portfolio.store_root}
        items = _walk(portfolio.store_root)
        kind = "store"
    counts = {c: 0 for c in CATEGORIES}
    per_fac: dict = {}
    rows = []
    n_bytes = 0
    for key, mtime, size in items:
        if kind == "keys" and key.startswith(("_", ".")) and not key.startswith(QUARANTINE_PREFIX):
            continue  # registry / marker files at the bucket root
        row = _classify(
            reg,
            key,
            mtime,
            source=kind,
            base=None if kind == "keys" else source["path"],
            store_root=portfolio.store_root,
        )
        row["bytes"] = size
        n_bytes += size or 0
        counts[row["category"]] += 1
        if row["facility_id"]:
            fc = per_fac.setdefault(row["facility_id"], {"state": row["state"]})
            fc[row["category"]] = fc.get(row["category"], 0) + 1
        if row["category"] != "ok":
            rows.append(row)
    return {
        "source": source,
        "objects_scanned": sum(counts.values()),
        "bytes_scanned": n_bytes,
        "counts": counts,
        "to_quarantine": sum(1 for r in rows if r["action"] == "quarantine"),
        "objects": rows,
        "facilities": dict(sorted(per_fac.items())),
        "read_only": True,
    }
