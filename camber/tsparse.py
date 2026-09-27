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

**Site time zone.** A stamp that names an instant -- an ISO ``Z`` / ``+hh:mm`` offset, a trailing
``UTC`` / ``GMT``, or an epoch number -- is not a wall-clock reading. Given the site's IANA zone
(``timezone="America/Chicago"``; ``source.timezone`` in a config) it is converted to that zone's
wall clock and then made naive, so hour-of-day and schedule logic sees local time. Without one,
the historical behaviour is kept (the clock *as written*, i.e. UTC for ``Z``) and a
:class:`TimezoneWarning` says so; ``strict_timezone=True`` refuses instead. A converted DST
fall-back hour repeats (two instants, one wall-clock label) and the spring-forward hour is absent,
exactly like a naive local BAS export: the adapters collapse the repeat with
:func:`camber.timegrid.regularize`.
"""

from __future__ import annotations

import re
import warnings

import numpy as np
import pandas as pd

__all__ = [
    "parse_timestamps",
    "TimezoneWarning",
    "check_timezone",
]


class TimezoneWarning(UserWarning):
    """Timestamps carry a UTC offset (or are epoch instants) but no site time zone was given.

    Provisional (0.90.1). The stamps are read as the clock written in the file -- UTC for ``Z`` --
    so every hour-of-day, schedule and occupancy judgement is shifted by the site's UTC offset.
    """


def check_timezone(timezone: str | None) -> str | None:
    """Validate an IANA zone name (``None`` passes through); raise ``ValueError`` if unknown.

    Provisional (0.90.1).
    """
    if timezone is None:
        return None
    if not isinstance(timezone, str) or not timezone.strip():
        raise ValueError(
            f"timezone must be an IANA zone name like 'America/Chicago', got {timezone!r}"
        )
    try:
        pd.Timestamp("2020-01-01", tz=timezone)
    except Exception as e:  # pytz / zoneinfo raise different types
        raise ValueError(
            f"unknown timezone {timezone!r}; use an IANA name like 'America/Chicago'"
        ) from e
    return timezone


# An explicit instant marker after a clock time: ISO ``Z`` or a numeric ``+hh:mm`` / ``-hhmm`` /
# ``+hh`` offset, or a trailing ``UTC`` / ``GMT`` label. Anchored on a preceding ``h:mm`` so a
# date such as ``01-01-2018`` (which ends in ``-2018``) is never read as an offset.
_OFFSET = re.compile(
    r"(?<=\d:\d\d)(?P<sec>(?::\d\d(?:[.,]\d+)?)?)\s*(?P<off>Z|z|[+-]\d\d(?::?\d\d)?|\s(?:UTC|GMT|utc|gmt))$"
)


def _split_offsets(text: pd.Series):
    """``(text without its offsets, per-row UTC offset as a Timedelta or NaT)``."""
    m = text.str.extract(_OFFSET)
    off = m["off"]
    if off.isna().all():
        return text, None
    stripped = text.str.replace(_OFFSET, lambda g: g.group("sec"), regex=True).str.strip()

    def _td(o):
        if not isinstance(o, str):
            return pd.NaT
        o = o.strip()
        if o.upper() in ("Z", "UTC", "GMT"):
            return pd.Timedelta(0)
        sign = -1 if o[0] == "-" else 1
        digits = o[1:].replace(":", "")
        hh, mm = int(digits[:2]), int(digits[2:4] or 0)
        return sign * pd.Timedelta(hours=hh, minutes=mm)

    return stripped, pd.to_timedelta(off.map(_td))


def _no_site_tz(what: str, strict: bool) -> None:
    msg = (
        f"timestamps are {what} but no site timezone was given: they are kept as the clock "
        "written in the data (UTC for 'Z' / epoch stamps), NOT converted to local time, so "
        "schedule, occupancy and hour-of-day rules will be shifted by the site's UTC offset. "
        "Pass timezone='<IANA zone>' (config: source.timezone) to convert them"
    )
    if strict:
        raise ValueError(msg + "; refused because strict_timezone is set")
    warnings.warn(
        msg + ", or strict_timezone=True to refuse such data.", TimezoneWarning, stacklevel=4
    )


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
    timezone: str | None = None,
    strict_timezone: bool = False,
) -> pd.DatetimeIndex:
    """Parse ``values`` (strings or numbers) into a :class:`pandas.DatetimeIndex`.

    Unparseable entries become ``NaT`` (never raises, except under ``strict_timezone``).
    ``formats`` overrides the default try-list; ``dayfirst=True`` enables European ``DD/MM``
    formats; ``epoch_unit`` (``"s"``/``"ms"``) forces an epoch interpretation; ``assume_tz``
    localizes naive results to a zone. ``naive`` (default) returns a tz-naive index (wall-clock),
    CAMBER's downstream convention.

    ``timezone`` (provisional, 0.90.1) is the **site's** IANA zone. Stamps that name an instant
    (ISO ``Z`` / ``+hh:mm`` offsets -- mixed offsets across a DST switch included -- a trailing
    ``UTC``/``GMT``, epoch numbers) are converted to that zone's wall clock; naive stamps are taken
    to be local already (or in ``assume_tz``, converted from it). Without ``timezone`` such stamps
    keep the clock as written (the pre-0.90.1 behaviour) and a :class:`TimezoneWarning` is issued;
    ``strict_timezone=True`` raises ``ValueError`` instead.
    """
    check_timezone(timezone)
    s = pd.Series(values)
    tzkw = {"timezone": timezone, "strict": strict_timezone}

    # 1) numeric encodings (epoch / Excel serial), unless the caller passed explicit string formats
    if formats is None:
        kind, arr = _numeric_kind(s)
        if epoch_unit:
            out = pd.to_datetime(_epoch_safe(s, epoch_unit), unit=epoch_unit, errors="coerce")
            return _finish(out, assume_tz, naive, instants="epoch", **tzkw)
        if kind == "excel":
            # errstate: pandas' day->ns cast does its float math on the NaN slots too, which
            # trips a harmless numpy overflow warning once a masked-out cell is present.
            with np.errstate(over="ignore", invalid="ignore"):
                out = _EXCEL_ORIGIN + pd.to_timedelta(_excel_safe(arr), unit="D")
            return _finish(pd.DatetimeIndex(out), assume_tz, naive, **tzkw)
        if kind in ("epoch_s", "epoch_ms", "epoch_ns"):
            unit = {"epoch_s": "s", "epoch_ms": "ms", "epoch_ns": "ns"}[kind]
            out = pd.to_datetime(_epoch_safe(arr, unit), unit=unit, errors="coerce")
            return _finish(out, assume_tz, naive, instants="epoch", **tzkw)

    # 2) string timestamps: split off explicit UTC offsets (kept per row, so mixed offsets across
    # a DST switch parse), strip a trailing tz abbrev, then try explicit formats, best
    # parse-rate wins
    text = s.astype(str).str.strip()
    text, offsets = _split_offsets(text)
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
        out = pd.to_datetime(text, format=best, errors="coerce")
    else:
        # 3) last resort: pandas inference (mixed/uncommon formats) -- honor dayfirst. Silence
        # pandas' per-element format-inference warning; reaching here already means no explicit
        # format fit.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            out = pd.to_datetime(text, errors="coerce", dayfirst=bool(dayfirst), utc=False)
    return _finish(out, assume_tz, naive, offsets=offsets, **tzkw)


def _finish(
    out,
    assume_tz,
    naive,
    *,
    timezone=None,
    strict=False,
    instants=None,
    offsets=None,
) -> pd.DatetimeIndex:
    """Attach / convert zones and return the index (naive unless ``naive=False``).

    ``instants="epoch"`` marks a whole index of UTC instants; ``offsets`` is the per-row UTC offset
    split off the text (``NaT`` for a row that carried none); a tz-aware ``out`` is an instant too.
    """
    idx = pd.DatetimeIndex(out)
    aware_rows = None  # boolean mask of rows that name an instant, when only some do
    if idx.tz is not None:
        what = "tz-aware"
        utc = idx.tz_convert("UTC")
    elif instants == "epoch":
        what = "epoch numbers (UTC instants)"
        utc = idx.tz_localize("UTC")
    elif offsets is not None and offsets.notna().any():
        what = "written with a UTC offset ('Z' / '+hh:mm' / 'UTC')"
        aware_rows = offsets.notna().to_numpy()
        # wall clock as written minus its offset = the UTC instant (rows without one stay NaT)
        utc = (idx - pd.TimedeltaIndex(offsets.to_numpy())).tz_localize("UTC")
    else:
        what = ""
        utc = None

    if utc is not None:
        if timezone is None:
            _no_site_tz(what, strict)
            if not naive:
                idx = utc if aware_rows is None or aware_rows.all() else idx  # the instants
            elif idx.tz is not None:
                idx = idx.tz_localize(None)  # the clock as written
            # epoch / offset-bearing text: idx already holds the clock as written
        else:
            local = utc.tz_convert(timezone).tz_localize(None)
            if aware_rows is not None and not aware_rows.all():
                # a row with no offset of its own is site-local wall clock (or in assume_tz)
                rest = idx[~aware_rows]
                if assume_tz:
                    rest = rest.tz_localize(assume_tz, ambiguous="NaT", nonexistent="NaT")
                    rest = rest.tz_convert(timezone).tz_localize(None)
                vals = local.to_numpy().copy()
                vals[~aware_rows] = rest.to_numpy()
                local = pd.DatetimeIndex(vals)
            if naive:
                idx = local
            elif aware_rows is None or aware_rows.all():
                idx = utc.tz_convert(timezone)  # the instants, exactly
            else:  # a repeated fall-back label cannot be re-localized unambiguously
                idx = local.tz_localize(timezone, ambiguous="NaT", nonexistent="NaT")
        idx.name = None
        return idx

    if assume_tz and idx.tz is None:
        try:
            idx = idx.tz_localize(assume_tz, ambiguous="NaT", nonexistent="NaT")
        except Exception:
            pass
        if timezone is not None and idx.tz is not None:
            idx = idx.tz_convert(timezone)
    elif timezone is not None and not naive and idx.tz is None:
        idx = idx.tz_localize(timezone, ambiguous="NaT", nonexistent="NaT")
    if naive and idx.tz is not None:
        idx = idx.tz_localize(None)  # wall-clock (CAMBER treats a trend as one local clock)
    idx.name = None  # a timestamp index never carries the source column's name
    return idx
