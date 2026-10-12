"""Daily M&V frames keep only days their meter data cover (0.105, #157).

``daily_energy_vs_temp`` used to sum whatever samples a day held, so a day missing hours was
treated as whole and read low. Days under ``min_coverage`` (default 0.9) are now dropped and
counted in ``attrs["dropped_days"]``; daylight-saving days are as long as their clock when the
site's zone is known. Synthetic data only.
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv.intervalfit import (  # noqa: E402
    daily_energy_vs_temp,
    day_coverage,
    day_hours,
)

LA = "America/Los_Angeles"


def _hourly(days=30, start="2021-03-01", freq="1h", seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(
        start, periods=int(days * pd.Timedelta("1D") / pd.Timedelta(freq)), freq=freq
    )
    e = pd.Series(20 + rng.random(len(idx)) * 10, index=idx)
    t = pd.Series(40 + 20 * np.sin(np.arange(len(idx)) / 50.0), index=idx)
    return e, t


def _drop_hours(s, day, n):
    """``s`` without the first ``n`` hours of ``day``."""
    day = pd.Timestamp(day)
    cut = (s.index >= day) & (s.index < day + pd.Timedelta(hours=n))
    return s[~cut]


@pytest.mark.parametrize("is_rate", [True, False])
def test_a_day_missing_hours_is_dropped_and_counted(is_rate):
    e, t = _hourly()
    gappy = _drop_hours(_drop_hours(e, "2021-03-10", 6), "2021-03-20", 2)  # 75 % and 92 %
    df = daily_energy_vs_temp(gappy, t, rate_is_energy_rate=is_rate)
    assert pd.Timestamp("2021-03-10") not in df.index  # 18 of 24 hours: dropped
    assert pd.Timestamp("2021-03-20") in df.index  # 22 of 24 hours: kept at 0.9
    assert df.attrs["dropped_days"] == 1 and df.attrs["min_coverage"] == 0.9
    strict = daily_energy_vs_temp(gappy, t, rate_is_energy_rate=is_rate, min_coverage=1.0)
    assert strict.attrs["dropped_days"] == 2
    every = daily_energy_vs_temp(gappy, t, rate_is_energy_rate=is_rate, min_coverage=0.0)
    assert every.attrs["dropped_days"] == 0 and len(every) == 30
    # the partial day would have read low: it holds 18 hours of energy
    assert every.loc["2021-03-10", "energy"] < 0.8 * every["energy"].median()


def test_sub_hourly_meters_count_their_own_interval():
    e, t = _hourly(days=10, freq="15min")
    gappy = _drop_hours(e, "2021-03-05", 4)  # 80 of 96 samples
    df = daily_energy_vs_temp(gappy, t, rate_is_energy_rate=False)
    assert df.attrs["dropped_days"] == 1 and pd.Timestamp("2021-03-05") not in df.index
    cov = day_coverage(gappy)
    assert cov.loc["2021-03-05"] == pytest.approx(80 / 96)
    assert cov.drop(pd.Timestamp("2021-03-05")).eq(1.0).all()


def test_full_days_are_bit_for_bit_unchanged():
    e, t = _hourly(days=60, seed=4)
    new = daily_energy_vs_temp(e, t)
    old = daily_energy_vs_temp(e, t, min_coverage=0.0)  # every day kept: the frame before 0.105
    pd.testing.assert_frame_equal(new, old)
    assert new.attrs["dropped_days"] == 0
    e2 = e.copy()
    e2.iloc[5] = np.nan  # one missing hour (23 of 24) passes the default
    assert daily_energy_vs_temp(e2, t, rate_is_energy_rate=False).attrs["dropped_days"] == 0


def test_daily_samples_are_whole_days():
    idx = pd.date_range("2021-01-01", periods=90, freq="D")
    e = pd.Series(100.0 + np.arange(90), index=idx)
    t = pd.Series(50.0, index=idx)
    df = daily_energy_vs_temp(e, t, rate_is_energy_rate=False)
    assert len(df) == 90 and df.attrs["dropped_days"] == 0


def _wall_clock_hourly(start_utc, end_utc, tz):
    """A constant load stored as the store keeps it: naive wall clock, resampled to the hour (the
    repeated fall-back hour averaged into one stamp, the skipped spring hour absent)."""
    utc = pd.date_range(start_utc, end_utc, freq="1h", tz="UTC", inclusive="left")
    local = utc.tz_convert(tz).tz_localize(None)
    return pd.Series(1.0, index=local).resample("1h").mean().dropna()


def test_dst_days_are_judged_against_their_own_length():
    hourly = _wall_clock_hourly("2018-03-01 08:00", "2018-12-01 08:00", LA)
    oat = pd.Series(55.0, index=hourly.index)
    spring, autumn = pd.Timestamp("2018-03-11"), pd.Timestamp("2018-11-04")
    assert day_hours(pd.DatetimeIndex([spring, autumn]), LA).tolist() == [23.0, 25.0]
    assert day_hours(pd.DatetimeIndex([spring]), None).tolist() == [24.0]
    # with the zone both days are whole: 23 of 23 hours, and 24 stamps standing for 25 hours
    zoned = daily_energy_vs_temp(hourly, oat, timezone=LA, min_coverage=1.0)
    assert spring in zoned.index and autumn in zoned.index
    assert zoned.loc[autumn, "energy"] == 25.0 and zoned.attrs["dropped_days"] == 0
    cov = day_coverage(hourly, timezone=LA)
    assert cov.loc[spring] == 1.0 and cov.loc[autumn] == 1.0
    # without it every day is 24 hours: the spring day is 23/24, which passes 0.9 but not 1.0
    naive = daily_energy_vs_temp(hourly, oat, min_coverage=1.0)
    assert spring not in naive.index and naive.attrs["dropped_days"] == 1
    assert spring in daily_energy_vs_temp(hourly, oat).index


def test_a_gappy_fall_back_day_counts_the_repeated_hour_twice():
    hourly = _wall_clock_hourly("2018-10-20 07:00", "2018-11-20 08:00", LA)
    oat = pd.Series(55.0, index=hourly.index)
    autumn = pd.Timestamp("2018-11-04")
    # three missing hours: 21 stamps, the repeated 01:00 one counting twice = 22 of 25 hours
    gappy = hourly.drop(
        pd.DatetimeIndex(["2018-11-04 10:00", "2018-11-04 11:00", "2018-11-04 12:00"])
    )
    cov = day_coverage(gappy, timezone=LA)
    assert cov.loc[autumn] == pytest.approx(22 / 25)
    assert autumn not in daily_energy_vs_temp(gappy, oat, timezone=LA).index
    assert autumn in daily_energy_vs_temp(gappy, oat, timezone=LA, min_coverage=0.85).index


def test_billing_input_passes_min_coverage_to_the_bills():
    from camber.mandv.billing import BillingSeries

    starts = pd.date_range("2021-01-01", periods=12, freq="MS")
    bills = BillingSeries.from_frame(
        pd.DataFrame({"start": starts, "end": starts + pd.offsets.MonthEnd(0), "energy": 3000.0})
    )
    t_idx = pd.date_range("2021-01-01", "2021-12-31 23:00", freq="1h")
    t = pd.Series(50.0, index=t_idx)
    t = t[~((t.index >= "2021-03-01") & (t.index < "2021-03-10"))]  # March 71 % covered
    assert "2021-03-01" not in daily_energy_vs_temp(bills, t).index.strftime("%Y-%m-%d")
    assert len(daily_energy_vs_temp(bills, t, min_coverage=0.5)) == 12


def test_caltrack_savings_reports_the_dropped_days():
    from camber.mandv.caltrack import caltrack_savings

    idx = pd.date_range("2020-01-01", "2021-12-31 23:00", freq="1h")
    t = pd.Series(50 + 25 * np.sin(2 * np.pi * np.arange(len(idx)) / 8760), index=idx)
    rng = np.random.default_rng(1)
    e = pd.Series(
        10 + 0.4 * np.clip(55 - t.to_numpy(), 0, None) + rng.normal(0, 0.3, len(idx)), index=idx
    )
    base_e, rep_e = e[:"2020-12-31"], e["2021-01-01":] * 0.9
    base_e = _drop_hours(_drop_hours(base_e, "2020-02-03", 12), "2020-06-08", 20)
    res = caltrack_savings(base_e, t[:"2020-12-31"], rep_e, t["2021-01-01":])
    assert res.baseline_n == 364  # 366 days less the two partial ones
    assert any("2 baseline day(s)" in c and "90%" in c for c in res.savings.caveats)
    keep = caltrack_savings(base_e, t[:"2020-12-31"], rep_e, t["2021-01-01":], min_coverage=0.0)
    assert keep.baseline_n == 366
    assert not any("day(s) with meter data" in c for c in keep.savings.caveats)


def test_non_routine_detectors_count_the_dropped_days():
    from camber.mandv.nonroutine import detect_non_routine, detect_step_change, detect_step_changes

    e, t = _hourly(days=120, seed=2)
    gappy = _drop_hours(e, "2021-04-01", 10)
    nr = detect_non_routine(gappy, t)
    assert nr.n_days_dropped == 1 and nr.as_dict()["n_days_dropped"] == 1 and nr.n_total == 119
    assert "n_days_dropped" not in detect_non_routine(e, t).as_dict()  # clean data: as before
    assert detect_step_change(gappy, t).as_dict()["n_days_dropped"] == 1
    sc = detect_step_changes(gappy, t)
    assert sc.n_days_dropped == 1 and any("1 input day(s)" in c for c in sc.caveats)
    assert detect_non_routine(gappy, t, min_coverage=0.0).n_total == 120


def _store_config(tmp_path, hourly, **mv):
    from camber.model.roles import Role
    from camber.portfolio import Portfolio

    ws = str(tmp_path / "ws")
    pf = Portfolio.init(ws)
    pf.add_facility("Coverage site", facility_id="f1", reason="test", activate=True)
    oat = 45.0 + 15.0 * np.sin(np.arange(len(hourly)) / 400.0)
    frame = pd.DataFrame({Role.POWER: hourly, Role.OAT: oat}, index=hourly.index)
    pf.store.write_role_frame(frame, facility_id="f1", equip="meter", equip_class="M")
    return {
        "source": {"kind": "store", "store": os.path.join(ws, "store"), "facility_id": "f1"},
        "equipment": [{"class": "M"}],
        "mv": [{"class": "M", "role": "power", "period": ["2018-01-01", "2018-06-30"], **mv}],
    }


def test_config_mv_min_coverage(tmp_path):
    from camber.config import run_config
    from camber.mvrun import meter_series

    idx = pd.date_range("2018-01-01", "2018-06-30 23:00", freq="1h")
    hourly = pd.Series(30.0 + 0.1 * (idx.dayofyear % 7), index=idx)
    hourly = _drop_hours(_drop_hours(hourly, "2018-02-14", 12), "2018-05-02", 8)
    cfg = _store_config(tmp_path, hourly)
    (ms,) = meter_series(cfg)
    assert ms.daily.attrs["dropped_days"] == 2 and pd.Timestamp("2018-02-14") not in ms.daily.index
    (b,) = [f for f in run_config(cfg).findings if f.rule == "mv_baseline"]
    assert b.metrics["n_days"] == 179 and b.metrics["n_days_dropped"] == 2
    assert any("2 baseline day(s)" in c for c in b.caveats)
    cfg["mv"][0]["min_coverage"] = 0.0
    (b0,) = [f for f in run_config(cfg).findings if f.rule == "mv_baseline"]
    assert b0.metrics["n_days"] == 181 and "n_days_dropped" not in b0.metrics
    (ms0,) = meter_series(cfg)
    assert len(ms0.daily) == 181
    cfg["mv"][0]["min_coverage"] = 1.5
    with pytest.raises(ValueError, match="mv.min_coverage"):
        run_config(cfg)
