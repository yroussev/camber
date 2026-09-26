"""Per-facility state under ``<root>/state/<facility_id>/`` and its manifest.

::

    state/<facility_id>/
      faults.json          fault lifecycle (camber.faultlifecycle), keyed by facility_id
      baselines.json       frozen drift baselines (camber.store.modelstore), keyed by facility_id
      migrated/            the original records each migrated legacy file held for this facility
      manifest.json        every file above with its sha256, plus artifacts kept elsewhere

The manifest is the facility's footprint outside the Parquet store: every file under its state
directory (rescanned each time the manifest is written) and every **external** artifact a run
wrote for it at a path the config chose (reports, a ``drift.store`` outside ``state/``), each with
its sha256 and size. Offboarding and purging (a later step) enumerate a facility from here.

This module also maps free-text site labels to facility ids (:class:`SiteResolver`) for the
migration and for the compatibility read path.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import os
import unicodedata

from .._statefile import read_json, write_json
from ..store import make_facility_id, require_facility_id

STATE_DIR = "state"
MANIFEST_FILE = "manifest.json"
FAULTS_FILE = "faults.json"
BASELINES_FILE = "baselines.json"
MIGRATED_DIR = "migrated"
MANIFEST_SCHEMA = 1


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def state_dir(root: str, facility_id: str) -> str:
    """``<root>/state/<facility_id>`` (the id is validated, so the path is always safe)."""
    return os.path.join(root, STATE_DIR, require_facility_id(facility_id))


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_manifest(root: str, facility_id: str) -> dict:
    """The facility's manifest (``{}`` when it has none yet)."""
    return read_json(os.path.join(state_dir(root, facility_id), MANIFEST_FILE))


def _entry(path: str, kind: str, prev: dict) -> dict:
    if not os.path.isfile(path):
        return {"kind": kind, "missing": True, "sha256": prev.get("sha256"), "bytes": None}
    digest = sha256_file(path)
    same = prev.get("sha256") == digest and prev.get("updated_at")
    return {
        "kind": kind,
        "sha256": digest,
        "bytes": os.path.getsize(path),
        "updated_at": prev["updated_at"] if same else _utc_now(),
    }


def _kind_of(rel: str) -> str:
    top = rel.split("/", 1)[0]
    return {
        FAULTS_FILE: "faults",
        BASELINES_FILE: "baselines",
        MIGRATED_DIR: "migrated",
        "reports": "report",
    }.get(top, "other")


def refresh_manifest(root: str, facility_id: str, *, external=None, migrated_from=None) -> dict:
    """Rewrite the facility's manifest (the caller holds the workspace lock).

    Every file under ``state/<facility_id>/`` is rescanned (sha256, size); ``external`` adds or
    updates ``{absolute path: kind}`` artifacts kept elsewhere (earlier ones are kept and
    re-hashed, flagged ``missing`` once deleted); ``migrated_from`` appends migration records.
    Returns the manifest.
    """
    sdir = state_dir(root, facility_id)
    old = read_manifest(root, facility_id)
    old_files = old.get("files") or {}
    files = {}
    if os.path.isdir(sdir):
        for dirpath, _dirs, names in os.walk(sdir):
            for n in sorted(names):
                if n.endswith(".tmp"):
                    continue
                full = os.path.join(dirpath, n)
                rel = os.path.relpath(full, sdir).replace(os.sep, "/")
                if rel == MANIFEST_FILE:
                    continue
                files[rel] = _entry(full, _kind_of(rel), old_files.get(rel) or {})
    ext_old = old.get("external") or {}
    kinds = {p: (e or {}).get("kind", "other") for p, e in ext_old.items()}
    kinds.update({os.path.abspath(p): k for p, k in (external or {}).items()})
    ext = {p: _entry(p, k, ext_old.get(p) or {}) for p, k in sorted(kinds.items())}
    doc = {
        "schema_version": MANIFEST_SCHEMA,
        "facility_id": facility_id,
        "updated_at": _utc_now(),
        "files": dict(sorted(files.items())),
        "external": ext,
        "migrated_from": list(old.get("migrated_from") or []) + list(migrated_from or []),
    }
    unchanged = {k: v for k, v in doc.items() if k != "updated_at"} == {
        k: v for k, v in old.items() if k != "updated_at"
    }
    if unchanged:
        return old
    write_json(os.path.join(sdir, MANIFEST_FILE), doc)
    return doc


