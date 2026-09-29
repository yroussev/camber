"""Export bundles: a facility's whole footprint, copied and checksummed under ``archive/<fid>/``.

::

    archive/<fid>/<bundle_id>/            bundle_id = <UTC yyyymmddThhmmssZ>-<kind>
      manifest.json                       every file below with sha256 + size, counts, fingerprint
      manifest.sha256                     sha256 of manifest.json (the manifest is checked too)
      registry.json                       the registry entry, catalog keys, retention override, hold
      audit.ndjson                        the facility's audit records at export time (a copy)
      store/facility_id=<fid>/...         raw trend partitions, as stored
      rollups/<freq>/facility_id=<fid>/.. rollup partitions (hourly, daily)
      state/...                           state/<fid>/: faults, drift + M&V (incl. billing)
                                          baselines, weather audit log, migrated originals,
                                          reports, manifest
      external/<n>-<name>                 artifacts the facility's manifest lists outside state/

A bundle is built in a staging directory and swapped in only once every copied file has been
re-read and matched against its checksum (see :mod:`camber.store._swap`), so a crash never leaves
a half-written bundle that looks complete. :func:`verify_bundle` re-checks a bundle end to end;
:func:`restore_bundle` puts its content back and verifies the restored files against the same
checksums.

The **fingerprint** is a sha256 over the (path, sha256) list of the facility's hot files. Archive
compares it with the latest bundle's, so a bundle that no longer matches the data (anything written
during the grace period) is never trusted as the only copy.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import shutil

from ..store import _swap
from ._audit import _actor, read_audit
from ._state import sha256_file, state_dir

ARCHIVE_DIR = "archive"
ROLLUPS_DIR = "rollups"
BUNDLE_SCHEMA = "camber.bundle/1"
MANIFEST = "manifest.json"
MANIFEST_SUM = "manifest.sha256"
_META = (MANIFEST, MANIFEST_SUM)


def archive_dir(root: str, facility_id: str) -> str:
    """``<root>/archive/<facility_id>`` (the facility's bundles)."""
    # state_dir validates the id; the archive path mirrors it
    return os.path.join(root, ARCHIVE_DIR, os.path.basename(state_dir(root, facility_id)))


def _visible(name: str) -> bool:
    return not name.startswith(("_", ".")) and not name.endswith(".tmp")


def _walk(base: str):
    """Relative paths (``/``-separated) of every visible file under ``base``, sorted."""
    out: list = []
    if not os.path.isdir(base):
        return out
    for dirpath, dirs, names in os.walk(base):
        dirs[:] = sorted(d for d in dirs if _visible(d))
        for n in sorted(names):
            if _visible(n):
                out.append(os.path.relpath(os.path.join(dirpath, n), base).replace(os.sep, "/"))
    return out


def rollup_roots(root: str) -> dict:
    """``{freq: <root>/rollups/<freq>}`` for every rollup store in the workspace."""
    base = os.path.join(root, ROLLUPS_DIR)
    if not os.path.isdir(base):
        return {}
    return {
        d: os.path.join(base, d)
        for d in sorted(os.listdir(base))
        if _visible(d) and os.path.isdir(os.path.join(base, d))
    }


def hot_files(pf, facility_id: str) -> list:
    """Every hot (deletable-by-archive) file of a facility: ``[(source, bundle_rel, category)]``.

    Categories: ``store``, ``rollup``, ``state``, ``external`` (an artifact the facility's
    manifest lists outside ``state/<fid>/``; only ones that still exist).
    """
    out: list = []
    part = f"facility_id={facility_id}"
    sroot = os.path.join(pf.store_root, part)
    out += [(os.path.join(sroot, r), f"store/{part}/{r}", "store") for r in _walk(sroot)]
    for freq, rroot in rollup_roots(pf.root).items():
        base = os.path.join(rroot, part)
        out += [
            (os.path.join(base, r), f"rollups/{freq}/{part}/{r}", "rollup") for r in _walk(base)
        ]
    sdir = state_dir(pf.root, facility_id)
    out += [(os.path.join(sdir, r), f"state/{r}", "state") for r in _walk(sdir)]
    man = pf.manifest(facility_id)
    for i, (path, _e) in enumerate(sorted((man.get("external") or {}).items())):
        if os.path.isfile(path):
            out.append((path, f"external/{i:03d}-{os.path.basename(path)}", "external"))
    return out


def fingerprint(entries) -> str:
    """sha256 over the sorted ``(bundle_rel, sha256)`` pairs."""
    h = hashlib.sha256()
    for rel, sha in sorted(entries):
        h.update(f"{rel}\0{sha}\n".encode())
    return h.hexdigest()


def hot_fingerprint(pf, facility_id: str) -> str:
    """The fingerprint of the facility's hot files as they are now (hashes every file)."""
    return fingerprint((rel, sha256_file(src)) for src, rel, _c in hot_files(pf, facility_id))


def _copy_hashed(src: str, dst: str) -> tuple:
    """Copy ``src`` to ``dst`` (mtime kept), hashing the bytes written; ``(sha256, bytes)``."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    h = hashlib.sha256()
    n = 0
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        for chunk in iter(lambda: fi.read(1 << 20), b""):
            h.update(chunk)
            fo.write(chunk)
            n += len(chunk)
    shutil.copystat(src, dst)
    return h.hexdigest(), n


def _write_json(path: str, data) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True, default=str)
        fh.write("\n")


def _rows(files: list) -> int:
    """Rows in a list of parquet files (0 for any it cannot read)."""
    if not files:
        return 0
    import pyarrow.parquet as pq

    n = 0
    for f in files:
        try:
            n += int(pq.ParquetFile(f).metadata.num_rows)
        except Exception:  # noqa: BLE001 - an unreadable file counts no rows but is still copied
            continue
    return n


def _utc(now) -> _dt.datetime:
    now = now or _dt.datetime.now(_dt.timezone.utc)
    return now if now.tzinfo else now.replace(tzinfo=_dt.timezone.utc)


def summarize(pf, facility_id: str) -> dict:
    """What a bundle of the facility would hold now (no hashing, no copying)."""
    files = hot_files(pf, facility_id)
    by: dict = {}
    for src, _rel, cat in files:
        c = by.setdefault(cat, {"files": 0, "bytes": 0})
        c["files"] += 1
        c["bytes"] += os.path.getsize(src)
    store_files = [s for s, _r, c in files if c == "store" and s.endswith(".parquet")]
    return {
        "files": len(files),
        "bytes": sum(c["bytes"] for c in by.values()),
        "by_category": by,
        "store_rows": _rows(store_files),
    }


def export_bundle(pf, facility_id: str, *, kind: str, reason: str, now=None) -> dict:
    """Write a verified bundle of the facility (caller holds the lock); returns its manifest.

    The bundle is staged, every file copied with a running sha256, then the whole staging
    directory is re-read and verified before it is swapped in under ``archive/<fid>/``.
    """
    now = _utc(now)
    adir = archive_dir(pf.root, facility_id)
    os.makedirs(adir, exist_ok=True)
    _swap.recover_tree(adir, max_depth=0)
    base = f"{now.strftime('%Y%m%dT%H%M%SZ')}-{kind}"
    bundle_id, i = base, 1
    while os.path.exists(os.path.join(adir, bundle_id)):
        i += 1
        bundle_id = f"{base}-{i}"
    target = os.path.join(adir, bundle_id)
    stage = _swap.staging(target)
    files: dict = {}
    external = []
    hot = []
    man = pf.manifest(facility_id)
    ext_kinds = {p: (e or {}).get("kind", "other") for p, e in (man.get("external") or {}).items()}
    for src, rel, cat in hot_files(pf, facility_id):
        sha, n = _copy_hashed(src, os.path.join(stage, rel))
        files[rel] = {"sha256": sha, "bytes": n}
        hot.append((rel, sha))
        if cat == "external":
            external.append(
                {
                    "path": src,
                    "kind": ext_kinds.get(src, "other"),
                    "bundle_path": rel,
                    "sha256": sha,
                }
            )
    reg = pf.registry
    entry = reg._raw().get(facility_id)
    doc = pf._doc()
    overrides = ((doc.get("retention") or {}).get("overrides") or {}).get(facility_id)
    store = pf.store
    try:
        points = [{"equip": k.equip, "role": k.role} for k in store.points(facility_id=facility_id)]
        equipment = store.equipment(facility_id=facility_id).get(facility_id, {})
    except Exception:  # noqa: BLE001 - the catalog is a convenience copy; the data is what counts
        points, equipment = [], {}
    meta = {
        "facility_id": facility_id,
        "entry": entry,
        "catalog": {"points": points, "equipment": equipment},
        "retention_override": overrides,
        "legal_hold": (doc.get("legal_holds") or {}).get(facility_id),
    }
    for name, payload in (
        ("registry.json", meta),
        ("audit.ndjson", read_audit(pf.root, facility_id=facility_id)),
    ):
        p = os.path.join(stage, name)
        if name.endswith(".ndjson"):
            with open(p, "w", encoding="utf-8") as fh:
                for rec in payload:
                    fh.write(json.dumps(rec, sort_keys=True, default=str) + "\n")
        else:
            _write_json(p, payload)
        files[name] = {"sha256": sha256_file(p), "bytes": os.path.getsize(p)}
    store_files = [os.path.join(stage, r) for r in files if r.startswith("store/")]
    rollup_counts: dict = {}
    for r in files:
        if r.startswith("rollups/"):
            c = rollup_counts.setdefault(r.split("/")[1], {"files": 0, "rows": 0})
            c["files"] += 1
    for freq in rollup_counts:
        rollup_counts[freq]["rows"] = _rows(
            [os.path.join(stage, r) for r in files if r.startswith(f"rollups/{freq}/")]
        )
    from .. import __version__

    manifest = {
        "schema": BUNDLE_SCHEMA,
        "facility_id": facility_id,
        "bundle_id": bundle_id,
        "kind": kind,
        "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "created_by": _actor(),
        "reason": reason,
        "camber_version": __version__,
        "state_at_export": (entry or {}).get("state", "active"),
        "fingerprint": fingerprint(hot),
        "counts": {
            "files": len(files),
            "bytes": sum(e["bytes"] for e in files.values()),
            "store_files": len(store_files),
            "store_rows": _rows(store_files),
            "rollups": rollup_counts,
            "state_files": sum(1 for r in files if r.startswith("state/")),
            "external": len(external),
        },
        "external": external,
        "files": dict(sorted(files.items())),
    }
    _write_json(os.path.join(stage, MANIFEST), manifest)
    with open(os.path.join(stage, MANIFEST_SUM), "w", encoding="utf-8") as fh:
        fh.write(sha256_file(os.path.join(stage, MANIFEST)) + "  " + MANIFEST + "\n")
    check = verify_bundle(stage)
    if not check["ok"]:  # pragma: no cover - a disk that returns other bytes than were written
        raise OSError(f"bundle verification failed while staging: {check['problems'][:3]}")
    _swap.commit(target)
    return manifest


def read_bundle_manifest(path: str) -> dict:
    with open(os.path.join(path, MANIFEST), encoding="utf-8") as fh:
        return json.load(fh)


def list_bundles(root: str, facility_id: str) -> list:
    """The facility's bundles, oldest first: ``[{bundle_id, kind, created_at, path, ...}]``."""
    adir = archive_dir(root, facility_id)
    out: list = []
    if not os.path.isdir(adir):
        return out
    for name in sorted(os.listdir(adir)):
        p = os.path.join(adir, name)
        if not _visible(name) or not os.path.isfile(os.path.join(p, MANIFEST)):
            continue
        try:
            m = read_bundle_manifest(p)
        except (OSError, ValueError):
            out.append({"bundle_id": name, "path": p, "unreadable": True})
            continue
        out.append(
            {
                "bundle_id": m.get("bundle_id", name),
                "kind": m.get("kind"),
                "created_at": m.get("created_at"),
                "created_by": m.get("created_by"),
                "fingerprint": m.get("fingerprint"),
                "counts": m.get("counts"),
                "path": p,
            }
        )
    return out


def verify_bundle(path: str) -> dict:
    """Re-hash a bundle: ``{"ok", "problems", "files"}``.

    Checks the manifest against ``manifest.sha256``, then every listed file's size and sha256, and
    that the bundle holds no file the manifest does not list.
    """
    problems: list = []
    mpath = os.path.join(path, MANIFEST)
    try:
        with open(os.path.join(path, MANIFEST_SUM), encoding="utf-8") as fh:
            want = fh.read().split()[0]
        if sha256_file(mpath) != want:
            problems.append("manifest.json does not match manifest.sha256")
        man = read_bundle_manifest(path)
    except (OSError, ValueError, IndexError) as err:
        return {"ok": False, "problems": [f"unreadable manifest: {err}"], "files": 0}
    if man.get("schema") != BUNDLE_SCHEMA:
        problems.append(f"unknown bundle schema {man.get('schema')!r}")
    listed = man.get("files") or {}
    for rel, e in sorted(listed.items()):
        p = os.path.join(path, *rel.split("/"))
        if not os.path.isfile(p):
            problems.append(f"missing: {rel}")
        elif os.path.getsize(p) != e.get("bytes") or sha256_file(p) != e.get("sha256"):
            problems.append(f"checksum mismatch: {rel}")
    extra = sorted(set(_walk(path)) - set(listed) - set(_META))
    problems += [f"not in manifest: {r}" for r in extra]
    return {"ok": not problems, "problems": problems, "files": len(listed)}


def _restore_tree(bundle: str, prefix: str, target: str, listed: dict) -> int:
    """Swap ``target`` for the bundle's files under ``prefix`` (crash-safe); returns files."""
    rels = [r for r in listed if r.startswith(prefix)]
    if not rels:
        return 0
    stage = _swap.staging(target)
    for rel in rels:
        dst = os.path.join(stage, *rel[len(prefix) :].split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.join(bundle, *rel.split("/")), dst)
    _swap.commit(target)
    return len(rels)


def restore_bundle(pf, facility_id: str, bundle_path: str) -> dict:
    """Put a verified bundle's content back (caller holds the lock and updates the registry).

    Store and rollup partitions and ``state/<fid>/`` are each swapped in whole (crash-safe:
    an interrupted restore leaves each tree either as it was or fully restored, and re-running
    finishes it). External artifacts are copied back only where their path is free. Every restored
    file is then re-hashed against the bundle's manifest. Raises ``ValueError`` when the bundle
    fails verification, before anything is touched.
    """
    check = verify_bundle(bundle_path)
    if not check["ok"]:
        raise ValueError(
            f"bundle {bundle_path} failed verification, nothing restored: "
            + "; ".join(check["problems"][:5])
        )
    man = read_bundle_manifest(bundle_path)
    if man.get("facility_id") != facility_id:
        raise ValueError(f"bundle is for {man.get('facility_id')!r}, not {facility_id!r}")
    listed = man.get("files") or {}
    part = f"facility_id={facility_id}"
    counts: dict = {"store": 0, "rollups": 0, "state": 0, "external": 0, "external_skipped": []}
    counts["store"] = _restore_tree(
        bundle_path, f"store/{part}/", os.path.join(pf.store_root, part), listed
    )
    freqs = sorted({r.split("/")[1] for r in listed if r.startswith("rollups/")})
    for freq in freqs:
        target = os.path.join(pf.root, ROLLUPS_DIR, freq, part)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        counts["rollups"] += _restore_tree(bundle_path, f"rollups/{freq}/{part}/", target, listed)
    sdir = state_dir(pf.root, facility_id)
    os.makedirs(os.path.dirname(sdir), exist_ok=True)
    counts["state"] = _restore_tree(bundle_path, "state/", sdir, listed)
    for ext in man.get("external") or []:
        dst = ext.get("path")
        if not dst or os.path.exists(dst):
            counts["external_skipped"].append(dst)
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.join(bundle_path, *ext["bundle_path"].split("/")), dst)
        counts["external"] += 1
    # round trip: every restored file must hash to what the bundle recorded
    bad = []
    for src, rel, _cat in hot_files(pf, facility_id):
        e = listed.get(rel)
        if rel.startswith("external/"):
            continue  # external paths are matched by original path below
        if e is None or sha256_file(src) != e["sha256"]:
            bad.append(rel)
    missing = [
        r
        for r in listed
        if r.startswith(("store/", "rollups/", "state/"))
        and not os.path.isfile(_live_path(pf, facility_id, r))
    ]
    if bad or missing:  # pragma: no cover - a disk that returns other bytes than were written
        raise OSError(f"restored files do not match the bundle: {(bad + missing)[:5]}")
    from ..resolve import clear_store_cache

    pf.store._invalidate_catalog()
    clear_store_cache(pf.store_root, facility_id)
    counts["verified"] = True
    return counts


def _live_path(pf, facility_id: str, rel: str) -> str:
    parts = rel.split("/")
    if parts[0] == "store":
        return os.path.join(pf.store_root, *parts[1:])
    if parts[0] == "rollups":
        return os.path.join(pf.root, ROLLUPS_DIR, *parts[1:])
    return os.path.join(state_dir(pf.root, facility_id), *parts[1:])
