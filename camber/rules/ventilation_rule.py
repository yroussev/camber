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
    DEFAULT_EZ_COOLING,
    DEFAULT_EZ_HEATING,
    VentZone,
    assess_62_1,
    assess_dcv,
    assess_system_62_1,
    economizer_active_mask,
    estimate_oa_cfm,
    system_outdoor_air,
    zones_from_records,
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
    # 0.93 (#37): the ventilation proxies of a declared 100 % outdoor-air unit
    Role.AIRFLOW,
    Role.SUPPLY_FAN_SPEED,
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


# ============================================================================ 0.93 (#37)
#: On a 100 % outdoor-air unit (no return air, no economizer damper) every cfm the fan moves is
#: outdoor air, so the supply airflow -- or, failing that, the supply fan speed -- is the
#: ventilation signal. Used only when the rule is told the unit is 100 % OA.
_FULL_OA_PROXIES = (Role.AIRFLOW, Role.SUPPLY_FAN_SPEED)

_FULL_OA_CAVEAT = {
    Role.AIRFLOW: (
        "100% outdoor-air unit: OA judged from the supply airflow (all of it is outdoor air); "
        "no economizer applies, but the fan may also speed up for cooling"
    ),
    Role.SUPPLY_FAN_SPEED: (
        "100% outdoor-air unit: OA judged from the supply fan speed, a proxy for its airflow "
        "(all of it is outdoor air) -- not a measured flow; no economizer applies, but the fan "
        "may also speed up for cooling"
    ),
}

_SIGNAL_LABEL = {
    Role.OA_AIRFLOW: "OA flow",
    Role.OA_DAMPER: "OA damper",
    Role.AIRFLOW: "supply airflow",
    Role.SUPPLY_FAN_SPEED: "supply fan speed",
}


def _oa_candidates(full_outdoor_air: bool = False) -> tuple:
    """Ventilation signals, best first: OA flow, the OA damper, then (on a declared 100 % OA
    unit only) the supply airflow and the supply fan speed."""
    return (Role.OA_AIRFLOW, Role.OA_DAMPER) + (_FULL_OA_PROXIES if full_outdoor_air else ())


def _oa_role(frame: pd.DataFrame, full_outdoor_air: bool = False):
    for role in _oa_candidates(full_outdoor_air):
        if role in frame.columns:
            return role
    return None


