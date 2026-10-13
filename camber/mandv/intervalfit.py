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

import warnings
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from ..timegrid import interval_hours, regularize
from ._temperature import screen_temperature, screened_caveat
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
    # the nominal step from the positive gaps only (0.105, #158): repeated stamps (a fall-back hour
    # kept twice, overlapping exports) are zero gaps and could pull the median to 0, crediting
    # every sample nothing
    pos = secs[secs > 0]
    if not len(pos):
        return pd.Series(dtype=float)
    nominal = float(np.median(pos))
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
    min_coverage: float = 0.9,
    temp_bounds: tuple | None = None,
) -> pd.DataFrame:
    """Daily energy vs daily-mean OAT, ready for change-point fitting.

    ``rate``: interval meter series. If ``rate_is_energy_rate`` it is a rate
    (BTU/hr etc.) integrated to daily energy; otherwise it is already energy per
    interval and is simply summed. (The default here is ``True``, a rate, while the savings
    functions built on this frame -- :func:`~camber.mandv.caltrack.daily_changepoint_savings`,
    the non-routine detectors -- default to ``False``, energy per interval. The two defaults are
    historical and are kept; pass the flag explicitly.)

    **Day coverage** (0.105, #157). A day is kept only when its meter samples cover at least
    ``min_coverage`` (default 0.9) of the day: the samples present times the series' interval
    (:func:`camber.timegrid.interval_hours`, the modal step) over the day's hours. A day missing
    hours would otherwise be summed as if it were whole, and read low. With ``timezone`` a day is
    as long as its clock (23 or 25 hours on a daylight-saving change, a repeated-hour sample
    counting for both passes, as below); without it every day is 24 hours, so the naive 23-sample
    spring-forward day still passes the default. The frame's ``attrs["dropped_days"]`` counts the
    days the rule removed and ``attrs["min_coverage"]`` records the threshold; ``min_coverage=0``
    keeps every day, the frame before 0.105. Billing input applies ``min_coverage`` to each bill's
    temperature coverage instead (:meth:`~camber.mandv.billing.BillingSeries.energy_vs_temp`).

    **Temperature screen** (0.105, #159). Temperatures outside ``temp_bounds`` (default the
    sensor-health range for outdoor air, -40 to 140 degF;
    :func:`camber.mandv._temperature.screen_temperature`) are treated as missing before the daily
    means are taken; ``attrs["n_temp_screened"]`` counts them.

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
    change. Without ``timezone`` duplicate timestamps are one hour each (0.105, #169): energy and
    temperature are averaged per stamp, so a repeated fall-back hour no longer counts twice, a
    :class:`camber.tsparse.TimezoneWarning` says how many were merged and
    ``attrs["n_duplicate_stamps"]`` counts them, as :func:`hourly_energy_vs_temp` does (#158).
    Data without duplicates is unchanged.
    """
    from .billing import BillingSeries, as_billing_series, is_billing_like

    oat, n_screened = screen_temperature(oat, temp_bounds)
    if isinstance(rate, BillingSeries) or (not rate_is_energy_rate and is_billing_like(rate, oat)):
        out = as_billing_series(rate).energy_vs_temp(oat, min_coverage=min_coverage)
        out.attrs["billing"] = True
        out.attrs["n_temp_screened"] = n_screened
        return out
    n_dup = 0
    if not timezone:
        # 0.105 (#169): a repeated stamp is one hour, as on the hourly path (#158)
        rate, oat, n_dup = _merge_duplicates(rate, oat)
    if rate_is_energy_rate:
        e = rate_to_energy(rate, "D", timezone=timezone)
    else:
        e = _energy_sum(rate, "D", timezone)
    t = _mean_by_clock(oat.sort_index(), "D", timezone)
    df = pd.DataFrame({"energy": e, "oat": t}).dropna()
    df = df[df["energy"] >= 0]
    cov = day_coverage(rate, timezone=timezone).reindex(df.index)
    keep = (cov >= float(min_coverage) - 1e-9).to_numpy()  # NaN coverage (no sample) fails
    dropped = int((~keep).sum())
    if dropped:
        df = df[keep]
    df.attrs["dropped_days"] = dropped
    df.attrs["min_coverage"] = float(min_coverage)
    df.attrs["n_temp_screened"] = n_screened
    df.attrs["n_duplicate_stamps"] = n_dup
    return df


def day_hours(days, timezone: str | None = None) -> np.ndarray:
    """Hours of clock in each calendar day of ``days`` (midnight stamps): 24, or 23 / 25 on a
    daylight-saving change in ``timezone`` (a tz-aware ``days`` uses its own zone)."""
    days = pd.DatetimeIndex(days)
    tz = days.tz if days.tz is not None else timezone
    if tz is None or len(days) == 0:
        return np.full(len(days), 24.0)
    naive = days.tz_localize(None) if days.tz is not None else days
    kw = {"ambiguous": True, "nonexistent": "shift_forward"}
    start = naive.tz_localize(tz, **kw)
    end = (naive + pd.Timedelta(days=1)).tz_localize(tz, **kw)
    return (end.asi8 - start.asi8) / 3.6e12


