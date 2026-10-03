"""Crash-safe directory replacement and deletion (private plumbing for lifecycle and retention).

Every destructive or replacing step on the store, the per-facility state and the archive goes
through two primitives whose intermediate states a crash can leave behind, and which
:func:`recover` / :func:`recover_tree` finish or roll back deterministically:

**Replace** (:func:`staging` + :func:`commit`) -- build the new content next to the target, then
swap it in::

    <parent>/_swap-<name>.new     the new content, written completely first
    <parent>/_swap-<name>.ready   written (and fsynced) only once .new is complete
    <parent>/_swap-<name>.old     the previous <name>, moved aside during the swap

    commit:  write .ready -> rename <name> to .old -> rename .new to <name> -> rm .old -> rm .ready

Recovery reads the leftovers: a ``.ready`` marker means the new content is complete, so the swap
is rolled *forward*; without it the new content is incomplete and is discarded, and a moved-aside
``.old`` is put back. Either way the target ends up holding exactly the old or exactly the new
content, never a mix.

**Delete** (:func:`discard`) -- rename the directory to ``_trash-<name>-<token>`` (one atomic
rename: readers see it gone at once) and then remove it. A crash mid-removal leaves only a
``_trash-*`` directory, which recovery removes.

Names starting with ``_`` are invisible to pyarrow's dataset discovery, to
``ParquetStore.facilities()`` and to the portfolio's state scan, so none of these leftovers is
ever read as data.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import uuid

SWAP_PREFIX = "_swap-"
TRASH_PREFIX = "_trash-"
_NEW, _OLD, _READY = ".new", ".old", ".ready"


def _paths(target: str) -> tuple:
    parent, name = os.path.split(os.path.abspath(target))
    stem = os.path.join(parent, f"{SWAP_PREFIX}{name}")
    return stem + _NEW, stem + _OLD, stem + _READY


def _fsync_dir(path: str) -> None:
    if os.name == "nt":  # pragma: no cover - directories cannot be opened for fsync on Windows
        return
    with contextlib.suppress(OSError):
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _rm(path: str) -> None:
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    elif os.path.lexists(path):
        os.remove(path)


def staging(target: str) -> str:
    """A fresh, empty ``_swap-<name>.new`` directory next to ``target`` (after recovering any
    earlier interrupted swap of the same target). Fill it, then call :func:`commit`."""
    recover(target)
    new, _old, _ready = _paths(target)
    _rm(new)
    os.makedirs(new)
    return new


def fsync_tree(path: str) -> None:
    """fsync every file (and directory) under ``path`` so a completed stage survives a power cut."""
    for dirpath, _dirs, names in os.walk(path):
        for n in names:
            with contextlib.suppress(OSError):
                fd = os.open(os.path.join(dirpath, n), os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        _fsync_dir(dirpath)


def commit(target: str) -> None:
    """Swap the completed staging directory in as ``target`` (see the module docstring)."""
    new, old, ready = _paths(target)
    if not os.path.isdir(new):
        raise FileNotFoundError(f"nothing staged for {target}")
    fsync_tree(new)
    fd = os.open(ready, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    _finish(target)


def _finish(target: str) -> None:
    """Roll a swap whose ``.ready`` marker exists forward to completion (idempotent)."""
    new, old, ready = _paths(target)
    parent = os.path.dirname(os.path.abspath(target))
    if os.path.isdir(new):
        if os.path.lexists(target):
            _rm(old)
            os.rename(target, old)
        os.rename(new, target)
        _fsync_dir(parent)
    _rm(old)
    _rm(ready)
    _fsync_dir(parent)


def recover(target: str):
    """Finish or roll back an interrupted swap of ``target``; returns what was done (or ``None``).

    ``"rolled_forward"``: the staged content was complete (``.ready``) and is now the target.
    ``"rolled_back"``: the staged content was incomplete and is gone; the target holds what it
    held before the swap started.
    """
    new, old, ready = _paths(target)
    if os.path.exists(ready):
        _finish(target)
        return "rolled_forward"
    if not (os.path.lexists(new) or os.path.lexists(old)):
        return None
    _rm(new)
    if os.path.lexists(old):
        if os.path.lexists(target):  # pragma: no cover - .old is only made after .ready
            _rm(old)
        else:
            os.rename(old, target)
    return "rolled_back"


def discard(path: str) -> bool:
    """Delete a directory (or file) crash-safely: one atomic rename, then removal.

    Returns whether anything was there. A crash after the rename leaves a ``_trash-*`` entry that
    :func:`recover_tree` removes; readers never see a half-deleted directory.
    """
    path = os.path.abspath(path)
    if not os.path.lexists(path):
        return False
    parent, name = os.path.split(path)
    trash = os.path.join(parent, f"{TRASH_PREFIX}{name}-{uuid.uuid4().hex[:12]}")
    os.rename(path, trash)
    _fsync_dir(parent)
    _rm(trash)
    return True


def recover_tree(root: str, *, max_depth: int = 5) -> list:
    """Recover every interrupted swap and delete every ``_trash-*`` leftover under ``root``.

    Returns ``[{"path", "action"}]`` (``rolled_forward``, ``rolled_back`` or ``trash_removed``).
    Only directories are descended into (``max_depth`` levels), so a large store is cheap to scan.
    """
    out: list = []
    if not os.path.isdir(root):
        return out

    def walk(d: str, depth: int) -> None:
        try:
            entries = sorted(os.scandir(d), key=lambda e: e.name)
        except OSError:  # pragma: no cover - vanished under us
            return
        targets: set = set()
        for e in entries:
            if e.name.startswith(TRASH_PREFIX):
                _rm(e.path)
                out.append({"path": e.path, "action": "trash_removed"})
            elif e.name.startswith(SWAP_PREFIX):
                for suffix in (_NEW, _OLD, _READY):
                    if e.name.endswith(suffix):
                        targets.add(os.path.join(d, e.name[len(SWAP_PREFIX) : -len(suffix)]))
        for t in sorted(targets):
            act = recover(t)
            if act:
                out.append({"path": t, "action": act})
        if depth >= max_depth:
            return
        for e in entries:
            if e.name.startswith((TRASH_PREFIX, SWAP_PREFIX)):
                continue
            if os.path.isdir(os.path.join(d, e.name)) and not e.is_symlink():
                walk(os.path.join(d, e.name), depth + 1)

    walk(os.path.abspath(root), 0)
    return out
