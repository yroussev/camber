"""Quarantine for uploads from facilities that do not accept data (provisional, 0.95, #18).

An upload for a facility that is ``suspended``, ``offboarding``, ``archived`` or ``purged`` (or
unknown to the registry, or whose content does not match the hash in its name) never enters the
store. It goes to the workspace's ``quarantine/`` directory under its original key, beside a
``<key>.quarantine.json`` record of why, when, the facility state and the content sha256::

    <workspace>/quarantine/facility_id=<id>/year=<yyyy>/month=<m>/part-<sha16>.parquet
    <workspace>/quarantine/facility_id=<id>/year=<yyyy>/month=<m>/part-<sha16>.parquet.quarantine.json

(or the year-only key an older forwarder wrote). A year-only upload whose name and content the
store already migrated into month partitions (``camber store migrate-partitions``) is a
``duplicate``: it is quarantined, never stored twice, and cannot be released. So is an upload
whose rows a part of its store partition already holds in different bytes (0.107, #142): a month
re-sent after a forwarder, pandas or pyarrow upgrade (see :mod:`camber.edge.row_digest`), one
whose rows are all stored already, and a year-only one whose rows the migrated month partitions of
its year hold (0.108, #228, #230). An upload that overlaps a stored part without holding all of
its rows, and disagrees with it on a shared observation, is a ``conflict``: quarantined, never
merged (0.108, #228). One that agrees with it everywhere they overlap (a rolling window that
dropped its oldest rows) is merged with it into one part (0.108).

A bucket that a presigned-URL broker routes with :func:`camber.edge.landing.route_key` holds the
same thing under its ``_quarantine/`` prefix.

Every change is an audited admin action (the OS user and a mandatory reason in the portfolio's
``_audit.ndjson``), taken under the portfolio lock, and a dry run unless ``apply=True``:

* :func:`land` -- route a landing inbox: accepted objects into the store, the rest to quarantine.
* :func:`quarantine_reconciled` -- ``camber edge reconcile --apply``: quarantine what
  :func:`~camber.edge.landing.reconcile` flags.
* :func:`remove_row_duplicates` -- ``camber edge reconcile --apply --yes``: keep the oldest copy
  of each group of stored parts holding identical rows (0.108, #229).
* :func:`release` -- move quarantined objects into the store once the facility accepts data.
* :func:`discard` -- delete quarantined objects: also needs ``yes=True`` or the typed facility id,
  and a legal hold on the facility refuses it.

Crash safety: the record is written (atomically) before the object moves, and an object is moved
with ``os.replace`` (or copy + fsync + replace across filesystems) before its source is removed,
so an interrupted run leaves every object in exactly one readable place, or in two identical
copies that the next run de-duplicates. :func:`list_quarantine` shows any record whose object is
missing (``incomplete``); re-running the command, or discarding the record, finishes it.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import hashlib
import json
import os
import shutil

from .landing import (
    QUARANTINE_PREFIX,
    _Registry,
    _walk,
    already_migrated,
    parse_landed_key,
    reconcile,
    row_duplicates,
)
from .row_digest import (
    PartitionRows,
    PartitionView,
    _contains,
    _norm_table,
    _rows_frame,
    _span,
    _summary,
    file_rows_digest,
    merge_parts,
    read_rows,
    rows_digest,
)

__all__ = [
    "QUARANTINE_DIR",
    "RECORD_SUFFIX",
    "quarantine_root",
    "list_quarantine",
    "land",
    "quarantine_reconciled",
    "remove_row_duplicates",
    "release",
    "discard",
    "QuarantineCollision",
]

QUARANTINE_DIR = "quarantine"
RECORD_SUFFIX = ".quarantine.json"


# ---------------------------------------------------------------------- primitives


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _need_reason(reason) -> str:
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("a reason is required for every quarantine change (it is audited)")
    return reason.strip()


def _audit(portfolio, action: str, *, facility_id=None, state=None, reason=None, details=None):
    """One audit line through the public ``Portfolio.audit`` (the facility's state as given)."""
    return portfolio.audit(
        action, reason=reason, facility_id=facility_id, state=state, details=details
    )


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _fsync_dir(path: str) -> None:
    if not hasattr(os, "O_DIRECTORY"):  # pragma: no cover - Windows
        return
    with contextlib.suppress(OSError):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _write_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True, default=str)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    _fsync_dir(os.path.dirname(path))


def _path(root: str, key: str) -> str:
    return os.path.join(root, *key.split("/"))


def _move(src: str, dst: str) -> str:
    """Move ``src`` to ``dst`` without ever losing it: ``"moved"`` or ``"deduplicated"``.

    An identical ``dst`` (an earlier run that stopped before removing ``src``) just drops ``src``;
    a different one raises ``FileExistsError`` and changes nothing.
    """
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst):
        if _sha256(dst) != _sha256(src):
            raise FileExistsError(f"{dst} already exists with different content")
        os.remove(src)
        return "deduplicated"
    try:
        os.replace(src, dst)  # atomic on one filesystem
    except OSError:
        part = dst + ".part"  # across filesystems: copy, fsync, rename, then drop the source
        shutil.copyfile(src, part)
        with open(part, "rb") as fh:
            os.fsync(fh.fileno())
        os.replace(part, dst)
        os.remove(src)
    _fsync_dir(os.path.dirname(dst))
    return "moved"


def quarantine_root(portfolio) -> str:
    """``<workspace>/quarantine``: where quarantined objects and their records live."""
    return os.path.join(portfolio.root, QUARANTINE_DIR)


