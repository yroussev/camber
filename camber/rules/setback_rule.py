"""Rule: AHU night/weekend setback (PNNL Re-tuning Ch.5).

Flags an air handler whose supply fan runs during unoccupied hours -- no effective
night/weekend setback, the cheapest large saver. Adapts
:func:`camber.setback.analyze_setback` to the role-frame interface.

Since 0.93 (#43) a fan that runs unoccupied only to hold the zone at its setback temperature is
not "missing" its setback: when the runtime test fails, the zone temperature (``SPACE_TEMP``, else
the return air while the fan runs) against the unoccupied setpoint (``HEAT_SP``/``COOL_SP`` in
unoccupied hours, else the configured ``unoccupied_heat_sp_f``/``unoccupied_cool_sp_f``), or
against its own occupied temperature, decides whether the fan was cycling to hold the setback.
See :mod:`camber.setback`.
"""

from __future__ import annotations

import pandas as pd

from ..model.roles import Role
from ..setback import analyze_setback
from .base import Finding

_ROLE_TO_COL = {
    Role.SUPPLY_FAN_STATUS: "SupplyFanStatus",
    Role.SUPPLY_FAN_SPEED: "SupplyFanSpeed",
    Role.OCCUPANCY: "Occupancy",
    # 0.93 (#43): the held-setback test
    Role.SPACE_TEMP: "ZoneTemp",
    Role.RETURN_AIR_TEMP: "ReturnAir",
    Role.HEAT_SP: "HeatSP",
    Role.COOL_SP: "CoolSP",
}


_SOURCE = {"ZoneTemp": "space_temp", "ReturnAir": "return_air_temp"}


def _pct(v: float) -> str:
    """A runtime percentage with enough digits that a near-idle unit doesn't read as 0%."""
    return f"{v:.0f}%" if v >= 10 or v == 0 else f"{v:.2g}%"


