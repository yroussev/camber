"""Source readers for catalog ingest: tables, clocks, row selection and multi-file runs.

**Tables.** Every source table is read through :func:`read_table`, which picks the reader by file
extension:

* ``.csv`` / ``.txt`` / ``.tsv`` -- :func:`pandas.read_csv` (core; ``encoding`` declares a text
  encoding other than UTF-8, e.g. ``"latin-1"``);
* ``.parquet`` -- :func:`pandas.read_parquet` (core);
* ``.xlsx`` / ``.xlsm`` -- :func:`pandas.read_excel` with ``openpyxl``, the ``xlsx`` extra
  (``pip install "camber-toolkit[xlsx]"``);
* ``.xls`` (legacy BIFF) -- :func:`pandas.read_excel` with ``xlrd``, which is **not** part of the
  extra: no catalog entry needs a legacy workbook (the one published beside a catalog dataset, a
  steady-state summary appendix, is not time-series data and is not ingested). Install ``xlrd``
  yourself to read one.

Both Excel engines are imported lazily, only when such a file is read, and a missing engine
raises :class:`MissingExtra` naming the exact install command. ``usecols`` (a column predicate)
prunes the read the same way for every format; ``sheet`` names a worksheet (default: the first).

**Clocks** (:func:`clock_columns`, :func:`stamp`). By default a row's time is its ``timestamp``
column, parsed with the pinned ``timestamp_format`` (a strftime format, or ``"ISO8601"`` for an
export whose stamps mix precisions -- an inferred format silently drops the odd ones). A source
without a parseable wall-clock column declares a ``clock``:

* ``{"kind": "elapsed", "unit": "s", "origin": "2025-01-01"}`` -- the ``timestamp`` column counts
  ``unit`` (``s``, ``min``, ``h``, ``ms``) from ``origin`` (a simulation clock);
* ``{"kind": "day", "day": col, "time": col, "start": date, "every_days": n}`` -- the publisher
  anonymised the dates: day ``d`` at time ``t`` is stored at ``start + d * every_days + t``;
* ``{"kind": "rows", "freq": "1min", "start": date}`` -- no clock at all (steady-state test
  points): each run's rows are numbered ``start + i * freq``.

``day`` and ``rows`` are **synthetic**: CAMBER's construction, never a measured time
(:func:`synthetic_clock_note` says so in the ingest notes and the provenance).

**Time zones** (:func:`parse_timestamps`, :func:`to_local_clock`). The store holds naive wall-clock
time, the convention of every catalog entry and of CAMBER's schedule rules. A stamp that carries a
UTC offset keeps its wall clock as written (the label is dropped -- an export that labels local
time ``+00:00`` is described as a data issue on its entry). A dataset published in another clock
declares ``source_timezone`` -- ``"UTC"``, an IANA zone, or ``"offset"`` (stamps carrying true
per-row UTC offsets, which change at a DST switch: parsed as instants) -- and ``local_timezone``,
the site's IANA zone; the index moves to the local wall clock after the quirks, so a quirk's
timestamps are in the publisher's clock.

**Row selection** (:func:`select_rows`). ``where`` (``{column: value or [values]}``, compared as
text) keeps a run's rows of a table that stacks several rooms or test points, before duplicate
stamps are dropped.

**Multi-file runs** (:func:`read_sources`). A run reads one table, a list of tables (``members``:
per-quantity files, their columns joined on the timestamp), or one file per point (``members`` as
``{raw column: member}``: each member holds one point -- its last non-clock column, or the second
column of a headerless ``timestamp,value`` file -- renamed to the key and joined on the
timestamp). :class:`TableReader` parses a table several runs share once.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable

import pandas as pd

#: catalog ``requires_extras`` name -> (module it needs, install hint)
EXTRAS = {
    "xlsx": ("openpyxl", 'pip install "camber-toolkit[xlsx]"'),
    "brick": ("rdflib", 'pip install "camber-toolkit[brick]"'),
}
EXCEL_EXTS = (".xlsx", ".xlsm")
LEGACY_EXCEL_EXTS = (".xls",)
TEXT_EXTS = (".csv", ".txt", ".tsv")
PARQUET_EXTS = (".parquet",)
#: ``clock.kind`` values (see the module docstring); ``day`` and ``rows`` are synthetic.
CLOCK_KINDS = ("elapsed", "day", "rows")
SYNTHETIC_CLOCKS = ("day", "rows")
#: ``clock.unit`` values of an elapsed clock (pandas ``to_datetime`` units).
ELAPSED_UNITS = ("s", "min", "h", "ms")

_STAMP = "__camber_stamp__"  # the parsed clock of a stamped table (internal column)


class MissingExtra(ImportError):
    """An optional dependency a dataset needs is not installed (the message says how to add it)."""


def _import(module: str, hint: str, why: str):
    try:
        return importlib.import_module(module)
    except ImportError as e:
        raise MissingExtra(f"{why} needs the optional package {module!r}: {hint}") from e


def require_extras(extras, *, what: str) -> None:
    """Raise :class:`MissingExtra` unless each named extra's module imports (``what``: who asks)."""
    for name in extras or ():
        if name not in EXTRAS:
            raise ValueError(f"{what}: unknown extra {name!r} (known: {sorted(EXTRAS)})")
        module, hint = EXTRAS[name]
        _import(module, hint, f"{what} (extra {name!r})")