class QuarantineCollision(FileExistsError):
    """Quarantine already holds a different object (or record) at this key (0.108, #247)."""


def _collision(portfolio, key: str, sha: str) -> str | None:
    """Why quarantining content ``sha`` at ``key`` would clash with what quarantine holds there:
    a different object, or a record of different content. ``None`` when nothing clashes (empty,
    or the same content from an interrupted earlier run)."""
    dst = _path(quarantine_root(portfolio), key)
    if os.path.exists(dst) and _sha256(dst) != sha:
        return "quarantine already holds a different object at this key"
    rec_path = dst + RECORD_SUFFIX
    if os.path.exists(rec_path):
        try:
            with open(rec_path, encoding="utf-8") as fh:
                held = json.load(fh).get("sha256")
        except (OSError, ValueError, AttributeError):
            return "quarantine holds an unreadable record at this key"
        if held and held != sha:
            return "quarantine already holds a record of different content at this key"
    return None


_COLLISION_DETAIL = (
    "{why} (sha256 {held}...); this upload (sha256 {sha}...) stays in the inbox: release or "
    "discard the held one, then land again"
)


def _quarantine_one(portfolio, src: str, row: dict, *, reason: str, source: str) -> str:
    """Record, then move one object into quarantine (see the module docstring on crashes).

    0.108 (#247): an existing record or object at the key is never overwritten. Different content
    there raises :class:`QuarantineCollision` before anything is written."""
    key = row["key"]
    dst = _path(quarantine_root(portfolio), key)
    sha = _sha256(src)
    why = _collision(portfolio, key, sha)
    if why:
        raise QuarantineCollision(f"{key}: {why}")
    if os.path.exists(dst + RECORD_SUFFIX):  # the same content, recorded by an earlier run
        return _move(src, dst)
    record = {
        "key": key,
        "facility_id": row.get("facility_id"),
        "category": row.get("category"),
        "state": row.get("state"),
        "detail": row.get("detail"),
        "reason": reason,
        "source": source,
        "sha256": sha,
        "bytes": os.path.getsize(src),
        "landed_at": row.get("landed_at"),
        "quarantined_at": _utc_now(),
    }
    for extra in ("conflicts_with", "duplicate_of"):
        if row.get(extra):
            record[extra] = row[extra]
    _write_json(dst + RECORD_SUFFIX, record)
    return _move(src, dst)


def _group(rows):
    out: dict = {}
    for r in rows:
        out.setdefault(r.get("facility_id"), []).append(r)
    return out


# ---------------------------------------------------------------------- listing


def list_quarantine(portfolio, *, facility_id=None) -> list:
    """Every quarantined object, oldest record first (optionally for one facility).

    Each row is the object's record plus ``status``: ``held`` (object and record present),
    ``incomplete`` (a record whose object is missing -- an interrupted move, or a release that
    stopped before removing the record) or ``unrecorded`` (an object with no record).
    """
    root = quarantine_root(portfolio)
    rows: dict = {}
    if not os.path.isdir(root):
        return []
    for dirpath, dirs, files in os.walk(root):
        # _trash-* / _swap-* leftovers of an interrupted purge (Portfolio.recover removes them)
        dirs[:] = [d for d in dirs if not d.startswith(("_", "."))]
        for f in files:
            path = os.path.join(dirpath, f)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            if f.endswith(RECORD_SUFFIX):
                key = rel[: -len(RECORD_SUFFIX)]
                try:
                    with open(path, encoding="utf-8") as fh:
                        rec = json.load(fh)
                except (OSError, ValueError):
                    rec = {"key": key, "detail": "unreadable record"}
                rows.setdefault(key, {}).update(rec if isinstance(rec, dict) else {})
                rows[key]["_record"] = True
            elif f.endswith((".tmp", ".part")):
                continue
            else:
                rows.setdefault(rel, {})["_object"] = True
    out = []
    for key, r in rows.items():
        lk, _why = parse_landed_key(key)
        r.setdefault("key", key)
        r.setdefault("facility_id", lk.facility_id if lk else None)
        has_obj, has_rec = r.pop("_object", False), r.pop("_record", False)
        r["status"] = "held" if has_obj and has_rec else ("incomplete" if has_rec else "unrecorded")
        if facility_id is None or r.get("facility_id") == facility_id:
            out.append(r)
    return sorted(out, key=lambda r: (str(r.get("quarantined_at") or ""), r["key"]))


def _select(portfolio, facility_id, keys) -> list:
    if not facility_id and not keys:
        raise ValueError("name what to act on: a facility id, or one or more keys")
    rows = list_quarantine(portfolio, facility_id=facility_id)
    if keys:
        wanted = [
            k[len(QUARANTINE_PREFIX) :] if k.startswith(QUARANTINE_PREFIX) else k for k in keys
        ]
        have = {r["key"]: r for r in rows}
        missing = [k for k in wanted if k not in have]
        if missing:
            raise KeyError(f"not in quarantine: {', '.join(missing)}")
        rows = [have[k] for k in wanted]
    if not rows:
        raise KeyError(f"nothing in quarantine for {facility_id!r}")
    return rows


# ---------------------------------------------------------------------- landing gate

_DUPLICATE_ROWS_DETAIL = (
    "the store already holds these rows as {match}, in different bytes (a re-send after a "
    "forwarder, pandas or pyarrow upgrade); storing it again would double them"
)


