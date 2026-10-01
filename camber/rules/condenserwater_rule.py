"""Rule: condenser-water reset (CW supply temp vs wet-bulb; ASHRAE 90.1 / optimization).

Flags a condenser-water loop holding a fixed supply-temp setpoint instead of resetting
it down with wet-bulb -- a missed chiller-efficiency opportunity (colder condenser
water lowers lift / kW-ton). Adapts :func:`camber.condenserwater.analyze_cw_reset` to
the role-frame interface. Wet-bulb is measured or derived from OAT + RH (building-level
points arriving via the runner's ``shared`` channel); a derived wet-bulb takes the site's
``elevation_ft`` / ``pressure_psia`` (0.98, #92) and is caveated at sea level without them.
"""

from __future__ import annotations

import pandas as pd

from ..condenserwater import analyze_cw_reset
from ..model.roles import Role
from .base import Finding

_ROLE_TO_COL = {
    Role.CW_SUPPLY_TEMP: "CWS_Temp",
    Role.CW_RETURN_TEMP: "CWR_Temp",
    Role.WETBULB_TEMP: "WetBulb",
    Role.OAT: "OAT",
    Role.OUTDOOR_RH: "RH",
    Role.TOWER_FAN_SPEED: "TowerFanSpeed",
}


class CondenserWaterReset:
    """Detects a condenser-water loop with no supply-temp reset vs wet-bulb (ASHRAE 90.1)."""

    name = "condenser_water_reset"
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
        reset_slope_flat: float = 0.3,
        *,
        elevation_ft: float | None = None,
        pressure_psia: float | None = None,
    ):
        # An ideal reset tracks wet-bulb ~1:1; below this slope it is effectively flat.
        self.reset_slope_flat = reset_slope_flat
        # 0.98 (#92): a wet-bulb derived from OAT + RH assumes sea level unless the site elevation
        # (or a measured barometric pressure) is given -- as in the cooling-tower rules
        self.elevation_ft = elevation_ft
        self.pressure_psia = pressure_psia

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        cols = {r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns}
        legacy = frame.rename(columns=cols)
        res = analyze_cw_reset(
            legacy,
            equip,
            reset_slope_flat=self.reset_slope_flat,
            elevation_ft=self.elevation_ft,
            pressure_psia=self.pressure_psia,
        )
        if res is None:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                summary="insufficient data (need CW supply temp + wet-bulb or OAT+RH)",
            )
        # No reset is an efficiency opportunity, not a hard fault -> warn.
        severity = "ok" if res.reset_present else "warn"
        note = "resets with wet-bulb" if res.reset_present else "flat setpoint (no wet-bulb reset)"
        caveats = []
        if (
            res.wetbulb_source == "derived"
            and self.elevation_ft is None
            and self.pressure_psia is None
        ):
            caveats.append(
                "wet-bulb derived from OAT + RH assuming sea-level pressure; above sea level it "
                "reads high (~1.4 °F at 500 m, ~2.6 °F at 1600 m in hot, dry air), which shifts "
                "the reset slope -- pass elevation_ft (or set the config's site_elevation_ft) to "
                "correct it"
            )
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "cws_median_f": res.cws_median_f,
                "cws_slope_per_wetbulb": res.cws_slope_per_wetbulb,
                "reset_present": res.reset_present,
                "wetbulb_source": res.wetbulb_source,
                "n_operating": res.n_operating,
            },
            caveats=caveats,
            summary=(
                f"{equip}: CW supply median {res.cws_median_f:.1f}F, "
                f"slope {res.cws_slope_per_wetbulb:.2f} F/F vs wet-bulb; {note}"
            ),
        )
