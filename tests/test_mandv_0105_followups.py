"""Follow-ups to the 0.105 M&V work (#166-#174). Synthetic data only.

#166 the CalTRACK least-squares solve survives an SVD that does not converge, and one failing
month segment no longer aborts the other eleven; #167 CalTRACK Hourly declines when nothing can be
predicted; #168 the temperature screen runs at prediction time too; #169 the daily frame counts a
duplicate timestamp once without a zone; #170 the CalTRACK duplicate message says a zone is needed;
#171 CalTRACK predicts clip negatives on request; #172 ModelFitError names why no points were left;
#173 error messages print plain numbers; #174 the ensemble description helper.
"""

from __future__ import annotations

import json
import warnings

import numpy as np
import pandas as pd
import pytest

from camber.mandv import _caltrack as K
from camber.mandv import caltrack_hourly as CH
from camber.mandv.caltrack_daily import (
    CalTRACKBillingModel,
    CalTRACKDailyModel,
    daily_usage,
    fit_caltrack_daily,
)
from camber.mandv.caltrack_hourly import (
    CalTRACKHourlyModel,
    caltrack_hourly_savings,
    fit_caltrack_hourly,
    hourly_frame,
)
from camber.mandv.degreeday import MeanTempDegreeDayModel, fit_mean_temp_degree_day
from camber.mandv.intervalfit import daily_energy_vs_temp, hourly_energy_vs_temp
from camber.mandv.models import ChangePointModel, ModelFitError, best_model
from camber.mandv.stats import avoided_energy_savings
from camber.mandv.towt import TOWTAtIndex, TOWTModel, fit_towt
from camber.tsparse import TimezoneWarning

SENTINEL = -237.0
IDX = pd.date_range("2017-01-01", periods=8760, freq="h")


def year(index=IDX, seed=0, scale=1.0, offset=0.0):
    """A sinusoidal year with a Mon-Fri 08-18 schedule and cooling above 65 degF."""
    doy = index.dayofyear.to_numpy()
    hr = index.hour.to_numpy()
    T = 55 - 25 * np.cos(2 * np.pi * doy / 365) + 5 * np.sin(2 * np.pi * hr / 24)
    rng = np.random.default_rng(seed)
    occ = (index.dayofweek < 5) & (hr >= 8) & (hr < 18)
    y = scale * (5 + 10 * occ + 0.2 * np.maximum(T - 65, 0)) + rng.normal(0, 0.1, len(index))
    return pd.Series(y + offset, index=index), pd.Series(T, index=index)


@pytest.fixture(scope="module")
def hourly_model():
    e, t = year()
    return fit_caltrack_hourly(e, t, intervention_start="2018-01-01")


def _daily(n=200, seed=0):
    rng = np.random.default_rng(seed)
    T = rng.uniform(10, 90, n)
    y = 50 + 2.0 * np.clip(55 - T, 0, None) + 1.5 * np.clip(T - 70, 0, None) + rng.normal(0, 1, n)
    return T, y


