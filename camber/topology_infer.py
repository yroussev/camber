"""Infer a served-by :class:`~camber.model.topology.Topology` from equipment naming / spaces.

When a building carries no semantic topology (no Brick ``feeds``, no Haystack ``ahuRef``), the only
remaining signal for "which air handler serves this zone" is **convention**: a shared space label,
or an id-prefix like ``AHU_1_VAV_3`` sitting under ``AHU_1``. This module turns those conventions
into a topology stamped ``provenance="heuristic"`` — a **guess**, not a verified edge — so a
consumer knows to caveat it (the honest-degradation contract of :mod:`camber.model.topology`).

It is deliberately conservative: it only links a terminal to an air handler when the evidence points
to **exactly one** candidate; conflicting or absent evidence yields no edge, not a wrong guess.
Yield is modest in practice — ``Equip.space`` is often unset and ids are not always prefixed — which
is expected: this is the fallback of last resort, below the semantic builders.

:func:`topology_from_config` (0.91, provisional) is the opposite end: a served-by map the site
*declares* -- an explicit ``{child: parent}`` mapping and/or CSV files such as a controls
contractor's VAV-to-AHU schedule -- stamped ``provenance="explicit"``. A config run hands it to
every grouping-aware rule in place of the naming guess, and records where it came from.
"""

from __future__ import annotations

import csv
import os
import re

from .model.topology import Topology

_AHU_CLASSES = ("AHU", "RTU", "DOAS")
_TERMINAL_CLASSES = ("VAV", "CAV", "FCAV", "FCU")


def _as_equips(equips):
    """Accept a ``Site`` (use its ``.equips``) or any iterable of ``Equip``."""
    inner = getattr(equips, "equips", None)
    return list(inner) if inner is not None else list(equips)


def _under(term_id: str, ahu_id: str) -> bool:
    """``term_id`` sits under ``ahu_id`` by id prefix.

    A dataset scenario (``<equip>__<scenario>``, see :mod:`camber.datasets`) is honoured: the
    terminal ``RTU_VAV_106__stuck_040`` sits under ``RTU__stuck_040`` -- the same scenario, and
    the base ids nest -- and never under another scenario's air handler.
    """
    if term_id.startswith(ahu_id + "_"):
        return True
    t_base, t_sep, t_scen = term_id.partition("__")
    a_base, a_sep, a_scen = ahu_id.partition("__")
    return bool(t_sep and a_sep and t_scen == a_scen and t_base.startswith(a_base + "_"))


def topology_from_naming(
    equips,
    *,
    ahu_classes: tuple = _AHU_CLASSES,
    terminal_classes: tuple = _TERMINAL_CLASSES,
) -> Topology:
    """Guess a served-by topology from equipment ids / space labels (``provenance="heuristic"``).

    ``equips`` is a :class:`~camber.model.entities.Site` or an iterable of
    :class:`~camber.model.entities.Equip`. For each terminal (VAV/CAV/FCAV/FCU), it links to an air
    handler (AHU/RTU/DOAS) by, in order: (1) a **shared space label** — the terminal's ``space``
    equals the AHU's id or the AHU's ``space``; (2) an **id-prefix** — the terminal id begins with
    ``"<ahu_id>_"`` (for dataset scenario equipment ``<equip>__<scenario>``: the same scenario
    and nested base ids). An edge is emitted only when exactly one AHU matches; ambiguous or
    unmatched terminals are skipped (a guess is never forced). ``ahu_classes`` /
    ``terminal_classes`` are overridable for non-standard class names.
    """
    items = _as_equips(equips)
    ahus = [e for e in items if getattr(e, "equip_class", "") in ahu_classes]
    terminals = [e for e in items if getattr(e, "equip_class", "") in terminal_classes]

    edges: list = []
    for term in terminals:
        space = getattr(term, "space", "") or ""
        # rule 1: shared space label (highest precision)
        by_space = {
            ahu.id
            for ahu in ahus
            if space and (space == ahu.id or space == (getattr(ahu, "space", "") or ""))
        }
        if len(by_space) == 1:
            edges.append((next(iter(by_space)), term.id))
            continue
        if by_space:
            continue  # ambiguous space -> no guess
        # rule 2: id-prefix containment (AHU_1_VAV_3 under AHU_1), scenario-aware
        by_prefix = {ahu.id for ahu in ahus if _under(term.id, ahu.id)}
        if len(by_prefix) == 1:
            edges.append((next(iter(by_prefix)), term.id))
        # 0 or >1 matches -> skip

    return Topology.from_edges(edges, provenance="heuristic")


#: CSV column names read as the served (child) and serving (parent) equipment, in order of
#: preference, when a topology CSV entry does not name its columns.
CHILD_COLUMNS = ("child", "vav_id", "vav", "zone", "terminal", "ahu_id", "equip", "equipment")
PARENT_COLUMNS = (
    "parent",
    "parent_ahu",
    "ahu",
    "served_by",
    "parent_plant",
    "plant",
    "parent_equip",
)


