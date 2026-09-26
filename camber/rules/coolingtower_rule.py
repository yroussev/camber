"""Rule: cooling-tower approach (condenser-water supply vs wet-bulb; CTI/ASHRAE).

Flags a cooling tower achieving a persistently high approach at load -- fouled fill,
plugged nozzles, reduced airflow, or an over-loaded tower -- which raises condenser
water temperature, chiller lift, and kW/ton. Adapts
:func:`camber.coolingtower.analyze_cooling_tower_approach` to the role-frame interface.
Wet-bulb is taken from a mapped point or derived from OAT + RH; OAT/RH are
building-level and arrive via the runner's ``shared`` channel. The design approach is
tower/climate-specific, so it is a constructor parameter, not a baked constant.
"""

from __future__ import annotations

import pandas as pd

from ..coolingtower import analyze_cooling_tower_approach
from ..model.roles import Role
from ..units import normalize_percent
from .base import Finding

_ROLE_TO_COL = {
    Role.CW_SUPPLY_TEMP: "CWS_Temp",
    Role.CW_RETURN_TEMP: "CWR_Temp",
    Role.WETBULB_TEMP: "WetBulb",
    Role.OAT: "OAT",
    Role.OUTDOOR_RH: "RH",
    Role.TOWER_FAN_SPEED: "TowerFanSpeed",
}


class CoolingTowerApproach:
    """Detects a cooling tower running above its design approach at load (CTI/ASHRAE)."""

    name = "cooling_tower_approach"
    roles_required = (Role.CW_SUPPLY_TEMP,)
    roles_optional = (
        Role.WETBULB_TEMP,
        Role.OAT,
        Role.OUTDOOR_RH,
        Role.CW_RETURN_TEMP,
        Role.TOWER_FAN_SPEED,
    )

    def __init__(
        self,
        design_approach_f: float = 7.0,
        *,
        min_effort_pct: float | None = 90.0,
        elevation_ft: float | None = None,
        pressure_psia: float | None = None,
    ):
        # Tower/climate-specific: confirm against the tower schedule / selection.
        self.design_approach_f = design_approach_f
        self.min_effort_pct = min_effort_pct
        # A wet-bulb derived from OAT + RH assumes sea level unless the site elevation (or a
        # measured barometric pressure) is given -- see camber.coolingtower.stull_wetbulb_f.
        self.elevation_ft = elevation_ft
        self.pressure_psia = pressure_psia

    def _decline_reason(self, frame: pd.DataFrame) -> tuple[str, str, dict]:
        """Why the analysis returned nothing -- stated as what actually happened.

        Returns ``(summary_tail, caveat, metrics)``. The analysis needs a CW-supply temperature, a
        wet-bulb source, and (when a fan speed is trended) at least 10 samples at high fan effort;
        this names whichever of those was missing rather than assuming it was the fan.
        """
        has_wb = Role.WETBULB_TEMP in frame.columns or (
            Role.OAT in frame.columns and Role.OUTDOOR_RH in frame.columns
        )
        if not has_wb:
            return (
                "no wet-bulb source (need a wet-bulb point, or OAT + RH)",
                "could not evaluate tower approach: no wet-bulb, and no OAT + RH to derive it",
                {},
            )
        fan = normalize_percent(pd.to_numeric(frame[Role.TOWER_FAN_SPEED], errors="coerce"))
        n_high = int((fan >= self.min_effort_pct).sum())
        metrics = {"n_high_effort": n_high}
        if n_high == 0:
            peak = float(fan.max()) if fan.notna().any() else float("nan")
            return (
                f"the tower fan never reached {self.min_effort_pct:.0f}% "
                f"(peak {peak:.0f}%; at part fan the approach is the controller's choice, not the "
                "tower's limit)",
                "could not evaluate tower approach: the fan never reached the high-effort gate",
                metrics,
            )
        return (
            f"only {n_high} sample(s) at >= {self.min_effort_pct:.0f}% fan -- after requiring a "
            "plausible CW-supply and wet-bulb reading at the same time, fewer than the 10 needed "
            "to judge the tower's limit",
            f"could not evaluate tower approach: too few usable samples at high fan effort "
            f"({n_high} reached {self.min_effort_pct:.0f}%, need 10 with valid CW supply and "
            "wet-bulb)",
            metrics,
        )

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        cols = {r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns}
        legacy = frame.rename(columns=cols)
        # a 0-1 fan speed would never clear the percent gates (Registry.run normalizes upstream;
        # a direct analyze() call may not have)
        for col in ("TowerFanSpeed", "RH"):
            if col in legacy.columns:
                legacy[col] = normalize_percent(pd.to_numeric(legacy[col], errors="coerce"))
        res = analyze_cooling_tower_approach(
            legacy,
            equip,
            design_approach_f=self.design_approach_f,
            min_effort_pct=self.min_effort_pct,
            elevation_ft=self.elevation_ft,
            pressure_psia=self.pressure_psia,
        )
        if res is None:
            if Role.TOWER_FAN_SPEED in frame.columns and self.min_effort_pct is not None:
                tail, caveat, extra = self._decline_reason(frame)
                return Finding(
                    rule=self.name,
                    equip=equip,
                    severity="info",
                    metrics={"declined": True, **extra},
                    summary=f"{equip}: approach not judged -- {tail}",
                    caveats=[caveat],
                )
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                summary="insufficient data (need CW supply temp + wet-bulb or OAT+RH)",
            )
        caveats = []
        derived_at_sea_level = (
            res.wetbulb_source == "derived"
            and self.elevation_ft is None
            and self.pressure_psia is None
        )
        if derived_at_sea_level:
            caveats.append(
                "wet-bulb derived from OAT + RH assuming sea-level pressure; above sea level it "
                "reads high (~1.4 °F at 500 m, ~2.6 °F at 1600 m in hot, dry air), understating "
                "the approach -- pass elevation_ft to correct it"
            )
        if res.effort_gated is None:
            caveats.append(
                "no tower fan speed trended: hours the tower was deliberately held above its best "
                "approach (a minimum condenser-water temperature in cold weather) could not be "
                "excluded, so a high approach may be correct control"
            )
        ratio = res.approach_median_f / res.design_approach_f if res.design_approach_f else 0.0
        if ratio >= 1.7:
            severity = "fault"
        elif ratio >= 1.3:
            severity = "warn"
        else:
            severity = "ok"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "approach_median_f": res.approach_median_f,
                "design_approach_f": res.design_approach_f,
                "range_median_f": res.range_median_f,
                "pct_hours_high_approach": res.pct_hours_high_approach,
                "wetbulb_source": res.wetbulb_source,
                "n_operating": res.n_operating,
                "effort_gated": res.effort_gated,
                "wetbulb_pressure_basis": (
                    "sea-level"
                    if derived_at_sea_level
                    else "site"
                    if res.wetbulb_source == "derived"
                    else "measured"
                ),
                "n_low_effort_excluded": res.n_low_effort_excluded,
            },
            caveats=caveats,
            summary=(
                f"{equip}: tower approach median {res.approach_median_f:.1f}F"
                f"{' at high fan' if res.effort_gated else ''} "
                f"vs design {res.design_approach_f:.1f}F "
                f"({res.pct_hours_high_approach:.0f}% of operating hours high; "
                f"wet-bulb {res.wetbulb_source})"
            ),
        )
