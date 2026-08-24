"""Shared topology-grouping resolution for the request-census fleet rules (private).

The rogue-zone census and the cohort-starvation rule both turn a served-by
:class:`camber.model.topology.Topology` into a ``{zone: ahu}`` grouping and attach the *same*
provenance/coverage-aware caveat -- a semantic grouping drops the confound caveat, a heuristic one
softens it, partial coverage pools the remainder, and no grouping keeps the full building-wide
caveat. Keeping that matrix in one place guarantees the two twins stay byte-identical.
"""

from __future__ import annotations

NO_TOPOLOGY_CAVEAT = (
    "no zone->AHU topology supplied; zones pooled building-wide -- a zone flagged here may simply "
    "serve a different, hotter loop. Screening signal only"
)
HEURISTIC_CAVEAT = (
    "zone->AHU grouping inferred from equipment naming (screening-grade, not a verified served-by "
    "model); a per-AHU finding here is provisional"
)
PARTIAL_CAVEAT = (
    "{n} zone(s) not covered by the served-by model were pooled together (building-wide fallback); "
    "their attribution is confounded"
)
ZERO_COVERAGE_CAVEAT = (
    "served-by topology supplied but covered no evaluated zone; zones pooled building-wide -- "
    "screening signal only"
)


def resolve_grouping(groups, frame_keys, topology):
    """Pick the effective ``{zone: ahu}`` grouping + its provenance/coverage caveats.

    Precedence: a supplied ``topology`` (its ``group_map`` over the actual zones, caveated by its
    ``provenance`` and coverage) beats an explicit ``groups`` beats no grouping (building-wide pool
    + full confound caveat). Returns ``(effective_groups, caveats, provenance, n_ungrouped)``.
    """
    keys = list(frame_keys)
    if topology is not None:
        gm = topology.group_map(keys)  # pred=None -> a zone's direct parent (its AHU)
        if not gm:  # topology covered no evaluated zone -> honest building-wide fallback
            return None, [ZERO_COVERAGE_CAVEAT], topology.provenance, len(keys)
        uncovered = [z for z in keys if z not in gm]
        caveats = []
        if topology.provenance == "heuristic":
            caveats.append(HEURISTIC_CAVEAT)
        if uncovered:
            caveats.append(PARTIAL_CAVEAT.format(n=len(uncovered)))
        return gm, caveats, topology.provenance, len(uncovered)
    if groups is not None:
        uncovered = [z for z in keys if z not in groups] if isinstance(groups, dict) else []
        caveats = [PARTIAL_CAVEAT.format(n=len(uncovered))] if uncovered else []
        return groups, caveats, "explicit", len(uncovered)
    return None, [NO_TOPOLOGY_CAVEAT], None, 0
