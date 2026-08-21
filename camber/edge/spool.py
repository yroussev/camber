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
"""

from __future__ import annotations

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
_DEFAULT_MAX_BYTES = 2 * 1024**3  # 2 GiB


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
    ):
        self.root = root
        self.max_bytes = max_bytes
        self._clock = clock or _default_clock
        os.makedirs(os.path.join(root, _PENDING), exist_ok=True)
        os.makedirs(os.path.join(root, _TMP), exist_ok=True)

    # ------------------------------------------------------------------ journal
    def _journal_path(self) -> str:
        return os.path.join(self.root, _JOURNAL)

    def _append_journal(self, record: dict) -> None:
        line = json.dumps(record, separators=(",", ":")) + "\n"
        with open(self._journal_path(), "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())

    def _replay(self) -> dict:
        """Rebuild ``{seq: SpoolEntry}`` from the journal (source of truth), oldest-first."""
        path = self._journal_path()
        live: dict = {}
        attempts: dict = {}
        if not os.path.isfile(path):
            return live
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue  # a torn trailing line -- ignore, journal stays authoritative
                op, seq = rec.get("op"), rec.get("seq")
                if op == "enqueue":
                    attempts[seq] = 0
                    live[seq] = SpoolEntry(
                        seq=seq,
                        key=rec["key"],
                        file=rec["file"],
                        content_type=rec.get("content_type", "application/octet-stream"),
                        metadata=rec.get("metadata", {}),
                        bytes=rec.get("bytes", 0),
                        enqueued_ts=rec.get("ts", ""),
                        attempts=0,
                    )
                elif op == "attempt":
                    attempts[seq] = attempts.get(seq, 0) + 1
                    if seq in live:
                        live[seq] = _with_attempts(live[seq], attempts[seq])
                elif op in ("ack", "evict"):
                    live.pop(seq, None)
        # Only entries whose payload file still exists are truly pending (guards orphaned commits).
        return {
            seq: e
            for seq, e in sorted(live.items())
            if os.path.isfile(os.path.join(self.root, _PENDING, e.file))
        }

    def _next_seq(self) -> int:
        path = self._journal_path()
        n = -1
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        seq = json.loads(line).get("seq", -1)
                    except json.JSONDecodeError:
                        continue
                    n = max(n, seq)
        return n + 1

    # ------------------------------------------------------------------ enqueue
    def enqueue(self, key: str, data: bytes, *, content_type: str, metadata=None) -> SpoolEntry:
        """Durably queue a batch: atomic payload write, then a journal commit record."""
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
        removed = self._remove_file(entry)
        if removed:
            self._append_journal({"op": "ack", "seq": entry.seq, "key": entry.key})

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
