"""``camber store verify`` / ``camber store normalize`` (0.104, #136): the canonical part layout.

Since 0.103 (#130) every part the store writes is in one canonical layout (see
:func:`camber.store.parquet_store._canonicalize`): the ``camber.layout = "1"`` marker, no pandas
metadata, one row group (up to 2**20 rows), the store columns first and rows sorted by
``(ts, equip, role, equip_class, value)``. Older parts read back the same. :func:`verify` reports
which parts are canonical (read-only, no lock); :func:`normalize` rewrites the others.

**What normalize rewrites.** A non-canonical, readable part in a ``year=/month=`` partition of a
facility that accepts data. Each part is rewritten **in place under its own name**, with the same
rows (a retention rollup keeps its ``camber.rollup.covers`` key). Names stay because other records
point at them: a rollup's ``covers`` lists the raw part names it was built from (renaming a raw
part would make the next rollup of that month count its rows twice), and a part's ``<seq>`` feeds
``_next_seq``. Left alone, each with a reason in the report:

- parts that are already canonical;
- **edge parts** (``part-<sha16>.parquet``) that are not canonical (forwarders before 0.107; since
  0.107 they write canonical parts): the name is the sha256 of the bytes. ``camber edge land``
  checks it (``hash_mismatch``), and a forwarder re-sending the same batch lands on the same key,
  so the store holds it once. Rewriting the bytes would break both; see docs/EDGE-DEPLOY.md;
- **year-only parts** (pre-0.95, directly under ``year=Y/``): ``camber store migrate-partitions``
  rewrites them canonically and records their sha256 for the edge landing's duplicate check;
- unreadable parts;
- facilities that do not accept data (any state but ``provisioning`` / ``active``, as for the edge
  landing), retired ids, and, in a workspace, facilities under a legal hold: their bytes stay as
  they are, and an offboarding facility's export bundle keeps matching its data.

**Crash safety.** Per partition, every new part is first written to a stage directory inside the
partition (``_trash-normalize-<token>``, invisible to readers), fsynced and read back: it must hold
exactly the original rows in canonical order, with the same types and the kept metadata. Only then
is each staged file moved over its original with ``os.replace`` (atomic per file), and the stage
removed. The old and the new file of a part hold the same rows, so at every point a reader sees
each part exactly once, old or new: rows are never lost or duplicated, and unlike a directory swap
there is no moment at which the partition is missing. A crash leaves at most a ``_trash-*`` stage,
which the next run (or any lifecycle / retention recovery) removes, and a partition whose remaining
parts the next run rewrites. Every step is idempotent.

**Workspace stores.** Normalizing a workspace's store also covers its rollup stores
(``rollups/hourly``, ``rollups/daily``), under the workspace lock; ``apply`` needs a reason there
and is audited as ``store.normalize``.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import uuid

from . import _swap
from .facilities import FacilityRegistry, _workspace_of_store, require_facility_id
from .parquet_store import (
    _COLUMNS,
    _FACILITY,
    _LAYOUT,
    _LAYOUT_KEY,
    _MONTH,
    _ROWS_PER_GROUP,
    _SORT_KEYS,
    _YEAR,
    _canonicalize,
    _invalidate_catalog_at,
    _read_part_file,
    _write_part,
)

__all__ = ["check_part", "part_origin", "verify", "normalize"]

_COVERS_KEY = b"camber.rollup.covers"  # camber.portfolio._retention._COVERS_KEY
_PANDAS_KEY = b"pandas"
_EDGE_NAME = re.compile(r"^part-[0-9a-f]{16}\.parquet$")
_STAGE_PREFIX = _swap.TRASH_PREFIX + "normalize-"
_ACCEPTING = ("provisioning", "active")  # camber.edge.landing.ACCEPTING_STATES (a test pins it)
_ROLLUP_FREQS = ("hourly", "daily")  # camber.portfolio._retention.ROLLUP_FREQS
_ROLLUPS_DIR = "rollups"  # camber.portfolio._bundle.ROLLUPS_DIR

EDGE_REASON = (
    "edge part: its name is the sha256 of its bytes, which the edge landing checks and which "
    "lands a re-sent batch on the same key; rewriting it would break both (see EDGE-DEPLOY.md)"
)
YEAR_ONLY_REASON = (
    "year-only (pre-0.95) part: `camber store migrate-partitions` rewrites it canonically and "
    "records it for the edge landing's duplicate check"
)
UNREADABLE_REASON = "unreadable part (see `camber store verify`)"

# Indirections a test replaces to simulate a crash at each step.
_replace = os.replace
_remove_stage = shutil.rmtree


# --------------------------------------------------------------------------- one part


def part_origin(name: str, meta: dict, *, rollup_store: bool = False) -> str:
    """``"rollup"`` (a retention rollup part), ``"edge"`` (an edge forwarder's content-addressed
    part) or ``"store"`` (written by the store: ``part-<seq>-<i>``, ``part-legacy-*``, others)."""
    if rollup_store or _COVERS_KEY in (meta or {}):
        return "rollup"
    if _EDGE_NAME.match(name):
        return "edge"
    return "store"


def _arange(n: int, typ):
    import numpy as np
    import pyarrow as pa

    return pa.array(np.arange(n, dtype="int64")).cast(typ)


def check_part(path: str) -> dict:
    """One part's layout: ``{"rows", "bytes", "marker", "canonical", "problems", "meta",
    "error"}``. Reads the whole part; a part that fails to read has ``error`` set."""
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    out: dict = {
        "rows": None,
        "bytes": None,
        "marker": False,
        "canonical": False,
        "problems": [],
        "meta": {},
        "error": None,
    }
    try:
        out["bytes"] = os.path.getsize(path)
        with pq.ParquetFile(path) as f:
            groups = f.metadata.num_row_groups
            table = f.read()
    except Exception as e:  # noqa: BLE001 - any failure to read is what verify reports
        out["error"] = f"{type(e).__name__}: {e}"
        return out
    meta = dict(table.schema.metadata or {})
    rows = table.num_rows
    out.update(meta=meta, rows=rows)
    problems = out["problems"]
    out["marker"] = meta.get(_LAYOUT_KEY) == _LAYOUT
    if _LAYOUT_KEY not in meta:
        problems.append("no camber.layout marker")
    elif not out["marker"]:
        problems.append(f"camber.layout is {meta[_LAYOUT_KEY]!r}, not {_LAYOUT!r}")
    if _PANDAS_KEY in meta:
        problems.append("pandas metadata")
    extra = sorted(k for k in meta if k not in (_LAYOUT_KEY, _PANDAS_KEY, _COVERS_KEY))
    if extra:
        problems.append(
            "other schema metadata: " + ", ".join(k.decode("utf-8", "replace") for k in extra)
        )
    want_groups = max(1, -(-rows // _ROWS_PER_GROUP))
    if groups > want_groups:
        problems.append(f"{groups} row groups (canonical: {want_groups})")
    names = table.column_names
    order = [c for c in _COLUMNS if c in names] + sorted(c for c in names if c not in _COLUMNS)
    if names != order:
        problems.append("columns not in canonical order")
    keys = [(c, "ascending") for c in _SORT_KEYS if c in names]
    if keys and rows > 1:
        idx = pc.sort_indices(table, sort_keys=keys)  # stable: sorted input gives 0..n-1
        if not idx.equals(_arange(rows, idx.type)):
            problems.append("rows not sorted by (ts, equip, role, equip_class, value)")
    out["canonical"] = not problems
    return out


# --------------------------------------------------------------------------- the store's parts


def _targets(root: str):
    """``(workspace or None, main store root, [(label, store root)])`` for ``root``.

    A workspace's store brings its rollup stores along; a workspace rollup store given directly
    is normalized alone, but under the workspace's lock and lifecycle rules."""
    from ..portfolio._workspace import Portfolio, is_workspace  # lazy: portfolio imports store

    root = os.path.abspath(os.fspath(root))
    ws = _workspace_of_store(root)
    if ws is not None:
        stores = [("store", root)]
        for freq in _ROLLUP_FREQS:
            r = os.path.join(ws, _ROLLUPS_DIR, freq)
            if os.path.isdir(r):
                stores.append((f"{_ROLLUPS_DIR}/{freq}", r))
        return ws, root, stores
    parent = os.path.dirname(root)
    if (
        os.path.basename(root) in _ROLLUP_FREQS
        and os.path.basename(parent) == _ROLLUPS_DIR
        and is_workspace(os.path.dirname(parent))
    ):
        ws = os.path.dirname(parent)
        return ws, Portfolio(ws).store_root, [(f"{_ROLLUPS_DIR}/{os.path.basename(root)}", root)]
    return None, root, [("store", root)]