def _rows_index(indexes: dict, dst: str) -> PartitionRows:
    d = os.path.dirname(dst)
    if d not in indexes:
        indexes[d] = PartitionRows(d)
    return indexes[d]


_HELD_ROWS_DETAIL = (
    "every row it holds is already stored, in {match} (a re-send of an earlier, smaller poll); "
    "storing it again would double them"
)
_MIGRATED_ROWS_DETAIL = (
    "a year-only part whose rows the store already holds in the month partitions of {year} "
    "(re-sent in new bytes by an older forwarder after the year was migrated); storing it again "
    "would double them"
)
_CONFLICT_DETAIL = (
    "its rows overlap {match} without holding all of them ({n} shared observation(s) carry other "
    "values); not merged -- compare the two and release or discard it by hand"
)
_MERGED_DETAIL = (
    "merged in the store into {by}, the union of its rows and those of a later poll that agrees "
    "with it on every shared observation (a rolling window); kept byte for byte because the "
    "facility is under a legal hold"
)
_REPLACED_DETAIL = (
    "replaced in the store by {by}, which holds every row of it but at most a revised last hour "
    "(the month grew since this part was sent); kept byte for byte because the facility is under "
    "a legal hold"
)


def _views(views: dict, indexes: dict, d: str) -> PartitionView:
    if d not in views:
        views[d] = PartitionView(d, _rows_index(indexes, os.path.join(d, "_")))
    return views[d]


def _month_dirs(ydir: str) -> list:
    if not os.path.isdir(ydir):
        return []
    return [
        os.path.join(ydir, m)
        for m in sorted(os.listdir(ydir))
        if m.startswith("month=") and os.path.isdir(os.path.join(ydir, m))
    ]


def _check_rows(
    row: dict, src: str, dst: str, lk, views: dict, indexes: dict, scratch: dict
) -> None:
    """Route a new store key by its rows: a ``duplicate`` when its partition (or, for a year-only
    part, the month partitions of its year) already holds its rows, a ``conflict`` when it
    overlaps a part and disagrees with it, and the store otherwise -- replacing the parts whose
    rows it holds in full (a month that grew since its last send; 0.108, #228), or merging with
    the parts it agrees with on every shared observation (a rolling window; 0.108).

    Parts planned earlier in the same run count as stored. An object with no digest (unreadable,
    or not the five long columns) lands as before 0.107."""
    summ = _summary(src)
    digest = summ["rows_sha256"]
    row["rows_sha256"] = digest
    if digest is None:
        row["to"] = "store"
        return
    d = os.path.dirname(dst)
    view = _views(views, indexes, d)
    match = view.exact(digest, exclude=lk.name)
    if match is not None:  # the same rows in other bytes (0.107, #142)
        row.update(
            category="duplicate",
            detail=_DUPLICATE_ROWS_DETAIL.format(match=match),
            duplicate_of=match,
            to="quarantine",
        )
        return
    frame = None
    months = _month_dirs(d) if lk.month is None else []
    if months or view.overlapping(summ["span"], exclude=lk.name):
        frame = read_rows(src)
    if frame is not None and months:
        # a year-only part re-sent after its year was migrated (0.108, #230): compare it with the
        # month partitions of the year (and the year's own legacy parts)
        held, unions = [], []
        for md in months:
            names, union = _views(views, indexes, md).held_by(frame, summ["span"])
            held += [f"{os.path.basename(md)}/{n}" for n in names]
            if union is not None:
                unions.append(union)
        names, union = view.held_by(frame, summ["span"], exclude=lk.name)
        held += names
        if union is not None:
            unions.append(union)
        if unions:
            import pandas as pd

            if _contains(pd.concat(unions, ignore_index=True), frame):
                row.update(
                    category="duplicate",
                    detail=_MIGRATED_ROWS_DETAIL.format(year=lk.year),
                    duplicate_of=", ".join(held),
                    to="quarantine",
                )
                return
    if frame is not None:
        j = view.judge(frame, summ["span"], exclude=lk.name)
        if j["verdict"] == "duplicate":
            match = ", ".join(j["parts"])
            row.update(
                category="duplicate",
                detail=_HELD_ROWS_DETAIL.format(match=match),
                duplicate_of=match,
                to="quarantine",
            )
            return
        if j["verdict"] == "conflict":
            match = ", ".join(j["parts"])
            row.update(
                category="conflict",
                detail=_CONFLICT_DETAIL.format(match=match, n=j["differing"]),
                conflicts_with=list(j["parts"]),
                to="quarantine",
            )
            return
        if j["verdict"] == "supersedes":
            row["replaces"] = list(j["parts"])
            row["revised"] = j["revised"]
            for n in j["parts"]:
                view.drop(n)
        if j["verdict"] == "merges" and _plan_merge(row, src, lk, view, j, scratch):
            return
    view.add(lk.name, src, summ, frame)
    row.update(to="store", rows=summ["rows"], span=summ["span"])


