"""Rules: ASHRAE 62.1 ventilation-rate procedure (VRP) and DCV verification.

`DemandControlledVentilation` is config-free -- it checks that outdoor air actually rises with
ventilation demand (CO₂), so it auto-registers and runs on any equipment that carries both an OA
signal and CO₂ (an air handler with a return-air CO₂ sensor, a single-zone unit). Most buildings
put CO₂ on the zones and OA on the air handler, so the fleet rule `DcvSystemVerification` joins
each air handler's OA to the CO₂ of the zones it serves, via the served-by topology.
`VentilationRateProcedure` needs the zone's design inputs (area, population, space type) and so is
instantiated explicitly (not in the default registry). All adapt :mod:`camber.ventilation` to the
role-frame interface.
"""

from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

from ..model.roles import Role
from ..schedules import occupied_mask
from ..ventilation import (
    DEFAULT_ECON_HIGH_LIMIT_F,
    assess_62_1,
    assess_dcv,
    economizer_active_mask,
)
from ._topology_grouping import HEURISTIC_CAVEAT
from .base import Finding

_SEVERITY_RANK = {"ok": 0, "info": 1, "warn": 2, "fault": 3}

#: roles both DCV rules read when present (the gating signals + the OA signal)
_DCV_CONTEXT = (
    Role.OA_AIRFLOW,
    Role.OA_DAMPER,
    Role.OCCUPANCY,
    Role.WARMUP,
    Role.COOLDOWN,
    Role.SUPPLY_FAN_STATUS,
    Role.ECON_CMD,
    Role.OAT,
    Role.HEAT_VALVE,
)

_ECON_CAVEAT = {
    "none": (
        "no economizer command, OAT or heating-valve trend: economizer periods could not be "
        "excluded, so OA may be following outdoor temperature rather than demand"
    ),
    "oat": (
        "economizer state inferred from OAT alone (only hours above the high limit kept), not a "
        "trended economizer command"
    ),
    "oat_heat_valve": (
        "economizer state inferred from OAT and the heating valve, not a trended economizer command"
    ),
}

_MSG = {
    "static": "OA does not modulate with demand (DCV not functioning / fixed OA)",
    "uncorrelated": "OA modulates, but not with demand",
    "functioning": "OA rises with demand (DCV functioning)",
}

_REASON = {
    "too_few_samples": "too few occupied, non-economizing samples",
    "no_demand_variation": "demand never varied enough to test DCV",
    "demand_below_engage": "CO₂ never reached the level where DCV should respond",
    "oa_rarely_raised": "OA was raised above its floor too rarely to compare",
    "schedule_confounded": "OA follows the time of day, so occupancy response can't be separated",
}


def _oa_role(frame: pd.DataFrame):
    if Role.OA_AIRFLOW in frame.columns:
        return Role.OA_AIRFLOW
    if Role.OA_DAMPER in frame.columns:
        return Role.OA_DAMPER
    return None


