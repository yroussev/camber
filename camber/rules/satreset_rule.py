"""Rule: supply-air-temperature reset behavior.

Supply air held cold regardless of load (no upward reset at low cooling demand)
sustains terminal reheat and wastes energy. Adapts
:func:`camber.satreset.analyze_satreset` to the role-frame interface; needs
SUPPLY_AIR_TEMP, and uses COOL_VALVE (to isolate cooling-mode hours) and OAT
(the reset regressor) when present.
"""

from __future__ import annotations

import math

import pandas as pd

from ..model.roles import Role
from ..satreset import analyze_satreset
from .base import Finding

_ROLE_TO_SAT_COL = {
    Role.SUPPLY_AIR_TEMP: "SupplyAir",
    Role.COOL_VALVE: "CHW_Valve",
    Role.OCCUPANCY: "Occupancy",
    Role.WARMUP: "WarmUp",
    Role.COOLDOWN: "CoolDown",
}


class SupplyAirReset:
    """Detects missing/weak supply-air-temperature reset that sustains reheat
    (PNNL Re-tuning / G36)."""

    name = "supply_air_reset"
    roles_required = (Role.SUPPLY_AIR_TEMP,)
    roles_optional = (Role.COOL_VALVE, Role.OAT, Role.OCCUPANCY, Role.WARMUP, Role.COOLDOWN)

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        cols = {r: c for r, c in _ROLE_TO_SAT_COL.items() if r in frame.columns}
        legacy = frame.rename(columns=cols)
        oat = frame[Role.OAT] if Role.OAT in frame.columns else None
        res = analyze_satreset(legacy, equip, oat=oat)
        if res is None:
            return Finding(
                rule=self.name, equip=equip, severity="info", summary="insufficient data"
            )
        # Flag when SAT sits cold most of the time and isn't reset upward at low
        # load (flat/near-zero or load-tracking slope). A clear upward reset is ok.
        # The reset SLOPE needs OAT; without it slope is None (not evaluated) -- the
        # cold-dominant check still stands (it needs no OAT), but we caveat the missing
        # reset judgement and never format a confident slope.
        slope = res.slope_per_F
        caveats = []
        reset_evaluated = slope is not None and not math.isnan(slope)
        if not reset_evaluated:
            caveats.append("SAT reset not evaluated: no OAT")
        resetting_up = reset_evaluated and slope > 0.10
        cold_dominant = res.pct_sat_below_58 >= 50.0
        if resetting_up:
            severity = "ok"
        elif cold_dominant:
            severity = "warn"
        else:
            severity = "info"
        slope_note = f"slope {slope:+.2f} F/F" if reset_evaluated else "slope n/a (no OAT)"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "sat_median": res.sat_median,
                "sat_std": res.sat_std,
                "slope_per_F": res.slope_per_F,
                "r2": res.r2,
                "pct_sat_below_58": res.pct_sat_below_58,
                "n_considered": res.n_considered,
            },
            summary=(
                f"{equip}: SAT median {res.sat_median:.1f}F, {slope_note}, "
                f"<58F {res.pct_sat_below_58:.0f}% of cooling hours -- {res.verdict}"
            ),
            caveats=caveats,
        )

    def evidence(self, equip: str, frame: pd.DataFrame):
        """Pattern J: SAT-vs-OAT against the reset schedule (off-schedule points shaded)."""
        from ..charts.diagnostic import TEMPLATES
        from ..charts.evidence import Evidence

        if Role.SUPPLY_AIR_TEMP in frame.columns and Role.OAT in frame.columns:
            return Evidence(
                renderer="diagnostic", template=TEMPLATES["sat_reset"], title=f"{equip}: SAT reset"
            )
        return None
