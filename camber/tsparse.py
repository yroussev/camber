"""Robust multi-format timestamp parsing for BAS / interval-data exports.

Every BAS export tool stamps time differently — ISO 8601, US ``MM/DD/YYYY``, European
``DD/MM/YYYY``, the classic BAS ``21-Apr-23 8:30:03 AM PDT``, LBNL's ``yyyymmdd hh:mm``, epoch
seconds/millis, or an Excel serial number — and pandas' bare inference silently misreads several
of them (European dates read as US; epoch integers read as nanoseconds). This module centralizes
parsing so every adapter shares one well-behaved path instead of a hand-rolled ``pd.to_datetime``
per file.

Strategy: strip a trailing timezone abbreviation, then try an **ordered list of explicit formats**
(the common BAS/ISO ones first, so existing data parses identically), detect **epoch** and **Excel
serial** numeric encodings, and fall back to pandas inference **last**. Auto-detect picks the
format with the highest parse rate on a sample. Naive-local by default (CAMBER's downstream
convention — see ``timegrid``); tz is preserved only when asked. numpy/pandas + stdlib.
"""

from __future__ import annotations

import re
import warnings

import numpy as np
import pandas as pd

__all__ = [
    "parse_timestamps",
]

# Trailing timezone abbreviation (PDT/PST/GMT/…) — pandas can't parse %Z reliably and CAMBER
# treats a trend as one local clock (DST is re-attached deliberately in timegrid.localize). The
# negative lookahead protects a trailing AM/PM meridiem (also 2 letters) from being stripped
# as a tz.
_TZ_ABBREV = re.compile(r"\s+(?![AaPp][Mm]$)[A-Za-z]{2,4}$")

# Ordered explicit formats. BAS + ISO lead so already-working data parses identically; European
# day-first formats are only tried when dayfirst is requested (they're ambiguous with US otherwise).
_BASE_FORMATS = [
    "ISO8601",  # pandas 2 fast path (incl. offsets)
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%d-%b-%y %I:%M:%S %p",
    "%d-%b-%Y %I:%M:%S %p",  # BAS trend export (12-h)
    "%d-%b-%y %H:%M:%S",
    "%d-%b-%Y %H:%M:%S",
    "%m/%d/%Y %I:%M:%S %p",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",  # US
    "%m/%d/%y %I:%M:%S %p",
    "%m/%d/%y %H:%M",
    "%Y%m%d %H:%M",
    "%Y%m%d %H:%M:%S",  # LBNL yyyymmdd hh:mm
]
_DAYFIRST_FORMATS = ["%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%y %H:%M"]

_EXCEL_ORIGIN = pd.Timestamp("1899-12-30")  # Excel's serial-date epoch


def _rate(parsed) -> float:
    n = len(parsed)
    return 0.0 if n == 0 else float(parsed.notna().sum()) / n


def _in_range(arr, lo: float, hi: float):
    """``arr`` as float64 with non-finite and out-of-``[lo, hi]`` entries replaced by NaN.

    pandas stores datetimes as int64 nanoseconds, so a unit conversion (``to_datetime(unit=…)``,
    ``to_timedelta``) on ``inf`` or on a value past that range raises ``OverflowError`` *before*
    ``errors="coerce"`` gets a say — ``'INFINITY'`` reads as a float via ``to_numeric``, so a
    single such cell used to blow up the whole parse. Masking them here keeps them NaT.
    """
    with np.errstate(over="ignore", invalid="ignore"):
        vals = pd.to_numeric(pd.Series(arr), errors="coerce").astype("float64")
        raw = vals.to_numpy()
        bad = ~np.isfinite(raw) | (raw < lo) | (raw > hi)
        return vals.mask(bad)


def _epoch_safe(arr, unit: str):
    """``arr`` masked to the values ``to_datetime(unit=unit)`` can represent."""
    try:
        per_unit = float(pd.Timedelta(1, unit=unit).value)
    except (ValueError, TypeError):
        per_unit = 1.0
    limit = float(np.iinfo(np.int64).max) / max(per_unit, 1.0)
    return _in_range(arr, -limit, limit)


def _excel_safe(arr):
    """``arr`` masked to the serial-day offsets that land inside pandas' Timestamp bounds."""
    # In int64 nanoseconds — subtracting the Timestamp bounds directly overflows Timedelta.
    ns_per_day = 86_400 * 10**9
    lo = (pd.Timestamp.min.value - _EXCEL_ORIGIN.value) / ns_per_day
    hi = (pd.Timestamp.max.value - _EXCEL_ORIGIN.value) / ns_per_day
    return _in_range(arr, lo + 1.0, hi - 1.0)