def day_coverage(samples: pd.Series, *, timezone: str | None = None) -> pd.Series:
    """Share of each calendar day that ``samples`` cover (0.105, #157), indexed by day.

    The non-missing samples are counted once per timestamp (a repeated stamp is one sample), times
    the series' modal interval (:func:`camber.timegrid.interval_hours`), over the day's hours
    (:func:`day_hours`). With ``timezone`` a sample in the repeated hour of a fall-back counts for
    both passes of the clock (:func:`repeated_hour_weights`) against a 25-hour day, and the
    spring-forward day is 23 hours. Capped at 1. A day with no sample is absent.
    """
    s = samples.dropna() if isinstance(samples, pd.Series) else pd.Series(dtype=float)
    idx = pd.DatetimeIndex(s.index).unique().sort_values()
    if not len(idx):
        return pd.Series(dtype=float)
    step = interval_hours(idx)
    w = repeated_hour_weights(idx, timezone)
    counts = pd.Series(np.ones(len(idx)) if w is None else w, index=idx)
    per_day = counts.resample("D").sum()
    per_day = per_day[per_day > 0]
    cov = per_day * step / day_hours(per_day.index, timezone)
    return cov.clip(upper=1.0)


def _energy_sum(energy: pd.Series, freq: str, timezone: str | None) -> pd.Series:
    """Energy-per-interval samples summed per ``freq`` bin; a repeated-hour sample counts twice."""
    out = resample(energy, freq, method="time_weighted_sum")
    s = energy.sort_index()
    if timezone and s.index.has_duplicates:
        # both readings of a repeated stamp kept by an export: one sample, their mean, which the
        # weight below counts for both passes -- their sum, not each reading twice (0.105)
        s = s.groupby(level=0).mean()
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
    temp_bounds: tuple | None = None,
) -> pd.DataFrame:
    """Energy per ``freq`` bin vs degree-days, for daily or monthly heating/cooling.

    Degree-days are computed from the hourly OAT (so cold hours under a mild daily
    mean still count). Returns columns ``energy`` and ``dd``; fit energy ~ a + b*dd
    (a 2P line in degree-day space) -- the canonical daily/monthly heating model.
    Temperatures outside ``temp_bounds`` are treated as missing first (0.105, #159; see
    :func:`daily_energy_vs_temp`), counted in ``attrs["n_temp_screened"]``.
    """
    oat_hourly, n_screened = screen_temperature(oat_hourly, temp_bounds)
    if rate_is_energy_rate:
        e = rate_to_energy(rate, freq)
    else:
        e = resample(rate, freq, method="time_weighted_sum")
    dd = degree_days(oat_hourly.sort_index(), freq, base_f=base_f, kind=kind)
    df = pd.DataFrame({"energy": e, "dd": dd}).dropna()
    df = df[df["energy"] >= 0]
    df.attrs["n_temp_screened"] = n_screened
    return df


def _on_zone_clock(s: pd.Series, timezone: str) -> tuple:
    """``(series on a tz-aware local index, n_copied)``: naive wall-clock samples placed on the
    real instants of ``timezone`` (0.105, #158).

    A stamp in the repeated hour of a fall-back is two instants. Readings an export kept twice
    under it are the two passes, in order (the first is daylight time). A lone reading stands for
    both passes -- the store's hourly resample averages them into one bin -- so it is placed on
    both, as :func:`repeated_hour_weights` counts it twice for a day; ``n_copied`` counts those.
    A spring-forward stamp that cannot exist is moved forward (:func:`camber.timegrid.localize`).
    """
    s = s.sort_index(kind="stable")
    idx = pd.DatetimeIndex(s.index)
    if idx.tz is not None:
        return pd.Series(s.to_numpy(), index=idx.tz_convert(timezone), name=s.name), 0
    uniq = idx.unique()
    w = repeated_hour_weights(uniq, timezone)
    if w is None:
        out = s.copy()
        out.index = idx.tz_localize(timezone, ambiguous=True, nonexistent="shift_forward")
        return regularize(out, dedupe="mean"), 0
    amb = np.isin(idx.asi8, uniq.asi8[w == 2.0])
    occurrence = s.groupby(level=0).cumcount().to_numpy()
    size = s.groupby(level=0).transform("size").to_numpy()
    lone = amb & (size == 1)
    vals = np.concatenate([s.to_numpy(), s.to_numpy()[lone]])
    stamps = pd.DatetimeIndex(np.concatenate([idx.asi8, idx.asi8[lone]]).astype("datetime64[ns]"))
    dst = np.concatenate([~amb | (occurrence == 0), np.zeros(int(lone.sum()), dtype=bool)])
    local = stamps.tz_localize(timezone, ambiguous=dst, nonexistent="shift_forward")
    out = pd.Series(vals, index=local, name=s.name)
    return regularize(out, dedupe="mean"), int(lone.sum())


