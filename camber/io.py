"""Data loading and alignment for trend-log CSVs.

CSV contract: one timestamp column + one column per point, each column named
``<prefix><id>_<measure>`` or ``Bldg*_<measure>``.
"""

from __future__ import annotations

import pandas as pd

__all__ = [
    "load_csv",
    "add_oat_band",
]


def load_csv(
    path,
    timestamp_col: str | None = None,
    resample: str | None = None,
    dedupe: str = "first",
    *,
    profile=None,
    encoding: str | None = None,
    delimiter: str | None = None,
    skiprows: int | None = None,
    decimal: str | None = None,
    thousands: str | None = None,
    dayfirst: bool | None = None,
):
    """Load a trend CSV into a DataFrame indexed by a parsed DatetimeIndex.

    Parameters
    ----------
    path : str
        CSV file path.
    timestamp_col : str, optional
        Name of the timestamp column. If None, the profile's ``ts_col`` or the first column is used.
    resample : str, optional
        Pandas offset alias (e.g. "15min", "1h") to resample numeric columns to (mean).
        None = native.
    dedupe : str, optional
        Collapse duplicate timestamps: ``"first"`` (default) / ``"last"`` / ``"mean"`` / ``None``.
    profile : str | IngestProfile, optional
        A vendor ingest profile (name or object) supplying the delimiter/encoding/skiprows/timestamp
        format/decimal conventions — see :mod:`camber.ingest.profiles`. Individual keyword args
        below override the profile. Defaults resolve to the ``generic`` profile (fully backward
        compatible).
    """
    from .coerce import coerce_numeric
    from .ingest.profiles import get_profile
    from .timegrid import regularize
    from .tsparse import parse_timestamps

    p = get_profile(profile)
    enc = encoding or p.encoding
    sep = delimiter or p.delimiter
    skip = skiprows if skiprows is not None else p.skiprows
    dec = decimal or p.decimal
    tho = thousands if thousands is not None else p.thousands
    dfst = dayfirst if dayfirst is not None else p.dayfirst
    fmts = [p.ts_format] if p.ts_format else None

    try:
        df = pd.read_csv(path, encoding=enc, sep=sep, skiprows=skip)
    except pd.errors.EmptyDataError as e:
        raise ValueError(f"empty CSV (no header/columns): {path}") from e
    if df.shape[1] == 0:
        raise ValueError(f"CSV has no columns: {path}")
    if timestamp_col is None:
        timestamp_col = p.ts_col if p.ts_col is not None else df.columns[0]
    if timestamp_col not in df.columns:
        raise ValueError(f"timestamp column {timestamp_col!r} not found in {list(df.columns)}")

    # Parse timestamps leniently via the shared multi-format parser: unparseable rows become NaT and
    # are dropped (one bad row must not sink the whole load). If the file had rows but NONE parsed,
    # that's a real error, not empty data.
    ts = pd.Series(parse_timestamps(df[timestamp_col], formats=fmts, dayfirst=dfst), index=df.index)
    n_rows = len(ts)
    values = df.drop(columns=[timestamp_col])
    # Value columns are numeric by contract; coerce (thousands/decimal/null-token aware) so a stray
    # text cell becomes NaN instead of silently poisoning the whole column to object dtype.
    values = values.apply(lambda c: coerce_numeric(c, thousands=tho, decimal=dec))
    values.index = ts
    values = values[values.index.notna()]
    if n_rows > 0 and len(values) == 0:
        raise ValueError(f"no parseable timestamps in column {timestamp_col!r}")

    df = regularize(values, dedupe=dedupe)  # sort + collapse duplicate ts (handles empty)
    if resample and len(df):
        df = df.resample(resample).mean(numeric_only=True)
    return df


def add_oat_band(df, oat_col, cooling_cutoff_f: float = 65.0):
    """Return a boolean Series: is each interval in cooling season (OAT > cutoff)?

    PNNL Ch.7 uses ~65 F as the point above which reheat is clearly a fault.
    """
    return df[oat_col] > cooling_cutoff_f
