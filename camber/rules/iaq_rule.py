"""Rule: CO2-based ventilation adequacy (ASHRAE 62.1 / ventilation-rate guidance).

Flags a zone whose CO2 sits persistently above the elevated threshold during occupancy
(**under-ventilation** -- an IAQ concern) or barely rises above outdoor (**over-
ventilation** -- a conditioning-energy penalty in a hot climate). Adapts
:func:`camber.iaq.analyze_co2_ventilation` to the role-frame interface. The air-quality
companion to the Std-55 thermal-comfort analytic.
"""

from __future__ import annotations

import pandas as pd

from ..iaq import analyze_co2_ventilation
from ..model.roles import Role
from .base import Finding

_ROLE_TO_COL = {Role.CO2: "CO2", Role.OUTDOOR_CO2: "OutdoorCO2"}


class CO2Ventilation:
    """Detects under- or over-ventilation from zone CO2 (ASHRAE 62.1 ventilation-rate proxy)."""

    name = "co2_ventilation"
    roles_required = (Role.CO2,)
    roles_optional = (Role.OUTDOOR_CO2,)

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        cols = {r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns}
        legacy = frame.rename(columns=cols)
        res = analyze_co2_ventilation(legacy, equip)
        if res is None:
            return Finding(rule=self.name, equip=equip, severity="info",
                           summary="insufficient data (need zone CO2 over occupied hours)")
        # Under-ventilation (high CO2) is the IAQ fault and drives severity; persistent
        # over-ventilation is an energy opportunity surfaced as a warn at most.
        if res.under_vent_pct >= 20.0:
            severity = "fault"
        elif res.under_vent_pct >= 5.0:
            severity = "warn"
        elif res.over_vent_pct >= 60.0:
            severity = "warn"
        else:
            severity = "ok"
        if res.under_vent_pct >= 5.0:
            tail = (f"under-ventilated {res.under_vent_pct:.0f}% of occupied hours "
                    f"(CO2 p95 {res.co2_p95_ppm:.0f} ppm, > {res.high_ppm:.0f} threshold)")
        elif res.over_vent_pct >= 60.0:
            tail = (f"over-ventilated: CO2 near outdoor {res.over_vent_pct:.0f}% of "
                    f"occupied hours (energy opportunity)")
        else:
            tail = f"CO2 median {res.co2_median_ppm:.0f} ppm; ventilation adequate"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "co2_median_ppm": res.co2_median_ppm,
                "co2_p95_ppm": res.co2_p95_ppm,
                "under_vent_pct": res.under_vent_pct,
                "over_vent_pct": res.over_vent_pct,
                "outdoor_co2_ppm": res.outdoor_co2_ppm,
                "n_occupied": res.n_occupied,
            },
            summary=f"{equip}: {tail}",
        )