class DemandControlledVentilation:
    """Verifies DCV: outdoor air should rise when CO₂ (ventilation demand) rises.

    Judged only on occupied samples (the schedule, AND-ed with a trended ``OCCUPANCY`` point,
    minus ``WARMUP``/``COOLDOWN``) with the supply fan running and the economizer inactive -- from
    ``ECON_CMD``, else inferred from ``OAT`` + ``HEAT_VALVE``. Flags a **static** OA signal (fixed
    OA / DCV not functioning) or one that modulates but not with demand; ``fault`` when CO₂ breaches
    ``co2_setpoint`` while OA sits at its minimum, or OA falls below ``oa_floor_cfm``. Uses OA
    airflow where it is trended and the OA-damper position where it is not (0.93, #37: each
    signal judges its own samples, listed in ``metrics["oa_segments"]``); with
    ``full_outdoor_air=True`` (a 100 % outdoor-air unit) the supply airflow and then the supply
    fan speed follow as proxies. The CO₂ lift is taken within the hour of day
    (``stratify_hour``) when enough same-hour samples exist. Frames without an OA signal (a VAV
    zone's CO₂) return ``None``: :class:`DcvSystemVerification` joins those to their air handler.
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
        full_outdoor_air: bool = False,
        stratify_hour: bool = True,
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
        # 0.93 (#37)
        self.full_outdoor_air = bool(full_outdoor_air)
        self.stratify_hour = bool(stratify_hour)

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

    # ------------------------------------------------------------------ 0.93 (#37) OA proxies
    def _oa_segments(self, frame: pd.DataFrame) -> list:
        """``[(role, mask), ...]`` -- which ventilation signal judges which samples.

        The best signal judges every sample it covers; a lesser one only the samples the better
        ones miss. So an air handler whose OA flow is masked for a period (a failed or imputed
        flow station) is judged there on its OA damper, and on its flow everywhere else.
        """
        segs: list = []
        covered = pd.Series(False, index=frame.index)
        for role in _oa_candidates(self.full_outdoor_air):
            if role not in frame.columns:
                continue
            ok = frame[role].notna() & ~covered
            if not ok.any():
                continue
            segs.append((role, ok))
            covered = covered | ok
        if not segs:  # every candidate column is empty: judge the best one (too few samples)
            role = _oa_role(frame, self.full_outdoor_air)
            if role is not None:
                segs.append((role, pd.Series(True, index=frame.index)))
        return segs

    def _judge(self, equip: str, frame: pd.DataFrame, demand: pd.Series) -> Finding:
        """Evaluate one OA source against a demand series; shared by both DCV rules.

        0.93 (#37): judged per OA signal segment (:meth:`_oa_segments`). The first segment with a
        verdict leads the finding (its metrics are the finding's); every judged segment is listed
        in ``metrics["oa_segments"]`` and the worst severity wins.
        """
        if not frame.index.is_unique:
            frame = frame[~frame.index.duplicated(keep="last")]
            demand = demand[~demand.index.duplicated(keep="last")]
        demand = demand[~demand.index.duplicated(keep="last")].reindex(frame.index)
        segs = self._oa_segments(frame)
        occupied = (
            self._occupied(frame) if self.occupied_only else pd.Series(True, index=frame.index)
        )
        parts = []
        for i, (role, seg) in enumerate(segs):
            # a fallback segment with too few occupied samples to judge is not worth a line
            if i > 0 and int((seg & occupied & demand.notna()).sum()) < self.min_samples:
                continue
            parts.append(self._judge_segment(equip, frame, demand, role, seg, fallback=i > 0))
        if len(parts) > 1:
            lead = next((p for p in parts if p["metrics"]["status"] != "insufficient"), parts[0])
        else:
            lead = parts[0]
        severity = max((p["severity"] for p in parts), key=_SEVERITY_RANK.__getitem__)
        metrics = dict(lead["metrics"])
        caveats = list(lead["caveats"])
        msg = lead["msg"]
        if len(parts) > 1:
            metrics["oa_segments"] = [
                {
                    "oa_signal": p["metrics"]["oa_signal"],
                    "severity": p["severity"],
                    "summary": p["msg"],
                    **{
                        k: p["metrics"][k]
                        for k in ("status", "reason", "demand_lift", "n", "start", "end")
                    },
                }
                for p in parts
            ]
            for p in parts:
                if p is lead:
                    continue
                caveats.extend(c for c in p["caveats"] if c not in caveats)
                msg += f"; {p['label']}: {p['msg']}"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics=metrics,
            caveats=caveats,
            summary=f"{equip}: {msg}",
        )

    def _judge_segment(
        self,
        equip: str,
        frame: pd.DataFrame,
        demand: pd.Series,
        oa_role,
        segment: pd.Series,
        *,
        fallback: bool = False,
    ) -> dict:
        """One OA signal's DCV judgement over the samples ``segment`` selects."""
        caveats: list = []
        idx = frame.index
        occupied = self._occupied(frame) if self.occupied_only else pd.Series(True, index=idx)
        occupied = occupied & segment.reindex(idx, fill_value=False)
        mask = occupied
        fan_on = None
        if Role.SUPPLY_FAN_STATUS in frame.columns:
            # an hourly mean below 1 is a partial-hour fan transition -- its OA mean is diluted
            fan_on = frame[Role.SUPPLY_FAN_STATUS].reindex(idx).fillna(0) > 0.95
            mask = mask & fan_on
        full_oa = oa_role in _FULL_OA_PROXIES
        if full_oa:
            # a 100 % outdoor-air unit has no economizer damper: its OA is its airflow
            econ, econ_basis = None, "full_outdoor_air"
            caveats.append(_FULL_OA_CAVEAT[oa_role])
        else:
            econ, econ_basis = economizer_active_mask(
                idx,
                econ_cmd=frame.get(Role.ECON_CMD),
                oat=frame.get(Role.OAT),
                heat_valve=frame.get(Role.HEAT_VALVE),
                high_limit_f=self.econ_high_limit_f,
            )
        if econ_basis in _ECON_CAVEAT:
            caveats.append(_ECON_CAVEAT[econ_basis])

        # the span the segment can judge: occupied samples with demand, else the whole segment
        span = occupied & demand.notna()
        seg_idx = (
            idx[span.to_numpy()]
            if span.any()
            else idx[segment.reindex(idx, fill_value=False).to_numpy()]
        )
        start = str(seg_idx.min().date()) if len(seg_idx) else None
        end = str(seg_idx.max().date()) if len(seg_idx) else None
        label = _SIGNAL_LABEL.get(oa_role, oa_role.value)
        if fallback:
            label = f"{label} where the better OA signal is missing ({start} to {end})"

        floor = self._floor_for(equip)
        if floor is not None and oa_role not in (Role.OA_AIRFLOW, Role.AIRFLOW):
            caveats.append(
                f"OA floor not checked on the {_SIGNAL_LABEL.get(oa_role, oa_role.value)}: it is "
                "in cfm and this signal is not a flow"
            )
            floor = None
        if oa_role is Role.OA_DAMPER:
            caveats.append(
                "OA judged from damper position, not measured OA flow"
                + (f" ({start} to {end}, where OA flow is missing)" if fallback else "")
            )

        res = assess_dcv(
            frame[oa_role].where(segment.reindex(idx, fill_value=False)),
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
            stratify_hour=self.stratify_hour,
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
        if not_evaluated and not fallback:
            caveats.append("not evaluated: " + "; ".join(not_evaluated))
        if res.closed_pct is not None and res.closed_pct >= 5.0:
            caveats.append(
                f"OA was closed on {res.closed_pct:.0f}% of occupied samples (warm-up, fan "
                "transitions or a damper fault); those samples were excluded"
            )
        if res.lift_basis == "pooled" and res.status in ("functioning", "uncorrelated"):
            caveats.append(
                "OA was rarely both raised and at its floor within the same hour of day, so the "
                "CO₂ lift is pooled across hours: a valve that follows a time clock cannot be "
                "told apart from one that follows CO₂"
            )

        if res.status == "insufficient":
            severity, msg = "info", f"DCV not judged -- {_REASON.get(res.reason or '', res.reason)}"
        else:
            msg = _MSG[res.status]
            if res.status == "functioning":
                severity = "ok"
            elif res.status == "uncorrelated" and not res.econ_excluded and not full_oa:
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
            "demand_lift_pooled": res.demand_lift_pooled,
            "lift_basis": res.lift_basis,
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
            "start": start,
            "end": end,
        }
        return {
            "severity": severity,
            "msg": msg,
            "metrics": metrics,
            "caveats": caveats,
            "label": label,
        }

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding | None:
        if _oa_role(frame, self.full_outdoor_air) is None:
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
        full = self._judge_rule.full_outdoor_air
        sources = {
            e: fr
            for e, fr in frames.items()
            if _oa_role(fr, full) is not None and Role.CO2 not in fr
        }
        zones = {
            e: fr
            for e, fr in frames.items()
            if Role.CO2 in fr.columns and _oa_role(fr, full) is None
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
            if not assign:  # covered none of the zones: no grouping happened, so no provenance
                caveats.append(
                    f"{provenance} served-by topology covered none of the {len(zones)} CO₂ zone(s)"
                )
                provenance = None
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


# ============================================================================ 0.92 (#17)


def _norm_id(x) -> str:
    return "".join(ch for ch in str(x).lower() if ch.isalnum())


class VentilationSystemVRP:
    """ASHRAE 62.1 VRP per **system**: each air handler's outdoor air against Vot = Vou / Ev.

    A fleet rule (provisional, 0.92, #17). The zone inputs (floor area, design population, space
    type or Rp/Ra, optional Ez and minimum primary airflow) come from the config's ``ventilation``
    section -- nothing in a trend export carries them -- so with no zones it declines. Each zone
    joins the air handler that serves it: the zone's declared ``system``, else the served-by
    topology (the zone's nearest ancestor that carries an OA signal; a config ``topology`` counts as
    declared, a semantic (Brick) or naming-heuristic one caps severity at ``warn``), else -- with
    exactly one OA source -- that one, also capped at ``warn``. See
    :func:`camber.ventilation.system_outdoor_air` for the requirement and
    :func:`camber.ventilation.assess_system_62_1` for the verdict.

    Measured OA is the air handler's ``OA_AIRFLOW`` over occupied, fan-on samples; without a flow
    station it is estimated from MAT/RAT/OAT x ``AIRFLOW`` with a propagated uncertainty band
    (capped at ``warn``), and without either the system is declined. Heating-mode samples (the
    heating valve above 5 %) are judged against the heating Vot. Severity per system: ``fault``
    under-ventilated, ``warn`` over-ventilated (a conditioning-energy penalty), ``ok`` adequate,
    ``info`` uncertain / insufficient / declined; assumed zone inputs cap it at ``warn``.
    """

    name = "ventilation_system_62_1"
    roles_required: tuple = ()
    roles_optional = (
        Role.OA_AIRFLOW,
        Role.AIRFLOW,
        Role.MIXED_AIR_TEMP,
        Role.RETURN_AIR_TEMP,
        Role.OAT,
        Role.HEAT_VALVE,
        Role.SUPPLY_FAN_STATUS,
        Role.SUPPLY_FAN_SPEED,
        Role.OCCUPANCY,
        Role.WARMUP,
        Role.COOLDOWN,
    )
    wants_topology = True

    def __init__(
        self,
        *,
        zones=(),
        systems=None,
        method: str = "simplified",
        ez_cooling: float = DEFAULT_EZ_COOLING,
        ez_heating: float = DEFAULT_EZ_HEATING,
        under_tol: float = 0.9,
        over_factor: float = 1.5,
        heat_active_pct: float = 5.0,
        start_hour: int = 7,
        end_hour: int = 18,
        occupied_days: tuple = (0, 1, 2, 3, 4),
        sensor_err_f: float = 2.0,
        flow_uncertainty: float = 0.10,
        min_samples: int = 24,
    ):
        zs = list(zones or ())
        self.zones = [z if isinstance(z, VentZone) else zones_from_records([z])[0] for z in zs]
        self.systems = dict(systems or {})
        unknown = {
            k
            for v in self.systems.values()
            for k in v
            if k not in ("ps", "d", "vps_cfm", "system_type", "method")
        }
        if unknown:
            raise ValueError(f"unknown ventilation system key(s): {sorted(unknown)}")
        self.method = method
        self.ez_cooling, self.ez_heating = ez_cooling, ez_heating
        self.under_tol, self.over_factor = under_tol, over_factor
        self.heat_active_pct = heat_active_pct
        self.start_hour, self.end_hour = start_hour, end_hour
        self.occupied_days = tuple(occupied_days)
        self.sensor_err_f, self.flow_uncertainty = sensor_err_f, flow_uncertainty
        self.min_samples = min_samples

    # ---------------------------------------------------------------- inputs
    @staticmethod
    def _oa_capable(fr: pd.DataFrame) -> bool:
        if Role.OA_AIRFLOW in fr.columns and fr[Role.OA_AIRFLOW].notna().any():
            return True
        need = (Role.AIRFLOW, Role.MIXED_AIR_TEMP, Role.RETURN_AIR_TEMP, Role.OAT)
        return all(r in fr.columns and fr[r].notna().any() for r in need)

    def _assign(self, sources: dict, topology) -> tuple[dict, dict, list]:
        """``({zone: system}, {zone: provenance}, caveats)`` -- declared, topology, single."""
        by_norm = {_norm_id(e): e for e in sources}
        assign: dict = {}
        prov: dict = {}
        caveats: list = []
        pending = []
        for z in self.zones:
            if z.system:
                sysid = by_norm.get(_norm_id(z.system), z.system)
                assign[z.zone], prov[z.zone] = sysid, "declared"
            else:
                pending.append(z.zone)
        if pending and topology is not None:
            nodes = {_norm_id(n): n for e in topology.edges for n in e}
            ids = {zid: nodes.get(_norm_id(zid)) for zid in pending}
            got = topology.group_map(
                [v for v in ids.values() if v is not None], pred=lambda e: e in sources
            )
            tprov = getattr(topology, "provenance", "explicit")
            for zid, node in ids.items():
                if node is not None and node in got:
                    assign[zid] = got[node]
                    prov[zid] = "declared" if tprov == "explicit" else tprov
            if tprov == "heuristic" and any(prov.get(z) == "heuristic" for z in pending):
                caveats.append(HEURISTIC_CAVEAT)
            if tprov == "semantic" and any(prov.get(z) == "semantic" for z in pending):
                caveats.append(
                    "zone -> air handler membership read from the semantic (Brick) model: "
                    "verify it against the mechanical drawings; severity capped at warn"
                )
            pending = [z for z in pending if z not in assign]
        if pending and len(sources) == 1:
            only = next(iter(sources))
            for zid in pending:
                assign[zid], prov[zid] = only, "single_source"
            caveats.append(
                f"{len(pending)} zone(s) with no declared system assumed served by the only OA "
                f"source, {only}; severity capped at warn"
            )
            pending = []
        if pending:
            caveats.append(
                f"{len(pending)} zone(s) could not be attributed to an air handler and were not "
                "used: " + ", ".join(sorted(pending)[:8]) + ("..." if len(pending) > 8 else "")
            )
        return assign, prov, caveats

    def _occupied(self, fr: pd.DataFrame) -> pd.Series:
        if Role.OCCUPANCY in fr.columns and fr[Role.OCCUPANCY].notna().any():
            return occupied_mask(
                fr.index,
                start_hour=0,
                end_hour=24,
                days=range(7),
                occ=fr[Role.OCCUPANCY],
                warmup=fr.get(Role.WARMUP),
                cooldown=fr.get(Role.COOLDOWN),
            )
        return occupied_mask(
            fr.index,
            start_hour=self.start_hour,
            end_hour=self.end_hour,
            days=self.occupied_days,
            warmup=fr.get(Role.WARMUP),
            cooldown=fr.get(Role.COOLDOWN),
        )

    @staticmethod
    def _estimate_unusable(fr: pd.DataFrame, judged: pd.Series) -> str | None:
        """Why the mixing temperatures cannot carry an OA estimate (None when they can): one of
        them is a copy of another point, or MAT leaves the OA/RA band too often (#16's checks)."""
        from ..sensorhealth import copied_signal_consistency, mixing_consistency

        cols = [
            r
            for r in (Role.MIXED_AIR_TEMP, Role.RETURN_AIR_TEMP, Role.OAT, Role.SUPPLY_AIR_TEMP)
            if r in fr.columns
        ]
        cp = copied_signal_consistency(fr[cols])
        if cp.severity == "fault":
            return cp.summary
        mx = mixing_consistency(fr[judged.reindex(fr.index).fillna(False).to_numpy()])
        if mx.severity in ("warn", "fault"):
            return mx.summary
        return None

    # ---------------------------------------------------------------- one system
    def _system(self, sysid: str, fr: pd.DataFrame, zones: list, cap: bool) -> dict:
        from ..schedules import FAN_GATE_NONE, fan_on_mask

        cfg = self.systems.get(sysid) or self.systems.get(
            next((k for k in self.systems if _norm_id(k) == _norm_id(sysid)), ""), {}
        )
        req = system_outdoor_air(
            zones,
            system=sysid,
            system_type=cfg.get("system_type", "multiple_zone"),
            method=cfg.get("method", self.method),
            ps=cfg.get("ps"),
            d=cfg.get("d"),
            vps_cfm=cfg.get("vps_cfm"),
            ez_cooling=self.ez_cooling,
            ez_heating=self.ez_heating,
        )
        caveats = list(req.caveats)
        out: dict = {
            "n_zones": len(zones),
            "zones": [z.zone for z in zones],
            "requirement": {
                k: v for k, v in req.as_dict().items() if k not in ("zones", "caveats", "system")
            },
            "zone_detail": req.zones,
        }
        if req.declined:
            out.update(severity="info", status="declined", reason=req.declined)
            out["summary"] = f"{sysid}: 62.1 system VRP not evaluated -- {req.declined}"
            out["caveats"] = caveats
            return out
        occ = self._occupied(fr)
        fan, fan_src = fan_on_mask(fr)
        judged = occ.copy()
        if fan is not None:
            judged &= fan.reindex(fr.index).fillna(False).astype(bool)
        else:
            caveats.append("no fan signal: occupied samples judged with the fan state unknown")
        heat = None
        if Role.HEAT_VALVE in fr.columns and fr[Role.HEAT_VALVE].notna().any():
            from ..units import normalize_percent

            heat = normalize_percent(pd.to_numeric(fr[Role.HEAT_VALVE], errors="coerce"))
            heat = heat > self.heat_active_pct
        elif req.vot_heating_cfm != req.vot_cooling_cfm:
            caveats.append(
                "no heating-valve point: every sample judged against the cooling-mode Vot "
                f"({req.vot_cooling_cfm:,.0f} cfm; heating-mode Vot {req.vot_heating_cfm:,.0f})"
            )
        lo = hi = None
        if Role.OA_AIRFLOW in fr.columns and fr[Role.OA_AIRFLOW].notna().any():
            oa = pd.to_numeric(fr[Role.OA_AIRFLOW], errors="coerce")
        elif self._oa_capable(fr):
            unusable = self._estimate_unusable(fr, judged)
            if unusable:
                out.update(
                    severity="info",
                    status="declined",
                    reason=f"no OA flow station, and the OA estimate is not usable: {unusable}",
                )
                out["summary"] = f"{sysid}: 62.1 system VRP not evaluated -- {out['reason']}"
                out["caveats"] = caveats
                return out
            est = estimate_oa_cfm(
                fr[Role.MIXED_AIR_TEMP],
                fr[Role.RETURN_AIR_TEMP],
                fr[Role.OAT],
                fr[Role.AIRFLOW],
                sensor_err_f=self.sensor_err_f,
                flow_uncertainty=self.flow_uncertainty,
            )
            oa, lo, hi = est["oa_cfm"], est["oa_lo_cfm"], est["oa_hi_cfm"]
            cap = True
            caveats.append(
                "no OA flow station: OA estimated from MAT/RAT/OAT x supply airflow on samples "
                f"with |OAT - RAT| >= 10 F, +/-{self.sensor_err_f:g} F per sensor and "
                f"+/-{100 * self.flow_uncertainty:.0f} % on supply flow; a biased MAT sensor "
                "shifts it (see the mixed-air flow balance); severity capped at warn"
            )
        else:
            out.update(
                severity="info",
                status="declined",
                reason="no OA flow station and no MAT/RAT/OAT + supply airflow to estimate OA",
            )
            out["summary"] = f"{sysid}: 62.1 system VRP not evaluated -- {out['reason']}"
            out["caveats"] = caveats
            return out
        res = assess_system_62_1(
            oa,
            req,
            heating_mask=heat,
            judged_mask=judged,
            oa_lo_cfm=lo,
            oa_hi_cfm=hi,
            under_tol=self.under_tol,
            over_factor=self.over_factor,
            min_samples=self.min_samples,
        )
        if req.assumed:
            cap = True
        sev = {"under": "fault", "over": "warn", "adequate": "ok"}.get(res.status, "info")
        if cap and sev == "fault":
            sev = "warn"
        band = (
            f" (band {res.ratio_lo:.2f}-{res.ratio_hi:.2f})"
            if res.ratio_lo is not None and res.ratio_hi is not None
            else ""
        )
        vot = (
            f"Vot {req.vot_cooling_cfm:,.0f} cfm"
            if req.vot_cooling_cfm == req.vot_heating_cfm
            else f"Vot {req.vot_cooling_cfm:,.0f} cfm cooling / {req.vot_heating_cfm:,.0f} heating"
        )
        how = (
            f"{req.method} Ev {req.ev_cooling:.2f}, D {req.d:.2f}"
            if req.system_type == "multiple_zone"
            else req.system_type.replace("_", " ")
        )
        if res.status in ("insufficient",):
            tail = f"too few occupied, fan-on samples to judge ({res.n})"
        else:
            tail = (
                f"OA {res.measured_cfm:,.0f} cfm vs {vot} ({how}; {len(zones)} zones) -- "
                f"ratio {res.ratio:.2f}{band}: {res.status}"
            )
            if res.status == "under":
                tail += f", {res.under_hours_pct:.0f}% of judged hours under"
            elif res.status == "over":
                tail += " (conditioning-energy penalty)"
        out.update(
            severity=sev,
            status=res.status,
            basis=res.basis,
            fan_gate=fan_src if fan is not None else FAN_GATE_NONE,
            **{
                k: v
                for k, v in res.as_dict().items()
                if k not in ("requirement", "system", "status", "basis")
            },
        )
        out["summary"] = f"{sysid}: {tail}"
        out["caveats"] = caveats
        return out

    # ---------------------------------------------------------------- the rule
    def analyze_fleet(self, frames: dict, *, topology=None) -> Finding:
        """Judge every air handler with configured zones; one Finding with ``per_system``."""
        base = {"n_zones_configured": len(self.zones)}
        if not self.zones:
            return Finding(
                rule=self.name,
                equip="<fleet>",
                severity="info",
                metrics={**base, "declined": True},
                summary=(
                    "62.1 system VRP not evaluated: needs zone design inputs (floor area, "
                    "population, space type) -- the config's ventilation.zones"
                ),
                caveats=["ventilation.zones not configured: nothing in the trends carries them"],
            )
        sources = {e: fr for e, fr in frames.items() if self._oa_capable(fr)}
        assign, prov, caveats = self._assign(sources, topology)
        by_sys: dict = {}
        for z in self.zones:
            if z.zone in assign:
                by_sys.setdefault(assign[z.zone], []).append(z)
        per: dict = {}
        worst, worst_sys = "ok", None
        rank = _SEVERITY_RANK
        for sysid, zs in sorted(by_sys.items()):
            provs = sorted({prov[z.zone] for z in zs})
            cap = any(p != "declared" for p in provs)
            if sysid not in sources:
                why = (
                    f"{sysid} is not an equipment with an OA signal in this run"
                    if sysid not in frames
                    else f"{sysid} has no OA flow and no MAT/RAT/OAT + supply airflow"
                )
                per[sysid] = {
                    "severity": "info",
                    "status": "declined",
                    "reason": why,
                    "n_zones": len(zs),
                    "zones": [z.zone for z in zs],
                    "membership": provs,
                    "summary": f"{sysid}: 62.1 system VRP not evaluated -- {why}",
                    "caveats": [],
                }
            else:
                per[sysid] = {**self._system(sysid, sources[sysid], zs, cap), "membership": provs}
            if topology is not None:
                served = (
                    set(topology.zones_of(sysid))
                    if sysid in {n for e in topology.edges for n in e}
                    else set()
                )
                listed = {_norm_id(z.zone) for z in zs}
                extra = sorted(x for x in served if _norm_id(x) not in listed)
                if extra:
                    per[sysid]["n_served_without_inputs"] = len(extra)
                    per[sysid]["caveats"].append(
                        f"the topology lists {len(extra)} more terminal(s) under {sysid} with no "
                        "ventilation inputs: Vot covers the listed zones only (an adequate or "
                        "over verdict may be over-stated)"
                    )
            caveats.extend(f"{sysid}: {c}" for c in per[sysid]["caveats"])
            sev = per[sysid]["severity"]
            if worst_sys is None or rank[sev] > rank[worst]:
                worst, worst_sys = sev, sysid
        metrics = {
            **base,
            "n_systems": len(per),
            "n_zones_attributed": len(assign),
            "method": self.method,
            "per_system": per,
        }
        if not per:
            return Finding(
                rule=self.name,
                equip="<fleet>",
                severity="info",
                metrics={**metrics, "declined": True},
                caveats=caveats,
                summary="62.1 system VRP not evaluated: no zone could be joined to an air handler",
            )
        flagged = [k for k, v in per.items() if v["severity"] in ("warn", "fault")]
        if flagged:
            summary = (
                f"62.1 system VRP: {len(flagged)} of {len(per)} system(s) flagged -- "
                + per[str(worst_sys)]["summary"]
            )
        else:
            summary = f"62.1 system VRP: {len(per)} system(s) judged; none flagged -- " + "; ".join(
                v["summary"] for v in per.values()
            )
        return Finding(
            rule=self.name,
            equip="<fleet>",
            severity=worst if worst_sys is not None else "info",
            metrics=metrics,
            caveats=caveats,
            summary=summary,
        )
