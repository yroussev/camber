"""The forwarder: read BAS trends read-only, map + quality-gate, serialize, store-and-forward.

One `poll_once` reads a window from any :class:`~camber.ingest.base.SourceAdapter` (historian-first
per :mod:`docs/SECURITY`), turns each raw ``<equip>_<measure>`` token into a vendor-neutral
:class:`~camber.model.roles.Role` via a :class:`~camber.model.mapping.MappingProvider`, runs
:func:`camber.ingest.quality.assess` (report-only -- it never mutates the data), melts to the
store's long shape with :func:`camber.store.role_frame_to_long`, serializes one Parquet part per
``year=/month=`` partition (0.95; one per ``year=`` before) directly into the
:class:`~camber.store.ParquetStore` Hive layout -- in the store's canonical part layout since
0.107 (#142), so the same rows give the same bytes and key -- enqueues it on the
durable :class:`~camber.edge.spool.Spool`, and drains the spool through the one-way ``Sink``.

Nothing here listens, binds, or writes to the BAS -- it only reads the source and pushes data out.
All FDD/M&V analysis runs in the cloud on the landed store, which reads these part files with the
*existing* ``ParquetStore.read_long`` / ``ReadAPI`` and no transform.
"""

from __future__ import annotations

import hashlib
import io
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ..ingest.quality import assess
from ..store.facilities import require_facility_id
from ..store.parquet_store import _canonical_table, _write_part, role_frame_to_long
from .row_digest import rows_digest

_LOG = logging.getLogger("camber.edge")

_SCHEMA_VERSION = 1
_LONG_COLS = ["ts", "equip", "equip_class", "role", "value"]
_CONTENT_TYPE = {"parquet": "application/vnd.apache.parquet", "ndjson": "application/x-ndjson"}
_EXT = {"parquet": "parquet", "ndjson": "ndjson"}
# 0.107 (#142): "canonical" writes the store's 0.103 part layout; "legacy" the pre-0.107 bytes
# (pyarrow's default writer, pandas metadata, the batch's row order) for a cloud landing that
# predates the row-digest check.
PART_LAYOUTS = ("canonical", "legacy")


def _default_equip_of(token: str) -> str:
    """Split ``<equip>_<measure>`` on the last underscore; the equip half (or the whole token)."""
    return token.rsplit("_", 1)[0] if "_" in token else token


def _measure_of(token: str) -> str:
    return token.rsplit("_", 1)[1] if "_" in token else token


@dataclass
class BatchResult:
    """The outcome of one :meth:`Forwarder.poll_once`."""

    facility_id: str
    rows: int
    window: tuple
    quality: dict
    keys: list = field(default_factory=list)
    spooled: int = 0
    forwarded: int = 0
    already_spooled: int = 0  # 0.108 (#246): batches skipped, the same bytes already queued
    rejected: int = 0  # 0.108 (#245): permanent rejections this cycle
    dead: list = field(default_factory=list)  # keys dead-lettered this cycle


