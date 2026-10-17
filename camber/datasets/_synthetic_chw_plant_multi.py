"""Synthetic two-chiller chilled-water plant with a secondary loop (0.105, #145).

The ``chw_plant_multi`` generator of the ``synthetic-chw-plant-multi`` catalog entry: one
primary-secondary chilled-water plant -- two equal chillers on constant primary flow, a decoupler,
a variable-flow secondary loop and one cooling tower -- every 15 minutes for the three summer weeks
of :func:`~camber.datasets._synthetic._staging_weather` (a heat wave in the third), as seven
plants that differ in one way each:

* ``well_run``: the lag chiller stages on above 90 % of one chiller's capacity and off again
  below 75 %; the coils return water 10-13 °F warmer than they get it; a clean tower resets its
  leaving water to the wet-bulb plus 7 °F (65-85 °F); clean condensers.
* ``over_staged``: both chillers run whenever the plant does, whatever the load.
* ``reverse_mixing``: low delta-T coils (3-4 °F) make the secondary pumps move more water than
  one chiller's primary flow, so secondary return water flows backwards through the decoupler
  and warms the secondary supply. The chillers themselves hold their setpoint.
* ``fouled_condenser``: chiller 1's condenser tubes are scaled: its condenser approach is about
  twice design, and it draws more power for the same cooling.
* ``tower_full_fan``: a badly fouled tower that cannot reach its setpoint even at full fan, so
  its fan is pinned at 100 % and its approach is far above design.
* ``tower_controlled``: a moderately fouled tower that still makes its setpoint most of the time,
  by running its fan harder: the approach barely moves, the fan effort does.
* ``near_miss``: a plant a little off everywhere that passes every check: the lag chiller stages
  on early, chiller 1's condenser approach is 1.38 times design, the coils return 6-8 °F.

Each plant is four pieces of equipment in the CSV's columns: ``CH1_*`` and ``CH2_*`` (each
chiller's run status, power, leaving and entering water, condenser and evaporator approach),
``SEC_*`` (the secondary loop's supply and return, flow, pump speed and pump power; the plant's
supply setpoint ``CHWS_SP`` is shared), and the tower's ``CWS_T``, ``CWR_T``, ``CW_GPM`` and
``CT_FAN``, with the outdoor ``OAT`` and ``WB``. The approaches are the chiller controller's own
refrigerant-side readings, emitted directly. Off samples carry a 0 (status, power, flow, fan) or a
blank (the approaches and the condenser water temperatures).

Each running chiller has its own constant-speed condenser pump (600 gpm), so the tower's
condenser-water flow ``CW_GPM`` (version 2, 0.106) steps from 600 to 1200 gpm when the lag chiller
stages on, read through a flow meter about 1 % noisy. With both chillers on, the same heat
rejected spreads over twice the water and the tower's range halves: a rule that matched the
tower's fan effort on the range alone read that as the tower working harder.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ._synthetic import Generator, _noise, _occupied, _smooth, _staging_weather, register

__all__: list = []

_START = "2025-07-07"  # a Monday; the clock of _staging_weather (its heat wave is days 14-19)
_DAYS = 21
_DESIGN_TONS = 380.0  # the building's design cooling load
_CH_TONS = 200.0  # each chiller's capacity
_PRI_GPM = 480.0  # constant primary flow per running chiller (200 tons at a 10 °F delta-T)
_CW_GPM = 600.0  # condenser water per running chiller
_CHWS_SP = 44.0
_CW_FLOOR, _CW_CEIL, _CW_APPROACH = 65.0, 85.0, 7.0  # the tower's reset: wet-bulb + 7, 65-85 °F
_FAN_MIN = 0.2  # the tower fan's lowest speed (fraction)

#: scenario -> (lag-chiller stage-on tons or None for both always on, stage-off tons, secondary
#: delta-T at no load and its rise to full load (°F), chiller 1's condenser fouling (°F added at
#: half load), tower fouling (the multiple of a clean tower's full-fan approach)).
_PLANTS = {
    "well_run": (180.0, 150.0, (10.0, 3.0), 0.0, 1.0),
    "over_staged": (None, None, (10.0, 3.0), 0.0, 1.0),
    "reverse_mixing": (180.0, 150.0, (3.0, 1.0), 0.0, 1.0),
    "fouled_condenser": (180.0, 150.0, (10.0, 3.0), 10.0, 1.0),
    "tower_full_fan": (180.0, 150.0, (10.0, 3.0), 0.0, 3.0),
    "tower_controlled": (180.0, 150.0, (10.0, 3.0), 0.0, 1.4),
    "near_miss": (120.0, 90.0, (6.0, 2.0), 6.0, 1.0),
}


def _stage(tons: np.ndarray, on: np.ndarray, up, down) -> np.ndarray:
    """Chillers running per step: the lead whenever the plant is on; the lag after the load has
    been above ``up`` for 30 minutes, until it has been below ``down`` for an hour (``up`` None:
    both whenever the plant is on)."""
    n = np.zeros(len(tons), dtype=int)
    if up is None:
        return np.where(on, 2, 0)
    count, above, below = 1, 0, 0
    for i in range(len(tons)):
        if not on[i]:
            count, above, below = 1, 0, 0
            continue
        above = above + 1 if tons[i] > up else 0
        below = below + 1 if tons[i] < down else 0
        if count == 1 and above >= 2:
            count, below = 2, 0
        elif count == 2 and below >= 4:
            count, above = 1, 0
        n[i] = count
    return n


def _chw_plant_multi(seed: int) -> dict:
    """One CSV, 15-minute, the seven plants stacked with a ``scenario`` column."""
    idx = pd.date_range(_START, periods=_DAYS * 96, freq="15min")
    n = len(idx)
    oat, wb = _staging_weather(seed, idx)
    on = _occupied(idx, 6, 19)
    hour = np.asarray(idx.hour) + np.asarray(idx.minute) / 60.0
    # the building's load, a fraction of design: weather and people while the plant runs, a
    # morning pull-down in its first hour
    frac = 0.12 + 0.78 * np.clip((oat - 76.0) / 20.0, 0.0, 1.0)
    frac = frac + np.where(on & (hour < 7.0), 0.12, 0.0)
    frac = np.clip(frac * (1.0 + 0.08 * _smooth(_noise(n, seed, 3), 8)), 0.05, 1.0)
    tons = np.where(on, frac * _DESIGN_TONS, 0.0)
    frames = []
    for k, (scenario, spec) in enumerate(_PLANTS.items()):
        up, down, (dt0, dt1), cond_foul, tower_foul = spec
        jit = [_noise(n, seed, 100 * (k + 1) + s) - 0.5 for s in range(9)]
        count = _stage(tons, on, up, down)
        prim = _PRI_GPM * count
        # secondary loop: the coils' delta-T sets the flow the pumps must move
        sec_dt = dt0 + dt1 * frac
        sec = np.where(on, tons * 24.0 / sec_dt, 0.0)
        excess = np.maximum(sec - prim, 0.0)  # backwards through the decoupler
        mix = np.divide(excess * sec_dt, prim, out=np.zeros(n), where=prim > 0)
        sec_s = np.where(on, _CHWS_SP + mix + 0.3 * jit[0], 58.0 + 2.0 * jit[0])
        sec_r = np.where(on, sec_s + sec_dt, sec_s + 0.5)
        speed = np.where(on, np.clip(30.0 + 65.0 * sec / 900.0, 30.0, 100.0) + jit[1], 0.0)
        pump_kw = np.where(on, 1.0 + 24.0 * (speed / 100.0) ** 2.5, 0.0)
        ewt = np.where(count > 0, _CHWS_SP + tons * 24.0 / np.maximum(prim, 1.0), np.nan)
        # the tower first (the condenser water sets the chillers' lift), from last step's heat
        each = np.divide(tons, count, out=np.zeros(n), where=count > 0)
        plr = each / _CH_TONS
        kw0 = 120.0 * (0.18 + 0.62 * plr + 0.20 * plr**2)
        rej = tons + 0.2843 * kw0 * count  # heat rejected, tons
        q = np.clip(rej / 454.0, 0.0, 1.2)
        app_full = (2.5 + 4.5 * q) * tower_foul  # the tower's approach at full fan
        target = np.clip(wb + _CW_APPROACH, _CW_FLOOR, _CW_CEIL)
        want = target - wb
        fan = np.clip(1.0 - (want / app_full - 1.0) / 1.2, _FAN_MIN, 1.0)
        approach = app_full * (1.0 + 1.2 * (1.0 - fan))
        running = count > 0
        cws = np.where(running, wb + approach + 0.3 * jit[2], np.nan)
        cw_gpm = _CW_GPM * np.maximum(count, 1)
        cwr = cws + rej * 24.0 / cw_gpm
        fan_pct = np.where(running, np.clip(100.0 * fan + 0.8 * jit[3], 0.0, 100.0), 0.0)
        # the condenser pumps (one per running chiller), through a flow meter about 1 % noisy
        cw_flow = np.where(running, _CW_GPM * count * (1.0 + 0.02 * jit[8]), 0.0)
        cols = {
            "Timestamp": idx.strftime("%Y-%m-%d %H:%M"),
            "scenario": scenario,
            "OAT": oat,
            "WB": wb,
        }
        for c in (1, 2):
            run = count >= c
            foul = cond_foul if c == 1 else 0.0
            cond = 1.5 + 2.5 * plr + foul * (0.5 + 0.5 * plr) + 0.2 * jit[3 + c]
            evap = 1.0 + 1.5 * plr + 0.2 * jit[5 + c]
            lift = (
                1.0
                + 0.015 * (np.nan_to_num(cws, nan=75.0) - 75.0)
                + 0.02 * foul * (0.5 + 0.5 * plr)
            )
            kw = kw0 * lift
            idle = np.where(on, sec_r, 58.0 + 2.0 * jit[0])  # an isolated barrel at loop return
            cols[f"CH{c}_STS"] = run.astype(int)
            cols[f"CH{c}_KW"] = np.where(run, np.round(kw, 1), 0.0)
            cols[f"CH{c}_LWT"] = np.round(np.where(run, _CHWS_SP + 0.3 * jit[c], idle), 1)
            cols[f"CH{c}_EWT"] = np.round(np.where(run, ewt + 0.3 * jit[c], idle), 1)
            cols[f"CH{c}_COND_APP"] = np.where(run, np.round(cond, 1), np.nan)
            cols[f"CH{c}_EVAP_APP"] = np.where(run, np.round(evap, 1), np.nan)
        cols.update(
            {
                "CHWS_SP": np.full(n, _CHWS_SP),
                "SEC_SWT": np.round(sec_s, 1),
                "SEC_RWT": np.round(sec_r, 1),
                "SEC_GPM": np.round(sec, 0),
                "SEC_PUMP_SPD": np.round(speed, 1),
                "SEC_PUMP_KW": np.round(pump_kw, 1),
                "CWS_T": np.round(cws, 1),
                "CWR_T": np.round(cwr, 1),
                "CW_GPM": np.round(cw_flow, 0),
                "CT_FAN": np.round(fan_pct, 1),
            }
        )
        frames.append(pd.DataFrame(cols))
    text = pd.concat(frames, ignore_index=True).to_csv(
        index=False, float_format="%.1f", lineterminator="\n"
    )
    return {"chw_plant_multi.csv": text}


register(
    "chw_plant_multi",
    Generator(
        version=2,
        files=("chw_plant_multi.csv",),
        build=_chw_plant_multi,
        description=(
            "a primary-secondary chilled-water plant with two chillers, a decoupler, a "
            "variable-flow secondary loop and one cooling tower (OAT, wet-bulb; each chiller's "
            "status, power, leaving and entering water, condenser and evaporator approach; the "
            "secondary supply, return, setpoint, flow, pump speed and power; the tower's "
            "leaving and return water, condenser-water flow and fan speed), every 15 minutes "
            "for three summer weeks with a heat wave, as seven plants: well run, both chillers "
            "always on, decoupler reverse mixing from low delta-T coils, a fouled condenser on "
            "chiller 1, a fouled tower pinned at full fan, a fouled tower that holds its "
            "setpoint by fan effort, and a near miss"
        ),
    ),
)
