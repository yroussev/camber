"""Rule: CO2-based ventilation adequacy (ASHRAE 62.1 / ventilation-rate guidance).

Flags a zone whose CO2 sits persistently above the elevated threshold during occupancy
(**under-ventilation** -- an IAQ concern) or barely rises above outdoor (**over-
ventilation** -- a conditioning-energy penalty in a hot climate). Adapts
:func:`camber.iaq.analyze_co2_ventilation` to the role-frame interface. The air-quality
companion to the Std-55 thermal-comfort analytic.

0.93 (#38): an economizer brings in outdoor air beyond the ventilation minimum whenever outdoor
air can cool, and in a mild climate that is most occupied hours -- CO2 then sits near outdoor
because the unit is cooling for free, not because it over-ventilates. Economizer-mode hours
(:func:`camber.iaq.economizer_mode_mask`: a trended economizer command, or the OAT inside the
economizer window with the OA damper above its minimum) are left out of the over-ventilation
verdict and reported apart. :class:`CO2Ventilation` reads that from its own frame (a single-zone
unit, an air handler with return CO2); zones whose CO2 is on a terminal while the damper is on the
air handler are joined to it by the fleet rule :class:`CO2VentilationSystem`.
"""

from __future__ import annotations

import pandas as pd

from ..iaq import DEFAULT_ECON_HIGH_LIMIT_F, analyze_co2_ventilation, economizer_mode_mask
from ..model.roles import Role
from ._topology_grouping import HEURISTIC_CAVEAT
from .base import Finding

_ROLE_TO_COL = {Role.CO2: "CO2", Role.OUTDOOR_CO2: "OutdoorCO2"}

#: 0.93 (#38): the economizer evidence either CO2 rule reads
_ECON_ROLES = (
    Role.ECON_CMD,
    Role.OAT,
    Role.OA_DAMPER,
    Role.MIXED_AIR_TEMP,
    Role.RETURN_AIR_TEMP,
    Role.SUPPLY_FAN_STATUS,
)

_SEV_RANK = {"ok": 0, "info": 1, "warn": 2, "fault": 3}


def _econ_mask(frame: pd.DataFrame, *, damper_min_pct=None, high_limit_f=DEFAULT_ECON_HIGH_LIMIT_F):
    """:func:`economizer_mode_mask` over one frame's own economizer evidence."""
    fan = None
    if Role.SUPPLY_FAN_STATUS in frame.columns:
        fan = frame[Role.SUPPLY_FAN_STATUS].fillna(0.0) > 0.5
    return economizer_mode_mask(
        frame.index,
        econ_cmd=frame.get(Role.ECON_CMD),
        oat=frame.get(Role.OAT),
        damper=frame.get(Role.OA_DAMPER),
        mat=frame.get(Role.MIXED_AIR_TEMP),
        rat=frame.get(Role.RETURN_AIR_TEMP),
        fan_on=fan,
        damper_min_pct=damper_min_pct,
        high_limit_f=high_limit_f,
    )


