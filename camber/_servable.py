"""Which stored facilities an assistant adapter may serve, decided fail-closed (0.111, #290).

The capability registry's research-only refusal (:mod:`camber.capabilities._policy`) and the
``camber mcp --allow-all-active`` lifecycle filter (:mod:`camber.mcp._launch`) both read the
store's facility registry (``_facilities.json``). The store itself reads a missing or damaged
registry as empty, which there would widen what is served: an NC / ND facility would lose its
dataset block and every facility would count as active. This module reads it the other way:

- :func:`read_registry` reports a registry it cannot trust (unreadable, not a JSON object, an
  entry that is not an object, or missing where one must exist) as a *problem* instead of an empty
  registry. A missing registry is a problem in a store holding a ``ds-`` facility (``camber
  datasets ingest`` always registers one); a store of the user's own data may have none, and its
  unregistered facilities count as active, as everywhere else in CAMBER.
- :func:`catalog_entries_for` maps a facility id to the in-package catalog entries it was ingested
  from by name alone (``ds-<base>`` or ``ds-<base>-<group>``, where ``ingest.facility`` is the
  base), independent of the registry.
- :func:`refusal` says why a facility may not be served: research-only by the catalog (by name)
  or by its registry entry; a registry problem; a ``ds-`` facility with no registry entry, or one
  that neither the catalog nor a recorded dataset block establishes as open. There is no override.
- :func:`active_facilities` is the lifecycle filter: no facility is active when the registry has
  a problem, and a ``ds-`` facility with no entry is not active either.

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
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):  # unreadable, empty, truncated, not UTF-8, not JSON
        return {}, "cannot be read"
    if not isinstance(raw, dict) or not all(isinstance(e, dict) for e in raw.values()):
        return {}, "is not a valid facility registry"
    return {str(fid): _normalize(str(fid), e) for fid, e in raw.items()}, None


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