def _weeks(weeks=10, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2021-01-04", periods=24 * 7 * weeks, freq="1h")
    t = pd.Series(45 + 15 * np.sin(np.arange(len(idx)) / 70.0), index=idx)
    occ = (idx.dayofweek < 5) & (idx.hour >= 8) & (idx.hour < 18)
    e = pd.Series(30 + 25 * occ + 0.8 * np.clip(55 - t.to_numpy(), 0, None), index=idx)
    return e + rng.normal(0, 0.5, len(idx)), t


def _raise(*a, **k):
    raise np.linalg.LinAlgError("SVD did not converge")


# ----------------------------------------------------------------- #166 the least-squares solve
@pytest.mark.parametrize("weighted", [False, True])
@pytest.mark.parametrize("fail", [("pinv",), ("pinv", "lstsq")])
def test_wls_fallbacks_match_the_pseudo_inverse(monkeypatch, weighted, fail):
    rng = np.random.default_rng(4)
    X = np.column_stack([np.ones(300), rng.normal(size=(300, 4))])
    y = X @ np.array([2.0, 1.0, -0.5, 0.3, 0.0]) + rng.normal(0, 0.2, 300)
    w = rng.uniform(0.5, 1.0, 300) if weighted else None
    ref = K.wls(X, y, w)
    for name in fail:
        monkeypatch.setattr(np.linalg, name, _raise)
    got = K.wls(X, y, w)
    assert got.rank == ref.rank == 5 and got.n == ref.n
    np.testing.assert_allclose(got.params, ref.params, rtol=1e-9, atol=1e-12)
    np.testing.assert_allclose(got.bse, ref.bse, rtol=1e-8)
    np.testing.assert_allclose(got.pvalues, ref.pvalues, rtol=1e-6, atol=1e-12)
    assert got.r_squared == pytest.approx(ref.r_squared, rel=1e-12)
    assert got.r_squared_adj == pytest.approx(ref.r_squared_adj, rel=1e-12)


def test_wls_raises_when_every_path_fails_on_a_rank_deficient_design(monkeypatch):
    X = np.column_stack([np.ones(10), np.arange(10.0), 2 * np.arange(10.0)])
    monkeypatch.setattr(np.linalg, "pinv", _raise)
    monkeypatch.setattr(np.linalg, "lstsq", _raise)
    with pytest.raises(np.linalg.LinAlgError):
        K.wls(X, np.arange(10.0))


@pytest.mark.parametrize("which", ["design", "every"])
def test_hourly_fit_is_unchanged_when_the_pseudo_inverse_fails(monkeypatch, hourly_model, which):
    ref, _ = hourly_model
    e, t = year()
    real = np.linalg.pinv

    def pinv(a, *args, **kw):
        # "design": only the tall weighted design fails, as observed; "every": all calls do
        if which == "every" or a.shape[0] != a.shape[1]:
            raise np.linalg.LinAlgError("SVD did not converge")
        return real(a, *args, **kw)

    monkeypatch.setattr(np.linalg, "pinv", pinv)
    m, _ = fit_caltrack_hourly(e, t, intervention_start="2018-01-01")
    assert all(s.status == "fitted" for s in m.segments.values())
    a = ref.predict(IDX, t.to_numpy()).to_numpy()
    b = m.predict(IDX, t.to_numpy()).to_numpy()
    np.testing.assert_allclose(b, a, rtol=1e-9, atol=1e-9)


def test_one_failing_segment_is_unfit_and_the_others_fit(monkeypatch):
    e, t = year()
    real = CH.wls
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 5:  # the third segment's preliminary model (two solves per segment)
            raise np.linalg.LinAlgError("SVD did not converge")
        return real(*a, **k)

    monkeypatch.setattr(CH, "wls", flaky)
    res = caltrack_hourly_savings(
        e, t, *year(pd.date_range("2018-01-01", periods=24 * 120, freq="h"), seed=3, scale=0.9)
    )
    seg = res.model.segments["feb-mar-apr-weighted"]
    assert seg.status == "unfit" and "LinAlgError" in seg.reason
    assert sum(s.status == "fitted" for s in res.model.segments.values()) == 11
    assert res.sufficiency["segments"]["feb-mar-apr-weighted"] == "unfit"
    assert res.sufficiency["reporting_counts"]["n_no_segment_model"] == 31 * 24
    assert any("feb-mar-apr-weighted could not be fitted" in c for c in res.savings.caveats)
    assert not res.savings.declined and res.savings.avoided_energy > 0
    d = json.loads(json.dumps(res.model.as_dict()))
    assert d["segments"]["feb-mar-apr-weighted"]["reason"] == seg.reason
    assert "reason" not in d["segments"]["jan-feb-mar-weighted"]  # a fitted segment's dict
    assert CalTRACKHourlyModel.from_dict(d).segments["feb-mar-apr-weighted"].reason == seg.reason


# ----------------------------------------------------------------- #167 decline when nothing
def test_hourly_savings_decline_when_no_reporting_hour_is_predicted():
    e, t = year()
    ridx = pd.date_range("2018-01-01", periods=24 * 30, freq="h")
    re_, rt = year(ridx, seed=2)
    res = caltrack_hourly_savings(e, t, re_, pd.Series(np.nan, index=ridx))
    s = res.savings
    assert s.declined and "no reporting hour could be predicted" in s.declined_reason
    assert f"{24 * 30} without a temperature" in s.declined_reason
    assert s.avoided_energy is None and s.baseline_projected is None
    json.dumps(res.as_dict())
    ok = caltrack_hourly_savings(e, t, re_, rt)
    assert not ok.savings.declined and ok.savings.avoided_energy is not None


# ----------------------------------------------------------------- #168 screen at prediction time
def test_towt_predict_screens_with_the_fitted_bounds():
    e, t = _weeks()
    m = fit_towt(e, t)
    idx = t.index[:3]
    p, c = m.predict(idx, np.array([SENTINEL, 50.0, np.nan]), return_counts=True)
    assert np.isnan(p[0]) and np.isfinite(p[1]) and np.isnan(p[2])
    assert c["n_temp_screened"] == 1 and c["n_no_temperature"] == 1
    assert "temp_bounds" not in m.as_dict()  # the default range: the dict is unchanged
    wide = fit_towt(e, t, temp_bounds=(-300.0, 200.0))
    assert np.isfinite(wide.predict(idx[:1], np.array([SENTINEL]))).all()
    d = json.loads(json.dumps(wide.as_dict()))
    assert d["temp_bounds"] == [-300.0, 200.0]
    assert TOWTModel.from_dict(d).temp_bounds == (-300.0, 200.0)
    inf = fit_towt(e, t, temp_bounds=(-np.inf, np.inf))
    d = json.loads(json.dumps(inf.as_dict()))  # strict JSON: no Infinity
    assert d["temp_bounds"] == [None, None]
    assert TOWTModel.from_dict(d).temp_bounds == (-np.inf, np.inf)
    old = m.as_dict()  # a dict from before 0.105 has no bounds: the default screen applies
    assert np.isnan(TOWTModel.from_dict(old).predict(idx[:1], np.array([SENTINEL]))).all()


def test_towt_savings_exclude_a_screened_row_without_grading_it():
    e, t = _weeks(weeks=12)
    base, rep = slice(0, 24 * 7 * 8), slice(24 * 7 * 8, None)
    m = fit_towt(e.iloc[base], t.iloc[base])
    rt = t.iloc[rep].to_numpy().copy()
    y = e.iloc[rep].to_numpy() * 0.9
    kw = {"cv_rmse": 0.05, "n_baseline": 24 * 7 * 8, "p_baseline": m.n_params}
    clean = avoided_energy_savings(TOWTAtIndex(m, t.index[rep]), rt, y, **kw)
    rt[5] = SENTINEL
    s = avoided_energy_savings(TOWTAtIndex(m, t.index[rep]), rt, y, **kw)
    assert not s.declined and s.coverage["tier"] == clean.coverage["tier"]
    assert s.coverage["n_used"] == s.coverage["n_report"] - 1
    assert s.coverage["max_beyond_low"] == clean.coverage["max_beyond_low"]
    assert any("implausible temperature" in c for c in s.caveats)


def test_change_point_and_degree_day_predict_screen():
    T, y = _daily()
    m = best_model(T, y)
    p, c = m.predict(np.array([SENTINEL, 50.0]), return_counts=True)
    assert np.isnan(p[0]) and np.isfinite(p[1]) and c["n_temp_screened"] == 1
    assert np.isnan(m.predict(SENTINEL)) and np.isfinite(m.predict(50.0))
    assert "temp_bounds" not in m.as_dict()
    wide = best_model(T, y, temp_bounds=(-300, 200))
    assert np.isfinite(wide.predict(SENTINEL))
    assert ChangePointModel.from_dict(wide.as_dict()).temp_bounds == (-300.0, 200.0)
    cov = m.coverage(np.r_[np.full(20, 50.0), SENTINEL])
    assert cov.tier == "in_range" and cov.n_used == 20

    rng = np.random.default_rng(1)
    tavg = rng.uniform(20, 80, 36)
    e = 100 + 3 * np.clip(60 - tavg, 0, None) + rng.normal(0, 1, 36)
    dd = fit_mean_temp_degree_day(tavg, e, kind="heating")
    p, c = dd.predict(np.array([SENTINEL, 50.0]), return_counts=True)
    assert np.isnan(p[0]) and np.isfinite(p[1]) and c["n_temp_screened"] == 1
    assert "temp_bounds" not in dd.as_dict()
    dw = fit_mean_temp_degree_day(tavg, e, kind="heating", temp_bounds=(-300, 200))
    assert MeanTempDegreeDayModel.from_dict(dw.as_dict()).temp_bounds == (-300.0, 200.0)
    assert np.isfinite(dw.predict(np.array([SENTINEL]))).all()
    assert dd.coverage(np.r_[np.full(10, 50.0), SENTINEL]).tier == "in_range"


def test_change_point_savings_exclude_a_screened_row():
    T, y = _daily()
    m = best_model(T, y)
    Tr = np.linspace(20, 80, 60)
    yr = np.asarray(m.predict(Tr)) * 0.9
    kw = {"cv_rmse": 0.05, "n_baseline": len(T), "p_baseline": 5}
    clean = avoided_energy_savings(m, Tr, yr, **kw)
    Tr2 = Tr.copy()
    Tr2[0] = SENTINEL
    s = avoided_energy_savings(m, Tr2, yr, **kw)
    assert not s.declined and s.coverage["tier"] == clean.coverage["tier"] == "in_range"
    assert s.coverage["n_used"] == 59
    assert any("1 of them had no finite baseline projection" in c for c in s.caveats)


def test_caltrack_hourly_predict_screens(hourly_model):
    m, _ = hourly_model
    q = pd.DatetimeIndex(["2017-07-10 10:00", "2017-07-10 11:00", "2017-07-10 12:00"])
    p, c = m.predict(q, [SENTINEL, 70.0, np.nan], return_counts=True)
    assert np.isnan(p.iloc[0]) and np.isfinite(p.iloc[1]) and np.isnan(p.iloc[2])
    assert c["n_temp_screened"] == 1 and c["n_no_temperature"] == 1
    assert p.attrs["n_temp_screened"] == 1
    assert "temp_bounds" not in m.as_dict()
    e, t = year()
    wide, _ = fit_caltrack_hourly(
        e, t, intervention_start="2018-01-01", segment_type="single", temp_bounds=(-300, 200)
    )
    assert np.isfinite(wide.predict(q[:1], [SENTINEL]).iloc[0])
    d = json.loads(json.dumps(wide.as_dict()))
    assert CalTRACKHourlyModel.from_dict(d).temp_bounds == (-300.0, 200.0)


DAYS = pd.date_range("2017-01-01", periods=365, freq="D")
T_W = 30.0 + (np.arange(365) % 61)


def _caltrack_daily(**kw):
    U = 10 + 2 * np.maximum(60 - T_W, 0.0)
    temp = pd.Series(np.repeat(T_W, 24), index=pd.date_range("2017-01-01", periods=8760, freq="h"))
    return fit_caltrack_daily(pd.Series(U, index=DAYS), temp, fuel="gas", **kw)


def test_caltrack_daily_and_billing_predict_screen():
    m, _ = _caltrack_daily()
    p, c = m.predict(DAYS[:2], [SENTINEL, 50.0], return_counts=True)
    assert np.isnan(p.iloc[0]) and p.iloc[1] == pytest.approx(30.0) and c["n_temp_screened"] == 1
    assert "temp_bounds" not in m.as_dict()
    wide, _ = _caltrack_daily(temp_bounds=(-300, 200))
    assert CalTRACKDailyModel.from_dict(wide.as_dict()).temp_bounds == (-300.0, 200.0)
    assert np.isfinite(wide.predict(DAYS[:1], [SENTINEL]).iloc[0])
    hours = pd.date_range("2018-01-01", periods=48, freq="h")
    temp = pd.Series(50.0, index=hours)
    temp.iloc[:24] = SENTINEL  # the first day has no plausible hour
    p, c = m.predict(pd.DatetimeIndex(["2018-01-01", "2018-01-02"]), temp, return_counts=True)
    assert np.isnan(p.iloc[0]) and np.isfinite(p.iloc[1]) and c["n_temp_screened"] == 24

    bm = CalTRACKBillingModel(
        "hdd_only", 10.0, beta_hdd=2.0, heating_balance_point=60.0, temp_bounds=None
    )
    periods = pd.DataFrame(
        {
            "start": pd.to_datetime(["2018-01-01", "2018-01-11"]),
            "end": pd.to_datetime(["2018-01-11", "2018-01-21"]),
        }
    )
    th = pd.Series(50.0, index=pd.date_range("2018-01-01", periods=480, freq="h"))
    th.iloc[:240] = SENTINEL
    p, c = bm.predict(periods, th, return_counts=True)
    assert np.isnan(p.iloc[0]) and p.iloc[1] == pytest.approx(10 * (10 + 2 * 10))
    assert c["n_temp_screened"] == 240
    bw = CalTRACKBillingModel("hdd_only", 10.0, beta_hdd=2.0, heating_balance_point=60.0)
    bw.temp_bounds = (-300.0, 200.0)
    assert np.isfinite(bw.predict(periods, th).iloc[0])


# ----------------------------------------------------------------- #169 daily duplicates, no zone
def _with_repeated_hour(kind="energy"):
    idx = pd.date_range("2018-11-03", "2018-11-06", freq="h", inclusive="left")
    s = pd.Series(1.0, index=idx)
    t = pd.Series(50.0, index=idx)
    dup = pd.Timestamp("2018-11-04 01:00")
    s = pd.concat([s, pd.Series([1.0], index=[dup])]).sort_index(kind="stable")
    t = pd.concat([t, pd.Series([52.0], index=[dup])]).sort_index(kind="stable")
    return s, t


@pytest.mark.parametrize("rate", [False, True])
def test_daily_frame_counts_a_duplicate_once_without_a_zone(rate):
    s, t = _with_repeated_hour()
    with pytest.warns(TimezoneWarning, match="1 duplicate timestamp") as rec:
        df = daily_energy_vs_temp(s, t, rate_is_energy_rate=rate)
    with pytest.warns(TimezoneWarning, match="1 duplicate timestamp") as rec_h:
        hf = hourly_energy_vs_temp(s, t, rate_is_energy_rate=rate)
    assert str(rec[0].message) == str(rec_h[0].message)
    assert df.attrs["n_duplicate_stamps"] == hf.attrs["n_duplicate_stamps"] == 1
    day = pd.Timestamp("2018-11-04")
    assert df.loc[day, "energy"] == pytest.approx(24.0)  # not 25: the stamp is one hour
    assert df.loc[day, "oat"] == pytest.approx((23 * 50 + 51) / 24)


def test_daily_frame_with_a_zone_and_clean_data_are_unchanged():
    s, t = _with_repeated_hour()
    with warnings.catch_warnings():
        warnings.simplefilter("error", TimezoneWarning)
        z = daily_energy_vs_temp(s, t, rate_is_energy_rate=False, timezone="America/New_York")
        clean = s[~s.index.duplicated()]
        c = daily_energy_vs_temp(clean, t[~t.index.duplicated()], rate_is_energy_rate=False)
    assert z.loc[pd.Timestamp("2018-11-04"), "energy"] == pytest.approx(25.0)  # two real hours
    assert z.attrs["n_duplicate_stamps"] == 0 and c.attrs["n_duplicate_stamps"] == 0
    assert c.loc[pd.Timestamp("2018-11-04"), "energy"] == pytest.approx(24.0)


# ----------------------------------------------------------------- #170 the duplicate message
def test_caltrack_duplicate_message_asks_for_a_timezone():
    s, t = _with_repeated_hour()
    with pytest.raises(ValueError, match="cannot be told from a real duplicate") as ei:
        hourly_frame(s, t)
    assert "timezone=" in str(ei.value) and "outside a daylight-saving" not in str(ei.value)
    july = pd.date_range("2018-07-01", periods=48, freq="h")
    j = pd.concat([pd.Series(1.0, index=july), pd.Series([1.0], index=[july[5]])]).sort_index()
    with pytest.raises(ValueError, match="outside a daylight-saving fall-back hour in Amer"):
        daily_usage(j, timezone="America/New_York")


# ----------------------------------------------------------------- #171 clip_negative
def test_caltrack_hourly_predict_clips_on_request():
    e, t = year(offset=-10.0)  # unoccupied hours run about -5
    m, _ = fit_caltrack_hourly(e, t, intervention_start="2018-01-01", fuel="gas")
    q = pd.date_range("2017-03-06", periods=24 * 7, freq="h")
    tq = t.reindex(q).to_numpy()
    raw, c0 = m.predict(q, tq, return_counts=True)
    clipped, c1 = m.predict(q, tq, clip_negative=True, return_counts=True)
    neg = raw.to_numpy() < 0
    assert neg.sum() > 50 and c0["n_clipped"] == 0 and c1["n_clipped"] == int(neg.sum())
    assert (clipped.to_numpy()[neg] == 0).all()
    assert np.array_equal(clipped.to_numpy()[~neg], raw.to_numpy()[~neg])
    assert clipped.attrs["n_clipped"] == c1["n_clipped"]


def test_caltrack_daily_and_billing_predict_clip():
    m = CalTRACKDailyModel("hdd_only", -5.0, beta_hdd=1.0, heating_balance_point=60.0)
    days = pd.DatetimeIndex(["2018-01-01", "2018-01-02"])
    assert m.predict(days, [70.0, 50.0]).tolist() == [-5.0, 5.0]
    p, c = m.predict(days, [70.0, 50.0], clip_negative=True, return_counts=True)
    assert p.tolist() == [0.0, 5.0] and c["n_clipped"] == 1
    f = m.predict(days, [70.0, 50.0], clip_negative=True, disaggregate=True)
    assert f["predicted"].tolist() == [0.0, 5.0] and f["base_load"].tolist() == [-5.0, -5.0]
    b = CalTRACKBillingModel("intercept_only", -1.0)
    periods = pd.DataFrame(
        {"start": [pd.Timestamp("2018-01-01")], "end": [pd.Timestamp("2018-01-11")]}
    )
    th = pd.Series(50.0, index=pd.date_range("2018-01-01", periods=240, freq="h"))
    assert b.predict(periods, th).iloc[0] == pytest.approx(-10.0)
    p, c = b.predict(periods, th, clip_negative=True, return_counts=True)
    assert p.iloc[0] == 0.0 and c["n_clipped"] == 1


# ----------------------------------------------------------------- #172 ModelFitError's cause
def test_model_fit_error_names_screened_temperatures():
    T, y = _daily(n=30)
    with pytest.raises(ModelFitError, match="30 temperature value") as ei:
        best_model(np.full(30, SENTINEL), y)
    err = ei.value
    assert err.causes == {"n_points": 0, "n_temp_screened": 30}
    assert "only 0 usable point(s) remained" in str(err)
    assert [c["kind"] for c in err.candidates] == ["2P", "3PC", "3PH", "4P", "5P"]
    T2 = T.copy()
    T2[2:] = np.nan
    with pytest.raises(ModelFitError, match="28 row\\(s\\) missing") as ei:
        best_model(T2, y)
    assert ei.value.causes == {"n_points": 2, "n_missing": 28}


def test_model_fit_error_names_days_dropped_by_coverage():
    from camber.mandv._mvform import fit

    idx = pd.date_range("2018-01-01", periods=24 * 20, freq="h")
    e = pd.Series(1.0, index=idx)[idx.hour < 12]  # every day half covered
    t = pd.Series(50.0, index=idx)
    frame = daily_energy_vs_temp(e, t, rate_is_energy_rate=False)
    assert frame.empty and frame.attrs["dropped_days"] == 20
    with pytest.raises(ModelFitError, match="20 day\\(s\\) dropped by the day-coverage rule") as ei:
        fit(frame)
    assert ei.value.causes["dropped_days"] == 20 and len(ei.value.candidates) == 5


def test_model_fit_error_without_a_points_problem_is_unchanged():
    T, y = _daily()
    with pytest.raises(ModelFitError) as ei:
        best_model(T, y, kinds=("NOPE",))
    assert ei.value.causes is None and "remained" not in str(ei.value)


def test_billing_model_fit_error_names_dropped_bills():
    from camber.mandv._mvform import best_billing_model

    frame = pd.DataFrame({"oat": [], "energy": [], "days": [], "hdd": [], "cdd": []}, dtype=float)
    frame.attrs.update(
        {"dd_kind": "DD-H", "heating_base_f": 60.0, "dd_bases_fitted": False, "dropped_bills": 7}
    )
    with pytest.raises(ModelFitError, match="7 bill\\(s\\) dropped") as ei:
        best_billing_model(frame)
    assert ei.value.causes == {"n_points": 0, "dropped_bills": 7}
    assert any(c["kind"] == "DD-H" for c in ei.value.candidates)


# ----------------------------------------------------------------- #173 plain numbers
def test_towt_unseen_message_prints_plain_ints():
    e, t = _weeks()
    wk = e.index.dayofweek < 5
    m = fit_towt(e[wk], t[wk])
    sat = t.index[t.index.dayofweek == 5][:3]
    with pytest.raises(ValueError, match=r"e\.g\. \[120, 121, 122\]") as ei:
        m.predict(sat, t.reindex(sat).to_numpy())
    assert "np." not in str(ei.value)


def test_chaining_message_prints_dates():
    from camber.mandv.methods import _check_sep_periods

    ts = pd.Timestamp
    periods = {
        "baseline": (ts("2020-01-01"), ts("2020-12-31")),
        "intermediate": (ts("2022-01-01"), ts("2022-12-31")),
        "reporting": (ts("2021-01-01"), ts("2021-12-31")),
    }
    with pytest.raises(ValueError, match=r"intermediate \['2022-01-01', '2022-12-31'\]") as ei:
        _check_sep_periods(periods)
    assert "Timestamp(" not in str(ei.value)


# ----------------------------------------------------------------- #174 names
def test_ensemble_description_from_registry_rows():
    from camber.mandv.names import MV_NAMES, component_description, ensemble_description

    text = ensemble_description(["3PC", "towt"], "the prediction is the unweighted mean of the two")
    assert text == (
        "Ensemble approach combining a three-parameter cooling change-point model (3PC) [ASHRAE "
        "G14-2014] and a Time-of-Week-and-Temperature (TOWT) model (Mathieu et al. 2011; LBNL "
        "RMV2.0), CAMBER variant [Mathieu et al. 2011]; the prediction is the unweighted mean of "
        "the two."
    )
    T, y = _daily()
    m = best_model(T, y)
    e, t = _weeks()
    tw = fit_towt(e, t)
    auto = ensemble_description([m, tw.as_dict()], "the lower CV(RMSE) of the two is used.")
    assert auto.startswith("Ensemble approach combining a ") and auto.endswith("is used.")
    assert MV_NAMES[m.kind].standard_name[1:] in auto
    fb = {"type": "ChangePointModel", "kind": "5P", "change_points": []}
    assert component_description(fb).startswith("a two-parameter (2P)")
    assert component_description("degreeday", cite="all") == (
        "a degree-day on period-mean temperature (CAMBER)"
    )
    assert "[Fels 1986 (PRISM); Kissock" in component_description("DD-H", cite="all")
    with pytest.raises(ValueError):
        ensemble_description(["3PC"], "mean")
    with pytest.raises(ValueError):
        ensemble_description(["3PC", "4P"], " ")


def test_differences_are_descriptions_not_instructions():
    from camber.mandv.names import MV_NAMES

    imperative = ("Report ", "report the", "use `", "Use ", "pass ", "Pass ", "needs a declared")
    for code, row in MV_NAMES.items():
        for word in imperative:
            assert word not in row.differences, (code, word)
