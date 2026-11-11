"""What interrupted writers leave in a store root, read-only (0.107, #213).

A dataset ingest (:mod:`camber.datasets._ingest`, also behind the open-fdd ingest) builds its
partitions in ``<store>/_staging/ingest-<pid>-<token>/`` and swaps each one in with
:mod:`._swap`; a pre-0.107 swap moved the old partition to ``_staging/<fid>.old-<pid>``. A crash
or a kill can leave any of these behind. :func:`staging_leftovers` lists them, for the ingest's
recovery (:func:`camber.datasets._ingest.recover_staging`) and for ``camber store verify``.

**Whose is a staging directory (0.112, #330).** Its name carries the pid of the ingest that made
it, which does not say whether that ingest still runs: pid 1 is alive on every system (``launchd``,
any container's init), so a killed pid-1 container ingest left ``ingest-1-*`` forever, and a pid
of another kernel or container names some unrelated process here. An ingest now records who it is
in ``<dir>/_owner.json`` (:func:`mark_owner`: pid, kernel id, pid namespace, start time). A
directory is its writer's while that process runs, when this process can tell (same kernel and
pid namespace, :func:`camber.portfolio._lock.process_state`); one made elsewhere is its writer's
while that writer holds the store's lock claim, which an ingest needs throughout -- so the next
ingest, which holds it, removes it. A directory without the record (0.111 and older, an append's)
is judged by its pid, except pid 1 and this process's own pid, which are never its writer.
"""

from __future__ import annotations

import json
import os
import re

from ._swap import SWAP_PREFIX, TRASH_PREFIX

STAGING_DIR = "_staging"
_INGEST_SCRATCH = re.compile(r"^(?:ingest|append)-(\d+)-[0-9a-f]+(\.extract)?$")
_LEGACY_ASIDE = re.compile(r"^(?P<fid>.+)\.old-(?P<pid>\d+)$")
OWNER_FILE = "_owner.json"  # 0.112 (#330): who made an ingest's staging directory
# 0.112 (#335): the temp file of an atomic write at the store root (the registry's
# ``_facilities.json.tmp[-<pid>-<token>]``, ``_catalog.gen.<random>.tmp``, ...)
_TEMP_FILE = re.compile(r"^_.*\.tmp(?:-\d+(?:-[0-9a-f]+)?)?$")


def _process_alive(pid: int) -> bool:
    """Whether process ``pid`` may still be running (conservative: unsure counts as alive)."""
    if pid == os.getpid():
        return True
    if not 0 < pid <= 2**31 - 1:  # pid_t is 32 bits: no process has such a pid (#321)
        return False
    if os.name == "nt":  # pragma: no cover - os.kill(pid, 0) signals the process on Windows
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:  # EPERM: alive, owned by someone else
        return True
    return True


def mark_owner(path: str) -> str:
    """Create the staging directory ``path`` with this process's record in it (#330)."""
    from ..portfolio._lock import process_identity  # lazy: the portfolio imports the store

    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, OWNER_FILE), "w", encoding="utf-8") as fh:
        json.dump(process_identity(), fh, sort_keys=True)
    return path


def _read_owner(path: str):
    try:
        with open(os.path.join(path, OWNER_FILE), encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) else None


def _claim_of(root: str):
    """The lock claim now on the store at ``root`` (the workspace's for a workspace store)."""
    from ..portfolio._lock import CLAIM_FILE, _read_claim  # lazy: the portfolio imports the store
    from .facilities import _workspace_of_store

    return _read_claim(os.path.join(_workspace_of_store(root) or root, CLAIM_FILE))[1]


