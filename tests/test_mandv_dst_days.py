"""Daily M&V days are as long as their clock (0.93, #68): 25 h in autumn, 23 h in spring.

CAMBER's series are naive wall-clock time. On the fall-back day the repeated hour lands in one
naive stamp (the store's hourly resample averages its two readings), so with the site's zone that
sample counts for both passes of the clock. Without a zone every day is exactly as before.
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv.intervalfit import (  # noqa: E402
    daily_energy_vs_temp,
    rate_to_energy,
    repeated_hour_weights,
)

OSLO, LA = "Europe/Oslo", "America/Los_Angeles"


def _wall_clock(start_utc: str, end_utc: str, tz: str, freq: str = "1h", kw=None):
    """A constant (or given) load as a naive local series, the way the store keeps it: UTC
    instants moved to the wall clock, then resampled to the hour (the repeat is averaged)."""
    utc = pd.date_range(start_utc, end_utc, freq=freq, tz="UTC", inclusive="left")
    vals = np.ones(len(utc)) if kw is None else kw(utc)
    local = utc.tz_convert(tz).tz_localize(None)
    raw = pd.Series(vals, index=local)
    return raw, raw.resample("1h").mean().dropna()


def test_weights_mark_only_the_repeated_hour():
    idx = pd.date_range("2018-10-28 00:00", "2018-10-28 05:00", freq="1h")
    w = repeated_hour_weights(idx, OSLO)
    assert list(w) == [1, 1, 2, 1, 1, 1]  # 02:00-02:59 happens twice in Oslo
    idx_us = pd.date_range("2018-11-04 00:00", "2018-11-04 03:00", freq="15min")
    w_us = repeated_hour_weights(idx_us, LA)
    assert list(np.flatnonzero(w_us == 2)) == [4, 5, 6, 7]  # 01:00-01:45 in Los Angeles
    assert repeated_hour_weights(idx, None) is None
    assert repeated_hour_weights(pd.date_range("2018-07-01", periods=48, freq="1h"), OSLO) is None
    assert repeated_hour_weights(idx.tz_localize("UTC"), OSLO) is None  # tz-aware: instants


@pytest.mark.parametrize(
    "tz,spring,autumn", [(OSLO, "2018-03-25", "2018-10-28"), (LA, "2018-03-11", "2018-11-04")]
)
def test_dst_days_are_23_and_25_hours(tz, spring, autumn):
    _raw, hourly = _wall_clock("2018-01-01", "2019-01-01", tz)
    oat = pd.Series(50.0, index=hourly.index)
    old = daily_energy_vs_temp(hourly, oat)
    new = daily_energy_vs_temp(hourly, oat, timezone=tz)
    assert old.loc[spring, "energy"] == new.loc[spring, "energy"] == 23.0
    assert old.loc[autumn, "energy"] == 24.0  # the historical result: an hour short
    assert new.loc[autumn, "energy"] == 25.0
    other = new.index.difference(pd.DatetimeIndex([autumn]))
    pd.testing.assert_frame_equal(new.loc[other], old.loc[other])  # every other day bit for bit
    assert new["energy"].sum() == pytest.approx(8760.0)  # the year's energy is whole again


def test_rate_mean_weights_the_repeated_hour_twice():
    # a temperature that differs in the two passes of the repeated hour
    utc = pd.date_range("2018-10-27 22:00", "2018-10-29 00:00", freq="1h", tz="UTC")
    local = utc.tz_convert(OSLO).tz_localize(None)
    oat = pd.Series(np.arange(len(utc), dtype=float), index=local)  # 2 readings at 02:00
    hourly_oat = oat.resample("1h").mean().dropna()
    e = pd.Series(1.0, index=hourly_oat.index)
    df = daily_energy_vs_temp(e, hourly_oat, timezone=OSLO)
    day = pd.Timestamp("2018-10-28")
    truth = oat[oat.index.normalize() == day].mean()  # 25 readings, one per real hour
    assert df.loc[day, "oat"] == pytest.approx(truth)
    assert daily_energy_vs_temp(e, hourly_oat).loc[day, "oat"] != pytest.approx(truth)


def test_duplicate_stamps_of_an_export_count_both_readings():
    raw, _hourly = _wall_clock(
        "2018-11-03 08:00", "2018-11-06 08:00", LA, freq="15min", kw=lambda u: np.arange(len(u))
    )
    assert raw.index.has_duplicates  # 01:00-01:45 twice
    truth = raw.groupby(raw.index.normalize()).sum() * 0.25
    got = rate_to_energy(raw, "D", timezone=LA)
    assert got.loc["2018-11-04"] == pytest.approx(truth.loc["2018-11-04"])


def test_energy_per_interval_samples_sum_the_repeat_twice():
    _raw, hourly = _wall_clock("2018-10-26", "2018-10-31", OSLO)
    oat = pd.Series(40.0, index=hourly.index)
    df = daily_energy_vs_temp(hourly, oat, rate_is_energy_rate=False, timezone=OSLO)
    assert df.loc["2018-10-28", "energy"] == 25.0
    assert df.loc["2018-10-27", "energy"] == 24.0


def test_no_fall_back_in_the_data_is_bit_identical():
    rng = np.random.default_rng(3)
    idx = pd.date_range("2018-05-01", "2018-08-01", freq="1h", inclusive="left")
    e = pd.Series(rng.random(len(idx)) * 40, index=idx)
    t = pd.Series(rng.random(len(idx)) * 30 + 50, index=idx)
    pd.testing.assert_frame_equal(
        daily_energy_vs_temp(e, t, timezone=OSLO), daily_energy_vs_temp(e, t)
    )


def test_catalog_timezone_prose_is_not_a_zone():
    from camber.config import _catalog_timezone

    assert _catalog_timezone({"dataset": {"dataset_id": "bdg2"}}) is None  # a prose description
    assert _catalog_timezone({"dataset": {"dataset_id": "cofactor-drammen"}}) == OSLO


def test_config_mv_days_follow_the_source_timezone(tmp_path):
    from camber.config import _prepare
    from camber.model.roles import Role
    from camber.mvrun import meter_series
    from camber.portfolio import Portfolio

    ws = str(tmp_path / "ws")
    pf = Portfolio.init(ws)
    pf.add_facility("DST site", facility_id="f1", reason="test", activate=True)
    _raw, hourly = _wall_clock("2018-09-01", "2018-12-01", OSLO)
    frame = pd.DataFrame({Role.POWER: hourly, Role.OAT: 45.0 + 0.0 * hourly})
    pf.store.write_role_frame(frame, facility_id="f1", equip="meter", equip_class="M")
    cfg = {
        "source": {"kind": "store", "store": os.path.join(ws, "store"), "facility_id": "f1"},
        "equipment": [{"class": "M"}],
        "mv": [{"class": "M", "role": "power", "period": ["2018-09-01", "2018-11-30"]}],
    }
    (ms,) = meter_series(cfg)
    assert ms.daily.loc["2018-10-28", "energy"] == 24.0  # no zone named: as before
    cfg["source"]["timezone"] = OSLO
    assert _prepare(cfg, ".").timezone == OSLO
    (ms,) = meter_series(cfg)
    assert ms.daily.loc["2018-10-28", "energy"] == 25.0