class DemandControlledVentilation:
    """Verifies DCV: outdoor air should rise when CO₂ (ventilation demand) rises.

    Judged only on occupied samples (the schedule, AND-ed with a trended ``OCCUPANCY`` point,
    minus ``WARMUP``/``COOLDOWN``) with the supply fan running and the economizer inactive -- from
    ``ECON_CMD``, else inferred from ``OAT`` + ``HEAT_VALVE``. Flags a **static** OA signal (fixed
    OA / DCV not functioning) or one that modulates but not with demand; ``fault`` when CO₂ breaches
    ``co2_setpoint`` while OA sits at its minimum, or OA falls below ``oa_floor_cfm``. Uses OA
    airflow if present, else OA-damper position. Frames without an OA signal (a VAV zone's CO₂)
    return ``None``: :class:`DcvSystemVerification` joins those to their air handler.
    """

    name = "dcv_verification"
    roles_required = (Role.CO2,)
    roles_optional = _DCV_CONTEXT

    def __init__(
        self,
        *,
        min_corr: float | None = None,
        min_modulation: float = 0.1,
        co2_setpoint: float | None = None,
        occupied_only: bool = True,
        dcv_engage_ppm: float | None = None,
        min_demand_span: float = 150.0,
        min_lift_ppm: float = 50.0,
        econ_high_limit_f: float = DEFAULT_ECON_HIGH_LIMIT_F,
        oa_floor_cfm: float | Mapping | None = None,
        breach_fault_pct: float = 10.0,
        below_floor_fault_pct: float = 10.0,
        excess_warn_pct: float = 50.0,
        min_samples: int = 24,
        start_hour: float = 7,
        end_hour: float = 18,
        occupied_days=(0, 1, 2, 3, 4),
        unventilated_co2_ppm: float | None = None,
        unventilated_fault_hours: float = 4.0,
    ):
        self.min_corr = min_corr
        self.min_modulation = min_modulation
        self.co2_setpoint = co2_setpoint
        self.occupied_only = occupied_only
        self.dcv_engage_ppm = dcv_engage_ppm
        self.min_demand_span = min_demand_span
        self.min_lift_ppm = min_lift_ppm
        self.econ_high_limit_f = econ_high_limit_f
        self.oa_floor_cfm = oa_floor_cfm
        self.breach_fault_pct = breach_fault_pct
        self.below_floor_fault_pct = below_floor_fault_pct
        self.excess_warn_pct = excess_warn_pct
        self.min_samples = min_samples
        self.start_hour = start_hour
        self.end_hour = end_hour
        self.occupied_days = tuple(occupied_days)
        self.unventilated_co2_ppm = unventilated_co2_ppm
        self.unventilated_fault_hours = unventilated_fault_hours

    def _occupied(self, frame: pd.DataFrame) -> pd.Series:
        """Occupied samples: a trended ``OCCUPANCY`` point is the truth when present (it
        *replaces* the schedule, so a 24/7 space isn't cut to the default weekday window);
        otherwise the configured schedule. ``WARMUP`` / ``COOLDOWN`` are excluded either way."""
        idx = frame.index
        if Role.OCCUPANCY in frame.columns:
            return occupied_mask(
                idx,
                start_hour=0,
                end_hour=24,
                days=range(7),
                occ=frame[Role.OCCUPANCY],
                warmup=frame.get(Role.WARMUP),
                cooldown=frame.get(Role.COOLDOWN),
            )
        return occupied_mask(
            idx,
            start_hour=self.start_hour,
            end_hour=self.end_hour,
            days=self.occupied_days,
            warmup=frame.get(Role.WARMUP),
            cooldown=frame.get(Role.COOLDOWN),
        )

    def _floor_for(self, equip: str):
        if isinstance(self.oa_floor_cfm, Mapping):
            return self.oa_floor_cfm.get(equip)
        return self.oa_floor_cfm

    def _judge(self, equip: str, frame: pd.DataFrame, demand: pd.Series) -> Finding:
        """Evaluate one OA source against a demand series; shared by both DCV rules."""
        if not frame.index.is_unique:
            frame = frame[~frame.index.duplicated(keep="last")]
            demand = demand[~demand.index.duplicated(keep="last")]
        oa_role = _oa_role(frame)
        caveats: list = []
        idx = frame.index
        demand = demand[~demand.index.duplicated(keep="last")].reindex(idx)
        occupied = self._occupied(frame) if self.occupied_only else pd.Series(True, index=idx)
        mask = occupied
        fan_on = None
        if Role.SUPPLY_FAN_STATUS in frame.columns:
            # an hourly mean below 1 is a partial-hour fan transition -- its OA mean is diluted
            fan_on = frame[Role.SUPPLY_FAN_STATUS].reindex(idx).fillna(0) > 0.95
            mask = mask & fan_on
        econ, econ_basis = economizer_active_mask(
            idx,
            econ_cmd=frame.get(Role.ECON_CMD),
            oat=frame.get(Role.OAT),
            heat_valve=frame.get(Role.HEAT_VALVE),
            high_limit_f=self.econ_high_limit_f,
        )
        if econ_basis in _ECON_CAVEAT:
            caveats.append(_ECON_CAVEAT[econ_basis])

        floor = self._floor_for(equip)
        if floor is not None and oa_role is not Role.OA_AIRFLOW:
            caveats.append(
                "OA floor not checked: it is in cfm and only OA-damper position is trended"
            )
            floor = None
        if oa_role is Role.OA_DAMPER:
            caveats.append("OA judged from damper position, not measured OA flow")

        res = assess_dcv(
            frame[oa_role],
            demand,
            occupied_mask=mask,
            min_corr=self.min_corr,
            min_modulation=self.min_modulation,
            co2_setpoint=self.co2_setpoint,
            equip=equip,
            economizer_mask=econ,
            dcv_engage_ppm=self.dcv_engage_ppm,
            min_demand_span=self.min_demand_span,
            min_lift_ppm=self.min_lift_ppm,
            oa_floor=floor,
            min_samples=self.min_samples,
        )

        # Occupied, CO₂ high, and nothing ventilating: the fan off or the OA damper shut. The
        # verdict above excludes those samples (it judges modulation, not outages), so without this
        # check the worst failure -- a lecture theatre at the CO₂ sensor's full scale with the fan
        # off -- reads "not judged".
        limit = self.unventilated_co2_ppm or self.co2_setpoint or 1100.0
        oa_vals = frame[oa_role].reindex(idx)
        ref = float(oa_vals[occupied].quantile(0.95)) if occupied.any() else float("nan")
        off = oa_vals <= 0.02 * ref if ref == ref and ref > 0 else oa_vals <= 0
        if fan_on is not None:
            off = off | ~fan_on
        judged = occupied & demand.notna()
        unvent = unvent_h = None
        if judged.sum() >= self.min_samples:
            hit = (off & (demand >= limit))[judged]
            unvent = round(100.0 * float(hit.mean()), 1)
            step_h = float(pd.Series(idx).diff().median() / pd.Timedelta(hours=1))
            unvent_h = round(float(hit.sum()) * step_h, 1) if step_h == step_h else None

        not_evaluated = []
        if res.co2_breach_at_min_pct is None:
            not_evaluated.append("CO₂-above-setpoint at minimum OA (no co2_setpoint)")
        if res.below_floor_pct is None:
            not_evaluated.append("OA below the Ra·Az floor (no oa_floor_cfm)")
        if not_evaluated:
            caveats.append("not evaluated: " + "; ".join(not_evaluated))
        if res.closed_pct is not None and res.closed_pct >= 5.0:
            caveats.append(
                f"OA was closed on {res.closed_pct:.0f}% of occupied samples (warm-up, fan "
                "transitions or a damper fault); those samples were excluded"
            )

        if res.status == "insufficient":
            severity, msg = "info", f"DCV not judged -- {_REASON.get(res.reason or '', res.reason)}"
        else:
            msg = _MSG[res.status]
            if res.status == "functioning":
                severity = "ok"
            elif res.status == "uncorrelated" and not res.econ_excluded:
                severity = "info"  # the economizer may be what moves OA -- not a DCV verdict
            else:
                severity = "warn"
        if (res.excess_at_low_demand_pct or 0.0) >= self.excess_warn_pct and severity == "ok":
            severity = "warn"
            msg += f"; OA above the floor at low demand {res.excess_at_low_demand_pct:.0f}%"
        if (res.co2_breach_at_min_pct or 0.0) >= self.breach_fault_pct:
            severity = "fault"
            msg += (
                f"; CO₂ above {self.co2_setpoint:.0f} ppm with OA at minimum "
                f"{res.co2_breach_at_min_pct:.0f}% of samples"
            )
        if (res.below_floor_pct or 0.0) >= self.below_floor_fault_pct:
            severity = "fault"
            msg += f"; OA below the floor {res.below_floor_pct:.0f}% of samples"
        # an outage is judged by its duration, not its share: 110 hours at the CO₂ sensor's full
        # scale is a fault whether the dataset holds one month or fourteen
        if (unvent_h or 0.0) >= self.unventilated_fault_hours:
            severity = "fault"
            msg += (
                f"; occupied with CO₂ ≥ {limit:.0f} ppm and no ventilation (fan off or OA shut) "
                f"for {unvent_h:.0f} h ({unvent:.1f}% of occupied samples)"
            )
        if (
            econ_basis in ("oat", "oat_heat_valve")
            and res.n_econ_excluded is not None
            and res.n_econ_excluded >= 0.95 * max(1, res.n_econ_excluded + res.n)
        ):
            caveats.append(
                f"{res.n_econ_excluded} of the samples were excluded as possibly economizing "
                f"(OAT below the {self.econ_high_limit_f:.0f} °F high limit). CAMBER temperatures "
                "are °F -- an OAT trended in °C reads as always cold; in a cool climate, map "
                "ECON_CMD so the economizer need not be inferred"
            )

        metrics = {
            "status": res.status,
            "reason": res.reason,
            "demand_lift": res.demand_lift,
            "modulation": res.modulation,
            "correlation": res.correlation,
            "demand_span": res.demand_span,
            "co2_breach_at_min_pct": res.co2_breach_at_min_pct,
            "below_floor_pct": res.below_floor_pct,
            "excess_at_low_demand_pct": res.excess_at_low_demand_pct,
            "closed_pct": res.closed_pct,
            "economizer_basis": econ_basis,
            "n_econ_excluded": res.n_econ_excluded,
            "oa_signal": oa_role.value if oa_role is not None else None,
            "demand_kind": res.demand_kind,
            "unventilated_high_co2_pct": unvent,
            "unventilated_high_co2_hours": unvent_h,
            "n": res.n,
        }
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics=metrics,
            caveats=caveats,
            summary=f"{equip}: {msg}",
        )

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding | None:
        if _oa_role(frame) is None:
            return None  # a zone's CO₂ -- judged against its air handler by DcvSystemVerification
        f = self._judge(equip, frame, frame[Role.CO2])
        f.caveats.insert(
            0,
            "CO₂ on an OA-carrying unit is typically return-air CO₂, which averages the zones and "
            "dilutes the critical one",
        )
        return f