def _plan_merge(row: dict, src: str, lk, view: PartitionView, j: dict, scratch) -> bool:
    """Plan the union of ``src`` and the parts it overlaps and agrees with (0.108, the rolling
    window rule): the merged part is written to the run's ``scratch`` directory under its own
    content-addressed name, and the row stores it there in place of the parts. False when the
    parts cannot be merged (then the object is a ``conflict``)."""
    import io

    from ..store.parquet_store import _write_part

    parts = list(j["parts"])
    m = merge_parts(src, [view.members[n]["path"] for n in parts])
    if m is None:
        row.update(
            category="conflict",
            detail=_CONFLICT_DETAIL.format(match=", ".join(parts), n=0),
            conflicts_with=parts,
            to="quarantine",
        )
        return True
    buf = io.BytesIO()
    _write_part(m["table"], buf)
    data = buf.getvalue()
    sha = hashlib.sha256(data).hexdigest()
    name = f"part-{sha[:16]}.parquet"
    os.makedirs(scratch["dir"], exist_ok=True)
    path = os.path.join(scratch["dir"], f"{len(os.listdir(scratch['dir']))}-{name}")
    with open(path, "wb") as fh:
        fh.write(data)
    norm, _aware = _norm_table(m["table"])
    summ = {
        "rows_sha256": rows_digest(m["table"]),
        "rows": int(m["table"].num_rows),
        "span": _span(norm),
    }
    keydir = row["key"].rsplit("/", 1)[0]
    for n in parts:
        view.drop(n)
    view.add(name, path, summ, _rows_frame(m["table"]))
    row.update(
        replaces=parts,
        revised=j["revised"],
        merged={
            "stored_as": f"{keydir}/{name}",
            "kept_old_rows": m["kept_old_rows"],
            "new_rows": m["new_rows"],
            "revised": j["revised"],
        },
        merged_path=path,
        rows_sha256=summ["rows_sha256"],
        to="store",
        rows=summ["rows"],
        span=summ["span"],
    )
    return True


def _record_rows(indexes: dict, dst: str, row: dict) -> None:
    """Record a part just stored in its partition's row index (digested from the file if the plan
    has no digest for it)."""
    _rows_index(indexes, dst).record(
        os.path.basename(dst), row.get("rows_sha256"), row.get("rows"), row.get("span")
    )


def _link_tree(src: str, dst: str, skip) -> None:
    """Hard-link (or copy) everything under ``src`` into ``dst`` except the top-level ``skip``."""
    for dirpath, _dirs, files in os.walk(src):
        rel = os.path.relpath(dirpath, src)
        top = rel == "."
        os.makedirs(os.path.join(dst, rel), exist_ok=True)
        for f in files:
            if top and f in skip:
                continue
            a, b = os.path.join(dirpath, f), os.path.join(dst, rel, f)
            try:
                os.link(a, b)
            except OSError:  # pragma: no cover - no hard links on this filesystem
                shutil.copy2(a, b)


def _quarantine_copy(portfolio, src: str, row: dict, *, reason: str, source: str) -> None:
    """Record, then copy one store part into quarantine (the store keeps it until the swap)."""
    dst = _path(quarantine_root(portfolio), row["key"])
    why = _collision(portfolio, row["key"], _sha256(src))
    if why:  # 0.108 (#247): never overwrite what quarantine holds; the replace does not happen
        raise QuarantineCollision(f"{row['key']}: {why}")
    record = {
        "key": row["key"],
        "facility_id": row.get("facility_id"),
        "category": row.get("category"),
        "state": row.get("state"),
        "detail": row.get("detail"),
        "reason": reason,
        "source": source,
        "sha256": _sha256(src),
        "bytes": os.path.getsize(src),
        "landed_at": row.get("landed_at"),
        "quarantined_at": _utc_now(),
    }
    _write_json(dst + RECORD_SUFFIX, record)
    if os.path.exists(dst) and _sha256(dst) == record["sha256"]:
        return  # an earlier run copied it already
    tmp = dst + ".part"
    shutil.copyfile(src, tmp)
    with open(tmp, "rb") as fh:
        os.fsync(fh.fileno())
    os.replace(tmp, dst)
    _fsync_dir(os.path.dirname(dst))


def _replace_parts(
    portfolio,
    src: str,
    dst: str,
    row: dict,
    *,
    held: bool,
    reason: str,
    source: str,
    data_path: str | None = None,
) -> str:
    """Swap the partition of ``dst`` for one holding ``src`` instead of the parts it replaces.

    The new partition is staged beside the old one (hard links to every file kept, a copy of
    ``src``) and swapped in with :mod:`camber.store._swap`, so a reader or a crash sees the old
    partition or the new one, never both parts nor neither. Under a legal hold the replaced parts
    are copied into quarantine first (never released: their rows are in the store). ``src`` is
    removed only after the swap; an interrupted run finds the new part stored under its own key
    and de-duplicates the inbox copy. A merge (0.108) stores ``data_path`` (the union) at ``dst``
    instead of ``src``; an interrupted merge finds the inbox copy's rows stored and quarantines
    it as a ``duplicate``."""
    from ..store import _swap

    d, name = os.path.split(dst)
    keydir = row["key"].rsplit("/", 1)[0]
    gone = [n for n in row["replaces"] if os.path.isfile(os.path.join(d, n))]
    if held:
        for n in gone:
            _quarantine_copy(
                portfolio,
                os.path.join(d, n),
                {
                    "key": f"{keydir}/{n}",
                    "facility_id": row.get("facility_id"),
                    "category": "duplicate",
                    "state": row.get("state"),
                    "detail": (_MERGED_DETAIL if data_path else _REPLACED_DETAIL).format(by=name),
                },
                reason=reason,
                source=source,
            )
    stage = _swap.staging(d)
    _link_tree(d, stage, set(gone) | {name})
    tmp = os.path.join(stage, "_" + name + ".tmp")
    shutil.copyfile(data_path or src, tmp)
    os.replace(tmp, os.path.join(stage, name))
    _swap.commit(d)
    os.remove(src)
    _fsync_dir(os.path.dirname(src))
    row["replaced"] = gone
    return "merged" if data_path else "replaced"


