"""A digest of a part's rows that does not depend on its bytes (0.107, #142).

An edge part is named by the sha256 of its bytes, so the landing could only recognise a re-sent
batch when the bytes were identical. They are not after a forwarder upgrade that changes the
serialization (0.107 writes canonical parts), nor after a pandas or pyarrow upgrade on the edge
device. :func:`rows_digest` hashes the decoded rows instead:

1. read the part's five long columns ``(ts, equip, equip_class, role, value)``;
2. normalize the types: ``ts`` to int64 nanoseconds (UTC instants for a tz-aware column; the
   header records whether it was tz-aware), ``value`` to float64 with ``-0.0`` read as ``0.0`` and
   one NaN, the three labels to UTF-8 strings (dictionary and large strings read the same);
3. sort the rows by ``(ts, equip, role, equip_class, value)``, the store's canonical order;
4. sha256 over a fixed encoding of every column (a null mask, then the values; strings length
   prefixed), after a versioned header with the row count.

Column order, row order, row groups, encodings, compression, timestamp unit, string type and the
schema metadata (pandas' own, with its version) do not change the digest; any change to a row
does, and so does a duplicated or dropped row. A part that cannot be read, or whose columns are not
exactly the long five, has no digest (``None``): the landing then falls back to its byte checks.

:class:`PartitionRows` keeps the digests of the parts in one partition directory in
``_edge_rows.json`` beside them. It is a cache: an entry counts only while its part's size and
mtime are unchanged, and parts with no entry (landed before 0.107, straight into the store, or
written by the store itself) are digested on first use. Losing the file loses nothing but time.
Since 0.108 each entry also records the part's timestamp span (``ts_min`` / ``ts_max``), so a
landing compares rows only with the parts whose span meets its own.

:class:`PartitionView` (0.108, #228, #230) is the landing's working picture of one partition: the
parts on disk plus the ones planned in the current run, minus the ones planned to be replaced. It
answers the row-level questions the digest cannot: does a new part hold every row of an existing
one (a month that grew since its last send), are all its rows already stored, or do the two
disagree (a conflict)? Rows are compared on their normalized values, so bytes never matter.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import struct

__all__ = [
    "DIGEST_VERSION",
    "INDEX_NAME",
    "rows_digest",
    "file_rows_digest",
    "PartitionRows",
    "PartitionView",
    "read_rows",
]

DIGEST_VERSION = 1
INDEX_NAME = "_edge_rows.json"  # leading "_": dataset discovery and the landing walk skip it
_COLUMNS = ("ts", "equip", "equip_class", "role", "value")
_SORT = ("ts", "equip", "role", "equip_class", "value")
_LABELS = ("equip", "equip_class", "role")
_NULL = struct.pack("<I", 0xFFFFFFFF)


def _norm_table(table):
    """The five long columns with normalized types, or ``None`` when the table is not one."""
    import pyarrow as pa
    import pyarrow.compute as pc

    if sorted(table.column_names) != sorted(_COLUMNS):
        return None, None
    ts = table.column("ts")
    if not pa.types.is_timestamp(ts.type):
        return None, None
    aware = ts.type.tz is not None
    # an aware column's int64 values are UTC instants whatever its tz; a unit cast keeps that
    ts = pc.cast(ts, pa.timestamp("ns", tz=ts.type.tz)).cast(pa.int64())
    value = table.column("value")
    if not (pa.types.is_floating(value.type) or pa.types.is_integer(value.type)):
        return None, None
    value = pc.cast(value, pa.float64())
    value = pc.add(value, pa.scalar(0.0))  # -0.0 + 0.0 == +0.0
    cols = {"ts": ts, "value": value}
    for c in _LABELS:
        col = table.column(c)
        if pa.types.is_dictionary(col.type):
            col = pc.cast(col, col.type.value_type)
        if not (pa.types.is_string(col.type) or pa.types.is_large_string(col.type)):
            if pa.types.is_null(col.type):
                col = pa.chunked_array([pa.nulls(len(col), pa.string())])
            else:
                return None, None
        cols[c] = pc.cast(col, pa.large_string())
    out = pa.table({c: cols[c] for c in _COLUMNS})
    if out.num_rows > 1:
        out = out.sort_by([(c, "ascending") for c in _SORT])
    return out, aware


def _label(s) -> bytes:
    """One string, length prefixed (a null is a length no string can have)."""
    if s is None:
        return _NULL
    b = s.encode("utf-8")
    return struct.pack("<I", len(b)) + b


def _mask(col) -> bytes:
    import pyarrow.compute as pc

    return pc.is_null(col).to_numpy(zero_copy_only=False).astype("uint8").tobytes()


def rows_digest(table) -> str | None:
    """The hex row digest of an Arrow table of long rows (see the module docstring), or ``None``."""
    import numpy as np
    import pyarrow.compute as pc

    try:
        norm, aware = _norm_table(table)
    except Exception:  # noqa: BLE001 - a cast that fails means "no digest", never a crash
        return None
    if norm is None:
        return None
    h = hashlib.sha256()
    h.update(f"camber-rows/{DIGEST_VERSION};aware={int(bool(aware))};n={norm.num_rows}\n".encode())
    for c in _COLUMNS:
        col = norm.column(c)
        h.update(c.encode() + b"\0")
        h.update(_mask(col))
        if c == "ts":
            h.update(pc.fill_null(col, 0).to_numpy().astype("<i8").tobytes())
        elif c == "value":
            v = pc.fill_null(col, 0.0).to_numpy().astype("<f8")
            v = np.where(np.isnan(v), np.float64("nan"), v)  # one NaN bit pattern
            h.update(v.tobytes())
        else:
            h.update(b"".join(_label(s) for s in col.to_pylist()))
    return h.hexdigest()


def _read_table(path: str):
    import pyarrow.parquet as pq

    try:
        with pq.ParquetFile(path) as f:  # the file's own columns, no hive discovery
            return f.read()
    except Exception:  # noqa: BLE001 - unreadable: no digest
        return None


def _span(norm):
    """``(ts_min, ts_max)`` in int64 nanoseconds of a normalized table, ``None`` when empty."""
    import pyarrow.compute as pc

    mm = pc.min_max(norm.column("ts")).as_py()
    if mm["min"] is None:
        return None
    return int(mm["min"]), int(mm["max"])


def _summary(path: str):
    """``{"rows_sha256", "rows", "span"}`` of one part file (``rows_sha256`` ``None`` when it has
    no digest)."""
    table = _read_table(path)
    d = rows_digest(table) if table is not None else None
    if d is None:
        return {"rows_sha256": None, "rows": None, "span": None}
    try:
        norm, _aware = _norm_table(table)
        span = _span(norm)
    except Exception:  # noqa: BLE001 - the digest succeeded, so this does too; be safe anyway
        span = None
    return {"rows_sha256": d, "rows": int(table.num_rows), "span": span}


def file_rows_digest(path: str):
    """``(digest, rows)`` of one Parquet part file, or ``(None, None)`` when it has none."""
    table = _read_table(path)
    if table is None:
        return None, None
    d = rows_digest(table)
    return (d, int(table.num_rows)) if d else (None, None)


_NULL_LABEL = "\x00null"  # a null label, kept apart from every real string in a row comparison


def read_rows(path: str):
    """The rows of one Parquet part as a pandas frame for comparing parts (0.108), or ``None``.

    The five long columns, normalized as for :func:`rows_digest`: ``ts`` as int64 nanoseconds,
    the labels as strings (a null as a marker no label can equal) and ``value`` as the int64 bit
    pattern of its float64 (``-0.0`` read as ``0.0``, one NaN), so equal rows compare equal
    whatever bytes held them. ``None`` when the part has no digest.
    """
    import numpy as np
    import pandas as pd
    import pyarrow.compute as pc

    table = _read_table(path)
    if table is None:
        return None
    try:
        norm, _aware = _norm_table(table)
    except Exception:  # noqa: BLE001 - no digest, no comparison
        return None
    if norm is None:
        return None
    out = {}
    out["ts"] = pc.fill_null(norm.column("ts"), np.iinfo("int64").min).to_numpy()
    for c in _LABELS:
        out[c] = [_NULL_LABEL if v is None else v for v in norm.column(c).to_pylist()]
    v = pc.fill_null(norm.column("value"), np.float64("nan")).to_numpy().astype("<f8")
    v = np.where(np.isnan(v), np.float64("nan"), v)
    out["value"] = v.view("<i8")
    return pd.DataFrame(out, columns=list(_COLUMNS))


_KEY = ["ts", "equip", "role"]  # one observation: what two parts must agree on
_ROW = list(_COLUMNS)


def _contains(big, small) -> bool:
    """Whether every row of ``small`` is a row of ``big`` (rows compared as sets)."""
    if small is None or big is None:
        return False
    if len(small) == 0:
        return True
    m = small.drop_duplicates().merge(big.drop_duplicates(), how="left", on=_ROW, indicator=True)
    return bool((m["_merge"] == "both").all())


def _shared_keys(a, b) -> int:
    """How many observations (``ts``, ``equip``, ``role``) the two frames share."""
    if a is None or b is None or len(a) == 0 or len(b) == 0:
        return 0
    return int(len(a[_KEY].drop_duplicates().merge(b[_KEY].drop_duplicates(), on=_KEY)))


def _tail_covered(big, small):
    """How many observations of ``small`` ``big`` revises, when ``big`` covers ``small`` but for
    a revised last observation of each series; ``None`` when it does not.

    ``big`` covers ``small`` when it holds every observation ``(ts, equip, role)`` of ``small``
    and every row of ``small`` it does not hold is the last observation of its series
    ``(equip, role)`` in ``small``: the hour that was still in progress when ``small`` was polled
    (a resampled mean of the samples so far), revised in a later poll. Any other difference is
    a conflict."""
    if small is None or big is None:
        return None
    if len(small) == 0:
        return 0
    keys = (
        small[_KEY]
        .drop_duplicates()
        .merge(big[_KEY].drop_duplicates(), how="left", on=_KEY, indicator=True)
    )
    if not bool((keys["_merge"] == "both").all()):
        return None
    m = small.drop_duplicates().merge(big.drop_duplicates(), how="left", on=_ROW, indicator=True)
    missing = m.loc[m["_merge"] == "left_only", _ROW]
    if missing.empty:
        return 0
    last = small.groupby(["equip", "role"], sort=False)["ts"].max().rename("_last").reset_index()
    tail = missing.merge(last, on=["equip", "role"], how="left")
    if not bool((tail["ts"] == tail["_last"]).all()):
        return None
    return int(missing[_KEY].drop_duplicates().shape[0])


def _differing(a, b) -> int:
    """How many shared observations carry a different row (another value or class) in ``b``."""
    if a is None or b is None or len(a) == 0 or len(b) == 0:
        return 0
    m = a.drop_duplicates().merge(b.drop_duplicates(), on=_KEY, suffixes=("", "_b"))
    if m.empty:
        return 0
    diff = (m["value"] != m["value_b"]) | (m["equip_class"] != m["equip_class_b"])
    return int(m.loc[diff, _KEY].drop_duplicates().shape[0])


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class PartitionRows:
    """The row digests of the parts in one partition directory (``year=/month=`` or ``year=``).

    :meth:`find` answers "which part here already holds these rows"; :meth:`record` adds a part
    just stored; :meth:`save` writes ``_edge_rows.json`` atomically when anything changed. The
    caller holds the lock that serializes landings (the portfolio lock).
    """

    def __init__(self, directory: str):
        self.dir = directory
        self.path = os.path.join(directory, INDEX_NAME)
        self.parts: dict = {}
        self._dirty = False
        try:
            with open(self.path, encoding="utf-8") as fh:
                doc = json.load(fh)
            parts = doc.get("parts") if isinstance(doc, dict) else None
            if isinstance(parts, dict) and doc.get("version") == DIGEST_VERSION:
                self.parts = {k: v for k, v in parts.items() if isinstance(v, dict)}
        except (OSError, ValueError):
            pass

    def _fresh(self, name: str):
        """The part's entry if its file still has the size and mtime it was recorded with."""
        e = self.parts.get(name)
        try:
            st = os.stat(os.path.join(self.dir, name))
        except OSError:
            return None
        if e and e.get("bytes") == st.st_size and e.get("mtime_ns") == st.st_mtime_ns:
            return e
        return None

    def _digest_part(self, name: str):
        path = os.path.join(self.dir, name)
        try:
            st = os.stat(path)
        except OSError:
            return None
        summ = _summary(path)
        e = {
            "rows_sha256": summ["rows_sha256"],
            "rows": summ["rows"],
            "bytes": st.st_size,
            "mtime_ns": st.st_mtime_ns,
            "recorded_at": _utc_now(),
        }
        if summ["span"] is not None:
            e["ts_min"], e["ts_max"] = summ["span"]
        self.parts[name] = e
        self._dirty = True
        return e

    def entry(self, name: str):
        """The fresh entry of part ``name`` here, digesting it when it has none (``None`` when the
        file is gone)."""
        return self._fresh(name) or self._digest_part(name)

    def names(self) -> list:
        """The part files in this directory (``.parquet``, not ``_`` / ``.`` names), sorted."""
        if not os.path.isdir(self.dir):
            return []
        return sorted(
            n
            for n in os.listdir(self.dir)
            if not n.startswith(("_", "."))
            and n.endswith(".parquet")
            and os.path.isfile(os.path.join(self.dir, n))
        )

    def forget(self, name: str) -> None:
        """Drop the entry of a part that was removed or replaced."""
        if self.parts.pop(name, None) is not None:
            self._dirty = True

    def find(self, digest: str | None, *, exclude: str | None = None) -> str | None:
        """The name of a part in this directory whose rows have ``digest`` (``None`` if none).

        Every part file is compared: edge parts, the parts ``migrate-partitions`` split from
        year-only edge parts, and the store's own parts (a part holding exactly these rows means
        they are already here, whoever wrote it). Parts with a fresh entry are compared by it; the
        others are digested now and cached."""
        if not digest or not os.path.isdir(self.dir):
            return None
        for name in sorted(os.listdir(self.dir)):
            if name == exclude or name.startswith(("_", ".")) or not name.endswith(".parquet"):
                continue
            e = self._fresh(name) or self._digest_part(name)
            if e and e.get("rows_sha256") == digest:
                return name
        return None

    def record(
        self, name: str, digest: str | None = None, rows: int | None = None, span=None
    ) -> None:
        """Record a part just stored here (digested from its file when ``digest`` is not given);
        ``span`` is its ``(ts_min, ts_max)`` in nanoseconds when known."""
        path = os.path.join(self.dir, name)
        if digest is None:
            self._digest_part(name)
            return
        st = os.stat(path)
        e = {
            "rows_sha256": digest,
            "rows": rows,
            "bytes": st.st_size,
            "mtime_ns": st.st_mtime_ns,
            "recorded_at": _utc_now(),
        }
        if span is not None:
            e["ts_min"], e["ts_max"] = int(span[0]), int(span[1])
        self.parts[name] = e
        self._dirty = True

    def save(self) -> bool:
        """Write the index (atomically) if it changed and the directory exists; drop entries of
        parts that are gone."""
        if not self._dirty or not os.path.isdir(self.dir):
            return False
        live = {n: e for n, e in self.parts.items() if os.path.exists(os.path.join(self.dir, n))}
        doc = {"version": DIGEST_VERSION, "parts": dict(sorted(live.items()))}
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)
        self._dirty = False
        return True


