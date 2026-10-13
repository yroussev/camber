"""Opt-in TOWT options (0.105): unseen-bin fallback (#154), non-negative predictions (#155),
fixed knots / weighted least squares / the LBNL reference config (#162), and the billing
selector's ModelFitError. Synthetic fixtures only; every default is unchanged."""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv._design import design_rows, projection_variance  # noqa: E402
from camber.mandv.caltrack import towt_savings_hourly  # noqa: E402
from camber.mandv.coverage import towt_coverage  # noqa: E402
from camber.mandv.degreeday import fit_mean_temp_degree_day  # noqa: E402
from camber.mandv.models import ModelFitError, fit_model  # noqa: E402
from camber.mandv.stats import avoided_energy_savings  # noqa: E402
from camber.mandv.sufficiency import InsufficientBaseline  # noqa: E402
from camber.mandv.towt import (  # noqa: E402
    LBNL_KNOTS_F,
    TOWTAtIndex,
    TOWTModel,
    _build_design,
    _temp_components,
    fit_towt,
    hour_of_week,
    lbnl_time_weights,
    towt_lbnl_config,
)


def _hourly(weeks=8, seed=0, start="2024-01-01"):
    idx = pd.date_range(start, periods=weeks * 7 * 24, freq="1h")
    rng = np.random.default_rng(seed)
    occ = (idx.dayofweek < 5) & (idx.hour >= 8) & (idx.hour < 18)
    t = 60 + 15 * np.sin((idx.hour - 9) / 24 * 2 * np.pi) + rng.normal(0, 1, len(idx))
    e = 10.0 + 20.0 * occ + np.clip(t - 68, 0, None) * 0.8 + rng.normal(0, 0.5, len(idx))
    return pd.Series(e, index=idx), pd.Series(t, index=idx)


# --------------------------------------------------------------------------- #154 fallback


def test_default_still_raises_on_unseen_bins_and_is_unchanged():
    e, t = _hourly()
    wd = e.index.dayofweek < 5
    m = fit_towt(e[wd], t[wd])
    with pytest.raises(ValueError, match="never seen at fit"):
        m.predict(e.index, t.to_numpy())
    with pytest.raises(ValueError, match="unseen must be one of"):
        m.predict(e.index, t.to_numpy(), unseen="nan")
    # on covered hours the default is exactly X @ beta, as before
    idx = e.index[wd]
    X = _build_design(hour_of_week(idx), t[wd].to_numpy(), m.bins, m.breakpoints, m.occ_bins)
    np.testing.assert_array_equal(m.predict(idx, t[wd].to_numpy()), X @ m.beta)


def test_fallback_step1_uses_nearest_day_same_hour_same_class():
    e, t = _hourly()
    gap = (e.index.dayofweek == 1) & (e.index.hour == 10)  # every Tuesday 10:00 missing
    m = fit_towt(e[~gap], t[~gap])
    assert 34 not in set(m.bins)  # Tue 10:00 = 1 * 24 + 10
    idx, temp = e.index[gap], t[gap].to_numpy()
    p, counts = m.predict(idx, temp, unseen="fallback", return_counts=True)
    # weekday 10:00 bins are all occupied -> class occupied; Monday and Wednesday are both one day
    # away, and a tie takes the earlier day: Monday 10:00 (bin 10)
    mon = idx - pd.Timedelta(days=1)
    np.testing.assert_allclose(p, m.predict(mon, temp))
    assert counts["n_unseen_fallback"] == gap.sum() and counts["n_unseen_hour_mean"] == 0
    assert counts["n_unseen_unfilled"] == 0 and counts["n_clipped"] == 0