def _store_changed(store_root: str, facilities) -> None:
    """Invalidate the point catalog and the read cache after parts entered or left the store."""
    from ..resolve import clear_store_cache
    from ..store.parquet_store import _invalidate_catalog_at

    _invalidate_catalog_at(store_root)
    for fid in sorted(set(facilities)):
        clear_store_cache(store_root, fid)


def land(portfolio, inbox, *, apply: bool = False, reason=None) -> dict:
    """Route every object in a landing ``inbox``: into the store, or into quarantine.

    An object goes to the store when its facility accepts data (``provisioning`` / ``active``, or
    a store partition with no registry entry) and to quarantine when the facility does not, is
    unknown, or the content's sha256 does not start with the ``part-<sha16>`` in its name. A
    Parquet object whose rows a part of its store partition already holds, in different bytes, is
    a ``duplicate`` and goes to quarantine too (0.107, #142: see :mod:`camber.edge.row_digest`),
    as does one whose rows are all stored already, or (a year-only object) held by the month
    partitions of its year (0.108, #230). An object that holds every row of the stored parts it
    overlaps replaces them (a growing month, 0.108, #228): its row has ``replaces``, and the
    partition is swapped in whole (:mod:`camber.store._swap`); under a legal hold the replaced
    parts are copied into quarantine first. One that lacks rows of a part it overlaps but agrees
    with it on every shared observation (a rolling window, 0.108) is merged: the union of their
    rows is stored as one canonical part (``merged`` in its row), swapped in the same way. One
    that disagrees with a part it overlaps is a ``conflict`` and goes to quarantine.
    Quarantine never overwrites what it holds (0.108, #247): a different upload at a held key
    stays in the inbox as a ``quarantine_collision``, and an object that fails is listed in the
    report's ``errors`` while the run goes on. Each part it stores is recorded in that
    partition's row index, and the store's point catalog is invalidated.
    Orphaned objects (no store key) and NDJSON objects (not a store format) stay in the inbox and
    are reported. A dry run by default; ``apply=True`` needs a ``reason``, takes the lock, and
    audits one begin and one done record per facility.
    """
    inbox = os.path.abspath(os.fspath(inbox))
    if not os.path.isdir(inbox):
        raise FileNotFoundError(f"landing inbox {inbox} does not exist")
    if apply:
        reason = _need_reason(reason)
    lock = portfolio.lock() if apply else contextlib.nullcontext()
    with lock:
        reg = _Registry(portfolio)
        plan: list = []
        if apply:
            from ..store import _swap

            _swap.recover_tree(portfolio.store_root, max_depth=3)  # finish an interrupted replace
        indexes: dict = {}  # partition dir -> PartitionRows (the store's row digests)
        views: dict = {}  # partition dir -> PartitionView (the store as this run's plan leaves it)
        import tempfile

        scratch = {"dir": tempfile.mkdtemp(prefix="camber-edge-land-")}  # planned merged parts
        try:
            return _land(portfolio, inbox, reg, plan, indexes, views, scratch, apply, reason)
        finally:
            shutil.rmtree(scratch["dir"], ignore_errors=True)


def _plan_one(portfolio, reg, inbox, key, row, views, indexes, scratch) -> None:
    lk, why = parse_landed_key(key)
    if lk is None:
        row.update(category="orphaned", detail=str(why), to="inbox")
        return
    st = reg.status(lk.facility_id)
    row.update(facility_id=lk.facility_id, state=st["state"], detail=st["detail"])
    src = _path(inbox, key)
    if lk.sha16 is not None and _sha256(src)[:16] != lk.sha16:
        row.update(
            category="hash_mismatch",
            detail="content sha256 does not match the part-<sha16> in its name",
            to="quarantine",
        )
    elif already_migrated(portfolio.store_root, lk, src):
        row.update(
            category="duplicate",
            detail="a year-only part the store already migrated to month partitions "
            "(re-sent by an older forwarder); storing it again would double its rows",
            to="quarantine",
        )
    elif st["status"] in ("ok", "unregistered"):
        row["category"] = st["status"]
        if lk.ext != "parquet":
            row.update(detail="ndjson is not a store format; convert it first", to="inbox")
        else:
            dst = _path(portfolio.store_root, key)
            if os.path.exists(dst):
                if _sha256(dst) != _sha256(src):
                    row.update(detail="the store already has different content", to="inbox")
                else:
                    row["to"] = "store"  # the same bytes at the same key: deduplicated
            else:
                _check_rows(row, src, dst, lk, views, indexes, scratch)
    else:
        row.update(category=st["status"], to="quarantine")
    if row["to"] == "quarantine":
        # 0.108 (#247): quarantine never overwrites a held object or its record; a second,
        # different upload at a held key stays in the inbox and is reported
        sha = _sha256(src)
        clash = _collision(portfolio, key, sha)
        if clash:
            held = _held_sha(portfolio, key)
            row.update(
                category="quarantine_collision",
                detail=_COLLISION_DETAIL.format(why=clash, held=held[:16], sha=sha[:16]),
                held_category=row["category"],
                to="inbox",
            )


def _held_sha(portfolio, key: str) -> str:
    dst = _path(quarantine_root(portfolio), key)
    try:
        with open(dst + RECORD_SUFFIX, encoding="utf-8") as fh:
            sha = json.load(fh).get("sha256")
        if sha:
            return str(sha)
    except (OSError, ValueError, AttributeError):
        pass
    return _sha256(dst) if os.path.exists(dst) else "?"


