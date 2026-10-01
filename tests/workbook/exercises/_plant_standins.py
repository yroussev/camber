"""Synthetic stand-ins for the central-plant workbook exercises (#82).

``lbnl-chiller`` and ``lbnl-boiler`` are simulated plants, one year per labelled run; each run is
ingested as one piece of equipment, ``PLANT__<run id>``, in ``ds-lbnl-chiller`` (class
``CHW_PLANT``) or ``ds-lbnl-boiler`` (class ``HW_PLANT``), with the roles the catalog mappings
produce. These builders write four weeks of hourly data per run with the same ids, classes and
roles, and encode the *behaviour* each exercise teaches -- not the real data's figures. The only
published parameters reused are the ones the configs also use: the 1.44 kW/ton and 8.1 F ceilings
the lbnl-chiller template calibrates, the plant's constant DP setpoints and its 34.5 % secondary
pump floor.

The shared weather: four weeks warming from spring to summer (daily mean dry-bulb 50 -> 85 F,
+/- 8 F a day), the wet-bulb a few degrees below it; the building's cooling load follows the
dry-bulb above 55 F, heavier in the occupied weekday hours.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from _workbook import hourly_index, write_standin

from camber.model.roles import Role

#: the plant's design point, shared by every chiller run
CHW_FLOW_GPM = 250.0  # chiller 1's constant primary flow while it runs
KW_PER_TON = 1.44  # the fault-free chiller's kW/ton (the template's calibrated ceiling)
TOWER_A0_F = 8.3  # the clean tower's approach at full fan and design heat
TOWER_SP_OFFSET_F = 8.0  # tower leaving-water setpoint: wet-bulb + 8 F ...
TOWER_FLOOR_F = 60.0  # ... never below 60 F
HEAT_DESIGN_TONS = 120.0  # condenser heat at design (the chiller's load plus its compressor)
PUMP_FLOOR = 0.345  # the secondary pump's VFD minimum (0-1)
CHW_DP_SP = 960.96  # the secondary loop's constant DP setpoint
HW_DP_SP = 480.52  # the hot-water loop's constant DP setpoint (inH2O)

#: run id -> (label, fault parameters); only the runs the exercises use
CHILLER_RUNS = {
    "fault_free": ("", {}),
    "chiller_fouling_065": ("chiller_fouling", {"kw_mult": 1.64}),
    "chiller_fouling_095": ("chiller_fouling", {"kw_mult": 1.06}),
    "coolingtower_fouling_065": ("tower_fouling", {"ua": 0.65, "kw_mult": 1.02}),
    "bypass_stuck_075": ("bypass_valve", {"bypass": 0.75}),
    "chiller_bias_2": ("sensor_bias", {"chw_bias_f": 3.6}),
    "chiller_bias_m2": ("sensor_bias", {"chw_bias_f": -3.6}),
    "coolingtower_bias_2": ("sensor_bias", {"cw_bias_f": 3.6}),
    "coolingtower_bias_m2": ("sensor_bias", {"cw_bias_f": -3.6}),
    "secondary_chilled_water_pressure_bias_020": ("sensor_bias", {"dp_bias": 0.20}),
    "secondary_chilled_water_pressure_bias_m020": ("sensor_bias", {"dp_bias": -0.20}),
}


def _weather(idx: pd.DatetimeIndex):
    """Dry-bulb, wet-bulb (F) and the occupied-hours mask for the shared four weeks."""
    day = np.arange(len(idx)) / 24.0
    daily = 50.0 + 35.0 * np.floor(day) / max(1.0, np.floor(day[-1]))
    oat = daily + 8.0 * np.sin((idx.hour.to_numpy() - 9) / 24 * 2 * np.pi)
    wb = oat - np.clip(4.0 + 0.2 * (oat - 50.0), 2.0, None)
    occ = (idx.dayofweek < 5) & (idx.hour >= 6) & (idx.hour < 19)
    return oat, wb, np.asarray(occ)


def chiller_plant(idx: pd.DatetimeIndex, fault: dict) -> pd.DataFrame:
    """One ``PLANT__<run>`` frame of the chiller plant (chiller 1, tower 1, secondary loop).

    Physics, one line per choice:

    - load (tons) follows the dry-bulb above 55 F, 3x heavier when occupied; the chiller runs
      above 5 tons at a constant 250 gpm, so its loop delta-T is load x 24 / 250 (low at part
      load, as a constant-flow primary loop is);
    - the leaving-water setpoint resets 54 -> 44 F as the dry-bulb goes 60 -> 80 F, and the
      chiller holds its *sensor* at it: a sensor reading ``chw_bias_f`` high makes the real water
      that much colder (more lift, ~2 %/F more power) and the delta-T it reports that much smaller
      (fewer tons), so its kW/ton reads high; a sensor reading low does the opposite, and the
      warmer real water drives the secondary pump to full speed;
    - ``kw_mult`` scales the chiller's power (fouling), ``KW_PER_TON`` when healthy;
    - the tower holds its leaving-water sensor at max(wet-bulb + 8, 60) F; its fan speed is what
      that takes, approach(fan) = 8.3 x heat fraction / (UA x fan^0.8), saturating at full fan,
      where the approach then rises: a fouled tower (UA 0.65) spends many more hours at full fan
      for a small rise in approach; a tower sensor reading ``cw_bias_f`` high makes the real water
      colder (and the reverse), which the condenser entering-water sensor sees as a constant
      offset at every load;
    - ``bypass``: the tower bypass valve is commanded shut but passes that fraction of the
      condenser return around the tower, so the chillers see warmer water (entering = leaving +
      fraction x range), lose capacity (12 tons) and run at about 3 kW/ton; the chilled water
      they cannot cool rises above setpoint and the secondary pump runs flat out;
    - the secondary pump speed rises from its 34.5 % floor with load, around a flat DP setpoint;
      a DP sensor reading ``dp_bias`` high makes the pump slow down (it thinks the loop is
      satisfied) and one reading low speeds it up, while the DP reading itself stays at setpoint.
    """
    oat, wb, occ = _weather(idx)
    load = np.clip(np.where(occ, 3.0, 1.0) * (oat - 55.0), 0.0, 120.0)
    run = load >= 5.0
    sp = np.clip(54.0 - 0.5 * (oat - 60.0), 44.0, 54.0)
    chw_bias = float(fault.get("chw_bias_f", 0.0))
    bypass = float(fault.get("bypass", 0.0))
    delivered = np.minimum(load, 12.0) if bypass else load
    flow = np.where(run, CHW_FLOW_GPM, 0.0)
    dt_true = np.where(run, delivered * 24.0 / CHW_FLOW_GPM, 0.2)
    # the leaving-water sensor sits at setpoint (short of it when the plant lacks capacity)
    short = np.clip(0.25 * (load - delivered), 0.0, 16.0) if bypass else 0.0
    chws = np.where(run, sp + short, sp + 3.0)
    chwr = np.where(run, chws - chw_bias + dt_true, chws + 0.2)
    kw_per_ton = 3.0 if bypass else KW_PER_TON * float(fault.get("kw_mult", 1.0))
    kw_per_ton *= 1.0 + 0.02 * chw_bias  # colder (or warmer) real water: more (or less) lift
    power = np.where(run, kw_per_ton * delivered, 1.9)

    # tower 1: hold the leaving-water sensor at setpoint with the least fan
    ua = float(fault.get("ua", 1.0))
    cw_bias = float(fault.get("cw_bias_f", 0.0))
    heat_frac = np.where(run, 1.3 * delivered / HEAT_DESIGN_TONS, 0.0)
    cw_sp = np.maximum(wb + TOWER_SP_OFFSET_F, TOWER_FLOOR_F)
    need_approach = np.maximum(cw_sp - cw_bias - wb, 0.5)  # the real water the controller makes
    with np.errstate(divide="ignore"):
        fan = (TOWER_A0_F * heat_frac / (ua * need_approach)) ** 1.25
    fan = np.where(heat_frac > 0, np.clip(fan, 0.1, 1.0), 0.0)
    approach_full = TOWER_A0_F * heat_frac / ua
    cws_true = np.where(fan >= 1.0, wb + np.maximum(approach_full, need_approach), cw_sp - cw_bias)
    cws_true = np.where(heat_frac > 0, cws_true, cw_sp - cw_bias)
    # a bypassed condenser loop runs hot: most of its water never reaches the tower
    cw_range = np.where(heat_frac > 0, 10.0 + 20.0 * heat_frac, 0.0) if bypass else 10.0 * heat_frac
    cws_read = cws_true + cw_bias
    cwr = cws_true + cw_range
    cond_in = cws_true + bypass * cw_range

    # the secondary pump: its floor at no load, full speed near peak
    dp_bias = float(fault.get("dp_bias", 0.0))
    speed = PUMP_FLOOR + (1.0 - PUMP_FLOOR) * np.clip(load / 30.0, 0.0, 1.0) ** 0.7
    speed = speed * np.sqrt(1.0 - dp_bias)
    if bypass or chw_bias < 0:
        speed = np.where(run, 1.0, speed)
    speed = np.clip(speed, PUMP_FLOOR, 1.0)

    return pd.DataFrame(
        {
            Role.OAT: oat,
            Role.WETBULB_TEMP: wb,
            Role.POWER: power,
            Role.CHW_SUPPLY_TEMP: chws,
            Role.CHW_RETURN_TEMP: chwr,
            Role.CHW_FLOW: flow,
            Role.CW_SUPPLY_TEMP: cws_read,
            Role.CW_RETURN_TEMP: cwr,
            Role.TOWER_FAN_SPEED: fan,
            Role.COND_ENTERING_WATER_TEMP: cond_in,
            Role.CW_BYPASS_VALVE: np.zeros(len(idx)),
            Role.CHW_PUMP_SPEED: speed,
            Role.CHW_DIFF_PRESS: np.full(len(idx), CHW_DP_SP),
            Role.CHW_DIFF_PRESS_SP: np.full(len(idx), CHW_DP_SP),
        },
        index=idx,
    )


def chiller_standin(store, runs) -> None:
    """``ds-lbnl-chiller`` with the named runs of :data:`CHILLER_RUNS`, labelled as published."""
    idx = hourly_index(days=28)
    frames = {f"PLANT__{r}": ("CHW_PLANT", chiller_plant(idx, CHILLER_RUNS[r][1])) for r in runs}
    labels = {f"PLANT__{r}": CHILLER_RUNS[r][0] for r in runs}
    write_standin(store, "lbnl-chiller", frames, labels=labels)


#: boiler run id -> (label, fault parameters)
BOILER_RUNS = {
    "fault_free": ("", {}),
    "boiler_foul_065": ("boiler_fouling", {"gas_mult": 1.57}),
    "hot_water_pressure_bias_20": ("sensor_bias", {"dp_bias": 0.20}),
    "hot_water_pressure_bias_m20": ("sensor_bias", {"dp_bias": -0.20}),
}


def boiler_plant(idx: pd.DatetimeIndex, fault: dict) -> pd.DataFrame:
    """One ``PLANT__<run>`` frame of the boiler plant (boiler 1, pump 1, the loop).

    Physics: the loop supply is held at a fixed 176 F all year (no reset); the heating load
    follows the dry-bulb below 65 F, so boiler 1's gas input (heat out / 0.8, times ``gas_mult``
    when the boiler's heat exchanger is fouled) falls to zero in warm weather while pump 1 keeps
    running around ~29 % speed against a flat DP setpoint. A DP sensor reading ``dp_bias`` high
    slows the pump (and low speeds it up) while the DP reading itself stays at setpoint. There is
    no boiler run status: the plant's status point is an enable.

    0.98 (#86 item 4a, 098-plant-boiler): while the boiler fires the loop delta-T is 30-42 F (the
    real plant's firing-hour median is 36 F), and 0 F when it does not; the gas-input firing
    fallback now judges it, so it must not sit below the 20 F design floor on a healthy plant.
    """
    oat, _wb, _occ = _weather(idx)
    heat = np.clip((65.0 - oat) / 30.0, 0.0, 1.0)  # fraction of design heating load
    flow = 60.0 + 40.0 * heat
    dt = np.where(heat > 0.02, 30.0 + 12.0 * heat, 0.0)  # 0.98 (#86 item 4a)
    dp_bias = float(fault.get("dp_bias", 0.0))
    speed = (0.29 + 0.08 * heat) * np.sqrt(1.0 - dp_bias)
    gas = 500.0 * flow * dt / 1000.0 * 0.293 / 0.8 * float(fault.get("gas_mult", 1.0))
    return pd.DataFrame(
        {
            Role.OAT: oat,
            Role.HW_SUPPLY_TEMP: np.full(len(idx), 176.0),
            Role.HW_RETURN_TEMP: 176.0 - dt,
            Role.HW_FLOW: flow,
            Role.HW_DIFF_PRESS: np.full(len(idx), HW_DP_SP),
            Role.HW_DIFF_PRESS_SP: np.full(len(idx), HW_DP_SP),
            Role.HW_PUMP_SPEED: speed,
            Role.PUMP_STATUS: np.ones(len(idx)),
            Role.GAS_INPUT_RATE: gas,
        },
        index=idx,
    )


def boiler_standin(store, runs) -> None:
    """``ds-lbnl-boiler`` with the named runs of :data:`BOILER_RUNS`, labelled as published."""
    idx = hourly_index(days=28)
    frames = {f"PLANT__{r}": ("HW_PLANT", boiler_plant(idx, BOILER_RUNS[r][1])) for r in runs}
    labels = {f"PLANT__{r}": BOILER_RUNS[r][0] for r in runs}
    write_standin(store, "lbnl-boiler", frames, labels=labels)