def test_fallback_step2_is_the_hour_of_day_mean():
    e, t = _hourly()
    wd = e.index.dayofweek < 5
    m = fit_towt(e[wd], t[wd])  # the -1.2 vs 40 case of the docstring: no weekend bins at all
    we = e.index[~wd]
    temp = t[~wd].to_numpy()
    p, counts = m.predict(we, temp, unseen="fallback", return_counts=True)
    assert counts["n_unseen_fallback"] == counts["n_unseen_hour_mean"] == len(we)
    # the mean of the five weekday predictions at the same hour and temperature
    monday = we - pd.to_timedelta(we.dayofweek, unit="D")
    days = [monday + pd.Timedelta(days=k) for k in range(5)]
    expect = np.mean([m.predict(d, temp) for d in days], axis=0)
    np.testing.assert_allclose(p, expect)
    assert np.all(np.isfinite(p))


def _toy(bins, occ_bins):
    """A hand-built TOWT model: level = the bin id, no temperature slope."""
    bins = np.asarray(sorted(bins))
    bps = np.array([0.0, 100.0])
    n_t = 1 if occ_bins is None else 2
    beta = np.concatenate([bins.astype(float), np.zeros(n_t)])
    return TOWTModel(bins, bps, None if occ_bins is None else frozenset(occ_bins), beta, 0, True)


def test_fallback_plan_rules():
    tue3 = 1 * 24 + 3
    # Mon 03 occupied, Wed 03 occupied, Thu 03 unoccupied -> class occupied; Mon/Wed tie -> Mon
    m = _toy([3, 2 * 24 + 3, 3 * 24 + 3], {3, 2 * 24 + 3})
    assert m._fallback_plan([tue3])[tue3] == ("bin", 3)
    # Mon 03 unoccupied, Wed/Thu 03 occupied -> class occupied -> the nearest occupied is Wed
    m = _toy([3, 2 * 24 + 3, 3 * 24 + 3], {2 * 24 + 3, 3 * 24 + 3})
    assert m._fallback_plan([tue3])[tue3] == ("bin", 2 * 24 + 3)
    # a class tie (one occupied, one unoccupied) -> the hour-of-day mean
    m = _toy([3, 2 * 24 + 3, 6 * 24 + 3], {3})
    assert m._fallback_plan([tue3])[tue3] == ("mean", (3, 2 * 24 + 3, 6 * 24 + 3))
    # weekday donors are not used for a weekend bin when a weekend bin exists: Sat 03 -> Sun 03
    m = _toy([4 * 24 + 3, 6 * 24 + 3], None)
    assert m._fallback_plan([5 * 24 + 3])[5 * 24 + 3] == ("bin", 6 * 24 + 3)
    # no seen bin at that hour at all -> unfilled
    assert m._fallback_plan([7])[7] == ("none", ())


def test_fallback_unfilled_rows_are_nan_and_counted():
    m = _toy([3, 27], None)
    idx = pd.DatetimeIndex(["2024-01-01 03:00", "2024-01-01 05:00"])  # Mon 03 (seen), Mon 05
    p, c = m.predict(idx, [50.0, 50.0], unseen="fallback", return_counts=True)
    assert p[0] == 3.0 and np.isnan(p[1])
    assert c["n_unseen_unfilled"] == 1 and c["n_unseen_fallback"] == 0


def test_coverage_grades_unseen_bins_unsupported_not_unoccupied():
    e, t = _hourly()
    wd = e.index.dayofweek < 5
    m = fit_towt(e[wd], t[wd])
    cov = towt_coverage(m, e.index, t.to_numpy())
    n_we = int((~wd).sum())
    assert cov.info["n_unseen_tow_bin"] == n_we
    out = cov.outside_mask()
    assert out[~wd].all()  # every weekend hour is outside the support
    assert any("never saw" in c and "not as unoccupied" in c for c in cov.caveats)
    # the per-mode temperature variables count only seen hours
    assert sum(v["n_below"] + v["n_above"] for v in cov.variables) <= int(wd.sum())
    # a fully covered period carries no unseen count
    assert "n_unseen_tow_bin" not in towt_coverage(m, e.index[wd], t[wd].to_numpy()).info


