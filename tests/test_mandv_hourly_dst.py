"""Hourly M&V frames count the daylight-saving fall-back hour once per real hour (0.105, #158).

Naive wall-clock data holds the repeated fall-back hour under one stamp, sometimes twice.
``hourly_energy_vs_temp`` used to sum the two energy readings into one hour (counting it double)
and had no ``timezone=``. With a zone both real hours are kept; without one duplicates are
averaged into one hour, counted, and a ``TimezoneWarning`` says so. Synthetic data only.
"""

import os
import sys
import warnings

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv.intervalfit import hourly_energy_vs_temp, rate_to_energy  # noqa: E402
from camber.tsparse import TimezoneWarning  # noqa: E402

LA = "America/Los_Angeles"
AUTUMN = "2018-11-04"


def _export(kind="energy", freq="1h"):
    """A UTC-stamped meter export moved to the naive LA wall clock, both readings of the repeated
    hour kept (as a trend export does): energy per interval, or a rate. Each real hour's value is
    its UTC hour number, so the two passes of 01:00 differ."""
    utc = pd.date_range(
        "2018-11-03 07:00", "2018-11-06 08:00", freq=freq, tz="UTC", inclusive="left"
    )
    step_h = pd.Timedelta(freq) / pd.Timedelta("1h")
    vals = (np.arange(len(utc)) * step_h // 1 + 1.0) * (step_h if kind == "energy" else 1.0)
    local = utc.tz_convert(LA).tz_localize(None)
    s = pd.Series(vals, index=local)
    t = pd.Series(40.0 + (np.arange(len(utc)) * step_h // 1), index=local)
    return s, t, utc, vals


@pytest.mark.parametrize("kind", ["energy", "rate"])
def test_without_a_zone_the_repeated_hour_counts_once(kind):
    s, t, _utc, _vals = _export(kind)
    assert s.index.has_duplicates
    with pytest.warns(TimezoneWarning, match="1 duplicate timestamp"):
        df = hourly_energy_vs_temp(s, t, rate_is_energy_rate=(kind == "rate"))
    one = pd.Timestamp(f"{AUTUMN} 01:00")
    two = s.loc[one].to_numpy()
    assert df.loc[one, "energy"] == pytest.approx(two.mean())  # one hour: the mean, never the sum
    assert df.loc[one, "oat"] == pytest.approx(t.loc[one].mean())
    assert df.attrs["n_duplicate_stamps"] == 1 and not df.index.has_duplicates
    # every other hour is its own reading
    other = df.index != one
    assert np.allclose(
        df.loc[other, "energy"].to_numpy(),
        s[~s.index.duplicated(keep=False)].reindex(df.index[other]).to_numpy(),
    )


@pytest.mark.parametrize("kind", ["energy", "rate"])
def test_with_a_zone_both_real_hours_are_kept(kind):
    s, t, utc, vals = _export(kind)
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # no warning: the zone resolves the repeat
        df = hourly_energy_vs_temp(s, t, rate_is_energy_rate=(kind == "rate"), timezone=LA)
    assert str(df.index.tz) == LA and len(df) == len(utc)
    # the frame is the publisher's instants, hour for hour
    assert np.allclose(df["energy"].to_numpy(), vals)
    assert (df.index.tz_convert("UTC") == utc).all()
    rep = df[df.index.tz_localize(None) == pd.Timestamp(f"{AUTUMN} 01:00")]
    assert len(rep) == 2 and list(rep["hour"]) == [1, 1]
    assert df.attrs["n_duplicate_stamps"] == 0 and df.attrs["n_repeated_hour_copied"] == 0
    day = df[df.index.tz_localize(None).normalize() == pd.Timestamp(AUTUMN)]
    assert len(day) == 25


def test_a_lone_stored_sample_stands_for_both_passes():
    s, t, _utc, _vals = _export("energy")
    stored = s.resample("1h").mean().dropna()  # the store's hourly mean: one stamp for the repeat
    t_stored = t.resample("1h").mean().dropna()
    df = hourly_energy_vs_temp(stored, t_stored, rate_is_energy_rate=False, timezone=LA)
    rep = df[df.index.tz_localize(None) == pd.Timestamp(f"{AUTUMN} 01:00")]
    assert len(rep) == 2 and (rep["energy"] == stored.loc[f"{AUTUMN} 01:00"]).all()
    assert df.attrs["n_repeated_hour_copied"] == 1
    # the day's energy matches the daily path's repeated_hour_weights (the repeat counted twice)
    from camber.mandv.intervalfit import daily_energy_vs_temp

    daily = daily_energy_vs_temp(stored, t_stored, rate_is_energy_rate=False, timezone=LA)
    day = df[df.index.tz_localize(None).normalize() == pd.Timestamp(AUTUMN)]
    assert day["energy"].sum() == pytest.approx(daily.loc[AUTUMN, "energy"])


def test_sub_hourly_rate_with_duplicates_does_not_degenerate():
    s, t, _utc, _vals = _export("rate", freq="15min")
    # many duplicate stamps (overlapping exports) would pull the median spacing to zero
    dup = pd.concat([s, s, s]).sort_index(kind="stable")  # every reading three times
    secs = np.diff(dup.index.asi8)
    assert np.median(secs) == 0
    e = rate_to_energy(dup, "1h")
    assert e.notna().sum() > 70 and (e.dropna() > 0).all()
    with pytest.warns(TimezoneWarning):
        df = hourly_energy_vs_temp(dup, t)
    assert len(df) == 72  # 73 real hours; the repeated one merged
    assert df["energy"].between(1, 100).all()


def test_clean_hourly_data_is_unchanged():
    idx = pd.date_range("2021-06-01", periods=24 * 30, freq="1h")
    rng = np.random.default_rng(7)
    e = pd.Series(rng.random(len(idx)) * 30, index=idx)
    t = pd.Series(60 + rng.random(len(idx)) * 20, index=idx)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        df = hourly_energy_vs_temp(e, t)
    want = pd.DataFrame({"energy": rate_to_energy(e, "1h"), "oat": t.resample("1h").mean()})
    want = want.dropna()
    want["hour"] = want.index.hour
    pd.testing.assert_frame_equal(df, want[want["energy"] >= 0])
    # a zone with no fall-back in the data keeps the same numbers, on the zone's clock
    z = hourly_energy_vs_temp(e, t, timezone=LA)
    assert np.array_equal(z["energy"].to_numpy(), df["energy"].to_numpy())
    assert (z.index.tz_localize(None) == df.index).all()


def test_caltrack_hourly_takes_the_zone(monkeypatch):
    from camber.mandv import caltrack

    seen = []
    real = caltrack.hourly_energy_vs_temp

    def spy(*a, **kw):
        seen.append(kw.get("timezone"))
        return real(*a, **kw)

    monkeypatch.setattr(caltrack, "hourly_energy_vs_temp", spy)
    idx = pd.date_range("2021-01-04", periods=24 * 7 * 12, freq="1h")
    t = pd.Series(50 + 10 * np.sin(np.arange(len(idx)) / 24), index=idx)
    e = pd.Series(20 + (idx.hour.isin(range(8, 18))) * 10 + 0.2 * t.to_numpy(), index=idx)
    half = len(idx) // 2
    caltrack.caltrack_savings_hourly(
        e[:half], t[:half], e[half:], t[half:], min_hours=24 * 30, timezone=LA
    )
    assert seen == [LA, LA]
