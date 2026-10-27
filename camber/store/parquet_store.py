"""A Parquet-backed time-series store keyed to the semantic entity model.

Layout: one tidy (long-form) dataset, hive-partitioned by ``facility_id``, ``year`` and
``month``, so a portfolio of buildings lives under one root and a query touches only the
partitions it needs::

    <root>/facility_id=fox-lodge-9f3a1c/year=2024/month=7/part-*.parquet

Stores written before 0.95 are partitioned by year only (``year=2024/part-*.parquet``); they are
read unchanged, a store may mix both layouts, and :meth:`ParquetStore.migrate_partitions` converts
the year-only partitions (month partitions are what date-level retention prunes; see
docs/PORTFOLIO.md). Each row is ``(ts, equip, equip_class, role, value)`` plus the
``facility_id``/``year``/``month`` partition keys. The ``facility_id`` is a stable, path-safe
identifier (see :mod:`camber.store.facilities`); a facility's human display name and metadata live
in a sibling ``_facilities.json`` registry, decoupled from the storage identity so a rename
never orphans history and two same-named facilities never collide. Storing by *role* (the
vendor-neutral meaning, see :mod:`camber.model.roles`) rather than the raw vendor token
means a query reads the same column name on any building.

Reads use pyarrow dataset filters (predicate pushdown on the partition keys and a
row filter on ts/equip/role), then pivot to the wide, role-named frames the rules
already consume -- so the store is a drop-in source behind ``resolve``-style data
without changing rule code.

**Single writer (0.104, #135).** Every write method (``write_long`` / ``write_role_frame``,
``register_facility``, ``write_rollup``, ``prune``, ``drop_partition``, ``drop_facility``,
``migrate_partitions(apply=True)``, ``normalize(apply=True)``) holds the store's single-writer lock
(:func:`camber.store._lock.store_lock`: ``<store>/_lock``, or the workspace's lock for a workspace
store) for its duration. A second process waits up to ``lock_timeout`` seconds, then gets
``StoreLocked`` naming the holder. The lock is re-entrant within a process, so a bulk load takes it
once around the whole operation with :meth:`ParquetStore.lock` and every write inside re-enters it
cheaply. Reads never take it; a read that rebuilds the cached ``_catalog.json`` guards its
write-back with the catalog's write generation instead (#141, see ``_CATALOG_GEN``).

Dependencies: pandas + pyarrow only (no SQL engine), consistent with the rest of
the package.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
import unicodedata
import uuid
from collections import OrderedDict
from contextlib import nullcontext as _nullcontext
from dataclasses import dataclass
from typing import BinaryIO

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds

from .._deprecation import deprecated
from ..model.roles import Role
from ._leftovers import STAGING_DIR
from .facilities import FacilityRegistry, require_facility_id

# Stable column schema for the long-form store.
_TS = "ts"
_VALUE = "value"
_EQUIP = "equip"
_CLASS = "equip_class"
_ROLE = "role"
_FACILITY = "facility_id"
_YEAR = "year"
_MONTH = "month"
_PART_RE = re.compile(r"^part-(\d+)-\d+\.parquet$")

# A cached catalog of distinct (facility_id, equip, role) keys, written alongside the dataset so
# points() needn't rescan every partition. Arrow's dataset discovery ignores leading-"_"
# paths, so this file is invisible to reads.
_CATALOG = "_catalog.json"

# The catalog's write generation (#141): a random token every invalidation replaces (under the
# store lock, after the data change). A catalog records the token current when its scan began and
# is served only while that token is still current, so a read-side rebuild that raced a write and
# wrote back a pre-write catalog is ignored rather than served. A random token, not a counter: a
# lost or reset generation file can never make an old catalog look current again (no ABA).
_CATALOG_GEN = "_catalog.gen"
_GEN_UNKNOWN = object()  # an unreadable generation file: serve and cache nothing


def _read_gen(root: str):
    """The store's current catalog generation token: ``None`` for a store no write has
    invalidated since #141 (its legacy catalog stays valid), :data:`_GEN_UNKNOWN` if unreadable."""
    try:
        with open(os.path.join(root, _CATALOG_GEN), encoding="ascii") as fh:
            return fh.read().strip() or None
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError):
        return _GEN_UNKNOWN


def _replace_atomically(root: str, name: str, text: str, *, keep=lambda: True) -> bool:
    """Write ``text`` to ``root/name`` via a unique temp file and ``os.replace``, if ``keep()``
    still holds just before the rename. Returns whether the file was replaced."""
    fd, tmp = tempfile.mkstemp(prefix=f"{name}.", suffix=".tmp", dir=root)  # "_..." -> unseen
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        if keep():
            os.replace(tmp, os.path.join(root, name))
            return True
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return False


def _invalidate_catalog_at(root: str) -> None:
    """Mark the catalog of the store at ``root`` stale: a fresh generation token, then drop the
    catalog file (O(1)). Called by every writer after its data change, under the store lock."""
    if not os.path.isdir(root):
        return
    try:
        _replace_atomically(root, _CATALOG_GEN, os.urandom(16).hex())
    except OSError:
        pass
    try:
        os.remove(os.path.join(root, _CATALOG))
    except OSError:
        pass


# Written into a year directory by migrate_partitions (0.95): the legacy part files it split into
# month partitions, by original name, with their sha256. The edge landing reads it so a year-only
# upload an older forwarder re-sends after the migration is recognised as data already in the
# store, not appended twice. Leading "_" keeps it out of dataset discovery.
_MIGRATED = "_migrated.json"


# Per-facility fragment index (#35): which part files hold which equipment, so a one-equipment read
# opens only that equipment's files instead of every file of the facility. Keyed on (root,
# facility_id); each entry carries the facility partition's modification signature, so a write,
# prune or drop rebuilds it. The dataset schema is captured with it, so a pruned read materializes
# exactly the columns and types a full-dataset read does.
_FRAG_INDEX_MAX = 16
_FRAG_INDEX: OrderedDict = OrderedDict()
_FRAG_LOCK = threading.Lock()
_NO_PARTITION = object()  # ParquetStore._scoped_dataset: the facility has no partition directory

# Per-facility catalog memo (0.111, #293): one facility's point keys and equipment classes from a
# projected scan of that facility's partition only, for a points() / equipment() call on one
# facility while the store-wide catalog is stale. A no-persist reader (an MCP call) never writes
# the catalog back, so without it every such call rescanned every facility of the store. Keyed
# and validated like the fragment index; holds only the keys (no frames), and is bounded.
_FAC_CATALOG_MAX = 256
_FAC_CATALOG: OrderedDict = OrderedDict()


class StoreReadError(OSError):
    """Stored data could not be read (0.111, #301).

    ``facility_id`` is the facility the read was scoped to (``None`` for a store-wide read).
    ``retryable`` is True when a part file went missing between listing and opening it -- a
    concurrent ingest or prune replaced it, and the same read retried once already -- so a later
    call will likely succeed; False for a damaged (truncated, empty, not Parquet) or unreadable
    file, which a retry does not fix. ``reason`` is a short description that names no path; the
    exception text adds the underlying error.
    """

    def __init__(self, reason: str, *, facility_id=None, retryable: bool = False, cause=None):
        detail = f" ({type(cause).__name__}: {cause})" if cause is not None else ""
        super().__init__(f"{reason}{detail}")
        self.reason = reason
        self.facility_id = facility_id
        self.retryable = retryable


# Canonical part layout (#130): the same rows written by the same CAMBER and pyarrow versions give
# byte-identical part files. Rows are sorted timestamp first, written single-threaded into one row
# group per part (up to 2**20 rows), with fixed writer options and no pandas metadata (which embeds
# the pandas and pyarrow versions); a ``camber.layout`` key marks a part written this way. Parts
# written before carry pandas metadata and read back the same.
_LAYOUT_KEY = b"camber.layout"
_LAYOUT = b"1"
_COLUMNS = (_TS, _EQUIP, _CLASS, _ROLE, _VALUE)
_SORT_KEYS = (_TS, _EQUIP, _ROLE, _CLASS, _VALUE)
_ROWS_PER_GROUP = 1 << 20
_PARQUET_OPTIONS = {
    "compression": "snappy",
    "use_dictionary": True,
    "write_statistics": True,
    "version": "2.6",
    "data_page_version": "1.0",
}


