"""Which stored facilities an assistant adapter may serve, decided fail-closed (0.111, #290).

The capability registry's research-only refusal (:mod:`camber.capabilities._policy`) and the
``camber mcp --allow-all-active`` lifecycle filter (:mod:`camber.mcp._launch`) both read the
store's facility registry (``_facilities.json``). The store itself reads a missing or damaged
registry as empty, which there would widen what is served: an NC / ND facility would lose its
dataset block and every facility would count as active. This module reads it the other way:

- :func:`read_registry` reports a registry it cannot trust (unreadable, not a JSON object, an
  entry that is not an object, missing where one must exist, or -- #312 -- a research-only entry
  whose facility directory is missing although it is not archived or purged, the mark of a
  renamed or moved partition) as a *problem* instead of an empty registry. A missing registry is
  a problem in a store holding a ``ds-`` facility (``camber datasets ingest`` always registers
  one); a store of the user's own data may have none, and its unregistered facilities count as
  active, as everywhere else in CAMBER.
  A read that fails is tried again for a moment; one that keeps failing while the file or its
  directory changes (a writer replacing it), or with an I/O error, raises the retryable
  :class:`RegistryUnavailable` instead (0.112, #324, #329); a registry that stays a directory,
  unreadable or a symlink loop is a problem. Within :func:`call_snapshot` (each capability call)
  the registry is read once, and the store's own registry reads see that read too.
- :func:`catalog_entries_for` maps a facility id to the in-package catalog entries it was ingested
  from by name alone (``ds-<base>`` or ``ds-<base>-<group>``, where ``ingest.facility`` is the
  base), independent of the registry.
- :func:`refusal` says why a facility may not be served: research-only by the catalog (by name)
  or by its registry entry; a part file marked research-only in its own metadata (0.112, #319:
  :mod:`camber.store._partprov`), whatever facility, directory or store it was copied to; a copy
  of a research-only facility's part files under another id (#312, for parts ingested before
  0.112 carried the mark); a registry problem; a ``ds-`` facility with no registry entry, or one
  that neither the catalog nor a recorded dataset block establishes as open. There is no
  override.
- :func:`active_facilities` is the lifecycle filter: no facility is active when the registry has
  a problem, and a ``ds-`` facility with no entry is not active either.

The registry checks catch a facility directory that was renamed, moved or copied; the part-file
mark (#319) catches research-only data copied in part or whole into any facility or store. Neither
catches a deliberate edit that strips the mark from the files (or data exported out of Parquet and
re-ingested as the user's own): the store's integrity is its operator's responsibility.

Shared by every adapter (MCP, and the A2A server after it); it imports neither. Private module --
not public API.
"""

from __future__ import annotations

import contextlib
import contextvars
import errno
import json
import os
import time

from .store.parquet_store import StoreReadError

#: the prefix ``camber datasets ingest`` gives every facility it writes (``ingest.facility``)
DATASET_PREFIX = "ds-"


def _is_dataset_name(facility_id: str) -> bool:
    return str(facility_id).lower().startswith(DATASET_PREFIX)


def catalog_entries_for(facility_id: str) -> list:
    """The catalog entries whose ingest names ``facility_id``: its ``ingest.facility`` base, or the
    base plus ``-<group>`` (compared without letter case). Usually one; every match counts."""
    from .datasets import catalog

    fid = str(facility_id).lower()
    out = []
    for e in catalog():
        base = str((e.ingest or {}).get("facility") or "").lower()
        if base and (fid == base or fid.startswith(base + "-")):
            out.append(e)
    return out


def _entry_research_only(e) -> bool:
    from .datasets._catalog import is_research_only_licence

    return bool(e.research_only) or is_research_only_licence(str(e.licence or ""))


def research_only_block(meta: dict) -> bool:
    """Whether a facility's registry entry marks research-only (NC / ND) data: its dataset block
    says so, or the catalog's current entry for the block's ``dataset_id`` does."""
    from .datasets._catalog import is_research_only_licence

    block = (meta or {}).get("dataset")
    if not isinstance(block, dict):
        return False
    if block.get("access") == "research_only" or block.get("redistribution") == "prohibited":
        return True
    if block.get("licence") and is_research_only_licence(str(block["licence"])):
        return True
    did = block.get("dataset_id")
    if did:
        try:
            from .datasets import get

            return _entry_research_only(get(str(did)))
        except Exception:  # noqa: BLE001 - an unknown / retired id: the recorded block decides
            return False
    return False


class _NotRegular(OSError):
    """The registry path is not a regular file."""


def _read_regular(path: str) -> bytes:
    """The bytes of the regular file at ``path``; ``OSError`` for anything else (a FIFO, whose
    open would wait for a writer, or a device), without blocking (#314)."""
    import stat

    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as fh:
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
            raise _NotRegular(f"{path} is not a regular file")
        return fh.read()