def _parts(sroot: str, facility_id=None):
    """Every part file of a store: ``(facility_id, year, month or None, directory, name)``."""
    if not os.path.isdir(sroot):
        return
    fdirs = sorted(d for d in os.listdir(sroot) if d.startswith(f"{_FACILITY}="))
    for fd in fdirs:
        fid = fd.split("=", 1)[1]
        if facility_id is not None and fid != facility_id:
            continue
        fpath = os.path.join(sroot, fd)
        if not os.path.isdir(fpath):
            continue
        for yd in sorted(os.listdir(fpath)):
            ypath = os.path.join(fpath, yd)
            if not yd.startswith(f"{_YEAR}=") or not os.path.isdir(ypath):
                continue
            try:
                year = int(yd.split("=", 1)[1])
            except ValueError:
                continue
            names = sorted(os.listdir(ypath))
            for n in names:
                if _is_part(ypath, n):
                    yield fid, year, None, ypath, n
            for md in names:
                mpath = os.path.join(ypath, md)
                if not md.startswith(f"{_MONTH}=") or not os.path.isdir(mpath):
                    continue
                try:
                    month = int(md.split("=", 1)[1])
                except ValueError:
                    continue
                for n in sorted(os.listdir(mpath)):
                    if _is_part(mpath, n):
                        yield fid, year, month, mpath, n


