"""Billing-period series and the interval-aware non-routine detectors (#54)."""

import numpy as np
import pandas as pd
import pytest

from camber.mandv.billing import (
    BillingSeries,
    as_billing_series,
    daily_weather,
    is_billing_like,
)
from camber.mandv.intervalfit import daily_energy_vs_temp
from camber.mandv.nonroutine import (
    _pelt,
    detect_non_routine,
    detect_step_change,
    detect_step_changes,
)


def _daily_oat(start="2018-01-01", end="2023-12-31", seed=2):
    rng = np.random.default_rng(seed)
    days = pd.date_range(start, end, freq="D")
    return pd.Series(
        50 + 25 * np.sin(2 * np.pi * (days.dayofyear - 105) / 365) + rng.normal(0, 8, len(days)),
        index=days,
    )


def _bills(oat, *, step_at="2021-01-01", step=0.8, seed=3):
    """Calendar-month bills (read on the last day of the month) of a heating meter."""
    rng = np.random.default_rng(seed)
    ends = pd.date_range(oat.index[0], oat.index[-1], freq="ME")
    starts = ends - pd.offsets.MonthBegin(1)
    t = np.array([oat.loc[s:e].mean() for s, e in zip(starts, ends)])
    per_day = 300 + 25 * np.clip(62 - t, 0, None) + rng.normal(0, 20, len(ends))
    per_day[starts >= pd.Timestamp(step_at)] *= step
    days = (ends - starts).days + 1
    return pd.Series(per_day * days, index=ends), t


# ------------------------------------------------------------------ BillingSeries


def test_from_frame_inclusive_end_dates():
    df = pd.DataFrame(
        {
            "from": ["2024-01-03", "2024-02-02"],
            "to": ["2024-02-01", "2024-03-04"],
            "kwh": [3000.0, 3300.0],
            "est": [False, True],
        }
    )
    b = BillingSeries.from_frame(
        df, start="from", end="to", energy="kwh", estimated="est", units="kWh"
    )
    assert b.units == "kWh"
    assert b.days.tolist() == [30, 32]
    assert b.frame["end"].iloc[0] == pd.Timestamp("2024-02-02")  # stored exclusive
    assert b.frame["estimated"].tolist() == [False, True]
    assert b.per_day().round(3).tolist() == [100.0, 103.125]
    assert b.total() == 6300.0
    assert b.total([100.0, 100.0]) == 6200.0  # day-weighted total of a per-day series


def test_from_frame_exclusive_and_validation():
    df = pd.DataFrame({"start": ["2024-01-01"], "end": ["2024-01-31"], "energy": [1.0]})
    assert BillingSeries.from_frame(df, end_inclusive=False).days.tolist() == [30]
    with pytest.raises(ValueError, match="overlap"):
        BillingSeries.from_frame(
            pd.DataFrame(
                {
                    "start": ["2024-01-01", "2024-01-20"],
                    "end": ["2024-01-31", "2024-02-20"],
                    "energy": [1.0, 1.0],
                }
            )
        )
    with pytest.raises(ValueError, match="end after"):
        BillingSeries.from_frame(
            pd.DataFrame({"start": ["2024-02-01"], "end": ["2024-01-01"], "energy": [1.0]})
        )
    with pytest.raises(ValueError, match="missing columns"):
        BillingSeries(pd.DataFrame({"start": [], "energy": []}))
    with pytest.raises(ValueError, match="disagrees"):
        BillingSeries(
            pd.DataFrame(
                {"start": ["2024-01-01"], "end": ["2024-01-31"], "days": [31], "energy": [1.0]}
            )
        )