def needs_extra(filename: str) -> str | None:
    """The extra a file's format needs to be read (``"xlsx"`` for a workbook), else ``None``."""
    ext = os.path.splitext(str(filename))[1].lower()
    return "xlsx" if ext in EXCEL_EXTS + LEGACY_EXCEL_EXTS else None


def read_table(path: str, *, usecols=None, sheet=None, encoding=None, **kw) -> pd.DataFrame:
    """Read one source table by extension (see the module docstring).

    ``usecols`` is a predicate on column names (or a list); ``sheet`` a worksheet name or index for
    a workbook (ignored for other files); ``encoding`` a text file's encoding (ignored for binary
    formats). Extra keyword arguments go to the pandas reader.
    """
    ext = os.path.splitext(str(path))[1].lower()
    if ext in EXCEL_EXTS:
        _import("openpyxl", EXTRAS["xlsx"][1], f"reading {os.path.basename(path)}")
        return pd.read_excel(
            path, sheet_name=0 if sheet is None else sheet, usecols=usecols, engine="openpyxl", **kw
        )
    if ext in LEGACY_EXCEL_EXTS:
        _import(
            "xlrd",
            'pip install "xlrd>=2" (legacy .xls is not part of the xlsx extra)',
            f"reading {os.path.basename(path)}",
        )
        return pd.read_excel(
            path, sheet_name=0 if sheet is None else sheet, usecols=usecols, engine="xlrd", **kw
        )
    if ext in PARQUET_EXTS:
        cols = None
        if callable(usecols):
            import pyarrow.parquet as pq

            cols = [c for c in pq.read_schema(path).names if usecols(c)]
        elif usecols is not None:
            cols = list(usecols)
        return pd.read_parquet(path, columns=cols)
    sep = "\t" if ext == ".tsv" else ","
    if encoding:
        kw["encoding"] = encoding
    return pd.read_csv(path, usecols=usecols, sep=kw.pop("sep", sep), **kw)


# --------------------------------------------------------------------------- clocks


def _clock(spec: dict) -> dict:
    clk = spec.get("clock")
    return clk if isinstance(clk, dict) else {}


def clock_columns(spec: dict) -> set:
    """The raw columns that make up a row's time (none for a ``rows`` clock)."""
    clk = _clock(spec)
    kind = clk.get("kind")
    if kind == "day":
        return {clk["day"], clk["time"]}
    if kind == "rows":
        return set()
    return {spec.get("timestamp", "Datetime")}


def synthetic_clock_note(spec: dict) -> str | None:
    """What a synthetic clock is, for the ingest notes and the provenance (``None`` otherwise)."""
    clk = _clock(spec)
    if clk.get("kind") == "day":
        return (
            f"synthetic day clock: day d of column {clk['day']!r} is stored on "
            f"{clk.get('start', '2000-01-03')} + {int(clk.get('every_days', 1))}*d days "
            "(not a measured date)"
        )
    if clk.get("kind") == "rows":
        return (
            f"synthetic {clk.get('freq')} clock: the source has no timestamps; each run's rows are "
            f"numbered from {clk.get('start', '2000-01-01')} (not a measured time)"
        )
    return None


def parse_timestamps(values, spec: dict) -> pd.DatetimeIndex:
    """Parse a timestamp column in the **source clock** (naive; see the module docstring).

    ``timestamp_format`` pins the parse (unparseable -> ``NaT``). With ``source_timezone:
    "offset"`` the stamps are instants: parsed to UTC, then the offset is dropped (UTC wall time;
    :func:`to_local_clock` moves it to the site's clock). Otherwise an offset label is dropped and
    the wall clock kept as written.
    """
    fmt = spec.get("timestamp_format")
    kw = {"format": fmt} if fmt else {}
    if spec.get("source_timezone") == "offset":
        out = pd.DatetimeIndex(pd.to_datetime(values, errors="coerce", utc=True, **kw))
        return out.tz_localize(None)
    out = pd.DatetimeIndex(pd.to_datetime(values, errors="coerce", **kw))
    return out.tz_localize(None) if out.tz is not None else out


