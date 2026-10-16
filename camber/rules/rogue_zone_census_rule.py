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
serves. The grouping is resolved in this order
(:func:`camber.rules._topology_grouping.resolve_grouping`):

1. a ``topology`` handed to :meth:`analyze_fleet` -- a semantic Brick/Haystack model, or, since the
   rule sets ``wants_topology``, the naming-based served-by graph that
   :meth:`camber.rules.base.Registry.run_fleet` auto-builds from the equipment refs when none is
   given. The census scopes per air handler; a naming-inferred grouping carries a
   screening-grade caveat, and zones it does not cover are counted and caveated;
2. else an explicit ``groups`` map (``{zone: ahu}`` or a ``zone -> ahu`` callable);
3. else (a topology that covers none of the zones, or a direct call with neither) the zones are
   pooled building-wide with a loud confound caveat (a zone flagged here may simply serve a hotter
   loop -- a screening signal only).

**An overloaded zone is not a static rogue (0.106, #186).** A zone that cannot be cooled -- an
equipment room no box can keep up with -- sits at its cooling maximum with its damper wide open,
so it asks for more duct static as well as for colder air. When the zones trend their space
temperature and cooling setpoint, the static census checks each rogue's static-request cycles: a
zone at least 3 °F above its cooling setpoint on half of them or more is short of *cooling*, the
SAT census's rogue, not a starved inlet. It is listed in ``overloaded_zones`` (with
``overloaded_share``) instead of ``rogues``, and the finding still warns, since it does drag the
duct static up.

Thresholds are screening / opportunity-grade.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from ..g36_reset import rogue_zone_census
from ..model.roles import Role
from ._topology_grouping import resolve_grouping
from .base import Finding

# request cycles count only while the system is on: supply-fan status and/or occupancy, when the
# zone frames carry them (per zone, or merged in as a building-level ``shared`` series)
_GATES: tuple[Role, ...] = (Role.SUPPLY_FAN_STATUS, Role.OCCUPANCY)

# reset key -> (roles_required, human label)
# 0.106 (#186): a static rogue >= _OVERLOAD_F above its cooling setpoint on >= _OVERLOAD_SHARE of
# its static-request cycles (damper > _FAN_THR) is overloaded (short of cooling), not starved
_FAN_THR = 95.0
_OVERLOAD_F = 3.0  # G36's 2-request SAT tier
_OVERLOAD_SHARE = 0.5

_RESETS: dict[str, tuple[tuple[Role, ...], str]] = {
    "sat": ((Role.SPACE_TEMP, Role.COOL_SP), "supply-air-temp"),
    "static": ((Role.AIRFLOW, Role.AIRFLOW_SP, Role.DAMPER), "duct-static"),
}


