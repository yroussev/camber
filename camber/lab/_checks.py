"""The lab's pre-flight checks: disk space, optional extras and a local folder (0.103, #123).

Each check runs twice: in the catalog view (so the page can disable a button and say why) and
again when a job is submitted (so a request that skips the page is refused the same way, with
the same numbers). Nothing here downloads, extracts or writes.

**Disk.** A fetch-and-ingest needs room for three things, and the check counts all three:

* the **download** still to fetch (what :func:`camber.datasets._ops.fetch_dataset` checks: the
  subset's files not yet in the cache, a partial ``.part`` counted as done);
* the **archive extraction**, which stays in the cache next to the download. Once the archive is
  on disk and is a zip, this is exact: the uncompressed size of the members the subset's runs
  read and that are not extracted yet, which is what ingest checks before it extracts. Before
  that it is estimated from the catalog's ``extracted_size``: the share of the archive's listed
  members the subset reads, or the whole archive when the catalog lists no members;
* the **store**: the subset's ``store_bytes_estimate``.

Every term gets the fetch's own headroom (:data:`camber.datasets._fetch.DISK_MARGIN`, rounded
up), so a selection the page allows is never one the fetch refuses. When the cache and the store
share a filesystem their needs are added and checked once.
"""

from __future__ import annotations

import importlib.util
import math
import os
import shutil

from ..datasets import _paths
from ..datasets._fetch import DISK_MARGIN, human_bytes

#: the longest local folder path the lab accepts (a path, not a document)
MAX_DIR_CHARS = 4096


def probe(path: str) -> str:
    """The nearest existing ancestor of ``path`` (what a free-space query can stat)."""
    p = os.path.abspath(os.fspath(path))
    while not os.path.exists(p):
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    return p


def free_bytes(path: str) -> int | None:
    """Free bytes on the filesystem that holds ``path`` (``None`` when it cannot be read)."""
    try:
        return int(shutil.disk_usage(probe(path)).free)
    except OSError:  # pragma: no cover - unreadable mount
        return None


def same_disk(a: str, b: str) -> bool:
    """True when ``a`` and ``b`` (or their nearest existing ancestors) share a filesystem."""
    try:
        return os.stat(probe(a)).st_dev == os.stat(probe(b)).st_dev
    except OSError:  # pragma: no cover - unreadable mount
        return False


def with_margin(n: int) -> int:
    """``n`` bytes plus the fetch's headroom, rounded up (the fetch refuses ``free < n * 1.05``)."""
    return int(math.ceil(max(0, int(n or 0)) * (1.0 + DISK_MARGIN))) if n else 0


# --------------------------------------------------------------------------- per-entry needs


def pending_download(entry, subset: str, data_dir: str) -> int:
    """Bytes the fetch of ``subset`` still has to download (exactly what the fetch checks)."""
    from ..datasets._ops import _pending_bytes

    return _pending_bytes(data_dir, entry, entry.subset_files(subset))


def _members_read(entry, subset: str) -> dict:
    """``{file name: [archive members]}`` the subset's ingest reads (runs and the Brick model)."""
    from ..datasets._ingest import run_members

    out: dict = {}
    for r in entry.runs(subset):
        for m in run_members(r):
            out.setdefault(r.get("file"), [])
            if m not in out[r.get("file")]:
                out[r.get("file")].append(m)
    brick = entry.ingest.get("brick") or {}
    if brick.get("member"):
        out.setdefault(brick["file"], []).append(brick["member"])
    return out


def _exact_extract(path: str, members: list, dest: str) -> int | None:
    """The uncompressed bytes still to extract from a downloaded zip (``None``: not knowable)."""
    import zipfile

    from ..datasets._archive import archive_kind
    from ..datasets._ingest import _pending_bytes

    if not (os.path.isfile(path) and archive_kind(path) == "zip"):
        return None  # a tar header walk reads the whole stream: estimate instead
    try:
        return int(_pending_bytes(path, members, dest))
    except (KeyError, OSError, zipfile.BadZipFile):
        return None


def pending_extract(entry, subset: str, data_dir: str) -> int:
    """Bytes the ingest of ``subset`` will extract into the cache (see the module docstring)."""
    from ..datasets._ops import _dest, _dir_bytes

    reads = _members_read(entry, subset)
    total = 0
    for f in entry.subset_files(subset):
        size = int(f.get("extracted_size") or 0)
        if not f.get("archive") or size <= 0:
            continue
        dest = os.path.join(
            _paths.extracted_dir(data_dir, entry.id),
            os.path.splitext(os.path.basename(f["name"]))[0],
        )
        needed = reads.get(f["name"]) or []
        listed = list(f.get("members") or [])
        if needed:
            exact = _exact_extract(_dest(data_dir, entry, f["name"]), needed, dest)
            if exact is not None:
                total += exact
                continue
        if needed and listed:
            missing = [m for m in needed if not os.path.isfile(os.path.join(dest, m))]
            total += int(math.ceil(size * len(missing) / max(len(listed), len(needed))))
        else:
            total += max(0, size - (_dir_bytes(dest) if os.path.isdir(dest) else 0))
    return total