def _canonicalize(table: pa.Table, *, keep_meta=()) -> pa.Table:
    """``table`` in the canonical part layout: the store columns first in a fixed order (any other
    column after them, by name), rows sorted by ``(ts, equip, role, equip_class, value)`` (a stable
    sort, so fully identical rows need no further key), and schema metadata reduced to the layout
    marker plus the ``keep_meta`` keys."""
    names = table.column_names
    order = [c for c in _COLUMNS if c in names] + sorted(c for c in names if c not in _COLUMNS)
    table = table.select(order)
    keys = [(c, "ascending") for c in _SORT_KEYS if c in names]
    if keys and table.num_rows > 1:
        table = table.sort_by(keys)
    old = table.schema.metadata or {}
    meta = {k: old[k] for k in keep_meta if k in old}
    meta[_LAYOUT_KEY] = _LAYOUT
    return table.replace_schema_metadata(meta)


def _canonical_table(df: pd.DataFrame) -> pa.Table:
    """A long-form frame as a canonical Arrow table (see :func:`_canonicalize`)."""
    return _canonicalize(pa.Table.from_pandas(df, preserve_index=False))


def _write_part(table: pa.Table, path: str | BinaryIO) -> None:
    """Write one canonical part file (or buffer: the edge forwarder's parts, 0.107) with the
    store's fixed writer options."""
    import pyarrow.parquet as pq

    pq.write_table(table, path, row_group_size=_ROWS_PER_GROUP, **_PARQUET_OPTIONS)


def _sha256_file(path: str) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_part_file(path: str) -> pa.Table:
    """One Parquet part file's own columns, exactly as stored.

    Not ``pq.read_table(path)``: before pyarrow 25 that applies hive partition discovery to a
    single file's path, so a part under ``facility_id=X/year=Y/`` comes back with dictionary-typed
    ``facility_id`` and ``year`` columns. Rewritten into the store, those columns then clash with
    the string/int partition keys and the whole dataset stops opening ("Unable to merge: Field
    facility_id has incompatible types").
    """
    import pyarrow.parquet as pq

    with pq.ParquetFile(path) as f:
        return f.read()


def _partition_signature(fdir: str) -> tuple:
    """Name, size and mtime of every entry of a facility partition, its year dirs and their
    month dirs (part files included), plus the partition dir's own mtime: changes on any write,
    prune or drop, whichever layout the partition uses, and (#312) when a part file is rewritten
    in place. Stat calls only; ``()`` when the partition cannot be listed."""

    def walk(path: str, rel: str, depth: int, out: list) -> None:
        with os.scandir(path) as it:
            entries = sorted(it, key=lambda e: e.name)
        for e in entries:
            st = e.stat()
            name = f"{rel}{e.name}"
            out.append((name, st.st_size, st.st_mtime_ns))
            if depth and e.is_dir():
                walk(e.path, name + "/", depth - 1, out)

    try:
        sig: list = [os.stat(fdir).st_mtime_ns]
        walk(fdir, "", 2, sig)
        return tuple(sig)
    except OSError:
        return ()


def _fragment_equips(frag) -> frozenset | None:
    """The equipment ids a part file holds: from its row-group statistics when every row group
    holds one equipment (min == max), else from a projected read of the equip column."""
    try:
        md = frag.metadata
        col = md.schema.to_arrow_schema().get_field_index(_EQUIP)
        if col >= 0 and md.num_row_groups:
            vals: set = set()
            for i in range(md.num_row_groups):
                st = md.row_group(i).column(col).statistics
                if st is None or not st.has_min_max or st.min != st.max or st.null_count:
                    break
                vals.add(st.min)
            else:  # every row group holds a single equipment
                return frozenset(vals)
        if col < 0:
            return frozenset()
        tab = frag.to_table(columns=[_EQUIP])
        return frozenset(v for v in tab.column(0).unique().to_pylist() if v is not None)
    except Exception:  # noqa: BLE001 - an unreadable footer means "don't prune this file"
        return None


def _role_slug(r) -> str:
    """Role enum or string -> its stable slug."""
    return r.value if isinstance(r, Role) else str(r)


def _bad_label_char(label: str) -> str | None:
    """The first character of ``label`` an id may not hold: a control character (C0, DEL, C1:
    newline, tab, ESC, NUL ...), a format character (bidi override or isolate, zero-width, tag)
    or a line / paragraph separator."""
    for ch in label:
        if unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Zl", "Zp"):
            return ch
    return None


def _require_labels(long) -> None:
    """Refuse (``ValueError``) a long frame whose equipment id, equipment class or role holds a
    character :func:`_bad_label_char` names (0.111, #291).

    Such an id cannot be told apart from its neighbours on screen, breaks a one-line summary and
    has been used to forge notices in an assistant's tool results, so it is refused at ingest
    rather than silently rewritten (rewriting could merge two ids, or split one already stored).
    Ids already in a store are read as they are; map or rename them before re-ingesting."""
    if long is None or len(long) == 0:
        return
    for col, what in ((_EQUIP, "equipment id"), (_CLASS, "equipment class"), (_ROLE, "role")):
        if col not in long:
            continue
        for label in pd.unique(long[col].astype(str)):
            ch = _bad_label_char(label)
            if ch is not None:
                raise ValueError(
                    f"{what} {label[:80]!r} holds the character U+{ord(ch):04X} "
                    f"({unicodedata.name(ch, unicodedata.category(ch))}); an id may hold no "
                    "control, bidi or invisible character -- rename it in the mapping"
                )


def _scratch_store(root: str) -> ParquetStore:
    """A store whose writes take no lock of their own: an ingest's staging area or a workspace
    rollup, written only under the lock its caller already holds on the store it belongs to."""
    st = ParquetStore(root)
    st._self_lock = False
    return st


def role_frame_to_long(frame: pd.DataFrame, *, equip: str, equip_class: str = "") -> pd.DataFrame:
    """Melt a wide role-named frame (``resolve`` output) to the store's long form.

    ``frame`` has a DatetimeIndex and columns that are :class:`Role` members (or
    role slugs). NaNs are dropped -- the store holds observations, not a dense
    grid. Returns columns ``[ts, equip, equip_class, role, value]``. The
    ``facility_id`` partition key is attached at write time by :meth:`ParquetStore.write_long`.
    """
    if frame is None or frame.empty:
        return pd.DataFrame(columns=[_TS, _EQUIP, _CLASS, _ROLE, _VALUE])
    f = frame.copy()
    # always nanoseconds: pyarrow reads a whole store with one timestamp unit, so a frame written
    # at another resolution (a parquet source at ms) would change every facility's readback
    f.index = pd.DatetimeIndex(pd.to_datetime(f.index)).as_unit("ns")
    f.index.name = _TS
    long = f.reset_index().melt(id_vars=_TS, var_name=_ROLE, value_name=_VALUE)
    long[_ROLE] = long[_ROLE].map(_role_slug)
    long[_EQUIP] = equip
    long[_CLASS] = equip_class
    long = long.dropna(subset=[_VALUE])
    long[_VALUE] = long[_VALUE].astype("float64")
    return long[[_TS, _EQUIP, _CLASS, _ROLE, _VALUE]]


@dataclass(frozen=True)
class PointKey:
    """One stored series: which equipment, which role, at which facility."""

    facility_id: str
    equip: str
    role: str