class CO2Ventilation:
    """Detects under- or over-ventilation from zone CO2 (ASHRAE 62.1 ventilation-rate proxy).

    Economizer-mode hours are left out of the over-ventilation verdict (0.93, #38) when the frame
    carries an economizer command, or an OAT with an OA damper or mixed/return temperatures;
    ``exclude_economizer=False`` turns that off.
    """

    name = "co2_ventilation"
    roles_required = (Role.CO2,)
    roles_optional = (Role.OUTDOOR_CO2,) + _ECON_ROLES

    def __init__(
        self,
        *,
        exclude_economizer: bool = True,
        oa_damper_min_pct: float | None = None,
        econ_high_limit_f: float = DEFAULT_ECON_HIGH_LIMIT_F,
    ):
        self.exclude_economizer = bool(exclude_economizer)
        self.oa_damper_min_pct = oa_damper_min_pct
        self.econ_high_limit_f = econ_high_limit_f

    def _mask_for(self, frame: pd.DataFrame):
        if not self.exclude_economizer:
            return None, "disabled"
        return _econ_mask(
            frame, damper_min_pct=self.oa_damper_min_pct, high_limit_f=self.econ_high_limit_f
        )

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        if not frame.index.is_unique:
            frame = frame[~frame.index.duplicated(keep="last")]
        econ, basis = self._mask_for(frame)
        return self._judge(equip, frame, econ, basis)

    def _judge(self, equip, frame, econ, basis, *, source=None) -> Finding:
        cols = {r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns}
        legacy = frame[[r for r in _ROLE_TO_COL if r in frame.columns]].rename(columns=cols)
        res = analyze_co2_ventilation(legacy, equip, economizer_mask=econ)
        if res is None:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                summary="insufficient data (need zone CO2 over occupied hours)",
            )
        caveats: list = []
        over = res.over_vent_pct
        # Under-ventilation (high CO2) is the IAQ fault and drives severity; persistent
        # over-ventilation is an energy opportunity surfaced as a warn at most.
        if res.under_vent_pct >= 20.0:
            severity = "fault"
        elif res.under_vent_pct >= 5.0:
            severity = "warn"
        elif over is not None and over >= 60.0:
            severity = "warn"
        else:
            severity = "ok"
        where = f" (economizer from {source})" if source else ""
        if res.under_vent_pct >= 5.0:
            tail = (
                f"under-ventilated {res.under_vent_pct:.0f}% of occupied hours "
                f"(CO2 p95 {res.co2_p95_ppm:.0f} ppm, > {res.high_ppm:.0f} threshold)"
            )
        elif over is not None and over >= 60.0:
            tail = (
                f"over-ventilated: CO2 near outdoor {over:.0f}% of "
                + ("non-economizer " if econ is not None else "")
                + "occupied hours (energy opportunity)"
            )
        elif over is None:
            tail = (
                f"CO2 median {res.co2_median_ppm:.0f} ppm; over-ventilation not judged -- "
                f"economizer mode on {res.econ_hours_pct:.0f}% of occupied hours{where}"
            )
        else:
            tail = f"CO2 median {res.co2_median_ppm:.0f} ppm; ventilation adequate"
        if econ is not None:
            caveats.append(
                f"economizer-mode hours ({res.econ_hours_pct:.0f}% of occupied hours{where}, from "
                + (
                    "the economizer command"
                    if basis == "econ_cmd"
                    else "the OAT inside the economizer window with the OA damper above its minimum"
                )
                + ") are left out of the over-ventilation verdict: CO2 near outdoor then is free "
                "cooling"
                + (
                    f"; CO2 near outdoor {res.over_vent_econ_pct:.0f}% of those hours"
                    if res.over_vent_econ_pct is not None
                    else ""
                )
            )
        elif (
            self.exclude_economizer
            and (res.over_vent_pct or 0.0) >= 60.0
            and res.under_vent_pct < 5.0
        ):
            caveats.append(
                "no economizer command or OA damper with OAT on this equipment: if the unit "
                "serving it economizes, part of this outdoor air is free cooling, not "
                "over-ventilation (co2_ventilation_system joins zones to their air handler)"
            )
        metrics: dict = {
            "co2_median_ppm": res.co2_median_ppm,
            "co2_p95_ppm": res.co2_p95_ppm,
            "under_vent_pct": res.under_vent_pct,
            "over_vent_pct": res.over_vent_pct,
            "outdoor_co2_ppm": res.outdoor_co2_ppm,
            "n_occupied": res.n_occupied,
        }
        if econ is not None:
            metrics.update(
                {
                    "economizer_basis": basis,
                    "econ_hours_pct": res.econ_hours_pct,
                    "over_vent_econ_pct": res.over_vent_econ_pct,
                    "over_vent_all_pct": res.over_vent_all_pct,
                }
            )
            if source:
                metrics["economizer_source"] = source
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics=metrics,
            caveats=caveats,
            summary=f"{equip}: {tail}",
        )


