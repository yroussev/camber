"""Facility identity for the time-series store: a stable, unique, path-safe ``facility_id``.

The store partitions by ``facility_id`` (``<root>/facility_id=<id>/year=<Y>/``), decoupled from a
facility's human display name, which lives in a small registry (``_facilities.json``, a sibling of
the ``_catalog.json`` cache). A path-safe id is what lets a portfolio of many facilities coexist
under one root without name collisions, rename-orphaning, or the filesystem-encoding hazards that a
raw name causes as a partition directory.

- :func:`make_facility_id` derives a deterministic path-safe id from a name/seed.
- :func:`require_facility_id` guards writes so an unsafe id fails loudly instead of silently
  corrupting the layout.
- :class:`FacilityRegistry` maps ``facility_id -> {name, ...metadata}``.
- :func:`migrate_site_to_facility` converts an old ``site=<name>`` store in place.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import urllib.parse

_FACILITIES = "_facilities.json"  # leading "_" -> ignored by pyarrow dataset discovery
_CATALOG = "_catalog.json"

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
    """A JSON registry mapping ``facility_id -> {"name": str, ...metadata}`` in a store root."""

    def __init__(self, root: str):
        self.root = root

    def _path(self) -> str:
        return os.path.join(self.root, _FACILITIES)

    def all(self) -> dict:
        """The whole registry ``{facility_id: {...}}`` (empty if absent/corrupt)."""
        p = self._path()
        if not os.path.isfile(p):
            return {}
        try:
            with open(p, encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def _write(self, data: dict) -> None:
        os.makedirs(self.root, exist_ok=True)
        tmp = self._path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, sort_keys=True)
        os.replace(tmp, self._path())

    def register(self, facility_id: str, name: str | None = None, **meta) -> None:
        """Record a facility's display ``name`` and any metadata.

        Raises ``ValueError`` if ``facility_id`` is already registered under a *different* name --
        a cheap guard against two distinct facilities silently sharing one id.
        """
        require_facility_id(facility_id)
        data = self.all()
        entry = dict(data.get(facility_id, {}))
        if name is not None:
            existing = entry.get("name")
            if existing and existing != name:
                raise ValueError(
                    f"facility_id {facility_id!r} already registered as {existing!r}, not {name!r}"
                )
            entry["name"] = name
        entry.update(meta)
        data[facility_id] = entry
        self._write(data)

    def name(self, facility_id: str) -> str:
        """Display name for ``facility_id`` (falls back to the id when unregistered)."""
        return self.all().get(facility_id, {}).get("name") or facility_id

    def get(self, facility_id: str) -> dict:
        """The metadata entry for ``facility_id`` (empty dict if unregistered)."""
        return dict(self.all().get(facility_id, {}))

    def remove(self, facility_id: str) -> bool:
        """Drop ``facility_id`` from the registry; returns whether it was present."""
        data = self.all()
        if facility_id in data:
            del data[facility_id]
            self._write(data)
            return True
        return False


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