class RogueZoneCensus:
    """Flags the zone(s) monopolizing a G36 reset and dragging it for the whole air handler.

    Computes each zone's per-cycle reset-request rate across the fleet and flags a **rogue** — a
    zone that both holds the binding (maximum) request a dominant fraction of the active cycles and
    commands a disproportionate share of the group's total requests. ``reset`` selects the family:
    ``"sat"`` (zone temp vs cooling setpoint) or ``"static"`` (zone airflow vs setpoint and damper).
    Warn-level (an operational opportunity, not a hard fault). Scopes per air handler from the
    fleet ``topology`` (the runner auto-builds a naming one), else from ``groups`` (a
    ``{zone: ahu}`` dict or ``zone -> ahu`` callable); with neither it pools zones building-wide
    with a confound caveat (see the module docstring for the order).
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
        # gate request cycles on the system being on (a free-floating building isn't demanding)
        self.roles_optional: tuple[Role, ...] = _GATES
        if reset == "sat":
            # 0.106 (#186): the zone cooling loop adds G36's tier-1 SAT request (loop > 95 %)
            self.roles_optional = _GATES + (Role.ZONE_COOL_LOOP,)
        self._label = label
        if reset == "static":
            # 0.106 (#186): tell a zone short of cooling (overloaded) from a starved inlet
            self.roles_optional = _GATES + (Role.SPACE_TEMP, Role.COOL_SP)
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
            return {
                "temp_col": Role.SPACE_TEMP,
                "cool_sp_col": Role.COOL_SP,
                "cool_loop_col": Role.ZONE_COOL_LOOP,  # 0.106 (#186): SAT tier-1 requests
            }
        return {
            "flow_col": Role.AIRFLOW,
            "flow_sp_col": Role.AIRFLOW_SP,
            "damper_col": Role.DAMPER,
        }

    def _overloaded(self, frames: dict, zones) -> dict:
        """``{zone: share}`` of each static rogue's static-request cycles (damper > 95 %, system
        on) on which it sat >= 3 °F above its cooling setpoint; zones without the temperatures
        or with too few such cycles are left out."""
        out: dict = {}
        for z in zones:
            f = frames.get(z)
            if f is None or any(
                r not in f.columns for r in (Role.SPACE_TEMP, Role.COOL_SP, Role.DAMPER)
            ):
                continue
            on = pd.to_numeric(f[Role.DAMPER], errors="coerce") > _FAN_THR
            for gate in _GATES:
                if gate in f.columns:
                    on &= pd.to_numeric(f[gate], errors="coerce") > 0.5
            over = (f[Role.SPACE_TEMP] - f[Role.COOL_SP])[on].dropna()
            if len(over) >= self._kwargs["min_active_cycles"]:
                out[z] = round(float((over >= _OVERLOAD_F).mean()), 3)
        return out

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

        groups, grouping_caveats, provenance, n_ungrouped = resolve_grouping(
            self.groups, frames.keys(), topology
        )
        res = rogue_zone_census(
            frames,
            reset=self.reset,
            groups=groups,
            **self._cols(),
            **self._kwargs,
            gate_cols=_GATES,
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
        rogues, rogue_by_group, worst = res.rogues, res.rogue_by_group, res.worst_zone
        overloaded: dict = {}
        if self.reset == "static" and res.rogues:
            shares = self._overloaded(frames, res.rogues)
            overloaded = {z: v for z, v in shares.items() if v >= _OVERLOAD_SHARE}
            if overloaded:
                rogues = [z for z in res.rogues if z not in overloaded]
                rogue_by_group = {
                    g: kept
                    for g, zs in res.rogue_by_group.items()
                    if (kept := [z for z in zs if z not in overloaded])
                }
                worst = (
                    max(
                        rogues,
                        key=lambda z: (res.zone_binding_frac[z], res.zone_request_share[z]),
                    )
                    if rogues
                    else None
                )

        metrics = {
            "reset": res.reset,
            "grouped": res.grouped,
            "grouping_provenance": provenance,
            "n_zones_ungrouped": n_ungrouped,
            "n_zones_evaluated": res.n_zones_evaluated,
            "n_groups": res.n_groups,
            "total_requests": res.total_requests,
            "rogues": rogues,
            "rogue_by_group": rogue_by_group,
            "worst_zone": worst,
            "worst_zone_share": res.zone_request_share.get(worst) if worst else None,
            "zone_request_share": res.zone_request_share,
            "zone_binding_frac": res.zone_binding_frac,
            "unevaluable_zones": res.unevaluable_zones,
        }
        if self.reset == "static":
            metrics["overloaded_zones"] = sorted(overloaded)
            metrics["overloaded_share"] = overloaded

        if rogues or overloaded:
            if rogues:
                binding = res.zone_binding_frac.get(worst, 0.0)
                share = res.zone_request_share.get(worst, 0.0)
                summary = (
                    f"fleet: zone {worst} monopolizes the {self._label} reset -- holds the "
                    f"binding request {binding:.0%} of active cycles and {share:.0%} of total "
                    f"requests ({len(rogues)} rogue zone(s): {', '.join(rogues)})"
                )
            else:
                summary = f"fleet: no starved inlet drags the {self._label} reset"
            if overloaded:
                summary += (
                    f"; {', '.join(sorted(overloaded))} also drag(s) it because short of cooling "
                    f"(>= {_OVERLOAD_F:g} °F above the cooling setpoint on most of its static "
                    "requests): an overloaded zone, the SAT census's rogue -- fix its load first"
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