class RegistryUnavailable(StoreReadError):
    """The facility registry could not be read just now (0.112, #324): it kept changing while it
    was read (a writer replacing it), or reading it failed with an I/O error. Retryable: a
    capability call answers ``unavailable`` with ``"retryable": true`` and ``"reason":
    "registry_unreadable"``."""

    code = "registry_unreadable"

    def __init__(self, cause=None):
        super().__init__(
            "the store's facility registry cannot be read just now (a write may be replacing it)",
            retryable=True,
            cause=cause,
        )


#: seconds to wait before each read of the registry (#324): a writer replaces it with
#: ``os.replace``, which some filesystems (a Docker Desktop bind mount) show a reader as briefly
#: missing, so a read that fails is tried again, about 0.4 s in all
_RETRY_WAITS = (0.0, 0.01, 0.02, 0.05, 0.1, 0.2)
#: store roots whose registry this process has read: it is replaced, never deleted, so it going
#: missing there is retried even in a store with no catalog-dataset facility
_SEEN: set = set()
#: within :func:`call_snapshot`: ``{store root: (raw registry, problem)}``, one read per call
_CALL: contextvars.ContextVar = contextvars.ContextVar("camber_registry_call", default=None)


#: errors that say what the registry path *is* (a directory, a file this user may not read, a
#: symlink loop), not that a read failed just now: one that does not change is a damaged
#: registry, not a retryable one (#329)
_LASTING = frozenset(
    getattr(errno, n)
    for n in ("EISDIR", "EACCES", "EPERM", "ELOOP", "ENOTDIR", "ENAMETOOLONG")
    if hasattr(errno, n)
)


def _read_once(path: str) -> tuple:
    """One read of the registry file: ``("ok", parsed JSON)``, ``("missing", None)``,
    ``("damaged", what)`` (not a regular file, a :data:`_LASTING` error, or bytes that are not
    UTF-8 JSON, or nest too deep to parse) or ``("error", errno)`` (any other ``OSError``)."""
    if not os.path.lexists(path):
        return "missing", None
    try:
        data = _read_regular(path)
    except FileNotFoundError:
        return "missing", None
    except _NotRegular:
        return "damaged", "not a regular file"
    except OSError as e:
        return ("damaged", f"errno {e.errno}") if e.errno in _LASTING else ("error", e.errno)
    try:
        return "ok", json.loads(data.decode("utf-8"))
    except (ValueError, RecursionError):  # empty, truncated, not UTF-8, not JSON, too deep
        return "damaged", data


def _witness(path: str) -> tuple:
    """What changes when the registry is replaced, renamed, rewritten or re-permissioned: its
    directory's mtime and the path's own ``lstat`` (#329: a writer that breaks it again before
    the retries end leaves every read failing alike, but not these)."""
    out: list = []
    for p, fn in ((os.path.dirname(path) or ".", os.stat), (path, os.lstat)):
        try:
            st = fn(p)
        except OSError:
            out.append(None)
            continue
        if p == path:
            out.append((st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns))
        else:
            out.append(st.st_mtime_ns)
    return tuple(out)


def _read_settled(store_root: str) -> tuple:
    """``(raw, problem)``: the registry's parsed JSON (``{}`` when there is none) and ``None``,
    or ``{}`` and the problem of a registry that stays missing or damaged while it is read again
    (#290). ``RegistryUnavailable`` when the reads fail and do not agree, the registry or its
    directory changed between them (#329), or they fail with an I/O error (#324)."""
    from .store.facilities import _FACILITIES

    path = os.path.join(store_root, _FACILITIES)
    root = os.path.abspath(store_root)
    seen: list = []
    expected = None
    for wait in _RETRY_WAITS:
        if wait:
            time.sleep(wait)
        before = _witness(path)
        kind, value = _read_once(path)
        if kind == "ok":
            _SEEN.add(root)
            return value, None
        if kind == "missing" and not seen:
            expected = any(_is_dataset_name(f) for f in _partitions(store_root))
            if not expected and root not in _SEEN:
                return {}, None  # a store of one's own data that never had a registry
        seen.append((kind, value, before, _witness(path)))
    kind, value = seen[0][:2]
    changed = any(o[:2] != (kind, value) or o[2:] != (seen[0][2],) * 2 for o in seen)
    if kind == "error" or changed:
        raise RegistryUnavailable(OSError(value, os.strerror(value)) if kind == "error" else None)
    if kind == "missing":
        if expected:
            return {}, "is missing although the store holds catalog-dataset facilities"
        return {}, None  # removed for good: read as none, as the store does
    return {}, "cannot be read"


