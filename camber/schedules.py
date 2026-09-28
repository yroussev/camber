"""Day-typing / occupancy classification.

Classifying each interval by day type and occupied/unoccupied lets every
diagnostic share one consistent filter instead of re-deriving occupancy. We
provide:

- ``occupied_mask`` -- weekday daytime window, optionally AND-ed with a BAS
  occupancy point and minus warm-up/cool-down prep modes.
- ``day_type`` -- 'weekday' / 'weekend'.
- ``time_of_week_bin`` -- integer bin (day-of-week * 24 + hour), the natural
  x-axis for time-of-week load/behavior charts.

Pure functions over a DatetimeIndex so they are trivially testable.
"""

from __future__ import annotations

import pandas as pd

__all__ = [
    "occupied_mask",
    "effective_occupied_mask",
    "fan_on_mask",
    "FAN_GATE_NONE",
    "plant_run_mask",
    "PLANT_GATE_NONE",
    "CHILLER_POWER_RUN_FRAC",
    "GAS_FIRING_FRAC",
    "day_type",
    "time_of_week_bin",
]


def occupied_mask(
    index,
    *,
    start_hour=7,
    end_hour=18,
    occ=None,
    warmup=None,
    cooldown=None,
    days=(0, 1, 2, 3, 4),
):
    """Boolean Series: is each interval occupied?

    ``days`` (0 = Monday … 6 = Sunday; default Mon-Fri) within [start_hour, end_hour). If
    ``occ`` (a BAS occupancy Series) is given it is AND-ed in. WarmUp/CoolDown Series, if given,
    exclude unoccupied-prep intervals. For a 24/7 space pass ``start_hour=0, end_hour=24,
    days=range(7)``.

    The default 07:00-18:00 weekday window is a generic office occupancy
    assumption, NOT site-specific -- pass start_hour/end_hour (or a real ``occ``
    point) to match the actual schedule. This should become per-site config as the
    tool generalizes beyond one building.
    """
    hour = index.hour + index.minute / 60.0
    m = pd.Series(
        index.dayofweek.isin(list(days)) & (hour >= start_hour) & (hour < end_hour), index=index
    )
    if occ is not None:
        m = m & (occ.reindex(index).fillna(0) > 0.5)
    for flag in (warmup, cooldown):
        if flag is not None:
            m = m & ~(flag.reindex(index).fillna(0) > 0.5)
    return m


def effective_occupied_mask(
    index,
    *,
    occ=None,
    start_hour=7,
    end_hour=18,
    days=(0, 1, 2, 3, 4),
    warmup=None,
    cooldown=None,
):
    """Occupied samples, preferring a **trended** occupancy point over the assumed schedule.

    When ``occ`` (a BAS occupied/unoccupied Series with at least one non-null value) is given it
    *replaces* the ``start_hour``/``end_hour``/``days`` schedule -- it is the building's truth, so a
    07-22 every-day building is not cut to the default weekday office window. Otherwise the
    schedule applies (defaults: Mon-Fri 07:00-18:00, a generic assumption). WarmUp/CoolDown flags
    exclude prep intervals either way.
    """
    if occ is not None and pd.Series(occ).notna().any():
        return occupied_mask(
            index,
            start_hour=0,
            end_hour=24,
            days=range(7),
            occ=occ,
            warmup=warmup,
            cooldown=cooldown,
        )
    return occupied_mask(
        index,
        start_hour=start_hour,
        end_hour=end_hour,
        days=days,
        warmup=warmup,
        cooldown=cooldown,
    )


def day_type(index):
    """Series of 'weekday' / 'weekend' per interval."""
    return pd.Series(["weekend" if d >= 5 else "weekday" for d in index.dayofweek], index=index)


def time_of_week_bin(index):
    """Integer time-of-week bin: dayofweek*24 + hour (0..167)."""
    return pd.Series(index.dayofweek * 24 + index.hour, index=index)


#: The gate label reported when a unit trends no fan signal at all (samples are not fan-gated).
FAN_GATE_NONE = "ungated — no fan signal"


