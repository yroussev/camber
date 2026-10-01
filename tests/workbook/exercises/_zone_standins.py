"""Synthetic stand-ins shared by the terminal-unit exercises (#81): ``lbnl-fpu`` and
``ornl-frp-vav``.

``zone-reheat-overcooling`` and ``zone-reheat-saturated`` both run on the ``lbnl-fpu`` stand-in;
``zone-bad-box`` and ``zone-reheat-saturated`` both run on the ``ornl-frp-vav`` one. Each writes the
real ingest's facility, equipment ids, class and roles; the behaviour is the lesson's, not a
replay of the data (see each builder's docstring for the physics of every choice).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from _workbook import hourly_index, write_standin

from camber.model.roles import Role

# --------------------------------------------------------------------------- lbnl-fpu

#: the default subset's runs and their catalog fault types (labels.fault_types)
FPU_LABELS = {
    "PFPU__fault_free": "",
    "PFPU__VAVDMPRStuck_50pct": "terminal_damper",
    "PFPU__VAVDMPRStuck_100pct": "terminal_damper",
    "PFPU__ReheatVLVStuck_0pct": "valve_stuck",
    "PFPU__ReheatVLVLeak_50pctMaxFlow": "valve_leak",
}


def _fpu_box(idx: pd.DatetimeIndex, fault: str) -> pd.DataFrame:
    """The South-zone parallel fan-powered box of one run, hourly.

    Occupied weekdays 06-18 (heating setpoint 68 F, cooling 72 F; 55/85 F set back), primary air
    at 55 F. Two cold weeks, then two mild ones. The fault-free box sits at its 201 cfm minimum
    (about half its 420 cfm peak) nearly all day, so the zone is overcooled below its cooling
    setpoint at minimum flow; on cold mornings it reheats lightly (valve 15-20 %) at minimum flow.

    - ``VAVDMPRStuck_*``: the damper is fixed, so airflow no longer follows its setpoint (about
      480 cfm half open, 770 cfm fully open, against a 201 cfm setpoint); the extra cold air is
      reheated all day (valve 25-60 %), and fully open the box cannot hold the heating setpoint on
      cold mornings even with the valve near full (the zone falls to 66 F).
    - ``ReheatVLVStuck_0pct``: the valve is shut whatever the controller asks, so on cold days the
      controller's demand (``HEAT_VALVE``) sits at 100 % while the zone falls to 62 F at the right
      airflow -- a capacity shortfall -- and the measured position (``HEAT_VALVE_POSITION``) stays
      at 0 %. In every other run the position follows the demand (as in the real data). The box
      fan still mixes in warm plenum air, so the discharge sits about 7 F above the primary air
      with the valve shut.
    - ``ReheatVLVLeak_*``: a passing valve adds a little heat (discharge ~4 F over the primary air
      with the valve shut), enough for the light morning load, so the controller hardly opens
      it; the zone stays in band.
    """
    n = len(idx)
    hour = idx.hour.to_numpy()
    day = np.arange(n) // 24
    occ = np.asarray(idx.dayofweek < 5) & (hour >= 6) & (hour < 18)
    cold = day < 14
    heat_sp = np.where(occ, 68.0, 55.0)
    cool_sp = np.where(occ, 72.0, 85.0)
    primary = np.where(occ, 55.0, 54.0)
    static = np.where(occ, 1.5, 0.5)
    afternoon = (hour >= 11) & (hour < 16)
    flow_sp = np.where(occ, 201.0 + np.where(~cold & afternoon, 40.0, 0.0), 60.0)
    morning = occ & cold & (hour >= 6) & (hour < 9)
    valve = np.where(morning, np.where(hour == 8, 8.0, 16.0), 0.0)
    space = np.where(occ, np.where(cold, 68.6, 70.4), 65.0) + np.where(morning, 0.0, 0.3)
    flow = flow_sp.copy()
    damper = flow / 8.4
    rise = 0.7 * valve  # a hot-water coil lifts discharge ~11 F at 16 % valve and minimum flow
    position = None  # the measured valve position: the demand, unless the valve is stuck
    if fault.startswith("VAVDMPRStuck"):
        wide = fault.endswith("100pct")
        damper = np.full(n, 100.0 if wide else 50.0)
        flow = np.where(occ, 770.0 if wide else 480.0, 400.0 if wide else 250.0)
        if wide:
            valve = np.where(occ, np.where(morning, 85.0, np.where(cold, 60.0, 45.0)), 0.0)
            space = np.where(occ, np.where(morning, 66.0, 68.0), 64.0)
        else:
            valve = np.where(occ, np.where(cold, 40.0, np.where(afternoon, 0.0, 20.0)), 0.0)
            space = np.where(occ, 68.3, 64.5)
        rise = np.where(valve > 0, 11.0, 0.0)
    elif fault == "ReheatVLVStuck_0pct":
        valve = np.where(occ, np.where(cold, 100.0, np.where(afternoon, 0.0, 30.0)), 0.0)
        position = np.zeros(n)
        space = np.where(occ, np.where(cold, 62.0, 68.4), 62.0)
        rise = np.where(occ, 7.0, 0.0)
    elif fault.startswith("ReheatVLVLeak"):
        valve = np.zeros(n)  # the passing water's heat covers the light morning load
        rise = np.where(occ, 4.0, 2.0)
        space = space + 0.2
    return pd.DataFrame(
        {
            Role.DAMPER: damper,
            Role.AIRFLOW_SP: flow_sp,
            Role.AIRFLOW: flow,
            Role.HEAT_VALVE: valve,
            Role.HEAT_VALVE_POSITION: valve if position is None else position,
            Role.SUPPLY_AIR_TEMP: primary + rise,
            Role.MIXED_AIR_TEMP: primary,
            Role.DUCT_STATIC: static,
            Role.SPACE_TEMP: space,
            Role.COOL_SP: cool_sp,
            Role.HEAT_SP: heat_sp,
        },
        index=idx,
    )


def fpu_standin(store) -> None:
    """ds-lbnl-fpu with the default subset's five runs (the South-zone box of each)."""
    idx = hourly_index(days=28)
    frames = {eq: ("VAV", _fpu_box(idx, eq.split("__", 1)[1])) for eq in FPU_LABELS}
    write_standin(store, "lbnl-fpu", frames, labels=FPU_LABELS)