class DcvSystemVerification:
    """Fleet DCV check: each air handler's OA judged against the CO₂ of the zones it serves.

    Zone CO₂ lives on the VAV zones and OA on the air handler, so no single equipment frame
    carries both. This groups zones to their serving air handler through the served-by
    topology (a semantic one, else the naming heuristic :meth:`Registry.run_fleet` builds -- which
    caps severity at ``warn``), drops zones whose CO₂ is implausible, stuck, or stays well above
    outdoor when unoccupied, takes the per-timestamp **maximum** (the critical zone should drive
    the reset; ``agg="mean"`` is available), and runs the same judgement as
    :class:`DemandControlledVentilation`. With no usable grouping and exactly one OA source, all
    zones join it, with a caveat. Air handlers carrying their own CO₂ are left to the
    single-equipment rule.
    """

    name = "dcv_system_verification"
    roles_required: tuple = ()
    roles_optional = (Role.CO2, Role.OUTDOOR_CO2) + _DCV_CONTEXT
    wants_topology = True

    def __init__(
        self,
        *,
        agg: str = "max",
        stuck_std_ppm: float = 5.0,
        unoccupied_excess_ppm: float = 300.0,
        assumed_outdoor_ppm: float = 420.0,
        **judge_kwargs,
    ):
        if agg not in ("max", "mean"):
            raise ValueError(f"agg must be 'max' or 'mean', got {agg!r}")
        self.agg = agg
        self.stuck_std_ppm = stuck_std_ppm
        self.unoccupied_excess_ppm = unoccupied_excess_ppm
        self.assumed_outdoor_ppm = assumed_outdoor_ppm
        self._judge_rule = DemandControlledVentilation(**judge_kwargs)
        self._judge_rule.name = self.name

    def _usable_zone_co2(self, frame: pd.DataFrame) -> tuple[pd.Series | None, str | None]:
        if not frame.index.is_unique:
            frame = frame[~frame.index.duplicated(keep="last")]
        co2 = frame[Role.CO2].where(frame[Role.CO2].between(250.0, 5000.0))
        if co2.count() < 24:
            return None, "too few plausible CO₂ samples"
        if float(co2.std()) < self.stuck_std_ppm:
            return None, "CO₂ stuck (flat)"
        outdoor = self.assumed_outdoor_ppm
        if Role.OUTDOOR_CO2 in frame.columns and frame[Role.OUTDOOR_CO2].notna().any():
            outdoor = float(frame[Role.OUTDOOR_CO2].median())
        unocc = co2[~self._judge_rule._occupied(frame).reindex(co2.index, fill_value=False)]
        unocc = unocc.dropna()
        if len(unocc) >= 24 and float(unocc.median()) > outdoor + self.unoccupied_excess_ppm:
            return None, "CO₂ stays high when unoccupied (offset / miscalibrated)"
        return co2, None

    def analyze_fleet(self, frames: dict, *, topology=None) -> Finding:
        sources = {
            e: fr for e, fr in frames.items() if _oa_role(fr) is not None and Role.CO2 not in fr
        }
        zones = {
            e: fr for e, fr in frames.items() if Role.CO2 in fr.columns and _oa_role(fr) is None
        }
        base = {"n_oa_sources": len(sources), "n_zones_with_co2": len(zones)}
        if not zones or not sources:
            return Finding(
                rule=self.name,
                equip="<fleet>",
                severity="info",
                metrics={**base, "declined": True},
                summary="system DCV not evaluated: needs zone CO₂ and an air-handler OA signal",
            )

        caveats: list = []
        provenance = None
        assign: dict = {}
        if topology is not None:
            provenance = topology.provenance
            # nearest *OA-source* ancestor: a Brick model chains AHU -> VAV -> zone, so a zone's
            # direct parent is usually a terminal box, not the unit that brings in outdoor air
            assign = topology.group_map(list(zones), pred=lambda e: e in sources)
        if not assign and len(sources) == 1:
            only = next(iter(sources))
            assign = {z: only for z in zones}
            provenance = "single_source"
            caveats.append(
                f"no served-by grouping resolved; all {len(zones)} CO₂ zone(s) assumed served by "
                f"the only OA source, {only}"
            )
        elif provenance == "heuristic" and assign:
            caveats.append(HEURISTIC_CAVEAT)
        unattributed = sorted(z for z in zones if z not in assign)
        if unattributed:
            caveats.append(
                f"{len(unattributed)} CO₂ zone(s) could not be attributed to an air handler and "
                "were not used"
            )

        excluded: dict = {}
        by_ahu: dict = {}
        for z, ahu in assign.items():
            co2, why = self._usable_zone_co2(zones[z])
            if co2 is None:
                excluded[z] = why
            else:
                by_ahu.setdefault(ahu, []).append(co2)
        if excluded:
            caveats.append(
                f"{len(excluded)} zone CO₂ sensor(s) excluded as untrustworthy: "
                + "; ".join(f"{z} ({w})" for z, w in sorted(excluded.items()))
            )

        per_ahu: dict = {}
        worst = "ok"
        worst_ahu = None
        for ahu, series in sorted(by_ahu.items()):
            stacked = pd.concat(series, axis=1)
            demand = stacked.max(axis=1) if self.agg == "max" else stacked.mean(axis=1)
            f = self._judge_rule._judge(ahu, sources[ahu], demand.reindex(sources[ahu].index))
            sev = f.severity
            if provenance == "heuristic" and sev == "fault":
                sev = "warn"
            per_ahu[ahu] = {
                "severity": sev,
                "summary": f.summary,
                "n_zones": len(series),
                **f.metrics,
            }
            caveats.extend(f"{ahu}: {c}" for c in f.caveats)
            if _SEVERITY_RANK[sev] > _SEVERITY_RANK[worst] or worst_ahu is None:
                worst, worst_ahu = sev, ahu

        metrics = {
            **base,
            "grouping_provenance": provenance,
            "agg": self.agg,
            "n_zones_joined": sum(len(v) for v in by_ahu.values()),
            "n_zones_unattributed": len(unattributed),
            "n_zones_excluded": len(excluded),
            "per_ahu": per_ahu,
        }
        if not per_ahu:
            return Finding(
                rule=self.name,
                equip="<fleet>",
                severity="info",
                metrics={**metrics, "declined": True},
                caveats=caveats,
                summary="system DCV not evaluated: no usable zone CO₂ joined to an air handler",
            )
        flagged = [a for a, m in per_ahu.items() if m["severity"] in ("warn", "fault")]
        if flagged:
            summary = (
                f"system DCV: {len(flagged)} of {len(per_ahu)} air handler(s) flagged -- "
                + (per_ahu[worst_ahu]["summary"])
            )
        else:
            summary = (
                f"system DCV: {len(per_ahu)} air handler(s) judged against "
                f"{metrics['n_zones_joined']} zone CO₂ sensor(s); none flagged"
            )
        return Finding(
            rule=self.name,
            equip="<fleet>",
            severity=worst,
            metrics=metrics,
            caveats=caveats,
            summary=summary,
        )


