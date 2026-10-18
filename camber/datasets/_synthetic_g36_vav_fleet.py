"""Synthetic G36 VAV fleet: trim-and-respond resets and their zones (0.105, #144).

The ``g36_vav_fleet`` generator of the ``synthetic-g36-vav-fleet`` catalog entry: a VAV air
handler with six single-duct reheat boxes, hourly for three weeks of spring weekdays and weekends,
as eight air handlers that share the weather and differ in one way each. No open dataset shows a
fleet of zones with the air handler's reset-request counts, so the G36 zone-fleet rules (the
rogue-zone census, cohort starvation, reset effectiveness) and the reheat checks never fire on
open data.

The fleet is simulated, not drawn. Each hour every box asks for the airflow its zone's cooling
load needs at the current supply-air temperature, gets what the duct static lets through, and
the zone warms by whatever the box could not deliver. The boxes' Guideline 36 reset requests
(SAT: 3 at 5 °F over the cooling setpoint, 2 at 3 °F, 1 at a cooling loop over 95 %; duct static:
3 below half the box's active airflow setpoint with its damper over 95 % open, 2 below 70 %, 1 at
the damper alone) are summed, and the air handler's two trim-and-respond resets
(:func:`camber.g36_reset.tr_step` with ``SAT_TR`` and ``STATIC_TR``) take a cycle on the sum.

Version 3 (0.108, #226) makes the healthy air handler's zones comfortable, as a correct reset
should. Version 2 left them 3-5 °F warm and short of air for a quarter of the occupied hours:
each perimeter zone peaked alone (east in the morning, south at noon, west in the afternoon), a
reset that ignores two requests (G36's I = 2) never answered a lone zone until it was 5 °F warm,
and one cycle an hour could not follow the morning load. Now:

* the zones' loads rise and fall together (internal gains dominate, the solar peaks are small),
  so three or more zones ask at once when the supply air is too warm;
* the resets take three cycles per trended hour (a 20-minute step; see :data:`_CYCLES`), and the
  trend keeps the hour's last cycle: its requests, the setpoints it produced and the boxes;
* both resets sit at their start value (SAT 65 °F, duct static 0.5 in. w.c.) while the fan is off
  (G36 §5.1.14), instead of trimming overnight;
* a tier-1 request holds until the cooling loop or the damper falls below 85 % (G36's hysteresis).

The eight air handlers (``scenario``):

* ``healthy``: good trim-and-respond, a dual-maximum reheat sequence, no fault.
* ``rogue_sat``: zone 1 carries a large extra load (an equipment room) the box cannot meet at
  any supply temperature: it asks for colder air every occupied hour and drags the SAT reset to
  its floor for every zone.
* ``rogue_static``: zone 4's inlet is blocked: its damper is wide open and it gets about 40 % of
  its minimum airflow, so it asks for more duct static every hour and holds the static reset at
  its ceiling. The zone carries almost no load, so it stays comfortable. (Zone 5 until version
  3: the north zone asks little of the SAT reset, so blocking it leaves that reset's census
  unchanged.)
* ``starved``: the supply fan is capped (a drive held far below its speed): the duct static
  cannot rise past 0.06 in. w.c. whatever the setpoint, so every box's damper is wide open and
  every box is short of air at once. Both resets respond correctly; they cannot fix a fan.
* ``not_responding``: the trim-and-respond logic ignores its requests (the request input reads
  nothing): it only trims. The SAT setpoint sits at 65 °F, the static setpoint trims from 0.5 to
  0.1 in. w.c. within two hours of each morning's start, and the zones go short.
* ``reheat``: boxes 2 and 4 (west and north, the zones that need heat on cool mornings) run a
  single-maximum heating sequence: whenever they reheat, they hold their airflow at 80 % of
  their cooling maximum instead of at the minimum.
* ``heat_cool``: boxes 1 to 5 have their heating setpoint (74 °F) above their cooling setpoint
  (73 °F) while the fan runs, so the reheat valve and the cooling airflow fight all day.
* ``near_miss``: zone 1 carries a little extra load: it asks for colder air a little more often
  than the healthy fleet's zone 1, but not often enough to be called a rogue.

Every air handler runs weekdays 05:00-19:00 (the occupancy point reads occupied 06:00-18:00);
the zones set back to 62-80 °F with the fan off. Each box reports its airflow, its active airflow
setpoint (``ZN{n}_FLOW_SP``: what the controller asks the damper for this hour, between the
minimum and the cooling or heating maximum; 0 with the fan off) and its minimum airflow setpoint
(``ZN{n}_FLOW_MIN``), its damper and its reheat valve, its heating and cooling loop outputs
(``ZN{n}_HTG_LOOP`` and ``ZN{n}_CLG_LOOP``, 0-100 %, 0 with the fan off), as well as the space
temperature and both setpoints.

The loops are the signals the boxes are sequenced on (version 2, 0.106). The cooling loop is the
zone's cooling demand as a share of its cooling maximum: the box holds its minimum until the loop
asks for more, and the loop over 95 % is the tier-1 SAT request (held, since version 3, until the
loop falls below 85 %). The heating loop is the zone's
heating demand: in the dual-maximum sequence the reheat valve opens over its first half and the
airflow rises toward the heating maximum over its second; the single-maximum boxes of ``reheat``
follow the same loop with their airflow held high. The fighting boxes of ``heat_cool`` carry both
loops at once: a heating loop at half their valve (the dual-maximum valve range) and a cooling
loop that includes the reheat they add.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..g36_reset import SAT_TR, STATIC_TR, tr_step
from ._synthetic import Generator, _noise, _smooth, register

__all__: list = []

_START = "2025-04-28"  # a Monday
_DAYS = 21
_N_ZONES = 6
_FAN_HOURS = (5, 19)  # weekdays
_OCC_HOURS = (6, 18)  # weekdays
_COOL_SP, _HEAT_SP = 75.0, 70.0  # occupied zone setpoints, °F
_OAT_MEAN = 60.0  # °F, rising 6 °F over the three weeks
_OAT_SWING = 14.0  # °F, half the day-night range
_DESIGN_STATIC = 1.0  # in. w.c. at which a box's open damper passes 1.15 x its cooling maximum
_STARVED_STATIC = 0.06  # in. w.c.: the most a capped supply fan can make
_WARM_F = 12.0  # °F a zone warms per unit of unmet load (a fraction of its cooling maximum)
_NEAR_MISS_LOAD = 0.01  # the near-miss zone's extra load (a fraction of its cooling maximum)
#: version 3 (0.108, #226): trim-and-respond cycles per trended hour, a 20-minute time step. G36's
#: default is 2 minutes, but on an hourly trend a 2-minute reset's hour-to-hour moves no longer
#: follow the last cycle's requests: reset_effectiveness read 35-51 % of them as the wrong way
#: (measured at 30 cycles an hour), against 15-32 % at three. One cycle an hour (version 2) was
#: too slow to follow the morning load.
_CYCLES = 3
#: G36 request hysteresis: a tier-1 request (cooling loop over 95 %, damper over 95 %) holds until
#: the loop or the damper falls below this
_HYST_OFF = 85.0
#: the reheat a fighting box of ``heat_cool`` adds, as cooling load (a fraction of its maximum)
_FIGHT_LOAD = 0.03
#: the heating demand (a fraction of the cooling range) at which a box's heating loop is at 100 %
_HEAT_SPAN = 1.0
#: each zone's own slow load swing (a fraction of its cooling maximum)
_WOBBLE = 0.12
#: the box of ``rogue_static`` with the blocked inlet (0-based)
_ROGUE_STATIC_BOX = 3
#: hours over which the internal gains ramp in from 06:00
_RAMP_H = 2.0

# --- the faults the RCx cause-chain entry adds (``g36_rcx_chains``, 0.108) ---
#: ``warm_sat``: the coldest supply air the cooling coil can make (°F), its valve then wide open
_COIL_FLOOR = 63.0
#: ``low_static``: the most duct static the weak supply fan can make at full speed (in. w.c.)
_WEAK_FAN_STATIC = 0.35
#: ``cold_sat``: the fixed supply-air setpoint of a reset left disabled (°F)
_FIXED_SAT = 55.0
#: ``cold_sat``: the airflow its single-maximum boxes reheat at (a fraction of the cooling maximum)
_COLD_SAT_SINGLE_MAX = 0.6
#: ``one_bad_box``: the box (0-based) whose damper is stuck, and the position it is stuck at (%)
_STUCK_BOX, _STUCK_DAMPER = 2, 30.0

#: per zone (1..6): cooling maximum airflow (cfm), base load (fraction of the cooling range at
#: 55 °F supply air), weather sensitivity (per 20 °F above 62 °F), solar peak hour and size, and
#: internal gains (people, lights and plug loads from 08:00 to 17:00, ramping in from 06:00).
#: Version 3 (0.108, #226): the internal gains dominate and the solar peaks are small, so the
#: zones' loads rise and fall together; in version 2 each perimeter zone peaked alone, and a
#: correct reset ignoring two requests (G36's I = 2) left that zone 3-5 °F warm for hours.
_ZONES = (
    (1000.0, 0.10, 0.22, 9.0, 0.04, 0.42),  # 1: east
    (900.0, -0.07, 0.37, 16.0, 0.08, 0.42),  # 2: west
    (800.0, 0.10, 0.22, 12.5, 0.04, 0.42),  # 3: south
    (1100.0, -0.10, 0.30, 12.5, 0.03, 0.30),  # 4: north
    (700.0, 0.15, 0.13, 13.0, 0.0, 0.42),  # 5: interior
    (600.0, 0.15, 0.13, 13.0, 0.0, 0.42),  # 6: interior
)
_SCENARIOS = (
    "healthy",
    "rogue_sat",
    "rogue_static",
    "starved",
    "not_responding",
    "reheat",
    "heat_cool",
    "near_miss",
)


def _weather(seed: int, idx: pd.DatetimeIndex) -> np.ndarray:
    n = len(idx)
    hour = np.asarray(idx.hour, dtype=float)
    day = np.arange(n) / 24.0
    diurnal = _OAT_SWING * np.sin((hour - 9.0) / 24.0 * 2.0 * np.pi)  # warmest mid-afternoon
    swing = 6.0 * _smooth(_noise(n, seed, 1), 24 * 3)  # multi-day weather systems
    jitter = 1.2 * (_noise(n, seed, 2) - 0.5)
    return np.round(_OAT_MEAN + 6.0 * day / _DAYS + diurnal + swing + jitter, 1)


def _loads(seed: int, idx: pd.DatetimeIndex, oat: np.ndarray, a: int) -> np.ndarray:
    """``(n, zones)`` cooling load at 55 °F supply air, a fraction of each box's cooling range
    (negative: the zone needs heat). Air handler ``a`` gets its own small variations."""
    n = len(idx)
    hour = np.asarray(idx.hour, dtype=float)
    out = np.empty((n, _N_ZONES))
    for k, (_, base, env, peak, solar, gains) in enumerate(_ZONES):
        sun = solar * np.clip(np.cos((hour - peak) / 7.0 * np.pi), 0.0, None)
        wobble = _WOBBLE * _smooth(_noise(n, seed, 100 + 10 * a + k), 6)
        people = gains * np.clip((hour - 6.0) / _RAMP_H, 0.0, 1.0) * (hour < 17)
        out[:, k] = base + env * (oat - 62.0) / 20.0 + sun + people + wobble
    return out


@dataclass
class _Box:
    """One box's state in one trim-and-respond cycle."""

    temp: float
    flow: float
    damper: float
    valve: float
    want: float
    heat_loop: float
    cool_loop: float
    sat_req: int
    dsp_req: int