# ============================================================================ 0.93 (#38)
class CO2VentilationSystem:
    """Fleet CO2 ventilation: each zone's CO2 judged with its air handler's economizer state.

    Zone CO2 lives on the zones and the OA damper on the air handler, so a zone frame cannot tell
    economizer outdoor air from over-ventilation. This groups each CO2 zone to its serving air
    handler through the served-by topology (a semantic one, else the naming heuristic
    :meth:`Registry.run_fleet` builds), takes that unit's economizer-mode hours
    (:func:`camber.iaq.economizer_mode_mask`) and runs :class:`CO2Ventilation`'s judgement on the
    zone with them excluded. A zone that carries its own economizer evidence uses it; a zone no
    unit can be attributed to is judged without exclusion, with a caveat. With no usable grouping
    and exactly one economizing unit, every zone joins it, with a caveat. One finding for the
    fleet: the worst zone's severity, every zone in ``metrics["per_zone"]``.
    """

    name = "co2_ventilation_system"
    roles_required: tuple = ()
    roles_optional = (Role.CO2, Role.OUTDOOR_CO2) + _ECON_ROLES
    wants_topology = True

    def __init__(self, **zone_kwargs):
        self._zone_rule = CO2Ventilation(**zone_kwargs)
        self._zone_rule.name = self.name

    def analyze_fleet(self, frames: dict, *, topology=None) -> Finding:
        zr = self._zone_rule
        zones = {e: fr for e, fr in frames.items() if Role.CO2 in fr.columns}
        masks = {}
        for e, fr in frames.items():
            if not fr.index.is_unique:
                fr = fr[~fr.index.duplicated(keep="last")]
            m, basis = zr._mask_for(fr)
            if m is not None:
                masks[e] = (m, basis)
        sources = [e for e in masks if e not in zones]
        base = {"n_zones_with_co2": len(zones), "n_econ_sources": len(sources)}
        if not zones:
            return Finding(
                rule=self.name,
                equip="<fleet>",
                severity="info",
                metrics={**base, "declined": True},
                summary="CO2 ventilation not evaluated: no zone CO2",
            )
        caveats: list = []
        provenance = None
        need = [z for z in zones if z not in masks]
        assign: dict = {}
        if need and sources and topology is not None:
            provenance = topology.provenance
            assign = topology.group_map(need, pred=lambda e: e in masks and e not in zones)
            if not assign:
                caveats.append(
                    f"{provenance} served-by topology covered none of the {len(need)} CO2 zone(s)"
                )
                provenance = None
        if need and not assign and len(sources) == 1:
            only = sources[0]
            assign = {z: only for z in need}
            provenance = "single_source"
            caveats.append(
                f"no served-by grouping resolved; all {len(need)} CO2 zone(s) assumed served by "
                f"the only economizing unit, {only}"
            )
        elif provenance == "heuristic" and assign:
            caveats.append(HEURISTIC_CAVEAT)
        unattributed = sorted(z for z in need if z not in assign) if sources else []
        if unattributed:
            caveats.append(
                f"{len(unattributed)} CO2 zone(s) could not be attributed to an air handler; their "
                "over-ventilation is judged without excluding economizer hours"
            )

        per_zone: dict = {}
        worst, worst_zone = "ok", None
        for z, fr in sorted(zones.items()):
            if not fr.index.is_unique:
                fr = fr[~fr.index.duplicated(keep="last")]
            src = None
            if z in masks:
                m, basis = masks[z]
            elif z in assign:
                src = assign[z]
                m, basis = masks[src]
                m = m[~m.index.duplicated(keep="last")].reindex(fr.index)
            else:
                m, basis = None, "none"
            f = zr._judge(z, fr, m, basis, source=src)
            per_zone[z] = {"severity": f.severity, "summary": f.summary, **f.metrics}
            if _SEV_RANK[f.severity] > _SEV_RANK[worst] or worst_zone is None:
                worst, worst_zone = f.severity, z
        metrics = {
            **base,
            "grouping_provenance": provenance,
            "n_zones_joined": len(assign),
            "n_zones_unattributed": len(unattributed),
            "per_zone": per_zone,
        }
        flagged = [z for z, v in per_zone.items() if v["severity"] in ("warn", "fault")]
        n_not_judged = sum(1 for v in per_zone.values() if v.get("over_vent_pct", 0) is None)
        if flagged:
            summary = (
                f"CO2 ventilation: {len(flagged)} of {len(per_zone)} zone(s) flagged -- "
                + per_zone[worst_zone]["summary"]
            )
        else:
            summary = f"CO2 ventilation: {len(per_zone)} zone(s) judged; none flagged" + (
                f" ({n_not_judged} with over-ventilation not judged: economizer mode most hours)"
                if n_not_judged
                else ""
            )
        return Finding(
            rule=self.name,
            equip="<fleet>",
            severity=worst,
            metrics=metrics,
            caveats=caveats,
            summary=summary,
        )
