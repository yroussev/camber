"""SQL/historian adapter: a long/narrow point table from any DB-API connection.

Most time-series historians (and many SQL exports of one) store data in a
long/narrow shape -- one row per (timestamp, point, value), optionally with a
unit column -- rather than a wide one-column-per-point frame. This adapter reads
such a table over any :pep:`249` (Python DB-API 2.0) connection (tested with the
stdlib :mod:`sqlite3`), so the layers above never see the source.

The column names for timestamp, point, value, and unit are configurable, and an
optional ``where`` clause narrows the rows. Rows are grouped by point into one
time-indexed Series each (sorted DatetimeIndex), matching the shape produced by
:func:`camber.realio.load_point`.

References:
- PEP 249 -- Python Database API Specification v2.0 (the ``connection``/cursor
  contract this relies on).
"""

from __future__ import annotations

import pandas as pd


def _quote_ident(name: str) -> str:
    """Quote a SQL identifier with double quotes (SQL-standard), escaping any.

    Column/table names here come from the caller (not untrusted input), but we
    still quote so names with mixed case or odd characters survive, and reject
    embedded NUL.
    """
    if "\x00" in name:
        raise ValueError(f"invalid SQL identifier: {name!r}")
    return '"' + name.replace('"', '""') + '"'


def read_points(connection, table, *, ts_col, point_col, value_col,
                unit_col=None, where=None) -> dict[str, pd.Series]:
    """Read a long/narrow point table into ``{point name -> Series}``.

    ``connection`` is any DB-API 2.0 connection (e.g. ``sqlite3.connect(...)``).
    The table has a timestamp column ``ts_col``, a point-name column
    ``point_col`` and a value column ``value_col``; ``unit_col`` (optional) is
    read only by :class:`SqlSource` for :meth:`SqlSource.units`. ``where`` is an
    optional SQL boolean expression (no leading ``WHERE``) to narrow the rows.

    Each returned Series has a sorted :class:`~pandas.DatetimeIndex`, numeric
    values, and ``name`` set to the point name; unparseable timestamps and
    duplicate timestamps (first kept) are dropped, matching
    :func:`camber.realio.load_point`.
    """
    cols = [_quote_ident(ts_col), _quote_ident(point_col),
            _quote_ident(value_col)]
    sql = f"SELECT {', '.join(cols)} FROM {_quote_ident(table)}"
    if where:
        sql += f" WHERE {where}"
    cur = connection.cursor()
    try:
        cur.execute(sql)
        rows = cur.fetchall()
    finally:
        cur.close()
    if not rows:
        return {}

    df = pd.DataFrame(rows, columns=["ts", "point", "value"])
    idx = pd.DatetimeIndex(pd.to_datetime(df["ts"], errors="coerce").values)
    vals = pd.to_numeric(df["value"], errors="coerce")
    df = pd.DataFrame({"point": df["point"].values, "value": vals.values},
                      index=idx)
    df = df[~df.index.isna()]

    out: dict[str, pd.Series] = {}
    for name, grp in df.groupby("point", sort=True):
        s = pd.Series(grp["value"].values, index=grp.index)
        s = s[~s.index.duplicated(keep="first")].sort_index()
        s.name = str(name)
        out[str(name)] = s
    return out


class SqlSource:
    """SourceAdapter over a long/narrow historian table on a DB-API connection.

    The ``connection`` is held but never closed by this adapter (the caller owns
    its lifecycle). Point series are read lazily and cached on first use.
    """

    def __init__(self, connection, table, *, ts_col, point_col, value_col,
                 unit_col=None, where=None):
        self.connection = connection
        self.table = table
        self.ts_col = ts_col
        self.point_col = point_col
        self.value_col = value_col
        self.unit_col = unit_col
        self.where = where
        self._points: dict[str, pd.Series] | None = None
        self._units: dict[str, str] | None = None

    def _load(self) -> dict[str, pd.Series]:
        if self._points is None:
            self._points = read_points(
                self.connection, self.table, ts_col=self.ts_col,
                point_col=self.point_col, value_col=self.value_col,
                where=self.where)
        return self._points

    def point_names(self):
        """Sorted point names present in the (filtered) table."""
        return sorted(self._load())

    def load_points(self, names, resample: str | None = "1h") -> pd.DataFrame:
        """Load the named points into a wide, optionally resampled frame."""
        pts = self._load()
        cols = {name: pts[name] for name in names if name in pts}
        if not cols:
            return pd.DataFrame()
        df = pd.concat(cols, axis=1)
        if resample:
            df = df.resample(resample).mean(numeric_only=True)
        return df

    def units(self) -> dict:
        """Map point name -> unit from ``unit_col`` (empty if no unit column)."""
        if self.unit_col is None:
            return {}
        if self._units is None:
            cols = [_quote_ident(self.point_col), _quote_ident(self.unit_col)]
            sql = f"SELECT {', '.join(cols)} FROM {_quote_ident(self.table)}"
            if self.where:
                sql += f" WHERE {self.where}"
            cur = self.connection.cursor()
            try:
                cur.execute(sql)
                rows = cur.fetchall()
            finally:
                cur.close()
            out: dict[str, str] = {}
            for name, unit in rows:
                # first non-null unit seen per point wins
                if unit is not None and str(name) not in out:
                    out[str(name)] = str(unit)
            self._units = out
        return self._units
