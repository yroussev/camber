"""Facility identity for the time-series store: a stable, unique, path-safe ``facility_id``.

The store partitions by ``facility_id`` (``<root>/facility_id=<id>/year=<Y>/``), decoupled from a
facility's human display name, which lives in a small registry (``_facilities.json``, a sibling of
the ``_catalog.json`` cache). A path-safe id is what lets a portfolio of many facilities coexist
under one root without name collisions, rename-orphaning, or the filesystem-encoding hazards that a
raw name causes as a partition directory.

- :func:`make_facility_id` derives a deterministic path-safe id from a name/seed.
- :func:`require_facility_id` guards writes so an unsafe id fails loudly instead of silently
  corrupting the layout.
- :class:`FacilityRegistry` maps ``facility_id -> {name, state, ...metadata}`` and tombstones
  removed ids so they are never reused.
- :func:`migrate_site_to_facility` converts an old ``site=<name>`` store in place.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import urllib.parse

_FACILITIES = "_facilities.json"  # leading "_" -> ignored by pyarrow dataset discovery
_CATALOG = "_catalog.json"
_TOMBSTONES = "_tombstones.json"  # ids removed from the registry; never reusable
_WORKSPACE_MARKER = "_workspace.json"  # in a portfolio's store: points back at the workspace
_LIFECYCLE_KEYS = ("state", "created_at", "state_changed_at")
_CREATE_STATES = ("provisioning", "active")

# A facility_id contains only path-safe characters (no "/", "=", whitespace, or unicode -- the
# things that break/URL-encode a hive partition dir). Mixed case is allowed so natural external ids
# (e.g. "DemoSite", a BDG2 building id) pass directly; make_facility_id() emits lowercase, and on
# a case-insensitive filesystem two ids differing only by case collide -- prefer lowercase there.
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_MAX_ID_LEN = 200
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def _slug(name: str) -> str:
    """Lowercase, path-safe slug of ``name`` (runs of other characters collapse to ``-``)."""
    return _SLUG_STRIP.sub("-", str(name).lower()).strip("-")


def make_facility_id(name: str) -> str:
    """Derive a deterministic, path-safe ``facility_id`` from a name or seed.

    ``"Fox Lodge" -> "fox-lodge-9f3a1c"`` (slug + a short SHA-1 of the seed). Deterministic, so it
    is re-derivable, and the hash disambiguates two seeds that slugify identically. The result
    always satisfies :func:`valid_facility_id`.

    **Uniqueness across same-named facilities is the caller's responsibility** -- the same seed
    always yields the same id, so pass a more-specific seed (an address, an external building id)
    when display names can repeat. :meth:`FacilityRegistry.register` surfaces an accidental clash.
    """
    slug = _slug(name)
    digest = hashlib.sha1(str(name).encode("utf-8")).hexdigest()[:6]
    return f"{slug}-{digest}" if slug else digest


def valid_facility_id(facility_id: str) -> bool:
    """True if ``facility_id`` is a safe partition key (see :func:`require_facility_id`)."""
    return (
        isinstance(facility_id, str)
        and len(facility_id) <= _MAX_ID_LEN
        and bool(_ID_RE.match(facility_id))
    )


def require_facility_id(facility_id: str) -> str:
    """Return ``facility_id`` if path-safe, else raise a clear ``ValueError``.

    Rejects the exact inputs that corrupt the store layout -- ``/``, ``=``, whitespace, unicode,
    empty -- rather than letting them become a broken/URL-encoded partition directory.
    """
    if not valid_facility_id(facility_id):
        raise ValueError(
            f"invalid facility_id {facility_id!r}: must match {_ID_RE.pattern} "
            f"(no '/','=',space,unicode; ≤{_MAX_ID_LEN} chars). "
            f"Derive one from a name with camber.store.make_facility_id()."
        )
    return facility_id


class FacilityRegistry:
    """A JSON registry mapping ``facility_id -> {"name": str, ...metadata}`` in a store root.

    **Registry v2** (0.87) adds lifecycle fields to each entry -- ``state`` (see
    :mod:`camber.portfolio`), ``created_at``, ``state_changed_at``, an editable ``display_name``,
    ``owner``, ``portfolio`` tags and ``notes``. Entries written by older versions carry none of
    them and read as ``state: "active"`` with unknown (``None``) dates, so an existing store keeps
    working unchanged. ``name`` stays the name the facility was *registered* under (it is what the
    :meth:`register` collision guard compares); ``display_name`` is what a rename changes, and
    :meth:`name` returns it.

    **Ids are never reused.** :meth:`remove` leaves a tombstone in ``_tombstones.json`` and a new
    registration of a tombstoned id -- or of an id that differs from a known one only by letter
    case, which would share a directory on a case-insensitive filesystem -- is refused.

    When the store root belongs to a portfolio workspace (``camber portfolio init``), every
    read-modify-write takes the workspace's single-writer lock (waiting up to ``lock_timeout``
    seconds) and every new registration or removal is appended to its audit log.
    """

    def __init__(self, root: str, *, lock_timeout: float = 30.0):
        self.root = root
        self.lock_timeout = lock_timeout

    def _path(self) -> str:
        return os.path.join(self.root, _FACILITIES)

    # ----------------------------------------------------------------- workspace hooks

    def _workspace(self):
        """The portfolio workspace root this store belongs to, or ``None``."""
        return _workspace_of_store(self.root)

    def _locked(self):
        ws = self._workspace()
        if ws is None:
            return contextlib.nullcontext()
        from ..portfolio._lock import portfolio_lock  # lazy: portfolio imports the store

        return portfolio_lock(ws, timeout=self.lock_timeout)

    def _audit(self, action: str, **kw) -> None:
        ws = self._workspace()
        if ws is not None:
            from ..portfolio._audit import append_audit, audit_record

            append_audit(ws, audit_record(action, **kw))

    # ----------------------------------------------------------------- reads

    def _raw(self) -> dict:
        return _read_json(self._path())

    def all(self) -> dict:
        """The whole registry ``{facility_id: {...}}`` (empty if absent/corrupt).

        Each entry is normalized to the v2 shape: missing lifecycle fields read as
        ``state="active"``, ``created_at=None``, ``display_name=<name or id>`` and so on.
        """
        return {fid: _normalize(fid, e) for fid, e in self._raw().items() if isinstance(e, dict)}

    def name(self, facility_id: str) -> str:
        """Display name for ``facility_id``: its ``display_name``, else ``name``, else the id."""
        e = self._raw().get(facility_id) or {}
        return e.get("display_name") or e.get("name") or facility_id

    def get(self, facility_id: str) -> dict:
        """The (normalized) entry for ``facility_id`` (empty dict if unregistered)."""
        e = self._raw().get(facility_id)
        return _normalize(facility_id, e) if isinstance(e, dict) else {}

    def state(self, facility_id: str) -> str:
        """Lifecycle state of ``facility_id``; an unregistered facility counts as ``"active"``."""
        e = self._raw().get(facility_id) or {}
        return str(e.get("state") or "active")

    def tombstones(self) -> dict:
        """``{facility_id: {name, removed_at, ...}}`` for every removed id (never reusable)."""
        return _read_json(os.path.join(self.root, _TOMBSTONES))

    # ----------------------------------------------------------------- writes

    def _write(self, data: dict) -> None:
        _write_json(self._path(), data, root=self.root)

    def _check_new(self, facility_id: str, data: dict) -> None:
        """Refuse a *new* registration of a tombstoned id or a case-variant of a known one."""
        tomb = self.tombstones()
        if facility_id in tomb:
            when = (tomb[facility_id] or {}).get("removed_at") or "an earlier release"
            raise ValueError(
                f"facility_id {facility_id!r} was removed ({when}) and is tombstoned: facility ids "
                "are never reused, so old data can never be joined to a new building. Register "
                "the new facility under a different id."
            )
        known = set(data) | set(tomb) | set(_partition_ids(self.root))
        low = facility_id.lower()
        for other in sorted(known):
            if other != facility_id and other.lower() == low:
                raise ValueError(
                    f"facility_id {facility_id!r} differs only by letter case from the existing "
                    f"{other!r}; on a case-insensitive filesystem they would share one directory. "
                    "Use a distinct id (make_facility_id() emits lowercase)."
                )

    def _guard_write(self, facility_id: str) -> None:
        """Raise ``ValueError`` if data must not be written under ``facility_id``.

        Called by :meth:`ParquetStore.write_long`: a tombstoned id, or a new id that differs only
        by case from an existing partition/registration, is refused. Cheap when the facility's
        partition already exists.
        """
        if (
            os.path.isfile(os.path.join(self.root, _TOMBSTONES))
            and facility_id in self.tombstones()
        ):
            self._check_new(facility_id, {})  # raises the tombstone error
        parts = _partition_ids(self.root)
        if facility_id not in parts and facility_id not in self._raw():
            self._check_new(facility_id, self._raw())

    def _create(
        self,
        facility_id: str,
        name,
        *,
        state: str = "active",
        reason: str,
        meta=None,
        action: str = "facility.register",
    ) -> dict:
        """Create a new entry (caller holds the lock); audited. Returns the normalized entry."""
        if state not in _CREATE_STATES:
            raise ValueError(f"a new facility starts {' or '.join(_CREATE_STATES)}, not {state!r}")
        data = self._raw()
        self._check_new(facility_id, data)
        now = _utc_now()
        entry = dict(meta or {})
        if name is not None:
            entry["name"] = name
        entry.update({"state": state, "created_at": now, "state_changed_at": now})
        data[facility_id] = entry
        self._write(data)
        self._audit(
            action,
            facility_id=facility_id,
            to_state=state,
            reason=reason,
            details={"name": name} if name is not None else {},
        )
        return _normalize(facility_id, entry)

    def register(self, facility_id: str, name: str | None = None, **meta) -> None:
        """Record a facility's display ``name`` and any metadata.

        Raises ``ValueError`` if ``facility_id`` is already registered under a *different* name --
        a cheap guard against two distinct facilities silently sharing one id -- or, for a new id,
        if it is tombstoned or collides by case with a known id. A new facility registered this
        way (a store write, a dataset ingest) starts ``active``; the lifecycle fields
        (``state``, ``created_at``, ``state_changed_at``) cannot be changed through ``register``
        -- use :class:`camber.portfolio.Portfolio` for that.
        """
        require_facility_id(facility_id)
        with self._locked():
            data = self._raw()
            if facility_id not in data:
                bad = sorted(k for k in meta if k in _LIFECYCLE_KEYS and k != "state")
                if bad:
                    raise ValueError(f"register() cannot set {', '.join(bad)}")
                state = str(meta.pop("state", "active"))
                self._create(
                    facility_id,
                    name,
                    state=state,
                    reason="registered automatically on first write",
                    meta=meta,
                )
                return
            entry = dict(data[facility_id])
            if name is not None:
                existing = entry.get("name")
                if existing and existing != name:
                    raise ValueError(
                        f"facility_id {facility_id!r} already registered as {existing!r}, "
                        f"not {name!r}"
                    )
                entry["name"] = name
            clash = sorted(k for k in meta if k in _LIFECYCLE_KEYS and meta[k] != entry.get(k))
            if clash:
                raise ValueError(
                    f"register() cannot change {', '.join(clash)} of {facility_id!r}; "
                    "lifecycle changes go through camber.portfolio (camber facility ...)"
                )
            entry.update(meta)
            data[facility_id] = entry
            self._write(data)

    def _update(self, facility_id: str, fields: dict) -> dict:
        """Merge ``fields`` into an existing entry (caller holds the lock); return the result."""
        data = self._raw()
        if facility_id not in data:
            raise KeyError(f"facility {facility_id!r} is not registered")
        entry = dict(data[facility_id])
        entry.update(fields)
        data[facility_id] = entry
        self._write(data)
        return _normalize(facility_id, entry)

    def _tombstone(self, facility_id: str, entry: dict, *, reason: str) -> None:
        tomb = self.tombstones()
        rec = {
            "name": entry.get("name"),
            "display_name": entry.get("display_name"),
            "state": entry.get("state") or "active",
            "removed_at": _utc_now(),
            "reason": reason,
        }
        if isinstance(entry.get("dataset"), dict):  # provenance: lets a dataset reclaim its id
            rec["dataset_id"] = entry["dataset"].get("dataset_id")
        tomb[facility_id] = {k: v for k, v in rec.items() if v is not None}
        _write_json(os.path.join(self.root, _TOMBSTONES), tomb, root=self.root)

    def remove(self, facility_id: str, *, reason: str = "registry entry removed") -> bool:
        """Drop ``facility_id`` from the registry; returns whether it was present.

        The id is tombstoned (written *before* the entry is dropped, so a crash never leaves it
        reusable) and can never be registered again. Removing the entry does not touch stored
        data -- see :meth:`ParquetStore.drop_facility`.
        """
        return self._forget(facility_id, had_data=False, reason=reason)

    def _forget(self, facility_id: str, *, had_data: bool, reason: str) -> bool:
        with self._locked():
            data = self._raw()
            present = facility_id in data
            if not present and not had_data:
                return False
            entry = dict(data.get(facility_id) or {})
            self._tombstone(facility_id, entry, reason=reason)
            if present:
                del data[facility_id]
                self._write(data)
            self._audit(
                "facility.remove",
                facility_id=facility_id,
                from_state=entry.get("state") or "active",
                to_state=None,
                reason=reason,
                details={"tombstoned": True, "name": entry.get("name")},
            )
            return present

    def reclaim(self, facility_id: str, *, reason: str) -> bool:
        """Lift ``facility_id``'s tombstone so the *same* facility can be registered again.

        Only for re-creating the very facility the tombstone records -- e.g. re-ingesting a
        catalog dataset whose facility id is derived from the dataset itself. Never use it to hand
        a retired id to a different building. Audited; returns whether a tombstone was lifted.
        """
        with self._locked():
            tomb = self.tombstones()
            if facility_id not in tomb:
                return False
            rec = tomb.pop(facility_id)
            _write_json(os.path.join(self.root, _TOMBSTONES), tomb, root=self.root)
            self._audit(
                "facility.reclaim", facility_id=facility_id, reason=reason, details={"tomb": rec}
            )
            return True


def _workspace_of_store(root: str):
    """The portfolio workspace a store root belongs to (``None`` for a standalone store).

    A workspace's store carries a ``_workspace.json`` marker naming the workspace root relative to
    the store; the workspace must also have its ``_portfolio.json``.
    """
    marker = os.path.join(root, _WORKSPACE_MARKER)
    if not os.path.isfile(marker):
        return None
    rel = _read_json(marker).get("portfolio")
    if not isinstance(rel, str) or not rel:
        return None
    ws = os.path.normpath(os.path.join(os.path.abspath(root), rel))
    return ws if os.path.isfile(os.path.join(ws, "_portfolio.json")) else None


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_json(path: str) -> dict:
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write_json(path: str, data: dict, *, root: str) -> None:
    """Atomic write: temp file + ``os.replace`` (readers never see a half-written file)."""
    os.makedirs(root, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, sort_keys=True)
    os.replace(tmp, path)


def _partition_ids(root: str) -> list:
    """Facility ids that have a partition directory under ``root``."""
    try:
        names = os.listdir(root)
    except OSError:
        return []
    return [n.split("=", 1)[1] for n in names if n.startswith("facility_id=")]


def _normalize(facility_id: str, entry: dict) -> dict:
    """An entry in the v2 shape; v1 entries read as ``active`` with unknown dates."""
    out = dict(entry)
    out["state"] = out.get("state") or "active"
    out.setdefault("created_at", None)
    out.setdefault("state_changed_at", None)
    out["display_name"] = out.get("display_name") or out.get("name") or facility_id
    out.setdefault("owner", None)
    tags = out.get("portfolio")
    out["portfolio"] = list(tags) if isinstance(tags, (list, tuple)) else []
    out.setdefault("notes", None)
    return out


def migrate_site_to_facility(root: str, *, derive_ids: bool = False) -> int:
    """Convert an old ``site=<value>`` store under ``root`` to ``facility_id=<id>`` in place.

    Each ``site=<v>`` partition is renamed to ``facility_id=<v>`` when ``<v>`` is already path-safe
    (the common case), else to ``facility_id=make_facility_id(<v>)``; the original value is recorded
    as the facility's display name. Set ``derive_ids=True`` to slug+hash every id even when the old
    value was already safe. Idempotent; returns the number of partitions migrated.

    Partition values live in the directory name (not the parquet files), so a directory rename plus
    a hive read is sufficient -- no row rewrite. The point catalog is invalidated so the next read
    rebuilds it for the new keys.
    """
    if not os.path.isdir(root):
        return 0
    reg = FacilityRegistry(root)
    migrated = 0
    for entry in list(os.listdir(root)):
        if not entry.startswith("site="):
            continue
        value = urllib.parse.unquote(entry.split("=", 1)[1])  # decode any pyarrow URL-encoding
        fid = value if (valid_facility_id(value) and not derive_ids) else make_facility_id(value)
        src = os.path.join(root, entry)
        dst = os.path.join(root, f"facility_id={fid}")
        if os.path.abspath(src) == os.path.abspath(dst):
            continue  # already migrated
        if os.path.exists(dst):  # two old sites collapsed to one id -> merge year dirs
            for child in os.listdir(src):
                shutil.move(os.path.join(src, child), os.path.join(dst, child))
            os.rmdir(src)
        else:
            os.rename(src, dst)
        reg.register(fid, name=value)
        migrated += 1
    if migrated:
        cat = os.path.join(root, _CATALOG)
        if os.path.isfile(cat):
            try:
                os.remove(cat)  # invalidate; next points() rebuilds for the new keys
            except OSError:
                pass
    return migrated