def stamp(raw: pd.DataFrame, spec: dict) -> pd.DataFrame:
    """Replace the clock column(s) of a freshly read table by one parsed stamp column.

    A ``rows`` clock has no clock column: its rows are numbered per run by :func:`index_rows`.
    """
    clk = _clock(spec)
    kind = clk.get("kind")
    if kind == "rows":
        return raw
    ts = spec.get("timestamp", "Datetime")
    if kind == "day":
        day = pd.to_numeric(raw.pop(clk["day"]), errors="coerce")
        tod = pd.to_timedelta(raw.pop(clk["time"]).astype(str), errors="coerce")
        step = pd.to_timedelta(day * int(clk.get("every_days", 1)), unit="D")
        raw[_STAMP] = pd.Timestamp(clk.get("start", "2000-01-03")) + step + tod
    elif kind == "elapsed":
        raw[_STAMP] = pd.to_datetime(
            pd.to_numeric(raw.pop(ts), errors="coerce"),
            unit=clk["unit"],
            origin=pd.Timestamp(clk["origin"]),
            errors="coerce",
        )
    else:
        raw[_STAMP] = parse_timestamps(raw.pop(ts), spec).to_numpy()
    return raw


def select_rows(raw: pd.DataFrame, where: dict | None) -> pd.DataFrame:
    """The rows whose ``where`` columns hold the given value (or one of the listed values).

    Values compare as text, so ``{"id": 917810}`` matches an integer column and ``{"Filename":
    ["a.txt", "b.txt"]}`` a text one.
    """
    for col, want in (where or {}).items():
        vals = want if isinstance(want, list) else [want]
        raw = raw[raw[col].astype(str).isin([str(v) for v in vals])]
    return raw


def index_rows(raw: pd.DataFrame, spec: dict, where: dict | None = None) -> tuple:
    """A stamped table -> ``(frame on its source-clock index, duplicate stamps dropped)``.

    Keeps the ``where`` rows first, then indexes on the stamp, drops unparseable stamps and
    duplicated ones (the first reading wins) and sorts. A ``rows`` clock numbers the kept rows in
    their table order instead. Never mutates ``raw`` (a cached table is shared by several runs).
    """
    ts = spec.get("timestamp", "Datetime")
    rows = select_rows(raw, where)
    clk = _clock(spec)
    if clk.get("kind") == "rows":
        out = rows.reset_index(drop=True)
        out.index = pd.date_range(
            clk.get("start", "2000-01-01"), periods=len(out), freq=clk["freq"], name=ts
        )
        return out, 0
    idx = pd.DatetimeIndex(rows[_STAMP], name=ts)
    out = rows.drop(columns=[_STAMP])
    out.index = idx
    out = out[~out.index.isna()]
    dup = out.index.duplicated(keep="first")
    return out[~dup].sort_index(), int(dup.sum())


def to_local_clock(raw: pd.DataFrame, spec: dict) -> pd.DataFrame:
    """Re-express a naive source-clock index in the site's wall clock (``local_timezone``).

    ``source_timezone`` names the clock the stamps are in (``"UTC"``, an IANA zone, or ``"offset"``,
    which :func:`parse_timestamps` already brought to UTC). The result is naive local time: the
    repeated hour of a DST fall-back keeps both readings (a resample averages them) and the
    skipped spring-forward hour is simply absent. Without ``local_timezone`` the index is left as
    published.
    """
    local = spec.get("local_timezone")
    if not local or raw.empty:
        return raw
    src = spec.get("source_timezone") or "UTC"
    src = "UTC" if src == "offset" else src
    idx = pd.DatetimeIndex(raw.index)
    idx = idx.tz_localize(src, ambiguous="NaT", nonexistent="NaT").tz_convert(local)
    out = raw.copy()
    out.index = idx.tz_localize(None)
    out = out[~out.index.isna()]
    return out.sort_index(kind="stable")


# --------------------------------------------------------------------------- sources


def table_key(path, spec: dict) -> tuple:
    """The identity of one table read: file, worksheet, text encoding and how its clock parses
    (a run may carry its own ``timestamp_format``)."""
    clock = tuple(
        repr(spec.get(k)) for k in ("timestamp", "timestamp_format", "source_timezone", "clock")
    )
    return (os.fspath(path), spec.get("sheet"), spec.get("encoding"), clock)


