"""Which stored facilities an assistant adapter may serve, decided fail-closed (0.111, #290).

The capability registry's research-only refusal (:mod:`camber.capabilities._policy`) and the
``camber mcp --allow-all-active`` lifecycle filter (:mod:`camber.mcp._launch`) both read the
store's facility registry (``_facilities.json``). The store itself reads a missing or damaged
registry as empty, which there would widen what is served: an NC / ND facility would lose its
dataset block and every facility would count as active. This module reads it the other way:

- :func:`read_registry` reports a registry it cannot trust (unreadable, not a JSON object, an
  entry that is not an object, missing where one must exist, or -- #312 -- a research-only entry
  whose facility directory is missing, the mark of a renamed or moved partition) as a *problem*
  instead of an empty registry. A missing registry is a problem in a store holding a ``ds-``
  facility (``camber datasets ingest`` always registers one); a store of the user's own data may
  have none, and its unregistered facilities count as active, as everywhere else in CAMBER.
- :func:`catalog_entries_for` maps a facility id to the in-package catalog entries it was ingested
  from by name alone (``ds-<base>`` or ``ds-<base>-<group>``, where ``ingest.facility`` is the
  base), independent of the registry.
- :func:`refusal` says why a facility may not be served: research-only by the catalog (by name)
  or by its registry entry; a copy of a research-only facility's part files under another id
  (#312); a registry problem; a ``ds-`` facility with no registry entry, or one that neither the
  catalog nor a recorded dataset block establishes as open. There is no override.
- :func:`active_facilities` is the lifecycle filter: no facility is active when the registry has
  a problem, and a ``ds-`` facility with no entry is not active either.

These checks catch a facility directory that was renamed, moved or copied, not a deliberate edit
of the registry or of the data: the store's integrity is its operator's responsibility.

Shared by every adapter (MCP, and the A2A server after it); it imports neither. Private module --
not public API.
"""

from __future__ import annotations

import json
import os

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


def _read_regular(path: str) -> str:
    """The text of the regular file at ``path``; ``OSError`` for anything else (a FIFO, whose
    open would wait for a writer, or a device), without blocking (#314)."""
    import stat

    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as fh:
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
            raise OSError(f"{path} is not a regular file")
        return fh.read().decode("utf-8")


def read_registry(store_root: str) -> tuple:
    """``(entries, problem)`` for the facility registry of the store at ``store_root``.

    ``entries`` is ``{facility_id: entry}`` in the registry's v2 shape; ``problem`` is ``None``
    when the registry can be trusted, else a short phrase (``"cannot be read"``, ...) completing
    "the store's facility registry ...", and ``entries`` is then empty.
    """
    from .store.facilities import _FACILITIES, _normalize

    path = os.path.join(store_root, _FACILITIES)
    if not os.path.lexists(path):
        if any(_is_dataset_name(f) for f in _partitions(store_root)):
            return {}, "is missing although the store holds catalog-dataset facilities"
        return {}, None
    try:
        raw = json.loads(_read_regular(path))
    except (OSError, ValueError):  # unreadable, empty, truncated, not UTF-8, not JSON, no file
        return {}, "cannot be read"
    if not isinstance(raw, dict) or not all(isinstance(e, dict) for e in raw.values()):
        return {}, "is not a valid facility registry"
    entries = {str(fid): _normalize(str(fid), e) for fid, e in raw.items()}
    if any(_is_research_only(f, m) and not _present(store_root, f) for f, m in entries.items()):
        return {}, (
            "records research-only data for a facility whose directory is missing (renamed or "
            "moved?)"
        )
    return entries, None


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
    """The relative paths of a facility partition's files (to the month level), sorted; names
    only, no stat call."""
    fdir = os.path.join(store_root, f"facility_id={facility_id}")
    out: list = []

    def walk(path: str, rel: str, depth: int) -> None:
        with os.scandir(path) as it:
            for e in it:
                if depth and e.is_dir():
                    walk(e.path, f"{rel}{e.name}/", depth - 1)
                elif e.name.endswith(".parquet"):
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
