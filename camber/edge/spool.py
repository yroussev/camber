"""Durable store-and-forward spool: never lose a batch because the link was down.

An edge device is intermittently connected. The forwarder enqueues each serialized batch here
*before* trying the sink, so a batch survives a connectivity loss, a process crash, or a reboot and
is replayed (oldest-first) when the link returns -- the backfill the cloud needs to stay whole.

Durability model: a **write-ahead journal** (``journal.ndjson``, append-only) is the source of
truth. ``enqueue`` writes the payload to ``pending/`` with an atomic ``os.replace`` (mirror of
:meth:`camber.store.facilities.FacilityRegistry._write`), *then* appends a commit record; a payload
without its commit record (a crash in between) is ignored on reconstruction, so the queue is never
corrupt. ``drain`` sends each pending batch and, only on a 2xx/ok, ``ack``s it (deletes payload +
appends an ack record). A failure increments the attempt count, applies a capped backoff, and stops
the cycle -- nothing is dropped. A bounded disk cap evicts the oldest batch with a logged WARNING
(explicit, audited data loss, never silent corruption). Sequence numbers are monotonic and never
reused (derived from the journal), so nothing clobbers anything.

**Compaction (0.95, provisional).** The journal only grows; :meth:`Spool.compact` rewrites it to
one ``enqueue`` record per still-pending batch (its attempt count folded in) plus a ``mark``
record that carries the sequence high-water mark, so sequence numbers stay unique after the
rewrite. The new journal is written beside the old one, fsynced, read back and checked to hold
exactly the same pending batches, and only then swapped in with an atomic ``os.replace``: a crash
at any point leaves either the old journal or the new one, never a mix, and never fewer pending
batches. Every journal write takes the spool's single-writer lock (``<spool>/_lock``, the same
advisory lock a portfolio workspace uses), so a compaction or a decommission never races a running
forwarder.

**Retirement (0.95, provisional).** A decommissioned device's spool carries ``retired.json``
(:meth:`Spool.retire`); :meth:`Spool.enqueue` refuses with :class:`SpoolRetired` from then on.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

_LOG = logging.getLogger("camber.edge")

_PENDING = "pending"
_TMP = "tmp"
_JOURNAL = "journal.ndjson"
_COMPACT_TMP = "journal.ndjson.compact"
_RETIRED = "retired.json"
_DEFAULT_MAX_BYTES = 2 * 1024**3  # 2 GiB
_DEFAULT_LOCK_TIMEOUT = 30.0


class SpoolRetired(RuntimeError):
    """The spool belongs to a decommissioned edge device; it takes no new batches."""


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


def _default_backoff(attempts: int) -> float:
    """Capped exponential backoff in seconds: 0.5, 1, 2, ... up to 60."""
    return min(60.0, 0.5 * (2 ** max(0, attempts - 1)))


@dataclass(frozen=True)
class SpoolEntry:
    """One queued batch: its cloud object key, the local payload file, and delivery metadata."""

    seq: int
    key: str
    file: str
    content_type: str
    metadata: dict
    bytes: int
    enqueued_ts: str
    attempts: int = 0


@dataclass
class CompactResult:
    """The outcome of one :meth:`Spool.compact` (provisional, 0.95).

    ``records_before`` / ``records_after`` count journal lines; ``pending`` is the number of
    batches still queued (identical before and after -- compaction never drops one). ``torn``
    counts unparseable lines dropped (a crash mid-append). ``missing_payloads`` lists committed
    batches whose payload file is gone (an ack interrupted after the delete, so already delivered);
    ``orphan_payloads`` and ``tmp_files`` list files no journal record owns (an enqueue interrupted
    before its commit). Those files are reported, never deleted.
    """

    records_before: int = 0
    records_after: int = 0
    bytes_before: int = 0
    bytes_after: int = 0
    pending: int = 0
    high_water_seq: int = -1
    torn: int = 0
    missing_payloads: list = field(default_factory=list)
    orphan_payloads: list = field(default_factory=list)
    tmp_files: list = field(default_factory=list)
    dry_run: bool = False
    compacted: bool = False


@dataclass
class DrainResult:
    """The outcome of one :meth:`Spool.drain` cycle."""

    forwarded: int = 0
    remaining: int = 0
    failed: int = 0
    keys: list = field(default_factory=list)


class Spool:
    """A durable, crash-safe FIFO of serialized batches awaiting one-way delivery to a sink."""

    def __init__(
        self,
        root: str,
        *,
        max_bytes: int = _DEFAULT_MAX_BYTES,
        clock: Callable[[], datetime] | None = None,
        lock_timeout: float = _DEFAULT_LOCK_TIMEOUT,
    ):
        self.root = root
        self.max_bytes = max_bytes
        self.lock_timeout = lock_timeout
        self._clock = clock or _default_clock
        os.makedirs(os.path.join(root, _PENDING), exist_ok=True)
        os.makedirs(os.path.join(root, _TMP), exist_ok=True)

    # ------------------------------------------------------------------ lock
    def lock(self, *, timeout: float | None = None):
        """Hold the spool's single-writer lock (``<spool>/_lock``; re-entrant in a process).

        Raises :class:`~camber.portfolio.PortfolioLocked` when another process keeps it longer
        than ``timeout`` seconds (default: the spool's ``lock_timeout``).
        """
        from ..portfolio._lock import portfolio_lock  # lazy: the lock is shared with portfolios

        return portfolio_lock(self.root, timeout=self.lock_timeout if timeout is None else timeout)

    # ------------------------------------------------------------------ journal
    def _journal_path(self) -> str:
        return os.path.join(self.root, _JOURNAL)

    def _append_journal(self, record: dict) -> None:
        line = (json.dumps(record, separators=(",", ":")) + "\n").encode("utf-8")
        with self.lock(), open(self._journal_path(), "a+b") as fh:
            # A crash mid-append can leave a torn last line with no newline; start a fresh line
            # so this record is not glued onto it (and lost with it) on replay.
            fh.seek(0, os.SEEK_END)
            if fh.tell() > 0:
                fh.seek(-1, os.SEEK_END)
                if fh.read(1) != b"\n":
                    line = b"\n" + line
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())

    def _replay(self, path: str | None = None) -> dict:
        """Rebuild ``{seq: SpoolEntry}`` from the journal (source of truth), oldest-first."""
        live, _hwm, _n, _torn = self._scan(path or self._journal_path())
        # Only entries whose payload file still exists are truly pending (guards orphaned commits).
        return {
            seq: e
            for seq, e in sorted(live.items())
            if os.path.isfile(os.path.join(self.root, _PENDING, e.file))
        }

    @staticmethod
    def _scan(path: str):
        """``(live entries, high-water seq, parsed records, torn lines)`` of a journal file."""
        live: dict = {}
        attempts: dict = {}
        hwm, n, torn = -1, 0, 0
        if not os.path.isfile(path):
            return live, hwm, n, torn
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    torn += 1
                    continue  # a torn trailing line -- ignore, journal stays authoritative
                if not isinstance(rec, dict):
                    torn += 1
                    continue
                n += 1
                op, seq = rec.get("op"), rec.get("seq")
                if isinstance(seq, int):
                    hwm = max(hwm, seq)
                if op == "enqueue" and isinstance(seq, int):
                    attempts[seq] = int(rec.get("attempts", 0) or 0)
                    live[seq] = SpoolEntry(
                        seq=seq,
                        key=rec["key"],
                        file=rec["file"],
                        content_type=rec.get("content_type", "application/octet-stream"),
                        metadata=rec.get("metadata", {}),
                        bytes=rec.get("bytes", 0),
                        enqueued_ts=rec.get("ts", ""),
                        attempts=attempts[seq],
                    )
                elif op == "attempt":
                    attempts[seq] = attempts.get(seq, 0) + 1
                    if seq in live:
                        live[seq] = _with_attempts(live[seq], attempts[seq])
                elif op in ("ack", "evict"):
                    live.pop(seq, None)
                # "mark" (the compaction high-water record) and unknown ops only raise ``hwm``
        return live, hwm, n, torn

    def _next_seq(self) -> int:
        return self._scan(self._journal_path())[1] + 1

    # ------------------------------------------------------------------ enqueue
    def enqueue(self, key: str, data: bytes, *, content_type: str, metadata=None) -> SpoolEntry:
        """Durably queue a batch: atomic payload write, then a journal commit record.

        Raises :class:`SpoolRetired` once the spool's device has been decommissioned.
        """
        with self.lock():
            return self._enqueue(key, data, content_type=content_type, metadata=metadata)

    def _enqueue(self, key: str, data: bytes, *, content_type: str, metadata=None) -> SpoolEntry:
        retired = self.retirement()
        if retired is not None:
            raise SpoolRetired(
                f"spool {self.root} was retired at {retired.get('retired_at', '?')} (device "
                f"{retired.get('device_id', '?')} decommissioned); it takes no new batches"
            )
        while self._would_exceed(len(data)):
            if self._evict_oldest() is None:
                break  # empty queue but the single batch is still larger than the cap; keep it
        seq = self._next_seq()
        fname = f"{seq:012d}.blob"
        tmp = os.path.join(self.root, _TMP, fname)
        final = os.path.join(self.root, _PENDING, fname)
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, final)  # atomic within the same filesystem
        entry = SpoolEntry(
            seq=seq,
            key=key,
            file=fname,
            content_type=content_type,
            metadata=dict(metadata or {}),
            bytes=len(data),
            enqueued_ts=self._clock().isoformat(),
        )
        self._append_journal(
            {
                "op": "enqueue",
                "seq": seq,
                "key": key,
                "file": fname,
                "content_type": content_type,
                "metadata": entry.metadata,
                "bytes": entry.bytes,
                "ts": entry.enqueued_ts,
            }
        )
        return entry

    def _would_exceed(self, incoming: int) -> bool:
        _, used = self.depth()
        return used + incoming > self.max_bytes

    def _evict_oldest(self) -> SpoolEntry | None:
        pend = self.pending()
        if not pend:
            return None
        victim = pend[0]
        self._remove_file(victim)
        self._append_journal({"op": "evict", "seq": victim.seq, "key": victim.key})
        _LOG.warning(
            "edge.spool.evict seq=%d key=%s bytes=%d — spool over %d-byte cap, oldest dropped",
            victim.seq,
            victim.key,
            victim.bytes,
            self.max_bytes,
        )
        return victim

    # ------------------------------------------------------------------ inspect
    def pending(self) -> list[SpoolEntry]:
        """Pending batches, oldest-first (reconstructed from the journal + present payloads)."""
        return list(self._replay().values())

    def depth(self) -> tuple[int, int]:
        """``(count, bytes)`` currently queued."""
        pend = self.pending()
        return len(pend), sum(e.bytes for e in pend)

    # ------------------------------------------------------------------ drain
    def drain(
        self,
        sink,
        *,
        max_batches: int | None = None,
        backoff: Callable[[int], float] | None = None,
        _sleep: Callable[[float], None] = time.sleep,
    ) -> DrainResult:
        """Send pending batches oldest-first; ack on success, stop+backoff on the first failure."""
        backoff = backoff or _default_backoff
        result = DrainResult()
        pend = self.pending()
        for i, entry in enumerate(pend):
            if max_batches is not None and i >= max_batches:
                break
            payload = self._read_payload(entry)
            if payload is None:
                continue
            try:
                resp = sink.put(
                    entry.key, payload, content_type=entry.content_type, metadata=entry.metadata
                )
                ok = bool(resp.get("ok", True))
            except Exception as exc:  # a network/transport error -- keep the batch, back off
                _LOG.warning(
                    "edge.spool.drain send failed seq=%d key=%s: %s", entry.seq, entry.key, exc
                )
                ok = False
            if ok:
                self.ack(entry)
                result.forwarded += 1
                result.keys.append(entry.key)
            else:
                self._append_journal({"op": "attempt", "seq": entry.seq})
                result.failed += 1
                _sleep(backoff(entry.attempts + 1))
                break  # leave the rest queued; the next cycle retries
        result.remaining = self.depth()[0]
        return result

    def ack(self, entry: SpoolEntry) -> None:
        """Mark a batch delivered: delete its payload and append an ack record (idempotent)."""
        with self.lock():
            removed = self._remove_file(entry)
            if removed:
                self._append_journal({"op": "ack", "seq": entry.seq, "key": entry.key})

    # ------------------------------------------------------------------ compaction
    def compact(self, *, dry_run: bool = False) -> CompactResult:
        """Rewrite the journal to the still-pending batches only (provisional, 0.95).

        Crash-safe and loss-free: the compacted journal is written to a side file, fsynced, read
        back and compared with the live queue -- same sequence numbers, keys, payload files,
        metadata and attempt counts -- and only then swapped in with an atomic ``os.replace``.
        A crash before the swap leaves the old journal (the side file is overwritten by the next
        compaction); after it, the new one. A ``mark`` record keeps the sequence high-water mark,
        so sequence numbers are never reused. Takes the spool lock. ``dry_run`` only reports.
        """
        with self.lock():
            jpath = self._journal_path()
            live, hwm, n_before, torn = self._scan(jpath)
            pending = self._replay()
            present = set(os.listdir(os.path.join(self.root, _PENDING)))
            missing = sorted(e.file for s, e in live.items() if s not in pending)
            owned = {e.file for e in live.values()}
            res = CompactResult(
                records_before=n_before,
                bytes_before=os.path.getsize(jpath) if os.path.isfile(jpath) else 0,
                pending=len(pending),
                high_water_seq=hwm,
                torn=torn,
                missing_payloads=missing,
                orphan_payloads=sorted(present - owned),
                tmp_files=sorted(os.listdir(os.path.join(self.root, _TMP))),
                dry_run=dry_run,
            )
            lines = [_enqueue_record(e) for e in pending.values()]
            if hwm >= 0:
                lines.append({"op": "mark", "seq": hwm, "ts": self._clock().isoformat()})
            body = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in lines)
            res.records_after = len(lines)
            res.bytes_after = len(body.encode("utf-8"))
            if dry_run:
                return res
            tmp = os.path.join(self.root, _COMPACT_TMP)
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(body)
                fh.flush()
                os.fsync(fh.fileno())
            # read the side file back: it must hold exactly the pending queue, or nothing changes
            check = self._replay(tmp)
            if [_identity(e) for e in check.values()] != [_identity(e) for e in pending.values()]:
                os.remove(tmp)
                raise RuntimeError(  # pragma: no cover - defensive; the rewrite is deterministic
                    "spool compaction check failed: the rewritten journal does not hold the same "
                    "pending batches; the journal was left unchanged"
                )
            if self._scan(tmp)[1] != hwm:
                os.remove(tmp)
                raise RuntimeError(  # pragma: no cover - defensive
                    "spool compaction check failed: the sequence high-water mark changed"
                )
            os.replace(tmp, jpath)  # atomic: the old journal or the new one, never a mix
            _fsync_dir(self.root)
            res.compacted = True
            _LOG.info(
                "edge.spool.compact records=%d->%d bytes=%d->%d pending=%d",
                res.records_before,
                res.records_after,
                res.bytes_before,
                res.bytes_after,
                res.pending,
            )
            return res

    # ------------------------------------------------------------------ retirement
    def retirement(self) -> dict | None:
        """The ``retired.json`` record if the device was decommissioned, else ``None``."""
        path = os.path.join(self.root, _RETIRED)
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            return {"retired_at": "?", "unreadable": True}  # present but torn: still retired
        return data if isinstance(data, dict) else {"retired_at": "?", "unreadable": True}

    def retire(self, record: dict) -> dict:
        """Write ``retired.json`` atomically (tmp + fsync + replace); enqueue refuses from then on.

        Called by :func:`camber.edge.decommission.decommission`; takes the spool lock.
        """
        with self.lock():
            path = os.path.join(self.root, _RETIRED)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(record, fh, indent=2, sort_keys=True, default=str)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
            _fsync_dir(self.root)
        return record

    # ------------------------------------------------------------------ helpers
    def _read_payload(self, entry: SpoolEntry) -> bytes | None:
        path = os.path.join(self.root, _PENDING, entry.file)
        try:
            with open(path, "rb") as fh:
                return fh.read()
        except OSError:
            return None

    def _remove_file(self, entry: SpoolEntry) -> bool:
        path = os.path.join(self.root, _PENDING, entry.file)
        try:
            os.remove(path)
            return True
        except FileNotFoundError:
            return False


def _enqueue_record(e: SpoolEntry) -> dict:
    rec = {
        "op": "enqueue",
        "seq": e.seq,
        "key": e.key,
        "file": e.file,
        "content_type": e.content_type,
        "metadata": e.metadata,
        "bytes": e.bytes,
        "ts": e.enqueued_ts,
    }
    if e.attempts:
        rec["attempts"] = e.attempts
    return rec


def _identity(e: SpoolEntry) -> tuple:
    return (
        e.seq,
        e.key,
        e.file,
        e.content_type,
        json.dumps(e.metadata, sort_keys=True),
        e.bytes,
        e.enqueued_ts,
        e.attempts,
    )


def _fsync_dir(path: str) -> None:
    """fsync a directory so a rename in it is durable (a no-op where unsupported)."""
    if not hasattr(os, "O_DIRECTORY"):  # pragma: no cover - Windows
        return
    with contextlib.suppress(OSError):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _with_attempts(entry: SpoolEntry, attempts: int) -> SpoolEntry:
    return SpoolEntry(
        seq=entry.seq,
        key=entry.key,
        file=entry.file,
        content_type=entry.content_type,
        metadata=entry.metadata,
        bytes=entry.bytes,
        enqueued_ts=entry.enqueued_ts,
        attempts=attempts,
    )
