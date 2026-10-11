"""Single-writer advisory lock on a portfolio root (``<root>/_lock``).

``fcntl.flock`` on POSIX, ``msvcrt.locking`` on Windows. The kernel drops the lock when the holding
process exits -- including a crash or ``kill -9`` -- so a leftover ``_lock`` file is never a stale
*lock*, only stale *text*: the next writer simply acquires it and overwrites the holder line. The
holder line (``pid``, ``host``, ``since``) exists only to make the refusal message useful.

Caveat: advisory file locks are unreliable on some network filesystems (older NFS, SMB with
oplocks disabled). Keep the portfolio root on a local disk or a filesystem with working
``flock`` semantics; see docs/PORTFOLIO.md.

The lock is **re-entrant within a process**: a CLI command that holds it can call the registry,
which takes it again, without deadlocking. Other threads of the same process wait on an in-process
lock; other processes are refused (or wait, with ``timeout``).

:func:`exclusive_lock` is the same lock under another name and error, for directories that are not
portfolios: a plain store (:func:`camber.store._lock.store_lock`) and a dataset cache
(:func:`camber.datasets._paths.cache_lock`) take it on ``<dir>/_lock`` (0.103, #124).
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import json
import os
import socket
import sys
import threading
import time

LOCK_FILE = "_lock"


class LockHeld(RuntimeError):
    """Another process holds a directory's single-writer lock (see :func:`exclusive_lock`)."""


class PortfolioLocked(LockHeld):
    """Another process holds the portfolio's single-writer lock."""


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class _Held:
    __slots__ = ("fh", "depth", "rlock")

    def __init__(self):
        self.fh = None
        self.depth = 0
        self.rlock = threading.RLock()


_HELD: dict = {}
_HELD_GUARD = threading.Lock()


def _slot(path: str) -> _Held:
    with _HELD_GUARD:
        h = _HELD.get(path)
        if h is None:
            h = _HELD[path] = _Held()
        return h


if sys.platform == "win32":  # pragma: no cover - exercised on Windows CI only
    import msvcrt

    _INFO_OFFSET = 1  # byte 0 is the locked byte; the holder line follows it

    def _try_lock(fh) -> bool:
        fh.seek(0)
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fh) -> None:
        fh.seek(0)
        with contextlib.suppress(OSError):
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    _INFO_OFFSET = 1

    def _try_lock(fh) -> bool:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(fh) -> None:
        with contextlib.suppress(OSError):
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def read_holder(root: str) -> dict:
    """The holder line last written to ``<root>/_lock`` (``{}`` if none/unreadable)."""
    try:
        with open(os.path.join(root, LOCK_FILE), "rb") as fh:
            fh.seek(_INFO_OFFSET)
            raw = fh.read(4096).decode("utf-8", "replace").strip()
        data = json.loads(raw) if raw else {}
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def probe(root: str):
    """The holder dict if another process holds ``<root>/_lock`` right now, else ``None``."""
    path = os.path.join(root, LOCK_FILE)
    if not os.path.isfile(path):
        return None
    slot = _slot(os.path.realpath(path))
    if slot.depth:  # held by this process
        return read_holder(root) or {"pid": os.getpid()}
    with _open(path) as fh:
        if _try_lock(fh):
            _unlock(fh)
            return None
    return read_holder(root) or {}


def _pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError, TypeError):
        return False
    return True


def describe_holder(holder: dict) -> str:
    """``"<pid>@<host> since <ts>"`` for a holder dict (``"an unknown process"`` if empty)."""
    if not holder:
        return "an unknown process"
    return f"{holder.get('pid', '?')}@{holder.get('host', '?')} since {holder.get('since', '?')}"


def _write_holder(fh) -> None:
    info = {"pid": os.getpid(), "host": socket.gethostname(), "since": _utc_now()}
    fh.seek(0)
    fh.truncate()
    fh.write(b"\n" + json.dumps(info, sort_keys=True).encode("utf-8") + b"\n")
    fh.flush()
    with contextlib.suppress(OSError):
        os.fsync(fh.fileno())


def _open(path: str):
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    return os.fdopen(fd, "r+b")


def portfolio_lock(root: str, *, timeout: float = 0.0, poll: float = 0.1):
    """Hold ``<root>/_lock`` for the ``with`` block; raise :class:`PortfolioLocked` if taken.

    ``timeout`` seconds to wait for another process to release it (``0`` = fail at once). A holder
    line left behind by a dead process is stale text, not a held lock (see the module docstring);
    it is overwritten on acquire.
    """
    return exclusive_lock(root, timeout=timeout, poll=poll)


@contextlib.contextmanager
def exclusive_lock(
    root: str,
    *,
    timeout: float = 0.0,
    poll: float = 0.1,
    what: str = "portfolio",
    error: type = PortfolioLocked,
    hint: str = "",
):
    """Hold ``<root>/_lock`` for the ``with`` block; raise ``error`` if another process keeps it.

    The single-writer lock behind :func:`portfolio_lock`, for any directory: ``what`` names it in
    the refusal (``"<what> is locked by <pid>@<host> since <ts>"``) and ``hint`` is appended to
    that message. ``root`` must exist. Re-entrant within a process; see the module docstring.
    """
    path = os.path.realpath(os.path.join(root, LOCK_FILE))
    slot = _slot(path)
    deadline = time.monotonic() + max(0.0, timeout)
    got = slot.rlock.acquire(timeout=timeout) if timeout > 0 else slot.rlock.acquire(False)
    if not got:
        raise error(  # pragma: no cover - another thread of this process holds it
            f"{what} is locked by {describe_holder(read_holder(root))} (this process){hint}"
        )
    try:
        if slot.depth == 0:
            fh = _open(path)
            while not _try_lock(fh):
                if time.monotonic() >= deadline:
                    fh.close()
                    holder = read_holder(root)
                    note = ""
                    if holder and holder.get("host") == socket.gethostname():
                        if not _pid_alive(holder.get("pid")):  # pragma: no cover - racy
                            note = " (that pid has exited; retry -- the lock is released)"
                    raise error(f"{what} is locked by {describe_holder(holder)}{note}{hint}")
                time.sleep(poll)
            _write_holder(fh)
            slot.fh = fh
        slot.depth += 1
        try:
            yield
        finally:
            slot.depth -= 1
            if slot.depth == 0 and slot.fh is not None:
                fh, slot.fh = slot.fh, None
                _unlock(fh)
                fh.close()
    finally:
        slot.rlock.release()