def _box(scenario, k, c, *, static, cool_sp, heat_sp, vmax, vmin, vheat, jit, fight, held):
    """Box ``k`` at cooling demand ``c`` (a fraction of its cooling maximum at the current supply
    air; negative: heating), the duct static it sees, and its held tier-1 requests ``held``
    (``[sat, static]``, updated in place: G36's hysteresis)."""
    reheat_box = scenario in ("reheat", "cold_sat") and k in (1, 3)
    fighting = scenario == "heat_cool" and k < 5
    valve = heat_loop = 0.0
    if fighting:
        c = c + _FIGHT_LOAD  # the reheat it adds is load the cooling airflow must remove
        valve = 35.0 + 25.0 * fight
        heat_loop = valve / 2.0  # the dual-maximum valve range is the loop's first half
    if c >= 0.0:
        want = min(max(c * vmax, vmin), vmax)
    else:
        h = min(-c / _HEAT_SPAN, 1.0)  # heating loop, 0-1
        heat_loop = 100.0 * h
        if reheat_box:
            want = (_COLD_SAT_SINGLE_MAX if scenario == "cold_sat" else 0.8) * vmax
            valve = 100.0 * min(1.0, h + 0.15)
        elif h <= 0.5:
            want = vmin
            valve = 100.0 * h / 0.5
        else:
            want = vmin + (h - 0.5) / 0.5 * (vheat - vmin)
            valve = 100.0
    avail = 1.15 * vmax * np.sqrt(static / _DESIGN_STATIC)
    if scenario == "rogue_static" and k == _ROGUE_STATIC_BOX:
        avail = 0.4 * vmin  # a blocked inlet
    stuck = scenario == "one_bad_box" and k == _STUCK_BOX
    if stuck:
        avail = avail * _STUCK_DAMPER / 100.0  # the damper cannot open past its stuck position
    flow = min(want, avail) * (1.0 + 0.03 * jit)
    damper = 100.0 if want >= avail else max(5.0, 100.0 * want / avail)
    if stuck:
        damper = _STUCK_DAMPER
    short = max(c * vmax - flow, 0.0) / vmax  # the load the air cannot carry
    if fighting:
        # held between the two setpoints; the heating loop lets go once the zone is warm
        t = cool_sp + 0.4 + min(_WARM_F * short, 9.0)
        if t >= heat_sp:
            valve = heat_loop = 0.0
    elif c >= 0.0:
        t = cool_sp - 1.2 + 1.0 * min(c, 1.0) + min(_WARM_F * short, 9.0)
    else:
        t = heat_sp + 1.2 - 0.8 * h
    t = t + 0.3 * jit
    cool_loop = 100.0 * min(max(c, 0.0), 1.0)
    # the box's G36 reset requests, with the tier-1 hysteresis (on over 95 %, off under 85 %)
    held[0] = cool_loop > 95.0 or (held[0] and cool_loop >= _HYST_OFF)
    held[1] = damper > 95.0 or (held[1] and damper >= _HYST_OFF)
    over = t - cool_sp
    sat_req = 3 if over >= 5.0 else 2 if over >= 3.0 else 1 if held[0] else 0
    ratio = flow / want
    if damper > 95.0:
        dsp_req = 3 if ratio < 0.5 else 2 if ratio < 0.7 else 1
    else:
        dsp_req = 1 if held[1] else 0
    return _Box(t, flow, damper, valve, want, heat_loop, cool_loop, sat_req, dsp_req)


