"""Synthetic heat pumps (0.105, #147): air-source and water-source units and their source loop.

No open dataset in the catalog shows a heat pump defrosting far too often, heating a room that
is already too warm, falling behind in a cold snap with its strip heat dead, fighting a second
unit in the same room, chattering between compressor stages, or a source loop that pumps water
without carrying heat. This generator builds one small building's heat pumps through a fortnight
of late winter -- damp frosty days, a three-day cold snap, mild fog and a warm spell, so the
units both heat and cool -- every 5 minutes (a defrost lasts 5 to 10 minutes; a coarser trend
averages it away).

The model, per room: one thermal node (``°F/h`` rates: envelope loss to outdoors, internal gains
in occupied hours, sun on the clear days) and the unit(s) serving it, each run by its own
thermostat on its own sensor:

* **air-source** units (``ASHP__*``): one compressor plus electric strip heat (the "aux" second
  stage, which also tempers the air during a defrost). Heating capacity falls with the outdoor
  temperature. Frost builds on the outdoor coil while the unit heats below about 44 °F, faster
  in humid air; the defrost reverses the valve (``RV`` 0) for 5 to 10 minutes. ``healthy`` and
  ``no_aux_heat`` defrost on demand (when frost has built up); ``defrost_timer`` uses a
  time-temperature control on its 60-minute interval (it defrosts whenever the coil is below
  freezing, frost or not); ``defrost_sensor`` has a coil sensor that reads far too cold, so it
  defrosts every 30 minutes of heating at any outdoor temperature, each one ending within 5
  minutes because the coil carries no frost. ``no_aux_heat`` has lost its strip heat.
* **water-source** units (``WSHP__*``): two compressor stages, capacity independent of the
  weather (the loop is held at 60-70 °F). ``classroom`` and ``classroom_twin`` share one room
  and agree; ``window_side`` and ``door_side`` share another, but one sensor sits by cold glass
  and the other in the sun by the door, so one heats while the other cools. ``stuck_heating``
  serves an interior room that needs cooling all day, and its reversing valve is stuck in
  heating: every cooling call heats. ``stage_hunting`` has a 0.1 °F stage-2 differential and
  no stage-up delay.
* the **source loop** (``LOOP__*``) of the water-source units: ``loop_ok`` pumps with a
  variable-speed drive against two-way valves at the units; ``loop_flat`` runs both pumps at
  constant speed with the bypass stuck open, so four times the design flow passes the plant's
  sensors and the loop delta-T collapses.

Deterministic: integer-hash noise only (:func:`._synthetic._noise`), a fixed seed per entry.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ._synthetic import Generator, _noise, _smooth, register

_START = "2025-02-17"  # a Monday
_DAYS = 14
_STEP_MIN = 5
_DT_H = _STEP_MIN / 60.0
_PER_DAY = 24 * 60 // _STEP_MIN
#: °F at a unit's own sensor per °F/h of its output: the air near it warms before the room
_AIR_NODE = 0.12

#: day -> (mean OAT °F, half the daily swing °F, humidity 0..1, sun 0..1)
_WEATHER = (
    (36.0, 3.0, 0.90, 0.1),  # Mon: damp, drizzle
    (38.0, 3.0, 0.92, 0.1),  # Tue: damp
    (26.0, 6.0, 0.70, 0.5),  # Wed: the cold front
    (12.0, 7.0, 0.50, 0.9),  # Thu: cold snap, dry and clear
    (14.0, 7.0, 0.50, 0.9),  # Fri
    (20.0, 8.0, 0.55, 0.8),  # Sat
    (29.0, 7.0, 0.75, 0.4),  # Sun
    (35.0, 4.0, 0.88, 0.2),  # Mon: damp again
    (44.0, 4.0, 0.95, 0.1),  # Tue: mild fog
    (47.0, 4.0, 0.95, 0.1),  # Wed: mild fog
    (51.0, 9.0, 0.60, 1.0),  # Thu: mild and sunny
    (55.0, 9.0, 0.55, 1.0),  # Fri: the warmest day
    (48.0, 7.0, 0.60, 0.8),  # Sat
    (40.0, 5.0, 0.85, 0.3),  # Sun
)

#: the scenarios, in file order: (scenario, kind)
_SCENARIOS = (
    ("healthy", "ashp"),
    ("defrost_sensor", "ashp"),
    ("defrost_timer", "ashp"),
    ("no_aux_heat", "ashp"),
    ("classroom", "wshp"),
    ("classroom_twin", "wshp"),
    ("stuck_heating", "wshp"),
    ("stage_hunting", "wshp"),
    ("window_side", "wshp"),
    ("door_side", "wshp"),
    ("loop_ok", "loop"),
    ("loop_flat", "loop"),
)

_COLUMNS = (
    "Timestamp",
    "scenario",
    "OAT",
    "ZAT",
    "SAT",
    "FAN",
    "OCC",
    "HTG_SP",
    "CLG_SP",
    "COMP",
    "RV",
    "STAGE",
    "LOOP_SWT",
    "LOOP_RWT",
    "LOOP_PUMP",
    "LOOP_SPD",
    "LOOP_DP",
)
_INTS = ("FAN", "OCC", "HTG_SP", "CLG_SP", "COMP", "RV", "STAGE", "LOOP_PUMP")


# --------------------------------------------------------------------------------- weather


def _weather(seed: int, idx: pd.DatetimeIndex) -> dict:
    n = len(idx)
    hour = np.asarray(idx.hour) + np.asarray(idx.minute) / 60.0
    t_day = np.arange(n) / _PER_DAY  # days from the start
    days = np.arange(_DAYS) + 0.5
    w = np.asarray(_WEATHER)
    mean = np.interp(t_day, days, w[:, 0])
    swing = np.interp(t_day, days, w[:, 1])
    hum = np.interp(t_day, days, w[:, 2])
    sun = w[np.minimum(t_day.astype(int), _DAYS - 1), 3]
    diurnal = np.sin((hour - 9.0) / 24.0 * 2.0 * np.pi)  # warmest mid-afternoon
    drift = 1.5 * _smooth(_noise(n, seed, 1), _PER_DAY // 2)
    jitter = 0.6 * (_noise(n, seed, 2) - 0.5)
    oat = mean + swing * diurnal + drift + jitter
    solar = sun * np.clip(np.sin((hour - 7.0) / 10.0 * np.pi), 0.0, None)  # 07:00-17:00
    dow = np.asarray(idx.dayofweek)
    weekday = dow < 5
    occ = weekday & (hour >= 7.0) & (hour < 18.0)  # people in the building
    hvac = weekday & (hour >= 6.0) & (hour < 18.0)  # occupied setpoints (one hour's warm-up)
    return {
        "oat": np.round(oat, 1),
        "hum": hum,
        "solar": solar,
        "occ": occ,
        "hvac": hvac,
        "n": n,
    }


# ------------------------------------------------------------------------------ the units


class _Unit:
    """One heat pump and its thermostat (state carried from step to step)."""

    def __init__(self, kind: str, **p):
        self.kind = kind
        self.p = p
        self.stage = 0  # 0 off, 1, 2 (air-source: 1 = compressor on)
        self.mode = 1  # the thermostat's last mode: 1 heating, -1 cooling
        self.aux = False
        self.off_steps = 99  # steps since the compressor stopped
        self.on_steps = 0  # steps since the current stage-1 run started
        self.frost = 0.0
        self.run_since_defrost = 0.0  # compressor heating hours since the last defrost
        self.defrost_left = 0
        self.lockout = 0  # steps of a high-pressure lockout left
        self.sat = 68.0
        self.q_prev = 0.0  # last step's output, °F/h: the air at the sensor feels it first

    # -- the thermostat
    def control(self, ts: float, hsp: float, csp: float) -> None:
        p = self.p
        heat_calls = ts < hsp - p["on_band"]
        cool_calls = ts > csp + p["on_band"]
        if self.stage == 0:
            if self.off_steps >= p["min_off"] and self.lockout == 0:
                if heat_calls:
                    self.stage, self.mode, self.on_steps = 1, 1, 0
                elif cool_calls:
                    self.stage, self.mode, self.on_steps = 1, -1, 0
        else:
            sp = hsp if self.mode == 1 else csp
            err = (sp - ts) * self.mode  # positive: the room still needs this mode
            if err < -p["off_band"]:
                self.stage = 0
                self.off_steps = 0
            elif self.kind == "wshp":
                if self.stage == 1 and err > p["s2_on"] and self.on_steps >= p["s2_delay"]:
                    self.stage = 2
                elif self.stage == 2 and err < p["s2_off"]:
                    self.stage = 1
        if self.stage == 0:
            self.off_steps += 1
        else:
            self.on_steps += 1
        if self.kind == "ashp":
            if p["aux_cap"] > 0 and self.mode == 1 and self.stage and ts < hsp - 1.0:
                self.aux = True
            elif ts > hsp or self.mode != 1:
                self.aux = False

    # -- the defrost (air-source only): returns True while defrosting
    def defrost(self, oat: float, hum: float) -> bool:
        p = self.p
        if self.defrost_left > 0:
            self.defrost_left -= 1
            return True
        if self.stage == 0 or self.mode != 1:
            return False
        self.run_since_defrost += _DT_H
        coil_frozen = oat < 40.0  # the outdoor coil runs ~10-15 °F below the air
        if oat < 44.0:  # frost builds on a coil below freezing, faster in humid air
            frost_rate = np.clip((44.0 - oat) / 10.0, 0.0, 1.0) * np.clip(oat / 20.0, 0.2, 1.0)
            self.frost += hum * frost_rate * _DT_H
        how = p["defrost"]
        start, length = False, 0
        if how == "demand":
            if self.frost >= 1.0 and self.run_since_defrost >= 0.5:
                # a heavy frost (humid air, a cold coil) takes 10 minutes to melt, else 5
                start, length = True, 2 if hum > 0.8 and oat < 38.0 else 1
        elif how == "timer":  # time-temperature, its interval timer while the coil is frozen
            if coil_frozen and self.run_since_defrost >= p["interval_h"]:
                start, length = True, 2 if self.frost >= 0.6 else 1
        elif how == "bad_sensor":  # the coil sensor always reads frozen
            if self.run_since_defrost >= p["interval_h"]:
                start, length = True, 1
        if start:
            self.frost = 0.0
            self.run_since_defrost = 0.0
            self.defrost_left = length - 1
            return True
        return False


def _ashp(defrost: str, aux: bool, **over) -> _Unit:
    p = {
        "on_band": 0.5,
        "off_band": 0.5,
        "min_off": 2,
        "cap": 3.8,
        "cool_cap": 5.0,
        "aux_cap": 5.0 if aux else 0.0,
        "defrost": defrost,
        "interval_h": 1.0,
    }
    p.update(over)
    return _Unit("ashp", **p)


def _wshp(**over) -> _Unit:
    p = {
        "on_band": 0.5,
        "off_band": 1.5,  # a 2 °F stage-1 differential
        "min_off": 2,  # 10 minutes
        "s2_on": 2.0,
        "s2_off": 0.5,
        "s2_delay": 2,  # 10 minutes on stage 1 first
        "cap": 4.0,  # full (stage 2) capacity, °F/h for its room
        "stuck_heating": False,
        "s1_frac": 0.6,  # stage 1 as a share of full capacity
    }
    p.update(over)
    return _Unit("wshp", **p)


def _ashp_capacity(oat: float) -> float:
    """Heating capacity as a fraction of the 47 °F rating (about 60 % at 17 °F)."""
    return 0.40 + 0.60 * min(max((oat - 5.0) / 42.0, 0.0), 1.1)


def _room(w: dict, seed: int, stream: int, units: list, offsets: list, room: dict) -> list:
    """Run one room and its unit(s) through the fortnight; one record dict per unit."""
    n = w["n"]
    oat, hum, solar, occ, hvac = w["oat"], w["hum"], w["solar"], w["occ"], w["hvac"]
    hsp = np.where(hvac, 70.0, room["heat_setback"])
    csp = np.where(hvac, 74.0, 82.0)
    noise = [0.3 * (_noise(n, seed, stream + 10 + j) - 0.5) for j in range(len(units))]
    sat_noise = [0.4 * (_noise(n, seed, stream + 20 + j) - 0.5) for j in range(len(units))]
    gains = np.where(occ, room["gain_occ"], room["gain_unocc"]) + room["solar"] * solar
    out = [{k: np.zeros(n) for k in ("zat", "sat", "fan", "comp", "rv", "stage")} for _ in units]
    t = room["t0"]
    for i in range(n):
        q = 0.0
        for j, u in enumerate(units):
            ts = t + offsets[j][i] + noise[j][i] + _AIR_NODE * u.q_prev
            if u.lockout:
                u.lockout -= 1
            u.control(ts, hsp[i], csp[i])
            p = u.p
            defrosting = False
            if u.kind == "ashp":
                defrosting = u.defrost(oat[i], hum[i])
                if u.stage and u.mode == 1 and not defrosting:
                    qh = p["cap"] * _ashp_capacity(oat[i])
                elif defrosting:
                    qh = -0.35 * p["cap"]  # cooling the room air while the coil thaws
                elif u.stage:
                    qh = -p["cool_cap"]
                else:
                    qh = 0.0
                aux_on = u.aux or (defrosting and p["aux_cap"] > 0)
                qa = p["aux_cap"] if aux_on else 0.0
                q_unit = qh + qa
                rv = 0 if (defrosting or u.mode == -1) else 1
                rise = 26.0 * qh / p["cap"] + 15.0 * qa / 5.0 if u.stage else 0.0
            else:
                frac = (0.0, p["s1_frac"], 1.0)[u.stage]
                heating = u.mode == 1 or p["stuck_heating"]
                q_unit = p["cap"] * frac * (1.0 if heating else -1.0)
                rv = 1 if heating else 0
                rise = (0.0, 21.0, 29.0)[u.stage] if heating else (0.0, -16.0, -21.0)[u.stage]
                aux_on = False
                # the stuck unit's high-pressure switch trips on warm return air
                if p["stuck_heating"] and u.stage and t > 80.0:
                    u.stage, u.off_steps, u.lockout = 0, 0, 6
                    q_unit, rise = 0.0, 0.0
            q += q_unit
            u.q_prev = q_unit
            running = u.stage > 0 or aux_on
            fan = bool(hvac[i]) or running
            target = t + rise if running else (t + 0.5 if fan else t - 1.5)
            u.sat += 0.7 * (target - u.sat)
            rec = out[j]
            rec["zat"][i] = ts
            rec["sat"][i] = u.sat + sat_noise[j][i]
            rec["fan"][i] = fan
            rec["comp"][i] = 1 if u.stage else 0
            rec["rv"][i] = rv
            rec["stage"][i] = u.stage
        t += _DT_H * (q + gains[i] - room["k"] * (t - oat[i]) - room.get("k_adj", 0.0) * (t - 70.0))
    for rec in out:
        rec["hsp"], rec["csp"] = hsp, csp
    return out


# ------------------------------------------------------------------------------ the loop


def _loop(w: dict, seed: int, flat: bool) -> dict:
    """A 60-ton water-source building's loop: heating extracts heat, interior zones reject it."""
    n = w["n"]
    oat, occ, hvac, solar = w["oat"], w["occ"], w["hvac"], w["solar"]
    wobble = _smooth(_noise(n, seed, 60), 12)
    heat_tons = 55.0 * np.clip((58.0 - oat) / 50.0, 0.0, 1.0) * np.where(hvac, 1.0, 0.75)
    cool_tons = np.where(occ, 9.0, 2.0) + 14.0 * solar * np.clip((oat - 48.0) / 12.0, 0.0, 1.0)
    heat_tons = np.clip(heat_tons * (1.0 + 0.15 * wobble), 0.0, 60.0)
    gross = np.clip(heat_tons + cool_tons, 0.0, 60.0)
    net = heat_tons - cool_tons  # extracted from the loop (heating dominates in late winter)
    design_gpm = 180.0  # 3 gpm a ton
    jit = _noise(n, seed, 61) - 0.5
    if flat:  # both pumps at constant speed, the bypass stuck open: 4x design past the sensors
        flow = np.full(n, 5.0 * design_gpm)
        speed = np.full(n, 100.0)
        dp = 14.0 + 0.6 * jit
    else:  # two-way valves at the units, a variable-speed pump holding 6 psi
        flow = np.maximum(0.15 * design_gpm, 3.0 * gross)
        speed = np.clip(22.0 + 78.0 * (flow / design_gpm) ** 0.8 + 1.5 * jit, 0.0, 100.0)
        dp = 6.0 + 0.4 * jit
    dt = 24.0 * net / flow  # tons -> °F: 12000 Btu/h a ton over 500 Btu/h per gpm·°F
    warm = np.clip(
        (np.convolve(oat, np.ones(_PER_DAY) / _PER_DAY, mode="same") - 45.0) / 12.0, 0, 1
    )
    supply = 62.5 + 2.0 * _smooth(_noise(n, seed, 62), _PER_DAY // 4) + 5.0 * warm
    supply = supply + 0.3 * (_noise(n, seed, 63) - 0.5)
    ret = supply - dt + 0.3 * (_noise(n, seed, 64) - 0.5)
    return {
        "swt": supply,
        "rwt": ret,
        "pump": np.ones(n, dtype=int),
        "spd": speed,
        "dp": dp,
    }


# ------------------------------------------------------------------------------ the build


def _ashp_rooms(w: dict, seed: int) -> dict:
    room = {"k": 0.085, "gain_occ": 0.8, "gain_unocc": 0.15, "solar": 1.2, "heat_setback": 66.0}
    zero = np.zeros(w["n"])
    specs = {
        "healthy": _ashp("demand", aux=True),
        "defrost_sensor": _ashp("bad_sensor", aux=True, interval_h=0.5),
        "defrost_timer": _ashp("timer", aux=True, interval_h=1.0),
        "no_aux_heat": _ashp("demand", aux=False),
    }
    out = {}
    for k, (name, unit) in enumerate(specs.items()):
        out[name] = _room(w, seed, 100 + 10 * k, [unit], [zero], {**room, "t0": 66.0})[0]
    return out


def _wshp_rooms(w: dict, seed: int) -> dict:
    n = w["n"]
    zero = np.zeros(n)
    perim = {"k": 0.05, "gain_occ": 1.2, "gain_unocc": 0.15, "solar": 1.6, "heat_setback": 64.0}
    out = {}
    # room A: two units that agree (sensors a few tenths apart)
    a = _room(
        w,
        seed,
        200,
        [_wshp(cap=2.5), _wshp(cap=2.5)],
        [zero + 0.2, zero - 0.2],
        {**perim, "t0": 68.0},
    )
    out["classroom"], out["classroom_twin"] = a
    # an interior room that needs cooling all day; its reversing valve is stuck in heating
    interior = {**perim, "k": 0.01, "k_adj": 0.3, "gain_occ": 2.4, "gain_unocc": 0.4, "solar": 0.0}
    out["stuck_heating"] = _room(
        w, seed, 230, [_wshp(stuck_heating=True)], [zero], {**interior, "t0": 70.0}
    )[0]
    # a corner classroom whose second stage chatters: 0.1 °F differential, no stage-up delay
    out["stage_hunting"] = _room(
        w,
        seed,
        240,
        [_wshp(cap=5.5, s1_frac=0.5, s2_on=0.1, s2_off=0.0, s2_delay=0)],
        [zero],
        {**perim, "k": 0.09, "heat_setback": 66.0, "t0": 68.0},
    )[0]
    # room B: one sensor by cold glass, the other in the sun by the door
    cold_glass = -(1.2 + 0.05 * np.clip(50.0 - w["oat"], 0.0, None))
    warm_spot = 1.4 + 2.0 * w["solar"] + 0.8 * w["occ"]
    big = {**perim, "k": 0.06, "gain_occ": 1.5, "solar": 1.0}
    b = _room(
        w,
        seed,
        250,
        [_wshp(cap=3.0), _wshp(cap=3.0)],
        [cold_glass, warm_spot],
        {**big, "t0": 68.0},
    )
    out["window_side"], out["door_side"] = b
    return out


def _heat_pump(seed: int) -> dict:
    """One CSV, 5-minute, the twelve scenarios stacked with a ``scenario`` column."""
    idx = pd.date_range(_START, periods=_DAYS * _PER_DAY, freq=f"{_STEP_MIN}min")
    w = _weather(seed, idx)
    n = w["n"]
    units = {**_ashp_rooms(w, seed), **_wshp_rooms(w, seed)}
    loops = {"loop_ok": _loop(w, seed, flat=False), "loop_flat": _loop(w, seed, flat=True)}
    stamp = idx.strftime("%Y-%m-%d %H:%M")
    frames = []
    for scenario, kind in _SCENARIOS:
        cols: dict = {c: np.full(n, np.nan) for c in _COLUMNS[2:]}
        cols["OAT"] = w["oat"]
        if kind == "loop":
            lp = loops[scenario]
            cols.update(
                LOOP_SWT=lp["swt"],
                LOOP_RWT=lp["rwt"],
                LOOP_PUMP=lp["pump"],
                LOOP_SPD=lp["spd"],
                LOOP_DP=lp["dp"],
            )
        else:
            u = units[scenario]
            cols.update(
                ZAT=u["zat"],
                SAT=u["sat"],
                FAN=u["fan"],
                OCC=w["occ"].astype(int),
                HTG_SP=u["hsp"],
                CLG_SP=u["csp"],
            )
            if kind == "ashp":
                cols.update(COMP=u["comp"], RV=u["rv"])
            else:
                cols["STAGE"] = u["stage"]
        frame = pd.DataFrame({"Timestamp": stamp, "scenario": scenario})
        for c in _COLUMNS[2:]:
            v = np.asarray(cols[c], dtype=float)
            if c in _INTS:
                frame[c] = pd.array(np.round(v), dtype="Float64").astype("Int64")
            else:
                frame[c] = np.round(v, 1)
        frames.append(frame)
    text = pd.concat(frames, ignore_index=True).to_csv(
        index=False, float_format="%.1f", lineterminator="\n"
    )
    return {"heat_pump.csv": text}


register(
    "heat_pump",
    Generator(
        version=1,
        files=("heat_pump.csv",),
        build=_heat_pump,
        description=(
            "a small building's heat pumps (outdoor air, zone and discharge-air temperatures, "
            "fan, occupancy, heating and cooling setpoints, compressor status and reversing "
            "valve or compressor stage) and their source loop (supply and return water, pump "
            "status, speed and DP), every 5 minutes for a fortnight of late winter with a cold "
            "snap and a warm spell, as twelve scenarios: four air-source units (healthy, a "
            "defrost sensor reading far too cold, a 60-minute defrost timer, no strip heat), six "
            "water-source units (a pair sharing a room, one with its reversing valve stuck in "
            "heating, one whose second stage hunts, and a pair that fight in one room) and two "
            "loops (variable flow, and constant speed with the bypass stuck open)"
        ),
    ),
)