def test_fallback_threads_through_savings_and_exact_kernel():
    e, t = _hourly()
    gap = (e.index.dayofweek == 1) & (e.index.hour == 10)
    base = e.index < e.index[0] + pd.Timedelta(weeks=6)
    m = fit_towt(e[base & ~gap], t[base & ~gap])
    rep = ~base
    idx, temp, y = e.index[rep], t[rep].to_numpy(), e[rep].to_numpy()
    with pytest.raises(ValueError, match="never seen"):
        avoided_energy_savings(
            TOWTAtIndex(m, idx), temp, y, cv_rmse=0.1, n_baseline=900, p_baseline=180
        )
    a = TOWTAtIndex(m, idx, unseen="fallback")
    r = avoided_energy_savings(
        a, temp, y, cv_rmse=0.1, n_baseline=900, p_baseline=180, kernel="exact", rho=0.0
    )
    n_gap = int(gap[rep].sum())
    assert a.predict_counts["n_unseen_fallback"] == n_gap
    assert any(f"{n_gap} reporting row(s) fall in hour-of-week bins" in c for c in r.caveats)
    assert r.coverage["info"]["n_unseen_tow_bin"] == n_gap
    # the exact kernel's design rows are the fallback's: X @ beta == the prediction
    X = design_rows(a, temp)
    np.testing.assert_allclose(X @ m.beta, a.predict(temp))
    with pytest.raises(ValueError, match="never seen"):
        design_rows(TOWTAtIndex(m, idx), temp)


def test_towt_savings_hourly_fallback_accepts_a_baseline_missing_bins():
    e, t = _hourly(weeks=14)
    gap = (e.index.dayofweek == 1) & (e.index.hour == 10)
    cut = e.index[0] + pd.Timedelta(weeks=10)
    eb, tb = e[(e.index < cut) & ~gap], t[(e.index < cut) & ~gap]
    er, tr = e[e.index >= cut], t[e.index >= cut]
    with pytest.raises(InsufficientBaseline, match="167 of 168"):
        towt_savings_hourly(eb, tb, er, tr)
    res = towt_savings_hourly(eb, tb, er, tr, unseen="fallback")
    assert res.n_tow_bins == 167
    assert any("covers 167 of 168" in c for c in res.savings.caveats)
    assert any("unseen='fallback' predicted" in c for c in res.savings.caveats)


# --------------------------------------------------------------------------- #155 clipping


def _low_load():
    """A low-load building whose TOWT fit predicts below zero at the coldest night hours."""
    e, t = _hourly(seed=3)
    e = (e - 10.5).clip(lower=0.0) + 0.02  # nights near zero
    return e, t


def test_clip_negative_counts_and_default_unchanged():
    e, t = _low_load()
    m = fit_towt(e, t)
    temp = t.to_numpy() - 25.0  # colder than the baseline: drives some projections negative
    raw = m.predict(e.index, temp)
    n_neg = int((raw < 0).sum())
    assert n_neg > 0
    p, c = m.predict(e.index, temp, clip_negative=True, return_counts=True)
    assert c["n_clipped"] == n_neg
    np.testing.assert_array_equal(p, np.where(raw < 0, 0.0, raw))
    np.testing.assert_array_equal(m.predict(e.index, temp), raw)  # default: no clipping