def _is_part(d: str, n: str) -> bool:
    return (
        n.endswith(".parquet")
        and not n.startswith(("_", "."))
        and os.path.isfile(os.path.join(d, n))
    )


def _facility_gate(main: str, ws):
    """``why(facility_id)``: the reason normalize leaves a facility alone, or ``None``."""
    reg = FacilityRegistry(main)
    entries = reg.all()
    tomb = reg.tombstones()
    holds: dict = {}
    if ws is not None:
        from ..portfolio._workspace import Portfolio

        holds = Portfolio(ws).legal_holds()

    def why(fid: str):
        if fid not in entries and fid in tomb:
            return "a retired (tombstoned) facility id; its leftover data is the lifecycle's"
        if fid in holds:
            return "under a legal hold: its part files are kept byte for byte"
        state = str((entries.get(fid) or {}).get("state") or "active")
        if state in _ACCEPTING:
            return None
        if state == "archived":
            return "archived: its data lives in its export bundle and it refuses store writes"
        if state == "offboarding":
            return "offboarding: its export bundle must keep matching its data"
        if state == "suspended":
            return "suspended: only facilities that accept data are rewritten (resume it first)"
        return f"in state {state!r}, which does not accept data"

    return why


def _scan(stores, facility_id, why):
    """Inspect every part: ``(rows for the report, {partition: [names]}, skipped)``."""
    rows, todo, skipped = [], {}, {}
    for label, sroot in stores:
        rollup_store = label != "store"
        for fid, year, month, d, n in _parts(sroot, facility_id):
            path = os.path.join(d, n)
            info = check_part(path)
            origin = part_origin(n, info["meta"], rollup_store=rollup_store)
            row = {
                "store": label,
                "path": os.path.relpath(path, sroot).replace(os.sep, "/"),
                "facility_id": fid,
                "year": year,
                "month": month,
                "origin": origin,
                "canonical": info["canonical"],
                "marker": info["marker"],
                "rows": info["rows"],
                "bytes": info["bytes"],
                "problems": info["problems"],
                "error": info["error"],
            }
            rows.append(row)
            if info["canonical"]:
                continue
            reason = why(fid) if why is not None else None
            if reason is None:
                if info["error"] is not None:
                    reason = UNREADABLE_REASON
                elif month is None:
                    reason = YEAR_ONLY_REASON
                elif origin == "edge":
                    reason = EDGE_REASON
            if reason is not None:
                k = (label, fid, reason)
                skipped[k] = skipped.get(k, 0) + 1
                continue
            todo.setdefault((label, sroot, fid, year, month, d), []).append(row)
    skipped_rows = [
        {"store": s, "facility_id": f, "parts": n, "reason": r}
        for (s, f, r), n in sorted(skipped.items())
    ]
    return rows, todo, skipped_rows


