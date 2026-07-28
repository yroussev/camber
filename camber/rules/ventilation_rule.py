"""Rules: ASHRAE 62.1 ventilation-rate procedure (VRP) and DCV verification.

`DemandControlledVentilation` is config-free — it checks that outdoor air actually modulates
with ventilation demand (CO₂/occupancy), so it auto-registers and runs across any equipment with
an OA signal and a demand signal. `VentilationRateProcedure` needs the zone's design inputs
(area, population, space type) and so is instantiated explicitly (not in the default registry).
Both adapt :mod:`camber.ventilation` to the role-frame interface.
"""

from __future__ import annotations

import pandas as pd

from ..model.roles import Role
from ..schedules import occupied_mask
from ..ventilation import assess_62_1, assess_dcv
from .base import Finding


class DemandControlledVentilation:
    """Verifies DCV: outdoor air should modulate with ventilation demand (CO₂/occupancy).

    Flags a **static** OA signal (fixed OA / DCV not functioning) or one **uncorrelated** with
    demand. Uses OA airflow if present, else OA-damper position; demand is CO₂ if present, else
    occupancy.
    """

    name = "dcv_verification"
    roles_required = (Role.CO2,)
    roles_optional = (Role.OA_AIRFLOW, Role.OA_DAMPER, Role.OCCUPANCY)

    def __init__(
        self,
        *,
        min_corr: float = 0.3,
        min_modulation: float = 0.1,
        co2_setpoint: float | None = None,
        occupied_only: bool = True,
    ):
        self.min_corr = min_corr
        self.min_modulation = min_modulation
        self.co2_setpoint = co2_setpoint
        self.occupied_only = occupied_only

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        oa_role = (
            Role.OA_AIRFLOW
            if Role.OA_AIRFLOW in frame.columns
            else Role.OA_DAMPER
            if Role.OA_DAMPER in frame.columns
            else None
        )
        demand_role = (
            Role.CO2
            if Role.CO2 in frame.columns
            else (Role.OCCUPANCY if Role.OCCUPANCY in frame.columns else None)
        )
        if oa_role is None or demand_role is None:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                summary=f"{equip}: declined — needs an OA signal + a demand signal",
            )
        mask = occupied_mask(frame.index) if self.occupied_only else None
        setpoint = self.co2_setpoint if demand_role is Role.CO2 else None
        res = assess_dcv(
            frame[oa_role],
            frame[demand_role],
            occupied_mask=mask,
            min_corr=self.min_corr,
            min_modulation=self.min_modulation,
            co2_setpoint=setpoint,
            equip=equip,
        )
        if res.status == "insufficient":
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                summary=f"{equip}: insufficient overlapping OA/demand data",
            )
        severity = "warn" if res.status in ("static", "uncorrelated") else "ok"
        if res.co2_breach_at_min_pct and res.co2_breach_at_min_pct >= 10.0:
            severity = "fault"  # under-ventilated while OA pinned at minimum
        msg = {
            "static": "OA does not modulate with demand (DCV not functioning / fixed OA)",
            "uncorrelated": "OA modulates but not with demand",
            "functioning": "OA tracks demand (DCV functioning)",
        }[res.status]
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "status": res.status,
                "correlation": res.correlation,
                "modulation": res.modulation,
                "co2_breach_at_min_pct": res.co2_breach_at_min_pct,
                "n": res.n,
            },
            summary=f"{equip}: {msg} (corr {res.correlation}, modulation {res.modulation})",
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