def _land(portfolio, inbox, reg, plan, indexes, views, scratch, apply, reason) -> dict:
    for key, mtime, size in _walk(inbox):
        row = {
            "key": key,
            "facility_id": None,
            "category": "ok",
            "state": None,
            "detail": "",
            "bytes": size,
            "landed_at": mtime.isoformat(),
            "to": None,
        }
        try:
            _plan_one(portfolio, reg, inbox, key, row, views, indexes, scratch)
        except Exception as exc:  # noqa: BLE001 - 0.108 (#247): one bad object never stops a run
            row.update(category="error", detail=f"{type(exc).__name__}: {exc}", to="inbox")
        plan.append(row)
    counts = {t: sum(1 for r in plan if r["to"] == t) for t in ("store", "quarantine", "inbox")}
    report = {
        "inbox": inbox,
        "counts": counts,
        "replaces": sum(len(r.get("replaces") or ()) for r in plan if r["to"] == "store"),
        "merges": sum(1 for r in plan if r["to"] == "store" and r.get("merged")),
        "errors": [
            {"key": r["key"], "error": r["detail"]} for r in plan if r["category"] == "error"
        ],
        "objects": plan,
        "dry_run": not apply,
        "applied": False,
    }
    if not apply:
        return report
    moving = [r for r in plan if r["to"] in ("store", "quarantine")]
    holds = portfolio.legal_holds()
    stored_fids: list = []
    for fid, rows in _group(moving).items():
        details = {
            "inbox": inbox,
            "to_store": [r["key"] for r in rows if r["to"] == "store"],
            "to_quarantine": [r["key"] for r in rows if r["to"] == "quarantine"],
        }
        replacing = {r["key"]: r["replaces"] for r in rows if r.get("replaces")}
        if replacing:
            details["replaces"] = replacing
        merging = {r["key"]: r["merged"] for r in rows if r.get("merged")}
        if merging:
            details["merges"] = merging
        _audit(
            portfolio,
            "edge.land",
            facility_id=fid,
            state=rows[0]["state"],
            reason=reason,
            details={**details, "phase": "begin"},
        )
        failed = []
        for r in rows:
            try:
                _apply_one(portfolio, inbox, r, indexes, holds, reason)
            except Exception as exc:  # noqa: BLE001 - 0.108 (#247): isolate per object
                r["result"] = "error"
                r["error"] = f"{type(exc).__name__}: {exc}"
                failed.append({"key": r["key"], "error": r["error"]})
                report["errors"].append({"key": r["key"], "error": r["error"]})
                continue
            if r["to"] == "store":
                stored_fids.append(fid)
        for idx in indexes.values():
            idx.save()
        done = {
            "phase": "done",
            "stored": sum(1 for r in rows if r["to"] == "store" and r.get("result") != "error"),
            "quarantined": sum(
                1 for r in rows if r["to"] == "quarantine" and r.get("result") != "error"
            ),
            "replaced": {r["key"]: r["replaced"] for r in rows if r.get("replaced") is not None},
            "replaced_kept_in_quarantine": fid in holds and bool(replacing),
        }
        merged = {r["key"]: r["merged"] for r in rows if r.get("result") == "merged"}
        if merged:
            done["merged"] = merged
        if failed:
            done["errors"] = failed
        _audit(
            portfolio,
            "edge.land",
            facility_id=fid,
            state=rows[0]["state"],
            reason=reason,
            details=done,
        )
    if stored_fids:
        _store_changed(portfolio.store_root, stored_fids)
    report["applied"] = True
    return report


def _apply_one(portfolio, inbox, r: dict, indexes: dict, holds, reason) -> None:
    src = _path(inbox, r["key"])
    fid = r.get("facility_id")
    if r["to"] != "store":
        r["result"] = _quarantine_one(portfolio, src, r, reason=reason, source="land")
        return
    if r.get("merged"):
        dst = _path(portfolio.store_root, r["merged"]["stored_as"])
    else:
        dst = _path(portfolio.store_root, r["key"])
    if r.get("replaces") and not os.path.exists(dst):
        r["result"] = _replace_parts(
            portfolio,
            src,
            dst,
            r,
            held=fid in holds,
            reason=reason,
            source="land",
            data_path=r.get("merged_path"),
        )
        idx = _rows_index(indexes, dst)
        for n in r["replaced"]:
            idx.forget(n)
    else:
        r["result"] = _move(src, dst)
    _record_rows(indexes, dst, r)


