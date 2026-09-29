"""Quarantine for uploads from facilities that do not accept data (provisional, 0.95, #18).

An upload for a facility that is ``suspended``, ``offboarding``, ``archived`` or ``purged`` (or
unknown to the registry, or whose content does not match the hash in its name) never enters the
store. It goes to the workspace's ``quarantine/`` directory under its original key, beside a
``<key>.quarantine.json`` record of why, when, the facility state and the content sha256::

    <workspace>/quarantine/facility_id=<id>/year=<yyyy>/part-<sha16>.parquet
    <workspace>/quarantine/facility_id=<id>/year=<yyyy>/part-<sha16>.parquet.quarantine.json

A bucket that a presigned-URL broker routes with :func:`camber.edge.landing.route_key` holds the
same thing under its ``_quarantine/`` prefix.

Every change is an audited admin action (the OS user and a mandatory reason in the portfolio's
``_audit.ndjson``), taken under the portfolio lock, and a dry run unless ``apply=True``:

* :func:`land` -- route a landing inbox: accepted objects into the store, the rest to quarantine.
* :func:`quarantine_reconciled` -- ``camber edge reconcile --apply``: quarantine what
  :func:`~camber.edge.landing.reconcile` flags.
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

from .landing import QUARANTINE_PREFIX, _Registry, _walk, parse_landed_key, reconcile

__all__ = [
    "QUARANTINE_DIR",
    "RECORD_SUFFIX",
    "quarantine_root",
    "list_quarantine",
    "land",
    "quarantine_reconciled",
    "release",
    "discard",
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
    from ..portfolio._audit import append_audit, audit_record

    return append_audit(
        portfolio.root,
        audit_record(
            action,
            facility_id=facility_id,
            from_state=state,
            to_state=state,
            reason=reason,
            details=details,
        ),
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


def _quarantine_one(portfolio, src: str, row: dict, *, reason: str, source: str) -> str:
    """Record, then move one object into quarantine (see the module docstring on crashes)."""
    key = row["key"]
    dst = _path(quarantine_root(portfolio), key)
    record = {
        "key": key,
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
    for dirpath, _dirs, files in os.walk(root):
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


def land(portfolio, inbox, *, apply: bool = False, reason=None) -> dict:
    """Route every object in a landing ``inbox``: into the store, or into quarantine.

    An object goes to the store when its facility accepts data (``provisioning`` / ``active``, or
    a store partition with no registry entry) and to quarantine when the facility does not, is
    unknown, or the content's sha256 does not start with the ``part-<sha16>`` in its name.
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
        plan = []
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
            lk, why = parse_landed_key(key)
            if lk is None:
                row.update(category="orphaned", detail=str(why), to="inbox")
                plan.append(row)
                continue
            st = reg.status(lk.facility_id)
            row.update(facility_id=lk.facility_id, state=st["state"], detail=st["detail"])
            src = _path(inbox, key)
            if lk.sha16 is not None and _sha256(src)[:16] != lk.sha16:
                row.update(
                    category="hash_mismatch",
                    detail="content sha256 does not match the part-<sha16> in its name",
                    to="quarantine",
                )
            elif st["status"] in ("ok", "unregistered"):
                row["category"] = st["status"]
                if lk.ext != "parquet":
                    row.update(detail="ndjson is not a store format; convert it first", to="inbox")
                else:
                    dst = _path(portfolio.store_root, key)
                    if os.path.exists(dst) and _sha256(dst) != _sha256(src):
                        row.update(detail="the store already has different content", to="inbox")
                    else:
                        row["to"] = "store"
            else:
                row.update(category=st["status"], to="quarantine")
            plan.append(row)
        counts = {t: sum(1 for r in plan if r["to"] == t) for t in ("store", "quarantine", "inbox")}
        report = {
            "inbox": inbox,
            "counts": counts,
            "objects": plan,
            "dry_run": not apply,
            "applied": False,
        }
        if not apply:
            return report
        moving = [r for r in plan if r["to"] in ("store", "quarantine")]
        for fid, rows in _group(moving).items():
            details = {
                "inbox": inbox,
                "to_store": [r["key"] for r in rows if r["to"] == "store"],
                "to_quarantine": [r["key"] for r in rows if r["to"] == "quarantine"],
            }
            _audit(
                portfolio,
                "edge.land",
                facility_id=fid,
                state=rows[0]["state"],
                reason=reason,
                details={**details, "phase": "begin"},
            )
            for r in rows:
                src = _path(inbox, r["key"])
                if r["to"] == "store":
                    r["result"] = _move(src, _path(portfolio.store_root, r["key"]))
                else:
                    r["result"] = _quarantine_one(portfolio, src, r, reason=reason, source="land")
            _audit(
                portfolio,
                "edge.land",
                facility_id=fid,
                state=rows[0]["state"],
                reason=reason,
                details={
                    "phase": "done",
                    "stored": len(details["to_store"]),
                    "quarantined": len(details["to_quarantine"]),
                },
            )
        report["applied"] = True
        return report


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
            for r in rows:
                r["result"] = _quarantine_one(
                    portfolio, _path(base, r["key"]), r, reason=reason, source=rep["source"]["kind"]
                )
            _audit(
                portfolio,
                "edge.reconcile.quarantine",
                facility_id=fid,
                state=rows[0]["state"],
                reason=reason,
                details={"phase": "done", "quarantined": len(keys)},
            )
        rep["read_only"] = False
        rep["quarantined"] = len(acting)
        return rep


# ---------------------------------------------------------------------- release / discard


def release(portfolio, *, facility_id=None, keys=None, reason=None, apply: bool = False) -> dict:
    """Move quarantined objects into the store, once their facility accepts data again.

    Select by ``facility_id`` and/or ``keys``. An object is refused (and stays put) while its
    facility does not accept data (resume or restore it first), when its content failed the hash
    check, or when the store holds different content at its key. A dry run by default;
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
        for r in rows:
            fid = r.get("facility_id")
            st = reg.status(fid) if fid else {"status": "orphaned", "detail": "no facility"}
            why = None
            if r["status"] == "incomplete":
                why = "the object is missing (an interrupted move); discard the record"
            elif r.get("category") == "hash_mismatch":
                why = "content failed the hash check; discard it"
            elif st["status"] not in ("ok", "unregistered"):
                why = f"{st['detail']}; resume or restore the facility first"
            else:
                dst = _path(portfolio.store_root, r["key"])
                src = _path(qroot, r["key"])
                if os.path.exists(dst) and _sha256(dst) != _sha256(src):
                    why = "the store already has different content at this key"
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
                _move(src, _path(portfolio.store_root, r["key"]))
                with contextlib.suppress(FileNotFoundError):
                    os.remove(src + RECORD_SUFFIX)
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
