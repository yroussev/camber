"""Shared synthetic stand-ins for the air-side workbook exercises (#80).

Each builder returns one role frame shaped like a real catalog ingest (same roles, same equipment
class), encoding the *behaviour* an exercise teaches -- never a copy of the real data. Only
published design parameters are reused: the lbnl-sdahu unit's 55.25 F supply-air and 1.607 in.
w.c. static-pressure setpoints (fixed in its sequence) and its 1.6 % design-minimum OA fraction;
the ORNL tests' 07:00-22:00 schedule and 15.6 C (60.1 F) heating setback.

The weather: two cool weeks (OAT 20-44 F: free cooling) then two warm ones (70-90 F: cooling
weather), each day swinging with the sun (coldest at 03:00, warmest at 15:00); the Irish unit
gets a milder climate of its own (40-56 F, then 55-71 F, then a 64-80 F warm spell).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from camber.model.roles import Role

SDAHU_SAT_SP = 55.25  # the lbnl-sdahu sequence's fixed supply-air setpoint (F)
SDAHU_STATIC_SP = 1.607  # its fixed duct static-pressure setpoint (in. w.c.)
SDAHU_MIN_OAF = 0.016  # its design minimum OA fraction (a 10 % damper position)
FAN_HEAT_F = 1.0  # supply-fan heat between the mixed-air and supply-air sensors


def _days(idx: pd.DatetimeIndex) -> np.ndarray:
    return ((idx - idx[0]) / pd.Timedelta("1D")).to_numpy()


def _phase(idx: pd.DatetimeIndex) -> np.ndarray:
    hours = idx.hour.to_numpy() + idx.minute.to_numpy() / 60.0
    return np.sin((hours - 9.0) / 24.0 * 2.0 * np.pi)


def weather(idx: pd.DatetimeIndex) -> np.ndarray:
    """OAT: two cool weeks (32 +- 12 F) then warm ones (80 +- 10 F)."""
    return np.where(_days(idx) < 14, 32.0 + 12.0 * _phase(idx), 80.0 + 10.0 * _phase(idx))


def mild_weather(idx: pd.DatetimeIndex) -> np.ndarray:
    """A mild maritime OAT: two cool weeks (48 +- 8 F), a mild one (63 +- 8 F), then a warm
    spell (72 +- 8 F)."""
    d = _days(idx)
    base = np.where(d < 14, 48.0, np.where(d < 21, 63.0, 72.0))
    return base + 8.0 * _phase(idx)


def office_hours(idx: pd.DatetimeIndex) -> np.ndarray:
    """Occupied 06-18 on weekdays (the stand-in's own schedule; the fan runs only then)."""
    return (idx.dayofweek < 5) & (idx.hour >= 6) & (idx.hour < 18)


def sdahu(
    idx: pd.DatetimeIndex,
    *,
    stuck_oaf: float | None = None,
    leak_f: float = 0.0,
    static_sp: bool = True,
) -> pd.DataFrame:
    """One lbnl-sdahu-shaped single-duct AHU: cooling coil only, fixed 60 F dry-bulb economizer.

    - The economizer mixes toward 55 F below the high limit, else holds the design minimum; a
      stuck damper (``stuck_oaf``) fixes the actual OA fraction whatever the command says.
    - The coil holds the fixed 55.25 F setpoint (no reset); with the valve shut the supply air is
      the mixed air plus the fan's heat. There is no heating coil, so cold mixed air (a damper
      stuck open in cool weather) leaves the supply air below its setpoint.
    - ``leak_f``: a valve commanded shut still passes chilled water, taking ``leak_f`` F out of
      the air -- here less than the fan's heat plus the rule's 3 F margin, like the published
      10 % leak.
    - Duct static is held at a fixed setpoint (trended when ``static_sp``; the faulted runs'
      setpoint is a masked placeholder, so only the measured static is left).
    """
    n = len(idx)
    oat = weather(idx)
    rat = np.full(n, 72.0) + 0.3 * np.cos(np.arange(n))
    occ = office_hours(idx)
    fan = occ.astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        need = np.clip((rat - 55.0) / (rat - oat), SDAHU_MIN_OAF, 1.0)
    cmd_oaf = np.where(oat < 60.0, need, SDAHU_MIN_OAF)
    damper = np.where(occ, 10.0 + 90.0 * (cmd_oaf - SDAHU_MIN_OAF) / (1.0 - SDAHU_MIN_OAF), 0.0)
    oaf = cmd_oaf if stuck_oaf is None else np.full(n, stuck_oaf)
    mat = np.where(occ, oaf * oat + (1.0 - oaf) * rat, rat - 1.0)
    off_coil = mat + FAN_HEAT_F
    cooling = occ & (off_coil > SDAHU_SAT_SP + 1.0)
    valve = np.where(cooling, np.clip((off_coil - SDAHU_SAT_SP) * 6.0, 6.0, 100.0), 0.0)
    sat = np.where(cooling, SDAHU_SAT_SP + 0.1 * np.sin(np.arange(n)), off_coil - leak_f)
    sat = np.where(occ, sat, mat)
    frame = {
        Role.OAT: oat,
        Role.RETURN_AIR_TEMP: rat,
        Role.MIXED_AIR_TEMP: mat,
        Role.SUPPLY_AIR_TEMP: sat,
        Role.SUPPLY_AIR_TEMP_SP: np.full(n, SDAHU_SAT_SP),
        Role.OA_DAMPER: damper,
        Role.COOL_VALVE: valve,
        Role.SUPPLY_FAN_STATUS: fan,
        Role.SUPPLY_FAN_SPEED: 0.8 * fan,
        Role.OCCUPANCY: fan,
        Role.AIRFLOW: 9000.0 * fan,
    }
    if static_sp:
        frame[Role.DUCT_STATIC_SP] = np.full(n, SDAHU_STATIC_SP)
    else:
        frame[Role.DUCT_STATIC] = np.where(occ, 1.55, 0.004)
    return pd.DataFrame(frame, index=idx)


def ddahu(idx: pd.DatetimeIndex) -> pd.DataFrame:
    """One lbnl-ddahu-shaped dual-duct AHU: a cold deck held at 55 F, a hot deck heating.

    The cold deck's supply air is held at a fixed 55 F whatever the weather (no setpoint is
    trended), and by design the hot deck heats while the cold deck cools: both valves are open
    together in mild weather.
    """
    n = len(idx)
    oat = weather(idx)
    rat = np.full(n, 72.0) + 0.3 * np.cos(np.arange(n))
    occ = office_hours(idx)
    fan = occ.astype(float)
    oaf = np.full(n, 0.3)
    mat = np.where(occ, oaf * oat + (1.0 - oaf) * rat, rat - 1.0)
    cool = np.where(occ & (mat > 54.0), np.clip((mat - 54.0) * 5.0, 6.0, 100.0), 0.0)
    heat = np.where(occ & (oat < 70.0), np.clip((70.0 - oat) * 2.0, 6.0, 100.0), 0.0)
    sat = np.where(occ, 55.0 + 0.2 * np.sin(np.arange(n)), mat)
    return pd.DataFrame(
        {
            Role.OAT: oat,
            Role.RETURN_AIR_TEMP: rat,
            Role.MIXED_AIR_TEMP: mat,
            Role.SUPPLY_AIR_TEMP: sat,
            Role.OA_DAMPER: np.where(occ, 45.0, 0.0),
            Role.COOL_VALVE: cool,
            Role.HEAT_VALVE: heat,
            Role.SUPPLY_FAN_STATUS: fan,
            Role.OCCUPANCY: fan,
            Role.OA_AIRFLOW: 3000.0 * fan,
            Role.AIRFLOW: 10000.0 * fan,
        },
        index=idx,
    )


def irish(idx: pd.DatetimeIndex, *, full_oa: tuple | None = None) -> pd.DataFrame:
    """One irish-ahu-shaped real mixing-box AHU: both coils, coil leaving-air sensors, no fan
    or occupancy signal (it runs around the clock), 24/7 on the whole record.

    - Heating and cooling coils never open together; with both shut, each coil's leaving air is
      the mixed air (no leak).
    - Supply air is reset *down* as the outdoor air warms (a G36-style OAT reset of the cooling
      supply temperature: 65 F at 60 F outdoors to 55 F at 70 F and above).
    - The OA damper holds its minimum (a 4.1 % OA fraction) when it is warm, and economizes in
      cool weather, mixing to the supply target; over ``full_oa`` (a ``(first, last)`` span in
      days from the start) it is held fully open, the way the real unit ran at 100 % outdoor air
      for a documented operating decision.
    """
    n = len(idx)
    oat = mild_weather(idx)
    rat = np.full(n, 70.0) + 0.3 * np.cos(np.arange(n))
    minimum = 0.041
    target = np.clip(65.0 - (oat - 60.0), 55.0, 65.0)  # the OAT reset of the supply air
    with np.errstate(divide="ignore", invalid="ignore"):
        need = np.clip((rat - target) / (rat - oat), minimum, 1.0)
    oaf = np.where(oat < 60.0, need, minimum)
    if full_oa is not None:
        d = _days(idx)
        oaf = np.where((d >= full_oa[0]) & (d < full_oa[1]), 1.0, oaf)
    mat = oaf * oat + (1.0 - oaf) * rat
    cool = np.where(mat > target + 0.5, np.clip((mat - target) * 5.0, 6.0, 100.0), 0.0)
    heat = np.where(
        (cool == 0.0) & (mat < target - 0.5), np.clip((target - mat) * 5.0, 6.0, 100.0), 0.0
    )
    sat = np.where((cool > 0) | (heat > 0), target, mat + 0.5)
    return pd.DataFrame(
        {
            Role.OAT: oat,
            Role.RETURN_AIR_TEMP: rat,
            Role.MIXED_AIR_TEMP: mat,
            Role.SUPPLY_AIR_TEMP: sat,
            Role.OA_DAMPER: 20.0 + 80.0 * (oaf - minimum) / (1.0 - minimum),
            Role.COOL_VALVE: cool,
            Role.HEAT_VALVE: heat,
            Role.HEAT_COIL_LEAVING_TEMP: np.where(heat > 0, target, mat),
            Role.COOL_COIL_LEAVING_TEMP: np.where(cool > 0, target, mat),
        },
        index=idx,
    )