def needs(entry, subset: str, data_dir: str) -> dict:
    """``{"download", "extract", "store"}`` bytes for one entry's subset (no margin applied)."""
    return {
        "download": pending_download(entry, subset, data_dir),
        "extract": pending_extract(entry, subset, data_dir),
        "store": int(entry.store_bytes(subset) or 0),
    }


def disk_plan(
    entries,
    subset: str,
    *,
    data_dir: str,
    store: str,
    download: bool,
    extract: bool = True,
    store_write: bool = True,
) -> dict:
    """Check a job's disk needs against free space; ``{"ok", "problems", "locations"}``.

    ``download`` / ``extract`` / ``store_write`` choose which terms the job incurs (a fetch-only
    job: the download; an ingest of fetched data: extraction and store). Each location's need is
    the sum of its terms with :func:`with_margin` applied once, compared with its free bytes; a
    location whose free space is unknown is not refused.
    """
    cache = st = 0
    for e in entries:
        n = needs(e, subset, data_dir)
        cache += (n["download"] if download else 0) + (n["extract"] if extract else 0)
        st += n["store"] if store_write else 0
    if same_disk(data_dir, store):
        locs = [("the dataset cache and the store", data_dir, cache + st)]
    else:
        locs = [("the dataset cache", data_dir, cache), ("the store", store, st)]
    problems: list = []
    locations: list = []
    for label, path, raw in locs:
        need, free = with_margin(raw), free_bytes(path)
        locations.append({"label": label, "need": need, "free": free})
        if raw and free is not None and need > free:
            problems.append(
                f"{label} needs about {human_bytes(need)} (with {DISK_MARGIN:.0%} headroom) but "
                f"only {human_bytes(free)} is free"
            )
    return {"ok": not problems, "problems": problems, "locations": locations}


# --------------------------------------------------------------------------- extras


def missing_extras(entry) -> list:
    """The entry's ``requires_extras`` whose package is not importable: ``[{extra, module, hint}]``.

    Looked up with :func:`importlib.util.find_spec` (nothing is imported). An extra CAMBER does
    not know is reported as missing, with no install hint to guess at.
    """
    from ..datasets._readers import EXTRAS

    out = []
    for name in entry.requires_extras or ():
        module, hint = EXTRAS.get(name, (None, ""))
        try:
            found = module is not None and importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            out.append({"extra": name, "module": module, "hint": hint})
    return out


def extras_refusal(entry) -> str | None:
    """A message naming the missing extras and how to install them, or ``None``."""
    miss = missing_extras(entry)
    if not miss:
        return None
    parts = [
        f"{m['extra']} (the {m['module']!r} package: {m['hint']})"
        if m["module"]
        else f"{m['extra']} (unknown extra)"
        for m in miss
    ]
    return (
        f"{entry.id} needs the optional extra(s) {', '.join(parts)}; install it and restart "
        "`camber lab`. Nothing was downloaded."
    )


# --------------------------------------------------------------------------- a local folder


def local_folder(value) -> str:
    """Validate a user-typed folder for ``ingest --from-dir``; returns its resolved real path.

    Raises ``ValueError`` (the message is shown to the user) unless ``value`` is a string of at
    most :data:`MAX_DIR_CHARS` characters, with no NUL or control character, that is absolute
    (``~`` is expanded) and names an existing, readable directory. Only reading follows: the
    folder is never written, listed beyond the catalog's own file names, or served back.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("dir must be the path of a local folder")
    if len(value) > MAX_DIR_CHARS:
        raise ValueError(f"dir is longer than {MAX_DIR_CHARS} characters")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("dir contains a control character")
    path = os.path.expanduser(value.strip())
    if not os.path.isabs(path):
        raise ValueError("dir must be an absolute path (or start with ~)")
    real = os.path.realpath(path)
    if not os.path.isdir(real):
        raise ValueError(f"{value} is not a folder")
    if not os.access(real, os.R_OK | os.X_OK):
        raise ValueError(f"{value} is not readable")
    return real


def folder_files(entry, subset: str, folder: str) -> list:
    """The subset's files as ``ingest --from-dir`` would take them from ``folder``.

    Raises ``ValueError`` when a file the catalog names resolves (through a symbolic link)
    outside ``folder``: the lab reads that folder and nothing else. A missing file is left to the
    ingest, which names every missing file and the entry's download instructions.
    """
    from ..datasets._ops import _local_source

    root = os.path.realpath(folder)
    found = []
    for f in entry.subset_files(subset):
        src = _local_source(root, f["name"])
        if src is None:
            continue
        real = os.path.realpath(src)
        if os.path.commonpath([real, root]) != root:
            raise ValueError(f"{f['name']} in that folder links outside it; copy the file in")
        found.append(f["name"])
    return found
