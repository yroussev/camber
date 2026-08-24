"""Fleet rule: which zone is dragging an air handler's G36 reset?

The Trim-&-Respond family so far looks at the reset *setpoint* — does it sit at the right target
(:class:`camber.rules.satreset_compliance_rule.SupplyAirResetCompliance`), does it actually trim and
respond (:class:`camber.rules.reset_effectiveness_rule.ResetEffectiveness`)? This rule looks at the
*demand side*: in G36 the SAT / duct-static reset responds to the high-percentile of per-zone
**requests** (§5.14.8), so **one chronically over-demanding zone can monopolize the requests and
drag the whole reset** — forcing a colder supply-air temperature or higher duct static than the rest
of the fleet needs. The plant then serves one bad box at everyone else's energy expense.

It is a :class:`camber.rules.base.FleetRule` (many zones in, one aggregate Finding out) wrapping the
pure :func:`camber.g36_reset.rogue_zone_census` analyzer. It ships as two instances —
``sat_rogue_zone_census`` (zone temp vs cooling setpoint) and ``static_rogue_zone_census`` (zone
airflow vs setpoint and damper) — sharing one engine.

**Topology honesty.** Deciding that a zone drags *AHU-1's* reset needs to know which zones AHU-1
serves, and the fleet interface carries no served-by topology. So by default the zones are pooled
building-wide and the rule attaches a loud confound caveat (a zone flagged here may simply serve a
hotter loop — a screening signal only). When a caller supplies a ``groups`` map (``{zone: ahu}`` or
a ``zone -> ahu`` callable) the census scopes per air handler and the caveat drops. Automatic
zone→AHU discovery is the remaining deferred piece. Thresholds are screening / opportunity-grade.
"""

from __future__ import annotations

from typing import Any

from ..g36_reset import rogue_zone_census
from ..model.roles import Role
from .base import Finding

# reset key -> (roles_required, human label)
_RESETS: dict[str, tuple[tuple[Role, ...], str]] = {
    "sat": ((Role.SPACE_TEMP, Role.COOL_SP), "supply-air-temp"),
    "static": ((Role.AIRFLOW, Role.AIRFLOW_SP, Role.DAMPER), "duct-static"),
}

_NO_TOPOLOGY_CAVEAT = (
    "no zone->AHU topology supplied; zones pooled building-wide -- a zone flagged here may simply "
    "serve a different, hotter loop. Screening signal only"
)
_HEURISTIC_CAVEAT = (
    "zone->AHU grouping inferred from equipment naming (screening-grade, not a verified served-by "
    "model); a per-AHU rogue here is provisional"
)
_PARTIAL_CAVEAT = (
    "{n} zone(s) not covered by the served-by model were pooled together (building-wide fallback); "
    "their attribution is confounded"
)
_ZERO_COVERAGE_CAVEAT = (
    "served-by topology supplied but covered no evaluated zone; zones pooled building-wide -- "
    "screening signal only"
)