def quarantine_reconciled(portfolio, *, landing=None, reason) -> dict:
    """``camber edge reconcile --apply``: quarantine every object reconciliation flags.

    Re-runs :func:`~camber.edge.landing.reconcile` under the lock (so the decision is made on the
    state at the moment of the move) over the workspace store or a local ``landing`` directory,
    then moves each row whose action is ``quarantine``. Key listings cannot be applied -- CAMBER
    never moves cloud objects; use a broker with :func:`~camber.edge.landing.route_key`.
    """
    reason = _need_reason(reason)
    with portfolio.lock():
        rep = reconcile(portfolio, landing=landing)
        base = rep["source"]["path"]
        acting = [r for r in rep["objects"] if r["action"] == "quarantine"]
        for fid, rows in _group(acting).items():
            keys = [r["key"] for r in rows]
            common = {"source": rep["source"]["kind"], "keys": keys}
            _audit(
                portfolio,
                "edge.reconcile.quarantine",
                facility_id=fid,
                state=rows[0]["state"],
                reason=reason,
                details={**common, "phase": "begin"},
            )
            failed = []
            for r in rows:
                try:
                    r["result"] = _quarantine_one(
                        portfolio,
                        _path(base, r["key"]),
                        r,
                        reason=reason,
                        source=rep["source"]["kind"],
                    )
                except Exception as exc:  # noqa: BLE001 - 0.108 (#247): isolate per object
                    r["result"] = "error"
                    r["error"] = f"{type(exc).__name__}: {exc}"
                    failed.append({"key": r["key"], "error": r["error"]})
            done = {"phase": "done", "quarantined": len(keys) - len(failed)}
            if failed:
                done["errors"] = failed
            _audit(
                portfolio,
                "edge.reconcile.quarantine",
                facility_id=fid,
                state=rows[0]["state"],
                reason=reason,
                details=done,
            )
        rep["read_only"] = False
        rep["quarantined"] = sum(1 for r in acting if r.get("result") != "error")
        rep["errors"] = [
            {"key": r["key"], "error": r["error"]} for r in acting if r.get("result") == "error"
        ]
        return rep


def remove_row_duplicates(
    portfolio, *, reason=None, apply: bool = False, yes: bool = False
) -> dict:
    """``camber edge reconcile --apply --yes``: keep one copy of each group of stored parts that
    hold identical rows (0.108, #229; see :func:`~camber.edge.landing.row_duplicates`).

    A dry run by default: the report lists the groups, the copy each keeps (the oldest) and the
    extra copies. ``apply=True`` needs a ``reason`` and ``yes=True``; it takes the store lock (the
    workspace's), re-reads the groups under it and, per facility, audits a begin record (the keys
    and sha256 of the copies) before it deletes anything and a done record after. A facility under
    a legal hold keeps its bytes: its extra copies move to quarantine (as ``duplicate``, never
    released). Facilities that do not accept data, and retired ids, are left alone. Each copy is
    one file, so a reader sees it or not, and the copy kept holds the same rows.
    """
    if apply:
        reason = _need_reason(reason)
        if not yes:
            raise ValueError(
                "removing duplicate parts deletes store files: pass yes=True (--yes) to confirm"
            )
    lock = portfolio.lock() if apply else contextlib.nullcontext()
    with lock:
        groups = row_duplicates(portfolio, save=apply)
        acting = [g for g in groups if g["action"]]
        report = {
            "groups": groups,
            "copies": sum(len(g["extra"]) for g in acting),
            "bytes": sum(g["bytes_extra"] for g in acting),
            "left_alone": [g for g in groups if not g["action"]],
            "dry_run": not apply,
            "applied": False,
        }
        if not apply:
            return report
        root = portfolio.store_root
        by_fid: dict = {}
        for g in acting:
            by_fid.setdefault(g["facility_id"], []).append(g)
        indexes: dict = {}
        for fid, grp in by_fid.items():
            copies = [(g, k) for g in grp for k in g["extra"]]
            _audit(
                portfolio,
                "edge.reconcile.dedupe",
                facility_id=fid,
                state=grp[0]["state"],
                reason=reason,
                details={
                    "phase": "begin",
                    "action": grp[0]["action"],
                    "keep": [g["keep"] for g in grp],
                    "keys": [k for _g, k in copies],
                    "sha256": [_sha256(_path(root, k)) for _g, k in copies],
                    "rows_sha256": [g["rows_sha256"] for g, _k in copies],
                },
            )
            for g, k in copies:
                path = _path(root, k)
                if g["action"] == "quarantine":
                    row = {
                        "key": k,
                        "facility_id": fid,
                        "category": "duplicate",
                        "state": g["state"],
                        "detail": f"the same rows as {g['keep']} (a row duplicate in the store); "
                        "kept byte for byte because the facility is under a legal hold",
                    }
                    _quarantine_one(portfolio, path, row, reason=reason, source="store")
                else:
                    with contextlib.suppress(FileNotFoundError):
                        os.remove(path)
                    _fsync_dir(os.path.dirname(path))
                _rows_index(indexes, path).forget(os.path.basename(path))
            for idx in indexes.values():
                idx.save()
            _audit(
                portfolio,
                "edge.reconcile.dedupe",
                facility_id=fid,
                state=grp[0]["state"],
                reason=reason,
                details={"phase": "done", "removed": len(copies), "action": grp[0]["action"]},
            )
        if by_fid:
            _store_changed(root, by_fid)
        report["applied"] = True
        return report


# ---------------------------------------------------------------------- release / discard


