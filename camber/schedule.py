"""Infer the actual operating schedule from interval data.

What hours does the building *actually* run, versus what the schedule claims? Inferring the real
weekly on/off pattern from an interval load (or fan-status) series drives setback verification,
demand-response eligibility, and onboarding (a detected schedule seeds the occupancy model). The
method is transparent: mark each interval "on" when it exceeds a threshold (default midway between
robust base and peak), then take, for each hour-of-week, the majority state across all weeks.

numpy/pandas; a synthetic weekday-daytime load proves it.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd


@dataclass
class DaySchedule:
    """Detected on-period for one day-of-week (0=Mon)."""

    dayofweek: int
    start_hour: float | None         # first on-hour, or None if the day is off
    end_hour: float | None           # last on-hour (inclusive)
    on_hours: int


@dataclass
class WeeklySchedule:
    """Detected weekly operating schedule from an interval series."""

    threshold: float
    occupied_fraction: float         # share of hour-of-week slots that are "on"
    days: list                       # list[DaySchedule], Mon..Sun
    on_slots: list                   # [(dayofweek, hour)] that are typically on

    def as_dict(self) -> dict:
        d = asdict(self)
        return d

    def is_on(self, dayofweek: int, hour: int) -> bool:
        return (dayofweek, hour) in set(self.on_slots)


def detect_schedule(series: pd.Series, *, threshold: float | None = None,
                    on_level: float = 0.5, min_fraction: float = 0.5) -> WeeklySchedule:
    """Infer the weekly on/off schedule from an interval load/status ``series``.

    ``threshold`` marks an interval "on"; if None it defaults to ``base + on_level·(peak − base)``
    using the 10th/90th percentiles (robust to spikes). An hour-of-week slot is "on" when it is above
    threshold in at least ``min_fraction`` of the weeks observed.
    """
    s = series.dropna()
    if s.empty:
        return WeeklySchedule(float("nan"), float("nan"), [], [])
    if threshold is None:
        lo, hi = np.percentile(s, 10), np.percentile(s, 90)
        threshold = float(lo + on_level * (hi - lo))
    idx = pd.DatetimeIndex(s.index)
    on = (s > threshold).to_numpy()
    frac = pd.Series(on, index=[idx.dayofweek, idx.hour]).groupby(level=[0, 1]).mean()

    on_slots, days = [], []
    for dow in range(7):
        hours_on = [h for h in range(24) if float(frac.get((dow, h), 0.0)) >= min_fraction]
        on_slots.extend((dow, h) for h in hours_on)
        days.append(DaySchedule(dayofweek=dow,
                                start_hour=float(min(hours_on)) if hours_on else None,
                                end_hour=float(max(hours_on)) if hours_on else None,
                                on_hours=len(hours_on)))
    return WeeklySchedule(threshold=round(float(threshold), 4),
                          occupied_fraction=round(len(on_slots) / (7 * 24), 4),
                          days=days, on_slots=on_slots)


def compare_schedule(detected: WeeklySchedule, stated_on_slots) -> dict:
    """Compare a detected schedule to a stated one (a set/list of ``(dayofweek, hour)`` on-slots).

    Returns the extra-runtime slots (on when it shouldn't be — a setback opportunity), missing slots
    (off when it should be on), and the agreement fraction over the 168 hour-of-week slots.
    """
    det = set(detected.on_slots)
    stated = {tuple(x) for x in stated_on_slots}
    extra = sorted(det - stated)              # running unexpectedly (setback opportunity)
    missing = sorted(stated - det)            # not running when scheduled
    agree = sum(1 for slot in ((d, h) for d in range(7) for h in range(24))
                if (slot in det) == (slot in stated))
    return {"extra_runtime_slots": extra, "missing_slots": missing,
            "n_extra": len(extra), "n_missing": len(missing),
            "agreement": round(agree / (7 * 24), 4)}
