"""Rule: leaking coil valve (PNNL Re-tuning Ch.5/Ch.7).

Flags a coil whose valve is commanded closed but still shifts supply-air temp --
uncommanded heating or cooling the simultaneous-H/C and reheat checks miss.
Adapts :func:`camber.leakvalve.analyze_leak_valves` to the role-frame interface.

Since 0.93 (#42): the supply fan's heat (``fan_heat_f``, G36's ΔT_SF, 2 F by default) is a
constructor parameter and an allowance -- a heating leak must rise beyond it; a mapped fan
status or speed limits the check to fan-on samples; and a coil with its own leaving-air sensor
(``HEAT_COIL_LEAVING_TEMP`` / ``COOL_COIL_LEAVING_TEMP``) is judged on it rather than on the
supply air downstream of the fan.

Since 0.98 (#84), all opt-in with byte-identical defaults: ``measured_fan_heat_f`` (the unit's own
measured fan rise, credited to the cooling-leak test) with ``cool_delta_thr_f`` (its margin);
``occupied_only`` (judge occupied samples only, from the trended occupancy when mapped); and
``judge_heating_on_supply_air=False`` (a dual-duct unit's mapped supply air is the cold deck, so a
heating leak is judged only on the heating coil's own leaving air).
"""

from __future__ import annotations

import pandas as pd

from ..leakvalve import analyze_leak_valves
from ..model.roles import Role
from .base import Finding

_ROLE_TO_COL = {
    Role.COOL_VALVE: "CHW_Valve",
    Role.HEAT_VALVE: "HHW_Valve",
    Role.MIXED_AIR_TEMP: "MixedAir",
    Role.SUPPLY_AIR_TEMP: "SupplyAir",
    # 0.93 (#42): coil leaving-air sensors and the fan gate
    Role.HEAT_COIL_LEAVING_TEMP: "HeatCoilLeaving",
    Role.COOL_COIL_LEAVING_TEMP: "CoolCoilLeaving",
    Role.SUPPLY_FAN_STATUS: "SupplyFanStatus",
    Role.SUPPLY_FAN_SPEED: "SupplyFanSpeed",
    # 0.98 (#84): read only when ``occupied_only`` is set (Role.OCCUPANCY is then optional)
    Role.OCCUPANCY: "Occupancy",
}

_BASIS = {
    "SupplyAir": "supply_air_temp",
    "HeatCoilLeaving": "heat_coil_leaving_temp",
    "CoolCoilLeaving": "cool_coil_leaving_temp",
}


