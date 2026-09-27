"""A Parquet-backed time-series store keyed to the semantic entity model.

Layout: one tidy (long-form) dataset, hive-partitioned by ``facility_id`` and ``year``,
so a portfolio of buildings lives under one root and a query touches only the
partitions it needs::

    <root>/facility_id=fox-lodge-9f3a1c/year=2024/part-*.parquet

Each row is ``(ts, equip, equip_class, role, value)`` plus the ``facility_id``/``year``
partition keys. The ``facility_id`` is a stable, path-safe identifier (see
:mod:`camber.store.facilities`); a facility's human display name and metadata live in a
sibling ``_facilities.json`` registry, decoupled from the storage identity so a rename
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
import shutil
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

# A cached catalog of distinct (facility_id, equip, role) keys, written alongside the dataset so
# points() needn't rescan every partition. Arrow's dataset discovery ignores leading-"_"
# paths, so this file is invisible to reads.
_CATALOG = "_catalog.json"


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
    f.index = pd.to_datetime(f.index)
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
        ``facility_id``/``year``; each call writes new part files (a per-call basename counter
        avoids clobbering prior writes), so repeated calls accumulate. Returns rows written.
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
        table = pa.Table.from_pandas(df, preserve_index=False)
        seq = self._next_seq(facility_id)
        ds.write_dataset(
            table,
            self.root,
            format="parquet",
            partitioning=[_FACILITY, _YEAR],
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
        """Monotonic per-facility write counter, derived from existing part files."""
        sdir = os.path.join(self.root, f"{_FACILITY}={facility_id}")
        n = 0
        for _dirpath, _dirs, files in os.walk(sdir):
            n += sum(1 for f in files if f.endswith(".parquet"))
        return n

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

    @staticmethod
    def _build_filter(*, facility_id=None, equips=None, roles=None, start=None, end=None):
        """Assemble a pyarrow dataset filter, pruning ``year`` partitions from the ts range.

        Translating ``start``/``end`` into bounds on the ``year`` *partition* field (not just
        the ``ts`` data column) lets pyarrow skip whole year directories, so a one-month query
        across a multi-year store opens only the relevant year(s).
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
        if end is not None:
            ts = pd.Timestamp(end)
            _and(ds.field(_TS) <= ts)
            _and(ds.field(_YEAR) <= int(ts.year))  # partition prune
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
        if not os.path.isdir(self.root):
            cols = columns or [_TS, _EQUIP, _CLASS, _ROLE, _VALUE, _FACILITY, _YEAR]
            return pd.DataFrame(columns=cols)
        dataset = self._dataset()
        filt = self._build_filter(
            facility_id=facility_id, equips=equips, roles=roles, start=start, end=end
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
        wide.index = pd.to_datetime(wide.index)
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

        Removes ``facility_id=*/year=Y`` directories with Y < ``before_year``. Returns the
        number of year partitions removed.
        """
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
                    shutil.rmtree(os.path.join(spath, yd))
                    removed += 1
        if removed:
            self._invalidate_catalog()  # next points() rebuilds from the remaining data
        return removed

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
            shutil.rmtree(fdir)
            self._invalidate_catalog()
        if forget:  # tombstones the id: a forgotten facility id is never reused
            self._registry()._forget(
                facility_id, had_data=rows > 0, reason="drop_facility(forget=True)"
            )
        from ..resolve import clear_store_cache  # lazy: resolve imports the store, not vice versa

        clear_store_cache(self.root, facility_id)
        return rows
