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
(:func:`camber.g36_reset.tr_step` with ``SAT_TR`` and ``STATIC_TR``) take one cycle per hour on
the sum. The next hour's boxes see the new setpoints. So a healthy reset is exactly the T&R
response to its zones' own requests, one cycle per trended sample.

The eight air handlers (``scenario``):

* ``healthy``: good trim-and-respond, a dual-maximum reheat sequence, no fault.
* ``rogue_sat``: zone 1 carries a large extra load (an equipment room) the box cannot meet at
  any supply temperature: it asks for colder air every occupied hour and drags the SAT reset to
  its floor for every zone.
* ``rogue_static``: zone 5's inlet is blocked: its damper is wide open and it gets about 40 % of
  its minimum airflow, so it asks for more duct static every hour and holds the static reset at
  its ceiling. The zone carries almost no load, so it stays comfortable.
* ``starved``: the supply fan is capped (a drive held far below its speed): the duct static
  cannot rise past 0.06 in. w.c. whatever the setpoint, so every box's damper is wide open and
  every box is short of air at once. Both resets respond correctly; they cannot fix a fan.
* ``not_responding``: the trim-and-respond logic ignores its requests (the request input reads
  nothing): it only trims. The SAT setpoint sits at 65 °F from the first hour, the static setpoint
  trims from 0.5 to 0.1 in. w.c. on the first morning and stays there, and the zones go short.
* ``reheat``: boxes 2 and 4 (west and north, the zones that need heat on cool mornings) run a
  single-maximum heating sequence: whenever they reheat, they hold their airflow at 80 % of
  their cooling maximum instead of at the minimum.
* ``heat_cool``: boxes 1 to 5 have their heating setpoint (74 °F) above their cooling setpoint
  (73 °F) while the fan runs, so the reheat valve and the cooling airflow fight all day.
* ``near_miss``: zone 1 carries a little extra load: it asks for colder air a little more often
  than the healthy fleet's zone 1, but not often enough to be called a rogue.

