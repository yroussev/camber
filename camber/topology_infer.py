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
"""

from __future__ import annotations

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


__all__ = ["topology_from_naming"]