def test_clip_negative_leaves_uncertainty_on_the_unclipped_projection():
    e, t = _low_load()
    m = fit_towt(e, t)
    temp = t.to_numpy() - 25.0
    plain = projection_variance(TOWTAtIndex(m, e.index), temp)
    clipped = TOWTAtIndex(m, e.index, clip_negative=True)
    pv = projection_variance(clipped, temp)
    assert pv.v_param == plain.v_param and pv.v_noise == plain.v_noise
    np.testing.assert_array_equal(pv.g, plain.g)
    y = e.to_numpy()
    r0 = avoided_energy_savings(
        TOWTAtIndex(m, e.index), temp, y, cv_rmse=0.2, n_baseline=1300, p_baseline=180, rho=0.0
    )
    r1 = avoided_energy_savings(
        clipped, temp, y, cv_rmse=0.2, n_baseline=1300, p_baseline=180, rho=0.0
    )
    n_clip = clipped.predict_counts["n_clipped"]
    assert any(f"{n_clip} negative baseline projection(s)" in c for c in r1.caveats)
    assert not any("negative baseline projection" in c for c in r0.caveats)


def test_change_point_and_degree_day_clip_negative():
    T = np.linspace(30, 90, 60)
    y = 5.0 - 0.2 * T + np.random.default_rng(1).normal(0, 0.05, 60)  # goes negative
    m = fit_model(T, y, "2P")
    raw = m.predict(T)
    p, c = m.predict(T, clip_negative=True, return_counts=True)
    assert c["n_clipped"] == int((raw < 0).sum()) > 0
    np.testing.assert_array_equal(p, np.where(raw < 0, 0.0, raw))
    np.testing.assert_array_equal(m.predict(T), raw)
    assert m.predict(90.0, clip_negative=True) == 0.0
    idx = pd.date_range("2024-01-01", periods=60, freq="D")
    dd = fit_mean_temp_degree_day(pd.Series(y, index=idx), pd.Series(T, index=idx))
    raw = np.asarray(dd.predict(T), dtype=float)
    q, c = dd.predict(T, clip_negative=True, return_counts=True)
    assert c["n_clipped"] == int((raw < 0).sum())
    assert np.all(np.asarray(q) >= 0)


# --------------------------------------------------------------------------- #162 knots, WLS


def test_component_temperatures_match_mathieu_table_3():
    # Mathieu et al. 2011, LBNL-4944E, Table 3: B1..B5 = 10..50 (arbitrary units)
    bps = np.array([0.0, 10, 20, 30, 40, 50, 60])
    got = _temp_components(np.array([2.0, 18, 32, 47, 58]), bps, open_ends=True)
    expect = [
        [2, 0, 0, 0, 0, 0],
        [10, 8, 0, 0, 0, 0],
        [10, 10, 10, 2, 0, 0],
        [10, 10, 10, 10, 7, 0],
        [10, 10, 10, 10, 10, 8],
    ]
    np.testing.assert_array_equal(got, expect)
    # outer segments are unbounded: below the first knot and above the last
    out = _temp_components(np.array([-5.0, 75.0]), bps, open_ends=True)
    np.testing.assert_array_equal(out, [[-5, 0, 0, 0, 0, 0], [10, 10, 10, 10, 10, 25]])


def test_fixed_knots_recover_a_piecewise_truth_exactly():
    idx = pd.date_range("2024-01-01", periods=6 * 7 * 24, freq="1h")
    rng = np.random.default_rng(5)
    t = rng.uniform(30, 100, len(idx))
    slopes = [-1.0, -0.5, 0.0, 0.7, 1.5]  # knots 50, 60, 70, 80
    comp = _temp_components(t, np.array([30.0, 50, 60, 70, 80, 100]), open_ends=True)
    e = 100.0 + 3.0 * (idx.hour.to_numpy() % 5) + comp @ slopes
    m = fit_towt(pd.Series(e, idx), pd.Series(t, idx), knots=[50, 60, 70, 80], occ_split=False)
    assert m.open_ends and m.breakpoints[1:-1].tolist() == [50, 60, 70, 80]
    np.testing.assert_allclose(m.beta[-5:], slopes, atol=1e-9)
    np.testing.assert_allclose(m.predict(idx, t), e, atol=1e-8)
    # beyond the fitted range the outer slopes continue (no flat hold)
    hot = m.predict(idx[:1], [110.0])[0] - m.predict(idx[:1], [100.0])[0]
    assert hot == pytest.approx(10 * 1.5)