def _fleet(
    seed: int, scenario: str, a: int, idx: pd.DatetimeIndex, oat: np.ndarray, *, rich=False
) -> dict:
    """One air handler and its six boxes: ``{column: array}``. Each fan-on hour runs
    :data:`_CYCLES` trim-and-respond cycles, the boxes answering the setpoints of each; the trend
    keeps the hour's last cycle (its requests, the setpoints it produced and the boxes). ``rich``
    adds the supply fan's speed (``SF_SPD``) and the cooling valve (``CLG_VLV``) for the RCx
    cause-chain entry."""
    n = len(idx)
    hour = np.asarray(idx.hour)
    weekday = np.asarray(idx.dayofweek) < 5
    fan = weekday & (hour >= _FAN_HOURS[0]) & (hour < _FAN_HOURS[1])
    occ = weekday & (hour >= _OCC_HOURS[0]) & (hour < _OCC_HOURS[1])
    load = _loads(seed, idx, oat, a)
    if scenario == "rogue_sat":
        load[:, 0] += 1.6  # an equipment room no box can keep up with
    if scenario == "near_miss":
        load[:, 0] += _NEAR_MISS_LOAD  # a sunnier, busier corner
    if scenario == "rogue_static":
        load[:, _ROGUE_STATIC_BOX] = 0.05 + 0.02 * _smooth(
            _noise(n, seed, 200 + a), 6
        )  # a lightly used room
    vmax = np.array([z[0] for z in _ZONES])
    vmin = np.round(0.3 * vmax, -1)
    vheat = np.round(0.5 * vmax, -1)
    cool_sp = np.full((n, _N_ZONES), _COOL_SP)
    heat_sp = np.full((n, _N_ZONES), _HEAT_SP)
    if scenario == "heat_cool":
        cool_sp[:, :5] = 73.0
        heat_sp[:, :5] = 74.0
    # setback with the fan off
    cool_sp[~fan] = 80.0
    heat_sp[~fan] = 62.0
    jit = [(_noise(n, seed, 300 + 10 * a + k) - 0.5) for k in range(_N_ZONES)]
    fight = [_smooth(_noise(n, seed, 400 + k), 4) for k in range(_N_ZONES)]
    cols = {f: np.zeros((n, _N_ZONES)) for f in ("T", "FLOW", "DMPR", "VLV", "SP", "HL", "CL")}
    sat_sp_out, dsp_sp_out = np.empty(n), np.empty(n)
    sat_req_out, dsp_req_out = np.zeros(n), np.zeros(n)
    dsp_out, sat_out = np.empty(n), np.empty(n)
    spd_out, vlv_out = np.zeros(n), np.zeros(n)
    sat_sp, dsp_sp = SAT_TR.sp0, STATIC_TR.sp0
    temp = np.full(_N_ZONES, 72.0)
    held = [[False, False] for _ in range(_N_ZONES)]
    for i in range(n):
        if not fan[i]:
            # off: the boxes close, the zones drift toward the weather, nobody asks for anything,
            # and both resets sit at their start value (G36 §5.1.14: SP0 while the fan is off)
            temp = 0.8 * temp + 0.2 * (69.0 + 0.2 * (oat[i] - 60.0))
            cols["T"][i] = temp
            sat_sp, dsp_sp = SAT_TR.sp0, STATIC_TR.sp0
            if scenario == "cold_sat":
                sat_sp = _FIXED_SAT  # a fixed setpoint, fan or no fan
            held = [[False, False] for _ in range(_N_ZONES)]
            sat_sp_out[i], dsp_sp_out[i] = sat_sp, dsp_sp
            dsp_out[i] = 0.0
            sat_out[i] = round(float(temp.mean()), 1)
            continue
        for _ in range(_CYCLES):
            static = min(dsp_sp, _STARVED_STATIC) if scenario == "starved" else dsp_sp
            if scenario == "low_static":
                static = min(dsp_sp, _WEAK_FAN_STATIC)
            # the supply air the boxes get: the coil of warm_sat cannot go below its floor
            sat = max(sat_sp, _COIL_FLOOR) if scenario == "warm_sat" else sat_sp
            boxes = [
                _box(
                    scenario,
                    k,
                    load[i, k] * 20.0 / (_COOL_SP - sat),  # warmer supply air needs more
                    static=static,
                    cool_sp=cool_sp[i, k],
                    heat_sp=heat_sp[i, k],
                    vmax=vmax[k],
                    vmin=vmin[k],
                    vheat=vheat[k],
                    jit=float(jit[k][i]),
                    fight=float(fight[k][i]),
                    held=held[k],
                )
                for k in range(_N_ZONES)
            ]
            sat_req = sum(b.sat_req for b in boxes)
            dsp_req = sum(b.dsp_req for b in boxes)
            if scenario == "not_responding":
                sat_sp = tr_step(sat_sp, 0, SAT_TR)  # the request input reads nothing
                dsp_sp = tr_step(dsp_sp, 0, STATIC_TR)
            elif scenario == "cold_sat":
                sat_sp = _FIXED_SAT  # the SAT reset is disabled
                dsp_sp = tr_step(dsp_sp, dsp_req, STATIC_TR)
            else:
                sat_sp = tr_step(sat_sp, sat_req, SAT_TR)
                dsp_sp = tr_step(dsp_sp, dsp_req, STATIC_TR)
        for k, b in enumerate(boxes):
            temp[k] = b.temp
            cols["T"][i, k], cols["FLOW"][i, k] = b.temp, b.flow
            cols["DMPR"][i, k], cols["VLV"][i, k] = b.damper, b.valve
            cols["SP"][i, k], cols["HL"][i, k] = b.want, b.heat_loop
            cols["CL"][i, k] = b.cool_loop
        sat_req_out[i], dsp_req_out[i] = sat_req, dsp_req
        sat_sp_out[i], dsp_sp_out[i] = sat_sp, dsp_sp
        dsp_out[i] = static
        sat_out[i] = max(sat_sp, _COIL_FLOOR) if scenario == "warm_sat" else sat_sp
        if rich:
            # a VFD that slows with the airflow and the static it holds; the weak fan of
            # low_static runs flat out once it cannot make its setpoint
            frac = sum(b.flow for b in boxes) / float(vmax.sum())
            spd = 30.0 + 60.0 * min(1.0, frac) * min(1.0, np.sqrt(static / _DESIGN_STATIC))
            if scenario == "low_static" and dsp_sp > _WEAK_FAN_STATIC:
                spd = 100.0
            spd_out[i] = spd
            # the cooling valve: open with the cooling the coil does on mostly-return mixed air,
            # wide open on warm_sat whenever the coil cannot reach the setpoint
            mat = 0.8 * 72.0 + 0.2 * oat[i]
            vlv = 100.0 * max(0.0, mat - sat) / max(mat - 48.0, 1.0)
            if scenario == "warm_sat" and sat_sp < _COIL_FLOOR:
                vlv = 100.0
            vlv_out[i] = min(vlv, 100.0)
    sj = _noise(n, seed, 500 + a) - 0.5
    pj = _noise(n, seed, 510 + a) - 0.5
    on = fan.astype(int)
    out = {
        "OAT": oat,
        "SF_STS": on,
        "OCC": occ.astype(int),
        "SAT": np.round(np.where(fan, sat_out + 0.4 * sj, sat_out), 1),
        "SAT_SP": np.round(sat_sp_out + 0.06 * sj, 2),
        "SAT_REQ": sat_req_out.astype(int),
        "DSP": np.round(np.where(fan, dsp_out + 0.02 * pj, 0.0), 2),
        "DSP_SP": np.round(dsp_sp_out, 2),
        "DSP_REQ": dsp_req_out.astype(int),
    }
    if rich:
        out["SF_SPD"] = np.round(spd_out, 1)
        out["CLG_VLV"] = np.round(vlv_out, 1)
    for k in range(_N_ZONES):
        z = f"ZN{k + 1}_"
        out[z + "T"] = np.round(cols["T"][:, k], 1)
        out[z + "CLG_SP"] = cool_sp[:, k]
        out[z + "HTG_SP"] = heat_sp[:, k]
        out[z + "FLOW"] = np.round(cols["FLOW"][:, k]).astype(int)
        out[z + "FLOW_SP"] = np.round(cols["SP"][:, k]).astype(int)
        out[z + "FLOW_MIN"] = np.full(n, int(vmin[k]))
        out[z + "DMPR"] = np.round(cols["DMPR"][:, k]).astype(int)
        out[z + "RHT_VLV"] = np.round(cols["VLV"][:, k]).astype(int)
        out[z + "HTG_LOOP"] = np.round(cols["HL"][:, k], 1)
        out[z + "CLG_LOOP"] = np.round(cols["CL"][:, k], 1)
    return out


