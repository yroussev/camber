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

Dependencies: pandas + pyarrow only (no SQL engine), consistent with the rest of
the package.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections import OrderedDict
from contextlib import nullcontext as _nullcontext
from dataclasses import dataclass

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds

from .._deprecation import deprecated
from ..model.roles import Role
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


def _sha256_file(path: str) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _partition_signature(fdir: str) -> tuple:
    """mtimes and file counts of a facility partition, its year dirs and their month dirs (changes
    on any write, prune or drop, whichever layout the partition uses)."""
    try:
        sig: list = [os.stat(fdir).st_mtime_ns]
        for d in sorted(os.listdir(fdir)):
            p = os.path.join(fdir, d)
            sig.append((d, os.stat(p).st_mtime_ns))
            if not os.path.isdir(p):
                sig.append(0)
                continue
            names = sorted(os.listdir(p))
            sig.append(len(names))
            for m in names:
                if m.startswith(f"{_MONTH}="):
                    mp = os.path.join(p, m)
                    sig.append((m, os.stat(mp).st_mtime_ns, len(os.listdir(mp))))
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
    """Read/write normalized point history as a partitioned Parquet dataset."""

    def __init__(self, root: str):
        self.root = root

    def _registry(self) -> FacilityRegistry:
        return FacilityRegistry(self.root)

    # ------------------------------------------------------------------ write
    def write_long(self, long: pd.DataFrame, *, facility_id: str, name=None, **meta) -> int:
        """Append a long-form frame (``role_frame_to_long`` shape) for one facility.

        ``facility_id`` must be a path-safe id (see :func:`camber.store.require_facility_id`);
        an unsafe id raises rather than silently corrupting the layout. Pass ``name=`` (and any
        keyword metadata) to record the facility's display name in the registry. Partitions by
        ``facility_id``/``year``/``month`` (0.95; year only before); each call writes new part
        files (a per-call basename counter avoids clobbering prior writes), so repeated calls
        accumulate. Returns rows written.
        """
        require_facility_id(facility_id)
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
        table = pa.Table.from_pandas(df, preserve_index=False)
        seq = self._next_seq(facility_id)
        ds.write_dataset(
            table,
            self.root,
            format="parquet",
            partitioning=[_FACILITY, _YEAR, _MONTH],
            partitioning_flavor="hive",
            existing_data_behavior="overwrite_or_ignore",
            basename_template=f"part-{seq}-{{i}}.parquet",
        )
        # Invalidate the cached catalog (cheap, O(1)); the next points() rebuilds it once.
        # (Merging keys into the catalog on every write would be O(n^2) over a bulk load.)
        self._invalidate_catalog()
        return len(df)

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
        """Record a facility's display ``name``/metadata without writing data."""
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

    def _read_catalog_payload(self):
        """The parsed ``_catalog.json`` dict, or None when absent/corrupt."""
        p = self._catalog_path()
        if not os.path.isfile(p):
            return None
        try:
            with open(p, encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            return None
        return data if isinstance(data, dict) else None

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

    def _write_catalog(self, keys, equipment=None) -> None:
        """Atomically write the catalog (sorted, de-duped) as ``_catalog.json``.

        ``equipment`` (``{facility_id: {equip: equip_class}}``) is cached alongside the point keys
        when known, so :meth:`equipment` needs no scan either.
        """
        os.makedirs(self.root, exist_ok=True)
        rows = sorted({(k.facility_id, k.equip, k.role) for k in keys})
        payload: dict = {"points": [{"facility_id": s, "equip": e, "role": r} for s, e, r in rows]}
        if equipment is not None:
            payload["equipment"] = {
                fid: dict(sorted(eqs.items())) for fid, eqs in sorted(equipment.items())
            }
        tmp = self._catalog_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, self._catalog_path())

    def _invalidate_catalog(self) -> None:
        """Mark the cached catalog stale (O(1)); the next :meth:`points` rebuilds it."""
        p = self._catalog_path()
        if os.path.isfile(p):
            try:
                os.remove(p)
            except OSError:
                pass

    def _scan(self):
        """One projected scan (no ts/value payload) -> (point keys, equipment classes)."""
        long = self.read_long(columns=[_FACILITY, _EQUIP, _CLASS, _ROLE])
        if long.empty:
            return [], {}
        uniq = long.drop_duplicates([_FACILITY, _EQUIP, _ROLE])
        keys = [
            PointKey(facility_id=f, equip=e, role=r)
            for f, e, r in zip(uniq[_FACILITY], uniq[_EQUIP], uniq[_ROLE])
        ]
        equipment: dict = {}
        for f, e, c in zip(uniq[_FACILITY], uniq[_EQUIP], uniq[_CLASS]):
            eqs = equipment.setdefault(str(f), {})
            if not eqs.get(e):  # first non-empty class recorded for this equip wins
                eqs[e] = "" if c is None or c != c else str(c)
        return keys, equipment

    def _scan_keys(self) -> list:
        """Distinct (facility_id, equip, role) keys via a projected scan (no ts/value payload)."""
        return self._scan()[0]

    def rebuild_catalog(self) -> int:
        """Rebuild the catalog from a projected scan (for stores predating it, or to resync
        after manual edits). Returns the number of distinct keys."""
        keys, equipment = self._scan()
        self._write_catalog(keys, equipment)
        return len(keys)

    # ------------------------------------------------------------------- read
    def _dataset(self):
        return ds.dataset(self.root, format="parquet", partitioning="hive")

    def _facility_dataset(self, facility_id, equips):
        """A dataset over only the part files of ``facility_id`` that hold one of ``equips`` (in
        the full dataset's file order and schema), or ``None`` to read the full dataset.

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
                full = self._dataset()
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
        dataset = None
        if equips is not None:
            equips = list(equips)
            if isinstance(facility_id, str) and all(isinstance(e, str) for e in equips):
                dataset = self._facility_dataset(facility_id, equips)
        if dataset is None:
            dataset = self._dataset()
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
        sorted DatetimeIndex. ``resample`` is a pandas offset alias
        (mean-aggregated) or None for the stored grid.
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
            wide = wide.resample(resample).mean()
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
        write.
        """
        cached = self._read_catalog()
        if cached is None:
            if not os.path.isdir(self.root):
                return []
            cached, equipment = self._scan()
            self._write_catalog(cached, equipment)  # memoize until the next write invalidates it
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
            keys, equipment = self._scan()
            self._write_catalog(keys, equipment)
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
        """Compute a rollup and write it to another store; returns rows written."""
        rolled = self.rollup(freq, facility_id=facility_id, agg=agg)
        if rolled.empty:
            return 0
        total = 0
        for s, sub in rolled.groupby(_FACILITY):
            total += dest.write_long(sub.drop(columns=[_FACILITY]), facility_id=s)
        return total

    def prune(self, *, before_year: int, facility_id=None) -> int:
        """Delete year partitions older than ``before_year`` (retention policy).

        Removes ``facility_id=*/year=Y`` directories with Y < ``before_year``, each by one atomic
        rename before removal (a crash never leaves a half-deleted year visible). Returns the
        number of year partitions removed. Month-level, policy-driven retention is
        ``camber retention apply`` (:mod:`camber.portfolio`).
        """
        from . import _swap

        if not os.path.isdir(self.root):
            return 0
        removed = 0
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
                    _swap.discard(os.path.join(spath, yd))
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

    def drop_partition(self, facility_id: str, year: int, month=None) -> int:
        """Delete one partition crash-safely; returns the rows it held (0 if absent).

        ``month=None`` deletes the whole ``year=Y`` directory (every month in it, and any legacy
        files); otherwise only ``year=Y/month=M``. A low-level primitive with no policy: retention
        (``camber retention apply``) decides what to drop and rolls it up first.
        """
        from . import _swap

        require_facility_id(facility_id)
        path = os.path.join(self.root, f"{_FACILITY}={facility_id}", f"{_YEAR}={int(year)}")
        if month is not None:
            path = os.path.join(path, f"{_MONTH}={int(month)}")
        if not os.path.isdir(path):
            return 0
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
        was or fully migrated, and re-running finishes it). Row order within a month is kept.
        Inside a portfolio workspace ``apply`` takes the lock and is audited
        (``store.migrate_partitions``) and needs a ``reason``. Idempotent. Returns
        ``{"store", "partitions": [...], "rows", "dry_run", "applied"}``.
        """
        from . import _swap

        reg = self._registry()
        ws = reg._workspace()
        if apply and ws is not None and not (isinstance(reason, str) and reason.strip()):
            raise ValueError("a reason is required inside a portfolio workspace (it is audited)")
        with reg._locked() if apply else _nullcontext():
            if apply:
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
            table = pq.read_table(os.path.join(ypath, n))
            migrated[n] = src_sha
            if table.num_rows == 0:
                continue
            ts = table.column(_TS)
            months = pc.month(ts)
            for mo in sorted(set(months.to_pylist())):
                part = table.filter(pc.equal(months, mo))
                mdir = os.path.join(stage, f"{_MONTH}={int(mo)}")
                os.makedirs(mdir, exist_ok=True)
                # Named by the source's content (unique across repeated migrations of one year),
                # and written to a temp name then renamed: the stage holds hard links to the
                # carried-over month files, and writing onto one would truncate the original.
                dst = os.path.join(mdir, f"part-legacy-{src_sha[:16]}-{k}.parquet")
                while os.path.exists(dst):  # pragma: no cover - identical content twice
                    dst = dst[: -len(".parquet")] + "x.parquet"
                pq.write_table(part, dst + ".tmp")
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

    def drop_facility(self, facility_id: str, *, forget: bool = False) -> int:
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
        """
        require_facility_id(facility_id)
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
            self._registry()._forget(
                facility_id, had_data=rows > 0, reason="drop_facility(forget=True)"
            )
        from ..resolve import clear_store_cache  # lazy: resolve imports the store, not vice versa

        clear_store_cache(self.root, facility_id)
        return rows