def record_outputs(root: str, facility_id: str, paths, *, timeout: float = 30.0) -> dict:
    """List artifacts a run wrote for a facility in its manifest, under the workspace lock.

    ``paths`` is ``{path: kind}``. Files inside the facility's state directory are picked up by
    the rescan; anything else is recorded as external.
    """
    from ._lock import portfolio_lock

    sdir = os.path.abspath(state_dir(root, facility_id))
    ext = {}
    for p, kind in dict(paths).items():
        if not p:
            continue
        ap = os.path.abspath(p)
        if not (ap == sdir or ap.startswith(sdir + os.sep)):
            ext[ap] = kind
    with portfolio_lock(root, timeout=timeout):
        return refresh_manifest(root, facility_id, external=ext)


# --------------------------------------------------------------------------- site labels


def fold(label: str) -> str:
    """A site label compared loosely: Unicode NFC, case-folded, whitespace collapsed."""
    return " ".join(unicodedata.normalize("NFC", str(label)).casefold().split())


class SiteResolver:
    """Maps free-text site labels to facility ids through the workspace registry.

    A facility is known by its id, its registered ``name``, its current ``display_name`` and every
    display name it had before (from the audit log's ``facility.rename`` records). A label maps to
    a facility when exactly one facility carries it -- compared exactly first, then loosely
    (:func:`fold`). A label carried by several facilities is **ambiguous**; one carried by a
    tombstoned (removed) facility is refused, because the history could be the old building's.
    As a last resort a label whose :func:`~camber.store.make_facility_id` is a known facility maps
    to it (that is how an un-pinned CSV config derives its id).
    """

    def __init__(self, portfolio):
        self.pf = portfolio
        self.live = {
            fid: e for fid, e in portfolio.facilities().items() if e.get("state") != "purged"
        }
        self.display = {fid: e.get("display_name") or fid for fid, e in self.live.items()}
        names: dict = {
            fid: {fid, e.get("name"), e.get("display_name")} for fid, e in self.live.items()
        }
        for rec in portfolio.audit_log():
            fid = rec.get("facility_id")
            if fid not in names:
                continue
            d = rec.get("details") or {}
            if rec.get("action") == "facility.rename":
                names[fid].update((d.get("from"), d.get("to")))
            elif d.get("name"):
                names[fid].add(d.get("name"))
        tomb: dict = {}
        for fid, t in portfolio.registry.tombstones().items():
            tomb[fid] = {fid, (t or {}).get("name"), (t or {}).get("display_name")}
        self._names = {k: {n for n in v if n} for k, v in names.items()}
        self._tomb = {k: {n for n in v if n} for k, v in tomb.items()}

    def _hits(self, label: str, key) -> tuple:
        live = sorted(f for f, ns in self._names.items() if label in {key(n) for n in ns})
        dead = sorted(f for f, ns in self._tomb.items() if label in {key(n) for n in ns})
        return live, dead

    def resolve(self, label, mapping=None) -> dict:
        """``{"facility_id", "via", "candidates", "problem"}`` for one label.

        ``via`` is ``map`` (an explicit ``mapping`` entry), ``exact``, ``folded`` or ``derived``;
        ``problem`` is ``None`` or one of ``empty``, ``ambiguous``, ``tombstoned``, ``unmapped``,
        ``unknown`` (a mapping to an id that is not a live facility).
        """
        label = "" if label is None else str(label)
        if mapping and label in mapping:
            return self.check(mapping[label], via="map")
        if not label.strip():
            return _res(None, None, [], "empty")
        for via, key, probe in (
            ("exact", lambda n: n, label),
            ("folded", fold, fold(label)),
        ):
            live, dead = self._hits(probe, key)
            if len(live) + len(dead) == 1 and live:
                return _res(live[0], via, live, None)
            if live or dead:
                problem = "tombstoned" if dead and not live else "ambiguous"
                return _res(None, via, live + [f"{d} (tombstoned)" for d in dead], problem)
        derived = make_facility_id(label)
        if derived in self.live:
            return _res(derived, "derived", [derived], None)
        if derived in self._tomb:
            return _res(None, "derived", [f"{derived} (tombstoned)"], "tombstoned")
        return _res(None, None, [], "unmapped")

    def check(self, facility_id, *, via: str) -> dict:
        """Validate an id given explicitly (a mapping, a config, a record's own ``facility_id``)."""
        fid = str(facility_id or "")
        if fid in self.live:
            return _res(fid, via, [fid], None)
        if fid in self._tomb:
            return _res(None, via, [f"{fid} (tombstoned)"], "tombstoned")
        return _res(None, via, [fid] if fid else [], "unknown")

    def legacy_sites(self, facility_id: str) -> list:
        """Labels that map to ``facility_id`` exactly and unambiguously (for the compat path)."""
        out = []
        for n in sorted(self._names.get(facility_id, ())):
            live, dead = self._hits(n, lambda x: x)
            if live == [facility_id] and not dead:
                out.append(n)
        return out


def _res(fid, via, candidates, problem) -> dict:
    return {"facility_id": fid, "via": via, "candidates": list(candidates), "problem": problem}