def _merge_duplicates(rate: pd.Series, oat: pd.Series) -> tuple:
    """``(rate, oat, n_dup)`` with duplicate timestamps averaged into one each, for data without a
    site timezone (0.105, #158 hourly, #169 daily). ``n_dup`` counts the merged energy stamps; a
    :class:`camber.tsparse.TimezoneWarning` says how many were merged. Unchanged when none."""
    n_dup = int(pd.DatetimeIndex(rate.index).duplicated().sum())
    n_dup_t = int(pd.DatetimeIndex(oat.index).duplicated().sum())
    if not (n_dup or n_dup_t):
        return rate, oat, 0
    from ..tsparse import TimezoneWarning

    warnings.warn(
        f"{max(n_dup, n_dup_t)} duplicate timestamp(s) (a daylight-saving fall-back hour, "
        "or overlapping exports) were averaged into one hour each because no site "
        "timezone was given; pass timezone='<IANA zone>' (config: source.timezone) to "
        "keep both real hours of a fall-back",
        TimezoneWarning,
        stacklevel=3,
    )
    return regularize(rate, dedupe="mean"), regularize(oat, dedupe="mean"), n_dup


def hourly_energy_vs_temp(
    rate: pd.Series,
    oat: pd.Series,
    *,
    rate_is_energy_rate: bool = True,
    timezone: str | None = None,
    temp_bounds: tuple | None = None,
) -> pd.DataFrame:
    """Hourly energy vs hourly-mean OAT, with an hour-of-day column.

    The ``hour`` column (0-23) lets a caller fit an hour-of-day basis (one model
    per hour-of-day) or a single pooled hourly model.

    **The repeated hour** (0.105, #158). Naive wall-clock data holds a daylight-saving fall-back
    hour under one stamp, sometimes twice. With the site's ``timezone`` the samples are placed on
    their real instants first (:func:`camber.timegrid.localize`'s convention): the repeated hour
    becomes two real hours, both kept, and a lone sample in it (the store's hourly mean of the two
    passes) stands for both, as on the daily path (:func:`repeated_hour_weights`). The frame is
    then indexed by tz-aware local time and ``hour`` is the local clock hour. Without a zone,
    duplicate timestamps are one hour: energy and temperature are averaged per stamp
    (:func:`camber.timegrid.regularize` with ``dedupe="mean"``), never summed, so the repeated
    hour no longer counts double, and a :class:`camber.tsparse.TimezoneWarning` says how many were
    merged. ``attrs["n_duplicate_stamps"]`` counts the merged energy stamps and
    ``attrs["n_repeated_hour_copied"]`` the lone samples placed on both passes.

    **Temperature screen** (0.105, #159). Temperatures outside ``temp_bounds`` are treated as
    missing (see :func:`daily_energy_vs_temp`), counted in ``attrs["n_temp_screened"]``.
    """
    oat, n_screened = screen_temperature(oat, temp_bounds)
    n_dup = n_copied = 0
    if timezone:
        rate, n_copied = _on_zone_clock(rate, timezone)
        oat, _ = _on_zone_clock(oat, timezone)
    else:
        rate, oat, n_dup = _merge_duplicates(rate, oat)
    if rate_is_energy_rate:
        e = rate_to_energy(rate, "1h")
    else:
        e = resample(rate, "1h", method="time_weighted_sum")
    t = oat.sort_index().resample("1h").mean()
    df = pd.DataFrame({"energy": e, "oat": t}).dropna()
    df = df[df["energy"] >= 0]
    df["hour"] = df.index.hour
    df.attrs["n_duplicate_stamps"] = n_dup
    df.attrs["n_repeated_hour_copied"] = n_copied
    df.attrs["n_temp_screened"] = n_screened
    return df


def _frame_caveats(frame: pd.DataFrame, period: str) -> list:
    """What building ``frame`` dropped or merged, as caveats (0.105, #157-#159): partial days under
    the day-coverage rule, temperatures screened as implausible, duplicate timestamps averaged.
    Empty for clean data."""
    a = frame.attrs
    out = []
    n = int(a.get("dropped_days", 0))
    if n:
        out.append(
            f"{n} {period} day(s) with meter data covering less than "
            f"{a.get('min_coverage', 0.9):.0%} of the day were dropped (min_coverage)"
        )
    n = int(a.get("n_temp_screened", 0))
    if n:
        out.append(screened_caveat(n, f"{period} temperature value(s)"))
    n = int(a.get("n_duplicate_stamps", 0))
    if n:
        out.append(
            f"{n} duplicate {period} timestamp(s) were averaged into one hour each (no site "
            "timezone was given, so a daylight-saving fall-back hour counts once)"
        )
    return out