@contextlib.contextmanager
def call_snapshot():
    """Within it, every read of a store's facility registry -- :func:`read_registry` and the
    store's own :class:`~camber.store.facilities.FacilityRegistry` -- sees the one read the first
    of them made (0.112, #324): a capability call checks the licence, picks the config template,
    reads the facility's timezone and says the notice from the same registry."""
    token = _CALL.set({})
    try:
        yield
    finally:
        _CALL.reset(token)


def _raw_registry(store_root: str) -> tuple:
    """:func:`_read_settled`, once per store root within a :func:`call_snapshot`."""
    snap = _CALL.get()
    if snap is None:
        return _read_settled(store_root)
    root = os.path.abspath(store_root)
    if root not in snap:
        snap[root] = _read_settled(store_root)
    return snap[root]


def snapshot_raw(store_root: str):
    """The raw registry of the call in progress (a fresh dict; ``{}`` for a registry with a
    problem, as the store reads one), or ``None`` outside a :func:`call_snapshot`."""
    if _CALL.get() is None:
        return None
    raw, problem = _raw_registry(store_root)
    return dict(raw) if problem is None and isinstance(raw, dict) else {}


def read_registry(store_root: str) -> tuple:
    """``(entries, problem)`` for the facility registry of the store at ``store_root``.

    ``entries`` is ``{facility_id: entry}`` in the registry's v2 shape; ``problem`` is ``None``
    when the registry can be trusted, else a short phrase (``"cannot be read"``, ...) completing
    "the store's facility registry ...", and ``entries`` is then empty. A read that fails is
    tried again for a moment; :class:`RegistryUnavailable` when it still fails and the file was
    changing meanwhile, or failed with an I/O error (0.112, #324).
    """
    from .store.facilities import _normalize

    raw, problem = _raw_registry(store_root)
    if problem is not None:
        return {}, problem
    if not isinstance(raw, dict) or not all(isinstance(e, dict) for e in raw.values()):
        return {}, "is not a valid facility registry"
    entries = {str(fid): _normalize(str(fid), e) for fid, e in raw.items()}
    if any(
        _is_research_only(f, m) and m.get("state") not in _GONE and not _present(store_root, f)
        for f, m in entries.items()
    ):
        return {}, (
            "records research-only data for a facility whose directory is missing (renamed or "
            "moved?)"
        )
    return entries, None


#: lifecycle states in which a facility's partition directory is gone by design: ``facility
#: archive`` moves it into a bundle, ``facility purge`` deletes it (#329: not a moved partition)
_GONE = ("archived", "purged")


def _is_research_only(facility_id: str, meta) -> bool:
    """Research-only by its registry entry or, by name, by the catalog."""
    if meta is not None and research_only_block(meta):
        return True
    return any(_entry_research_only(e) for e in catalog_entries_for(facility_id))


def _present(store_root: str, facility_id: str) -> bool:
    """Whether the facility's partition directory exists (or is mid-swap)."""
    from .store import _swap

    fdir = os.path.join(store_root, f"facility_id={facility_id}")
    return os.path.isdir(fdir) or any(os.path.lexists(p) for p in _swap._paths(fdir))


def _part_files(store_root: str, facility_id: str) -> list:
    """The relative paths of a facility partition's part files (to the month level, whatever
    their suffix: :func:`camber.store._partprov.is_part_name`), sorted; names only, no stat
    call."""
    from .store._partprov import is_part_name

    fdir = os.path.join(store_root, f"facility_id={facility_id}")
    out: list = []

    def walk(path: str, rel: str, depth: int) -> None:
        with os.scandir(path) as it:
            for e in it:
                if not is_part_name(e.name):
                    continue
                if depth and e.is_dir():
                    walk(e.path, f"{rel}{e.name}/", depth - 1)
                elif not e.is_dir():
                    out.append(f"{rel}{e.name}")

    try:
        walk(fdir, "", 2)
    except OSError:
        return []
    return sorted(out)


def _sizes(store_root: str, facility_id: str, files: list):
    fdir = os.path.join(store_root, f"facility_id={facility_id}")
    try:
        return [os.stat(os.path.join(fdir, f)).st_size for f in files]
    except OSError:
        return None