class VentilationRateProcedure:
    """Checks measured outdoor air against the ASHRAE 62.1 VRP requirement for one zone.

    Needs the zone's design inputs, so it is constructed explicitly (not auto-registered):
    ``VentilationRateProcedure(area_sqft=2500, population=15, space_type="office")``.
    """

    name = "ventilation_rate_62_1"
    roles_required = (Role.OA_AIRFLOW,)
    roles_optional = ()

    def __init__(
        self,
        *,
        area_sqft: float,
        population: float,
        space_type: str | None = None,
        rp: float | None = None,
        ra: float | None = None,
        ez: float = 1.0,
        occupied_only: bool = True,
        aggregate: str = "median",
        under_tol: float = 0.9,
        over_factor: float = 1.5,
    ):
        self.area_sqft = area_sqft
        self.population = population
        self.space_type = space_type
        self.rp, self.ra, self.ez = rp, ra, ez
        self.occupied_only = occupied_only
        self.aggregate = aggregate
        self.under_tol, self.over_factor = under_tol, over_factor

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        mask = occupied_mask(frame.index) if self.occupied_only else None
        res = assess_62_1(
            frame[Role.OA_AIRFLOW],
            area_sqft=self.area_sqft,
            population=self.population,
            space_type=self.space_type,
            rp=self.rp,
            ra=self.ra,
            ez=self.ez,
            occupied_mask=mask,
            aggregate=self.aggregate,
            under_tol=self.under_tol,
            over_factor=self.over_factor,
            equip=equip,
        )
        if res.status == "unknown":
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                summary=f"{equip}: insufficient OA-flow data",
            )
        severity = {"under": "fault", "over": "warn", "adequate": "ok"}[res.status]
        if res.status == "under":
            tail = (
                f"under-ventilated: {res.measured_cfm:.0f} cfm OA vs {res.required_cfm:.0f} "
                f"required (62.1 VRP), deficit {res.deficit_cfm:.0f} cfm"
            )
        elif res.status == "over":
            tail = (
                f"over-ventilated: {res.measured_cfm:.0f} cfm vs {res.required_cfm:.0f} "
                f"required ({res.ratio:.1f}× — conditioning-energy penalty)"
            )
        else:
            tail = f"OA adequate: {res.measured_cfm:.0f} cfm vs {res.required_cfm:.0f} required"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "required_cfm": res.required_cfm,
                "measured_cfm": res.measured_cfm,
                "ratio": res.ratio,
                "deficit_cfm": res.deficit_cfm,
                "status": res.status,
                "rp": res.rp,
                "ra": res.ra,
                "ez": res.ez,
            },
            summary=f"{equip}: {tail}",
        )