def _scratch_owner(root: str, path: str, pid: int) -> tuple:
    """``(alive, elsewhere)`` for an ingest's staging directory (see the module docstring)."""
    # lazy: the portfolio imports the store
    from ..portfolio._lock import kernel_id, pid_namespace, process_state

    base = path[: -len(".extract")] if path.endswith(".extract") else path
    rec = _read_owner(base)
    if rec is None:
        if pid in (1, os.getpid()):  # never its writer: init, or this process (whose are marked)
            return False, False
        return _process_alive(pid), False
    state = process_state(rec)
    if state is not None:
        return state, False
    held = _claim_of(root) or {}
    same = all(held.get(k) == rec.get(k) for k in ("pid", "kernel", "pidns", "started"))
    return same, rec.get("kernel") != kernel_id() or rec.get("pidns") != pid_namespace()


def _store_held_elsewhere(root: str) -> bool:
    """Whether another process holds the store's claim now: a temp file may then be its write in
    progress. This process's own claim does not count (its caller is not mid-write)."""
    from ..portfolio._lock import kernel_id, pid_namespace  # lazy: the portfolio imports the store

    held = _claim_of(root)
    if not held:
        return False
    mine = {"pid": os.getpid(), "kernel": kernel_id(), "pidns": pid_namespace()}
    return any(held.get(k) != v for k, v in mine.items())


def staging_leftovers(root: str) -> list:
    """What interrupted ingests left in the store at ``root`` (read-only; 0.107, #213).

    ``[{"path", "kind", "facility_id", "pid", "alive"}]``: ``kind`` is ``"swap"`` (an
    interrupted ``_swap-facility_id=*`` replace), ``"trash"`` (an interrupted partition delete,
    ``_trash-facility_id=*``), ``"aside"`` (a partition a pre-0.107 swap
    moved to ``_staging/<fid>.old-<pid>``) or ``"scratch"`` (an ingest's ``_staging/ingest-*``
    working directory, or an append's ``_staging/append-*`` one) or ``"tmp"`` (0.112, #335: the
    temp file of an interrupted registry or other atomic write at the store root, ``alive`` while
    another process holds the store's claim). ``alive`` says whether the
    process that made it may still be running; ``elsewhere`` (0.112, #330) that it was made on
    another system or in another container, whose writer is alive only while it holds the store.
    """
    out: list = []
    if not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root)):
        for kind, prefix in (("swap", SWAP_PREFIX), ("trash", TRASH_PREFIX)):
            if name.startswith(f"{prefix}facility_id="):
                rest = name[len(prefix) + len("facility_id=") :]
                fid = rest.rsplit(".", 1)[0] if kind == "swap" else rest.rsplit("-", 1)[0]
                out.append(
                    {
                        "path": os.path.join(root, name),
                        "kind": kind,
                        "facility_id": fid,
                        "pid": None,
                        "alive": False,
                        "elsewhere": False,
                    }
                )
    held = None
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if _TEMP_FILE.match(name) and os.path.isfile(path) and not os.path.islink(path):
            if held is None:
                held = _store_held_elsewhere(root)
            out.append(
                {
                    "path": path,
                    "kind": "tmp",
                    "facility_id": None,
                    "pid": None,
                    "alive": held,
                    "elsewhere": False,
                }
            )
    sdir = os.path.join(root, STAGING_DIR)
    if not os.path.isdir(sdir):
        return out
    for name in sorted(os.listdir(sdir)):
        path = os.path.join(sdir, name)
        m = _INGEST_SCRATCH.match(name)
        if m:
            pid = int(m.group(1))
            alive, elsewhere = _scratch_owner(root, path, pid)
            out.append(
                {
                    "path": path,
                    "kind": "scratch",
                    "facility_id": None,
                    "pid": pid,
                    "alive": alive,
                    "elsewhere": elsewhere,
                }
            )
            continue
        m = _LEGACY_ASIDE.match(name)
        if m and os.path.isdir(path):
            pid = int(m.group("pid"))
            out.append(
                {
                    "path": path,
                    "kind": "aside",
                    "facility_id": m.group("fid"),
                    "pid": pid,
                    "alive": _process_alive(pid),
                    "elsewhere": False,
                }
            )
    return out
