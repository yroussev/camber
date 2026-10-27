"""Per-facility write fences, so a reader that takes no lock notices a concurrent replace (#306).

Reads take no lock. A writer that replaces or removes a facility's files -- the partition swap of
a forced ``camber datasets ingest``, a drop, a prune, a retention roll-up, a partition migration --
changes what a reader sees in more than one step: a swap renames the old partition aside and then
the new one in, so for a moment the facility is absent, and a reader listing its directory across
either rename sees part of it. Each such step runs inside :func:`fence` for its facility, which
works like a sequence lock::

    <store>/_fence/<facility_id>    "busy <pid> <started> <token>" while the writer changes the
                                     files, then "idle <token>", with a fresh random token each time

A reader takes a :func:`snapshot` of the facilities it reads before it starts and checks it with
:func:`changed` when it is done. A writer still busy (after a short wait) or a token that changed
in between means the reader may have seen a mix, so it fails with a retryable
:class:`~camber.store.parquet_store.StoreReadError` (the capability error ``unavailable``) instead
of answering from a partial facility. The catalog generation is replaced inside the fence, before
the writer reports idle, so a catalog a reader rebuilt mid-swap is never served afterwards.

Random tokens, not a counter, so a lost file cannot make an old snapshot look current (no ABA).
A busy marker of a process that is gone (a writer killed mid-swap) does not block readers, nor
does one whose pid now belongs to another process: ``<started>`` is when the writer started, and
a live pid that started at another time is not that writer (#317). The next writer to take the
store lock resets such a marker (:func:`recover`), and its recovery settles the partition. The
directory starts with ``_``, so dataset discovery, ``ParquetStore.facilities()`` and the swap
recovery never read it as data.
"""

from __future__ import annotations

import contextlib
import os
import struct
import sys
import tempfile
import threading
import time

FENCE_DIR = "_fence"
_PARTITION = "facility_id="
_STAGING = "_staging"
# a store's own files: any one marks a directory of facility_id= partitions as a store
_STORE_FILES = (FENCE_DIR, "_facilities.json", "_lock", "_catalog.gen", "_catalog.json")
_WAIT = 1.0  # seconds a reader waits for a busy writer before it gives up (retryable)
_POLL = 0.02

_DEPTH: dict = {}  # (root, fid) -> [owning thread, how many fences it holds there (they nest)]
_DEPTH_LOCK = threading.Lock()


def _safe(fid) -> bool:
    return (
        isinstance(fid, str)
        and bool(fid)
        and "/" not in fid
        and os.sep not in fid
        and not fid.startswith(".")
    )


def _started(pid: int):
    """When process ``pid`` started, as an opaque string, or ``None`` when that cannot be read
    here (no such process, or a platform without a cheap way to ask). With the pid it names one
    process: a pid the system reused for another process has another start time (#317)."""
    try:
        if sys.platform.startswith("linux"):
            with open(f"/proc/{pid}/stat", "rb") as fh:
                stat = fh.read()
            return stat.rsplit(b")", 1)[1].split()[19].decode("ascii")  # field 22, starttime
        if sys.platform == "darwin":
            import ctypes

            mib = (ctypes.c_int * 4)(1, 14, 1, pid)  # CTL_KERN, KERN_PROC, KERN_PROC_PID
            buf = ctypes.create_string_buffer(1024)
            size = ctypes.c_size_t(len(buf))
            libc = ctypes.CDLL(None, use_errno=True)
            if libc.sysctl(mib, 4, buf, ctypes.byref(size), None, ctypes.c_size_t(0)):
                return None
            if size.value < 16:  # no such process
                return None
            sec, usec = struct.unpack_from("qi", buf.raw, 0)  # kp_proc.p_starttime, a timeval
            return f"{sec}.{usec:06d}"
    except (OSError, IndexError, ValueError, AttributeError):
        return None
    return None


def _file(root: str, fid: str) -> str:
    return os.path.join(root, FENCE_DIR, fid)


def _write(root: str, fid: str, text: str) -> None:
    d = os.path.join(root, FENCE_DIR)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{fid}.", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            fh.write(text)
        os.replace(tmp, _file(root, fid))
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise


def partition_of(path: str):
    """``(store root, facility id)`` when ``path`` is a store's ``facility_id=<id>`` partition or
    lies inside one; ``None`` otherwise (and for an ingest's staging area, which nobody reads, or
    a directory of partitions that holds none of a store's own files)."""
    parts = os.path.abspath(path).split(os.sep)
    for i, p in enumerate(parts):
        if p.startswith(_PARTITION):
            fid = p[len(_PARTITION) :]
            if not _safe(fid) or _STAGING in parts[:i]:
                return None
            if any(q.startswith(("_", ".")) for q in parts[i + 1 :]):
                return None
            root = os.sep.join(parts[:i]) or os.sep
            if not any(os.path.lexists(os.path.join(root, m)) for m in _STORE_FILES):
                return None  # not a store (the edge quarantine has the same layout)
            return root, fid
    return None


