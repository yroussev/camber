"""Rules: heat-pump **operating mode**, mode against need, capacity shortfall and same-room
imbalance -- for units trended with only discharge air, fan and zone temperature (0.93; #40).

A water-to-air (water-source or ground-source) heat pump in a school or office is often trended
with just three points: discharge-air temperature, fan status and the zone temperature. There is
no compressor status and no reversing-valve command, so :class:`~camber.rules.heatpump_rule.
HeatPumpDefrost` and the refrigerant-side rules have nothing to read. The operating mode is still
visible: heating air leaves well above the room, cooling air well below it
(:func:`infer_hp_mode`). From the mode and the zone temperature follow three checks:

* :class:`HPModeVsNeed` (``hp_mode_vs_need``) -- the unit **cools a room that is already below its
  heating setpoint**, or heats one above its cooling setpoint (a reversed or stuck changeover, a
  misplaced sensor, a thermostat fighting a neighbour);
* :class:`HPCapacityShortfall` (``hp_capacity_shortfall``) -- the room sits below its heating band
  (or above its cooling band) **while the unit is already heating (cooling)**: a capacity or
  airflow problem, not a control one. The same cold room with the unit idle is reported as a
  control problem instead;
* :class:`HPRoomImbalance` (``hp_room_imbalance``, fleet) -- **two or more units serving one
  room** that fight (one heats while another cools) or split the work very unevenly.

The heat-pump counterpart of the terminal-box "zone below setpoint with reheat saturated" check
lives here; that one is for VAV boxes only. Setpoints are the mapped ``heat_sp`` / ``cool_sp``
when present, else a stated default comfort band (68-76 degF) with a caveat. Occupied hours come
from the ``occupancy`` role, else the weekday daytime default (:func:`camber.resolve.occupied`).
Thresholds are screening-grade (provisional, 0.93).

**Defrost is heating (0.106, #185).** With the mode read from the compressor and the reversing
valve, an air-source unit's defrost reverses the valve with the compressor running, which read as
cooling: a unit that only defrosted was "cooling a room already below its heating setpoint". A
cooling run no longer than ``max_defrost_min`` with heating on both sides is now a defrost and
counts as heating in all three rules. ``heatpump_defrost`` judges how often a unit defrosts.

**Units that fight (0.106, #185).** Each unit of a pair fighting in one room sees its own sensor
out of band while it works, which ``hp_capacity_shortfall`` alone reads as a capacity shortfall.
When ``hp_room_imbalance`` runs in the same config and finds the room's units fighting, the
capacity finding of each unit in that room names the fight as the cause instead
(:func:`_defer_capacity_to_room_fight`).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..model.roles import Role
from ..resolve import occupied
from .base import Finding

__all__ = [
    "HEAT_RISE_F",
    "COOL_DROP_F",
    "DEFAULT_HEAT_SP_F",
    "DEFAULT_COOL_SP_F",
    "BAND_TOL_F",
    "MAX_DEFROST_MIN",
    "infer_hp_mode",
    "HPModeVsNeed",
    "HPCapacityShortfall",
    "HPRoomImbalance",
]

#: Discharge air this far above the zone reads as heating (a water-to-air heat pump delivers
#: ~85-105 degF air in heating against a ~70 degF room).
HEAT_RISE_F = 15.0
#: Discharge air this far below the zone reads as cooling (~52-60 degF air in cooling).
COOL_DROP_F = 10.0
DEFAULT_HEAT_SP_F = 68.0
DEFAULT_COOL_SP_F = 76.0
BAND_TOL_F = 1.0  # the zone must be this far past a setpoint to count as outside the band
#: A cooling run (mode from the compressor and the valve) no longer than this, with heating on both
#: sides, is a defrost (0.106, #185). Defrosts end on a coil temperature or a 10-14 minute timer.
MAX_DEFROST_MIN = 15.0
#: the equipment of the one room-imbalance finding over every shared room (the fleet rules' id)
_FLEET = "<fleet>"


def infer_hp_mode(
    frame: pd.DataFrame, *, heat_rise_f: float = HEAT_RISE_F, cool_drop_f: float = COOL_DROP_F
) -> pd.Series:
    """The operating mode per sample: ``1`` heating, ``-1`` cooling, ``0`` neither, NaN unknown.

    With a compressor status and a reversing-valve command mapped the mode is read from them
    (compressor off -> 0; valve 1 -> heating, 0 -> cooling). Otherwise it is inferred from the
    discharge air against the zone: at least ``heat_rise_f`` above -> heating, at least
    ``cool_drop_f`` below -> cooling. A mapped fan status that reads off forces 0 (no air, no
    delivered mode). NaN where the inputs are missing.
    """
    idx = frame.index
    if Role.COMPRESSOR_STATUS in frame.columns and Role.REVERSING_VALVE_CMD in frame.columns:
        comp = pd.to_numeric(frame[Role.COMPRESSOR_STATUS], errors="coerce")
        rv = pd.to_numeric(frame[Role.REVERSING_VALVE_CMD], errors="coerce")
        mode = pd.Series(np.where(rv > 0.5, 1.0, -1.0), index=idx)
        mode = mode.where(comp > 0.5, 0.0).where(comp.notna() & rv.notna())
    elif Role.SUPPLY_AIR_TEMP in frame.columns and Role.SPACE_TEMP in frame.columns:
        delta = pd.to_numeric(frame[Role.SUPPLY_AIR_TEMP], errors="coerce") - pd.to_numeric(
            frame[Role.SPACE_TEMP], errors="coerce"
        )
        mode = pd.Series(
            np.where(delta >= heat_rise_f, 1.0, np.where(delta <= -cool_drop_f, -1.0, 0.0)),
            index=idx,
        ).where(delta.notna())
    else:
        return pd.Series(np.nan, index=idx)
    if Role.SUPPLY_FAN_STATUS in frame.columns:
        fan = pd.to_numeric(frame[Role.SUPPLY_FAN_STATUS], errors="coerce")
        mode = mode.where(~(fan < 0.5), 0.0)
    return mode


def _status_mode(frame: pd.DataFrame) -> bool:
    """Whether :func:`infer_hp_mode` reads the mode from the compressor and the valve."""
    return Role.COMPRESSOR_STATUS in frame.columns and Role.REVERSING_VALVE_CMD in frame.columns


def _defrost_mask(frame: pd.DataFrame, mode: pd.Series, max_defrost_min: float) -> pd.Series:
    """The samples of ``mode`` that are a defrost, not cooling (0.106, #185).

    Only with the mode read from the compressor and the reversing valve (a discharge-air mode
    does not see the valve): a run of cooling samples lasting at most ``max_defrost_min`` with a
    heating sample on each side. A run at the trend's start or end, or next to an off or unknown
    sample, stays cooling.
    """
    out = np.zeros(len(mode), dtype=bool)
    if not _status_mode(frame) or max_defrost_min <= 0 or len(mode) < 3:
        return pd.Series(out, index=mode.index)
    step_min = _step_hours(frame.index) * 60.0
    m = mode.to_numpy(dtype=float)
    cool = m == -1.0
    edges = np.flatnonzero(np.diff(np.concatenate(([0], cool.astype(np.int8), [0]))))
    for start, stop in zip(edges[::2], edges[1::2]):  # each cooling run is m[start:stop]
        if start == 0 or stop >= len(m):
            continue
        if (stop - start) * step_min <= max_defrost_min + 1e-9 and m[start - 1] == m[stop] == 1.0:
            out[start:stop] = True
    return pd.Series(out, index=mode.index)


def _band(frame: pd.DataFrame, heat_sp_f: float, cool_sp_f: float, caveats: list):
    """(heating setpoint, cooling setpoint) series -- mapped, else the stated defaults."""
    idx = frame.index
    assumed = []
    if Role.HEAT_SP in frame.columns:
        hsp = pd.to_numeric(frame[Role.HEAT_SP], errors="coerce")
    else:
        hsp = pd.Series(heat_sp_f, index=idx)
        assumed.append(f"heating {heat_sp_f:g}°F")
    if Role.COOL_SP in frame.columns:
        csp = pd.to_numeric(frame[Role.COOL_SP], errors="coerce")
    else:
        csp = pd.Series(cool_sp_f, index=idx)
        assumed.append(f"cooling {cool_sp_f:g}°F")
    if assumed:
        caveats.append(
            "no zone setpoint mapped: judged against an assumed comfort band ("
            + ", ".join(assumed)
            + "); set heat_sp_f / cool_sp_f to the site's"
        )
    return hsp, csp


def _step_hours(index) -> float:
    if len(index) < 2:
        return 1.0
    step = pd.Series(index).diff().median()
    return float(step / pd.Timedelta(hours=1)) if pd.notna(step) else 1.0


def _occupied(frame: pd.DataFrame, caveats: list) -> pd.Series:
    if Role.OCCUPANCY not in frame.columns:
        caveats.append("no occupancy point: occupied hours are the weekday 07:00-18:00 default")
    return pd.Series(occupied(frame), index=frame.index).astype(bool)


class _HPBase:
    _flag_label = "flagged"
    roles_required = (Role.SUPPLY_AIR_TEMP, Role.SPACE_TEMP)
    roles_optional = (
        Role.SUPPLY_FAN_STATUS,
        Role.HEAT_SP,
        Role.COOL_SP,
        Role.OCCUPANCY,
        Role.COMPRESSOR_STATUS,
        Role.REVERSING_VALVE_CMD,
        # read only to tell a hydronic coil unit from a heat pump (``_coil_unit``)
        Role.HEAT_VALVE,
        Role.COOL_VALVE,
    )

    def __init__(
        self,
        *,
        heat_sp_f: float = DEFAULT_HEAT_SP_F,
        cool_sp_f: float = DEFAULT_COOL_SP_F,
        band_tol_f: float = BAND_TOL_F,
        heat_rise_f: float = HEAT_RISE_F,
        cool_drop_f: float = COOL_DROP_F,
        max_defrost_min: float = MAX_DEFROST_MIN,
    ):
        self.heat_sp_f = heat_sp_f
        self.cool_sp_f = cool_sp_f
        self.band_tol_f = band_tol_f
        self.heat_rise_f = heat_rise_f
        self.cool_drop_f = cool_drop_f
        self.max_defrost_min = max_defrost_min  # 0.106 (#185)

    def _mode(self, frame: pd.DataFrame) -> tuple:
        """``(mode, defrost)``: the operating mode with defrosts counted as heating (#185)."""
        mode = infer_hp_mode(frame, heat_rise_f=self.heat_rise_f, cool_drop_f=self.cool_drop_f)
        defrost = _defrost_mask(frame, mode, self.max_defrost_min)
        return mode.where(~defrost, 1.0), defrost

    def _prep(self, frame: pd.DataFrame, caveats: list):
        mode, defrost = self._mode(frame)
        zat = pd.to_numeric(frame[Role.SPACE_TEMP], errors="coerce")
        hsp, csp = _band(frame, self.heat_sp_f, self.cool_sp_f, caveats)
        occ = _occupied(frame, caveats)
        valid = occ & mode.notna() & zat.notna()
        cold = zat < hsp - self.band_tol_f
        hot = zat > csp + self.band_tol_f
        return mode, defrost, cold, hot, valid

    def _coil_unit(self, frame: pd.DataFrame) -> str | None:
        """Why ``frame`` reads as a coil unit rather than a heat pump, or ``None``.

        0.93 integration: a mapped heating or cooling *valve* with no compressor status and no
        reversing-valve command is a hydronic coil -- a terminal box's reheat, a fan coil, an air
        handler -- not a heat pump. On equipment with no recorded class the class gate cannot
        tell, and a VAV box out of reheat would otherwise be reported twice, here and by
        ``reheat_capacity_shortfall``. A heat pump with a supplemental heating valve still runs
        when a compressor or reversing-valve signal is mapped.
        """
        if Role.COMPRESSOR_STATUS in frame.columns or Role.REVERSING_VALVE_CMD in frame.columns:
            return None
        valves = [r for r in (Role.HEAT_VALVE, Role.COOL_VALVE) if r in frame.columns]
        if not valves:
            return None
        names = " / ".join(r.value for r in valves)
        return (
            f"{names} mapped with no compressor or reversing-valve signal: a hydronic coil unit "
            "(terminal box, fan coil, air handler), not a heat pump -- reheat_capacity_shortfall "
            "judges a box's reheat"
        )

    def _flagged(self, frame: pd.DataFrame, mode, cold, hot, valid) -> pd.Series | None:
        """The samples the finding counts as flagged (#298); ``None`` when the rule flags none."""
        return None

    #: what :meth:`_judged`'s samples are (0.111, #310)
    _judged_basis = "occupied samples"

    def _judged(self, mode, valid) -> pd.Series:
        """The samples the finding's share is of (0.111, #310): the occupied samples with a mode
        and a zone temperature, unless a rule says otherwise."""
        return valid

    def evidence(self, equip: str, frame: pd.DataFrame):
        """Pattern J (#298): the discharge air and the zone, with the occupied samples the
        finding counts shaded (see each rule's ``_flagged``)."""
        from ..charts.evidence import Evidence

        if self._coil_unit(frame) or any(r not in frame.columns for r in self.roles_required):
            return None
        mode, _defrost, cold, hot, valid = self._prep(frame, [])
        if int(valid.sum()) == 0:
            return None
        mask = self._flagged(frame, mode, cold, hot, valid)
        if mask is None:
            return None
        roles = [Role.SUPPLY_AIR_TEMP, Role.SPACE_TEMP] + [
            r for r in (Role.HEAT_SP, Role.COOL_SP) if r in frame.columns
        ]
        return Evidence(
            renderer="multitrend",
            roles=roles,
            mask=mask,
            judged=self._judged(mode, valid),
            judged_basis=self._judged_basis,
            label=self._flag_label,
            title=f"{equip}: {self.name}",  # type: ignore[attr-defined]
        )

    def _declined(self, equip, why) -> Finding:
        return Finding(
            rule=self.name,  # type: ignore[attr-defined]
            equip=equip,
            severity="info",
            metrics={"declined": True, "reason": why},
            summary=f"{equip}: declined -- {why}",
            caveats=[f"could not evaluate: {why}"],
        )


class HPModeVsNeed(_HPBase):
    """Cooling a room below its heating band, or heating one above its cooling band."""

    name = "hp_mode_vs_need"

    def __init__(
        self,
        *,
        warn_hours: float = 2.0,
        fault_hours: float = 10.0,
        warn_pct: float = 2.0,
        fault_pct: float = 10.0,
        **kw,
    ):
        super().__init__(**kw)
        self.warn_hours = warn_hours
        self.fault_hours = fault_hours
        self.warn_pct = warn_pct
        self.fault_pct = fault_pct

    _flag_label = "mode against need"

    @staticmethod
    def _wrong(mode, cold, hot, valid) -> tuple:
        """``(cooling a cold room, heating a warm room)`` sample masks."""
        return valid & (mode == -1) & cold, valid & (mode == 1) & hot

    def _flagged(self, frame, mode, cold, hot, valid):
        """The occupied samples in the mode the room does not need."""
        cool_cold, heat_hot = self._wrong(mode, cold, hot, valid)
        return cool_cold | heat_hot

    _judged_basis = "occupied running samples"

    def _judged(self, mode, valid):
        """The occupied running samples, ``wrong_mode_pct_of_running``'s denominator."""
        return valid & (mode != 0)

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Hours (and share of occupied running time) spent in the mode the room does not need."""
        coil = self._coil_unit(frame)
        if coil:
            return self._declined(equip, coil)
        caveats: list = []
        mode, defrost, cold, hot, valid = self._prep(frame, caveats)
        if int(valid.sum()) == 0:
            return self._declined(equip, "no occupied samples with a mode and a zone temperature")
        h = _step_hours(frame.index)
        running = valid & (mode != 0)
        cool_cold, heat_hot = self._wrong(mode, cold, hot, valid)
        wrong = cool_cold | heat_hot
        n_run = int(running.sum())
        wrong_h = float(wrong.sum()) * h
        pct = 100.0 * float(wrong.sum()) / n_run if n_run else 0.0
        if wrong_h >= self.fault_hours and pct >= self.fault_pct:
            severity = "fault"
        elif wrong_h >= self.warn_hours and pct >= self.warn_pct:
            severity = "warn"
        else:
            severity = "ok"
        metrics = {
            "cooling_while_cold_hours": round(float(cool_cold.sum()) * h, 2),
            "heating_while_warm_hours": round(float(heat_hot.sum()) * h, 2),
            "wrong_mode_pct_of_running": round(pct, 2),
            "occupied_running_hours": round(n_run * h, 2),
            "mode_source": "status" if _status_mode(frame) else "discharge_vs_zone",
        }
        if _status_mode(frame):  # 0.106 (#185): defrost samples, counted as heating
            metrics["defrost_hours"] = round(float((valid & defrost).sum()) * h, 2)
        if severity == "ok":
            summary = f"{equip}: the mode matches the room's need ({wrong_h:.1f} h against it)"
        else:
            which = (
                "cooling a room already below its heating setpoint"
                if cool_cold.sum() >= heat_hot.sum()
                else "heating a room already above its cooling setpoint"
            )
            summary = (
                f"{equip}: {wrong_h:.1f} occupied hours ({pct:.1f}% of its running time) "
                f"{which} -- check the changeover, the thermostat location and the setpoints"
            )
        return Finding(self.name, equip, severity, metrics, summary, caveats=caveats)


class HPCapacityShortfall(_HPBase):
    """A room outside its band while its unit already runs in the right mode (capacity/airflow).

    Of the occupied samples with the room below its heating band, the share with the unit
    heating: high -> a **capacity** shortfall (undersized unit, low airflow, a weak source loop,
    a refrigerant fault); low -> the unit is idle while the room is cold, a **control** problem.
    The cooling side is judged the same way. The worse side sets the finding.
    """

    name = "hp_capacity_shortfall"

    def __init__(
        self,
        *,
        warn_outside_pct: float = 10.0,
        fault_outside_pct: float = 25.0,
        saturated_pct: float = 70.0,
        idle_pct: float = 30.0,
        min_hours: float = 4.0,
        **kw,
    ):
        super().__init__(**kw)
        self.warn_outside_pct = warn_outside_pct
        self.fault_outside_pct = fault_outside_pct
        self.saturated_pct = saturated_pct
        self.idle_pct = idle_pct
        self.min_hours = min_hours

    def _side(self, mode, outside, valid, want: int, h: float) -> dict:
        n_valid = int(valid.sum())
        out = valid & outside
        n_out = int(out.sum())
        working = int((out & (mode == want)).sum())
        opposite = int((out & (mode == -want)).sum())  # 0.106 (#185)
        return {
            "outside_pct": 100.0 * n_out / n_valid if n_valid else 0.0,
            "outside_hours": n_out * h,
            "working_pct": 100.0 * working / n_out if n_out else None,
            "opposite_pct": 100.0 * opposite / n_out if n_out else None,
        }

    def _judge(self, mode, cold, hot, valid, h: float) -> tuple:
        """``(sides, severity, verdict, side)``: each side's shares and the worse side's verdict."""
        sides = {"heating": self._side(mode, cold, valid, 1, h)}
        sides["cooling"] = self._side(mode, hot, valid, -1, h)
        rank = {"ok": 0, "warn": 1, "fault": 2}
        severity, verdict, side_name = "ok", "ok", None
        for name, s in sides.items():
            if s["outside_hours"] < self.min_hours or s["outside_pct"] < self.warn_outside_pct:
                continue
            sev = "fault" if s["outside_pct"] >= self.fault_outside_pct else "warn"
            wp = s["working_pct"]
            if wp is not None and wp >= self.saturated_pct:
                v = "capacity"
            elif wp is not None and wp <= self.idle_pct:
                v = "control"
            else:
                v = "mixed"
                sev = "warn"
            if rank[sev] > rank[severity]:
                severity, verdict, side_name = sev, v, name
        return sides, severity, verdict, side_name

    _flag_label = "outside the band"

    def _flagged(self, frame, mode, cold, hot, valid):
        """The occupied samples outside the band on the finding's side (the worse one)."""
        h = _step_hours(frame.index)
        side = self._judge(mode, cold, hot, valid, h)[3]
        if side is None:
            return None
        return valid & (cold if side == "heating" else hot)

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Share of occupied time outside the band, and how often the unit was working then."""
        coil = self._coil_unit(frame)
        if coil:
            return self._declined(equip, coil)
        caveats: list = []
        mode, _defrost, cold, hot, valid = self._prep(frame, caveats)
        if int(valid.sum()) == 0:
            return self._declined(equip, "no occupied samples with a mode and a zone temperature")
        h = _step_hours(frame.index)
        sides, severity, verdict, side_name = self._judge(mode, cold, hot, valid, h)
        metrics = {
            f"{n}_{k}": (None if v is None else round(v, 2))
            for n, s in sides.items()
            for k, v in s.items()
        }
        metrics["verdict"] = verdict
        metrics["side"] = side_name
        if severity == "ok":
            summary = f"{equip}: the room stays in its band when the unit runs"
        else:
            s = sides[side_name]
            band = "below its heating band" if side_name == "heating" else "above its cooling band"
            what = {
                "capacity": f"while the unit was already {side_name} -- a capacity or airflow "
                "shortfall (unit size, airflow, source-loop temperature, refrigerant)",
                "control": "while the unit was mostly idle -- a control problem (thermostat, "
                "setpoint, schedule), not capacity",
                "mixed": "with the unit sometimes working, sometimes idle",
            }[verdict]
            # 0.106 (#185): a unit running the other way is not idle; say how it spent the time
            opp = s["opposite_pct"] or 0.0
            if verdict == "control" and opp > 0:
                other = "cooling" if side_name == "heating" else "heating"
                wp = s["working_pct"] or 0.0
                what = (
                    f"while the unit was {side_name} only {wp:.0f}% of that time, {other} "
                    f"{opp:.0f}% and idle {100.0 - wp - opp:.0f}% -- a control problem (a stuck "
                    "reversing valve or changeover, the thermostat, the setpoints), not capacity"
                )
            summary = (
                f"{equip}: {s['outside_pct']:.0f}% of occupied time {band} "
                f"({s['outside_hours']:.0f} h) {what}"
            )
        return Finding(self.name, equip, severity, metrics, summary, caveats=caveats)


class HPRoomImbalance(_HPBase):
    """Units serving the same room that fight each other or share the work very unevenly (fleet).

    Rooms come from ``rooms`` (``{room: [equip, ...]}``) or, failing that, from the topology: units
    whose single served-by parent is the same are one room. Per room with two or more units:
    ``fight_pct`` (co-occupied samples with one unit heating while another cools), the spread of
    the units' active (heating or cooling) shares, and the median spread between their zone
    sensors (one room, so the sensors should agree).
    """

    name = "hp_room_imbalance"

    def __init__(
        self,
        *,
        rooms: dict | None = None,
        warn_fight_pct: float = 2.0,
        fault_fight_pct: float = 10.0,
        warn_duty_spread_pct: float = 40.0,
        warn_sensor_spread_f: float = 3.0,
        **kw,
    ):
        super().__init__(**kw)
        self.rooms = rooms
        self.warn_fight_pct = warn_fight_pct
        self.fault_fight_pct = fault_fight_pct
        self.warn_duty_spread_pct = warn_duty_spread_pct
        self.warn_sensor_spread_f = warn_sensor_spread_f

    def _groups(self, frames: dict, topology) -> dict:
        if self.rooms:
            return {r: [e for e in eqs if e in frames] for r, eqs in self.rooms.items()}
        groups: dict = {}
        if topology is not None:
            for e in frames:
                parents = topology.parents_of(e)
                if len(parents) == 1:
                    groups.setdefault(parents[0], []).append(e)
        return groups

    def analyze_fleet(self, frames: dict, *, topology=None) -> Finding:
        """One aggregate finding over every room with two or more units."""
        groups = {r: eqs for r, eqs in self._groups(frames, topology).items() if len(eqs) >= 2}
        if not groups:
            return Finding(
                rule=self.name,
                equip=_FLEET,
                severity="info",
                metrics={"declined": True, "reason": "no_rooms", "n_rooms": 0},
                summary="fleet: declined -- no room served by two or more heat pumps is declared",
                caveats=[
                    "could not evaluate same-room imbalance: declare the rooms (the rule's "
                    "rooms={room: [units]}) or a topology whose parent of each unit is its room"
                ],
            )
        caveats: list = []
        per_room: dict = {}
        rank = {"ok": 0, "warn": 1, "fault": 2}
        worst, worst_room = "ok", None
        for room, eqs in sorted(groups.items()):
            modes, zats, occs = {}, {}, {}
            for e in eqs:
                fr = frames[e]
                tmp: list = []
                modes[e] = self._mode(fr)[0]  # 0.106 (#185): defrosts count as heating
                zats[e] = pd.to_numeric(fr[Role.SPACE_TEMP], errors="coerce")
                occs[e] = _occupied(fr, tmp)
                for c in tmp:
                    if c not in caveats:
                        caveats.append(c)
            m = pd.DataFrame(modes)
            occ = pd.DataFrame(occs).reindex(m.index).fillna(False).all(axis=1)
            both = occ & m.notna().all(axis=1)
            n = int(both.sum())
            if n == 0:
                per_room[room] = {"units": eqs, "n_samples": 0}
                continue
            mm = m[both]
            fight = ((mm == 1).any(axis=1) & (mm == -1).any(axis=1)).mean() * 100.0
            duty = {e: float((mm[e] != 0).mean() * 100.0) for e in eqs}
            spread = max(duty.values()) - min(duty.values())
            z = pd.DataFrame(zats).reindex(m.index)[both]
            zs = float((z.max(axis=1) - z.min(axis=1)).median())
            sev = "ok"
            if fight >= self.fault_fight_pct:
                sev = "fault"
            elif (
                fight >= self.warn_fight_pct
                or spread >= self.warn_duty_spread_pct
                or zs >= self.warn_sensor_spread_f
            ):
                sev = "warn"
            per_room[room] = {
                "units": eqs,
                "n_samples": n,
                "fight_pct": round(float(fight), 2),
                "fights": bool(fight >= self.warn_fight_pct),  # 0.106 (#185)
                "active_pct": {e: round(v, 1) for e, v in duty.items()},
                "active_spread_pct": round(spread, 1),
                "zone_sensor_spread_f": round(zs, 2),
                "severity": sev,
            }
            if rank[sev] > rank[worst]:
                worst, worst_room = sev, room
        flagged = [r for r, v in per_room.items() if v.get("severity") in ("warn", "fault")]
        metrics = {"n_rooms": len(per_room), "n_flagged": len(flagged), "rooms": per_room}
        if worst == "ok":
            summary = f"fleet: {len(per_room)} shared room(s); the units in each work together"
        else:
            r = per_room[worst_room]
            summary = (
                f"fleet: {len(flagged)} of {len(per_room)} shared room(s) imbalanced; worst "
                f"{worst_room}: units fight {r['fight_pct']:.1f}% of the time, active shares "
                f"differ by {r['active_spread_pct']:.0f} points, zone sensors by "
                f"{r['zone_sensor_spread_f']:.1f}°F"
            )
        return Finding(self.name, _FLEET, worst, metrics, summary, caveats=caveats)


#: The capacity verdicts that two units fighting in one room produce on their own (0.106, #185).
_FIGHT_VERDICTS = ("capacity", "mixed")


def _defer_capacity_to_room_fight(findings: list) -> int:
    """Defer the cause of a fighting room's capacity findings to ``hp_room_imbalance`` (#185).

    Each unit of a fighting pair sees its own sensor out of band while it works, which
    ``hp_capacity_shortfall`` reads as a capacity shortfall: the cause is the fight (often a
    sensor in the wrong place), which only the fleet rule can see. For every room the
    ``hp_room_imbalance`` finding in ``findings`` marks as fighting, a ``warn`` or ``fault``
    capacity finding of a unit in that room with a ``capacity`` or ``mixed`` verdict gets
    ``verdict`` ``room_fight`` (the former one kept as ``verdict_alone``), ``deferred_to`` and
    ``room``, and a summary and caveat that name the fight. Its severity stands: the room is out
    of band, so the unit still has a problem, just not a capacity one. A ``control`` verdict
    stands too. Edits ``findings`` in place; returns how many were deferred.
    """
    rooms: dict = {}
    for f in findings:
        if f.rule == HPRoomImbalance.name and f.equip in (_FLEET, "fleet"):
            rooms = (f.metrics or {}).get("rooms") or {}
    fighting = {
        unit: (room, r) for room, r in rooms.items() if r.get("fights") for unit in r["units"]
    }
    n = 0
    for f in findings:
        if f.rule != HPCapacityShortfall.name or f.equip not in fighting:
            continue
        m = f.metrics or {}
        if f.severity not in ("warn", "fault") or m.get("verdict") not in _FIGHT_VERDICTS:
            continue
        room, r = fighting[f.equip]
        f.metrics = {
            **m,
            "verdict": "room_fight",
            "verdict_alone": m.get("verdict"),
            "deferred_to": HPRoomImbalance.name,
            "room": room,
        }
        side = m.get("side")
        band = "below its heating band" if side == "heating" else "above its cooling band"
        f.summary = (
            f"{f.equip}: {m.get(f'{side}_outside_pct', 0.0):.0f}% of occupied time {band} while "
            f"it works, but the units in {room} fight ({r['fight_pct']:.1f}% of the co-occupied "
            f"time; see {HPRoomImbalance.name}) -- the fight, not capacity: check where each "
            "unit's zone sensor sits"
        )
        f.caveats = [
            *(f.caveats or []),
            f"cause deferred to {HPRoomImbalance.name}: another unit in {room} runs the other way "
            "while this one works, so this unit alone would read as short of capacity "
            f"(verdict_alone {m.get('verdict')}); do not size the unit up from this finding",
        ]
        n += 1
    return n
