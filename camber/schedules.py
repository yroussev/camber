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


def occupied_mask(index, *, start_hour=7, end_hour=18, occ=None,
                  warmup=None, cooldown=None):
    """Boolean Series: is each interval occupied?

    Weekday (Mon-Fri) within [start_hour, end_hour). If ``occ`` (a BAS occupancy
    Series) is given it is AND-ed in. WarmUp/CoolDown Series, if given, exclude
    unoccupied-prep intervals.

    The default 07:00-18:00 weekday window is a generic office occupancy
    assumption, NOT site-specific -- pass start_hour/end_hour (or a real ``occ``
    point) to match the actual schedule. This should become per-site config as the
    tool generalizes beyond one building.
    """
    hour = index.hour + index.minute / 60.0
    m = pd.Series((index.dayofweek < 5) & (hour >= start_hour) & (hour < end_hour),
                  index=index)
    if occ is not None:
        m = m & (occ.reindex(index).fillna(0) > 0.5)
    for flag in (warmup, cooldown):
        if flag is not None:
            m = m & ~(flag.reindex(index).fillna(0) > 0.5)
    return m


def day_type(index):
    """Series of 'weekday' / 'weekend' per interval."""
    return pd.Series(["weekend" if d >= 5 else "weekday" for d in index.dayofweek],
                     index=index)


def time_of_week_bin(index):
    """Integer time-of-week bin: dayofweek*24 + hour (0..167)."""
    return pd.Series(index.dayofweek * 24 + index.hour, index=index)
