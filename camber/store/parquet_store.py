"""A Parquet-backed time-series store keyed to the semantic entity model.

Layout: one tidy (long-form) dataset, hive-partitioned by ``site`` and ``year``,
so a portfolio of buildings lives under one root and a query touches only the
partitions it needs::

    <root>/site=DemoSite/year=2024/part-*.parquet

Each row is ``(ts, equip, equip_class, role, value)`` plus the ``site``/``year``
partition keys. Storing by *role* (the vendor-neutral meaning, see
:mod:`camber.model.roles`) rather than the raw vendor token means a query reads
the same column name on any building -- the store speaks the analytics layer's
language, not the BAS's.

Reads use pyarrow dataset filters (predicate pushdown on the partition keys and a
row filter on ts/equip/role), then pivot to the wide, role-named frames the rules
already consume -- so the store is a drop-in source behind ``resolve``-style data
without changing rule code.

Dependencies: pandas + pyarrow only (no SQL engine), consistent with the rest of
the package.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds

from ..model.roles import Role

# Stable column schema for the long-form store.
_TS = "ts"
_VALUE = "value"
_EQUIP = "equip"
_CLASS = "equip_class"
_ROLE = "role"
_SITE = "site"
_YEAR = "year"


def _role_slug(r) -> str:
    """Role enum or string -> its stable slug."""
    return r.value if isinstance(r, Role) else str(r)


def role_frame_to_long(frame: pd.DataFrame, *, site: str, equip: str,
                       equip_class: str = "") -> pd.DataFrame:
    """Melt a wide role-named frame (``resolve`` output) to the store's long form.

    ``frame`` has a DatetimeIndex and columns that are :class:`Role` members (or
    role slugs). NaNs are dropped -- the store holds observations, not a dense
    grid. Returns columns ``[ts, equip, equip_class, role, value]``.
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
    """One stored series: which equipment, which role, at which site."""

    site: str
    equip: str
    role: str