@contextlib.contextmanager
def fence(root: str, fid: str):
    """Mark ``fid``'s partition in the store at ``root`` busy while the block changes its files
    (the caller holds the store lock); on the way out the catalog is invalidated and the fence
    gets a fresh idle token. Nested fences on one facility in one process mark it once."""
    root = os.path.abspath(root)
    key = (root, fid)
    with _DEPTH_LOCK:
        held = _DEPTH.setdefault(key, [threading.get_ident(), 0])
        depth = held[1]
        held[1] += 1
    try:
        if depth == 0:
            pid = os.getpid()
            _write(root, fid, f"busy {pid} {_started(pid) or '-'} {os.urandom(8).hex()}\n")
        yield
    finally:
        with _DEPTH_LOCK:
            held[1] -= 1
            if not held[1]:
                del _DEPTH[key]
        if depth == 0:
            from .parquet_store import _invalidate_catalog_at  # lazy: it imports this module

            _invalidate_catalog_at(root)
            _write(root, fid, f"idle {os.urandom(8).hex()}\n")


def guard(path: str):
    """:func:`fence` for the facility partition ``path`` is or lies in; a no-op elsewhere."""
    where = partition_of(path)
    return fence(*where) if where else contextlib.nullcontext()


def _held_elsewhere(root: str, fid: str) -> bool:
    """Whether another thread of this process holds ``fid``'s fence (this thread's own write
    never blocks its own reads)."""
    with _DEPTH_LOCK:
        held = _DEPTH.get((root, fid))
        return held is not None and held[0] != threading.get_ident()


def _read(root: str, fid: str):
    """``(text, busy)`` of ``fid``'s fence: ``(None, False)`` when it has none."""
    try:
        with open(_file(root, fid), encoding="ascii") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError):
        return None, False
    return text, _busy(root, fid, text.split())


def _busy(root: str, fid: str, words) -> bool:
    """Whether the writer a fence's ``words`` name is still at work: its process is alive and,
    when the marker records it, started when that writer did (else the pid was reused, #317)."""
    if len(words) not in (3, 4) or words[0] != "busy" or not words[1].isdigit():
        return False
    from ._leftovers import _process_alive

    pid = int(words[1])
    if pid == os.getpid():
        return _held_elsewhere(root, fid)  # an earlier process with this pid is gone
    if not _process_alive(pid):
        return False
    if len(words) == 4 and words[2] != "-":
        now = _started(pid)
        return now is None or now == words[2]  # unsure counts as the writer
    return True


def recover(root: str) -> list:
    """Reset each busy marker in the store at ``root`` whose writer is gone (killed mid-change,
    or its pid since reused) to a fresh idle token; returns those facility ids (#317).

    The caller holds the store lock, so no other writer is at work; the partition itself is
    settled by the ingest's recovery. The catalog is invalidated first, as a writer does."""
    root = os.path.abspath(root)
    try:
        names = sorted(os.listdir(os.path.join(root, FENCE_DIR)))
    except OSError:
        return []
    stale = []
    for fid in names:
        if not _safe(fid):
            continue  # a writer's temporary file
        with _DEPTH_LOCK:
            if (root, fid) in _DEPTH:
                continue  # a fence this process holds
        text, busy = _read(root, fid)
        if text is not None and text.startswith("busy") and not busy:
            stale.append(fid)
    if stale:
        from .parquet_store import _invalidate_catalog_at  # lazy: it imports this module

        _invalidate_catalog_at(root)
        for fid in stale:
            _write(root, fid, f"idle {os.urandom(8).hex()}\n")
    return stale


def snapshot(root: str, fids, *, wait: float = _WAIT) -> dict:
    """``{facility_id: fence text}`` of ``fids`` in the store at ``root``, once no writer is busy
    on any of them; a retryable ``StoreReadError`` when one still is after ``wait`` seconds."""
    from .parquet_store import StoreReadError

    root = os.path.abspath(root)
    fids = sorted({f for f in fids if _safe(f)})
    if not fids or not os.path.isdir(os.path.join(root, FENCE_DIR)):
        return dict.fromkeys(fids)
    deadline = time.monotonic() + wait
    while True:
        snap, busy = {}, None
        for f in fids:
            snap[f], b = _read(root, f)
            if b and busy is None:
                busy = f
        if busy is None:
            return snap
        if time.monotonic() >= deadline:
            raise StoreReadError(
                "the facility's stored data is being replaced (a concurrent ingest, prune or "
                "removal)",
                facility_id=busy,
                retryable=True,
            )
        time.sleep(_POLL)


def changed(root: str, snap: dict):
    """The first facility of ``snap`` whose fence moved on since (a writer replaced or removed
    some of its files meanwhile, or is doing so now), or ``None``."""
    root = os.path.abspath(root)
    if all(v is None for v in snap.values()) and not os.path.isdir(os.path.join(root, FENCE_DIR)):
        return None  # no writer ever fenced a facility of this store
    for f in sorted(snap):
        text, busy = _read(root, f)
        if busy or text != snap[f]:
            return f
    return None


def changed_error(fid):
    """The retryable ``StoreReadError`` for a read that a writer of ``fid`` overlapped."""
    from .parquet_store import StoreReadError

    return StoreReadError(
        "the facility's stored data was replaced while it was read (a concurrent ingest, prune "
        "or removal)",
        facility_id=fid,
        retryable=True,
    )
