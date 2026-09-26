"""Safe, selective extraction of downloaded dataset archives (zip and tar).

A dataset archive is third-party input: the catalog pins its sha256, but the extractor still
treats every member as hostile so a reissued or tampered archive cannot write outside the cache.
The threat model and the guard for each:

* **Path traversal ("zip-slip")** -- absolute paths, drive letters, ``..`` components and
  backslash-separated traversal are rejected, and every target's ``realpath`` must stay inside the
  destination.
* **Links and special files** -- tar symlinks, hardlinks, devices and FIFOs, and zip entries whose
  Unix mode marks a symlink, are rejected (a link member could redirect a later write).
* **Decompression bombs** -- caps on the member count, the declared total size and (zip) the
  per-member compression ratio; the byte count is re-checked while streaming, so a member that
  lies about its size is aborted too.
* **Torn files** -- each member is streamed (1 MiB chunks, never read whole) into a temp file next
  to its target and published with ``os.replace``; an interrupted extract never leaves a truncated
  file under the final name.

Every selected member is validated *before* anything is written. Extraction is selective
(``members`` takes exact names or globs, matched against the full name or the basename) and
idempotent (a target already present with the declared size is skipped). Stdlib only.
"""

from __future__ import annotations

import fnmatch
import os
import stat
import tarfile
import zipfile
from dataclasses import dataclass
from typing import IO, Any

__all__ = [
    "MAX_FILES",
    "MAX_RATIO",
    "MAX_TOTAL_BYTES",
    "UnsafeArchive",
    "archive_kind",
    "list_members",
    "missing_patterns",
    "safe_extract",
    "select_members",
]

MAX_FILES = 10_000
MAX_TOTAL_BYTES = 64 * 1024**3
MAX_RATIO = 200
_CHUNK = 1024 * 1024

_TAR_EXTS = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")


class UnsafeArchive(ValueError):
    """An archive member failed a safety check (traversal, link, bomb cap, size lie)."""


def archive_kind(path) -> str | None:
    """``"zip"``, ``"tar"`` or ``None``: by extension, falling back to content sniffing."""
    p = os.fspath(path)
    low = p.lower()
    if low.endswith(".zip"):
        return "zip"
    if low.endswith(_TAR_EXTS):
        return "tar"
    if not os.path.isfile(p):
        return None
    if zipfile.is_zipfile(p):
        return "zip"
    try:
        if tarfile.is_tarfile(p):
            return "tar"
    except OSError:  # pragma: no cover - unreadable file
        return None
    return None


@dataclass
class _Member:
    name: str
    size: int
    compressed: int | None  # zip only
    handle: Any  # ZipInfo | TarInfo


def _kind_or_raise(path) -> str:
    kind = archive_kind(path)
    if kind is None:
        raise UnsafeArchive(f"not a zip or tar archive: {os.fspath(path)}")
    return kind


def _zip_is_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK(info.external_attr >> 16)


def _scan(path, kind: str) -> list:
    """Every non-directory member as a :class:`_Member` (links/specials kept, for rejection)."""
    out = []
    if kind == "zip":
        with zipfile.ZipFile(path) as zf:
            for zi in zf.infolist():
                if zi.is_dir():
                    continue
                out.append(_Member(zi.filename, zi.file_size, zi.compress_size, zi))
    else:
        with tarfile.open(path, "r:*") as tf:
            for ti in tf.getmembers():
                if ti.isdir():
                    continue
                out.append(_Member(ti.name, ti.size, None, ti))
    return out


def list_members(path) -> list:
    """Regular-file member names of the archive at ``path`` (directories excluded)."""
    kind = _kind_or_raise(path)
    names = []
    for m in _scan(path, kind):
        h = m.handle
        if isinstance(h, tarfile.TarInfo) and not h.isfile():
            continue
        if isinstance(h, zipfile.ZipInfo) and _zip_is_symlink(h):
            continue
        names.append(m.name)
    return names


def _matches(name: str, pattern: str) -> bool:
    if name == pattern:
        return True
    base = name.rsplit("/", 1)[-1]
    return fnmatch.fnmatchcase(name, pattern) or fnmatch.fnmatchcase(base, pattern)


def select_members(names, patterns) -> list:
    """Members of ``names`` matching any of ``patterns`` (all when ``None``), in archive order.

    A pattern is an exact member name or an ``fnmatch`` glob tried against the full name and the
    basename, so ``"PFPU_FaultFree.csv"``, ``"*/PFPU_*.csv"`` and ``"dir/file.csv"`` all work.
    """
    names = list(names)
    if patterns is None:
        return list(dict.fromkeys(names))
    pats = list(patterns)
    return list(dict.fromkeys(n for n in names if any(_matches(n, p) for p in pats)))


