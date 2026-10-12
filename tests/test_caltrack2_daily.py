"""CalTRACK 2.0 Daily and Billing (camber.mandv.caltrack_daily; 0.105, #156).

Every test names the spec rule it covers (CT-D-*, CT-C-*) and the hand-computed case (D*, B*,
M*) from the CAMBER CalTRACK spec. Synthetic data only.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from camber.mandv import _caltrack as K
from camber.mandv._caltrack import caltrack_fsu, caltrack_metrics, interpolate_short_gaps, wls
from camber.mandv.caltrack_daily import (
    DISQUALIFIED,
    ERROR,
    NOT_ATTEMPTED,
    QUALIFIED,
    CalTRACKBillingModel,
    CalTRACKCandidate,
    CalTRACKDailyModel,
    balance_point_grid,
    billing_temperature,
    caltrack_billing_savings,
    caltrack_daily_savings,
    clean_billing_periods,
    daily_temperature,
    daily_usage,
    degree_day_sufficient,
    fit_caltrack_billing,
    fit_caltrack_daily,
    select_candidate,
)
from camber.mandv.sufficiency import InsufficientBaseline

DAYS = pd.date_range("2017-01-01", periods=365, freq="D")
HOURS = pd.date_range("2017-01-01", periods=365 * 24, freq="h")
D = np.arange(365)
T_W = 30.0 + (D % 61)  # fixture W: sweeps 30-90 degF evenly


def temp_w(index=HOURS):
    return pd.Series(np.repeat(T_W, 24)[: len(index)], index=index)


def hdd(b, T=T_W):
    return np.maximum(b - T, 0.0)


def cdd(b, T=T_W):
    return np.maximum(T - b, 0.0)


def fit(U, fuel="electricity", **kw):
    return fit_caltrack_daily(pd.Series(U, index=DAYS), temp_w(), fuel=fuel, **kw)


# ----------------------------------------------------------------- fixture facts (spec section 6)
def test_fixture_w_facts():
    assert (hdd(60) > 0).sum() == 180 and hdd(60).sum() == 2790
    assert (hdd(54) > 0).sum() == 144 and (cdd(72) > 0).sum() == 107


# ----------------------------------------------------------------- CT-D-6: candidate models
def test_d1_pure_intercept_ct_d_6_1():
    m, _ = fit(np.full(365, 10.0))
    assert m.model_type == "intercept_only"
    assert m.intercept == pytest.approx(10.0) and m.r_squared_adj == 0.0


@pytest.mark.parametrize("compat", [False, True])
def test_d2_pure_hdd_gas_ct_d_6_2(compat):
    m, _ = fit(10 + 2 * hdd(60), fuel="gas", eemeter_compat=compat)
    assert m.model_type == "hdd_only" and m.heating_balance_point == 60
    assert m.beta_hdd == pytest.approx(2, abs=1e-9) and m.intercept == pytest.approx(10, abs=1e-9)
    assert m.r_squared_adj == pytest.approx(1.0, abs=1e-9)


@pytest.mark.parametrize("compat", [False, True])
def test_d3_hdd_and_cdd_ct_d_6_4(compat):
    m, _ = fit(20 + 1.5 * hdd(54) + 3 * cdd(72), eemeter_compat=compat)
    assert m.model_type == "cdd_hdd"
    assert (m.heating_balance_point, m.cooling_balance_point) == (54, 72)
    assert m.beta_hdd == pytest.approx(1.5, abs=1e-9)
    assert m.beta_cdd == pytest.approx(3, abs=1e-9)
    assert m.intercept == pytest.approx(20, abs=1e-9)


def test_cdd_only_ct_d_6_3():
    m, _ = fit(15 + 2.5 * cdd(75))
    assert m.model_type == "cdd_only" and m.cooling_balance_point == 75
    assert m.beta_cdd == pytest.approx(2.5, abs=1e-9) and m.beta_hdd == 0.0


def test_d4_adjusted_r2_arithmetic_ct_d_8_1_ct_c_1_1():
    x = np.array([0, 1, 2, 3, 4.0])
    y = np.array([1, 3, 2, 5, 4.0])
    r = wls(np.column_stack([np.ones(5), x]), y)
    assert r.params == pytest.approx([1.4, 0.8])
    assert r.ss_res == pytest.approx(3.6) and r.ss_tot == pytest.approx(10.0)
    assert r.r_squared == pytest.approx(0.64) and r.r_squared_adj == pytest.approx(0.52)


def test_b1_weighted_intercept_ct_d_6_5():
    r = wls(np.ones((2, 1)), np.array([1.0, 4.0]), np.array([10.0, 30.0]))
    assert r.params[0] == pytest.approx(3.25)  # OLS would give 2.5
    assert wls(np.ones((2, 1)), np.array([1.0, 4.0])).params[0] == pytest.approx(2.5)


def test_d5_negative_slope_disqualified_ct_d_7_2():
    m, info = fit(50 - 0.5 * hdd(60), fuel="gas")
    assert m.model_type == "intercept_only"
    assert m.intercept == pytest.approx(50 - 0.5 * 2790 / 365, abs=1e-6)  # 46.178082
    hdd_c = [c for c in info["candidates"] if c.model_type == "hdd_only"]
    assert {c.status for c in hdd_c} <= {DISQUALIFIED, NOT_ATTEMPTED}
    assert any(c.status == DISQUALIFIED and "beta_hdd" in c.reason for c in hdd_c)


def test_d7_noisy_hdd_matches_eemeter_values():
    m, _ = fit(10 + 2 * hdd(60) + 0.5 * np.sin(1.7 * D), fuel="electricity", eemeter_compat=True)
    assert m.model_type == "hdd_only" and m.heating_balance_point == 60
    assert m.beta_hdd == pytest.approx(1.99988, abs=1e-5)
    assert m.intercept == pytest.approx(10.00215, abs=1e-5)
    assert m.r_squared_adj == pytest.approx(0.999678, abs=1e-6)


def test_sign_rule_allows_zero_and_rejects_negative_intercept_ct_d_7_2_e4():
    # constant 0 usage on gas: mu == 0 passes (eemeter's < 0), the model is intercept-only
    m, _ = fit(np.zeros(365), fuel="gas")
    assert m.model_type == "intercept_only" and m.intercept == 0.0
    # mean usage below zero: intercept-only is DISQUALIFIED and nothing qualifies -> NO MODEL
    with pytest.raises(ValueError, match="NO MODEL"):
        fit(np.full(365, -5.0), fuel="gas", check_sufficiency=False)


def test_negative_intercept_candidate_loses_to_next_best_e4():
    # usage that a steep HDD line with a negative intercept fits best
    U = np.where(T_W < 60, 3.0 * (60 - T_W) - 4.0, 0.5) + 5
    U = np.clip(U, 0.1, None)
    _, info = fit(U, fuel="gas")
    neg = [c for c in info["candidates"] if c.reason and "intercept" in c.reason]
    best = select_candidate(info["candidates"])
    assert all(c.status == DISQUALIFIED for c in neg)
    assert best is not None and best.intercept >= 0


# ----------------------------------------------------------------- CT-D-7: qualification
def test_d6_degree_day_gates_ct_d_7_1():
    nine = np.r_[np.ones(9), np.zeros(20)] * 5.0
    ten = np.r_[np.ones(10), np.zeros(20)] * 5.0
    assert not degree_day_sufficient(nine, min_nonzero=10)[0]
    assert degree_day_sufficient(ten, min_nonzero=10)[0]
    assert not degree_day_sufficient(np.full(10, 1.9), min_nonzero=10)[0]  # total 19
    assert degree_day_sufficient(np.full(10, 2.0), min_nonzero=10)[0]  # total 20 (test is < 20)
    # billing: no non-zero-day rule; one period whose total (avg DD x kept days) is >= 20
    assert degree_day_sufficient([0.7, 0.0], [30, 30], min_nonzero=0)[0]  # 21 degree days
    assert not degree_day_sufficient([0.6, 0.0], [30, 30], min_nonzero=0)[0]  # 18


def test_not_attempted_candidates_are_never_selected_ct_d_7_1_e1():
    # W never goes below 30: the 30 degF heating balance point has no heating days
    _, info = fit(10 + 2 * hdd(60), fuel="gas")
    c30 = next(c for c in info["candidates"] if c.model_type == "hdd_only")
    assert c30.heating_balance_point == 30 and c30.status == NOT_ATTEMPTED
    assert c30.intercept is None


def test_p_value_screen_is_off_by_default_ct_d_7_3():
    rng = np.random.default_rng(3)
    U = 20 + rng.normal(0, 2, 365)  # no weather dependence at all
    _, info = fit(U)
    assert not any(c.reason and "p-value" in c.reason for c in info["candidates"])
    _, info2 = fit(U, max_p_value=0.05)
    assert any(c.reason and "p-value" in c.reason for c in info2["candidates"])
    assert K.MAX_P_VALUE == 1.0


def test_fit_error_is_an_error_candidate_ct_d_7_4(monkeypatch):
    import camber.mandv.caltrack_daily as cd

    real = cd.wls

    def flaky(X, y, w=None, **kw):
        if X.shape[1] == 3:
            raise np.linalg.LinAlgError("boom")
        return real(X, y, w, **kw)

    monkeypatch.setattr(cd, "wls", flaky)
    m, info = fit(20 + 1.5 * hdd(54) + 3 * cdd(72))
    errs = [c for c in info["candidates"] if c.status == ERROR]
    assert errs and all(c.model_type == "cdd_hdd" for c in errs)
    assert m.model_type != "cdd_hdd"


# ----------------------------------------------------------------- CT-D-8: selection
def test_d8_tie_order_ct_d_8_2():
    a = CalTRACKCandidate("hdd_only", QUALIFIED, 50, None, 1, 1, 0, r_squared_adj=0.8)
    b = CalTRACKCandidate("hdd_only", QUALIFIED, 53, None, 1, 1, 0, r_squared_adj=0.8)
    assert select_candidate([a, b]) is a  # exact tie: the earlier (lower bp) wins
    io = CalTRACKCandidate("intercept_only", QUALIFIED, intercept=1, r_squared_adj=0.0)
    zero = CalTRACKCandidate("hdd_only", QUALIFIED, 50, None, 1, 1, 0, r_squared_adj=0.0)
    assert select_candidate([io, zero]) is io  # intercept-only beats a weather model at 0
    nan = CalTRACKCandidate("hdd_only", QUALIFIED, 50, None, 1, 1, 0, r_squared_adj=None)
    assert select_candidate([nan]) is None  # a NaN adjusted R2 is never selected
    neg = CalTRACKCandidate("hdd_only", QUALIFIED, 50, None, 1, 1, 0, r_squared_adj=-0.1)
    assert select_candidate([io, neg]) is io


def test_scan_order_and_counts_ct_d_8_2_ct_d_5_3():
    _, info = fit(20 + 1.5 * hdd(54) + 3 * cdd(72), eemeter_compat=True)
    types = [c.model_type for c in info["candidates"]]
    assert types[0] == "intercept_only"
    first = {t: types.index(t) for t in ("hdd_only", "cdd_only", "cdd_hdd")}
    assert first["hdd_only"] < first["cdd_only"] < first["cdd_hdd"]
    ch = [c for c in info["candidates"] if c.model_type == "cdd_hdd"]
    assert len(ch) == 61 * 62 // 2 == 1891  # bc >= bh on the 1 degF grid
    assert all(c.heating_balance_point <= c.cooling_balance_point for c in ch)
    # outer loop over the cooling balance point, inner over heating
    assert [(c.cooling_balance_point, c.heating_balance_point) for c in ch[:3]] == [
        (30, 30),
        (31, 30),
        (31, 31),
    ]


def test_constant_usage_e2():
    m, info = fit(np.full(365, 7.0))
    assert m.model_type == "intercept_only" and m.r_squared_adj == 0.0
    w = [
        c for c in info["candidates"] if c.model_type != "intercept_only" and c.status == QUALIFIED
    ]
    assert all(c.r_squared_adj is None or c.r_squared_adj <= 0 for c in w)


def test_no_data_and_no_model_ct_d_8_3():
    with pytest.raises(ValueError, match="NO DATA"):
        fit_caltrack_daily(pd.Series(np.nan, index=DAYS), temp_w())


# ----------------------------------------------------------------- CT-D-5: grid
def test_balance_point_grid_ct_d_5_1_ct_d_5_2():
    g3 = balance_point_grid(3)
    assert len(g3) == 21 and g3[0] == 30 and g3[-1] == 90 and np.diff(g3).max() == 3
    assert len(balance_point_grid(1)) == 61 and len(balance_point_grid(2)) == 31
    with pytest.raises(ValueError):
        balance_point_grid(4)


def test_grid_default_docs_and_compat_eemeter_ct_d_5_2_diff():
    m, info = fit(10 + 2 * hdd(60), fuel="gas")
    assert (
        m.bp_step == 3 and len([c for c in info["candidates"] if c.model_type == "hdd_only"]) == 21
    )
    m, info = fit(10 + 2 * hdd(60), fuel="gas", eemeter_compat=True)
    assert (
        m.bp_step == 1 and len([c for c in info["candidates"] if c.model_type == "hdd_only"]) == 61
    )
    # off-grid truth: 3 degF grid picks a neighbour, the 1 degF grid finds 61
    m3, _ = fit(10 + 2 * hdd(61), fuel="gas")
    m1, _ = fit(10 + 2 * hdd(61), fuel="gas", bp_step=1)
    assert m3.heating_balance_point in (60, 63) and m1.heating_balance_point == 61


def test_gas_fits_no_cooling_terms_ct_d_5_4():
    _, info = fit(20 + 1.5 * hdd(54) + 3 * cdd(72), fuel="gas")
    assert {c.model_type for c in info["candidates"]} == {"intercept_only", "hdd_only"}


def test_balance_points_are_numbers_not_column_names_ct_d_5_5():
    m, _ = fit(20 + 1.5 * hdd(54) + 3 * cdd(72))
    d = json.loads(json.dumps(m.as_dict()))
    assert isinstance(d["heating_balance_point"], float) and d["cooling_balance_point"] == 72


# ----------------------------------------------------------------- CT-D-1: sufficiency
def _gappy(n_missing):
    U = pd.Series(10 + 2 * hdd(60), index=DAYS)
    U.iloc[100 : 100 + n_missing] = np.nan
    return U.dropna()


def test_d9_sufficiency_boundary_ct_d_1_2_diff():
    for compat in (False, True):
        m, info = fit_caltrack_daily(_gappy(36), temp_w(), fuel="gas", eemeter_compat=compat)
        assert info["sufficiency"]["status"] == "PASS"
    m, info = fit_caltrack_daily(_gappy(37), temp_w(), fuel="gas")
    assert info["sufficiency"]["status"] == "PASS"  # the documents allow 37
    with pytest.raises(InsufficientBaseline) as e:
        fit_caltrack_daily(_gappy(37), temp_w(), fuel="gas", eemeter_compat=True)
    assert e.value.need["have"] == 328
    with pytest.raises(InsufficientBaseline):
        fit_caltrack_daily(_gappy(38), temp_w(), fuel="gas")
    m, info = fit_caltrack_daily(_gappy(38), temp_w(), fuel="gas", check_sufficiency=False)
    assert info["sufficiency"]["status"] == "FAIL"
    assert any("not CalTRACK-compliant" in c for c in info["caveats"])


def test_baseline_is_the_365_days_before_the_intervention_ct_d_1_1():
    idx = pd.date_range("2016-07-01", "2017-12-31", freq="D")
    T = 30.0 + (np.arange(len(idx)) % 61)
    e = pd.Series(10 + 2 * hdd(60, T), index=idx)
    t = pd.Series(np.repeat(T, 24), index=pd.date_range(idx[0], periods=len(idx) * 24, freq="h"))
    m, info = fit_caltrack_daily(e, t, fuel="gas")
    s = info["sufficiency"]
    assert (s["baseline_start"], s["baseline_end"]) == ("2017-01-01", "2018-01-01")
    assert s["n_days_outside_baseline"] == 184 and m.n == 365
    m2, info2 = fit_caltrack_daily(e, t, fuel="gas", intervention_start="2017-07-01")
    assert info2["sufficiency"]["baseline_start"] == "2016-07-01" and m2.n == 365


def test_electricity_zero_is_missing_gas_zero_is_not_ct_d_1_3_e5_e6():
    U = pd.Series(10 + 2 * hdd(60), index=DAYS)
    U.iloc[:5] = 0.0
    u = daily_usage(U, fuel="electricity")
    assert len(u) == 360 and u.attrs["n_zero_masked"] == 5
    g = daily_usage(U, fuel="gas")
    assert len(g) == 365 and g.attrs["n_zero_masked"] == 0


def test_negative_values_flag_or_fail_ct_d_1_5_diff_e7():
    U = pd.Series(10 + 2 * hdd(60), index=DAYS)
    U.iloc[3] = -1.0
    m, info = fit_caltrack_daily(U, temp_w(), fuel="gas")
    assert info["sufficiency"]["status"] == "PASS" and info["sufficiency"]["n_negative"] == 1
    assert any("negative" in w for w in info["sufficiency"]["warnings"])
    with pytest.raises(InsufficientBaseline):
        fit_caltrack_daily(U, temp_w(), fuel="gas", eemeter_compat=True)


def test_outliers_flagged_never_removed_ct_d_1_6():
    U = pd.Series(10 + 2 * hdd(60), index=DAYS)
    U.iloc[10] = 10_000.0
    m, info = fit_caltrack_daily(U, temp_w(), fuel="gas")
    assert info["sufficiency"]["n_outliers"] == 1 and m.n == 365
    assert K.outlier_count([1, 2, 3, 4, 100]) == 1


def test_metadata_exclusions_ct_d_1_7():
    U = pd.Series(10 + 2 * hdd(60), index=DAYS)
    with pytest.raises(ValueError, match="net-metering"):
        caltrack_daily_savings(U, temp_w(), U, temp_w(), fuel="gas", net_metering_changed=True)
    res = caltrack_daily_savings(
        U, temp_w(), U[:30], temp_w()[: 30 * 24], fuel="gas", ev_charger_installed=True
    )
    assert any("EV charger" in c for c in res.savings.caveats)


# ----------------------------------------------------------------- CT-D-2: daily preparation
@pytest.mark.parametrize(
    ("n_missing", "compat", "kept"),
    [(11, False, True), (11, True, True), (12, False, True), (12, True, False), (13, False, False)],
)
def test_d10_hourly_to_daily_usage_ct_d_2_1_diff(n_missing, compat, kept):
    idx = pd.date_range("2017-03-01", periods=72, freq="h")
    s = pd.Series(1.0, index=idx)
    s.iloc[24 : 24 + n_missing] = np.nan  # the middle day loses n_missing hours
    u = daily_usage(s.dropna(), eemeter_compat=compat)
    day = pd.Timestamp("2017-03-02")
    assert (day in u.index) is kept
    if kept:
        assert u.loc[day, "usage"] == pytest.approx(24.0)  # filled with the day's mean


def test_daily_usage_on_a_dst_day_ct_d_2_1():
    idx = pd.date_range("2017-03-11", "2017-03-14", freq="h", inclusive="left")
    idx = idx[~((idx >= "2017-03-12 02:00") & (idx < "2017-03-12 03:00"))]  # spring forward
    u = daily_usage(pd.Series(1.0, index=idx), timezone="America/Los_Angeles")
    assert u.loc["2017-03-12", "usage"] == pytest.approx(23.0)
    assert u.loc["2017-03-12", "coverage"] == pytest.approx(1.0)


def test_missing_temperature_masks_the_day_ct_d_2_3():
    t = temp_w().copy()
    t["2017-02-01":"2017-02-03 23:00"] = np.nan  # three whole days without temperature
    m, info = fit_caltrack_daily(pd.Series(10 + 2 * hdd(60), index=DAYS), t, fuel="gas")
    assert m.n == 362 and len(info["frame"]) == 362


def test_duplicate_timestamps_raise_e18():
    s = pd.Series(1.0, index=pd.DatetimeIndex(["2017-01-01 01:00"] * 2 + ["2017-01-01 02:00"]))
    with pytest.raises(ValueError, match="more than one meter"):
        daily_usage(s)


# ----------------------------------------------------------------- CT-D-4: temperature
def _one_day(n_present):
    idx = pd.date_range("2017-05-01", periods=24, freq="h")
    t = pd.Series(50.0 + np.arange(24), index=idx)
    t.iloc[n_present:] = np.nan
    return t


def test_daily_temperature_validity_ct_d_4_1_diff_e9():
    assert np.isfinite(daily_temperature(_one_day(12)).loc["2017-05-01", "temp"])
    assert np.isnan(daily_temperature(_one_day(11)).loc["2017-05-01", "temp"])
    # eemeter: any hour gives a mean for the fit; sufficiency wants > 90 % of the hours
    c = daily_temperature(_one_day(1), eemeter_compat=True).loc["2017-05-01"]
    assert c["temp"] == 50.0 and not c["valid_sufficiency"]
    c = daily_temperature(_one_day(22), eemeter_compat=True).loc["2017-05-01"]
    assert c["valid_sufficiency"]
    c = daily_temperature(_one_day(21), eemeter_compat=True).loc["2017-05-01"]
    assert not c["valid_sufficiency"]


def test_six_hour_gaps_interpolated_ct_d_4_2_diff():
    idx = pd.date_range("2017-05-01", periods=30, freq="h")
    s = pd.Series(np.arange(30.0), index=idx)
    s6 = s.copy()
    s6.iloc[5:11] = np.nan
    out, n = interpolate_short_gaps(s6)
    assert n == 6 and out.iloc[5:11].tolist() == list(range(5, 11))
    s7 = s.copy()
    s7.iloc[5:12] = np.nan
    out, n = interpolate_short_gaps(s7)
    assert n == 0 and out.iloc[5:12].isna().all()
    s6.iloc[12:] = np.nan  # a trailing gap is never filled
    t = daily_temperature(s6)
    assert t.attrs["n_temp_interpolated"] == 6
    assert daily_temperature(s6, eemeter_compat=True).attrs["n_temp_interpolated"] == 0


def test_sub_hourly_temperature_nearest_minute_then_hourly_mean_ct_d_4_3():
    idx = pd.to_datetime(["2017-05-01 00:00", "2017-05-01 00:40", "2017-05-01 01:00"])
    h, _ = K.hourly_temperature(pd.Series([10.0, 20.0, 30.0], index=idx))
    # minutes 0-19 nearest 00:00 (10), 20-49 nearest 00:40 (20), 50-59 nearest 01:00 (30); a
    # minute equidistant from two readings takes the later one
    assert h.iloc[0] == pytest.approx((20 * 10 + 30 * 20 + 10 * 30) / 60)


def test_billing_degree_days_are_the_kept_day_mean_ct_d_4_4():
    periods = pd.DataFrame(
        {"start": [pd.Timestamp("2017-01-01")], "end": [pd.Timestamp("2017-01-31")]}
    )
    idx = pd.date_range("2017-01-01", "2017-01-31", freq="h", inclusive="left")
    t = pd.Series(40.0, index=idx)
    t["2017-01-01 00:00":"2017-01-01 23:00"] = 20.0
    t["2017-01-02 00:00":"2017-01-02 23:00"] = np.nan  # one day lost, 29/30 = 96.7 % coverage
    bt = billing_temperature(periods, t)
    assert bt.loc[0, "n_days_kept"] == 29
    rows = list(bt["day_temps"])
    from camber.mandv.caltrack_daily import _dd

    assert _dd(rows, 60.0, "hdd")[0] == pytest.approx((40 + 28 * 20) / 29)


# ----------------------------------------------------------------- CT-D-3: billing data
def _bills(lengths, start="2017-01-01", energy=None, estimated=None):
    starts = [pd.Timestamp(start)]
    for n in lengths[:-1]:
        starts.append(starts[-1] + pd.Timedelta(days=n))
    ends = [s + pd.Timedelta(days=n) for s, n in zip(starts, lengths, strict=True)]
    return pd.DataFrame(
        {
            "start": starts,
            "end": ends,
            "energy": energy if energy is not None else [float(n) for n in lengths],
            "estimated": estimated if estimated is not None else [False] * len(lengths),
        }
    )


def test_b3_length_filters_ct_d_3_2():
    f = clean_billing_periods(_bills([30, 24, 25, 35, 36, 30]), billing_cycle="monthly")
    assert f["days"].tolist() == [30, 25, 35, 30]
    f = clean_billing_periods(_bills([60, 70, 71, 24]), billing_cycle="bimonthly")
    assert f["days"].tolist() == [60, 70]


def test_b4_estimated_reads_ct_d_3_1_ct_d_1_4():
    f = clean_billing_periods(
        _bills(
            [30] * 7,
            energy=[2, 3, 5, 4, 6, 3, 4],
            estimated=[False, False, True, False, True, True, False],
        ),
        billing_cycle="bimonthly",  # keep every length so only the combine rule acts
    )
    # eemeter's docstring example, labelled by read (end) date: {1:2, 2:3, 4:9, 5:NaN, 7:7}
    got = f["energy"].tolist()
    assert got[:3] == [2, 3, 9] and np.isnan(got[3]) and got[4] == 7
    assert f["days"].tolist() == [30, 30, 60, 30, 60]
    assert f.attrs["merged"] == 2 and f.attrs["estimated_missing"] == 1


def test_b4_seventy_day_cap_ct_d_3_1_diff():
    bills = _bills([40, 40, 30], estimated=[True, False, False])
    f = clean_billing_periods(bills, billing_cycle="bimonthly")
    assert f["days"].tolist() == [30] and f.attrs["over_cap"] == 1  # 80-day merge dropped
    # eemeter: length filter first on the original bills, then combine, no cap
    g = clean_billing_periods(bills, billing_cycle="bimonthly", eemeter_compat=True)
    assert g["days"].tolist() == [80, 30]


def test_reporting_bills_combine_off_cycle_and_flag_long_ct_d_3_4():
    f = clean_billing_periods(_bills([30, 10, 30, 40]), period="reporting", billing_cycle="monthly")
    assert f["days"].tolist() == [30, 40, 40]
    assert f["flag_long"].tolist() == [False, True, True]
    assert f.attrs["n_off_cycle_combined"] == 1


def test_b5_billing_temperature_coverage_ct_d_3_3_e10():
    periods = _bills([30])
    idx = pd.date_range("2017-01-01", periods=720, freq="h")
    for frac, ok in ((0.89, False), (0.90, True)):
        t = pd.Series(40.0, index=idx)
        t.iloc[int(round(720 * frac)) :] = np.nan
        bt = billing_temperature(periods, t, eemeter_compat=True)  # no interpolation
        assert bool(bt.loc[0, "n_days_kept"] > 0) is ok


def _monthly_bills(U, temp_daily_index=DAYS):
    daily = pd.Series(U, index=temp_daily_index)
    b = daily.resample("MS").sum()
    return pd.DataFrame(
        {"start": b.index, "end": b.index.shift(1, freq="MS"), "energy": b.to_numpy()}
    )


def test_b2_pure_hdd_billing_ct_d_9():
    bills = _monthly_bills(10 + 2 * hdd(60))
    for compat in (False, True):
        m, info = fit_caltrack_billing(bills, temp_w(), fuel="gas", eemeter_compat=compat)
        assert isinstance(m, CalTRACKBillingModel)
        assert m.model_type == "hdd_only" and m.heating_balance_point == 60
        assert m.beta_hdd == pytest.approx(2, abs=1e-8) and m.intercept == pytest.approx(
            10, abs=1e-8
        )
        # CT-D-2.2 / CT-D-9.1: usage per day of the whole bill, weight = kept days
        f = info["frame"]
        assert (f["upd"] == f["energy"] / f["days"]).all() and (f["n_days_kept"] == f["days"]).all()
    # CT-D-1.8: twelve bills are enough; no bill-count floor beyond the 365-day rules
    assert m.n == 12


def test_billing_rejects_non_period_input_e17():
    with pytest.raises(TypeError):
        fit_caltrack_billing(pd.Series(1.0, index=DAYS), temp_w())


# ----------------------------------------------------------------- CT-D-10: prediction, savings
def test_d11_prediction_ct_d_10_1_ct_d_10_3():
    m, _ = fit(10 + 2 * hdd(60), fuel="gas")
    days = pd.date_range("2018-01-01", periods=2, freq="D")
    p = m.predict(days, [40.0, np.nan])
    assert p.iloc[0] == pytest.approx(50.0) and np.isnan(p.iloc[1])
    t = pd.Series(
        np.r_[np.full(24, 40.0), np.full(24, np.nan)],
        index=pd.date_range("2018-01-01", periods=48, freq="h"),
    )
    p2 = m.predict(days, t)
    assert p2.iloc[0] == pytest.approx(50.0) and np.isnan(p2.iloc[1])


def test_disaggregation_ct_d_10_5():
    m, _ = fit(20 + 1.5 * hdd(54) + 3 * cdd(72))
    d = m.predict(DAYS[:40], T_W[:40], disaggregate=True)
    assert np.allclose(d["base_load"] + d["heating_load"] + d["cooling_load"], d["predicted"])
    assert np.allclose(d["base_load"], 20.0)


def test_daily_savings_end_to_end_ct_d_10_1_ct_d_10_4():
    base = pd.Series(20 + 1.5 * hdd(54) + 3 * cdd(72), index=DAYS)
    rep_days = pd.date_range("2018-01-01", periods=120, freq="D")
    rT = 30.0 + (np.arange(120) % 61)
    rep = pd.Series(0.9 * (20 + 1.5 * hdd(54, rT) + 3 * cdd(72, rT)), index=rep_days)
    rt = pd.Series(np.repeat(rT, 24), index=pd.date_range("2018-01-01", periods=120 * 24, freq="h"))
    rep.iloc[5] = 0.0  # an electricity zero: missing (CT-D-10.3)
    rt.iloc[24 * 7 : 24 * 8] = np.nan  # a day without temperature: no counterfactual
    res = caltrack_daily_savings(base, temp_w(), rep, rt)
    tab = res.reporting
    assert res.method == "CalTRACK 2.0 daily" and res.model.model_type == "cdd_hdd"
    used = tab["avoided"].notna()
    assert used.sum() == 118
    assert res.savings.avoided_energy == pytest.approx(tab.loc[used, "avoided"].sum(), abs=0.01)
    assert res.savings.savings_pct == pytest.approx(0.1, abs=1e-4)
    assert any("no valid temperature" in c for c in res.savings.caveats)
    assert any("exactly 0" in c for c in res.savings.caveats)
    assert res.fsu is not None and res.fsu["granularity"] == "daily" and res.fsu["Q"] == 118
    assert res.fsu["M"] == pytest.approx(118 / 30)
    json.dumps(res.as_dict())


def test_billing_savings_end_to_end_ct_d_10_2():
    U = 10 + 2 * hdd(60)
    base = _monthly_bills(U)
    rep_idx = pd.date_range("2018-01-01", periods=365, freq="D")
    rep = _monthly_bills(0.8 * U, rep_idx)
    rt = pd.Series(
        np.repeat(T_W, 24), index=pd.date_range("2018-01-01", periods=365 * 24, freq="h")
    )
    rt.iloc[24 * 40 : 24 * 41] = np.nan  # a lost day in February: filled at the kept-day mean
    res = caltrack_billing_savings(base, temp_w(), rep, rt, fuel="gas")
    tab = res.reporting
    assert res.method == "CalTRACK 2.0 billing" and len(tab) == 12
    feb = tab.loc["2018-02-01"]
    kept = np.r_[T_W[31:40], T_W[41:59]]
    expect = 28 * (10 + 2 * np.maximum(60 - kept, 0).mean())
    assert feb["predicted"] == pytest.approx(expect)
    assert res.savings.savings_pct == pytest.approx(0.2, abs=0.01)
    assert res.fsu["granularity"] == "billing" and res.fsu["Q"] == 12 and res.fsu["M"] == 12


# ----------------------------------------------------------------- CT-C: metrics and FSU
def test_m1_cvrmse_ct_c_1_2():
    m = caltrack_metrics([10, 12, 14, 16], [11, 11, 15, 15], 1)
    assert m["cvrmse_adj"] == pytest.approx(np.sqrt(4 / 3) / 13, abs=1e-7)  # 0.0888231
    assert m["cvrmse"] == pytest.approx(1 / 13)


def test_m2_nmbe_eemeter_sign_ct_c_1_3():
    m = caltrack_metrics([10, 12, 14, 16], [11, 13, 15, 17], 1)
    assert m["nmbe"] == pytest.approx(4 / 52)  # positive: the model over-predicts


def test_other_metrics_ct_c_1_4():
    y = np.array([10.0, 12, 14, 16, 0, 9])
    yh = np.array([11.0, 13, 13, 15, 1, 9.5])
    m = caltrack_metrics(y, yh, 1)
    assert m["nmae"] == pytest.approx(5.5 / 61)
    assert m["mape"] is None  # a zero observation makes MAPE undefined
    assert m["mape_no_zeros"] == pytest.approx(np.mean([0.1, 1 / 12, 1 / 14, 1 / 16, 0.5 / 9]))
    assert m["r_squared"] == pytest.approx(np.corrcoef(y, yh)[0, 1] ** 2)
    rho = pd.Series(yh - y).autocorr(1)
    assert m["autocorr_resid"] == pytest.approx(rho)
    assert -1 < rho < 1 and m["n_prime"] == pytest.approx(6 * (1 - rho) / (1 + rho))


def test_m3_fsu_ct_c_1_5():
    out = caltrack_fsu(
        granularity="daily",
        cvrmse_adj=0.2,
        n_baseline=365,
        num_parameters=1,
        rho=0.5,
        n_reporting=365,
        reporting_months=12,
        savings_fraction=0.1,
    )
    assert out["P_prime"] == pytest.approx(121.667, abs=1e-3)
    assert out["polynomial"] == pytest.approx(1.3925, abs=1e-9)
    assert out["t"] == pytest.approx(1.64905, abs=1e-5)
    assert out["fsu"] == pytest.approx(0.419772, abs=1e-6)
    assert K.FSU_POLY["billing"] == (-0.00022, 0.03306, 0.94054)


# ----------------------------------------------------------------- serialisation
@pytest.mark.parametrize("compat", [False, True])
def test_daily_model_round_trips(compat):
    m, _ = fit(20 + 1.5 * hdd(54) + 3 * cdd(72), eemeter_compat=compat)
    d = json.loads(json.dumps(m.as_dict()))
    m2 = CalTRACKDailyModel.from_dict(d)
    assert m2 == m
    assert np.array_equal(m2.predict(DAYS, T_W).to_numpy(), m.predict(DAYS, T_W).to_numpy())


def test_billing_model_round_trips():
    bills = _monthly_bills(10 + 2 * hdd(60))
    m, _ = fit_caltrack_billing(bills, temp_w(), fuel="gas")
    d = json.loads(json.dumps(m.as_dict()))
    assert d["type"] == "CalTRACKBillingModel"
    m2 = CalTRACKBillingModel.from_dict(d)
    assert m2 == m
    a = m.predict(bills[["start", "end"]], temp_w())
    assert np.array_equal(a.to_numpy(), m2.predict(bills[["start", "end"]], temp_w()).to_numpy())