def _numeric_kind(values):
    """If ``values`` are all numeric, classify as
    ('epoch_s'|'epoch_ms'|'excel'|None, numeric_array)."""
    arr = pd.to_numeric(pd.Series(values), errors="coerce")
    nn = arr.dropna()
    if nn.empty or len(nn) < len(arr):  # any non-numeric -> treat as text timestamps
        return None, None
    # Classify on the finite values only: one ``inf`` would otherwise drag the median past every
    # threshold and mislabel an ordinary column as epoch nanoseconds.
    finite = nn.to_numpy(dtype="float64")
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        return None, None
    med = float(np.median(np.abs(finite)))
    if med >= 1e14:
        return "epoch_ns", arr
    if med >= 1e11:
        return "epoch_ms", arr
    if med >= 1e8:
        return "epoch_s", arr
    if 1.0 <= med < 1e5:  # ~1900..2100 as Excel serial days
        return "excel", arr
    return None, None


def parse_timestamps(
    values,
    *,
    formats=None,
    dayfirst: bool | None = None,
    epoch_unit: str | None = None,
    assume_tz: str | None = None,
    strip_tz_abbrev: bool = True,
    naive: bool = True,
) -> pd.DatetimeIndex:
    """Parse ``values`` (strings or numbers) into a :class:`pandas.DatetimeIndex`.

    Unparseable entries become ``NaT`` (never raises). ``formats`` overrides the default try-list;
    ``dayfirst=True`` enables European ``DD/MM`` formats; ``epoch_unit`` (``"s"``/``"ms"``) forces
    an epoch interpretation; ``assume_tz`` localizes naive results to a zone. ``naive`` (default)
    returns a tz-naive index (wall-clock), CAMBER's downstream convention.
    """
    s = pd.Series(values)

    # 1) numeric encodings (epoch / Excel serial), unless the caller passed explicit string formats
    if formats is None:
        kind, arr = _numeric_kind(s)
        if epoch_unit:
            out = pd.to_datetime(_epoch_safe(s, epoch_unit), unit=epoch_unit, errors="coerce")
            return _finish(out, assume_tz, naive)
        if kind == "excel":
            # errstate: pandas' day->ns cast does its float math on the NaN slots too, which
            # trips a harmless numpy overflow warning once a masked-out cell is present.
            with np.errstate(over="ignore", invalid="ignore"):
                out = _EXCEL_ORIGIN + pd.to_timedelta(_excel_safe(arr), unit="D")
            return _finish(pd.DatetimeIndex(out), assume_tz, naive)
        if kind in ("epoch_s", "epoch_ms", "epoch_ns"):
            unit = {"epoch_s": "s", "epoch_ms": "ms", "epoch_ns": "ns"}[kind]
            out = pd.to_datetime(_epoch_safe(arr, unit), unit=unit, errors="coerce")
            return _finish(out, assume_tz, naive)

    # 2) string timestamps: strip trailing tz abbrev, then try explicit formats, best
    # parse-rate wins
    text = s.astype(str).str.strip()
    if strip_tz_abbrev:
        text = text.str.replace(_TZ_ABBREV, "", regex=True).str.strip()
    tries = (
        list(formats)
        if formats is not None
        else ((_DAYFIRST_FORMATS + _BASE_FORMATS) if dayfirst else _BASE_FORMATS)
    )
    sample = text.dropna().head(200)
    best, best_rate = None, 0.0
    for fmt in tries:
        try:
            r = _rate(pd.to_datetime(sample, format=fmt, errors="coerce"))
        except (ValueError, TypeError):
            continue  # unsupported format string (e.g. old pandas) -> skip
        if r > best_rate:
            best, best_rate = fmt, r
        if r == 1.0:
            break
    if best is not None and best_rate >= 0.5:
        return _finish(pd.to_datetime(text, format=best, errors="coerce"), assume_tz, naive)

    # 3) last resort: pandas inference (mixed/uncommon formats) — honor dayfirst. Silence pandas'
    # per-element format-inference warning; reaching here already means no explicit format fit.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        out = pd.to_datetime(text, errors="coerce", dayfirst=bool(dayfirst), utc=False)
    return _finish(out, assume_tz, naive)


def _finish(out, assume_tz, naive) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(out)
    if assume_tz and idx.tz is None:
        try:
            idx = idx.tz_localize(assume_tz, ambiguous="NaT", nonexistent="NaT")
        except Exception:
            pass
    if naive and idx.tz is not None:
        idx = idx.tz_localize(None)  # wall-clock (CAMBER treats a trend as one local clock)
    idx.name = None  # a timestamp index never carries the source column's name
    return idx
