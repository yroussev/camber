"""Long/tall CSV adapter: one row per (timestamp, point, value) — the historian export shape.

Many historians and BAS "export all points" tools emit a **narrow** table
(``timestamp,point,value``, optionally ``unit``) rather than a wide one. This pivots it to the
standard `SourceAdapter` wide, datetime-indexed, numeric frame, reusing the shared timestamp/value
parsers and honoring a vendor :mod:`~camber.ingest.profiles` profile. (The SQL adapter already
does this from a DB connection; this is the CSV-file equivalent.)
"""

from __future__ import annotations

import pandas as pd

from ..coerce import coerce_numeric
from ..tsparse import parse_timestamps
from .profiles import get_profile


class LongCsvAdapter:
    """SourceAdapter over a long/narrow CSV (``timestamp,point,value[,unit]``)."""

    def __init__(
        self,
        path: str,
        *,
        ts_col: str = "timestamp",
        point_col: str = "point",
        value_col: str = "value",
        unit_col: str | None = "unit",
        profile=None,
    ):
        self.path = path
        self.ts_col, self.point_col, self.value_col, self.unit_col = (
            ts_col,
            point_col,
            value_col,
            unit_col,
        )
        self.profile = profile
        self._cache = None

    def _pivot(self):
        if self._cache is not None:
            return self._cache
        p = get_profile(self.profile)
        df = pd.read_csv(self.path, encoding=p.encoding, sep=p.delimiter, skiprows=p.skiprows)
        for col in (self.ts_col, self.point_col, self.value_col):
            if col not in df.columns:
                raise ValueError(f"column {col!r} not in {list(df.columns)}")
        idx = parse_timestamps(
            df[self.ts_col], formats=[p.ts_format] if p.ts_format else None, dayfirst=p.dayfirst
        )
        vals = coerce_numeric(df[self.value_col], thousands=p.thousands, decimal=p.decimal)
        frame = pd.DataFrame(
            {"point": df[self.point_col].astype(str).values, "value": vals.values}, index=idx
        )
        frame = frame[~frame.index.isna()]
        series, units = {}, {}
        for name, grp in frame.groupby("point", sort=True):
            s = pd.Series(grp["value"].values, index=grp.index)
            s = s[~s.index.duplicated(keep="first")].sort_index()
            s.name = str(name)
            series[str(name)] = s
        if self.unit_col and self.unit_col in df.columns:
            u = df[[self.point_col, self.unit_col]].dropna().astype(str)
            units = {
                r[self.point_col]: r[self.unit_col]
                for _, r in u.drop_duplicates(self.point_col).iterrows()
            }
        self._cache = (series, units)
        return self._cache

    def point_names(self):
        return sorted(self._pivot()[0])

    def load_points(self, names, resample: str | None = "1h") -> pd.DataFrame:
        series, _ = self._pivot()
        cols = {n: series[n] for n in names if n in series}
        if not cols:
            return pd.DataFrame()
        df = pd.concat(cols, axis=1)
        if resample and len(df):
            df = df.resample(resample).mean(numeric_only=True)
        return df

    def units(self) -> dict:
        return self._pivot()[1]
