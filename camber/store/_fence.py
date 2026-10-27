"""Per-facility write fences, so a reader that takes no lock notices a concurrent replace (#306).

Reads take no lock. A writer that replaces or removes a facility's files -- the partition swap of
a forced ``camber datasets ingest``, a drop, a prune, a retention roll-up, a partition migration --
changes what a reader sees in more than one step: a swap renames the old partition aside and then
the new one in, so for a moment the facility is absent, and a reader listing its directory across
either rename sees part of it. Each such step runs inside :func:`fence` for its facility, which
works like a sequence lock::

    <store>/_fence/<facility_id>    "busy <pid> <token>" while the writer changes the files,
                                     then "idle <token>", with a fresh random token each time

A reader takes a :func:`snapshot` of the facilities it reads before it starts and checks it with
:func:`changed` when it is done. A writer still busy (after a short wait) or a token that changed
in between means the reader may have seen a mix, so it fails with a retryable
:class:`~camber.store.parquet_store.StoreReadError` (the capability error ``unavailable``) instead
of answering from a partial facility. The catalog generation is replaced inside the fence, before
the writer reports idle, so a catalog a reader rebuilt mid-swap is never served afterwards.

Random tokens, not a counter, so a lost file cannot make an old snapshot look current (no ABA).
A busy marker of a process that is gone (a writer killed mid-swap) does not block readers; the
next writer's recovery settles the partition. The directory starts with ``_``, so dataset
discovery, ``ParquetStore.facilities()`` and the swap recovery never read it as data.
"""

from __future__ import annotations

import contextlib
import os
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
            _write(root, fid, f"busy {os.getpid()} {os.urandom(8).hex()}\n")
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
    words = text.split()
    busy = False
    if len(words) == 3 and words[0] == "busy" and words[1].isdigit():
        from ._leftovers import _process_alive

        pid = int(words[1])
        busy = _held_elsewhere(root, fid) if pid == os.getpid() else _process_alive(pid)
    return text, busy


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