def test_from_reads_read_dates_month_labels_and_first_start():
    reads = pd.Series(
        [10.0, 20.0, 30.0], index=pd.to_datetime(["2024-01-31", "2024-02-29", "2024-03-31"])
    )
    b = BillingSeries.from_reads(reads, estimated=[False, True, False])
    # (previous read, read]: February covers Feb 1..29, March covers Mar 1..31
    assert b.frame["start"].tolist()[1:] == [pd.Timestamp("2024-02-01"), pd.Timestamp("2024-03-01")]
    assert b.days.tolist()[1:] == [29, 31]
    assert b.frame["estimated"].tolist() == [False, True, False]
    b2 = BillingSeries.from_reads(reads, first_start="2024-01-01")
    assert b2.days.tolist() == [31, 29, 31]
    labels = pd.Series([1.0, 2.0], index=pd.to_datetime(["2024-01-01", "2024-02-01"]))
    b3 = BillingSeries.from_reads(labels)  # month-start stamps are calendar-month labels
    assert b3.days.tolist() == [31, 29]
    assert b3.frame["end"].iloc[-1] == pd.Timestamp("2024-03-01")
    with pytest.raises(ValueError, match="two reads"):
        BillingSeries.from_reads(pd.Series([1.0], index=pd.to_datetime(["2024-01-15"])))
    assert as_billing_series(b) is b


def test_period_weather_hourly_and_daily():
    hours = pd.date_range("2024-01-01", "2024-01-31 23:00", freq="1h")
    # 60F for half of every day, 40F for the other half: daily mean 50
    t = pd.Series(np.where(hours.hour < 12, 60.0, 40.0), index=hours)
    b = BillingSeries.from_frame(
        pd.DataFrame({"start": ["2024-01-01"], "end": ["2024-01-10"], "energy": [100.0]})
    )
    w = b.period_weather(t, base_f=55.0)
    assert w["oat"].iloc[0] == pytest.approx(50.0)
    # hourly degree-days: only the 40F hours count below 55F -> 7.5/day, not 5/day from the mean
    assert w["hdd"].iloc[0] == pytest.approx(7.5 * 10)
    assert w["cdd"].iloc[0] == pytest.approx(2.5 * 10)  # the 60F hours, 5F above 55F
    assert w["coverage"].iloc[0] == 1.0
    daily = t.resample("D").mean()
    wd = b.period_weather(daily, base_f=55.0)
    assert wd["hdd"].iloc[0] == pytest.approx(5.0 * 10)
    # a period with half its days missing is scaled to full length and reports its coverage
    wp = b.period_weather(daily.iloc[:5], base_f=55.0)
    assert wp["coverage"].iloc[0] == 0.5
    assert wp["hdd"].iloc[0] == pytest.approx(50.0)
    assert daily_weather(pd.Series(dtype=float)).empty


def test_energy_vs_temp_drops_uncovered_bills():
    oat = _daily_oat("2020-01-01", "2020-06-30")
    e, _ = _bills(oat)
    b = as_billing_series(e)
    out = b.energy_vs_temp(oat.loc[:"2020-05-15"])
    assert out.index[-1] < pd.Timestamp("2020-05-01")  # May is half-covered: dropped
    assert {"energy", "oat", "hdd", "cdd", "days", "estimated", "coverage"} <= set(out.columns)


# ------------------------------------------------------------------ daily_energy_vs_temp


def test_bills_pair_with_their_own_period_mean():
    oat = _daily_oat()
    e, bill_t = _bills(oat)
    df = daily_energy_vs_temp(e, oat, rate_is_energy_rate=False)
    assert df.attrs.get("billing") is True
    assert len(df) == len(e)
    np.testing.assert_allclose(df["oat"].to_numpy(), bill_t)
    np.testing.assert_allclose(df["energy"].to_numpy(), e.to_numpy() / df["days"].to_numpy())
    assert df.index[0] == pd.Timestamp("2018-01-01")  # indexed by bill start


