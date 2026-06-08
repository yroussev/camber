"""Build change-point model inputs from interval meter data (any BAS gas/thermal).

Monthly utility bills give only ~12 points/year -- too few for a tight fit. BAS
interval meters (e.g. a hot-water or gas BTU meter at 15-min) give thousands of
points, so daily and hourly change-point models fit far more sharply.

This pairs an interval *energy* series with an interval *temperature* series on a
common time grid and aggregates to the modeling resolution:

* **hourly**  : energy per hour vs that hour's OAT -- the right pairing for hourly
  models (hourly energy needs hourly OAT, not a daily mean).
* **daily/monthly** : energy per period vs that period's mean OAT *or* heating/
  cooling degree-days. Degree-days, computed from the underlying HOURLY temps
  (not the daily mean), capture how cold/hot the period actually got, which a
  daily-mean OAT smears out -- so for heating they fit better than mean OAT.

Rate vs. energy handling matters: a meter reporting a *rate* (BTU/hr, kW) must be
integrated over time, not summed naively. ``rate_to_energy`` converts a rate to
energy-per-target-interval using the sample spacing.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .resample import resample


def degree_days(oat_hourly: pd.Series, freq: str, base_f: float = 65.0,
                kind: str = "heating") -> pd.Series:
    """Heating or cooling degree-days per ``freq`` bin from HOURLY temperatures.

    Computed from the hourly series (not a daily mean) so a cold morning under a
    mild daily average still registers. HDD = sum over hours of max(0, base - T)
    / 24 ; CDD = sum of max(0, T - base) / 24. Units: degree-days (degF-day).
    """
    if kind == "heating":
        contrib = (base_f - oat_hourly).clip(lower=0)
    elif kind == "cooling":
        contrib = (oat_hourly - base_f).clip(lower=0)
    else:
        raise ValueError("kind must be 'heating' or 'cooling'")
    # sum hourly contributions per bin, divide by 24 to express as degree-DAYS
    return contrib.resample(freq).sum(min_count=1) / 24.0


def rate_to_energy(rate: pd.Series, freq: str) -> pd.Series:
    """Integrate an instantaneous rate (per hour) into energy per ``freq`` bin.

    Each sample represents its rate over the gap to the next sample; energy in a
    bin = sum(rate_i * hours_i). For uniformly-sampled data this equals
    mean(rate) * hours_in_bin, but we integrate explicitly so uneven sampling is
    handled correctly.
    """
    rate = rate.sort_index().dropna()
    if len(rate) < 2:
        return pd.Series(dtype=float)
    # hours each sample represents (gap to next; last repeats the prior gap)
    secs = np.diff(rate.index.view("int64")) / 1e9
    hours = np.append(secs, secs[-1]) / 3600.0
    energy_per_sample = pd.Series(rate.values * hours, index=rate.index)
    return energy_per_sample.resample(freq).sum(min_count=1)


def daily_energy_vs_temp(rate: pd.Series, oat: pd.Series, *,
                         rate_is_energy_rate: bool = True) -> pd.DataFrame:
    """Daily energy vs daily-mean OAT, ready for change-point fitting.

    ``rate``: interval meter series. If ``rate_is_energy_rate`` it is a rate
    (BTU/hr etc.) integrated to daily energy; otherwise it is already energy per
    interval and is simply summed.
    """
    if rate_is_energy_rate:
        e = rate_to_energy(rate, "D")
    else:
        e = resample(rate, "D", method="time_weighted_sum")
    t = oat.sort_index().resample("D").mean()
    df = pd.DataFrame({"energy": e, "oat": t}).dropna()
    return df[df["energy"] >= 0]


def energy_vs_degree_days(rate: pd.Series, oat_hourly: pd.Series, *, freq: str = "D",
                          base_f: float = 65.0, kind: str = "heating",
                          rate_is_energy_rate: bool = True) -> pd.DataFrame:
    """Energy per ``freq`` bin vs degree-days, for daily or monthly heating/cooling.

    Degree-days are computed from the hourly OAT (so cold hours under a mild daily
    mean still count). Returns columns ``energy`` and ``dd``; fit energy ~ a + b*dd
    (a 2P line in degree-day space) -- the canonical daily/monthly heating model.
    """
    if rate_is_energy_rate:
        e = rate_to_energy(rate, freq)
    else:
        e = resample(rate, freq, method="time_weighted_sum")
    dd = degree_days(oat_hourly.sort_index(), freq, base_f=base_f, kind=kind)
    df = pd.DataFrame({"energy": e, "dd": dd}).dropna()
    return df[df["energy"] >= 0]


def hourly_energy_vs_temp(rate: pd.Series, oat: pd.Series, *,
                          rate_is_energy_rate: bool = True) -> pd.DataFrame:
    """Hourly energy vs hourly-mean OAT, with an hour-of-day column.

    The ``hour`` column (0-23) lets a caller fit an hour-of-day basis (one model
    per hour-of-day) or a single pooled hourly model.
    """
    if rate_is_energy_rate:
        e = rate_to_energy(rate, "1h")
    else:
        e = resample(rate, "1h", method="time_weighted_sum")
    t = oat.sort_index().resample("1h").mean()
    df = pd.DataFrame({"energy": e, "oat": t}).dropna()
    df = df[df["energy"] >= 0]
    df["hour"] = df.index.hour
    return df
