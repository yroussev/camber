"""Data loading and alignment for trend-log CSVs.

CSV contract: one timestamp column + one column per point, each column named
``<prefix><id>_<measure>`` or ``Bldg*_<measure>``.
"""

from __future__ import annotations

import pandas as pd


def load_csv(path, timestamp_col: str | None = None, resample: str | None = None):
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
    """
    df = pd.read_csv(path)
    if timestamp_col is None:
        timestamp_col = df.columns[0]
    df[timestamp_col] = pd.to_datetime(df[timestamp_col])
    df = df.set_index(timestamp_col).sort_index()
    if resample:
        df = df.resample(resample).mean(numeric_only=True)
    return df


def add_oat_band(df, oat_col, cooling_cutoff_f: float = 65.0):
    """Return a boolean Series: is each interval in cooling season (OAT > cutoff)?

    PNNL Ch.7 uses ~65 F as the point above which reheat is clearly a fault.
    """
    return df[oat_col] > cooling_cutoff_f