def test_knot_pruning_follows_rmv2():
    idx = pd.date_range("2024-01-01", periods=200, freq="1h")
    t = np.r_[np.full(10, 35.0), np.full(170, 60.0), np.full(20, 95.0)]
    e = pd.Series(np.arange(200.0), idx)
    m = fit_towt(e, pd.Series(t, idx), knots=LBNL_KNOTS_F, knot_min_obs=20, occ_split=False)
    # 10 points below 40 and below 55 -> both dropped; 20 above 90 (not fewer than 20) -> kept
    assert m.breakpoints[1:-1].tolist() == [65, 80, 90]
    keep_all = fit_towt(e, pd.Series(t, idx), knots=LBNL_KNOTS_F, occ_split=False)
    assert keep_all.breakpoints[1:-1].tolist() == list(LBNL_KNOTS_F)
    with pytest.raises(ValueError, match="increasing"):
        fit_towt(e, pd.Series(t, idx), knots=[60, 50])


def test_weighted_fit_reproduces_a_hand_computed_case():
    # one hour-of-week bin (weekly stamps), temperatures 0 and 1 below the only knot: the model is
    # level + slope * T, so the WLS fit is the weighted mean of energy at each temperature
    idx = pd.date_range("2024-01-01", periods=60, freq="7D")
    t = np.repeat([0.0, 1.0], 30)
    y = np.r_[np.tile([0.0, 3.0], 15), np.tile([10.0, 4.0], 15)]
    w = np.r_[np.tile([2.0, 1.0], 15), np.tile([1.0, 2.0], 15)]
    E, T = pd.Series(y, idx), pd.Series(t, idx)
    m = fit_towt(E, T, knots=[50.0], occ_split=False, weights=w)
    # T=0: (0*2 + 3*1)/3 = 1; T=1: (10*1 + 4*2)/3 = 6
    assert m.predict(idx[:2], [0.0, 1.0]) == pytest.approx([1.0, 6.0])
    plain = fit_towt(E, T, knots=[50.0], occ_split=False)
    assert plain.predict(idx[:2], [0.0, 1.0]) == pytest.approx([1.5, 7.0])
    assert m.weighted and not plain.weighted
    # equal weights take the unweighted path exactly; a series aligns by timestamp
    same = fit_towt(E, T, knots=[50.0], occ_split=False, weights=np.full(60, 4.0))
    np.testing.assert_array_equal(same.beta, plain.beta)
    by_index = fit_towt(E, T, knots=[50.0], occ_split=False, weights=pd.Series(w, idx)[::-1])
    np.testing.assert_allclose(by_index.beta, m.beta)
    with pytest.raises(ValueError, match="positive"):
        fit_towt(E, T, weights=np.r_[w[:-1], 0.0])


def test_weighted_fit_record_is_the_wls_projection():
    e, t = _hourly(weeks=6)
    w = lbnl_time_weights(e.index, e.index[-1])
    m = fit_towt(e, t, weights=w, occ_split=False)
    X = design_rows(m, t.to_numpy(), index=e.index)
    wn = w / w.mean()
    A = np.linalg.pinv(X.T @ (X * wn[:, None]))
    np.testing.assert_allclose(m._fit_record.xtx_pinv, A, rtol=1e-6, atol=1e-10)
    beta = A @ (X.T @ (wn * e.to_numpy()))
    np.testing.assert_allclose(X @ m.beta, X @ beta, rtol=1e-8)