def fan_on_mask(frame: pd.DataFrame, *, speed_min_pct: float = 1.0, flow_frac: float = 0.05):
    """``(mask, source)``: which samples the supply fan was running, and what that was read from.

    Preference order, strongest evidence first:

    * ``supply_fan_status`` > 0.5 -> source ``"fan status"``;
    * ``supply_fan_speed`` above ``speed_min_pct`` % (0-1 or 0-100 accepted) -> ``"fan speed
      proxy"``;
    * ``airflow`` above ``flow_frac`` of its own 95th percentile -> ``"airflow proxy"`` (a small
      floor so transmitter noise at zero flow is not read as running).

    A signal that is present but never non-null is skipped. With none of the three, returns ``(None,
    FAN_GATE_NONE)`` -- callers then run ungated and must say so. Missing samples of the chosen
    signal count as *off* (not evidence of running). ``frame`` columns are :class:`Role` members or
    their string values.
    """
    from .model.roles import Role
    from .units import normalize_percent

    def _get(role):
        for key in (role, role.value):
            if key in frame.columns:
                s = pd.to_numeric(frame[key], errors="coerce")
                return s if s.notna().any() else None
        return None

    status = _get(Role.SUPPLY_FAN_STATUS)
    if status is not None:
        return (status > 0.5).fillna(False), "fan status"
    speed = _get(Role.SUPPLY_FAN_SPEED)
    if speed is not None:
        return (normalize_percent(speed) > speed_min_pct).fillna(False), "fan speed proxy"
    flow = _get(Role.AIRFLOW)
    if flow is not None:
        p95 = float(flow.quantile(0.95))
        if p95 > 0:
            return (flow > flow_frac * p95).fillna(False), "airflow proxy"
    return None, FAN_GATE_NONE


# --------------------------------------------------------------------------------------------- #
# 092-plant (#66): the plant run gate                                                            #
# --------------------------------------------------------------------------------------------- #

#: The gate label reported when a plant frame trends no run signal for that loop.
PLANT_GATE_NONE = "ungated — no plant run signal"

#: Share of its own 95th percentile a chiller's power must exceed to read as running. A stopped
#: chiller still draws controls, oil-heater and crankcase power; a tenth of its working draw is
#: above that standby load on the machines we have seen and well below its lightest running load.
CHILLER_POWER_RUN_FRAC = 0.10
#: Share of its own 95th percentile a boiler's gas input must exceed to read as firing (a pilot or a
#: purge reads a few percent).
GAS_FIRING_FRAC = 0.05

_AIR_FAN_ROLES = ("supply_fan_status", "supply_fan_speed")


def plant_run_mask(
    frame: pd.DataFrame,
    loop: str,
    *,
    power_frac: float = CHILLER_POWER_RUN_FRAC,
    gas_frac: float = GAS_FIRING_FRAC,
):
    """``(mask, source)``: which samples the plant equipment serving ``loop`` was running.

    Provisional (0.92, #66). ``loop`` is ``"chw"`` (a chiller: chilled- and condenser-water points)
    or ``"hw"`` (a boiler: hot-water points). Strongest evidence first:

    * ``"chw"``: the chiller's run status / command (``compressor_status``) > 0.5 -> ``"chiller
      status"``; else its electric ``power`` above ``power_frac`` of its own 95th percentile ->
      ``"chiller power proxy"``. Power is read as the chiller's only on a frame that carries a
      chilled-water role and no supply-fan signal (on an air handler it is the fan's).
    * ``"hw"``: the boiler's run status (``boiler_status``) > 0.5 -> ``"boiler status"``; else its
      gas input rate above ``gas_frac`` of its own 95th percentile -> ``"boiler firing (gas
      input)"``.

    A resampled status is a duty fraction: the equipment counts as running in an interval when it
    ran for more than half of it, as for the fan gate. Missing samples count as *not running*.
    Returns ``(None, PLANT_GATE_NONE)`` when no signal is present.
    """
    from .model.roles import Role

    def _get(role):
        for key in (role, role.value):
            if key in frame.columns:
                s = pd.to_numeric(frame[key], errors="coerce")
                return s if s.notna().any() else None
        return None

    def _above(s, frac):
        p95 = float(s.quantile(0.95))
        if not p95 > 0:
            return None
        return (s > frac * p95).fillna(False)

    if loop == "chw":
        status = _get(Role.COMPRESSOR_STATUS)
        if status is not None:
            return (status > 0.5).fillna(False), "chiller status"
        cols = {getattr(c, "value", c) for c in frame.columns}
        chw = {Role.CHW_SUPPLY_TEMP.value, Role.CHW_RETURN_TEMP.value, Role.CHW_FLOW.value}
        power = _get(Role.POWER)
        if power is not None and cols & chw and not cols.intersection(_AIR_FAN_ROLES):
            m = _above(power, power_frac)
            if m is not None:
                return m, "chiller power proxy"
        return None, PLANT_GATE_NONE
    if loop == "hw":
        status = _get(Role.BOILER_STATUS)
        if status is not None:
            return (status > 0.5).fillna(False), "boiler status"
        gas = _get(Role.GAS_INPUT_RATE)
        if gas is not None:
            m = _above(gas, gas_frac)
            if m is not None:
                return m, "boiler firing (gas input)"
        return None, PLANT_GATE_NONE
    raise ValueError(f"loop must be 'chw' or 'hw', got {loop!r}")