def release(portfolio, *, facility_id=None, keys=None, reason=None, apply: bool = False) -> dict:
    """Move quarantined objects into the store, once their facility accepts data again.

    Select by ``facility_id`` and/or ``keys``. An object is refused (and stays put) while its
    facility does not accept data (resume or restore it first), when its content failed the hash
    check, when the store holds different content at its key, or when a part of its partition
    already holds the same rows (0.107, #142). A dry run by default;
    ``apply=True`` needs a ``reason``, takes the lock and is audited per facility.
    """
    if apply:
        reason = _need_reason(reason)
    lock = portfolio.lock() if apply else contextlib.nullcontext()
    with lock:
        rows = _select(portfolio, facility_id, keys)
        reg = _Registry(portfolio)
        qroot = quarantine_root(portfolio)
        planned: list = []
        refused: list = []
        indexes: dict = {}
        landing: dict = {}
        for r in rows:
            fid = r.get("facility_id")
            st = reg.status(fid) if fid else {"status": "orphaned", "detail": "no facility"}
            why = None
            held_sha = None
            if r["status"] != "incomplete":
                held_sha = _sha256(_path(qroot, r["key"]))
            lk0 = parse_landed_key(r["key"])[0]
            if r["status"] == "incomplete":
                why = "the object is missing (an interrupted move); discard the record"
            elif r.get("category") == "hash_mismatch":
                why = "content failed the hash check; discard it"
            elif lk0 is not None and lk0.sha16 is not None and (held_sha or "")[:16] != lk0.sha16:
                # 0.108 (#247): checked again on release, record or not
                why = "the held object's sha256 does not match the part-<sha16> in its name"
            elif r.get("sha256") and held_sha != r["sha256"]:
                why = "the held object's sha256 does not match its quarantine record"
            elif r.get("category") == "duplicate" or (
                (lk := parse_landed_key(r["key"])[0]) is not None
                and already_migrated(portfolio.store_root, lk, _path(qroot, r["key"]))
            ):
                why = (
                    "the store already holds these rows (migrated to month partitions); discard it"
                )
            elif st["status"] not in ("ok", "unregistered"):
                why = f"{st['detail']}; resume or restore the facility first"
            else:
                dst = _path(portfolio.store_root, r["key"])
                src = _path(qroot, r["key"])
                if os.path.exists(dst):
                    if _sha256(dst) != _sha256(src):
                        why = "the store already has different content at this key"
                elif r["key"].endswith(".parquet"):
                    digest, nrows = file_rows_digest(src)
                    d = os.path.dirname(dst)
                    match = _rows_index(indexes, dst).find(
                        digest, exclude=os.path.basename(dst)
                    ) or landing.get((d, digest))
                    if match is not None:
                        why = f"the store already holds these rows as {match}; discard it"
                    elif digest is not None:
                        landing[(d, digest)] = os.path.basename(dst)
                        r = {**r, "rows_sha256": digest, "rows": nrows}
            (refused if why else planned).append({**r, "refused": why} if why else r)
        report = {
            "planned": [r["key"] for r in planned],
            "refused": [{"key": r["key"], "why": r["refused"]} for r in refused],
            "dry_run": not apply,
            "applied": False,
        }
        if not apply:
            return report
        for fid, grp in _group(planned).items():
            ks = [r["key"] for r in grp]
            _audit(
                portfolio,
                "edge.quarantine.release",
                facility_id=fid,
                reason=reason,
                details={"keys": ks, "phase": "begin"},
            )
            for r in grp:
                src = _path(qroot, r["key"])
                dst = _path(portfolio.store_root, r["key"])
                _move(src, dst)
                if dst.endswith(".parquet"):
                    _record_rows(indexes, dst, r)
                with contextlib.suppress(FileNotFoundError):
                    os.remove(src + RECORD_SUFFIX)
            for idx in indexes.values():
                idx.save()
            _audit(
                portfolio,
                "edge.quarantine.release",
                facility_id=fid,
                reason=reason,
                details={"released": len(ks), "phase": "done"},
            )
        report["applied"] = True
        return report


def discard(
    portfolio,
    *,
    facility_id=None,
    keys=None,
    reason=None,
    apply: bool = False,
    yes: bool = False,
    confirm=None,
) -> dict:
    """Delete quarantined objects (and their records). Destructive, so doubly guarded.

    A dry run by default. ``apply=True`` needs a ``reason`` and either ``yes=True`` or
    ``confirm`` equal to ``facility_id`` (the typed id); it takes the lock and is audited per
    facility *before* anything is deleted. Objects of a facility under a legal hold are refused.
    An interrupted discard leaves some records without objects (``incomplete``); running it again
    finishes the job.
    """
    rows = _select(portfolio, facility_id, keys)
    if apply:
        reason = _need_reason(reason)
        if not yes and (facility_id is None or confirm != facility_id):
            raise ValueError(
                "discard deletes data: pass yes=True (--yes) or confirm=<facility_id> "
                "(--confirm, the typed facility id)"
            )
    lock = portfolio.lock() if apply else contextlib.nullcontext()
    with lock:
        if apply:
            rows = _select(portfolio, facility_id, keys)  # re-read under the lock
        holds = portfolio.legal_holds()
        planned = [r for r in rows if r.get("facility_id") not in holds]
        refused = [
            {"key": r["key"], "why": "the facility is under a legal hold"}
            for r in rows
            if r.get("facility_id") in holds
        ]
        report = {
            "planned": [r["key"] for r in planned],
            "bytes": sum(int(r.get("bytes") or 0) for r in planned),
            "refused": refused,
            "dry_run": not apply,
            "applied": False,
        }
        if not apply:
            return report
        qroot = quarantine_root(portfolio)
        for fid, grp in _group(planned).items():
            ks = [r["key"] for r in grp]
            _audit(
                portfolio,
                "edge.quarantine.discard",
                facility_id=fid,
                reason=reason,
                details={"keys": ks, "sha256": [r.get("sha256") for r in grp], "phase": "begin"},
            )
            for r in grp:
                path = _path(qroot, r["key"])
                for p in (path, path + RECORD_SUFFIX):  # object first: a crash leaves a record
                    with contextlib.suppress(FileNotFoundError):
                        os.remove(p)
                _fsync_dir(os.path.dirname(path))
            _audit(
                portfolio,
                "edge.quarantine.discard",
                facility_id=fid,
                reason=reason,
                details={"discarded": len(ks), "phase": "done"},
            )
        report["applied"] = True
        return report