def _counts(rows) -> dict:
    c = {
        "parts": len(rows),
        "canonical": 0,
        "noncanonical": 0,
        "unreadable": 0,
        "legacy": 0,
        "edge": 0,
        "rollup": 0,
        "bytes": 0,
    }
    for r in rows:
        c["bytes"] += r["bytes"] or 0
        if r["error"] is not None:
            c["unreadable"] += 1
        elif r["canonical"]:
            c["canonical"] += 1
        else:
            c["noncanonical"] += 1
        if r["origin"] in ("edge", "rollup"):
            c[r["origin"]] += 1
        elif r["error"] is None and not r["marker"]:
            c["legacy"] += 1
    return c


def _require_known_facility(main: str, stores, facility_id: str) -> None:
    """``ValueError`` unless ``facility_id`` is registered in the store at ``main`` or has a
    partition in one of ``stores`` (0.107, #215: a typo is an error, not an empty success)."""
    reg = FacilityRegistry(main)
    if facility_id in reg.all():
        return
    for _label, sroot in stores:
        if os.path.isdir(os.path.join(sroot, f"{_FACILITY}={facility_id}")):
            return
    tomb = reg.tombstones().get(facility_id)
    if tomb is not None:
        raise ValueError(f"facility {facility_id!r} was removed (tombstoned) and has no data left")
    raise ValueError(f"unknown facility {facility_id!r} in store {main}")


_EMPTY_OK = ("provisioning", "archived")  # states in which a facility may hold no parts


def _health(main: str, facility_id) -> dict:
    """``{"leftovers", "empty_facilities"}`` of the store at ``main`` (0.107, #213).

    ``leftovers``: what interrupted ingests and swaps left behind
    (:func:`camber.store._leftovers.staging_leftovers`), each with ``stranded`` true when it
    holds a facility's data outside its partition. ``empty_facilities``: registered facilities
    with no part file, in a state that should have some (not ``provisioning`` / ``archived``).
    """
    from ._leftovers import staging_leftovers

    leftovers = []
    for item in staging_leftovers(main):
        if facility_id is not None and item["facility_id"] not in (None, facility_id):
            continue
        stranded = item["kind"] in ("swap", "aside") and not os.path.isdir(
            os.path.join(main, f"{_FACILITY}={item['facility_id']}")
        )
        leftovers.append(
            {**item, "path": os.path.relpath(item["path"], main), "stranded": stranded}
        )
    with_parts = {fid for fid, *_ in _parts(main, facility_id)}
    empty = []
    for fid, e in sorted(FacilityRegistry(main).all().items()):
        if facility_id is not None and fid != facility_id:
            continue
        state = str(e.get("state") or "active")
        if fid not in with_parts and state not in _EMPTY_OK:
            empty.append({"facility_id": fid, "state": state})
    return {"leftovers": leftovers, "empty_facilities": empty}


