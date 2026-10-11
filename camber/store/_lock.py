"""Single-writer lock on a store root (0.103, #124).

A store inside a portfolio workspace already has one: the workspace's ``_lock``
(:mod:`camber.portfolio._lock`). A plain store gets the same lock on ``<store>/_lock``. Both are
advisory ``flock`` / ``msvcrt`` locks the kernel drops when the holder exits, so a crashed writer
never leaves a stale lock, only a stale holder line that the next writer overwrites.

:func:`store_lock` picks the right one: the workspace's lock for a workspace-backed store (never a
second lock beside it, so a caller that already holds the workspace lock re-enters it instead of
deadlocking), else the store's own. It is re-entrant within a process. A second process waits up
to ``timeout`` seconds, then gets :class:`StoreLocked` naming the holder (``pid@host since
<time>``); for a workspace store it gets :class:`~camber.portfolio.PortfolioLocked`, as for every
other workspace write. The default wait is
:func:`~camber.portfolio._lock.default_lock_timeout` (``--lock-timeout``, else
``$CAMBER_LOCK_TIMEOUT``, else :data:`STORE_LOCK_TIMEOUT`).

Taken by the dataset catalog's writers (``camber datasets ingest`` / ``remove --purge-store`` and
``camber lab``'s ingest jobs) and, since 0.104 (#135), by every :class:`~camber.store.ParquetStore`
write method and the facility registry of a plain store. A command that writes many times takes
it once around the whole operation (:meth:`ParquetStore.lock`), and the per-call locks inside
re-enter it. Reads never take it: a reader sees the old partition or the new one, since an
ingest swaps a facility's partition in with directory renames.
"""

from __future__ import annotations

import os

# camber.store/__init__ does not import this module, so importing the portfolio package here (which
# imports the store) cannot cycle.
from ..portfolio._lock import LockHeld, default_lock_timeout, exclusive_lock, portfolio_lock
from .facilities import _workspace_of_store

STORE_LOCK_TIMEOUT = 30.0  # seconds a second writer waits before it is refused


class StoreLocked(LockHeld):
    """Another process holds a plain store's single-writer lock."""


def store_lock(root, *, timeout: float | None = None):
    """Context manager holding the single-writer lock of the store at ``root`` (see the module).

    ``timeout`` seconds to wait for another process (``0`` = fail at once; ``None`` = the default,
    see the module). Creates ``root`` if needed."""
    if timeout is None:
        timeout = default_lock_timeout(STORE_LOCK_TIMEOUT)
    root = os.path.abspath(os.fspath(root))
    ws = _workspace_of_store(root)
    if ws is not None:
        return portfolio_lock(ws, timeout=timeout)
    os.makedirs(root, exist_ok=True)
    return exclusive_lock(
        root,
        timeout=timeout,
        what=f"store {root}",
        error=StoreLocked,
        hint=f" (another ingest or remove is writing it; waited {timeout:g} s -- retry when it "
        "finishes)",
    )