def _read_stamped(key: tuple, spec: dict, keep: Callable[[str], bool]) -> pd.DataFrame:
    path, sheet, encoding, _clock_sig = key
    clock = clock_columns(spec)
    raw = read_table(path, usecols=lambda c: c in clock or keep(c), sheet=sheet, encoding=encoding)
    return stamp(raw, spec)


class TableReader:
    """Reads each source table once for every run that needs it (column-pruned, then released).

    A dataset that splits many units over a few tables (one CSV per quantity with every unit in
    it, one table stacking several rooms, one test sheet per scenario read by several mappings)
    would otherwise re-parse a table once per run. The runs are **planned** first -- each with the
    predicate of the columns it wants -- so a table is read once with the union of the columns,
    its clock parsed once, and dropped after its last use.
    """

    def __init__(self, spec: dict):
        self.spec = spec
        self.preds: dict = {}
        self.uses: dict = {}
        self.frames: dict = {}

    def plan(self, key: tuple, pred: Callable[[str], bool]) -> None:
        """Register one future :meth:`read` of ``key`` wanting the columns ``pred`` accepts."""
        self.preds.setdefault(key, []).append(pred)
        self.uses[key] = self.uses.get(key, 0) + 1

    def read(self, key: tuple, pred: Callable[[str], bool], spec: dict) -> pd.DataFrame:
        """The stamped table ``key`` (read now, or from the cache while planned uses remain)."""
        if key in self.frames:
            df = self.frames[key]
        else:
            preds = self.preds.get(key) or [pred]
            df = _read_stamped(key, spec, lambda c: any(p(c) for p in preds))
            if self.uses.get(key, 0) > 1:
                self.frames[key] = df
        if key in self.uses:
            self.uses[key] -= 1
            if self.uses[key] <= 0:
                self.frames.pop(key, None)
        return df


def read_point(path, spec: dict, *, value: str | None = None) -> tuple:
    """One point's file -> ``(numeric series on its source-clock index, duplicate stamps)``.

    ``value`` names the value column; without it the file either has a header naming the clock
    column(s) -- the value is its last non-clock column -- or is a headerless
    ``timestamp,value`` file.
    """
    ts = spec.get("timestamp", "Datetime")
    clock = clock_columns(spec)
    enc = spec.get("encoding")
    if value is None:
        header = list(read_table(path, nrows=0, encoding=enc).columns)
        if clock and clock <= set(header):
            value = [c for c in header if c not in clock][-1]
            raw = read_table(path, usecols=lambda c: c in clock or c == value, encoding=enc)
        else:  # headerless: timestamp, value
            value = "value"
            raw = read_table(path, header=None, names=[ts, value], dtype={ts: str}, encoding=enc)
    else:
        raw = read_table(path, usecols=lambda c: c in clock or c == value, encoding=enc)
    out, dups = index_rows(stamp(raw, spec), spec)
    return pd.to_numeric(out[value], errors="coerce"), dups


def read_sources(
    sources,
    spec: dict,
    keep: Callable[[str], bool],
    *,
    where: dict | None = None,
    reader: TableReader | None = None,
) -> pd.DataFrame:
    """A run's source file(s) -> one raw frame on the source-clock index (see the module).

    ``sources`` is one table path, a list of table paths (joined on the timestamp), or
    ``{raw column: path}`` for one-point files. ``keep(column)`` says which raw columns the run
    needs (the clock and ``where`` columns are read regardless); ``where`` keeps the run's rows of
    each table. ``reader`` shares tables between runs.
    """
    if isinstance(sources, dict):
        parts = [read_point(p, spec)[0].rename(col) for col, p in sources.items() if keep(col)]
        if not parts:
            ts = spec.get("timestamp", "Datetime")
            return pd.DataFrame(index=pd.DatetimeIndex([], name=ts))
    else:
        paths = [sources] if isinstance(sources, (str, os.PathLike)) else list(sources)
        wcols = set(where or {})

        def want(c) -> bool:
            return c in wcols or keep(c)

        parts = []
        for p in paths:
            key = table_key(p, spec)
            table = reader.read(key, want, spec) if reader else _read_stamped(key, spec, want)
            parts.append(index_rows(table, spec, where)[0])
    if len(parts) == 1:
        raw = parts[0]
        return raw.to_frame() if isinstance(raw, pd.Series) else raw
    raw = pd.concat(parts, axis=1)
    return raw.loc[:, ~raw.columns.duplicated(keep="first")].sort_index()
