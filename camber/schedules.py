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