class LeakingValve:
    """Detects a leaking (passing) coil valve via uncommanded SAT shift (PNNL Re-tuning Ch.5/7)."""

    name = "leaking_valve"
    # the cooling coil + air temps are required; the heating coil is optional, so
    # the rule also runs on cooling-only AHUs (heating-leak signature then n/a)
    roles_required = (Role.COOL_VALVE, Role.MIXED_AIR_TEMP, Role.SUPPLY_AIR_TEMP)
    roles_optional: tuple[Role, ...] = (
        Role.HEAT_VALVE,
        # 0.93 (#42)
        Role.HEAT_COIL_LEAVING_TEMP,
        Role.COOL_COIL_LEAVING_TEMP,
        Role.SUPPLY_FAN_STATUS,
        Role.SUPPLY_FAN_SPEED,
    )

    def __init__(
        self,
        *,
        fan_heat_f: float = 2.0,
        delta_thr_f: float = 3.0,
        valve_closed_thr: float = 5.0,
        coil_sensor_fan_heat: bool = False,
        measured_fan_heat_f: float | None = None,
        cool_delta_thr_f: float | None = None,
        occupied_only: bool = False,
        judge_heating_on_supply_air: bool = True,
    ):
        # ``fan_heat_f``: the supply fan's temperature rise between the mixed-air and supply-air
        # sensors (G36's ΔT_SF; its 2 F default is fdd_g36.G36Thresholds.dT_sf), allowed for
        # before a heating leak is called (a cooling leak gets no credit for it). Set it from the
        # unit's fan-on SAT - MAT with the coils idle when that is known.
        # ``coil_sensor_fan_heat``: the coil leaving-air sensors sit downstream of the fan (a
        # blow-through unit), so the allowance applies to them as well.
        self.fan_heat_f = float(fan_heat_f)
        self.delta_thr_f = float(delta_thr_f)
        self.valve_closed_thr = float(valve_closed_thr)
        self.coil_sensor_fan_heat = bool(coil_sensor_fan_heat)
        # 0.98 (#84), opt-in. ``measured_fan_heat_f``: the fan's rise measured on this unit's own
        # known-good, valve-shut, fan-on hours; a cooling leak on the supply-air path is then a
        # rise below ``measured_fan_heat_f - cool_delta_thr_f`` (default: no credit, below
        # ``-delta_thr_f``). ``cool_delta_thr_f`` (None = delta_thr_f) is that margin.
        # ``occupied_only``: occupied samples only, from the trended occupancy when the unit
        # maps one, else the weekday 07-18 schedule. ``judge_heating_on_supply_air=False``: judge
        # a heating leak only on HEAT_COIL_LEAVING_TEMP (a dual-duct unit's supply air is the
        # cold deck, which the hot-deck coil never touches).
        self.measured_fan_heat_f = (
            None if measured_fan_heat_f is None else float(measured_fan_heat_f)
        )
        self.cool_delta_thr_f = None if cool_delta_thr_f is None else float(cool_delta_thr_f)
        self.occupied_only = bool(occupied_only)
        self.judge_heating_on_supply_air = bool(judge_heating_on_supply_air)
        if self.occupied_only:
            # per instance, so the default rule's declared inputs (and every golden) stay as-is
            self.roles_optional = type(self).roles_optional + (Role.OCCUPANCY,)

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        cols = {r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns}
        if not self.occupied_only:
            cols.pop(Role.OCCUPANCY, None)
        legacy = frame.rename(columns=cols)
        res = analyze_leak_valves(
            legacy,
            equip,
            valve_closed_thr=self.valve_closed_thr,
            delta_thr_f=self.delta_thr_f,
            fan_heat_f=self.fan_heat_f,
            coil_sensor_fan_heat=self.coil_sensor_fan_heat,
            measured_fan_heat_f=self.measured_fan_heat_f,
            cool_delta_thr_f=self.cool_delta_thr_f,
            occupied_only=self.occupied_only,
            judge_heating_on_supply_air=self.judge_heating_on_supply_air,
        )
        if res is None:
            return Finding(
                rule=self.name, equip=equip, severity="info", summary="insufficient data"
            )
        worst = max(res.hw_leak_pct, res.chw_leak_pct)
        severity = "fault" if worst >= 30.0 else ("warn" if worst >= 10.0 else "ok")
        which = "HW (heating) coil" if res.hw_leak_pct >= res.chw_leak_pct else "CHW (cooling) coil"
        metrics = {
            "hw_leak_pct": res.hw_leak_pct,
            "chw_leak_pct": res.chw_leak_pct,
            "median_delta_f": res.median_delta_f,
            "n_both_closed": res.n_both_closed,
            # 0.93 (#42)
            "fan_heat_f": res.fan_heat_f,
            "fan_gated": res.fan_gated,
            "hw_basis": _BASIS.get(res.hw_basis or ""),
            "chw_basis": _BASIS.get(res.chw_basis or ""),
            "hw_median_delta_f": res.hw_median_delta_f,
            "chw_median_delta_f": res.chw_median_delta_f,
        }
        # 0.98 (#84): reported only when the matching option is set (defaults stay byte-identical)
        if res.cool_shift_f is not None:
            metrics["cool_shift_f"] = res.cool_shift_f
        if res.occupancy_gate is not None:
            metrics["occupancy_gate"] = res.occupancy_gate
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics=metrics,
            caveats=self._caveats(res),
            summary=(
                f"{equip}: with both valves shut, air shifts (median "
                f"{res.median_delta_f:+.1f}F beyond {res.fan_heat_f:g}F fan heat); HW-leak "
                f"{res.hw_leak_pct:.0f}% / CHW-leak {res.chw_leak_pct:.0f}% of closed hours "
                f"(worst: {which}){self._basis_txt(res)}"
            ),
        )

    @staticmethod
    def _basis_txt(res) -> str:
        own = [
            name
            for name, b in (("heating", res.hw_basis), ("cooling", res.chw_basis))
            if b and b != "SupplyAir"
        ]
        return f"; {' and '.join(own)} coil judged on its own leaving air" if own else ""

    def _caveats(self, res) -> list:
        out = []
        if not res.fan_gated:
            out.append(
                "no fan status or speed: every both-valves-closed sample is judged, including any "
                "with the fan off (no air across the coils)"
            )
        if res.hw_basis == "SupplyAir" or res.chw_basis == "SupplyAir":
            out.append(
                f"judged on supply air minus mixed air with a {res.fan_heat_f:g}F fan-heat "
                "allowance; a heat rise downstream of the coils (fan heat, supply-sensor "
                "placement) can read as a heating leak -- a coil leaving-air sensor, where "
                "trended, settles it"
            )
        # 0.98 (#84): notes for the opt-in options, only when they are set
        if not res.hw_judged:
            out.append(
                "heating coil not judged: it has no leaving-air sensor, and "
                "judge_heating_on_supply_air is off (the mapped supply air does not leave it)"
            )
        if res.cool_shift_f:
            out.append(
                f"cooling leak judged against a measured fan heat of {res.cool_shift_f:g}F (a "
                "rise below it by more than the margin is a leak); the value must come from the "
                "unit's own known-good hours, and a run it was measured on is not a fair test"
            )
        return out