class NightWeekendSetback:
    """Detects an AHU fan running unoccupied / missing night-weekend setback
    (PNNL Re-tuning Ch.5)."""

    name = "night_weekend_setback"
    # status preferred; speed is an acceptable substitute, so require neither
    # specifically -- gate on the pair via a custom check below.
    roles_required = ()
    roles_optional = (
        Role.SUPPLY_FAN_STATUS,
        Role.SUPPLY_FAN_SPEED,
        Role.OCCUPANCY,
        # 0.93 (#43): zone temperature (or return air) and setpoints for the held-setback test
        Role.SPACE_TEMP,
        Role.RETURN_AIR_TEMP,
        Role.HEAT_SP,
        Role.COOL_SP,
    )

    def __init__(
        self,
        *,
        start_hour: float = 7,
        end_hour: float = 18,
        occupied_days=(0, 1, 2, 3, 4),
        min_unoccupied_run_pct: float = 5.0,
        unoccupied_heat_sp_f: float | None = None,
        unoccupied_cool_sp_f: float | None = None,
        min_setback_depth_f: float = 3.0,
        max_hold_duty_pct: float = 90.0,
    ):
        # The schedule is only an assumption: a trended OCCUPANCY point replaces it.
        # ``min_unoccupied_run_pct``: unoccupied runtime below this share of unoccupied time is
        # immaterial -- the rule only fires when the unit actually runs unoccupied (#57).
        # 0.93 (#43), the held-setback test: ``unoccupied_heat_sp_f``/``unoccupied_cool_sp_f`` are
        # the unoccupied setpoints when none is trended (degF); ``min_setback_depth_f`` is how far
        # below (heating) or above (cooling) the occupied temperature a held setback sits when no
        # setpoint is known; a fan whose mean duty in the unoccupied hours it runs reaches
        # ``max_hold_duty_pct`` is running through the night, not cycling to hold a setback.
        self.min_unoccupied_run_pct = float(min_unoccupied_run_pct)
        self.unoccupied_heat_sp_f = (
            None if unoccupied_heat_sp_f is None else float(unoccupied_heat_sp_f)
        )
        self.unoccupied_cool_sp_f = (
            None if unoccupied_cool_sp_f is None else float(unoccupied_cool_sp_f)
        )
        self.min_setback_depth_f = float(min_setback_depth_f)
        self.max_hold_duty_pct = float(max_hold_duty_pct)
        self.start_hour = start_hour
        self.end_hour = end_hour
        self.occupied_days = tuple(occupied_days)

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        if (
            Role.SUPPLY_FAN_STATUS not in frame.columns
            and Role.SUPPLY_FAN_SPEED not in frame.columns
        ):
            return Finding(
                rule=self.name, equip=equip, severity="info", summary="no fan status or speed"
            )
        cols = {r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns}
        legacy = frame.rename(columns=cols)
        res = analyze_setback(
            legacy,
            equip,
            start_hour=self.start_hour,
            end_hour=self.end_hour,
            occupied_days=self.occupied_days,
            min_unoccupied_run_pct=self.min_unoccupied_run_pct,
            unoccupied_heat_sp_f=self.unoccupied_heat_sp_f,
            unoccupied_cool_sp_f=self.unoccupied_cool_sp_f,
            min_setback_depth_f=self.min_setback_depth_f,
            max_hold_duty=self.max_hold_duty_pct / 100.0,
        )
        if res is None:
            return Finding(
                rule=self.name, equip=equip, severity="info", summary="insufficient data"
            )
        un = res.fan_run_unoccupied_pct
        ratio = res.unoccupied_to_occupied_ratio
        ratio_txt = f", ratio {ratio:.2f}" if ratio is not None else ""
        held = res.setback_basis == "held_setback"
        if held:
            verdict = "effective (fan cycling to hold)"
        elif not res.setback_effective:
            verdict = "MISSING/weak"
        elif un < self.min_unoccupied_run_pct:
            verdict = (
                f"effective (unoccupied runtime below the {self.min_unoccupied_run_pct:g}% "
                "materiality floor)"
            )
        else:
            verdict = "effective"
        # High unoccupied run = no setback. ok only if setback is effective.
        if res.setback_effective:
            severity = "ok"
        elif un >= 50.0:
            severity = "fault"
        else:
            severity = "warn"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "fan_run_unoccupied_pct": res.fan_run_unoccupied_pct,
                "fan_run_occupied_pct": res.fan_run_occupied_pct,
                "setback_effective": res.setback_effective,
                "n_unoccupied": res.n_unoccupied,
                "n_occupied": res.n_occupied,
                "unoccupied_to_occupied_ratio": res.unoccupied_to_occupied_ratio,
                "min_unoccupied_run_pct": res.min_unoccupied_run_pct,
                # 0.93 (#43): the held-setback test
                "setback_basis": res.setback_basis,
                "unoccupied_duty_when_running_pct": res.unoccupied_duty_when_running_pct,
                "zone_temp_source": _SOURCE.get(res.zone_temp_source or ""),
                "zone_temp_unoccupied_f": res.zone_temp_unoccupied_f,
                "zone_temp_occupied_f": res.zone_temp_occupied_f,
                "unoccupied_heat_sp_f": res.unoccupied_heat_sp_f,
                "unoccupied_cool_sp_f": res.unoccupied_cool_sp_f,
                "held_side": res.held_side,
            },
            caveats=self._caveats(frame, res),
            summary=(
                f"{equip}: supply fan runs {_pct(res.fan_run_unoccupied_pct)} of "
                f"unoccupied hours (vs {_pct(res.fan_run_occupied_pct)} occupied{ratio_txt}); "
                f"setback {verdict}{self._held_txt(res) if held else ''}"
            ),
        )

    @staticmethod
    def _held_txt(res) -> str:
        """The held-setback evidence: duty when running and where the zone sat."""
        where = "zone" if res.zone_temp_source == "ZoneTemp" else "return air"
        sp = res.unoccupied_heat_sp_f if res.held_side == "heating" else res.unoccupied_cool_sp_f
        sp_txt = f" against a {sp:.1f}F setback" if sp is not None else ""
        occ_txt = (
            f" (occupied {res.zone_temp_occupied_f:.1f}F)"
            if res.zone_temp_occupied_f is not None
            else ""
        )
        return (
            f": fan duty {res.unoccupied_duty_when_running_pct:.0f}% in the unoccupied hours it "
            f"runs, {where} {res.zone_temp_unoccupied_f:.1f}F{occ_txt}{sp_txt}"
        )

    def _caveats(self, frame: pd.DataFrame, res) -> list:
        out = []
        if Role.OCCUPANCY not in frame.columns:
            out.append(
                f"no trended occupancy: unoccupied = outside the assumed schedule "
                f"({self.start_hour:g}-{self.end_hour:g}h, days {list(self.occupied_days)})"
            )
        if res.setback_basis == "held_setback":
            if res.zone_temp_source == "ReturnAir":
                out.append(
                    "no zone temperature on this unit: the return air while the fan runs stands "
                    "in for the zones it serves"
                )
            if res.unoccupied_heat_sp_f is None and res.unoccupied_cool_sp_f is None:
                out.append(
                    "no unoccupied setpoint (trended or configured): the setback is inferred from "
                    f"the zone sitting {self.min_setback_depth_f:g}F or more beyond its occupied "
                    "temperature while the fan cycles"
                )
        elif not res.setback_effective and res.zone_temp_source is None:
            out.append(
                "no zone or return-air temperature: runtime alone cannot tell a fan cycling to "
                "hold a setback from a missing one (map space_temp, or configure "
                "unoccupied_heat_sp_f / unoccupied_cool_sp_f with a zone signal)"
            )
        return out

    def evidence(self, equip: str, frame: pd.DataFrame):
        """Pattern J: a carpet of fan runtime — the fault *is* the schedule (night/weekend run)."""
        from ..charts.evidence import Evidence

        col = (
            Role.SUPPLY_FAN_SPEED
            if Role.SUPPLY_FAN_SPEED in frame.columns
            else Role.SUPPLY_FAN_STATUS
            if Role.SUPPLY_FAN_STATUS in frame.columns
            else None
        )
        if col is not None:
            return Evidence(renderer="carpet", roles=[col], title=f"{equip}: fan schedule")
        return None
