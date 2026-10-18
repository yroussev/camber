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

**Permanent rejections (0.108, #245).** A send that fails with a 4xx answer (other than 401,
403, 408, 425 and 429) or a validation error is permanent: retrying will not help. It is counted
as a rejection, the drain moves on to the next batch instead of stopping, and after
``dead_letter_after`` rejections (5 by default) the batch moves to ``dead/``: its payload and a
``<file>.json`` record (key, metadata, attempts, the last error, the time) are kept, a ``dead``
journal record and a WARNING log line are written, and :meth:`Spool.requeue_dead` sends it again
once the cause is fixed. A transient failure (a network error, a 5xx) still stops the cycle and
backs off. So does a 401 / 403 (0.108): the sink refuses the device's credentials, a fleet-wide
problem, never a bad batch; it is logged as a loud WARNING and recorded in ``auth_failing.json``
(:meth:`Spool.auth_failing`) until a batch is delivered again.

**No duplicate copies (0.108, #246).** :meth:`Spool.enqueue` skips a batch whose key and content
sha256 are already pending (or dead-lettered): a forwarder that re-reads the same window while
offline queues each distinct batch once.

**Retirement (0.95, provisional).** A decommissioned device's spool carries ``retired.json``
(:meth:`Spool.retire`); :meth:`Spool.enqueue` refuses with :class:`SpoolRetired` from then on.
"""

from __future__ import annotations

import contextlib
import hashlib
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
_AUTH = "auth_failing.json"  # 0.108: the sink refuses our credentials (401 / 403) since ...
_DEAD = "dead"
_DEFAULT_DEAD_LETTER_AFTER = 5
_ERROR_CHARS = 500
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
    rejects: int = 0  # 0.108 (#245): permanent rejections (a 4xx, a validation error)
    last_error: str = ""


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
    rejected: int = 0  # 0.108 (#245): permanent rejections this cycle (the drain went on)
    dead: list = field(default_factory=list)  # keys moved to dead/ this cycle
    auth_failed: bool = False  # 0.108: the cycle stopped on a 401 / 403 (see Spool.auth_failing)


def read_retirement(root: str) -> dict | None:
    """The ``retired.json`` record of the spool at ``root``, else ``None`` (reads only).

    Unlike constructing a :class:`Spool`, this creates nothing, so ``edge selftest`` can check a
    configured spool without touching it (0.108, #242).
    """
    path = os.path.join(root, _RETIRED)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"retired_at": "?", "unreadable": True}  # present but torn: still retired
    return data if isinstance(data, dict) else {"retired_at": "?", "unreadable": True}


class Spool:
    """A durable, crash-safe FIFO of serialized batches awaiting one-way delivery to a sink."""

    def __init__(
        self,
        root: str,
        *,
        max_bytes: int = _DEFAULT_MAX_BYTES,
        clock: Callable[[], datetime] | None = None,
        lock_timeout: float = _DEFAULT_LOCK_TIMEOUT,
        dead_letter_after: int = _DEFAULT_DEAD_LETTER_AFTER,
    ):
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ValueError(f"spool max_bytes must be a positive integer, got {max_bytes!r}")
        if isinstance(dead_letter_after, bool) or int(dead_letter_after) < 1:
            raise ValueError(f"dead_letter_after must be at least 1, got {dead_letter_after!r}")
        self.root = root
        self.max_bytes = max_bytes
        self.dead_letter_after = int(dead_letter_after)
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
        rejects: dict = {}
        errors: dict = {}
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
                    rejects[seq] = int(rec.get("rejects", 0) or 0)
                    errors[seq] = str(rec.get("last_error", "") or "")
                    live[seq] = SpoolEntry(
                        seq=seq,
                        key=rec["key"],
                        file=rec["file"],
                        content_type=rec.get("content_type", "application/octet-stream"),
                        metadata=rec.get("metadata", {}),
                        bytes=rec.get("bytes", 0),
                        enqueued_ts=rec.get("ts", ""),
                        attempts=attempts[seq],
                        rejects=rejects[seq],
                        last_error=errors[seq],
                    )
                elif op == "attempt":
                    attempts[seq] = attempts.get(seq, 0) + 1
                    if rec.get("permanent"):
                        rejects[seq] = rejects.get(seq, 0) + 1
                    if rec.get("error"):
                        errors[seq] = str(rec["error"])
                    if seq in live:
                        live[seq] = _with_attempts(
                            live[seq],
                            attempts[seq],
                            rejects=rejects.get(seq, 0),
                            last_error=errors.get(seq, ""),
                        )
                elif op in ("ack", "evict", "dead"):
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

    def _refuse_if_retired(self) -> None:
        retired = self.retirement()
        if retired is not None:
            raise SpoolRetired(
                f"spool {self.root} was retired at {retired.get('retired_at', '?')} (device "
                f"{retired.get('device_id', '?')} decommissioned); it takes no new batches"
            )

    def _enqueue(self, key: str, data: bytes, *, content_type: str, metadata=None) -> SpoolEntry:
        self._refuse_if_retired()
        sha = hashlib.sha256(data).hexdigest()
        same = self._same_batch(key, data, sha)
        if same is not None:  # 0.108 (#246): one copy of each batch, however often it is read
            _LOG.info(
                "edge.spool.enqueue skipped key=%s: the same batch is already %s (seq %d)",
                key,
                "dead-lettered" if same.file.startswith(_DEAD + "/") else "pending",
                same.seq,
            )
            return same
        return self._enqueue_new(key, data, content_type=content_type, metadata=metadata)

    def _enqueue_new(self, key: str, data: bytes, *, content_type: str, metadata=None):
        self._refuse_if_retired()
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

    def _same_batch(self, key: str, data: bytes, sha: str) -> SpoolEntry | None:
        """A pending (or dead-lettered) entry with this key and these exact bytes, else None."""
        for e in self.pending():
            if e.key == key and e.bytes == len(data):
                payload = self._read_payload(e)
                if payload is not None and hashlib.sha256(payload).hexdigest() == sha:
                    return e
        for rec in self.dead_letters():
            if rec.get("key") == key and rec.get("sha256") == sha:
                return SpoolEntry(
                    seq=int(rec.get("seq", -1)),
                    key=key,
                    file=f"{_DEAD}/{rec.get('file', '')}",
                    content_type=rec.get("content_type", "application/octet-stream"),
                    metadata=rec.get("metadata", {}),
                    bytes=int(rec.get("bytes", 0) or 0),
                    enqueued_ts=rec.get("enqueued_ts", ""),
                    attempts=int(rec.get("attempts", 0) or 0),
                    rejects=int(rec.get("rejects", 0) or 0),
                    last_error=str(rec.get("error", "")),
                )
        return None

    def _would_exceed(self, incoming: int) -> bool:
        _, used = self.depth()
        used += sum(int(r.get("bytes", 0) or 0) for r in self.dead_letters())
        return used + incoming > self.max_bytes

    def _evict_oldest(self):
        """Drop the oldest dead letter (already rejected) or else the oldest pending batch."""
        dead = self.dead_letters()
        if dead:
            victim = dead[0]
            d = os.path.join(self.root, _DEAD)
            blob = os.path.join(d, victim["file"])
            for path in (blob, blob + ".json"):
                with contextlib.suppress(FileNotFoundError):
                    os.remove(path)
            _LOG.warning(
                "edge.spool.evict dead key=%s bytes=%s — spool over %d-byte cap, oldest dead "
                "letter dropped",
                victim.get("key"),
                victim.get("bytes"),
                self.max_bytes,
            )
            return victim
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

    def stuck(self) -> list[SpoolEntry]:
        """Pending batches that failed at least one delivery attempt, oldest-first (0.108)."""
        return [e for e in self.pending() if e.attempts > 0]

    def dead_letters(self) -> list[dict]:
        """The dead-lettered batches (0.108, #245): each one's ``dead/<file>.json`` record.

        Only records whose payload is present count (a crash between the record and the move
        leaves the batch pending)."""
        d = os.path.join(self.root, _DEAD)
        if not os.path.isdir(d):
            return []
        out = []
        for name in sorted(os.listdir(d)):
            if not name.endswith(".json"):
                continue
            blob = name[: -len(".json")]
            if not os.path.isfile(os.path.join(d, blob)):
                continue
            try:
                with open(os.path.join(d, name), encoding="utf-8") as fh:
                    rec = json.load(fh)
            except (OSError, ValueError):
                rec = {"file": blob, "error": "unreadable dead-letter record"}
            if isinstance(rec, dict):
                rec.setdefault("file", blob)
                out.append(rec)
        return out

    def requeue_dead(self, keys=None) -> list[SpoolEntry]:
        """Queue dead-lettered batches again (all, or those whose key is in ``keys``).

        Each becomes a new pending batch with a fresh attempt count; its dead-letter payload and
        record are removed only after the new batch is committed, and a ``requeue`` journal record
        names both sequence numbers. Takes the spool lock; a retired spool refuses.
        """
        wanted = None if keys is None else set(keys)
        out = []
        with self.lock():
            d = os.path.join(self.root, _DEAD)
            for rec in self.dead_letters():
                if wanted is not None and rec.get("key") not in wanted:
                    continue
                blob = os.path.join(d, rec["file"])
                with open(blob, "rb") as fh:
                    data = fh.read()
                entry = self._enqueue_new(
                    rec["key"],
                    data,
                    content_type=rec.get("content_type", "application/octet-stream"),
                    metadata=rec.get("metadata") or {},
                )
                self._append_journal(
                    {
                        "op": "requeue",
                        "seq": entry.seq,
                        "from_seq": rec.get("seq"),
                        "key": rec["key"],
                    }
                )
                for path in (blob + ".json", blob):
                    with contextlib.suppress(FileNotFoundError):
                        os.remove(path)
                _fsync_dir(d)
                _LOG.info(
                    "edge.spool.requeue key=%s seq=%s->%d", rec["key"], rec.get("seq"), entry.seq
                )
                out.append(entry)
        return out

    def _dead_letter(self, entry: SpoolEntry, error: str, rejects: int) -> None:
        """Move a permanently rejected batch to ``dead/`` (record first, then payload, journal)."""
        with self.lock():
            d = os.path.join(self.root, _DEAD)
            os.makedirs(d, exist_ok=True)
            src = os.path.join(self.root, _PENDING, entry.file)
            payload = self._read_payload(entry)
            if payload is None:
                return
            record = {
                "seq": entry.seq,
                "key": entry.key,
                "file": entry.file,
                "content_type": entry.content_type,
                "metadata": entry.metadata,
                "bytes": entry.bytes,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "enqueued_ts": entry.enqueued_ts,
                "attempts": entry.attempts + 1,
                "rejects": rejects,
                "error": error,
                "dead_at": self._clock().isoformat(),
            }
            tmp = os.path.join(d, entry.file + ".json.tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(record, fh, indent=2, sort_keys=True, default=str)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, os.path.join(d, entry.file + ".json"))
            os.replace(src, os.path.join(d, entry.file))
            _fsync_dir(d)
            self._append_journal(
                {
                    "op": "dead",
                    "seq": entry.seq,
                    "key": entry.key,
                    "rejects": rejects,
                    "error": error,
                    "ts": record["dead_at"],
                }
            )
        _LOG.warning(
            "edge.spool.dead seq=%d key=%s rejects=%d error=%s — rejected permanently; kept in "
            "%s (requeue it once the cause is fixed)",
            entry.seq,
            entry.key,
            rejects,
            error,
            d,
        )

    # ------------------------------------------------------------------ drain
    def drain(
        self,
        sink,
        *,
        max_batches: int | None = None,
        backoff: Callable[[int], float] | None = None,
        _sleep: Callable[[float], None] = time.sleep,
    ) -> DrainResult:
        """Send pending batches oldest-first; ack on success, stop+backoff on the first failure.

        A permanent rejection (0.108, #245: a 4xx answer, a validation error; see
        :func:`camber.edge.sink.is_permanent_error`) does not stop the cycle: it is counted, the
        drain goes on with the next batch, and after ``dead_letter_after`` rejections the batch
        moves to ``dead/``."""
        from .sink import is_auth_error, is_auth_status, is_permanent_error, is_permanent_status

        backoff = backoff or _default_backoff
        result = DrainResult()
        pend = self.pending()
        for i, entry in enumerate(pend):
            if max_batches is not None and i >= max_batches:
                break
            payload = self._read_payload(entry)
            if payload is None:
                continue
            error, permanent, auth = "", False, False
            try:
                resp = sink.put(
                    entry.key, payload, content_type=entry.content_type, metadata=entry.metadata
                )
                resp = resp if isinstance(resp, dict) else {}
                ok = bool(resp.get("ok", True))
                if not ok:
                    error = f"sink answered status {resp.get('status', '?')}"
                    auth = is_auth_status(resp.get("status"))
                    permanent = not auth and (
                        bool(resp.get("permanent")) or is_permanent_status(resp.get("status"))
                    )
            except Exception as exc:  # a network/transport error -- keep the batch, back off
                ok = False
                error = f"{type(exc).__name__}: {exc}"[:_ERROR_CHARS]
                auth = is_auth_error(exc)
                permanent = not auth and is_permanent_error(exc)
                _LOG.warning(
                    "edge.spool.drain send failed seq=%d key=%s%s: %s",
                    entry.seq,
                    entry.key,
                    " (permanent)" if permanent else "",
                    exc,
                )
            if ok:
                self.ack(entry)
                self._clear_auth_failing()
                result.forwarded += 1
                result.keys.append(entry.key)
                continue
            result.failed += 1
            if permanent:
                rejects = entry.rejects + 1
                result.rejected += 1
                if rejects >= self.dead_letter_after:
                    self._dead_letter(entry, error, rejects)
                    result.dead.append(entry.key)
                else:
                    self._append_journal(
                        {"op": "attempt", "seq": entry.seq, "permanent": True, "error": error}
                    )
                continue  # a rejected batch never blocks the ones behind it
            rec = {"op": "attempt", "seq": entry.seq, "error": error}
            if auth:  # 0.108: credentials or configuration, fleet-wide: stop, never dead-letter
                rec["auth"] = True
                since = self._note_auth_failing(error)
                result.auth_failed = True
                _LOG.warning(
                    "edge.spool.auth AUTH FAILING since %s: the sink refuses this device's "
                    "credentials (%s) -- check the presigned URL template, broker or keys; "
                    "batches stay queued and are retried",
                    since,
                    error,
                )
            self._append_journal(rec)
            _sleep(backoff(entry.attempts + 1))
            break  # leave the rest queued; the next cycle retries
        result.remaining = self.depth()[0]
        return result

    def auth_failing(self) -> dict | None:
        """``{"since", "last", "failures", "error"}`` while the sink answers 401 / 403 to this
        device (0.108), else ``None``; cleared by the next delivered batch."""
        try:
            with open(os.path.join(self.root, _AUTH), encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _note_auth_failing(self, error: str) -> str:
        now = self._clock().isoformat()
        rec = self.auth_failing() or {"since": now, "failures": 0}
        rec.update(last=now, failures=int(rec.get("failures", 0)) + 1, error=error)
        path = os.path.join(self.root, _AUTH)
        with self.lock():
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(rec, fh, indent=2, sort_keys=True)
                fh.write("\n")
            os.replace(tmp, path)
        return str(rec["since"])

    def _clear_auth_failing(self) -> None:
        path = os.path.join(self.root, _AUTH)
        if os.path.exists(path):
            with self.lock(), contextlib.suppress(FileNotFoundError):
                os.remove(path)
            _LOG.info("edge.spool.auth the sink accepts this device's credentials again")

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
        return read_retirement(self.root)

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
    if e.rejects:
        rec["rejects"] = e.rejects
    if e.last_error:
        rec["last_error"] = e.last_error
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
        e.rejects,
        e.last_error,
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


def _with_attempts(
    entry: SpoolEntry, attempts: int, *, rejects: int = 0, last_error: str = ""
) -> SpoolEntry:
    return SpoolEntry(
        seq=entry.seq,
        key=entry.key,
        file=entry.file,
        content_type=entry.content_type,
        metadata=entry.metadata,
        bytes=entry.bytes,
        enqueued_ts=entry.enqueued_ts,
        attempts=attempts,
        rejects=rejects,
        last_error=last_error,
    )