class ParquetStore:
    """Read/write normalized point history as a partitioned Parquet dataset.

    ``lock_timeout`` is how long a write waits for another process's store lock (``None``: the
    default of :func:`camber.store._lock.store_lock` -- ``--lock-timeout``, else
    ``$CAMBER_LOCK_TIMEOUT``, else 30 s; ``0`` fails at once). See the module docstring.
    """

    def __init__(self, root: str, *, lock_timeout: float | None = None):
        self.root = root
        self.lock_timeout = lock_timeout
        # False only for a scratch store (an ingest's staging area, a workspace rollup) written
        # under a lock its caller already holds on the store it belongs to.
        self._self_lock = True

    def _registry(self) -> FacilityRegistry:
        return FacilityRegistry(self.root, lock_timeout=self.lock_timeout)

    def lock(self, timeout: float | None = None):
        """Context manager holding this store's single-writer lock for a whole operation.

        Re-entrant within a process: the write methods called inside it re-enter it instead of
        locking per call, so a bulk load takes the lock once. ``timeout`` defaults to the store's
        ``lock_timeout``. Raises ``StoreLocked`` (or ``PortfolioLocked`` for a workspace store)
        naming the holder if another process keeps it longer.
        """
        from ._lock import store_lock  # lazy: it imports the portfolio, which imports the store

        return store_lock(self.root, timeout=self.lock_timeout if timeout is None else timeout)

    def _write_lock(self, *, create: bool = True):
        """The lock a write method holds: :meth:`lock`, or nothing for a scratch store or (with
        ``create=False``, for a deletion) a store directory that does not exist yet."""
        if not self._self_lock or (not create and not os.path.isdir(self.root)):
            return _nullcontext()
        return self.lock()

    # ------------------------------------------------------------------ write
    def write_long(self, long: pd.DataFrame, *, facility_id: str, name=None, **meta) -> int:
        """Append a long-form frame (``role_frame_to_long`` shape) for one facility.

        ``facility_id`` must be a path-safe id (see :func:`camber.store.require_facility_id`);
        an unsafe id raises rather than silently corrupting the layout. Pass ``name=`` (and any
        keyword metadata) to record the facility's display name in the registry. Partitions by
        ``facility_id``/``year``/``month`` (0.95; year only before); each call writes new part
        files (a per-call basename counter avoids clobbering prior writes), so repeated calls
        accumulate. Returns rows written. Holds the store lock (see :meth:`lock`).
        """
        require_facility_id(facility_id)
        _require_labels(long)
        with self._write_lock():
            return self._write_long(long, facility_id=facility_id, name=name, **meta)

    def _write_long(self, long, *, facility_id: str, name=None, **meta) -> int:
        reg = self._registry()
        reg._guard_write(facility_id)  # tombstoned id / case-variant of a known id -> ValueError
        if name is not None or meta:
            reg.register(facility_id, name=name, **meta)
        if long is None or long.empty:
            return 0
        df = long.copy()
        df[_TS] = pd.to_datetime(df[_TS])
        df[_FACILITY] = facility_id
        df[_YEAR] = df[_TS].dt.year.astype("int32")
        df[_MONTH] = df[_TS].dt.month.astype("int32")
        table = _canonical_table(df)
        seq = self._next_seq(facility_id)
        # Written under _staging/ (unseen by readers: pyarrow skips "_" names) and renamed into
        # place file by file, so a concurrent reader never opens a part that is half written.
        stage = os.path.join(self.root, STAGING_DIR, f"append-{os.getpid()}-{uuid.uuid4().hex}")
        os.makedirs(stage)
        try:
            self._write_parts(table, stage, seq)
            for dirpath, _dirs, files in os.walk(stage):
                dst_dir = os.path.join(self.root, os.path.relpath(dirpath, stage))
                for fname in sorted(files):
                    os.makedirs(dst_dir, exist_ok=True)
                    os.replace(os.path.join(dirpath, fname), os.path.join(dst_dir, fname))
        finally:
            shutil.rmtree(stage, ignore_errors=True)
            try:
                os.rmdir(os.path.dirname(stage))  # an empty _staging/ is left by nothing
            except OSError:
                pass  # an ingest's scratch is in it
        # Invalidate the cached catalog (cheap, O(1)); the next points() rebuilds it once.
        # (Merging keys into the catalog on every write would be O(n^2) over a bulk load.)
        self._invalidate_catalog()
        return len(df)

    @staticmethod
    def _write_parts(table, root, seq) -> None:
        # Single-threaded (so batches keep their order), one row group per part: the default
        # threaded write lands batches in completion order, so the same rows would give different
        # bytes (#130).
        ds.write_dataset(
            table,
            root,
            format="parquet",
            partitioning=[_FACILITY, _YEAR, _MONTH],
            partitioning_flavor="hive",
            existing_data_behavior="overwrite_or_ignore",
            basename_template=f"part-{seq}-{{i}}.parquet",
            file_options=ds.ParquetFileFormat().make_write_options(**_PARQUET_OPTIONS),
            use_threads=False,  # also keeps order; preserve_order is not in pyarrow 16
            min_rows_per_group=_ROWS_PER_GROUP,
            max_rows_per_group=_ROWS_PER_GROUP,
        )

    def write_role_frame(
        self,
        frame: pd.DataFrame,
        *,
        facility_id: str,
        equip: str,
        equip_class: str = "",
        name=None,
        **meta,
    ) -> int:
        """Convenience: melt a wide role-frame and append it. Returns rows written."""
        return self.write_long(
            role_frame_to_long(frame, equip=equip, equip_class=equip_class),
            facility_id=facility_id,
            name=name,
            **meta,
        )

    def register_facility(self, facility_id: str, name=None, **meta) -> None:
        """Record a facility's display ``name``/metadata without writing data (under the lock)."""
        with self._write_lock():
            self._registry().register(facility_id, name=name, **meta)

    def facility_name(self, facility_id: str) -> str:
        """Display name for ``facility_id`` (falls back to the id when unregistered)."""
        return self._registry().name(facility_id)

    def facilities_meta(self) -> dict:
        """The whole facilities registry ``{facility_id: {name, ...}}``."""
        return self._registry().all()

    def _next_seq(self, facility_id: str) -> int:
        """Monotonic per-facility write counter, derived from existing part files.

        One past the highest ``part-<seq>-<i>`` number on disk (and at least the file count), so a
        prune that removes files can never make a later write reuse -- and overwrite -- the name of
        a file that is still there.
        """
        sdir = os.path.join(self.root, f"{_FACILITY}={facility_id}")
        n = 0
        top = -1
        for _dirpath, dirs, files in os.walk(sdir):
            dirs[:] = [d for d in dirs if not d.startswith(("_", "."))]
            for f in files:
                if f.endswith(".parquet"):
                    n += 1
                    m = _PART_RE.match(f)
                    if m:
                        top = max(top, int(m.group(1)))
        return max(n, top + 1)

    # --------------------------------------------------------------- catalog cache
    def _catalog_path(self) -> str:
        return os.path.join(self.root, _CATALOG)

    def _read_catalog_payload(self, *, validate: bool = True):
        """The parsed ``_catalog.json`` dict, or None when absent, corrupt or (``validate``) built
        under a generation a write has since replaced (#141)."""
        p = self._catalog_path()
        if not os.path.isfile(p):
            return None
        try:
            with open(p, encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            return None
        if not isinstance(data, dict):
            return None
        if validate and data.get("gen") != _read_gen(self.root):
            return None  # a stale write-back that landed after a writer's invalidation
        return data

    def _read_catalog(self):
        """Return cached :class:`PointKey` list, or None if there is no (valid) catalog."""
        data = self._read_catalog_payload()
        if data is None:
            return None
        try:
            return [
                PointKey(facility_id=k["facility_id"], equip=k["equip"], role=k["role"])
                for k in data["points"]
            ]
        except (KeyError, TypeError):
            return None  # treat a corrupt/old catalog as absent -> fall back to a scan

    def _write_catalog(self, keys, equipment=None, *, gen, check: bool = True) -> bool:
        """Atomically write the catalog (sorted, de-duped) as ``_catalog.json``, stamped with
        ``gen``: the generation read *before* the scan that produced ``keys``.

        ``equipment`` (``{facility_id: {equip: equip_class}}``) is cached alongside the point keys
        when known, so :meth:`equipment` needs no scan either. With ``check``, the catalog is
        written only if ``gen`` is still current just before the rename; a write that lands in the
        window after that check carries the old ``gen`` and is never served. Best effort: returns
        False (nothing cached) when skipped, inside a no-persist scope (0.110, #276) or when the
        store is not writable -- the caller already holds its result.
        """
        from .._nopersist import persisting

        if gen is _GEN_UNKNOWN or not persisting():  # 0.110 (#276): a no-persist reader
            return False
        rows = sorted({(k.facility_id, k.equip, k.role) for k in keys})
        payload: dict = {"points": [{"facility_id": s, "equip": e, "role": r} for s, e, r in rows]}
        if equipment is not None:
            payload["equipment"] = {
                fid: dict(sorted(eqs.items())) for fid, eqs in sorted(equipment.items())
            }
        if gen is not None:
            payload["gen"] = gen
        try:
            os.makedirs(self.root, exist_ok=True)
            return _replace_atomically(
                self.root,
                _CATALOG,
                json.dumps(payload),
                keep=(lambda: _read_gen(self.root) == gen) if check else (lambda: True),
            )
        except OSError:
            return False

    def _invalidate_catalog(self) -> None:
        """Mark the cached catalog stale (O(1)); the next :meth:`points` rebuilds it."""
        _invalidate_catalog_at(self.root)

    def _rebuild(self):
        """Scan and cache the catalog without the store lock; returns (keys, equipment). The
        generation is read before the scan, so a write that lands during it voids the result."""
        gen = _read_gen(self.root)
        keys, equipment = self._scan()
        self._write_catalog(keys, equipment, gen=gen)
        return keys, equipment

    def _scan(self):
        """A projected scan (no ts/value payload) -> (point keys, equipment classes), one facility
        partition at a time, so an ingest that replaces one facility's files fails (and retries)
        only that facility's part of the scan (#301), and the scan holds one facility's columns at
        a time (#293)."""
        keys: list = []
        equipment: dict = {}
        for f in self.facilities():
            k, eqs = self._facility_catalog(f)
            keys += k
            if eqs:
                equipment[f] = eqs
        return keys, equipment

    def _scan_facility(self, facility_id: str):
        """:meth:`_scan` of one facility's partition: ``(point keys, {equip: equip_class})``."""
        cols = [_FACILITY, _EQUIP, _CLASS, _ROLE]
        long = self.read_long(facility_id=facility_id, columns=cols)
        if long.empty:
            return [], {}
        uniq = long.drop_duplicates([_FACILITY, _EQUIP, _ROLE])
        del long
        keys = [
            PointKey(facility_id=f, equip=e, role=r)
            for f, e, r in zip(uniq[_FACILITY], uniq[_EQUIP], uniq[_ROLE])
        ]
        equipment: dict = {}
        for e, c in zip(uniq[_EQUIP], uniq[_CLASS]):
            if not equipment.get(e):  # first non-empty class recorded for this equip wins
                equipment[e] = "" if c is None or c != c else str(c)
        return keys, equipment

    def _facility_catalog(self, facility_id: str):
        """One facility's ``(point keys, {equip: equip_class})`` from a scan of its partition
        only, memoized in :data:`_FAC_CATALOG` until the partition changes (#293)."""
        fid = str(facility_id)
        if "/" in fid or os.sep in fid:  # not a partition name: no scoped scan, no memo
            return self._scan_facility(facility_id)
        fdir = os.path.join(os.path.abspath(self.root), f"{_FACILITY}={fid}")
        key = (os.path.abspath(self.root), fid)
        sig = _partition_signature(fdir)  # before the scan: a write during it voids the entry
        with _FRAG_LOCK:
            hit = _FAC_CATALOG.get(key)
            if hit is not None and sig and hit[0] == sig:
                _FAC_CATALOG.move_to_end(key)
                return list(hit[1]), dict(hit[2])
        keys, equipment = self._scan_facility(fid)
        if sig:
            with _FRAG_LOCK:
                _FAC_CATALOG[key] = (sig, tuple(keys), dict(equipment))
                _FAC_CATALOG.move_to_end(key)
                while len(_FAC_CATALOG) > _FAC_CATALOG_MAX:
                    _FAC_CATALOG.popitem(last=False)
        return list(keys), dict(equipment)

    def _scan_keys(self) -> list:
        """Distinct (facility_id, equip, role) keys via a projected scan (no ts/value payload)."""
        return self._scan()[0]

    def rebuild_catalog(self) -> int:
        """Rebuild the catalog from a projected scan (for stores predating it, or to resync
        after manual edits). Returns the number of distinct keys."""
        if os.path.isdir(self.root):
            # a fresh generation first: voids a catalog an unlocked manual edit left current
            _invalidate_catalog_at(self.root)
        keys, _ = self._rebuild()
        return len(keys)

    # ------------------------------------------------------------------- read
    def _dataset(self):
        return ds.dataset(self.root, format="parquet", partitioning="hive")

    def _scoped_dataset(self, facility_id):
        """A dataset over only ``facility_id``'s partition directory, its partition keys parsed
        from the store root (the same columns and types a full-dataset read gives);
        :data:`_NO_PARTITION` when the facility has no directory or no part file; ``None`` to
        read the full dataset. Listing one facility's files only means an ingest that replaces
        another facility's files cannot fail this read (#301)."""
        if not isinstance(facility_id, str) or "/" in facility_id or os.sep in facility_id:
            return None
        root = os.path.abspath(self.root)
        fdir = os.path.join(root, f"{_FACILITY}={facility_id}")
        if not os.path.isdir(fdir):
            return _NO_PARTITION
        # typed keys, not inferred from one facility's values: an id such as "123" stays a string
        keys = [pa.field(_FACILITY, pa.string()), pa.field(_YEAR, pa.int32())]
        if any(
            m.startswith(f"{_MONTH}=")
            for y in os.listdir(fdir)
            if y.startswith(f"{_YEAR}=") and os.path.isdir(os.path.join(fdir, y))
            for m in os.listdir(os.path.join(fdir, y))
        ):
            keys.append(pa.field(_MONTH, pa.int32()))
        dataset = ds.dataset(
            fdir,
            format="parquet",
            partitioning=ds.partitioning(pa.schema(keys), flavor="hive"),
            partition_base_dir=root,
        )
        return dataset if dataset.files else _NO_PARTITION

    def _facility_dataset(self, facility_id, equips):
        """A dataset over only the part files of ``facility_id`` that hold one of ``equips`` (in
        the facility dataset's file order and schema), or ``None`` to read without pruning.

        Filtering this dataset gives exactly the rows, order and types that filtering the full
        dataset does -- the files left out hold none of ``equips`` -- without opening every file of
        the facility on each equipment read (#35).
        """
        root = os.path.abspath(self.root)
        fdir = os.path.join(root, f"{_FACILITY}={facility_id}")
        if not os.path.isdir(fdir):
            return None
        key = (root, str(facility_id))
        sig = _partition_signature(fdir)
        with _FRAG_LOCK:
            hit = _FRAG_INDEX.get(key)
            if hit is not None and hit[0] == sig:
                _FRAG_INDEX.move_to_end(key)
                entry = hit[1]
            else:
                entry = None
        if entry is None:
            try:
                full = self._scoped_dataset(facility_id)
                if full is _NO_PARTITION:
                    return None
                full = self._dataset() if full is None else full
                frags = [
                    (f, _fragment_equips(f))
                    for f in full.get_fragments(filter=ds.field(_FACILITY) == facility_id)
                ]
            except Exception:  # noqa: BLE001 - the full-dataset read reports its own error
                return None
            entry = (full.schema, full.format, full.filesystem, frags)
            with _FRAG_LOCK:
                _FRAG_INDEX[key] = (sig, entry)
                _FRAG_INDEX.move_to_end(key)
                while len(_FRAG_INDEX) > _FRAG_INDEX_MAX:
                    _FRAG_INDEX.popitem(last=False)
        schema, fmt, fs, frags = entry
        want = set(equips)
        keep = [f for f, eqs in frags if eqs is None or (eqs & want)]
        return ds.FileSystemDataset(keep, schema=schema, format=fmt, filesystem=fs)

    @staticmethod
    def _build_filter(
        *, facility_id=None, equips=None, roles=None, start=None, end=None, months=False
    ):
        """Assemble a pyarrow dataset filter, pruning ``year`` (and ``month``) partitions from the
        ts range.

        Translating ``start``/``end`` into bounds on the ``year`` *partition* field (not just
        the ``ts`` data column) lets pyarrow skip whole year directories, so a one-month query
        across a multi-year store opens only the relevant year(s). With ``months`` (the dataset
        has month partitions) the bounds also skip month directories; a legacy year-only file
        (``month`` null) is never skipped by them.
        """
        filt = None

        def _and(expr):
            nonlocal filt
            filt = expr if filt is None else (filt & expr)

        if facility_id is not None:
            _and(ds.field(_FACILITY) == facility_id)
        if equips is not None:
            _and(ds.field(_EQUIP).isin(list(equips)))
        if roles is not None:
            _and(ds.field(_ROLE).isin([_role_slug(r) for r in roles]))
        if start is not None:
            ts = pd.Timestamp(start)
            _and(ds.field(_TS) >= ts)
            _and(ds.field(_YEAR) >= int(ts.year))  # partition prune
            if months:
                mo = ds.field(_MONTH)
                _and((ds.field(_YEAR) > int(ts.year)) | mo.is_null() | (mo >= int(ts.month)))
        if end is not None:
            ts = pd.Timestamp(end)
            _and(ds.field(_TS) <= ts)
            _and(ds.field(_YEAR) <= int(ts.year))  # partition prune
            if months:
                mo = ds.field(_MONTH)
                _and((ds.field(_YEAR) < int(ts.year)) | mo.is_null() | (mo <= int(ts.month)))
        return filt

    def read_long(
        self, *, facility_id=None, equips=None, roles=None, start=None, end=None, columns=None
    ) -> pd.DataFrame:
        """Tidy read with predicate pushdown. Returns long-form rows.

        ``equips`` is an iterable of equip ids; ``roles`` an iterable of
        :class:`Role` or slugs; ``start``/``end`` any pandas-parseable timestamps
        (inclusive). Any argument left None is unconstrained. ``columns`` restricts the
        columns read from Parquet (projection) -- pass only what you need at scale.
        """
        empty_cols = columns or [_TS, _EQUIP, _CLASS, _ROLE, _VALUE, _FACILITY, _YEAR]
        if not os.path.isdir(self.root):
            return pd.DataFrame(columns=empty_cols)
        if equips is not None:
            equips = list(equips)
        kw = dict(facility_id=facility_id, equips=equips, roles=roles, start=start, end=end)
        scope = facility_id if isinstance(facility_id, str) else None
        try:
            try:
                return self._read_long(empty_cols, columns, **kw)
            except FileNotFoundError:
                # a part file listed by this read was replaced before it was opened (a concurrent
                # ingest or prune): list again and retry once (#301)
                with _FRAG_LOCK:
                    _FRAG_INDEX.pop((os.path.abspath(self.root), str(facility_id)), None)
                return self._read_long(empty_cols, columns, **kw)
        except FileNotFoundError as e:
            raise StoreReadError(
                "a stored file went missing while it was read (most likely a concurrent ingest)",
                facility_id=scope,
                retryable=True,
                cause=e,
            ) from e
        except (OSError, pa.ArrowInvalid) as e:
            if isinstance(e, StoreReadError):
                raise
            raise StoreReadError(
                "a stored file is damaged or unreadable", facility_id=scope, cause=e
            ) from e

    def _read_long(self, empty_cols, columns, *, facility_id, equips, roles, start, end):
        dataset = None
        if equips is not None:
            if isinstance(facility_id, str) and all(isinstance(e, str) for e in equips):
                dataset = self._facility_dataset(facility_id, equips)
        if dataset is None:
            dataset = self._scoped_dataset(facility_id)
            if dataset is _NO_PARTITION:  # no such facility (or a writer just removed it)
                return pd.DataFrame(columns=empty_cols)
            dataset = self._dataset() if dataset is None else dataset
            if _FACILITY not in dataset.schema.names:  # no partition left (every facility dropped)
                return pd.DataFrame(columns=empty_cols)
        filt = self._build_filter(
            facility_id=facility_id,
            equips=equips,
            roles=roles,
            start=start,
            end=end,
            months=_MONTH in dataset.schema.names,
        )
        table = dataset.to_table(filter=filt, columns=columns)
        df = table.to_pandas()
        if not df.empty and _TS in df.columns:
            df = df.sort_values(_TS).reset_index(drop=True)
        return df

    def _ts_span(self, facility_id: str):
        """``(first, last)`` stored ``ts`` of one facility (``(None, None)`` when it holds none),
        from its part files' row-group statistics, so no column is read (0.111, #293). Falls back
        to a ``ts`` read (with its error handling) when a file has no usable statistics."""
        lo = hi = None
        try:
            dataset = self._scoped_dataset(facility_id)
            if dataset is _NO_PARTITION:
                return None, None
            if dataset is None:
                raise LookupError  # not a partition name: the plain read below
            for frag in dataset.get_fragments():
                md = frag.metadata
                col = md.schema.to_arrow_schema().get_field_index(_TS)
                if col < 0:
                    raise LookupError
                for i in range(md.num_row_groups):
                    rg = md.row_group(i)
                    if not rg.num_rows:
                        continue
                    st = rg.column(col).statistics
                    if st is None or not st.has_min_max:
                        raise LookupError
                    a, b = pd.Timestamp(st.min), pd.Timestamp(st.max)
                    lo = a if lo is None or a < lo else lo
                    hi = b if hi is None or b > hi else hi
        except Exception:  # noqa: BLE001 - no statistics or a damaged footer: read the column
            ts = self.read_long(facility_id=facility_id, columns=[_TS])
            if ts.empty or ts[_TS].isna().all():
                return None, None
            return pd.Timestamp(ts[_TS].min()), pd.Timestamp(ts[_TS].max())
        return lo, hi

    def read_role_frame(
        self,
        *,
        facility_id: str,
        equip: str,
        roles=None,
        start=None,
        end=None,
        resample: str | None = None,
    ) -> pd.DataFrame:
        """Read one equipment back as a wide, role-named frame (rule-ready).

        Columns are :class:`Role` members (mirroring ``resolve``); index is a
        sorted DatetimeIndex. ``resample`` is a pandas offset alias or None for the stored
        grid: each role takes the bin mean (a run status its duty, the share of the bin it was
        on), the warm-up/cool-down exclusion flags the max, as at ingest.
        """
        long = self.read_long(
            facility_id=facility_id,
            equips=[equip],
            roles=roles,
            start=start,
            end=end,
            columns=[_TS, _ROLE, _VALUE],
        )
        if long.empty:
            return pd.DataFrame()
        # Fast path: a plain pivot when each (ts, role) is unique; only fall back to the
        # (much slower) mean-aggregating pivot_table when the store holds duplicates.
        if long.duplicated([_TS, _ROLE]).any():
            wide = long.pivot_table(index=_TS, columns=_ROLE, values=_VALUE, aggfunc="mean")
        else:
            wide = long.pivot(index=_TS, columns=_ROLE, values=_VALUE)
        # nanoseconds whatever unit the store's files hold (CAMBER's rules assume it)
        wide.index = pd.DatetimeIndex(pd.to_datetime(wide.index)).as_unit("ns")
        wide = wide.sort_index()
        if resample:
            # the same per-role rule as ingest and resolve (0.104, #143): every role takes the
            # bin mean -- a run status its duty -- except the warm-up/cool-down exclusion flags,
            # which take the max
            from ..model.roles import _ANY_ON_ROLES

            flags = [c for c in wide.columns if c in {r.value for r in _ANY_ON_ROLES}]
            bins = wide.resample(resample)
            out = bins.mean()
            if flags:
                out[flags] = bins.max()[flags]
            wide = out
        # restore Role-typed columns where the slug is a known role
        slug_to_role = {r.value: r for r in Role}
        wide.columns = [slug_to_role.get(c, c) for c in wide.columns]
        wide.index.name = None
        # dataset provenance travels with the frame, so a report built from it shows the licence
        # (and the research-only banner) even when the caller passes no data_sources
        from .._provenance import PROVENANCE_ATTR, facility_provenance

        prov = facility_provenance(self.facilities_meta().get(facility_id, {}), facility_id)
        if prov:
            wide.attrs[PROVENANCE_ATTR] = [prov]
        return wide

    # --------------------------------------------------------------- catalog
    def points(self, *, facility_id=None) -> list:
        """Distinct stored series as :class:`PointKey` (facility_id, equip, role).

        Reads the cached catalog (``_catalog.json``) so enumerating a portfolio's points needs
        no partition scan. Writes *invalidate* the catalog (cheap), so the first call after a
        write burst rebuilds it once with a projected scan (facility/equip/role columns only -- no
        ts/value payload) and caches the result; subsequent calls are instant until the next
        write. A ``facility_id`` call on a stale catalog scans only that facility's partition
        (memoized in-process until it changes) and leaves the catalog to the next store-wide call,
        so its cost does not grow with the rest of the store (0.111, #293).
        """
        cached = self._read_catalog()
        if cached is None:
            if not os.path.isdir(self.root):
                return []
            if isinstance(facility_id, str):  # a stale catalog: scan this facility only (#293)
                return self._facility_catalog(facility_id)[0]
            cached, _ = self._rebuild()  # memoize until the next write invalidates it
        return [k for k in cached if facility_id is None or k.facility_id == facility_id]

    def equipment(self, *, facility_id=None) -> dict:
        """Stored equipment and its class: ``{facility_id: {equip: equip_class}}``.

        The class is the ``equip_class`` recorded at write time (what store-backed discovery,
        :func:`camber.resolve.discover_store`, filters on -- no filename-prefix inference). Served
        from the cached catalog like :meth:`points`; a catalog written before the equipment cache
        existed is rebuilt once. ``facility_id`` narrows the result to that one facility (an empty
        dict when it holds no data).
        """
        data = self._read_catalog_payload()
        equipment = data.get("equipment") if data is not None else None
        if not isinstance(equipment, dict):
            if not os.path.isdir(self.root):
                return {}
            if isinstance(facility_id, str):  # a stale catalog: scan this facility only (#293)
                eqs = self._facility_catalog(facility_id)[1]
                return {facility_id: eqs} if eqs else {}
            _, equipment = self._rebuild()
        if facility_id is not None:
            eqs = equipment.get(facility_id)
            return {facility_id: dict(eqs)} if eqs else {}
        return {fid: dict(eqs) for fid, eqs in equipment.items()}

    def facilities(self) -> list:
        """Distinct facility ids present in the store (the partition directories)."""
        if not os.path.isdir(self.root):
            return []
        return sorted(
            d.split("=", 1)[1] for d in os.listdir(self.root) if d.startswith(f"{_FACILITY}=")
        )

    def facility_state(self, facility_id: str) -> str:
        """Lifecycle state of ``facility_id`` (``"active"`` when unregistered; see
        :mod:`camber.portfolio`)."""
        return self._registry().state(facility_id)

    def active_facilities(self) -> list:
        """The facilities in the store that analyses should run on: those whose lifecycle state is
        ``active`` (an unregistered facility, or one registered before lifecycle states existed,
        counts as active). Suspended, provisioning, offboarding and archived facilities are left
        out -- see docs/PORTFOLIO.md."""
        meta = self._registry().all()
        return [
            f for f in self.facilities() if (meta.get(f) or {}).get("state", "active") == "active"
        ]

    @deprecated(since="0.10", remove_in="1.0", use="ParquetStore.facilities")
    def sites(self) -> list:
        """Deprecated alias for :meth:`facilities` (the key is a facility_id, not a site name)."""
        return self.facilities()

    # ------------------------------------------------------- rollup / retention
    def rollup(
        self, freq: str, *, facility_id=None, equips=None, roles=None, agg: str = "mean"
    ) -> pd.DataFrame:
        """Downsample stored history to ``freq`` per (facility_id, equip, role).

        Returns a long-form frame with ``ts`` bucketed to the period and ``value``
        aggregated by ``agg`` ("mean"/"sum"/"max"/"min"). Use for retention rollups
        (keep raw recent, coarse history long) and portfolio-scale reads.
        """
        long = self.read_long(facility_id=facility_id, equips=equips, roles=roles)
        if long.empty:
            return long
        long = long.copy()
        long[_TS] = (
            pd.to_datetime(long[_TS]).dt.floor("D")
            if freq == "D"
            else pd.to_datetime(long[_TS]).dt.to_period(freq).dt.start_time
        )
        grouped = (
            long.groupby([_FACILITY, _EQUIP, _CLASS, _ROLE, _TS])[_VALUE].agg(agg).reset_index()
        )
        return grouped.sort_values(_TS).reset_index(drop=True)

    def write_rollup(
        self, freq: str, dest: ParquetStore, *, agg: str = "mean", facility_id=None
    ) -> int:
        """Compute a rollup and write it to another store; returns rows written. Reads this store
        without a lock and holds ``dest``'s lock once for all its writes."""
        rolled = self.rollup(freq, facility_id=facility_id, agg=agg)
        if rolled.empty:
            return 0
        total = 0
        with dest._write_lock():
            for s, sub in rolled.groupby(_FACILITY):
                total += dest.write_long(sub.drop(columns=[_FACILITY]), facility_id=s)
        return total

    def prune(self, *, before_year: int, facility_id=None, reason=None) -> int:
        """Delete year partitions older than ``before_year`` (retention policy).

        Removes ``facility_id=*/year=Y`` directories with Y < ``before_year``, each by one atomic
        rename before removal (a crash never leaves a half-deleted year visible). Returns the
        number of year partitions removed. Month-level, policy-driven retention is
        ``camber retention apply`` (:mod:`camber.portfolio`). Holds the store lock.

        Inside a portfolio workspace (0.108, #250) it needs a ``reason``, refuses
        (:class:`~camber.portfolio.LifecycleError`, nothing deleted) when a facility it would
        prune is under a legal hold, and audits one ``store.prune`` line per facility pruned.
        """
        if not os.path.isdir(self.root):
            return 0
        with self._write_lock(create=False):
            doomed = self._prune_targets(before_year, facility_id)
            pf, reason = self._delete_gate(
                sorted({fid for fid, _ in doomed}), reason, "`ParquetStore.prune`"
            )
            removed = self._prune(before_year, facility_id)
            if pf is not None:
                for fid in sorted({f for f, _ in doomed}):
                    years = sorted(y for f, y in doomed if f == fid)
                    pf.audit(
                        "store.prune",
                        reason=reason,
                        facility_id=fid,
                        details={"before_year": int(before_year), "years": years},
                    )
            return removed

    def _delete_gate(self, fids, reason, by: str, what: str = "delete its data", states=False):
        """``(portfolio, reason)`` for a delete primitive: the workspace's hold, reason and audit
        rule (:func:`camber.datasets._lifecycle.store_delete_gate`); ``(None, reason)`` for a
        plain store or a scratch one."""
        if not self._self_lock:  # a scratch store (staging, rollups): its caller decides
            return None, reason
        from ..datasets._lifecycle import store_delete_gate  # lazy: datasets imports the store

        return store_delete_gate(self.root, fids, reason=reason, by=by, what=what, states=states)

    def _prune_targets(self, before_year: int, facility_id) -> list:
        """``[(facility_id, year)]`` of the year partitions :meth:`prune` would remove."""
        out = []
        fac_dirs = (
            [f"{_FACILITY}={facility_id}"]
            if facility_id is not None
            else [d for d in os.listdir(self.root) if d.startswith(f"{_FACILITY}=")]
        )
        for sd in fac_dirs:
            spath = os.path.join(self.root, sd)
            if not os.path.isdir(spath):
                continue
            for yd in os.listdir(spath):
                if not yd.startswith(f"{_YEAR}="):
                    continue
                try:
                    yr = int(yd.split("=", 1)[1])
                except ValueError:
                    continue
                if yr < before_year:
                    out.append((sd.split("=", 1)[1], yr))
        return sorted(out)

    def _prune(self, before_year: int, facility_id) -> int:
        from . import _swap

        removed = 0
        for fid, yr in self._prune_targets(before_year, facility_id):
            _swap.discard(os.path.join(self.root, f"{_FACILITY}={fid}", f"{_YEAR}={yr}"))
            removed += 1
        if removed:
            self._invalidate_catalog()  # next points() rebuilds from the remaining data
        return removed

    # ------------------------------------------------------- partitions (0.95)
    def partitions(self, *, facility_id=None) -> list:
        """Every stored partition: ``[{facility_id, year, month, path, files, rows, legacy}]``.

        ``month`` is ``None`` and ``legacy`` is ``True`` for a pre-0.95 year-only partition (part
        files directly under ``year=Y/``); a year directory holding both is listed twice. ``rows``
        comes from the Parquet footers (no data is read). Sorted by facility, year, month.
        """
        import pyarrow.parquet as pq

        def _rows(files):
            n = 0
            for f in files:
                try:
                    n += int(pq.ParquetFile(f).metadata.num_rows)
                except Exception:  # noqa: BLE001 - an unreadable file counts no rows
                    continue
            return n

        out = []
        fids = [facility_id] if facility_id is not None else self.facilities()
        for fid in fids:
            fdir = os.path.join(self.root, f"{_FACILITY}={fid}")
            if not os.path.isdir(fdir):
                continue
            for yd in sorted(os.listdir(fdir)):
                ypath = os.path.join(fdir, yd)
                if not yd.startswith(f"{_YEAR}=") or not os.path.isdir(ypath):
                    continue
                try:
                    year = int(yd.split("=", 1)[1])
                except ValueError:
                    continue
                names = sorted(os.listdir(ypath))
                legacy = [os.path.join(ypath, n) for n in names if n.endswith(".parquet")]
                if legacy:
                    out.append(
                        {
                            "facility_id": fid,
                            "year": year,
                            "month": None,
                            "path": ypath,
                            "files": len(legacy),
                            "rows": _rows(legacy),
                            "legacy": True,
                        }
                    )
                for md in names:
                    mpath = os.path.join(ypath, md)
                    if not md.startswith(f"{_MONTH}=") or not os.path.isdir(mpath):
                        continue
                    try:
                        month = int(md.split("=", 1)[1])
                    except ValueError:
                        continue
                    files = [
                        os.path.join(mpath, n)
                        for n in sorted(os.listdir(mpath))
                        if n.endswith(".parquet") and not n.startswith(("_", "."))
                    ]
                    out.append(
                        {
                            "facility_id": fid,
                            "year": year,
                            "month": month,
                            "path": mpath,
                            "files": len(files),
                            "rows": _rows(files),
                            "legacy": False,
                        }
                    )
        out.sort(key=lambda p: (p["facility_id"], p["year"], p["month"] or 0, p["legacy"]))
        return out

    def drop_partition(self, facility_id: str, year: int, month=None, *, reason=None) -> int:
        """Delete one partition crash-safely; returns the rows it held (0 if absent).

        ``month=None`` deletes the whole ``year=Y`` directory (every month in it, and any legacy
        files); otherwise only ``year=Y/month=M``. A low-level primitive with no policy: retention
        (``camber retention apply``) decides what to drop and rolls it up first.

        Inside a portfolio workspace (0.108, #250) it needs a ``reason``, is refused
        (:class:`~camber.portfolio.LifecycleError`) for a facility under a legal hold, and a
        partition it deletes is audited as ``store.drop_partition``.
        """
        require_facility_id(facility_id)
        with self._write_lock(create=False):
            pf, reason = self._delete_gate([facility_id], reason, "`ParquetStore.drop_partition`")
            rows = self._drop_partition(facility_id, year, month)
            if pf is not None and rows is not None:
                pf.audit(
                    "store.drop_partition",
                    reason=reason,
                    facility_id=facility_id,
                    details={"year": int(year), "month": month, "rows": rows},
                )
            return rows or 0

    def _drop_partition(self, facility_id: str, year: int, month=None):
        """:meth:`drop_partition` without the workspace rule (retention, which checks holds and
        audits itself, calls this): the rows deleted, or ``None`` when the partition is absent."""
        from . import _swap

        require_facility_id(facility_id)
        path = os.path.join(self.root, f"{_FACILITY}={facility_id}", f"{_YEAR}={int(year)}")
        if month is not None:
            path = os.path.join(path, f"{_MONTH}={int(month)}")
        with self._write_lock(create=False):
            if not os.path.isdir(path):
                return None
            try:
                rows = int(ds.dataset(path, format="parquet").count_rows())
            except (pa.ArrowInvalid, OSError):  # pragma: no cover - unreadable partition
                rows = 0
            _swap.discard(path)
            self._invalidate_catalog()
            from ..resolve import clear_store_cache

            clear_store_cache(self.root, facility_id)
            return rows

    def migrate_partitions(self, *, apply: bool = False, reason=None) -> dict:
        """Convert pre-0.95 year-only partitions to ``year=/month=`` partitions.

        A dry run (the default) lists each year partition holding legacy files with its rows.
        ``apply=True`` rebuilds each such year directory in a staging directory -- the legacy rows
        split by month, the year's existing month partitions carried over -- checks that the rows
        add up, and swaps it in (crash-safe: an interrupted migration leaves each year either as it
        was or fully migrated, and re-running finishes it). Each month file is written in the
        canonical part layout (rows sorted timestamp first; see docs/SCALE.md).
        ``apply`` holds the store lock (the workspace's inside a portfolio workspace, where it is
        also audited as ``store.migrate_partitions`` and needs a ``reason``). Idempotent. Returns
        ``{"store", "partitions": [...], "rows", "dry_run", "applied"}``.
        """
        from . import _swap
        from .facilities import _is_empty_store, _require_store_dir

        _require_store_dir(self.root)  # 0.107 (#215): a typo'd path is an error, nothing created
        # 0.108 (#233): an empty folder is an empty store -- nothing to migrate, no lock file
        locking = apply and not _is_empty_store(self.root)
        reg = self._registry()
        ws = reg._workspace()
        if apply and ws is not None and not (isinstance(reason, str) and reason.strip()):
            raise ValueError("a reason is required inside a portfolio workspace (it is audited)")
        with self._write_lock(create=False) if locking else _nullcontext():
            if locking:
                _swap.recover_tree(self.root, max_depth=3)
            todo = [p for p in self.partitions() if p["legacy"]]
            report = {
                "store": os.path.abspath(self.root),
                "partitions": [
                    {k: p[k] for k in ("facility_id", "year", "files", "rows")} for p in todo
                ],
                "rows": sum(p["rows"] for p in todo),
                "dry_run": not apply,
                "applied": False,
            }
            if not apply or not todo:
                return report
            for p in todo:
                self._migrate_year(p["path"], p["rows"])
                from ..resolve import clear_store_cache

                clear_store_cache(self.root, p["facility_id"])
            self._invalidate_catalog()
            report["applied"] = True
            if ws is not None:
                reg._audit(
                    "store.migrate_partitions",
                    reason=reason,
                    details={
                        "store": os.path.relpath(os.path.abspath(self.root), ws),
                        "partitions": len(todo),
                        "rows": report["rows"],
                    },
                )
            return report

    def verify(self, *, facility_id=None) -> dict:
        """Which part files are in the canonical layout (#136): read-only, takes no lock. See
        :func:`camber.store._normalize.verify` (``camber store verify``)."""
        from . import _normalize

        return _normalize.verify(self.root, facility_id=facility_id)

    def normalize(self, *, facility_id=None, apply: bool = False, reason=None) -> dict:
        """Rewrite non-canonical part files in the canonical layout, in place and content-identical
        (#136); a dry run unless ``apply``. See :func:`camber.store._normalize.normalize`
        (``camber store normalize``)."""
        from . import _normalize

        return _normalize.normalize(
            self.root,
            facility_id=facility_id,
            apply=apply,
            reason=reason,
            lock_timeout=self.lock_timeout,
        )

    def migrated_files(self, facility_id: str, year: int) -> dict:
        """``{legacy part name: sha256}`` of the year-only files ``migrate_partitions`` split into
        month partitions in ``facility_id``'s ``year=Y`` (``{}`` when none). Provisional (0.95)."""
        path = os.path.join(
            self.root, f"{_FACILITY}={facility_id}", f"{_YEAR}={int(year)}", _MIGRATED
        )
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            return {}
        files = doc.get("files") if isinstance(doc, dict) else None
        return dict(files) if isinstance(files, dict) else {}

    @staticmethod
    def _migrate_year(ypath: str, legacy_rows: int) -> None:
        """Rebuild one ``year=Y`` directory with its legacy files split into month partitions."""
        import pyarrow.compute as pc
        import pyarrow.parquet as pq

        from . import _swap

        stage = _swap.staging(ypath)
        names = sorted(os.listdir(ypath))
        written = 0
        before_months = 0
        migrated: dict = {}
        try:  # an earlier migration of this year (legacy files landed again since)
            with open(os.path.join(ypath, _MIGRATED), encoding="utf-8") as fh:
                migrated.update((json.load(fh) or {}).get("files") or {})
        except (OSError, ValueError, AttributeError):
            pass
        for md in names:  # carry the existing month partitions over (hard links where possible)
            src = os.path.join(ypath, md)
            if not md.startswith(f"{_MONTH}=") or not os.path.isdir(src):
                continue
            for dirpath, _dirs, files in os.walk(src):
                rel = os.path.relpath(dirpath, ypath)
                os.makedirs(os.path.join(stage, rel), exist_ok=True)
                for f in files:
                    a, b = os.path.join(dirpath, f), os.path.join(stage, rel, f)
                    try:
                        os.link(a, b)
                    except OSError:  # pragma: no cover - no hard links (some filesystems)
                        import shutil

                        shutil.copy2(a, b)
                    if f.endswith(".parquet"):
                        before_months += int(pq.ParquetFile(a).metadata.num_rows)
        for k, n in enumerate(n for n in names if n.endswith(".parquet")):
            src_sha = _sha256_file(os.path.join(ypath, n))
            table = _read_part_file(os.path.join(ypath, n))
            migrated[n] = src_sha
            if table.num_rows == 0:
                continue
            ts = table.column(_TS)
            # call_function, not pc.month / pc.equal: those are generated at import time, and the
            # pyarrow releases that ship type stubs (py.typed) do not declare them.
            months = pc.call_function("month", [ts])
            for mo in sorted(set(months.to_pylist())):
                part = table.filter(pc.call_function("equal", [months, mo]))
                mdir = os.path.join(stage, f"{_MONTH}={int(mo)}")
                os.makedirs(mdir, exist_ok=True)
                # Named by the source's content (unique across repeated migrations of one year),
                # and written to a temp name then renamed: the stage holds hard links to the
                # carried-over month files, and writing onto one would truncate the original.
                dst = os.path.join(mdir, f"part-legacy-{src_sha[:16]}-{k}.parquet")
                while os.path.exists(dst):  # pragma: no cover - identical content twice
                    dst = dst[: -len(".parquet")] + "x.parquet"
                _write_part(_canonicalize(part), dst + ".tmp")
                os.replace(dst + ".tmp", dst)
                written += part.num_rows
        if migrated:
            with open(os.path.join(stage, _MIGRATED), "w", encoding="utf-8") as fh:
                json.dump({"schema": 1, "files": dict(sorted(migrated.items()))}, fh, indent=1)
                fh.write("\n")
        after = int(ds.dataset(stage, format="parquet").count_rows()) if os.listdir(stage) else 0
        if written != legacy_rows or after != legacy_rows + before_months:
            _swap.recover(ypath)  # pragma: no cover - discard the stage; nothing was changed
            raise OSError(  # pragma: no cover
                f"partition migration of {ypath} does not add up ({written} of {legacy_rows} "
                "legacy rows); nothing was changed"
            )
        _swap.commit(ypath)

    def drop_facility(self, facility_id: str, *, forget: bool = False, reason=None) -> int:
        """Hard-delete every stored row of ``facility_id``; returns the number of rows removed.

        **Irreversible.** This is a narrow, low-level primitive: it removes the facility's
        ``facility_id=<id>`` partition directory from disk, invalidates the point catalog and clears
        :mod:`camber.resolve`'s per-equipment frame cache -- nothing else. It carries no policy (no
        archiving, no soft delete, no retention rules); it is the building block a higher-level
        facility-lifecycle layer calls once that layer has decided a hard delete is wanted.

        The store is otherwise append-only, so this is also how a facility's history is *replaced*
        (re-ingesting without it would silently duplicate rows, which the role-frame read then
        mean-aggregates). The facility's registry entry (display name + metadata) is kept unless
        ``forget=True``. An unknown facility is a no-op returning 0.

        Inside a portfolio workspace (0.108, #250) it needs a ``reason``, is refused
        (:class:`~camber.portfolio.LifecycleError`, nothing deleted) for a facility under a legal
        hold or one that is not ``provisioning`` / ``active`` (an archived facility goes through
        ``camber facility purge``), and is audited as ``store.drop_facility`` (plus
        ``facility.remove`` with the same reason when ``forget``). The lifecycle's own deletions
        (``camber facility archive|purge``) do not come through here.
        """
        require_facility_id(facility_id)
        with self._write_lock(create=forget):
            pf, reason = self._delete_gate(
                [facility_id], reason, "`ParquetStore.drop_facility`", states=True
            )
            rows = self._drop_facility(
                facility_id, forget, reason=reason or "drop_facility(forget=True)"
            )
            if pf is not None:
                pf.audit(
                    "store.drop_facility",
                    reason=reason,
                    facility_id=facility_id,
                    details={"rows": rows, "forget": bool(forget)},
                )
            return rows

    def _drop_facility(
        self, facility_id: str, forget: bool, *, reason: str = "drop_facility(forget=True)"
    ) -> int:
        """:meth:`drop_facility` without the workspace rule: for callers that have checked the
        lifecycle and audit the deletion themselves (``camber datasets remove|ingest``), holding
        the store lock."""
        fdir = os.path.join(self.root, f"{_FACILITY}={facility_id}")
        rows = 0
        if os.path.isdir(fdir):
            try:
                rows = int(ds.dataset(fdir, format="parquet").count_rows())
            except (pa.ArrowInvalid, OSError):  # pragma: no cover - unreadable partition
                rows = 0
            from . import _swap

            _swap.discard(fdir)  # one atomic rename, then removal: never half-deleted
            self._invalidate_catalog()
        if forget:  # tombstones the id: a forgotten facility id is never reused
            self._registry()._forget(facility_id, had_data=rows > 0, reason=reason)
        from ..resolve import clear_store_cache  # lazy: resolve imports the store, not vice versa

        clear_store_cache(self.root, facility_id)
        return rows