def verify(root, *, facility_id=None) -> dict:
    """Report which parts of the store at ``root`` are in the canonical layout (read-only; no
    lock). A workspace's store includes its rollup stores.

    Returns ``{"store", "workspace", "stores", "facility_id", "counts", "parts", "leftovers",
    "empty_facilities", "ok"}``. ``counts`` has ``parts`` = ``canonical`` + ``noncanonical`` +
    ``unreadable``, and by origin ``edge`` (edge forwarder parts), ``rollup`` (retention rollup
    parts) and ``legacy`` (other parts with no ``camber.layout`` marker: written before 0.103).
    ``ok`` is whether every part is readable and no facility's data is stranded outside its
    partition by an interrupted swap.

    0.107: ``root`` must be a store (``FileNotFoundError`` otherwise, #215; an empty directory is
    an empty store since 0.108, #233: 0 parts, ``ok``) and ``facility_id`` one it knows
    (``ValueError``); ``leftovers`` lists what interrupted ingests left (#213; the
    next ingest recovers them) and ``empty_facilities`` the registered facilities with no part
    file in a state that should have some.
    """
    from .facilities import _require_store_dir

    _require_store_dir(root)
    if facility_id is not None:
        require_facility_id(facility_id)
    ws, main, stores = _targets(root)
    if facility_id is not None:
        _require_known_facility(main, stores, facility_id)
    rows, _todo, _skipped = _scan(stores, facility_id, None)
    counts = _counts(rows)
    health = _health(main, facility_id)
    stranded = any(x["stranded"] for x in health["leftovers"])
    return {
        "store": os.path.abspath(os.fspath(root)),
        "workspace": ws,
        "stores": [label for label, _ in stores],
        "facility_id": facility_id,
        "counts": counts,
        "parts": rows,
        **health,
        "ok": counts["unreadable"] == 0 and not stranded,
    }


# --------------------------------------------------------------------------- rewriting


def _fsync_file(path: str) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _dir_rows(d: str) -> int:
    """Rows of the readable parts in partition ``d`` (from their footers; the stage and other
    ``_``/``.`` entries are not parts, and an unreadable part is left alone, so not counted)."""
    import pyarrow.parquet as pq

    n = 0
    for name in os.listdir(d):
        if _is_part(d, name):
            try:
                n += int(pq.ParquetFile(os.path.join(d, name)).metadata.num_rows)
            except Exception:  # noqa: BLE001 - unreadable: not ours to count or rewrite
                continue
    return n


def _same(back, canon) -> bool:
    """The part read back holds exactly the canonical table: rows, order, types and metadata."""
    want = canon.schema.metadata or {}
    got = back.schema.metadata or {}
    if got.get(_LAYOUT_KEY) != _LAYOUT or _PANDAS_KEY in got:
        return False
    if got.get(_COVERS_KEY) != want.get(_COVERS_KEY):
        return False
    return back.replace_schema_metadata(None).equals(canon.replace_schema_metadata(None))


def _normalize_partition(d: str, names) -> tuple:
    """Rewrite ``names`` in partition directory ``d`` canonically (see the module); returns
    ``(bytes before, bytes after)`` of those parts."""
    before_rows = _dir_rows(d)
    stage = os.path.join(d, f"{_STAGE_PREFIX}{uuid.uuid4().hex[:12]}")
    os.makedirs(stage)
    b0 = b1 = 0
    try:
        for n in names:
            src, dst = os.path.join(d, n), os.path.join(stage, n)
            orig = _read_part_file(src)
            canon = _canonicalize(orig, keep_meta=(_COVERS_KEY,))
            _write_part(canon, dst)
            _fsync_file(dst)
            back = _read_part_file(dst)
            if back.num_rows != orig.num_rows or not _same(back, canon):
                raise OSError(f"normalize: {src} did not read back identical; nothing was changed")
            b0 += os.path.getsize(src)
            b1 += os.path.getsize(dst)
        _swap._fsync_dir(stage)
    except Exception:  # a failed check (not a crash): drop the stage, the partition is untouched
        with contextlib.suppress(OSError):
            _remove_stage(stage)
        raise
    for n in names:  # each move is atomic, and old and new hold the same rows
        _replace(os.path.join(stage, n), os.path.join(d, n))
    _swap._fsync_dir(d)
    _remove_stage(stage)
    after_rows = _dir_rows(d)
    if after_rows != before_rows:  # pragma: no cover - every part was checked before the move
        raise OSError(f"normalize: {d} holds {after_rows} rows after, {before_rows} before")
    return b0, b1