def _g36_vav_fleet(seed: int) -> dict:
    """One CSV, hourly, the eight air handlers stacked with a ``scenario`` column; each row holds
    the air handler's points and its six boxes' (``ZN1_*`` .. ``ZN6_*``)."""
    idx = pd.date_range(_START, periods=_DAYS * 24, freq="1h")
    oat = _weather(seed, idx)
    frames = []
    for a, scenario in enumerate(_SCENARIOS):
        cols = _fleet(seed, scenario, a, idx, oat)
        frame = pd.DataFrame({"Timestamp": idx.strftime("%Y-%m-%d %H:%M"), "scenario": scenario})
        for name, values in cols.items():
            frame[name] = values
        frames.append(frame)
    text = pd.concat(frames, ignore_index=True).to_csv(index=False, lineterminator="\n")
    return {"g36_vav_fleet.csv": text}


register(
    "g36_vav_fleet",
    Generator(
        version=3,
        files=("g36_vav_fleet.csv",),
        build=_g36_vav_fleet,
        description=(
            "a VAV air handler with six reheat boxes under Guideline 36 trim-and-respond resets "
            "(SAT and duct-static setpoints and request counts; each box's space temperature and "
            "setpoints, airflow, active airflow setpoint and minimum, damper, reheat valve and "
            "heating and cooling loops), hourly for three weeks of spring, three reset cycles an "
            "hour, as eight air handlers: a healthy one, a rogue zone dragging the SAT reset, a "
            "rogue zone holding duct static up, a starved cohort behind a capped fan, a reset "
            "that ignores its requests, single-maximum reheat, overlapping heating and cooling "
            "setpoints, and a warm zone that is not quite a rogue"
        ),
    ),
)
