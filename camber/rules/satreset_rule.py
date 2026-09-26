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
from ..schedules import FAN_GATE_NONE, fan_on_mask
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
    roles_optional = (
        Role.COOL_VALVE,
        Role.OAT,
        Role.OCCUPANCY,
        Role.WARMUP,
        Role.COOLDOWN,
        # fan-on gate: status, else speed, else airflow (see camber.schedules.fan_on_mask)
        Role.SUPPLY_FAN_STATUS,
        Role.SUPPLY_FAN_SPEED,
        Role.AIRFLOW,
    )

    def __init__(self, *, fan_gate: bool = True):
        self.fan_gate = fan_gate

    def _missing(self, frame: pd.DataFrame, fan_src: str) -> list:
        """Optional inputs truly absent -- the three fan signals are alternatives, so one is
        named only when none is present (pre-empts the runner's backstop)."""
        plain = (Role.COOL_VALVE, Role.OAT, Role.OCCUPANCY, Role.WARMUP, Role.COOLDOWN)
        out = [r.value for r in plain if r not in frame.columns]
        if fan_src == FAN_GATE_NONE:
            out.append(Role.SUPPLY_FAN_STATUS.value)
        return out

    def _fan(self, frame: pd.DataFrame):
        """``(fan-on mask | None, source label)`` -- ``(None, "off")`` when gating is disabled."""
        if not self.fan_gate:
            return None, "off"
        return fan_on_mask(frame)

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        cols = {r: c for r, c in _ROLE_TO_SAT_COL.items() if r in frame.columns}
        legacy = frame.rename(columns=cols)
        oat = frame[Role.OAT] if Role.OAT in frame.columns else None
        fan, fan_src = self._fan(frame)
        res = analyze_satreset(legacy, equip, oat=oat, gate=fan)
        if res is None:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={
                    "fan_gate": fan_src,
                    "_missing_optional": self._missing(frame, fan_src),
                },
                summary="insufficient data",
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
                "fan_gate": fan_src,
                "_missing_optional": self._missing(frame, fan_src),
            },
            summary=(
                f"{equip}: SAT median {res.sat_median:.1f}F, {slope_note}, "
                f"<58F {res.pct_sat_below_58:.0f}% of cooling hours -- {res.verdict}"
            ),
            caveats=caveats,
        )

    def evidence(self, equip: str, frame: pd.DataFrame):
        """Pattern J: the SAT-vs-OAT cloud this rule's slope and spread are fitted on.

        The rule judges the *shape* of the reset (a slope, a spread), not a band, so its evidence is
        the cloud itself -- not a packaged reset line. Drawing a generic G36-style band here would
        shade "violations" against a sequence the site never declared (a site sequence, when known,
        is drawn by the RCx report's SAT reset census instead).
        """
        from ..charts.evidence import Evidence
        from ..schedules import occupied_mask

        if Role.SUPPLY_AIR_TEMP not in frame.columns or Role.OAT not in frame.columns:
            return None
        # the samples the fit uses: occupied, cooling (valve open) and a plausible SAT
        occ = frame[Role.OCCUPANCY] if Role.OCCUPANCY in frame.columns else None
        keep = occupied_mask(
            frame.index,
            occ=occ if occ is not None and occ.notna().any() else None,
            warmup=frame[Role.WARMUP] if Role.WARMUP in frame.columns else None,
            cooldown=frame[Role.COOLDOWN] if Role.COOLDOWN in frame.columns else None,
        )
        if Role.COOL_VALVE in frame.columns:
            keep &= frame[Role.COOL_VALVE] > 5.0
        fan = self._fan(frame)[0]
        if fan is not None:
            keep &= fan.to_numpy(dtype=bool)  # built on this frame: same index
        sat = frame[Role.SUPPLY_AIR_TEMP]
        keep &= (sat > 40) & (sat < 90)
        derived = frame.loc[keep.to_numpy(), [Role.SUPPLY_AIR_TEMP, Role.OAT]]
        if derived.dropna().empty:
            return None
        return Evidence(
            renderer="oat_scatter",
            roles=[Role.SUPPLY_AIR_TEMP],
            title=f"{equip}: SAT vs OAT, occupied fan-on cooling hours (reset shape)",
            frame=derived,
        )