class Forwarder:
    """Read-only in, map + quality-gate, Parquet, spool, one-way sink; analysis is cloud-side."""

    def __init__(
        self,
        source,
        sink,
        *,
        facility_id: str,
        spool,
        mapping=None,
        equip_of: Callable[[str], str] | None = None,
        equip_class_of: Callable[[str], str] | None = None,
        resample: str = "1h",
        quality: bool = True,
        wire_format: str = "parquet",
        part_layout: str = "canonical",
        clock: Callable[[], datetime] | None = None,
    ):
        if wire_format not in _CONTENT_TYPE:
            raise ValueError(f"wire_format must be 'parquet' or 'ndjson', got {wire_format!r}")
        if part_layout not in PART_LAYOUTS:
            raise ValueError(f"part_layout must be 'canonical' or 'legacy', got {part_layout!r}")
        require_facility_id(facility_id)
        self.source = source
        self.sink = sink
        self.facility_id = facility_id
        self.spool = spool
        self.mapping = mapping
        self.equip_of = equip_of or _default_equip_of
        self.equip_class_of = equip_class_of
        self.resample = resample
        self.quality = quality
        self.wire_format = wire_format
        self.part_layout = part_layout
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    # ------------------------------------------------------------------ serialize
    def _serialize(self, part: pd.DataFrame) -> tuple:
        """``(bytes, rows digest or None)`` of one partition's rows in the wire format.

        Parquet parts are in the store's canonical layout (0.107, #142; ``part_layout="legacy"``
        keeps the pre-0.107 bytes). NDJSON is unchanged: it is not a store format."""
        if self.wire_format == "ndjson":
            out = part.copy()
            out["ts"] = out["ts"].astype(str)
            return out.to_json(orient="records", lines=True).encode("utf-8"), None
        buf = io.BytesIO()
        if self.part_layout == "canonical":
            table = _canonical_table(part)
            _write_part(table, buf)
        else:
            table = pa.Table.from_pandas(part, preserve_index=False)
            pq.write_table(table, buf)
        return buf.getvalue(), rows_digest(table)

    def _role_wide(self, frame: pd.DataFrame):
        """Group source columns into per-equip role-wide frames; return (by_equip, unmapped)."""
        by_equip: dict = {}
        unmapped = 0
        for token in frame.columns:
            equip = self.equip_of(token)
            if self.mapping is not None:
                role = self.mapping.role_of(token)
                if role is None:
                    unmapped += 1
                    continue
                role_key = role
            else:
                role_key = _measure_of(token)
            by_equip.setdefault(equip, {})[role_key] = token
        return by_equip, unmapped

    # ------------------------------------------------------------------ the poll
    def poll_once(self, names=None, *, since=None, until=None) -> BatchResult:
        """Read one window read-only, forward it one-way, return what was spooled/sent."""
        names = list(names) if names is not None else list(self.source.point_names())
        frame = self.source.load_points(names, resample=self.resample)
        if frame is None or len(frame) == 0:
            return BatchResult(self.facility_id, 0, ("", ""), {})
        frame = frame.sort_index()
        if since is not None:
            frame = frame[frame.index >= pd.Timestamp(since)]
        if until is not None:
            frame = frame[frame.index <= pd.Timestamp(until)]
        if len(frame) == 0:
            return BatchResult(self.facility_id, 0, ("", ""), {})

        by_equip, unmapped = self._role_wide(frame)
        if unmapped:
            _LOG.info("edge.forward unmapped_points=%d", unmapped)

        longs = []
        n_pts, cov_sum, min_score, worst = 0, 0.0, 1.0, None
        for equip, rolemap in by_equip.items():
            role_wide = pd.DataFrame(
                {rk: frame[tok] for rk, tok in rolemap.items()}, index=frame.index
            )
            if self.quality:
                for tok in rolemap.values():
                    rep = assess(frame[tok], expected_freq=self.resample).as_dict()
                    n_pts += 1
                    cov_sum += rep["coverage"]
                    if rep["score"] < min_score:
                        min_score, worst = rep["score"], tok
            eq_class = self.equip_class_of(equip) if self.equip_class_of else ""
            longs.append(role_frame_to_long(role_wide, equip=equip, equip_class=eq_class))

        long = pd.concat(longs, ignore_index=True) if longs else pd.DataFrame(columns=_LONG_COLS)
        quality_summary = {
            "n_points": n_pts,
            "mean_coverage": round(cov_sum / n_pts, 4) if n_pts else None,
            "min_score": round(min_score, 4) if n_pts else None,
            "worst_point": worst,
        }
        window = (str(frame.index.min()), str(frame.index.max()))

        keys: list = []
        queued = {e.key for e in self.spool.pending()} | {
            r.get("key") for r in self.spool.dead_letters()
        }
        already = 0
        if not long.empty:
            long = long.copy()
            ts = pd.to_datetime(long["ts"])
            long["_year"], long["_month"] = ts.dt.year, ts.dt.month
            # one object per year=/month= partition -- the store's layout since 0.95, so
            # month-by-month retention never has to split an edge part (landing still reads the
            # year-only keys older forwarders wrote)
            for (year, month), grp in long.groupby(["_year", "_month"]):
                part = grp[_LONG_COLS].reset_index(drop=True)
                data, rows_sha = self._serialize(part)
                sha = hashlib.sha256(data).hexdigest()
                key = (
                    f"facility_id={self.facility_id}/year={int(year)}/month={int(month)}/"
                    f"part-{sha[:16]}.{_EXT[self.wire_format]}"
                )
                manifest = {
                    "facility_id": self.facility_id,
                    "year": int(year),
                    "month": int(month),
                    "window": list(window),
                    "rows": int(len(part)),
                    "roles": sorted(part["role"].unique().tolist()),
                    "equips": sorted(part["equip"].unique().tolist()),
                    "quality": quality_summary,
                    "content_sha256": sha,
                    "wire_format": self.wire_format,
                    # 0.107 (#142): audit only -- the landing computes its own digest
                    "part_layout": self.part_layout if self.wire_format == "parquet" else None,
                    "rows_sha256": rows_sha,
                    "schema_version": _SCHEMA_VERSION,
                    "built_at": self._clock().isoformat(),
                }
                if key in queued:
                    already += 1  # 0.108 (#246): the spool skips the second copy
                self.spool.enqueue(
                    key, data, content_type=_CONTENT_TYPE[self.wire_format], metadata=manifest
                )
                keys.append(key)

        drained = self.spool.drain(self.sink)
        _LOG.info(
            "edge.forward facility=%s rows=%d parts=%d already_spooled=%d forwarded=%d "
            "rejected=%d dead=%d spool_remaining=%d",
            self.facility_id,
            len(long),
            len(keys),
            already,
            drained.forwarded,
            drained.rejected,
            len(drained.dead),
            drained.remaining,
        )
        return BatchResult(
            facility_id=self.facility_id,
            rows=int(len(long)),
            window=window,
            quality=quality_summary,
            keys=keys,
            spooled=len(keys) - already,
            forwarded=drained.forwarded,
            already_spooled=already,
            rejected=drained.rejected,
            dead=list(drained.dead),
        )

    def run(self, interval: float, *, iterations: int | None = None, _sleep=None) -> None:
        """Daemon loop: ``poll_once`` then sleep ``interval`` s (``iterations`` bounds tests)."""
        import time

        from .spool import SpoolRetired

        sleep = _sleep or time.sleep
        i = 0
        while iterations is None or i < iterations:
            try:
                self.poll_once()
            except SpoolRetired:  # a decommissioned device stops; it never retries forever
                _LOG.error("edge.forward spool retired (device decommissioned); stopping")
                raise
            except Exception:  # a poll failure must not kill the daemon; log and continue
                _LOG.exception("edge.forward poll_once failed; will retry next interval")
            i += 1
            if iterations is not None and i >= iterations:
                break
            sleep(interval)
