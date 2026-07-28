"""Data loading and alignment for trend-log CSVs.

CSV contract: one timestamp column + one column per point, each column named
``<prefix><id>_<measure>`` or ``Bldg*_<measure>``.
"""

from __future__ import annotations

import pandas as pd


def load_csv(path, timestamp_col: str | None = None, resample: str | None = None,
             dedupe: str = "first"):
    """Load a trend CSV into a DataFrame indexed by a parsed DatetimeIndex.

    Parameters
    ----------
    path : str
        CSV file path.
    timestamp_col : str, optional
        Name of the timestamp column. If None, the first column is used.
    resample : str, optional
        Pandas offset alias (e.g. "15min", "1h") to resample numeric columns to,
        using the mean. If None, data is left at native interval.
    dedupe : str, optional
        How to collapse duplicate timestamps (the DST fall-back hour, or concatenated overlapping
        exports): ``"first"`` (default) / ``"last"`` / ``"mean"``, or ``None`` to keep duplicates.
    """
    from .timegrid import regularize

    try:
        df = pd.read_csv(path)
    except pd.errors.EmptyDataError as e:
        raise ValueError(f"empty CSV (no header/columns): {path}") from e
    if df.shape[1] == 0:
        raise ValueError(f"CSV has no columns: {path}")
    if timestamp_col is None:
        timestamp_col = df.columns[0]
    if timestamp_col not in df.columns:
        raise ValueError(f"timestamp column {timestamp_col!r} not found in {list(df.columns)}")

    # Parse timestamps leniently via the shared multi-format parser: unparseable rows become NaT and
    # are dropped (one bad row must not sink the whole load). If the file had rows but NONE parsed,
    # that's a real error, not empty data.
    from .tsparse import parse_timestamps
    ts = pd.Series(parse_timestamps(df[timestamp_col]), index=df.index)
    n_rows = len(ts)
    values = df.drop(columns=[timestamp_col])
    # Value columns are numeric by contract; coerce (thousands/null-token aware) so a stray text cell
    # becomes NaN instead of silently poisoning the whole column to object dtype.
    from .coerce import coerce_numeric
    values = values.apply(coerce_numeric)
    values.index = ts
    values = values[values.index.notna()]
    if n_rows > 0 and len(values) == 0:
        raise ValueError(f"no parseable timestamps in column {timestamp_col!r}")

    df = regularize(values, dedupe=dedupe)   # sort + collapse duplicate ts (handles empty)
    if resample and len(df):
        df = df.resample(resample).mean(numeric_only=True)
    return df


def add_oat_band(df, oat_col, cooling_cutoff_f: float = 65.0):
    """Return a boolean Series: is each interval in cooling season (OAT > cutoff)?

    PNNL Ch.7 uses ~65 F as the point above which reheat is clearly a fault.
    """
    return df[oat_col] > cooling_cutoff_f
