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

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from .resample import resample

if TYPE_CHECKING:
    from .billing import BillingSeries


def degree_days(
    oat_hourly: pd.Series, freq: str, base_f: float = 65.0, kind: str = "heating"
) -> pd.Series:
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


def repeated_hour_weights(index, timezone: str | None):
    """How many real hours of clock each naive local sample of ``index`` stands for, around a
    daylight-saving fall-back in ``timezone`` (0.93, #68; provisional).

    CAMBER's time series are naive **wall-clock** time (:mod:`camber.timegrid`). On the autumn
    fall-back day one hour of clock time happens twice, but a naive index can hold it only once:
    the store's hourly resample averages the two readings into one bin (a trend export's first
    reading is kept instead). Such a sample stands for **two** passes of the clock, so this
    returns an array of weights -- ``2.0`` for a sample whose wall-clock time is ambiguous in
    ``timezone`` (it falls in the repeated hour), ``1.0`` otherwise -- or ``None`` when no sample
    is (no zone, a tz-aware index, or no fall-back inside the data). The spring-forward hour needs
    no weight: it is simply absent, so that day already has 23 hours of samples.
    """
    if not timezone:
        return None
    idx = pd.DatetimeIndex(index)
    if idx.tz is not None or len(idx) == 0:
        return None
    n = len(idx)
    early = idx.tz_localize(timezone, ambiguous=np.ones(n, dtype=bool), nonexistent="NaT")
    late = idx.tz_localize(timezone, ambiguous=np.zeros(n, dtype=bool), nonexistent="NaT")
    amb = early.asi8 != late.asi8  # NaT (a skipped spring-forward time) is equal to itself
    if not amb.any():
        return None
    return np.where(amb, 2.0, 1.0)


def _dst_days(index, weights) -> pd.DatetimeIndex:
    """The calendar days (midnight stamps) holding a sample whose weight is not 1."""
    idx = pd.DatetimeIndex(index)
    return pd.DatetimeIndex(idx[weights != 1.0].normalize().unique())


def rate_to_energy(rate: pd.Series, freq: str, *, timezone: str | None = None) -> pd.Series:
    """Integrate an instantaneous rate (per hour) into energy per ``freq`` bin.

    Each sample represents its rate over the interval to the next sample, **capped at the
    series' nominal step** (the median spacing): energy in a bin = sum(rate_i * hours_i). For
    uniformly-sampled data this equals mean(rate) * hours_in_bin. A longer interval is a gap in
    the data, not a long reading -- the sample before it is credited one nominal step and the rest
    of the gap is left unfilled, so a day with missing readings reports less energy (and a coverage
    rule can drop it) instead of silently inheriting the last reading for the whole gap. (Before
    0.86.0 the sample before a gap was credited the entire gap: two missing days after an hourly
    reading of 1 put 49 units into that reading's day.)

    ``timezone`` (0.93, #68; provisional): the site's IANA zone. A sample in the repeated hour of
    a daylight-saving fall-back then counts for both passes of the clock
    (:func:`repeated_hour_weights`), so the autumn day has 25 hours of energy, not 24; the
    spring-forward day has 23 either way. ``None`` (the default) is exactly the historical
    result, and so is a zone whose data hold no fall-back.
    """
    rate = rate.sort_index().dropna()
    if timezone and rate.index.has_duplicates:
        # both readings of a repeated stamp kept by an export: one sample, their mean, which the
        # weight below counts for both passes (as the store's hourly resample does)
        rate = rate.groupby(level=0).mean()
    if len(rate) < 2:
        return pd.Series(dtype=float)
    # hours each sample represents: the interval to the next sample, capped at the nominal step
    secs = np.diff(rate.index.view("int64")) / 1e9
    nominal = float(np.median(secs))
    hours = np.minimum(np.append(secs, nominal), nominal) / 3600.0
    w = repeated_hour_weights(rate.index, timezone)
    if w is not None:
        hours = hours * w
    energy_per_sample = pd.Series(rate.values * hours, index=rate.index)
    return energy_per_sample.resample(freq).sum(min_count=1)


