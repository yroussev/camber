"""Loader for per-point BAS trend exports (one CSV per point).

Common BAS "trend export" shape -- each file: header ``Timestamp,Value (<unit>)``
(often UTF-8 BOM), rows like ``21-Apr-23 8:30:03 AM PDT,0.0``. This module loads
selected points for a piece of equipment and joins them on a common time grid.
"""

from __future__ import annotations

import os
import re
from glob import glob

import pandas as pd

from .tsparse import parse_timestamps
from .coerce import coerce_numeric, coerce_status, STATUS_ON as _STATUS_ON, STATUS_OFF as _STATUS_OFF

# Strip the trailing timezone abbreviation (PDT/PST) -- kept for callers that import it.
_TZ_RE = re.compile(r"\s+[A-Z]{2,4}$")


def _parse_ts(series: pd.Series) -> pd.DatetimeIndex:
    # Delegate to the shared multi-format parser (BAS 12-h format leads its try-list, so existing
    # exports parse identically; ISO / US / epoch / Excel-serial now also work).
    return parse_timestamps(series)


def load_point(path: str, name: str | None = None) -> pd.Series:
    """Load one point CSV into a time-indexed Series named ``name`` (or filename)."""
    df = pd.read_csv(path, encoding="utf-8-sig")
    ts_col, val_col = df.columns[0], df.columns[1]
    idx = _parse_ts(df[ts_col])
    s = pd.Series(coerce_numeric(df[val_col]).values, index=idx)   # thousands/null-token aware
    s = s[~s.index.isna()]
    s.name = name or os.path.basename(path)[:-4]
    return s[~s.index.duplicated(keep="first")].sort_index()


def load_status(path: str, name: str | None = None,
                resample: str | None = None) -> pd.Series:
    """Load a text/event-based status or command point as a 0/1 step series.

    BAS status (``Off``/``Running``) and command (``STOP``/``START``) points are
    logged only at state *changes* on an irregular clock, and carry text values, so
    :func:`load_point` (numeric coerce) yields all-NaN. This maps the on/off vocab
    to 1.0/0.0 and forward-fills the last state, so the series can be sampled on any
    grid. ``resample`` (offset alias) downsamples with the max (on if on at all in
    the interval); None keeps the raw step series.
    """
    df = pd.read_csv(path, encoding="utf-8-sig")
    ts_col, val_col = df.columns[0], df.columns[1]
    idx = _parse_ts(df[ts_col])
    s = pd.Series(coerce_status(df[val_col]).values, index=idx)   # On/Off/Open/Closed/Fault/… -> 0/1
    s = s[~s.index.isna()]
    s = s[~s.index.duplicated(keep="last")].sort_index().ffill()
    s.name = name or os.path.basename(path)[:-4]
    if resample:
        # max(): on if on at any point in the interval. ffill(): carry the last
        # known state across bins that contain no state-change event (the series is
        # event-logged, so most bins are empty).
        s = s.resample(resample).max().ffill()
    return s


def find_point(folder: str, equip: str, measure: str) -> str | None:
    """Path of ``<equip>_<measure>.csv`` in folder, or None.

    ``equip`` is the full equipment token incl. id, e.g. ``VAV_117`` or
    ``AHU_1``; ``measure`` e.g. ``HWValve``, ``CHW_Valve``.
    """
    cand = os.path.join(folder, f"{equip}_{measure}.csv")
    if os.path.exists(cand):
        return cand
    hits = glob(os.path.join(folder, f"{equip}_{measure}.csv"))
    return hits[0] if hits else None


def load_equipment(folder: str, equip: str, measures, resample: str = "15min"):
    """Load several measures for one equipment into a single aligned DataFrame.

    Missing measures are simply omitted (with no error) so callers can request a
    superset. Columns are named by measure.
    """
    cols = {}
    for m in measures:
        p = find_point(folder, equip, m)
        if p:
            cols[m] = load_point(p, name=m)
    if not cols:
        return pd.DataFrame()
    df = pd.concat(cols, axis=1)
    if resample:
        df = df.resample(resample).mean(numeric_only=True)
    return df


def list_equipment(folder: str, equip_type: str):
    """Distinct equipment ids of a given type present in folder.

    e.g. list_equipment(folder, "VAV") -> ["VAV_101", "VAV_102", ...]
    Uses the SpaceTemp file as the existence marker (every box has one).
    """
    out = set()
    for p in glob(os.path.join(folder, f"{equip_type}_*_SpaceTemp.csv")):
        base = os.path.basename(p)[:-4]
        out.add(base[: -len("_SpaceTemp")])
    return sorted(out)