def missing_patterns(names, patterns) -> list:
    """The ``patterns`` that match no member of ``names`` (empty when ``patterns`` is None)."""
    if patterns is None:
        return []
    names = list(names)
    return [p for p in patterns if not any(_matches(n, p) for n in names)]


def _check_name(name: str) -> None:
    if not name or "\x00" in name:
        raise UnsafeArchive(f"empty or NUL-containing member name: {name!r}")
    if "\\" in name:
        raise UnsafeArchive(f"backslash in member name (possible traversal): {name!r}")
    if name.startswith("/") or (len(name) >= 2 and name[1] == ":"):
        raise UnsafeArchive(f"absolute member path: {name!r}")
    if ".." in name.split("/"):
        raise UnsafeArchive(f"parent-directory traversal in member: {name!r}")


def _check_type(m: _Member) -> None:
    h = m.handle
    if isinstance(h, tarfile.TarInfo):
        if h.issym() or h.islnk():
            raise UnsafeArchive(f"link member refused: {m.name!r}")
        if not h.isfile():
            raise UnsafeArchive(f"special (device/fifo) member refused: {m.name!r}")
    elif isinstance(h, zipfile.ZipInfo) and _zip_is_symlink(h):
        raise UnsafeArchive(f"symlink member refused: {m.name!r}")


def _target(dest_real: str, name: str, flatten: bool) -> str:
    rel = name.rsplit("/", 1)[-1] if flatten else name
    target = os.path.realpath(os.path.join(dest_real, *rel.split("/")))
    if os.path.commonpath([dest_real, target]) != dest_real or target == dest_real:
        raise UnsafeArchive(f"member escapes the destination: {name!r}")
    return target


def _copy(src, tmp: str, *, name: str, declared: int, budget: int) -> int:
    written = 0
    with open(tmp, "wb") as out:
        while True:
            chunk = src.read(_CHUNK)
            if not chunk:
                break
            written += len(chunk)
            if written > declared:
                raise UnsafeArchive(f"member {name!r} is larger than its declared size")
            if written > budget:
                raise UnsafeArchive(f"extracting {name!r} exceeds the total-size cap")
            out.write(chunk)
    return written


def safe_extract(
    path,
    dest,
    *,
    members=None,
    flatten: bool = False,
    max_files: int = MAX_FILES,
    max_total_bytes: int = MAX_TOTAL_BYTES,
    max_ratio: float = MAX_RATIO,
    overwrite: bool = False,
) -> list:
    """Validate then extract the selected ``members`` of ``path`` under ``dest``.

    Returns the absolute paths of the selected members' targets (including ones skipped because
    they were already present with the declared size). Raises :class:`UnsafeArchive` -- before
    writing anything -- if any selected member fails a check; see the module docstring.
    """
    kind = _kind_or_raise(path)
    scanned = _scan(path, kind)
    chosen = set(select_members([m.name for m in scanned], members))
    sel = [m for m in scanned if m.name in chosen]
    if len(sel) > max_files:
        raise UnsafeArchive(f"{len(sel)} members exceeds the cap of {max_files}")
    os.makedirs(dest, exist_ok=True)
    dest_real = os.path.realpath(os.fspath(dest))
    total = 0
    plan = []
    seen: set = set()
    for m in sel:
        _check_name(m.name)
        _check_type(m)
        target = _target(dest_real, m.name, flatten)
        if target in seen:
            raise UnsafeArchive(f"two members extract to the same path: {m.name!r}")
        seen.add(target)
        if m.compressed is not None and m.size / max(m.compressed, 1) > max_ratio:
            raise UnsafeArchive(
                f"member {m.name!r} compression ratio exceeds {max_ratio} (zip bomb?)"
            )
        total += m.size
        if total > max_total_bytes:
            raise UnsafeArchive(f"declared total size exceeds the cap of {max_total_bytes} bytes")
        plan.append((m, target))

    out = []
    budget = max_total_bytes
    opener = zipfile.ZipFile(path) if kind == "zip" else tarfile.open(path, "r:*")
    with opener as arc:
        for m, target in plan:
            out.append(target)
            if not overwrite and os.path.isfile(target) and os.path.getsize(target) == m.size:
                budget -= m.size
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            tmp = f"{target}.tmp-{os.getpid()}"
            try:
                src: IO[bytes] | None
                if isinstance(arc, zipfile.ZipFile):
                    src = arc.open(m.handle)
                else:
                    src = arc.extractfile(m.handle)
                if src is None:  # pragma: no cover - regular files always have a stream
                    raise UnsafeArchive(f"member {m.name!r} has no data stream")
                with src:
                    budget -= _copy(src, tmp, name=m.name, declared=m.size, budget=budget)
                os.replace(tmp, target)
            except BaseException:
                if os.path.exists(tmp):
                    os.remove(tmp)
                raise
    return out