Every air handler runs weekdays 05:00-19:00 (the occupancy point reads occupied 06:00-18:00);
the zones set back to 62-80 °F with the fan off. Each box reports its airflow, its minimum airflow
setpoint (``ZN{n}_FLOW_MIN``), its damper and its reheat valve, as well as the space temperature
and both setpoints. The resets keep trimming while the fan is off (no requests), so each morning
starts at the trim end.
"""

from __future__ import annotations

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
_NEAR_MISS_LOAD = 0.06  # the near-miss zone's extra load (a fraction of its cooling maximum)

#: per zone (1..6): cooling maximum airflow (cfm), base load (fraction of the cooling range at
#: 55 °F supply air), weather sensitivity (per 20 °F above 62 °F), solar peak hour and size
_ZONES = (
    (1000.0, 0.15, 0.55, 9.0, 0.35),  # 1: east
    (900.0, 0.05, 0.55, 16.0, 0.40),  # 2: west
    (800.0, 0.20, 0.45, 12.5, 0.25),  # 3: south
    (1100.0, 0.05, 0.40, 12.5, 0.05),  # 4: north
    (700.0, 0.40, 0.08, 13.0, 0.0),  # 5: interior
    (600.0, 0.40, 0.08, 13.0, 0.0),  # 6: interior
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
    for k, (_, base, env, peak, solar) in enumerate(_ZONES):
        sun = solar * np.clip(np.cos((hour - peak) / 7.0 * np.pi), 0.0, None)
        wobble = 0.08 * _smooth(_noise(n, seed, 100 + 10 * a + k), 6)
        people = np.where((hour >= 8) & (hour < 17), 0.10, 0.0)
        out[:, k] = base + env * (oat - 62.0) / 20.0 + sun + people + wobble
    return out


def _fleet(seed: int, scenario: str, a: int, idx: pd.DatetimeIndex, oat: np.ndarray) -> dict:
    """One air handler and its six boxes, simulated hour by hour: ``{column: array}``."""
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
        load[:, 4] = 0.05 + 0.02 * _smooth(_noise(n, seed, 200 + a), 6)  # a lightly used room
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
    cols = {f: np.zeros((n, _N_ZONES)) for f in ("T", "FLOW", "DMPR", "VLV")}
    sat_sp_out, dsp_sp_out = np.empty(n), np.empty(n)
    sat_req_out, dsp_req_out = np.zeros(n), np.zeros(n)
    dsp_out, sat_out = np.empty(n), np.empty(n)
    sat_sp, dsp_sp = SAT_TR.sp0, STATIC_TR.sp0
    temp = np.full(_N_ZONES, 72.0)
    for i in range(n):
        if not fan[i]:
            # off: the boxes close, the zones drift toward the weather, nobody asks for anything
            temp = 0.8 * temp + 0.2 * (69.0 + 0.2 * (oat[i] - 60.0))
            cols["T"][i] = temp
            sat_sp = tr_step(sat_sp, 0, SAT_TR)
            dsp_sp = tr_step(dsp_sp, 0, STATIC_TR)
            sat_sp_out[i], dsp_sp_out[i] = sat_sp, dsp_sp
            dsp_out[i] = 0.0
            sat_out[i] = round(float(temp.mean()), 1)
            continue
        static = min(dsp_sp, _STARVED_STATIC) if scenario == "starved" else dsp_sp
        sat_req = dsp_req = 0
        for k in range(_N_ZONES):
            c = load[i, k] * 20.0 / (_COOL_SP - sat_sp)  # warmer supply air needs more of it
            reheat_box = scenario == "reheat" and k in (1, 3)
            fighting = scenario == "heat_cool" and k < 5
            valve = 0.0
            if fighting:
                c = c + 0.35  # the reheat it adds is load the cooling airflow must remove
                valve = 35.0 + 25.0 * float(fight[k][i])
            if c >= 0.0:
                want = min(max(c * vmax[k], vmin[k]), vmax[k])
            else:
                h = min(-c / 0.8, 1.0)  # heating loop, 0-1
                if reheat_box:
                    want = 0.8 * vmax[k]
                    valve = 100.0 * min(1.0, h + 0.15)
                elif h <= 0.5:
                    want = vmin[k]
                    valve = 100.0 * h / 0.5
                else:
                    want = vmin[k] + (h - 0.5) / 0.5 * (vheat[k] - vmin[k])
                    valve = 100.0
            avail = 1.15 * vmax[k] * np.sqrt(static / _DESIGN_STATIC)
            if scenario == "rogue_static" and k == 4:
                avail = 0.4 * vmin[k]  # a blocked inlet
            flow = min(want, avail) * (1.0 + 0.03 * jit[k][i])
            damper = 100.0 if want >= avail else max(5.0, 100.0 * want / avail)
            short = max(c * vmax[k] - flow, 0.0) / vmax[k]  # the load the air cannot carry
            if fighting:
                # held between the two setpoints; the heating loop lets go once the zone is warm
                t = cool_sp[i, k] + 0.4 + min(_WARM_F * short, 9.0)
                if t >= heat_sp[i, k]:
                    valve = 0.0
            elif c >= 0.0:
                t = cool_sp[i, k] - 1.2 + 1.0 * min(c, 1.0) + min(_WARM_F * short, 9.0)
            else:
                t = heat_sp[i, k] + 1.2 - 0.8 * min(-c / 0.8, 1.0)
            t = t + 0.3 * jit[k][i]
            temp[k] = t
            cols["T"][i, k], cols["FLOW"][i, k] = t, flow
            cols["DMPR"][i, k], cols["VLV"][i, k] = damper, valve
            # the box's G36 reset requests (SAT and duct static)
            over = t - cool_sp[i, k]
            sat_req += 3 if over >= 5.0 else 2 if over >= 3.0 else 1 if c > 0.95 else 0
            ratio = flow / want
            if damper > 95.0:
                dsp_req += 3 if ratio < 0.5 else 2 if ratio < 0.7 else 1
        if scenario == "not_responding":
            sat_sp = tr_step(sat_sp, 0, SAT_TR)  # the request input reads nothing
            dsp_sp = tr_step(dsp_sp, 0, STATIC_TR)
        else:
            sat_sp = tr_step(sat_sp, sat_req, SAT_TR)
            dsp_sp = tr_step(dsp_sp, dsp_req, STATIC_TR)
        sat_req_out[i], dsp_req_out[i] = sat_req, dsp_req
        sat_sp_out[i], dsp_sp_out[i] = sat_sp, dsp_sp
        dsp_out[i] = static
        sat_out[i] = sat_sp
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
    for k in range(_N_ZONES):
        z = f"ZN{k + 1}_"
        out[z + "T"] = np.round(cols["T"][:, k], 1)
        out[z + "CLG_SP"] = cool_sp[:, k]
        out[z + "HTG_SP"] = heat_sp[:, k]
        out[z + "FLOW"] = np.round(cols["FLOW"][:, k]).astype(int)
        out[z + "FLOW_MIN"] = np.full(n, int(vmin[k]))
        out[z + "DMPR"] = np.round(cols["DMPR"][:, k]).astype(int)
        out[z + "RHT_VLV"] = np.round(cols["VLV"][:, k]).astype(int)
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
        version=1,
        files=("g36_vav_fleet.csv",),
        build=_g36_vav_fleet,
        description=(
            "a VAV air handler with six reheat boxes under Guideline 36 trim-and-respond resets "
            "(SAT and duct-static setpoints and request counts; each box's space temperature and "
            "setpoints, airflow and minimum, damper and reheat valve), hourly for three weeks of "
            "spring, as eight air handlers: a healthy one, a rogue zone dragging the SAT reset, a "
            "rogue zone holding duct static up, a starved cohort behind a capped fan, a reset "
            "that ignores its requests, single-maximum reheat, overlapping heating and cooling "
            "setpoints, and a warm zone that is not quite a rogue"
        ),
    ),
)