def _norm_id(x: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(x).upper())


def _csv_pairs(path: str, child_col=None, parent_col=None) -> list:
    """``[(child, parent), ...]`` from one CSV (header row required)."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        return []
    cols = list(rows[0].keys())
    lower = {str(c).strip().lower(): c for c in cols}

    def pick(named, prefs, other=None):
        if named:
            if named not in cols:
                raise ValueError(f"topology CSV {os.path.basename(path)!r} has no column {named!r}")
            return named
        for p in prefs:
            if p in lower and lower[p] != other:
                return lower[p]
        return None

    c = pick(child_col, CHILD_COLUMNS)
    pcol = pick(parent_col, PARENT_COLUMNS, other=c)
    if (c is None or pcol is None) and len(cols) == 2:
        c, pcol = cols[0], cols[1]
    if c is None or pcol is None or c == pcol:
        raise ValueError(
            f"topology CSV {os.path.basename(path)!r}: cannot tell the child and parent columns "
            f"apart ({cols}); name them with 'child' / 'parent' in the config entry"
        )
    out = []
    for r in rows:
        a, b = str(r.get(c) or "").strip(), str(r.get(pcol) or "").strip()
        if a and b:
            out.append((a, b))
    return out


def topology_from_config(spec, *, base_dir: str = ".", equip_ids=()) -> tuple:
    """Build the served-by topology a config declares; return ``(Topology, provenance dict)``.

    ``spec`` is the config's ``"topology"`` section::

        {"parents": {"VAV-101": "AHU-1", "AHU-1": ["CH-1", "CH-2"]},
         "csv": ["vav_to_ahu.csv", {"path": "ahu_to_plant.csv", "child": "ahu", "parent": "plant"}],
         "match": "normalized"}

    ``parents`` maps a served (child) equipment to the equipment serving it -- one id or a list
    (an AHU fed by two chillers). ``csv`` is one path or a list of paths / ``{"path", "child",
    "parent"}`` entries; a CSV without named columns is read by the usual headers (``vav_id`` /
    ``parent_ahu``, ``child`` / ``parent``, ...; see :data:`CHILD_COLUMNS` / :data:`PARENT_COLUMNS`)
    or, with exactly two columns, as child then parent. Paths resolve against ``base_dir``.

    ``match`` (default ``"normalized"``) maps each id onto the discovered ``equip_ids`` ignoring
    case and separators (``VAV_101`` names ``VAV-101``); ``"exact"`` keeps ids as written. An id
    that names no discovered equipment keeps its spelling and is counted in the provenance as
    unmatched. The topology is ``provenance="explicit"``: a declared map, which grouping-aware
    rules use instead of the naming heuristic.
    """
    if not isinstance(spec, dict):
        raise ValueError('"topology" must be an object, e.g. {"csv": "vav_to_ahu.csv"}')
    unknown = set(spec) - {"parents", "csv", "match"}
    if unknown:
        raise ValueError(f"unknown topology key(s): {sorted(unknown)}")
    match = str(spec.get("match", "normalized"))
    if match not in ("normalized", "exact"):
        raise ValueError(f"topology.match must be 'normalized' or 'exact', not {match!r}")
    pairs: list = []
    files: list = []
    for child, parents in (spec.get("parents") or {}).items():
        for par in [parents] if isinstance(parents, str) else list(parents):
            pairs.append((str(child), str(par)))
    entries = spec.get("csv") or []
    if isinstance(entries, (str, dict)):
        entries = [entries]
    for ent in entries:
        ent = {"path": ent} if isinstance(ent, str) else dict(ent)
        path = ent["path"]
        full = path if os.path.isabs(path) else os.path.join(base_dir, path)
        got = _csv_pairs(full, ent.get("child"), ent.get("parent"))
        pairs += got
        files.append({"file": os.path.basename(path), "rows": len(got)})
    known = [str(e) for e in equip_ids]
    by_norm: dict = {}
    for e in known:
        by_norm.setdefault(_norm_id(e), []).append(e)
    known_set = set(known)
    unmatched: set = set()

    def resolve_id(x: str) -> str:
        if x in known_set:
            return x
        if match == "normalized":
            cands = by_norm.get(_norm_id(x), [])
            if len(cands) == 1:
                return cands[0]
        unmatched.add(x)
        return x

    edges = [(resolve_id(par), resolve_id(child)) for child, par in pairs]
    topo = Topology.from_edges(edges, provenance="explicit")
    prov = {
        "source": "config",
        "provenance": topo.provenance,
        "files": files,
        "n_mapped": len(spec.get("parents") or {}),
        "n_edges": len(topo.edges),
        "match": match,
        "unmatched_ids": sorted(unmatched) if known else [],
        "dropped_cycle_edges": [list(e) for e in topo.dropped_cycle_edges],
    }
    return topo, prov


__all__ = ["CHILD_COLUMNS", "PARENT_COLUMNS", "topology_from_config", "topology_from_naming"]