class ParquetStore:
    """Read/write normalized point history as a partitioned Parquet dataset."""

    def __init__(self, root: str):
        self.root = root

    # ------------------------------------------------------------------ write
    def write_long(self, long: pd.DataFrame, *, site: str) -> int:
        """Append a long-form frame (``role_frame_to_long`` shape) for one site.

        Partitions by ``site``/``year``. Each call writes new part files (a
        per-call basename counter avoids clobbering prior writes), so repeated
        calls accumulate. Returns the number of rows written.
        """
        if long is None or long.empty:
            return 0
        df = long.copy()
        df[_TS] = pd.to_datetime(df[_TS])
        df[_SITE] = site
        df[_YEAR] = df[_TS].dt.year.astype("int32")
        table = pa.Table.from_pandas(df, preserve_index=False)
        seq = self._next_seq(site)
        ds.write_dataset(
            table, self.root, format="parquet",
            partitioning=[_SITE, _YEAR], partitioning_flavor="hive",
            existing_data_behavior="overwrite_or_ignore",
            basename_template=f"part-{seq}-{{i}}.parquet",
        )
        return len(df)

    def write_role_frame(self, frame: pd.DataFrame, *, site: str, equip: str,
                         equip_class: str = "") -> int:
        """Convenience: melt a wide role-frame and append it. Returns rows written."""
        return self.write_long(
            role_frame_to_long(frame, site=site, equip=equip,
                               equip_class=equip_class),
            site=site)

    def _next_seq(self, site: str) -> int:
        """Monotonic per-site write counter, derived from existing part files."""
        sdir = os.path.join(self.root, f"{_SITE}={site}")
        n = 0
        for dirpath, _dirs, files in os.walk(sdir):
            n += sum(1 for f in files if f.endswith(".parquet"))
        return n

    # ------------------------------------------------------------------- read
    def _dataset(self):
        return ds.dataset(self.root, format="parquet", partitioning="hive")

    @staticmethod
    def _build_filter(*, site=None, equips=None, roles=None, start=None, end=None):
        """Assemble a pyarrow dataset filter, pruning ``year`` partitions from the ts range.

        Translating ``start``/``end`` into bounds on the ``year`` *partition* field (not just
        the ``ts`` data column) lets pyarrow skip whole year directories, so a one-month query
        across a multi-year store opens only the relevant year(s).
        """
        filt = None

        def _and(expr):
            nonlocal filt
            filt = expr if filt is None else (filt & expr)

        if site is not None:
            _and(ds.field(_SITE) == site)
        if equips is not None:
            _and(ds.field(_EQUIP).isin(list(equips)))
        if roles is not None:
            _and(ds.field(_ROLE).isin([_role_slug(r) for r in roles]))
        if start is not None:
            ts = pd.Timestamp(start)
            _and(ds.field(_TS) >= ts)
            _and(ds.field(_YEAR) >= int(ts.year))      # partition prune
        if end is not None:
            ts = pd.Timestamp(end)
            _and(ds.field(_TS) <= ts)
            _and(ds.field(_YEAR) <= int(ts.year))      # partition prune
        return filt

    def read_long(self, *, site=None, equips=None, roles=None,
                  start=None, end=None, columns=None) -> pd.DataFrame:
        """Tidy read with predicate pushdown. Returns long-form rows.

        ``equips`` is an iterable of equip ids; ``roles`` an iterable of
        :class:`Role` or slugs; ``start``/``end`` any pandas-parseable timestamps
        (inclusive). Any argument left None is unconstrained. ``columns`` restricts the
        columns read from Parquet (projection) -- pass only what you need at scale.
        """
        if not os.path.isdir(self.root):
            cols = columns or [_TS, _EQUIP, _CLASS, _ROLE, _VALUE, _SITE, _YEAR]
            return pd.DataFrame(columns=cols)
        dataset = self._dataset()
        filt = self._build_filter(site=site, equips=equips, roles=roles,
                                  start=start, end=end)
        table = dataset.to_table(filter=filt, columns=columns)
        df = table.to_pandas()
        if not df.empty and _TS in df.columns:
            df = df.sort_values(_TS).reset_index(drop=True)
        return df

    def read_role_frame(self, *, site: str, equip: str, roles=None,
                        start=None, end=None, resample: str | None = None
                        ) -> pd.DataFrame:
        """Read one equipment back as a wide, role-named frame (rule-ready).

        Columns are :class:`Role` members (mirroring ``resolve``); index is a
        sorted DatetimeIndex. ``resample`` is a pandas offset alias
        (mean-aggregated) or None for the stored grid.
        """
        long = self.read_long(site=site, equips=[equip], roles=roles,
                              start=start, end=end, columns=[_TS, _ROLE, _VALUE])
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
        return wide

    # --------------------------------------------------------------- catalog
    def points(self, *, site=None) -> list:
        """Distinct stored series as :class:`PointKey` (site, equip, role).

        Projects only the catalog columns (site/equip/role) out of Parquet -- it never reads
        the ts/value payload, so enumerating a large portfolio's points stays cheap.
        """
        long = self.read_long(site=site, columns=[_SITE, _EQUIP, _ROLE])
        if long.empty:
            return []
        keys = long.drop_duplicates([_SITE, _EQUIP, _ROLE])
        return [PointKey(site=r[_SITE], equip=r[_EQUIP], role=r[_ROLE])
                for _, r in keys.iterrows()]

    def sites(self) -> list:
        """Distinct site ids present in the store."""
        if not os.path.isdir(self.root):
            return []
        return sorted(d.split("=", 1)[1] for d in os.listdir(self.root)
                      if d.startswith(f"{_SITE}="))

    # ------------------------------------------------------- rollup / retention
    def rollup(self, freq: str, *, site=None, equips=None, roles=None,
               agg: str = "mean") -> pd.DataFrame:
        """Downsample stored history to ``freq`` per (site, equip, role).

        Returns a long-form frame with ``ts`` bucketed to the period and ``value``
        aggregated by ``agg`` ("mean"/"sum"/"max"/"min"). Use for retention rollups
        (keep raw recent, coarse history long) and portfolio-scale reads.
        """
        long = self.read_long(site=site, equips=equips, roles=roles)
        if long.empty:
            return long
        long = long.copy()
        long[_TS] = pd.to_datetime(long[_TS]).dt.floor("D") if freq == "D" \
            else pd.to_datetime(long[_TS]).dt.to_period(freq).dt.start_time
        grouped = (long.groupby([_SITE, _EQUIP, _CLASS, _ROLE, _TS])[_VALUE]
                   .agg(agg).reset_index())
        return grouped.sort_values(_TS).reset_index(drop=True)

    def write_rollup(self, freq: str, dest: "ParquetStore", *, agg: str = "mean",
                     site=None) -> int:
        """Compute a rollup and write it to another store; returns rows written."""
        rolled = self.rollup(freq, site=site, agg=agg)
        if rolled.empty:
            return 0
        total = 0
        for s, sub in rolled.groupby(_SITE):
            total += dest.write_long(sub.drop(columns=[_SITE]), site=s)
        return total

    def prune(self, *, before_year: int, site=None) -> int:
        """Delete year partitions older than ``before_year`` (retention policy).

        Removes ``site=*/year=Y`` directories with Y < ``before_year``. Returns the
        number of year partitions removed.
        """
        if not os.path.isdir(self.root):
            return 0
        removed = 0
        site_dirs = ([f"{_SITE}={site}"] if site is not None
                     else [d for d in os.listdir(self.root)
                           if d.startswith(f"{_SITE}=")])
        for sd in site_dirs:
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
        return removed