class PartitionView:
    """The landing's picture of one partition directory during a run (0.108, #228, #230).

    It starts from the parts on disk (their :class:`PartitionRows` entries, digested on first use)
    and follows the run's plan: :meth:`add` a part planned to land, :meth:`drop` one planned to be
    replaced. Parts with no digest (unreadable, or not the five long columns) are never compared.
    A comparison reads only the parts whose timestamp span meets the new part's.
    """

    def __init__(self, directory: str, index: PartitionRows | None = None):
        self.dir = directory
        self.index = index if index is not None else PartitionRows(directory)
        self.members: dict = {}  # name -> {"path", "rows_sha256", "span"}
        self._frames: dict = {}
        for n in self.index.names():
            e = self.index.entry(n)
            if e is None or not e.get("rows_sha256"):
                continue
            if "ts_min" not in e and e.get("rows"):  # recorded by 0.107: no span yet
                e = self.index._digest_part(n)
                if e is None or not e.get("rows_sha256"):  # pragma: no cover - changed under us
                    continue
            span = (e["ts_min"], e["ts_max"]) if "ts_min" in e else None
            self.members[n] = {
                "path": os.path.join(directory, n),
                "rows_sha256": e["rows_sha256"],
                "span": span,
            }

    def exact(self, digest: str | None, *, exclude: str | None = None) -> str | None:
        """A member whose rows have ``digest`` (``None`` if none)."""
        if not digest:
            return None
        for n in sorted(self.members):
            if n != exclude and self.members[n]["rows_sha256"] == digest:
                return n
        return None

    def overlapping(self, span, *, exclude: str | None = None) -> list:
        """The members whose timestamp span meets ``span`` (``(ts_min, ts_max)``)."""
        if span is None:
            return []
        lo, hi = span
        out = []
        for n in sorted(self.members):
            s = self.members[n]["span"]
            if n != exclude and s is not None and s[0] <= hi and lo <= s[1]:
                out.append(n)
        return out

    def rows(self, name: str):
        """The rows of member ``name`` (:func:`read_rows`; read once per run)."""
        if name not in self._frames:
            self._frames[name] = read_rows(self.members[name]["path"])
        return self._frames[name]

    def add(self, name: str, path: str, summary: dict, frame=None) -> None:
        """A part planned to land here, read from ``path`` until it is stored."""
        if not summary.get("rows_sha256"):
            return
        self.members[name] = {
            "path": path,
            "rows_sha256": summary["rows_sha256"],
            "span": summary.get("span"),
        }
        if frame is not None:
            self._frames[name] = frame
        else:
            self._frames.pop(name, None)

    def drop(self, name: str) -> None:
        """A member planned to be replaced (or removed)."""
        self.members.pop(name, None)
        self._frames.pop(name, None)

    def held_by(self, frame, span, *, exclude: str | None = None) -> tuple:
        """``(members sharing an observation with frame, their rows)``: the parts a new part with
        rows ``frame`` and span ``span`` overlaps, and the union of their rows (or ``None``)."""
        import pandas as pd

        hits = []
        for n in self.overlapping(span, exclude=exclude):
            r = self.rows(n)
            if r is not None and _shared_keys(frame, r):
                hits.append((n, r))
        if not hits:
            return [], None
        return [n for n, _ in hits], pd.concat([r for _, r in hits], ignore_index=True)

    def judge(self, frame, span, *, exclude: str | None = None) -> dict:
        """How a new part with rows ``frame`` relates to this partition.

        ``{"verdict", "parts", "differing", "revised"}``, where ``verdict`` is:

        ``new``
            no member shares an observation ``(ts, equip, role)`` with it;
        ``duplicate``
            the members it overlaps already hold every row of it (or every row but a revised
            last observation per series: an earlier, smaller poll that arrived late);
        ``supersedes``
            it holds every row of each member it overlaps -- a month that grew since its last
            send -- or every row but the last observation of each series in that member, which
            it revises (the hour that was still in progress; ``revised`` counts them);
        ``conflict``
            it overlaps a member any other way (``differing`` counts the shared observations
            whose rows differ).

        ``parts`` names the overlapped members."""
        names, union = self.held_by(frame, span, exclude=exclude)
        out = {"verdict": "new", "parts": names, "differing": 0, "revised": 0}
        if not names:
            return out
        if _contains(union, frame):
            return {**out, "verdict": "duplicate"}
        revised = [_tail_covered(frame, self.rows(n)) for n in names]
        if all(r is not None for r in revised):
            return {**out, "verdict": "supersedes", "revised": sum(revised)}
        if _tail_covered(union, frame) is not None:
            return {**out, "verdict": "duplicate"}
        return {**out, "verdict": "conflict", "differing": _differing(frame, union)}
