"""What interrupted writers leave in a store root, read-only (0.107, #213).

A dataset ingest (:mod:`camber.datasets._ingest`, also behind the open-fdd ingest) builds its
partitions in ``<store>/_staging/ingest-<pid>-<token>/`` and swaps each one in with
:mod:`._swap`; a pre-0.107 swap moved the old partition to ``_staging/<fid>.old-<pid>``. A crash
or a kill can leave any of these behind. :func:`staging_leftovers` lists them, for the ingest's
recovery (:func:`camber.datasets._ingest.recover_staging`) and for ``camber store verify``.
"""

from __future__ import annotations

import os
import re

from ._swap import SWAP_PREFIX, TRASH_PREFIX

STAGING_DIR = "_staging"
_INGEST_SCRATCH = re.compile(r"^(?:ingest|append)-(\d+)-[0-9a-f]+(\.extract)?$")
_LEGACY_ASIDE = re.compile(r"^(?P<fid>.+)\.old-(?P<pid>\d+)$")


def _process_alive(pid: int) -> bool:
    """Whether process ``pid`` may still be running (conservative: unsure counts as alive)."""
    if pid == os.getpid():
        return True
    if os.name == "nt":  # pragma: no cover - os.kill(pid, 0) signals the process on Windows
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:  # EPERM: alive, owned by someone else
        return True
    return True


def staging_leftovers(root: str) -> list:
    """What interrupted ingests left in the store at ``root`` (read-only; 0.107, #213).

    ``[{"path", "kind", "facility_id", "pid", "alive"}]``: ``kind`` is ``"swap"`` (an
    interrupted ``_swap-facility_id=*`` replace), ``"trash"`` (an interrupted partition delete,
    ``_trash-facility_id=*``), ``"aside"`` (a partition a pre-0.107 swap
    moved to ``_staging/<fid>.old-<pid>``) or ``"scratch"`` (an ingest's ``_staging/ingest-*``
    working directory, or an append's ``_staging/append-*`` one). ``alive`` says whether the process that made it may still be running.
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
            out.append(
                {
                    "path": path,
                    "kind": "scratch",
                    "facility_id": None,
                    "pid": pid,
                    "alive": _process_alive(pid),
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
                }
            )
    return out