def normalize(
    root,
    *,
    facility_id=None,
    apply: bool = False,
    reason=None,
    lock_timeout: float | None = None,
) -> dict:
    """Rewrite the store's non-canonical parts in the canonical layout (see the module).

    A dry run (the default) reports what would be rewritten and what is left alone, and why.
    ``apply=True`` holds the store lock (the workspace's, inside a workspace, where it also needs
    a ``reason`` and is audited as ``store.normalize``), first recovers interrupted work, rewrites
    partition by partition, and invalidates the catalog. Idempotent. ``facility_id`` limits it to
    one facility, and is refused (``ValueError``) when that facility is one normalize leaves alone.

    Returns ``{"store", "workspace", "stores", "facility_id", "dry_run", "applied",
    "partitions": [{store, facility_id, year, month, parts, rows, bytes_before, bytes_after}],
    "parts", "rows", "bytes_before", "bytes_after", "skipped": [{store, facility_id, parts,
    reason}], "counts"}`` (``bytes_after`` is ``None`` in a dry run).
    """
    from .facilities import _is_empty_store, _require_store_dir

    _require_store_dir(root)  # 0.107 (#215): before the lock, which would create the directory
    # 0.108 (#233): an empty folder is an empty store -- nothing to rewrite, and no lock file
    locking = apply and not _is_empty_store(root)
    if facility_id is not None:
        require_facility_id(facility_id)
    ws, main, stores = _targets(root)
    if facility_id is not None:
        _require_known_facility(main, stores, facility_id)
    if apply and ws is not None and not (isinstance(reason, str) and reason.strip()):
        raise ValueError("a reason is required inside a portfolio workspace (it is audited)")
    if locking:
        from ._lock import store_lock  # lazy: it imports the portfolio, which imports the store

        lock = store_lock(main, timeout=lock_timeout)
    else:
        lock = contextlib.nullcontext()
    with lock:
        why = _facility_gate(main, ws)
        if facility_id is not None:
            refused = why(facility_id)
            if refused is not None:
                raise ValueError(f"facility {facility_id!r} is not normalized: {refused}")
        if locking:
            for _label, sroot in stores:
                _swap.recover_tree(sroot, max_depth=3)
        rows, todo, skipped = _scan(stores, facility_id, why)
        parts = []
        for (label, _sroot, fid, year, month, _d), items in sorted(todo.items()):
            parts.append(
                {
                    "store": label,
                    "facility_id": fid,
                    "year": year,
                    "month": month,
                    "parts": len(items),
                    "rows": sum(r["rows"] for r in items),
                    "bytes_before": sum(r["bytes"] for r in items),
                    "bytes_after": None,
                }
            )
        report = {
            "store": os.path.abspath(os.fspath(root)),
            "workspace": ws,
            "stores": [label for label, _ in stores],
            "facility_id": facility_id,
            "dry_run": not apply,
            "applied": False,
            "partitions": parts,
            "parts": sum(p["parts"] for p in parts),
            "rows": sum(p["rows"] for p in parts),
            "bytes_before": sum(p["bytes_before"] for p in parts),
            "bytes_after": None,
            "skipped": skipped,
            "counts": _counts(rows),
        }
        if not apply or not todo:
            return report
        from ..resolve import clear_store_cache  # lazy: resolve imports the store

        touched: set = set()
        for p, ((_label, sroot, fid, _y, _m, d), items) in zip(parts, sorted(todo.items())):
            _b0, b1 = _normalize_partition(d, [os.path.basename(r["path"]) for r in items])
            p["bytes_after"] = b1
            touched.add((sroot, fid))
        for sroot in sorted({s for s, _ in touched}):
            _invalidate_catalog_at(sroot)
        for sroot, fid in sorted(touched):
            clear_store_cache(sroot, fid)
        report["bytes_after"] = sum(p["bytes_after"] for p in parts)
        report["applied"] = True
        if ws is not None:
            FacilityRegistry(main)._audit(
                "store.normalize",
                reason=reason,
                details={
                    "store": os.path.relpath(report["store"], ws),
                    "stores": report["stores"],
                    "facility_id": facility_id,
                    "partitions": len(parts),
                    "parts": report["parts"],
                    "rows": report["rows"],
                    "bytes_before": report["bytes_before"],
                    "bytes_after": report["bytes_after"],
                    "skipped": sum(s["parts"] for s in skipped),
                },
            )
        return report