class RogueZoneCensus:
    """Flags the zone(s) monopolizing a G36 reset and dragging it for the whole air handler.

    Computes each zone's per-cycle reset-request rate across the fleet and flags a **rogue** — a
    zone that both holds the binding (maximum) request a dominant fraction of the active cycles and
    commands a disproportionate share of the group's total requests. ``reset`` selects the family:
    ``"sat"`` (zone temp vs cooling setpoint) or ``"static"`` (zone airflow vs setpoint and damper).
    Warn-level (an operational opportunity, not a hard fault). Pools zones building-wide with a
    confound caveat unless a ``groups`` (``{zone: ahu}`` dict or ``zone -> ahu`` callable) is given.
    """

    def __init__(
        self,
        reset: str = "sat",
        *,
        groups=None,
        dominance_frac: float = 0.50,
        share_mult: float = 2.0,
        min_share: float = 0.30,
        min_active_cycles: int = 10,
        min_zones_per_group: int = 2,
    ):
        if reset not in _RESETS:
            raise ValueError(f"reset must be one of {sorted(_RESETS)}, got {reset!r}")
        self.reset = reset
        roles, label = _RESETS[reset]
        self.name = f"{reset}_rogue_zone_census"
        self.roles_required = roles
        self.roles_optional: tuple[Role, ...] = ()
        self._label = label
        self.groups = groups
        self.wants_topology = True  # run_fleet auto-builds a naming grouping when none is supplied
        self._kwargs: dict[str, Any] = {
            "dominance_frac": dominance_frac,
            "share_mult": share_mult,
            "min_share": min_share,
            "min_active_cycles": min_active_cycles,
            "min_zones_per_group": min_zones_per_group,
        }

    def _cols(self) -> dict:
        if self.reset == "sat":
            return {"temp_col": Role.SPACE_TEMP, "cool_sp_col": Role.COOL_SP}
        return {
            "flow_col": Role.AIRFLOW,
            "flow_sp_col": Role.AIRFLOW_SP,
            "damper_col": Role.DAMPER,
        }

    def _resolve_grouping(self, frame_keys, topology):
        """Pick the effective ``{zone: ahu}`` grouping + its provenance/coverage caveats.

        Precedence: a supplied ``topology`` (its ``group_map`` over the actual zones, with a caveat
        keyed to its ``provenance`` and coverage) beats a constructor ``groups`` beats no grouping
        (today's building-wide pool + full confound caveat). Returns
        ``(effective_groups, caveats, provenance, n_ungrouped)``.
        """
        keys = list(frame_keys)
        if topology is not None:
            gm = topology.group_map(keys)  # pred=None -> a zone's direct parent (its AHU)
            if not gm:  # topology covered no evaluated zone -> honest building-wide fallback
                return None, [_ZERO_COVERAGE_CAVEAT], topology.provenance, len(keys)
            uncovered = [z for z in keys if z not in gm]
            caveats = []
            if topology.provenance == "heuristic":
                caveats.append(_HEURISTIC_CAVEAT)
            if uncovered:
                caveats.append(_PARTIAL_CAVEAT.format(n=len(uncovered)))
            return gm, caveats, topology.provenance, len(uncovered)
        if self.groups is not None:
            uncovered = (
                [z for z in keys if z not in self.groups] if isinstance(self.groups, dict) else []
            )
            caveats = [_PARTIAL_CAVEAT.format(n=len(uncovered))] if uncovered else []
            return self.groups, caveats, "explicit", len(uncovered)
        return None, [_NO_TOPOLOGY_CAVEAT], None, 0

    def analyze_fleet(self, frames: dict, *, topology=None) -> Finding:
        """Run the rogue-zone census across the fleet's zone role-frames; one aggregate Finding.

        When a ``topology`` is supplied (semantic from a Brick/Haystack model, or the naming
        one :meth:`Registry.run_fleet` auto-builds), the census scopes **per air handler** and its
        caveat reflects the grouping's provenance and coverage; with none it pools building-wide.
        """
        if not frames:
            return Finding(
                rule=self.name,
                equip="<fleet>",
                severity="info",
                summary=f"no zones carrying {self._label} reset-request signals",
            )

        groups, grouping_caveats, provenance, n_ungrouped = self._resolve_grouping(
            frames.keys(), topology
        )
        res = rogue_zone_census(
            frames,
            reset=self.reset,
            groups=groups,
            **self._cols(),
            **self._kwargs,
        )
        if res is None:
            return Finding(
                rule=self.name,
                equip="<fleet>",
                severity="info",
                metrics={"declined": True},
                summary=f"{self._label} rogue-zone census: too few usable request rows across "
                "the fleet",
                caveats=["rogue-zone census not evaluated: no zone had enough usable request rows"],
            )

        caveats = list(res.caveats) + grouping_caveats

        metrics = {
            "reset": res.reset,
            "grouped": res.grouped,
            "grouping_provenance": provenance,
            "n_zones_ungrouped": n_ungrouped,
            "n_zones_evaluated": res.n_zones_evaluated,
            "n_groups": res.n_groups,
            "total_requests": res.total_requests,
            "rogues": res.rogues,
            "rogue_by_group": res.rogue_by_group,
            "worst_zone": res.worst_zone,
            "worst_zone_share": res.worst_zone_share,
            "zone_request_share": res.zone_request_share,
            "zone_binding_frac": res.zone_binding_frac,
            "unevaluable_zones": res.unevaluable_zones,
        }

        if res.rogues:
            worst = res.worst_zone
            binding = res.zone_binding_frac.get(worst, 0.0)
            share = res.worst_zone_share or 0.0
            summary = (
                f"fleet: zone {worst} monopolizes the {self._label} reset -- holds the binding "
                f"request {binding:.0%} of active cycles and {share:.0%} of total requests "
                f"({len(res.rogues)} rogue zone(s): {', '.join(res.rogues)})"
            )
            return Finding(
                rule=self.name,
                equip="<fleet>",
                severity="warn",
                metrics=metrics,
                caveats=caveats,
                summary=summary,
            )

        # no rogue: an honest "ok" only when there was something to evaluate; otherwise decline
        inconclusive = (
            res.n_zones_evaluated == 0 or res.total_requests == 0 or bool(res.unevaluable_zones)
        )
        if inconclusive:
            return Finding(
                rule=self.name,
                equip="<fleet>",
                severity="info",
                metrics=metrics,
                caveats=caveats,
                summary=(
                    f"{self._label} rogue-zone census inconclusive: "
                    f"{res.n_zones_evaluated} zone(s), {res.total_requests} requests"
                ),
            )
        return Finding(
            rule=self.name,
            equip="<fleet>",
            severity="ok",
            metrics=metrics,
            caveats=caveats,
            summary=(
                f"fleet: {res.n_zones_evaluated} zones share the {self._label} reset evenly -- "
                "no rogue zone dragging the reset"
            ),
        )
