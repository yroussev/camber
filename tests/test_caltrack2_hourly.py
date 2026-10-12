"""CalTRACK 2.0 Hourly (camber.mandv.caltrack_hourly; 0.105, #156).

Every test names the spec rule it covers (CT-H-*) and the hand-computed case (H*) from the CAMBER
CalTRACK spec. Synthetic data only.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from camber.mandv import _caltrack as K
from camber.mandv.caltrack_hourly import (
    SEGMENT_NAMES,
    CalTRACKHourlyModel,
    bin_features,
    caltrack_hourly_savings,
    fit_caltrack_hourly,
    fit_temperature_bins,
    hour_of_week,
    hourly_frame,
    occupancy_lookup,
    segment_weights,
    single_model_tests,
)
from camber.mandv.sufficiency import InsufficientBaseline

IDX = pd.date_range("2017-01-01", periods=8760, freq="h")


def h6(index=IDX, seed=0, scale=1.0):
    """Spec case H6: a sinusoidal year, a Mon-Fri 08-18 schedule, cooling above 65 degF."""
    doy = index.dayofyear.to_numpy()
    hr = index.hour.to_numpy()
    T = 55 - 25 * np.cos(2 * np.pi * doy / 365) + 5 * np.sin(2 * np.pi * hr / 24)
    rng = np.random.default_rng(seed)
    occ = (index.dayofweek < 5) & (hr >= 8) & (hr < 18)
    y = scale * (5 + 10 * occ + 0.2 * np.maximum(T - 65, 0)) + rng.normal(0, 0.1, len(index))
    return pd.Series(y, index=index), pd.Series(T, index=index), occ


@pytest.fixture(scope="module")
def fitted():
    e, t, _ = h6()
    return fit_caltrack_hourly(e, t, intervention_start="2018-01-01")


# ----------------------------------------------------------------- CT-H-3, CT-H-2: time and weights
def test_hour_of_week_starts_monday_midnight_ct_h_3_1():
    idx = pd.to_datetime(["2017-01-02 00:00", "2017-01-02 09:00", "2017-01-08 23:00"])
    assert hour_of_week(idx).tolist() == [0, 9, 167]


def test_h4_segment_weights_ct_h_2_1():
    months = np.arange(1, 13)
    w = segment_weights(months, 7)  # the July model
    assert w[6] == 1.0 and w[5] == 0.5 and w[7] == 0.5
    assert w.sum() == 2.0
    jan = segment_weights(months, 1)
    assert jan[0] == 1.0 and jan[11] == 0.5 and jan[1] == 0.5  # December <-> January wrap
    assert (segment_weights(months, None) == 1.0).all()
    assert SEGMENT_NAMES[6] == "jun-jul-aug-weighted" and SEGMENT_NAMES[0] == "dec-jan-feb-weighted"


# ----------------------------------------------------------------- CT-H-4: occupancy
def test_h5_preliminary_features_ct_h_4_1():
    T = np.array([40.0, 70.0])
    assert np.maximum(K.PRELIM_HDD_BP - T, 0).tolist() == [10.0, 0.0]
    assert np.maximum(T - K.PRELIM_CDD_BP, 0).tolist() == [0.0, 5.0]


def test_h3_occupancy_threshold_ct_h_4_3():
    def occ(n_pos, n):
        r = np.r_[np.ones(n_pos), -np.ones(n - n_pos)]
        return occupancy_lookup(np.full(n, 5), r)[5]

    assert occ(9, 13) == 1.0  # 0.692
    assert occ(8, 13) == 0.0  # 0.615
    assert occ(13, 20) == 0.0  # exactly 0.65: strict > fails


def test_unseen_hour_occupancy_is_unknown_ct_h_4_4():
    occ = occupancy_lookup(np.array([0, 0, 1]), np.array([1.0, 1.0, -1.0]))
    assert occ[0] == 1.0 and occ[1] == 0.0 and np.isnan(occ[2])
    occ_ee = occupancy_lookup(np.array([0, 0, 1]), np.array([1.0, 1.0, -1.0]), eemeter_compat=True)
    assert occ_ee[2] == 1.0  # eemeter casts the NaN to True


def test_h6_occupancy_matches_the_schedule_ct_h_4_2(fitted):
    m, _ = fitted
    truth = np.zeros(168)
    for d in range(5):
        truth[d * 24 + 8 : d * 24 + 18] = 1.0
    for seg in m.segments.values():
        assert seg.status == "fitted"
        assert np.array_equal(seg.occupancy, truth)  # 50 occupied hours of the week


# ----------------------------------------------------------------- CT-H-5: temperature bins
def test_h1_bin_features_ct_h_5_3():
    eps = K.BIN_ENDPOINTS
    F = bin_features([20.0, 40.0, 50.0, 100.0, np.nan], eps)
    assert F[0].tolist() == [20, 0, 0, 0, 0, 0, 0]
    assert F[1].tolist() == [30, 10, 0, 0, 0, 0, 0]
    assert F[2].tolist() == [30, 15, 5, 0, 0, 0, 0]
    assert F[3].tolist() == [30, 15, 10, 10, 10, 15, 10]
    assert np.isnan(F[4]).all()
    T = np.random.default_rng(2).uniform(-10, 110, 500)
    assert np.allclose(bin_features(T, eps).sum(axis=1), T)  # the features sum to T
    assert bin_features([42.0], []).tolist() == [[42.0]]  # E14: every endpoint dropped


def _temps(counts):
    """Temperatures with ``counts`` hours in the default bins <30, 30-45, ..., >90."""
    reps = [20.0, 40.0, 50.0, 60.0, 70.0, 80.0, 95.0]
    return np.concatenate([np.full(n, r) for r, n in zip(reps, counts, strict=True)])


def test_h2_bin_merging_ct_h_5_2():
    assert fit_temperature_bins(_temps([5, 100, 100, 100, 100, 100, 3])) == [45, 55, 65, 75]
    assert fit_temperature_bins(_temps([50, 10, 50, 50, 50, 50, 50])) == [30, 55, 65, 75, 90]
    assert fit_temperature_bins(_temps([5, 10, 50, 50, 50, 50, 50])) == [55, 65, 75, 90]
    assert fit_temperature_bins(_temps([20, 20, 20, 20, 20, 20, 20])) == list(K.BIN_ENDPOINTS)
    assert fit_temperature_bins(np.full(5, 50.0)) == []  # one bin left


def test_bins_are_right_closed_ct_h_5_1():
    assert fit_temperature_bins(np.r_[np.full(30, 30.0), np.full(30, 31.0)], [30.0]) == [30.0]
    assert fit_temperature_bins(np.r_[np.full(30, 30.0), np.full(10, 31.0)], [30.0]) == []


def test_h6_bins_and_parameters_ct_h_5_4_ct_h_6_2(fitted):
    m, _ = fitted
    djf = m.segments["dec-jan-feb-weighted"]
    assert djf.endpoints == [30.0, 45.0]
    assert djf.num_parameters == 168 + 2 * 3 == 174  # one bin set, shared by both modes
    assert djf.interaction and len(djf.bin_occ) == len(djf.bin_unocc) == 3


# ----------------------------------------------------------------- CT-H-6: design
def test_h8_no_intercept_and_tow_dummies_ct_h_6_1(fitted):
    m, info = fitted
    seg = m.segments["jun-jul-aug-weighted"]
    # the 168 levels carry the baseline load: no separate intercept anywhere in the model
    assert np.isfinite(seg.tow_coef).all() and seg.num_parameters == 168 + 2 * (
        len(seg.endpoints) + 1
    )
    assert "intercept" not in json.dumps(seg.as_dict())
    q = pd.DatetimeIndex(["2017-07-10 09:00", "2017-07-10 02:00"])  # Monday, occupied / not
    p = m.predict(q, [60.0, 60.0]).to_numpy()
    assert p == pytest.approx([15.0, 5.0], abs=0.2)


def test_one_occupancy_mode_drops_the_interaction_ct_h_6_2_e13():
    e, t, _ = h6()
    flat = pd.Series(7.0 + 0.2 * np.maximum(t.to_numpy() - 65, 0), index=IDX)
    m, _ = fit_caltrack_hourly(flat, t, intervention_start="2018-01-01")
    seg = m.segments["jun-jul-aug-weighted"]
    assert not seg.interaction
    assert seg.num_parameters == 168 + len(seg.endpoints) + 1


def test_h7_unseen_hour_predicts_nan_ct_h_6_3_ct_h_8_3():
    e, t, _ = h6()
    drop = (IDX.month <= 3) & (IDX.dayofweek == 0) & (IDX.hour == 9)
    q = pd.DatetimeIndex(["2017-02-06 09:00", "2017-02-06 10:00"])
    for compat in (False, True):  # the eemeter 2.10 bug (~0 coefficient) is never reproduced
        m, _ = fit_caltrack_hourly(
            e[~drop], t, intervention_start="2018-01-01", eemeter_compat=compat
        )
        p, counts = m.predict(q, [40.0, 40.0], return_counts=True)
        assert np.isnan(p.iloc[0]) and np.isfinite(p.iloc[1])
        assert counts["n_unseen_hour_of_week"] == 1 and p.attrs["n_unseen_hour_of_week"] == 1
        seg = m.segments["jan-feb-mar-weighted"]
        assert np.isnan(seg.tow_coef[9])
        assert (seg.occupancy[9] == 1.0) if compat else np.isnan(seg.occupancy[9])
        # eemeter's design still counts the hour's (empty) column: 168 levels in compat, 167 not
        assert seg.num_parameters == (168 if compat else 167) + 2 * (len(seg.endpoints) + 1)


# ----------------------------------------------------------------- CT-H-7: fit
def test_h6_fit_quality_and_centre_month_metrics_ct_h_7_2(fitted):
    m, info = fitted
    e, t, _ = h6()
    p = m.predict(IDX, t.to_numpy())
    rmse = float(np.sqrt(np.mean((e.to_numpy() - p.to_numpy()) ** 2)))
    assert rmse == pytest.approx(0.093, abs=0.003)
    jan = m.segments["dec-jan-feb-weighted"].metrics
    assert jan["n"] == 31 * 24 and jan["num_parameters"] == 174  # weight-1 rows only


def test_rank_deficient_design_fits_deterministically_ct_h_7_1():
    # occupied hours only ever see cold weather in this segment: an occupied bin column is all 0
    e, t, occ = h6()
    t2 = t.copy()
    t2[occ & (IDX.month == 7)] = 60.0
    a, _ = fit_caltrack_hourly(e, t2, intervention_start="2018-01-01")
    b, _ = fit_caltrack_hourly(e, t2, intervention_start="2018-01-01")
    sa, sb = a.segments["jun-jul-aug-weighted"], b.segments["jun-jul-aug-weighted"]
    assert sa.status == "fitted"
    assert np.array_equal(sa.bin_occ, sb.bin_occ) and np.array_equal(sa.tow_coef, sb.tow_coef)
    assert np.isfinite(a.predict(IDX[4000:4100], t2.to_numpy()[4000:4100])).all()


# ----------------------------------------------------------------- CT-H-1: sufficiency
def test_month_coverage_rule_ct_h_1_1():
    e, t, _ = h6()
    gap = (IDX >= "2017-05-01") & (IDX < "2017-05-05")  # 96 of 744 May hours: 87 % covered
    m, info = fit_caltrack_hourly(e[~gap], t, intervention_start="2018-01-01")
    assert info["month_coverage"][5] == pytest.approx(648 / 744)
    # every segment that needs May (its centre or a neighbour) is insufficient
    bad = {"mar-apr-may-weighted", "apr-may-jun-weighted", "may-jun-jul-weighted"}
    assert {k for k, s in m.segments.items() if s.status == "insufficient"} == bad
    june = pd.DatetimeIndex(["2017-06-05 10:00"])
    assert np.isnan(m.predict(june, [70.0]).iloc[0])  # no model for June: NaN (CT-H-8.3)
    m2, _ = fit_caltrack_hourly(e[~gap], t, intervention_start="2018-01-01", eemeter_compat=True)
    assert all(s.status == "fitted" for s in m2.segments.values())  # eemeter 2.10's stub


def test_no_segment_fits_raises_ct_h_1_1():
    e, t, _ = h6()
    short = IDX < "2017-02-15"
    with pytest.raises(InsufficientBaseline):
        fit_caltrack_hourly(e[short], t[short])


def test_temperature_gaps_interpolated_ct_h_1_2():
    e, t, _ = h6()
    t2 = t.copy()
    t2.iloc[100:106] = np.nan
    f = hourly_frame(e, t2)
    assert f.attrs["n_temp_interpolated"] == 6 and f["temp"].iloc[100:106].notna().all()
    f2 = hourly_frame(e, t2, eemeter_compat=True)
    assert f2["temp"].iloc[100:106].isna().all()


def test_baseline_window_ct_h_1_3():
    idx = pd.date_range("2016-10-01", "2017-12-31 23:00", freq="h")
    e, t, _ = h6(idx)
    m, info = fit_caltrack_hourly(e, t)
    assert info["n_hours_outside_baseline"] == len(idx) - 8760
    assert m.baseline_start == "2017-01-01 00:00:00" and len(info["frame"]) == 8760


def test_hourly_frame_masks_electricity_zeros_and_raises_on_duplicates():
    e, t, _ = h6()
    e2 = e.copy()
    e2.iloc[:3] = 0.0
    assert hourly_frame(e2, t).attrs["n_zero_masked"] == 3
    dup = pd.concat([e.iloc[:5], e.iloc[:1]])
    with pytest.raises(ValueError, match="more than one meter"):
        hourly_frame(dup, t)


def test_timezone_places_hours_on_the_local_clock():
    idx = pd.date_range("2017-01-01", periods=8760, freq="h")
    e, t, _ = h6(idx)
    f = hourly_frame(e, t, timezone="America/New_York")
    assert str(f.index.tz) == "America/New_York"
    assert hour_of_week(f.index[:1])[0] == 6 * 24  # Sunday 00:00 local


# ----------------------------------------------------------------- CT-H-2.3: single model
def test_single_segment_type_and_its_tests_ct_h_2_3():
    e, t, _ = h6()
    m, info = fit_caltrack_hourly(e, t, intervention_start="2018-01-01", segment_type="single")
    assert list(m.segments) == ["all"] and m.segments["all"].status == "fitted"
    rep_t = np.r_[t.to_numpy()[:100], 120.0]
    tests = single_model_tests(m, info["frame"], rep_t)
    assert set(tests["monthly_nmbe"]) == set(range(1, 13))
    assert tests["nmbe_rule_allows_single"] is (tests["n_months_nmbe_over_1pct"] <= 2)
    lo, hi = tests["baseline_temp_range"]
    assert tests["temp_range_rule_allows_single"] is (120.0 > hi + 0.1 * (hi - lo))
    with pytest.raises(ValueError):
        fit_caltrack_hourly(e, t, segment_type="monthly")


# ----------------------------------------------------------------- CT-H-8: prediction
def test_prediction_uses_the_month_segment_ct_h_8_1(fitted):
    m, _ = fitted
    q = pd.DatetimeIndex(["2017-07-10 10:00", "2017-08-07 10:00"])
    before = m.predict(q, [80.0, 80.0]).to_numpy()
    jul = m.segments["jun-jul-aug-weighted"]
    jul.tow_coef = jul.tow_coef + 100.0
    try:
        after = m.predict(q, [80.0, 80.0]).to_numpy()
    finally:
        jul.tow_coef = jul.tow_coef - 100.0
    assert after[0] == pytest.approx(before[0] + 100.0) and after[1] == before[1]


def test_edge_bins_extrapolate_linearly_ct_h_8_2_e16(fitted):
    m, _ = fitted
    q = pd.DatetimeIndex(["2017-07-10 10:00"] * 3)
    p = m.predict(q, [100.0, 110.0, 120.0]).to_numpy()
    assert p[2] - p[1] == pytest.approx(p[1] - p[0])
    assert p[1] - p[0] == pytest.approx(2.0, abs=0.2)  # 0.2 per degF above 65


def test_nan_rules_and_counts_ct_h_8_3(fitted):
    m, _ = fitted
    q = pd.DatetimeIndex(["2017-07-10 10:00", "2017-07-10 11:00"])
    p, c = m.predict(q, [np.nan, 70.0], return_counts=True)
    assert np.isnan(p.iloc[0]) and np.isfinite(p.iloc[1]) and c["n_no_temperature"] == 1


def test_hourly_savings_end_to_end_ct_h_8_4():
    e, t, _ = h6()
    ridx = pd.date_range("2018-01-01", periods=24 * 90, freq="h")
    re_, rt, _ = h6(ridx, seed=5, scale=0.85)
    rt = rt.copy()
    rt.iloc[10:17] = np.nan  # seven hours: longer than the six-hour interpolation limit
    res = caltrack_hourly_savings(e, t, re_, rt)
    tab = res.reporting
    assert res.method == "CalTRACK 2.0 hourly" and res.fsu is None
    used = tab["avoided"].notna()
    assert np.allclose(tab.loc[used, "avoided"], (tab["predicted"] - tab["observed"])[used])
    assert used.sum() == len(tab) - 7
    assert res.savings.avoided_energy == pytest.approx(tab.loc[used, "avoided"].sum(), abs=0.05)
    assert res.savings.savings_pct == pytest.approx(0.15, abs=0.01)
    assert res.sufficiency["status"] == "PASS"
    assert any("no temperature" in c for c in res.savings.caveats)
    json.dumps(res.as_dict())


def test_hourly_savings_counts_unseen_hours():
    e, t, _ = h6()
    drop = (IDX.month <= 3) & (IDX.dayofweek == 0) & (IDX.hour == 9)
    ridx = pd.date_range("2018-02-01", periods=24 * 28, freq="h")
    re_, rt, _ = h6(ridx, seed=6)
    res = caltrack_hourly_savings(e[~drop], t, re_, rt)
    mondays = int(((ridx.dayofweek == 0) & (ridx.hour == 9)).sum())
    assert res.sufficiency["reporting_counts"]["n_unseen_hour_of_week"] == mondays == 4
    assert any("never saw" in c for c in res.savings.caveats)
    assert res.reporting["predicted"].isna().sum() == 4


# ----------------------------------------------------------------- serialisation
@pytest.mark.parametrize("segment_type", ["three_month_weighted", "single"])
def test_hourly_model_round_trips(segment_type):
    e, t, _ = h6()
    drop = (IDX.month <= 3) & (IDX.dayofweek == 0) & (IDX.hour == 9)
    m, _ = fit_caltrack_hourly(
        e[~drop], t, intervention_start="2018-01-01", segment_type=segment_type
    )
    d = json.loads(json.dumps(m.as_dict()))
    m2 = CalTRACKHourlyModel.from_dict(d)
    a = m.predict(IDX, t.to_numpy()).to_numpy()
    b = m2.predict(IDX, t.to_numpy()).to_numpy()
    assert np.array_equal(np.isnan(a), np.isnan(b)) and np.array_equal(
        a[~np.isnan(a)], b[~np.isnan(b)]
    )
    assert m2.as_dict() == d


def test_segment_without_data_predicts_nan_e15():
    e, t, _ = h6()
    half = IDX < "2017-07-01"
    # eemeter_compat skips the coverage rule, so the autumn segments are fitted or empty by data
    m, _ = fit_caltrack_hourly(e[half], t, intervention_start="2018-01-01", eemeter_compat=True)
    assert m.segments["sep-oct-nov-weighted"].status == "no_data"
    p, c = m.predict(pd.DatetimeIndex(["2017-10-02 10:00"]), [60.0], return_counts=True)
    assert np.isnan(p.iloc[0]) and c["n_no_segment_model"] == 1