def daily_energy_vs_temp(
    rate: pd.Series | BillingSeries,
    oat: pd.Series,
    *,
    rate_is_energy_rate: bool = True,
    timezone: str | None = None,
) -> pd.DataFrame:
    """Daily energy vs daily-mean OAT, ready for change-point fitting.

    ``rate``: interval meter series. If ``rate_is_energy_rate`` it is a rate
    (BTU/hr etc.) integrated to daily energy; otherwise it is already energy per
    interval and is simply summed.

    **Billing periods** (0.90.1): energy that is a :class:`~camber.mandv.billing.BillingSeries`,
    or energy totals spaced a week or more apart against a daily-or-finer ``oat``, is not resampled
    to days (which paired each bill with the single day it was stamped on). Each bill is paired
    with the mean temperature of its own period instead, and ``energy`` is the bill's energy **per
    day**; the frame then also carries ``days``, ``start``, ``end``, ``estimated``, ``hdd``,
    ``cdd`` and ``coverage`` (see :meth:`~camber.mandv.billing.BillingSeries.energy_vs_temp`) and
    ``attrs["billing"]`` is true. Daily and sub-daily input is unchanged.

    **Daylight saving** (0.93, #68; provisional). With the site's ``timezone`` a day is as long as
    its clock: the autumn fall-back day sums 25 hours of energy (the repeated hour counts twice,
    see :func:`repeated_hour_weights`) and its mean temperature weights that hour twice; the
    spring-forward day has 23 hours (its skipped hour holds no sample). Only the fall-back days
    change; without ``timezone`` the frame is exactly as before.
    """
    from .billing import BillingSeries, as_billing_series, is_billing_like

    if isinstance(rate, BillingSeries) or (not rate_is_energy_rate and is_billing_like(rate, oat)):
        out = as_billing_series(rate).energy_vs_temp(oat)
        out.attrs["billing"] = True
        return out
    if rate_is_energy_rate:
        e = rate_to_energy(rate, "D", timezone=timezone)
    else:
        e = _energy_sum(rate, "D", timezone)
    t = _mean_by_clock(oat.sort_index(), "D", timezone)
    df = pd.DataFrame({"energy": e, "oat": t}).dropna()
    return df[df["energy"] >= 0]


def _energy_sum(energy: pd.Series, freq: str, timezone: str | None) -> pd.Series:
    """Energy-per-interval samples summed per ``freq`` bin; a repeated-hour sample counts twice."""
    out = resample(energy, freq, method="time_weighted_sum")
    s = energy.sort_index()
    w = repeated_hour_weights(s.index, timezone)
    if w is None:
        return out
    fixed = resample(s * w, freq, method="time_weighted_sum")
    days = _dst_days(s.index, w).intersection(out.index)
    out.loc[days] = fixed.reindex(days)
    return out


def _mean_by_clock(x: pd.Series, freq: str, timezone: str | None) -> pd.Series:
    """Per-bin mean of ``x``; on a fall-back day the repeated hour is weighted by its two passes.

    Only the days holding a repeated-hour sample are recomputed, so every other day is the plain
    ``resample(freq).mean()`` (bit for bit)."""
    out = x.resample(freq).mean()
    w = repeated_hour_weights(x.index, timezone)
    if w is None:
        return out
    ws = pd.Series(w, index=x.index).where(x.notna())
    num = (x * ws).resample(freq).sum(min_count=1)
    den = ws.resample(freq).sum(min_count=1)
    days = _dst_days(x.index, w).intersection(out.index)
    out.loc[days] = (num / den).reindex(days)
    return out


def energy_vs_degree_days(
    rate: pd.Series,
    oat_hourly: pd.Series,
    *,
    freq: str = "D",
    base_f: float = 65.0,
    kind: str = "heating",
    rate_is_energy_rate: bool = True,
) -> pd.DataFrame:
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


def hourly_energy_vs_temp(
    rate: pd.Series, oat: pd.Series, *, rate_is_energy_rate: bool = True
) -> pd.DataFrame:
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
