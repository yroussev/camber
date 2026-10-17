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
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import struct

__all__ = ["DIGEST_VERSION", "INDEX_NAME", "rows_digest", "file_rows_digest", "PartitionRows"]

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


def file_rows_digest(path: str):
    """``(digest, rows)`` of one Parquet part file, or ``(None, None)`` when it has none."""
    import pyarrow.parquet as pq

    try:
        with pq.ParquetFile(path) as f:  # the file's own columns, no hive discovery
            table = f.read()
    except Exception:  # noqa: BLE001 - unreadable: no digest
        return None, None
    d = rows_digest(table)
    return (d, int(table.num_rows)) if d else (None, None)


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
        d, rows = file_rows_digest(path)
        e = {
            "rows_sha256": d,
            "rows": rows,
            "bytes": st.st_size,
            "mtime_ns": st.st_mtime_ns,
            "recorded_at": _utc_now(),
        }
        self.parts[name] = e
        self._dirty = True
        return e

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

    def record(self, name: str, digest: str | None = None, rows: int | None = None) -> None:
        """Record a part just stored here (digested from its file when ``digest`` is not given)."""
        path = os.path.join(self.dir, name)
        if digest is None:
            self._digest_part(name)
            return
        st = os.stat(path)
        self.parts[name] = {
            "rows_sha256": digest,
            "rows": rows,
            "bytes": st.st_size,
            "mtime_ns": st.st_mtime_ns,
            "recorded_at": _utc_now(),
        }
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