def test_lbnl_config_matches_rmv2():
    cfg = towt_lbnl_config()
    assert cfg.knots == (40.0, 55.0, 65.0, 80.0, 90.0) and cfg.units == "degF"
    assert cfg.knot_min_obs == 20 and cfg.timescale_days == 14.0
    assert cfg.occ_split and cfg.clip_negative
    assert cfg.sources == ("mathieu-2011-towt", "lbnl-rmv2")
    c = towt_lbnl_config("degC")
    np.testing.assert_allclose(c.knots, [(k - 32) * 5 / 9 for k in LBNL_KNOTS_F])
    assert cfg.fit_kwargs() == {"knots": cfg.knots, "knot_min_obs": 20, "occ_split": True}
    assert cfg.predict_kwargs() == {"clip_negative": True}
    with pytest.raises(ValueError):
        towt_lbnl_config("K")
    # RMV2.0 makeBaseline: timescaleDays^2 / (timescaleDays^2 + tDiff^2), tDiff in days
    idx = pd.DatetimeIndex(["2024-03-01", "2024-02-16", "2024-02-02", "2024-03-01 12:00"])
    np.testing.assert_allclose(
        cfg.time_weights(idx, "2024-03-01"), [1.0, 0.5, 0.2, 196 / 196.25], rtol=1e-12
    )
    e, t = _hourly()
    m = fit_towt(e, t, **cfg.fit_kwargs(), weights=cfg.time_weights(e.index, e.index[-1]))
    assert m.open_ends and m.weighted


def test_lbnl_reference_is_registered():
    from camber.mandv.names import mv_name
    from camber.references import MV_REFERENCES, REFERENCES

    assert "lbnl-rmv2" in MV_REFERENCES and "lbnl-rmv2" in REFERENCES
    row = mv_name("towt")
    assert "lbnl-rmv2" in row.citation_id and "towt_lbnl_config()" in row.differences


# --------------------------------------------------------------------------- serialisation


def test_new_fields_round_trip_and_old_dicts_load():
    e, t = _hourly(weeks=6)
    cfg = towt_lbnl_config()
    m = fit_towt(e, t, **cfg.fit_kwargs(), weights=cfg.time_weights(e.index, e.index[-1]))
    d = m.as_dict()
    assert d["open_ends"] is True and d["weighted"] is True
    r = TOWTModel.from_dict(d)
    assert r.open_ends and r.weighted
    temp = t.to_numpy() + 30  # beyond the fitted range, where open ends matter
    np.testing.assert_array_equal(r.predict(e.index, temp), m.predict(e.index, temp))
    plain = fit_towt(e, t)
    pd_ = plain.as_dict()
    assert "open_ends" not in pd_ and "weighted" not in pd_  # a default dict is unchanged
    old = TOWTModel.from_dict(pd_)  # a pre-0.105 dict has neither key
    assert not old.open_ends and not old.weighted
    np.testing.assert_array_equal(old.predict(e.index, t.to_numpy()), plain.predict(e.index, t))


# --------------------------------------------------------------------------- billing selector


def test_best_billing_model_raises_model_fit_error_listing_candidates():
    from camber.mandv._mvform import best_billing_model

    fr = pd.DataFrame(
        {"oat": [40.0, 50.0], "energy": [3.0, 2.0], "days": [30.0, 31.0], "hdd": [750.0, 465.0]},
        index=pd.date_range("2024-01-31", periods=2, freq="ME"),
    )
    fr.attrs.update(dd_kind="DD-H", heating_base_f=65.0, dd_bases_fitted=False)
    with pytest.raises(ModelFitError) as ei:
        best_billing_model(fr)
    kinds = [c["kind"] for c in ei.value.candidates]
    assert kinds == ["2P", "3PC", "3PH", "4P", "5P", "DD-H"]
    assert all(c["refused"] for c in ei.value.candidates)
    msg = str(ei.value)
    assert msg.startswith("no model could be fit") and "DD-H (p=None): ValueError: need more" in msg
    assert "2P (p=2)" in msg
    with pytest.raises(ModelFitError) as ei:
        best_billing_model(fr, family="dd")
    assert [c["kind"] for c in ei.value.candidates] == ["DD-H"]
