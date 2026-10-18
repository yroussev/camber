"""Synthetic AHU sensor bias entry: a supply-air sensor that reads 3 F high (0.108).

Two identical variable-air-volume air handlers on one building's weekday schedule, every 15
minutes for three weeks of late spring, sharing the weather, the space and the sequence: the
supply-air temperature loop sequences the economizer (above a 20 % minimum, below a 65 F
dry-bulb high limit) and then the cooling valve to hold the **trended** supply-air sensor at a
fixed 55 F setpoint. The site's design sequence resets the setpoint from 55 F up to 62 F as the
outdoor air falls from 70 F to 60 F; the BAS was never set up to do it, on either unit.

* ``healthy``: its supply-air sensor reads the air;
* ``sat_bias``: its supply-air sensor reads 3 F high. The loop holds the false reading at 55 F,
  so the air actually leaves at 52 F, and with both coil valves shut the reading sits 5.5 F above
  the mixed air (2.5 F of it the fan's heat) -- the signature of a leaking heating valve.

A calibrated duct logger was left in each unit's supply duct for four weekdays in the second week
(``SAT_LOGGER``, empty outside those days): the independent reference that shows which unit's
sensor is wrong. No single trend can show a smooth, plausible offset; a reference can.

See :mod:`camber.datasets._synthetic` for how a synthetic entry is generated and ingested.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ._synthetic import Generator, _noise, _smooth, register

_START = "2025-05-12"  # a Monday
_DAYS = 21
_PER_DAY = 96  # 15-minute samples
_OCC_HOURS = (6, 19)  # weekdays; the fans run with the occupancy point
_COOL_SPELL = (1, 6)  # days from the start: cool mornings, the economizer alone carries the load
_LOGGER_DAYS = (7, 11)  # the duct logger's stay: Monday to Thursday of the second week
_SAT_SP_F = 55.0
_FAN_HEAT_F = 2.5  # the rise across the draw-through supply fan
_HIGH_LIMIT_F = 65.0
_OAF_MIN = 0.20
_DESIGN_CFM = 12000.0
_MIN_CFM = 6000.0

#: scenario -> the trended supply-air sensor's error (F)
_UNITS = {"healthy": 0.0, "sat_bias": 3.0}


def _ahu_sensor_bias(seed: int) -> dict:
    """One CSV, 15-minute, the two air handlers stacked with a ``scenario`` column."""
    idx = pd.date_range(_START, periods=_DAYS * _PER_DAY, freq="15min")
    n = len(idx)
    hour = np.asarray(idx.hour) + np.asarray(idx.minute) / 60.0
    day = np.arange(n) / _PER_DAY
    occ = (np.asarray(idx.dayofweek) < 5) & (hour >= _OCC_HOURS[0]) & (hour < _OCC_HOURS[1])
    season = 61.0 + 7.0 * day / _DAYS
    diurnal = 10.0 * np.sin((hour - 9.0) / 24.0 * 2.0 * np.pi)
    swing = 4.0 * _smooth(_noise(n, seed, 1), _PER_DAY * 3)
    spell = np.clip(np.minimum(day - _COOL_SPELL[0], _COOL_SPELL[1] - day), 0.0, 1.0) * 11.0
    oat = np.round(season + diurnal + swing - spell + 0.8 * (_noise(n, seed, 2) - 0.5), 1)
    space = np.where(occ, 73.5 + 0.03 * (oat - 70.0), 71.0 + 0.3 * (oat - 70.0))
    load = np.clip((oat - 55.0) / 35.0, 0.0, 1.0)
    logger_on = (day >= _LOGGER_DAYS[0]) & (day < _LOGGER_DAYS[1])
    frames = []
    for k, (scenario, bias) in enumerate(_UNITS.items()):
        rat = space + 1.0 + 0.3 * (_noise(n, seed, 10 + k) - 0.5)
        # the loop holds the reading (the air + the sensor's error) at the setpoint
        sat_air = _SAT_SP_F - bias + 0.3 * (_noise(n, seed, 20 + k) - 0.5)
        mat_want = sat_air - _FAN_HEAT_F
        enabled = (oat < _HIGH_LIMIT_F) & (oat < rat - 1.0)
        with np.errstate(divide="ignore", invalid="ignore"):
            want = (rat - mat_want) / (rat - oat)
        oaf = np.where(enabled, np.clip(np.nan_to_num(want, nan=_OAF_MIN), _OAF_MIN, 1.0), _OAF_MIN)
        mat = oaf * oat + (1.0 - oaf) * rat
        rise = sat_air - (mat + _FAN_HEAT_F)  # what the coils must add (+) or remove (-)
        heat = np.clip(rise / 25.0 * 100.0, 0.0, 100.0)
        cool = np.clip(-rise / 30.0 * 100.0, 0.0, 100.0)
        damper = 25.0 + 75.0 * (oaf - _OAF_MIN) / (1.0 - _OAF_MIN)  # 25 % is the minimum
        cfm = (_MIN_CFM + (_DESIGN_CFM - _MIN_CFM) * load) * (
            1.0 + 0.02 * (_noise(n, seed, 30 + k) - 0.5)
        )
        mat_read = mat + 0.4 * (_noise(n, seed, 40 + k) - 0.5)

        def still(j, k=k):  # with the fan off each sensor reads the still air in its own duct
            return space + 0.2 * j + 0.6 * (_noise(n, seed, 50 + 10 * k + j) - 0.5)

        duct = np.where(occ, sat_air, still(3))  # the air in the supply duct
        logger = duct + 0.2 * (_noise(n, seed, 60 + k) - 0.5)
        frames.append(
            pd.DataFrame(
                {
                    "Timestamp": idx.strftime("%Y-%m-%d %H:%M"),
                    "scenario": scenario,
                    "OAT": oat,
                    "RAT": np.round(np.where(occ, rat, still(1)), 1),
                    "MAT": np.round(np.where(occ, mat_read, still(2)), 1),
                    "SAT": np.round(duct + bias, 1),
                    "SAT_SP": _SAT_SP_F,
                    "OA_DMPR": np.round(np.where(occ, damper, 0.0), 1),
                    "SA_CFM": np.round(np.where(occ, cfm, 0.0), 0),
                    "HTG_VLV": np.round(np.where(occ, heat, 0.0), 1),
                    "CLG_VLV": np.round(np.where(occ, cool, 0.0), 1),
                    "SF_STS": occ.astype(int),
                    "OCC": occ.astype(int),
                    "SAT_LOGGER": np.where(logger_on, np.round(logger, 1), np.nan),
                }
            )
        )
    text = pd.concat(frames, ignore_index=True).to_csv(
        index=False, float_format="%.1f", lineterminator="\n"
    )
    return {"ahu_sensor_bias.csv": text}


register(
    "ahu_sensor_bias",
    Generator(
        version=1,
        files=("ahu_sensor_bias.csv",),
        build=_ahu_sensor_bias,
        description=(
            "two identical variable-air-volume air handlers (outdoor, return, mixed and supply "
            "air, the supply setpoint, OA damper, supply airflow, heating and cooling valves, fan "
            "status, occupancy, and a duct logger's supply-air reading for four days), every 15 "
            "minutes for three weeks of late spring on a fixed 55 F supply setpoint, as two "
            "units: one whose supply-air sensor reads the air, one whose sensor reads 3 F high"
        ),
    ),
)
