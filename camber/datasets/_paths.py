"""Where the dataset catalog keeps its downloads, extractions, manifest and licence ledger.

Resolution order for the cache root (first hit wins):

1. an explicit ``data_dir=`` argument (``--dir`` on the CLI),
2. ``$CAMBER_DATA_DIR``,
3. ``$XDG_CACHE_HOME/camber/datasets``,
4. ``~/.cache/camber/datasets``.

Layout under the root::

    <root>/<dataset_id>/downloads/<file>          # verified archives / files (+ .part while busy)
    <root>/<dataset_id>/extracted/<member path>   # selectively extracted archive members
    <root>/manifest.json                          # what was fetched, when, with which sha256
    <root>/acknowledgements.json                  # research-only licence acceptances (append-only)
    <root>/_lock                                  # the cache's single-writer lock (0.103)

Everything here is plain stdlib; JSON writes are atomic (tmp + ``os.replace``).

**Locking (0.103, #124).** Every write to a cache -- a fetch that downloads or records files,
``ingest --from-dir`` placing files, an ingest extracting archive members or recording an
acknowledgement, ``remove`` -- holds :func:`cache_lock`, the single-writer lock of
:mod:`camber.portfolio._lock` on ``<root>/_lock``. A second process waits up to
:data:`CACHE_LOCK_TIMEOUT` seconds, then gets :class:`CacheLocked` naming the holder. Reads take no
lock: files are published with ``os.replace``, so a reader sees a whole file or none.

**Read-only caches.** A cache the current user cannot write (:func:`writable` is false) -- one
shared read-only across a classroom -- works for everything that needs no write: a fetch whose
files are all present and verify is a no-op, and an ingest reads the downloads and any members
already extracted. Archive members not yet extracted go to a scratch directory beside the
ingest's staging area in the *store* (removed when the ingest ends), and a research-only
acknowledgement goes to the per-user ledger in :func:`user_state_dir` (see :mod:`._licence`).
Anything that must write the cache (a download, ``--from-dir``, ``remove``) raises
:class:`CacheReadOnly`.
"""

from __future__ import annotations

import datetime as _dt
import json
import os

from ..portfolio._lock import LockHeld, exclusive_lock

MANIFEST = "manifest.json"
ACKS = "acknowledgements.json"
CACHE_LOCK_TIMEOUT = 30.0  # seconds a second writer waits before it is refused


class CacheLocked(LockHeld):
    """Another process is writing the dataset cache (it holds ``<cache>/_lock``)."""


class CacheReadOnly(PermissionError):
    """The dataset cache cannot be written by this user, and the operation needs to write it."""


def data_dir(override: str | os.PathLike | None = None) -> str:
    """The cache root for downloaded datasets (see the module docstring for the order)."""
    if override:
        return os.path.abspath(os.fspath(override))
    env = os.environ.get("CAMBER_DATA_DIR")
    if env:
        return os.path.abspath(env)
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = xdg if xdg else os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "camber", "datasets")


def user_state_dir() -> str:
    """Per-user state for the dataset catalog: ``$XDG_STATE_HOME/camber/datasets``, else
    ``~/.local/state/camber/datasets``. Holds the acknowledgements a user gives against a
    read-only cache (``acknowledgements.json``)."""
    xdg = os.environ.get("XDG_STATE_HOME")
    base = xdg if xdg else os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "camber", "datasets")


def writable(path: str) -> bool:
    """Can this user create files in ``path`` (or, if it does not exist yet, create it)?

    Checks the nearest existing directory at or above ``path`` with ``os.access``.
    """
    probe = os.path.abspath(os.fspath(path))
    while not os.path.isdir(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            return False
        probe = parent
    return os.access(probe, os.W_OK | os.X_OK)


def cache_writable(root: str, dataset_id: str | None = None) -> bool:
    """True when the cache root (and ``<root>/<dataset_id>``, if given) can be written."""
    if not writable(root):
        return False
    return dataset_id is None or writable(dataset_dir(root, dataset_id))


def require_writable(root: str, dataset_id: str | None, what: str) -> None:
    """Raise :class:`CacheReadOnly` (saying what needed the write) unless the cache is writable."""
    if not cache_writable(root, dataset_id):
        raise CacheReadOnly(
            f"the dataset cache {root} is read-only for this user, and {what} needs to write it. "
            "Ask whoever maintains the shared cache to fetch it there, or use a cache you can "
            "write (--dir DIR, or $CAMBER_DATA_DIR)."
        )


def cache_lock(root: str, *, timeout: float | None = None):
    """Context manager holding the cache's single-writer lock (``<root>/_lock``; see the module).

    ``timeout`` defaults to :data:`CACHE_LOCK_TIMEOUT`. Re-entrant within a process. Creates
    ``root`` if needed.
    """
    timeout = CACHE_LOCK_TIMEOUT if timeout is None else timeout
    os.makedirs(root, exist_ok=True)
    return exclusive_lock(
        root,
        timeout=timeout,
        what=f"dataset cache {root}",
        error=CacheLocked,
        hint=f" (another fetch, ingest or remove is writing it; waited {timeout:g} s -- retry "
        "when it finishes)",
    )


def dataset_dir(root: str, dataset_id: str) -> str:
    """``<root>/<dataset_id>``."""
    return os.path.join(root, dataset_id)


def downloads_dir(root: str, dataset_id: str) -> str:
    """``<root>/<dataset_id>/downloads`` -- verified files and in-flight ``.part`` files."""
    return os.path.join(root, dataset_id, "downloads")


def extracted_dir(root: str, dataset_id: str) -> str:
    """``<root>/<dataset_id>/extracted`` -- archive members extracted for ingest."""
    return os.path.join(root, dataset_id, "extracted")


def utc_now() -> str:
    """The current UTC time as an ISO-8601 string (seconds precision)."""
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: str, default):
    """Parse a JSON file, returning ``default`` when it is absent or unreadable."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def write_json_atomic(path: str, data) -> None:
    """Write ``data`` as JSON to ``path`` atomically (tmp file + ``os.replace``)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def read_manifest(root: str) -> dict:
    """The fetch manifest ``{dataset_id: {...}}`` (empty when absent)."""
    data = read_json(os.path.join(root, MANIFEST), {})
    return data if isinstance(data, dict) else {}


def write_manifest(root: str, data: dict) -> None:
    """Replace the fetch manifest atomically."""
    write_json_atomic(os.path.join(root, MANIFEST), data)


def read_acknowledgements(root: str) -> list:
    """The research-only licence acceptance ledger (a list of records; empty when absent)."""
    data = read_json(os.path.join(root, ACKS), [])
    return data if isinstance(data, list) else []


def append_acknowledgement(root: str, record: dict) -> dict:
    """Append one acceptance record (stamped with ``accepted_at``) to the ledger; return it.

    ``root`` is the directory holding ``acknowledgements.json``: a cache root, or
    :func:`user_state_dir` for an acknowledgement given against a read-only cache. The caller
    holds the cache lock when ``root`` is a cache.
    """
    rec = dict(record)
    rec.setdefault("accepted_at", utc_now())
    ledger = read_acknowledgements(root)
    ledger.append(rec)
    write_json_atomic(os.path.join(root, ACKS), ledger)
    return rec