# --------------------------------------------------------------------------- ornl-frp-vav

ORNL_ROOMS = ("102", "103", "104", "105", "106", "202", "203", "204", "205", "206")
#: the stand-in keeps six of the ten boxes (enough for a cohort, and a fast test)
STANDIN_ROOMS = ("102", "104", "105", "106", "205", "206")


def ornl_rooms(ctx) -> tuple:
    """The rooms whose boxes a check can read: all ten on the real data, six on the stand-in."""
    return ORNL_ROOMS if ctx.mode == "real" else STANDIN_ROOMS


#: the default subset's scenarios (set 3: the box of room 205 is under test)
ORNL_SCENARIOS = (
    "fault_free",
    "stuck_000",
    "stuck_020",
    "stuck_040",
    "stuck_060",
    "stuck_080",
    "stuck_100",
)
ORNL_LABELS = {
    f"RTU_VAV_205__d3_{sc}": ("" if sc == "fault_free" else "terminal_damper")
    for sc in ORNL_SCENARIOS
}
# a healthy box's design airflow (cfm) at a fully open damper: the rooms differ in size
_ORNL_VMAX = dict(zip(ORNL_ROOMS, (290, 530, 760, 820, 760, 520, 360, 890, 560, 780)))


def _ornl_day(sc: str, day: int) -> dict:
    """One one-day scenario: the rooftop unit and six of its ten boxes, at 15 minutes.

    Occupied 07-22 (heating setpoint 69.8 F, cooling 75.2 F; 60/85 F set back), one day per
    scenario on its own date. A healthy box modulates its damper around 40 % and its room sits in
    band -- except room 102, a cold room that sits about 2 F below its heating setpoint for part
    of every day (the reheat is electric and not trended, so nothing says whether it was maxed).
    Room 205 is the box under test and the building's warm room: on the fault-free day its
    afternoon runs a few degrees over the cooling setpoint. Stuck at 20 or 40 % its airflow is
    capped and the afternoon runs hotter (a cool morning also runs cold at 20 %); stuck shut on a
    cool day it delivers almost no air, yet the room stays in band; stuck 60-100 % it delivers more
    air than any box and the room sits comfortably in band. The rooftop unit's supply airflow is
    the sum of its boxes, and its duct static falls as the stuck box opens (a fixed-speed fan
    moving more air through a more open system).
    """
    idx = pd.date_range(
        pd.Timestamp("2023-12-04") + pd.Timedelta(days=day), periods=96, freq="15min"
    )
    h = idx.hour.to_numpy() + idx.minute.to_numpy() / 60.0
    occ = (h >= 7) & (h < 22)
    heat_sp = np.where(occ, 69.8, 60.0)
    cool_sp = np.where(occ, 75.2, 85.0)
    stuck = None if sc == "fault_free" else float(sc.split("_")[1])
    hot_pm = occ & (h >= 12) & (h < 17)  # the south room's afternoon solar gain
    frames = {}
    total = np.zeros(len(idx))
    for room in STANDIN_ROOMS:
        eq = f"RTU_VAV_{room}__d3_{sc}"
        damper = np.where(occ, 40.0 + 2.0 * np.sin(h), 0.0)
        space = np.where(occ, 71.0 + 0.3 * np.cos(h), 68.0)
        if room == "102":
            space = np.where(occ & (h < 13), 67.8, space)
        if room == "205":
            if stuck is None:
                space = np.where(hot_pm, 77.5, space)
            elif stuck in (20.0, 40.0):
                space = np.where(hot_pm | (occ & (h >= 17) & (h < 19)), 78.5, space)
                if stuck == 20.0:
                    space = np.where(occ & (h < 9.5), 67.5, space)
            damper = damper if stuck is None else np.full(len(idx), stuck)
        flow = np.where(occ | (damper > 0), _ORNL_VMAX[room] * damper / 100.0, np.nan)
        if room == "205" and stuck is not None:
            flow = _ORNL_VMAX[room] * (0.05 + stuck / 100.0)
            flow = np.full(len(idx), flow)
        total += np.nan_to_num(np.where(occ, flow, 0.0))
        frames[eq] = (
            "VAV",
            pd.DataFrame(
                {
                    Role.AIRFLOW: np.where(occ, flow, np.nan),
                    Role.DAMPER: damper,
                    Role.SPACE_TEMP: space,
                    Role.COOL_SP: cool_sp,
                    Role.HEAT_SP: heat_sp,
                    Role.OCCUPANCY: occ.astype(float),
                    Role.SUPPLY_AIR_TEMP: np.where(occ, 58.0, np.nan),
                },
                index=idx,
            ),
        )
    oat = 45.0 + 8.0 * np.sin((h - 9.0) / 24.0 * 2.0 * np.pi)
    fan = occ.astype(float)
    frames[f"RTU__d3_{sc}"] = (
        "AHU",
        pd.DataFrame(
            {
                Role.SUPPLY_AIR_TEMP: np.where(occ, 57.8, 66.0),
                Role.RETURN_AIR_TEMP: np.full(len(idx), 68.2),
                Role.MIXED_AIR_TEMP: np.where(occ, 62.0, 67.0),
                Role.OAT: oat,
                Role.OA_DAMPER: np.where(occ, 30.0, 0.0),
                Role.AIRFLOW: np.where(occ, total, 0.0),
                Role.DUCT_STATIC: np.where(occ, 1.6 - total / 5000.0, 0.0),
                Role.SUPPLY_FAN_STATUS: fan,
                Role.OCCUPANCY: fan,
            },
            index=idx,
        ),
    )
    return frames


def ornl_standin(store) -> None:
    """ds-ornl-frp-vav with the default subset's seven one-day scenarios of set 3."""
    frames: dict = {}
    for i, sc in enumerate(ORNL_SCENARIOS):
        frames.update(_ornl_day(sc, i))
    write_standin(store, "ornl-frp-vav", frames, labels=ORNL_LABELS)