def test_daily_and_same_grid_input_unchanged():
    oat = _daily_oat("2020-01-01", "2020-12-31")
    e = pd.Series(np.arange(len(oat), dtype=float), index=oat.index)
    df = daily_energy_vs_temp(e, oat, rate_is_energy_rate=False)
    assert not df.attrs.get("billing")
    assert list(df.columns) == ["energy", "oat"]
    assert len(df) == len(oat)
    # monthly energy with monthly temperature: already paired, not treated as billing
    em = e.resample("MS").sum()
    tm = oat.resample("MS").mean()
    assert not is_billing_like(em, tm)
    dm = daily_energy_vs_temp(em, tm, rate_is_energy_rate=False)
    assert not dm.attrs.get("billing")
    # a rate series is never read as bills (it is integrated as before)
    assert not daily_energy_vs_temp(em, oat).attrs.get("billing")


# ------------------------------------------------------------------ detectors


def test_detect_step_changes_on_bills_counts_days():
    oat = _daily_oat()
    e, _ = _bills(oat)
    r = detect_step_changes(e, oat)
    assert r.billing and r.n_periods == 72
    assert r.n_days == int((oat.index[-1] - oat.index[0]).days) + 1
    assert [s.date for s in r.steps] == [pd.Timestamp("2021-01-01")]
    assert r.steps[0].delta < 0
    d = r.as_dict()
    assert d["billing"] is True and d["n_periods"] == 72
    # min_segment_days counts days of service, not bills: 72 bills are not 2 x 1500 days
    with pytest.raises(ValueError, match="days"):
        detect_step_changes(e, oat, min_segment_days=1500)
    # 400-day segments are satisfiable (the old row count would have refused 72 < 800)
    assert detect_step_changes(e, oat, min_segment_days=400).n_periods == 72


def test_detect_step_change_single_on_bills():
    oat = _daily_oat()
    e, _ = _bills(oat)
    r = detect_step_change(e, oat, min_segment_days=90)
    assert r.detected and r.date == pd.Timestamp("2021-01-01")
    assert r.n_days > 2000 and r.n_periods == 72
    assert r.as_dict()["billing"] is True
    with pytest.raises(ValueError, match="bills"):
        detect_step_change(e.iloc[:5], oat)


def test_detect_non_routine_on_bills():
    oat = _daily_oat()
    e, _ = _bills(oat, step=1.0)
    e.iloc[30] *= 3.0  # one bill far off
    r = detect_non_routine(e, oat)
    assert r.billing and r.n_total == 72 and r.n_days > 2000
    assert bool(r.mask.iloc[30])
    assert r.as_dict()["billing"] is True
    with pytest.raises(ValueError, match="bills"):
        detect_non_routine(e.iloc[:4], oat)


def test_billing_series_object_accepted_directly():
    oat = _daily_oat()
    e, _ = _bills(oat)
    b = BillingSeries.from_reads(e)
    assert detect_step_changes(b, oat).steps[0].date == pd.Timestamp("2021-01-01")


def test_daily_results_carry_no_billing_keys():
    oat = _daily_oat("2020-01-01", "2020-12-31")
    e = pd.Series(500 + 10 * np.clip(60 - oat.to_numpy(), 0, None), index=oat.index)
    e.loc["2020-07-01":] *= 0.8
    r = detect_step_changes(e, oat)
    assert not r.billing and "billing" not in r.as_dict()
    assert "billing" not in detect_step_change(e, oat).as_dict()
    assert "billing" not in detect_non_routine(e, oat).as_dict()


def test_pelt_span_of_rows_matches_plain_pelt():
    rng = np.random.default_rng(5)
    x = np.concatenate([rng.normal(0, 1, 60), rng.normal(3, 1, 50), rng.normal(-1, 1, 70)])
    plain = _pelt(x, scale=1.0, penalty=3 * np.log(len(x)), min_seg=10)
    spanned = _pelt(
        x, scale=1.0, penalty=3 * np.log(len(x)), min_seg=10, span=np.arange(len(x) + 1)
    )
    assert plain == spanned
    # measured in days of 30 per row, a 10-row segment is 300 days long
    days = np.arange(len(x) + 1) * 30
    assert _pelt(x, scale=1.0, penalty=3 * np.log(len(x)), min_seg=300, span=days) == plain