def _copies_research_only(store_root: str, facility_id: str, entries: dict) -> bool:
    """Whether ``facility_id``'s partition holds the very part files of a research-only facility
    of the store: the same paths and sizes, and its largest file byte for byte -- a renamed or
    copied partition (#312). Listing only, unless the listings match."""
    import filecmp

    mine = _part_files(store_root, facility_id)
    if not mine:
        return False
    sizes = None
    for other in sorted(set(_partitions(store_root)) - {facility_id}):
        if not _is_research_only(other, entries.get(other)):
            continue
        if _part_files(store_root, other) != mine:
            continue
        sizes = _sizes(store_root, facility_id, mine) if sizes is None else sizes
        if sizes is None or sizes != _sizes(store_root, other, mine):
            continue
        big = mine[max(range(len(mine)), key=sizes.__getitem__)]
        a, b = (os.path.join(store_root, f"facility_id={f}", big) for f in (facility_id, other))
        try:
            if filecmp.cmp(a, b, shallow=False):
                return True
        except OSError:
            return True  # a match by every stat that cannot be read through: fail closed
    return False


def _marked_research_only(store_root: str, facility_id: str) -> str | None:
    """The refusal message when one of the facility's part files carries a research-only
    provenance mark (0.112, #319): data ingested from such a dataset, wherever it was copied
    since. Footers only, memoized until the partition changes; ``None`` when there is none."""
    from .store import _partprov

    value = _partprov.research_only_value(_partprov.facility_marks(store_root, facility_id))
    if value is None:
        return None
    did = _partprov.dataset_of(value)
    return (
        f"facility {facility_id!r} holds part files marked as research-only (non-commercial / "
        "no-derivatives) data" + (f" of dataset {did!r}" if did else "")
    )


def _partitions(store_root: str) -> list:
    try:
        names = os.listdir(store_root)
    except OSError:
        return []
    return [n.split("=", 1)[1] for n in names if n.startswith("facility_id=")]


def registry_banner(problem: str) -> str:
    """The notice an adapter shows (stderr and every result) for a registry ``problem``."""
    return (
        f"The store's facility registry {problem}, so no facility's licence or lifecycle can be "
        "established: no facility is served until the registry is repaired (for example "
        "restored from a backup, or rebuilt by re-ingesting)."
    )


def refusal(facility_id: str, store_root: str | None, registry: tuple | None = None):
    """``None`` when ``facility_id`` may be served, else ``(kind, message)``: ``kind`` is
    ``"research_only"`` or ``"unverified"`` (its licence or lifecycle cannot be established).

    ``store_root=None`` checks a facility that has no store registry (a folder-backed config): only
    its name is checked against the catalog. ``registry`` is :func:`read_registry`'s result, when
    the caller already has it.
    """
    fid = str(facility_id)
    research = f"facility {fid!r} holds research-only (non-commercial / no-derivatives) data"
    matches = catalog_entries_for(fid)
    if any(_entry_research_only(e) for e in matches):
        return "research_only", research
    if store_root is None:
        return None
    entries, problem = registry if registry is not None else read_registry(store_root)
    if problem is not None:
        return "unverified", (
            f"facility {fid!r} cannot be served: the store's facility registry {problem}, so its "
            "licence and lifecycle cannot be established"
        )
    meta = entries.get(fid)
    if meta is not None and research_only_block(meta):
        return "research_only", research
    marked = _marked_research_only(store_root, fid)
    if marked is not None:
        return "research_only", marked
    if _copies_research_only(store_root, fid, entries):
        return "research_only", (
            f"facility {fid!r} holds the same part files as a research-only (non-commercial / "
            "no-derivatives) facility of this store"
        )
    if _is_dataset_name(fid):
        if meta is None:
            return "unverified", (
                f"facility {fid!r} is named for a catalog dataset but the store's facility "
                "registry has no entry for it, so its licence and lifecycle cannot be established"
            )
        if not matches and not isinstance(meta.get("dataset"), dict):
            return "unverified", (
                f"facility {fid!r} is named for a catalog dataset, but neither the catalog nor its "
                "registry entry records the dataset, so its licence cannot be established"
            )
    return None


def active_facilities(store_root: str) -> tuple:
    """``(ids, warnings)``: the facilities of the store at ``store_root`` that are active, read
    fail-closed. None when the registry has a problem; an unregistered facility of the user's own
    data counts as active (as in :meth:`camber.store.ParquetStore.active_facilities`), an
    unregistered ``ds-`` facility does not."""
    entries, problem = read_registry(store_root)
    if problem is not None:
        return [], [registry_banner(problem)]
    ids, unknown = [], []
    for fid in sorted(_partitions(store_root)):
        meta = entries.get(fid)
        if meta is None and _is_dataset_name(fid):
            unknown.append(fid)
        elif (meta or {}).get("state", "active") == "active":
            ids.append(fid)
    warnings = []
    if unknown:
        warnings.append(
            f"{len(unknown)} facilit{'y' if len(unknown) == 1 else 'ies'} named for a catalog "
            "dataset ha" + ("s" if len(unknown) == 1 else "ve") + " no entry in the store's "
            "facility registry, so the lifecycle is unknown and not counted as active: "
            + ", ".join(unknown)
        )
    return ids, warnings
