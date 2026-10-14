"""Synthetic AHU maintenance entry: a dirty filter and a hunting valve (0.105, #146).

One constant-volume air handler on a daily schedule, every 5 minutes for two weeks of early
summer, as five units that share the weather and the schedule: a healthy unit, a filter that
loads up through the fortnight and crosses its change-out pressure part-way, a filter left far
past it, a cooling valve whose PI loop hunts, and one that oscillates a little but not enough to
call. No open dataset shows a fouled filter or a hunting loop, and a hunting loop needs a trend
sampled every few minutes: at 15 minutes it cannot be seen at all.

See :mod:`camber.datasets._synthetic` for how a synthetic entry is generated and ingested.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ._synthetic import Generator, _noise, _smooth, register

_START = "2025-06-02"  # a Monday
_DAYS = 14
_STEP_MIN = 5
_PER_DAY = 24 * 60 // _STEP_MIN
#: the unit runs every day 05:00-21:00 (a building open seven days a week)
_ON_HOURS = (5, 21)
_SAT_SP_F = 55.0

#: scenario -> (filter DP at the start, filter DP at the end, in. w.c. with the fan running;
#: loading exponent: 1 is linear, larger loads late; cooling-valve oscillation period in minutes
#: and amplitude in % of stroke, 0 for a loop that settles)
_UNITS = {
    "healthy": (0.42, 0.48, 1.0, 0.0, 0.0),
    "loading": (0.55, 1.60, 1.4, 0.0, 0.0),
    "dirty": (1.62, 1.82, 1.0, 0.0, 0.0),
    "hunting": (0.50, 0.56, 1.0, 11.0, 22.0),
    "near_miss": (0.46, 0.52, 1.0, 22.0, 9.0),
}


def _ahu_maintenance(seed: int) -> dict:
    """One CSV, 5-minute, the five units stacked with a ``scenario`` column."""
    idx = pd.date_range(_START, periods=_DAYS * _PER_DAY, freq=f"{_STEP_MIN}min")
    n = len(idx)
    hour = np.asarray(idx.hour) + np.asarray(idx.minute) / 60.0
    day = np.arange(n) / _PER_DAY
    diurnal = 9.0 * np.sin((hour - 9.0) / 24.0 * 2.0 * np.pi)
    swing = 5.0 * _smooth(_noise(n, seed, 1), _PER_DAY * 3)
    jitter = 1.0 * (_noise(n, seed, 2) - 0.5)
    oat = np.round(70.0 + 4.0 * day / _DAYS + diurnal + swing + jitter, 1)
    on = (hour >= _ON_HOURS[0]) & (hour < _ON_HOURS[1])
    # the cooling the unit's coil must deliver, as a valve position (%): outdoor air and the
    # building's internal gains, following the weather slowly
    base = np.clip(20.0 + 2.2 * (oat - 62.0), 8.0, 92.0)
    base = base + 3.0 * _smooth(_noise(n, seed, 3), 12)
    frames = []
    for k, (scenario, spec) in enumerate(_UNITS.items()):
        dp0, dp1, expo, period, amp = spec
        frac = day / _DAYS
        dp_run = dp0 + (dp1 - dp0) * frac**expo
        dp_run = dp_run + 0.03 * (_noise(n, seed, 20 + k) - 0.5)
        # a transmitter with the fan off reads about zero
        dp = np.where(on, dp_run, 0.02 * _noise(n, seed, 30 + k))
        valve = base.copy()
        if amp > 0.0:
            # a PI loop tuned too hot: a limit cycle around the steady position, its period and
            # amplitude wandering a little from hour to hour
            wander = 1.0 + 0.12 * _smooth(_noise(n, seed, 40 + k), 24)
            phase = np.cumsum(2.0 * np.pi * _STEP_MIN / (period * wander))
            phase = phase + 2.0 * np.pi * _noise(1, seed, 50 + k)[0]
            size = amp * (1.0 + 0.2 * _smooth(_noise(n, seed, 60 + k), 36))
            valve = valve + size * np.sin(phase)
        valve = valve + 1.2 * (_noise(n, seed, 70 + k) - 0.5)
        valve = np.where(on, np.clip(valve, 0.0, 100.0), 0.0)
        # the supply air: on setpoint when the loop settles, swinging against the valve when it
        # hunts (lagging it), drifting toward the space with the fan off
        sat = _SAT_SP_F + 0.4 * (_noise(n, seed, 80 + k) - 0.5)
        if amp > 0.0:
            sat = sat - 0.12 * (np.roll(valve, 1) - base)
        sat = np.where(on, sat, 74.0 + 0.04 * (oat - 70.0) * 10.0)
        frames.append(
            pd.DataFrame(
                {
                    "Timestamp": idx.strftime("%Y-%m-%d %H:%M"),
                    "scenario": scenario,
                    "OAT": oat,
                    "SF_STS": on.astype(int),
                    "SAT": np.round(sat, 1),
                    "CLG_VLV": np.round(valve, 1),
                    "FLT_DP": np.round(dp, 2),
                }
            )
        )
    text = pd.concat(frames, ignore_index=True).to_csv(index=False, lineterminator="\n")
    return {"ahu_maintenance.csv": text}


register(
    "ahu_maintenance",
    Generator(
        version=1,
        files=("ahu_maintenance.csv",),
        build=_ahu_maintenance,
        description=(
            "a constant-volume air handler (OAT, supply fan status, supply-air temperature, "
            "cooling valve, filter differential pressure), every 5 minutes for two weeks of "
            "early summer on a daily 05:00-21:00 schedule, as five units: a healthy unit, a "
            "filter that loads up and crosses its change-out pressure part-way, a filter far "
            "past it, a cooling valve whose PI loop hunts, and one that oscillates a little"
        ),
    ),
)
