"""Synthetic AHU excess outside air entry: the cost of air above the minimum (0.108, #195).

Two variable-air-volume air handlers on one building's weekday schedule, every 15 minutes for six
weeks of a humid spring turning to early summer (cold mornings in the first week, hot humid
afternoons by the last), sharing the weather and the space conditions:

* ``excess_oa`` holds its outdoor-air damper at a fixed 60 %, about 45 % outdoor air, whatever the
  weather, against a declared 20 % minimum. In hot or humid hours the cooling coil pays for the
  extra air, in the cold mornings the heating coil does, and in mild dry hours it is free cooling;
* ``economizer`` runs a dry-bulb economizer: outdoor air above its 20 % minimum only while the
  outdoor air is below a fixed 65 F high limit (the ASHRAE 90.1 limit for a hot humid climate),
  modulated to hold the mixed air at the supply setpoint.

Both trend the outdoor and return humidity (RH), the outdoor, return, mixed and supply air
temperatures, the supply-air setpoint (the Guideline 36 reset: 55 F above 70 F outdoors, 65 F
below 60 F), the OA damper, supply airflow, the heating and cooling valves, the fan status and the
occupancy point. No open dataset trends all of these on a unit holding excess outside air.

See :mod:`camber.datasets._synthetic` for how a synthetic entry is generated and ingested.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ._synthetic import Generator, _noise, _smooth, register

_START = "2025-04-28"  # a Monday
_DAYS = 42
_PER_DAY = 96  # 15-minute samples
_OCC_HOURS = (6, 19)  # weekdays; the fans run with the occupancy point
_COLD_SNAP = (1, 8)  # days from the start: a late cold spell
_FAN_HEAT_F = 1.0
_DESIGN_CFM = 12000.0
_MIN_CFM = 6000.0
_HIGH_LIMIT_F = 65.0  # the economizer's fixed dry-bulb high limit

#: scenario -> (outdoor-air damper command %, OA fraction it delivers; None = an economizer that
#: modulates from its minimum)
_UNITS = {
    "excess_oa": (60.0, 0.45),
    "economizer": (None, 0.20),
}


def _rh_from_dewpoint(t_f: np.ndarray, dp_f: np.ndarray) -> np.ndarray:
    """Relative humidity (%) from dry bulb and dew point, by the Magnus form over water."""

    def es(t):
        c = (t - 32.0) * 5.0 / 9.0
        return np.exp(17.62 * c / (243.12 + c))

    return np.clip(100.0 * es(dp_f) / es(t_f), 1.0, 100.0)


def _weather(seed: int, idx: pd.DatetimeIndex) -> tuple:
    n = len(idx)
    hour = np.asarray(idx.hour) + np.asarray(idx.minute) / 60.0
    day = np.arange(n) / _PER_DAY
    season = 60.0 + 17.0 * day / _DAYS  # about 60 F at the end of April to the high 70s in June
    diurnal = 10.0 * np.sin((hour - 9.0) / 24.0 * 2.0 * np.pi)
    swing = 5.0 * _smooth(_noise(n, seed, 1), _PER_DAY * 3)
    snap = np.clip(np.minimum(day - _COLD_SNAP[0], _COLD_SNAP[1] - day), 0.0, 1.0) * 14.0
    jitter = 0.8 * (_noise(n, seed, 2) - 0.5)
    oat = season + diurnal + swing - snap + jitter
    # the dew point follows the season and the weather systems, never above the dry bulb
    dp = 45.0 + 24.0 * day / _DAYS + 4.0 * _smooth(_noise(n, seed, 3), _PER_DAY * 2) - 0.6 * snap
    dp = np.minimum(dp + 0.6 * (_noise(n, seed, 4) - 0.5), oat - 0.5)
    return np.round(oat, 1), dp


def _ahu_excess_oa(seed: int) -> dict:
    """One CSV, 15-minute, the two air handlers stacked with a ``scenario`` column."""
    idx = pd.date_range(_START, periods=_DAYS * _PER_DAY, freq="15min")
    n = len(idx)
    hour = np.asarray(idx.hour) + np.asarray(idx.minute) / 60.0
    occ = (np.asarray(idx.dayofweek) < 5) & (hour >= _OCC_HOURS[0]) & (hour < _OCC_HOURS[1])
    oat, oa_dp = _weather(seed, idx)
    oa_rh = _rh_from_dewpoint(oat, oa_dp)
    # the space: held near 73 F occupied, drifting with the weather unoccupied; its dew point is
    # what the cooling coils leave it at (drier than outdoors in humid weather)
    space = np.where(occ, 73.0 + 0.04 * (oat - 70.0), 70.0 + 0.3 * (oat - 70.0))
    space_dp = np.clip(0.45 * oa_dp + 26.0, 44.0, 55.0)
    # the Guideline 36 supply-air reset: 65 F at 60 F outdoors and below, 55 F at 70 F and above
    sat_sp = np.clip(65.0 - (oat - 60.0), 55.0, 65.0)
    # the cooling load sets the VAV supply airflow
    load = np.clip((oat - 55.0) / 35.0, 0.0, 1.0)
    frames = []
    for k, (scenario, (damper_cmd, oaf_min)) in enumerate(_UNITS.items()):
        rat = space + 1.0 + 0.3 * (_noise(n, seed, 10 + k) - 0.5)
        ra_rh = _rh_from_dewpoint(rat, space_dp)
        if damper_cmd is None:  # the economizer: modulate below the high limit, else minimum
            enabled = (oat < _HIGH_LIMIT_F) & (oat < rat)
            with np.errstate(divide="ignore", invalid="ignore"):
                want = (rat - (sat_sp - _FAN_HEAT_F)) / (rat - oat)
            oaf = np.where(
                enabled, np.clip(np.nan_to_num(want, nan=oaf_min), oaf_min, 1.0), oaf_min
            )
            damper = 25.0 + 75.0 * (oaf - oaf_min) / (1.0 - oaf_min)  # 25 % is the minimum
        else:  # a fixed minimum position set too high: about 45 % outside air in all weather
            oaf = np.full(n, oaf_min)
            damper = np.full(n, damper_cmd)
        mat = oaf * oat + (1.0 - oaf) * rat
        rise = sat_sp - (mat + _FAN_HEAT_F)  # what the coils must add (+) or remove (-)
        heat = np.clip(rise / 25.0 * 100.0, 0.0, 100.0)
        cool = np.clip(-rise / 30.0 * 100.0, 0.0, 100.0)
        sat = sat_sp + 0.3 * (_noise(n, seed, 20 + k) - 0.5)
        cfm = _MIN_CFM + (_DESIGN_CFM - _MIN_CFM) * load
        cfm = cfm * (1.0 + 0.02 * (_noise(n, seed, 30 + k) - 0.5))
        mat_read = mat + 0.4 * (_noise(n, seed, 40 + k) - 0.5)

        # with the fan off the unit is shut: no airflow, coils and damper closed, the mixing box
        # and duct drifting toward the space
        def off(j, k=k):  # each sensor its own reading of the still air
            return space + 0.2 * j + 0.6 * (_noise(n, seed, 50 + 10 * k + j) - 0.5)

        frames.append(
            pd.DataFrame(
                {
                    "Timestamp": idx.strftime("%Y-%m-%d %H:%M"),
                    "scenario": scenario,
                    "OAT": oat,
                    "OA_RH": np.round(oa_rh, 1),
                    "RAT": np.round(np.where(occ, rat, off(1)), 1),
                    "RA_RH": np.round(np.where(occ, ra_rh, _rh_from_dewpoint(off(1), space_dp)), 1),
                    "MAT": np.round(np.where(occ, mat_read, off(2)), 1),
                    "SAT": np.round(np.where(occ, sat, off(3)), 1),
                    "SAT_SP": np.round(sat_sp, 1),
                    "OA_DMPR": np.round(np.where(occ, damper, 0.0), 1),
                    "SA_CFM": np.round(np.where(occ, cfm, 0.0), 0),
                    "HTG_VLV": np.round(np.where(occ, heat, 0.0), 1),
                    "CLG_VLV": np.round(np.where(occ, cool, 0.0), 1),
                    "SF_STS": occ.astype(int),
                    "OCC": occ.astype(int),
                }
            )
        )
    text = pd.concat(frames, ignore_index=True).to_csv(
        index=False, float_format="%.1f", lineterminator="\n"
    )
    return {"ahu_excess_oa.csv": text}


register(
    "ahu_excess_oa",
    Generator(
        version=1,
        files=("ahu_excess_oa.csv",),
        build=_ahu_excess_oa,
        description=(
            "two variable-air-volume air handlers (outdoor and return temperature and humidity, "
            "mixed and supply air, the supply setpoint, OA damper, supply airflow, heating and "
            "cooling valves, fan status, occupancy), every 15 minutes for six weeks of a humid "
            "spring turning to early summer, as two units: one holding about 45 % outside air "
            "against a 20 % minimum in all weather, one economizing below a 65 F high limit"
        ),
    ),
)
