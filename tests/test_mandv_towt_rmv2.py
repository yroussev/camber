"""TOWT, LBNL RMV2.0 variant (0.105, #165): RMV2.0's occupancy rule and weighted ensemble.

Synthetic fixtures and hand-computed cases only. ``test_matches_rmv2_makebaseline`` compares with
RMV2.0 itself and is skipped unless ``CAMBER_RMV2_RSCRIPT`` names an ``Rscript`` (dev-only; R is
never a runtime or CI dependency). It also needs either ``CAMBER_RMV2_SOURCE``, the path of
``R/towt_baseline.R`` from LBNL-ETA/RMV2.0 at commit 979a963 (its model functions use base R
only), or RMV2.0 installed in ``CAMBER_RMV2_LIB`` (or R's default library)::

    curl -LO https://raw.githubusercontent.com/LBNL-ETA/RMV2.0/979a963/R/towt_baseline.R
    CAMBER_RMV2_RSCRIPT=$(which Rscript) CAMBER_RMV2_SOURCE=$PWD/towt_baseline.R \\
        python -m pytest tests/test_mandv_towt_rmv2.py -k rmv2_makebaseline
"""

import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv import names as N  # noqa: E402
from camber.mandv.towt import TOWTAtIndex, fit_towt, towt_lbnl_config  # noqa: E402
from camber.mandv.towt_rmv2 import (  # noqa: E402
    TOWTRMV2Model,
    fit_towt_rmv2,
    interval_of_week,
    rmv2_centres,
    rmv2_occupied_intervals,
)

_NO_SCREEN = (-1e9, 1e9)


def _synthetic(days, *, freq="1h", seed=0, start="2023-01-02", seasonal_schedule=False, tz=None):
    """Hourly (or sub-hourly) load with a weekday schedule, heating and cooling, and seasons."""
    step = pd.Timedelta(freq).total_seconds()
    idx = pd.date_range(start, periods=int(days * 86400 / step), freq=freq, tz=tz)
    rng = np.random.default_rng(seed)
    hour = idx.hour.to_numpy()
    season = np.sin((idx.dayofyear.to_numpy() - 110) / 365 * 2 * np.pi)
    t = 55 + 25 * season + 8 * np.sin((hour - 9) / 24 * 2 * np.pi) + rng.normal(0, 2, len(idx))
    if seasonal_schedule:  # a shorter summer day: not a temperature effect
        summer = season > 0.3
        start_h, end_h = np.where(summer, 7, 8), np.where(summer, 15, 18)
    else:
        start_h, end_h = 8, 18
    occ = (idx.dayofweek.to_numpy() < 5) & (hour >= start_h) & (hour < end_h)
    e = (
        20
        + 30 * occ
        + 0.9 * np.clip(t - 65, 0, None)
        + 0.4 * np.clip(50 - t, 0, None)
        + rng.normal(0, 2, len(idx))
    )
    return pd.Series(e, index=idx), pd.Series(t, index=idx)


# --------------------------------------------------------------------------- occupancy rule


def test_occupancy_rule_on_a_constructed_case():
    # temperatures between the hinges: the regression is the mean (0), so residuals are the loads
    tow = np.repeat([0, 1, 2], 20)
    load = np.concatenate(
        [
            [1.0] * 14 + [-1.0] * 6,  # 70 % positive: occupied
            [1.0] * 13 + [-1.0] * 7,  # exactly 65 %: not (RMV2.0 needs more than 65 %)
            [1.0] * 3 + [-1.0] * 17,  # 15 %
        ]
    )
    temp = np.full(60, 58.0)
    assert rmv2_occupied_intervals(tow, load, temp) == frozenset({0})


def test_occupancy_rule_regresses_on_the_50_and_65_degf_hinges():
    # load exactly linear in the hinges plus a +/- pattern per interval: the hinge fit removes the
    # weather, so a hot interval is not called occupied for being hot
    rng = np.random.default_rng(1)
    tow = np.repeat(np.arange(4), 40)
    temp = rng.uniform(20, 100, len(tow))
    weather = 50 + 2.0 * np.minimum(temp - 50, 0) + 3.0 * np.maximum(temp - 65, 0)
    sign = np.where(np.arange(len(tow)) % 40 < 30, 1.0, -1.0)  # 75 % positive...
    sign[tow >= 2] *= -1  # ...in intervals 0 and 1, 25 % in 2 and 3 (sums to zero)
    assert rmv2_occupied_intervals(tow, weather + sign, temp) == frozenset({0, 1})
    hot = tow == 3
    temp2 = np.where(hot, temp + 30, temp)
    weather2 = 50 + 2.0 * np.minimum(temp2 - 50, 0) + 3.0 * np.maximum(temp2 - 65, 0)
    assert rmv2_occupied_intervals(tow, weather2 + sign, temp2) == frozenset({0, 1})


def test_two_or_fewer_occupied_intervals_fit_one_model():
    idx = pd.date_range("2024-01-01", periods=21 * 24, freq="1h")
    rng = np.random.default_rng(2)
    # temperatures between the hinges, so the occupancy regression is the mean load
    t = pd.Series(rng.uniform(52, 63, len(idx)), index=idx)
    how = interval_of_week(idx)
    e = pd.Series(10 + 5 * np.isin(how, [10, 11]) + 0.0, index=idx)
    m = fit_towt_rmv2(e, t)
    assert m.occupied == frozenset({10, 11})
    assert m.occ_bins is None  # RMV2.0 line 298: the split needs more than two
    e3 = pd.Series(10 + 5 * np.isin(how, [10, 11, 12]) + 0.0, index=idx)
    assert fit_towt_rmv2(e3, t).occ_bins == frozenset({10, 11, 12})


def test_interval_of_week():
    idx = pd.DatetimeIndex(["2024-01-01 00:00", "2024-01-01 00:14", "2024-01-01 00:15",
                            "2024-01-07 23:59"])  # fmt: skip
    assert interval_of_week(idx, 15).tolist() == [0, 0, 1, 671]
    assert interval_of_week(idx, 60).tolist() == [0, 0, 0, 167]
    with pytest.raises(ValueError, match="divide"):
        interval_of_week(idx, 7)


# --------------------------------------------------------------------------- ensemble centres


def test_centres_against_hand_computation():
    # 100 hourly rows span 99 h = 4.125 days; with s = 1 day: nsegments = ceil(4.125) = 5,
    # segmentwidth = 99 / 5 = 19.8, floor(sort(100 - 19.8 * 0:5) + 0.001) = 1, 20, 40, 60, 80, 100
    idx = pd.date_range("2024-01-01", periods=100, freq="1h")
    assert rmv2_centres(idx, 1.0).tolist() == [0, 19, 39, 59, 79, 99]
    # a span shorter than the timescale: one segment, centres at the first and last rows
    assert rmv2_centres(idx, 14.0).tolist() == [0, 99]
    # a year of hourly data: 364.96 days / 14 -> 27 segments, 28 centres
    year = pd.date_range("2023-01-01", periods=8760, freq="1h")
    pos = rmv2_centres(year)
    assert len(pos) == 28 and pos[0] == 0 and pos[-1] == 8759
    assert np.all(np.diff(pos) >= 323) and np.all(np.diff(pos) <= 325)  # 8759 / 27 = 324.4


def test_weights_against_hand_computation():
    e, t = _synthetic(60)
    m = fit_towt_rmv2(e, t)
    c = m.centres[0]
    w = m.weights(pd.DatetimeIndex([c, c + pd.Timedelta(days=7), c - pd.Timedelta(days=14)]))
    # s^2 / (s^2 + dt^2): 196/196, 196/245, 196/392
    np.testing.assert_allclose(w[:, 0], [1.0, 0.8, 0.5])
    assert w.shape == (3, len(m.centres))


# --------------------------------------------------------------------------- combined prediction


def _hand_model(beta1, beta2):
    """One interval (Monday 00:00), one knot at 50 degF.

    The design columns are [level, min(T, 50), max(T - 50, 0)].
    """
    t0 = pd.Timestamp("2024-01-15 00:00")  # a Monday
    return t0, TOWTRMV2Model(
        bins=np.array([0]),
        breakpoints=np.array([0.0, 50.0, 100.0]),
        occ_bins=None,
        occupied=frozenset(),
        betas=np.array([beta1, beta2], dtype=float),
        centres=pd.DatetimeIndex([t0 - pd.Timedelta(days=14), t0 + pd.Timedelta(days=28)]),
        temp_bounds=_NO_SCREEN,
    )


def test_combined_prediction_against_hand_computation():
    t0, m = _hand_model([10.0, 0.1, 1.0], [20.0, 0.0, 0.0])
    # at 60 degF: p1 = 10 + 0.1 * 50 + 1 * 10 = 25, p2 = 20; w1 = 196 / 392 = 0.5,
    # w2 = 196 / (196 + 784) = 0.2; (0.5 * 25 + 0.2 * 20) / 0.7
    out = m.predict([t0], [60.0])
    np.testing.assert_allclose(out, [(0.5 * 25 + 0.2 * 20) / 0.7])
    np.testing.assert_allclose(m.local_predictions([t0], [60.0]), [[25.0, 20.0]])


def test_each_local_prediction_is_clipped_before_averaging():
    t0, m = _hand_model([-30.0, 0.1, 1.0], [20.0, 0.0, 0.0])  # p1 = -15 at 60 degF
    np.testing.assert_allclose(m.predict([t0], [60.0]), [(0.5 * 0 + 0.2 * 20) / 0.7])
    raw, counts = m.predict([t0], [60.0], clip_negative=False, return_counts=True)
    np.testing.assert_allclose(raw, [(0.5 * -15 + 0.2 * 20) / 0.7])
    assert counts["n_clipped"] == 0
    assert m.predict([t0], [60.0], return_counts=True)[1]["n_clipped"] == 1


def test_predict_is_the_weighted_average_of_the_local_fits():
    e, t = _synthetic(90, seed=4)
    m = fit_towt_rmv2(e, t)
    idx = pd.date_range(e.index[-1] + pd.Timedelta(hours=1), periods=24 * 30, freq="1h")
    temp = np.linspace(20, 95, len(idx))
    P, W = m.local_predictions(idx, temp), m.weights(idx)
    np.testing.assert_allclose(m.predict(idx, temp), (P * W).sum(1) / W.sum(1))
    # the local fits differ (each is weighted about its own centre) and none dominates far out
    assert np.ptp(P, axis=1).max() > 0.1
    assert len(m.centres) == 8  # 89.96 days / 14 -> 7 segments


def test_units_degc_gives_the_same_baseline():
    e, t = _synthetic(60, seed=6)
    mf = fit_towt_rmv2(e, t)
    mc = fit_towt_rmv2(e, (t - 32) * 5 / 9, units="degC")
    assert mf.occupied == mc.occupied
    idx = pd.date_range(e.index[-1] + pd.Timedelta(hours=1), periods=200, freq="1h")
    tf = np.linspace(30, 90, 200)
    np.testing.assert_allclose(mf.predict(idx, tf), mc.predict(idx, (tf - 32) * 5 / 9), rtol=1e-8)


def test_missing_temperature_predicts_nan():
    e, t = _synthetic(30)
    m = fit_towt_rmv2(e, t)
    out, counts = m.predict(e.index[:3], [60.0, np.nan, 999.0], return_counts=True)
    assert np.isfinite(out[0]) and np.isnan(out[1]) and np.isnan(out[2])
    assert counts["n_no_temperature"] == 2 and counts["n_temp_screened"] == 1


# --------------------------------------------------------------------------- unseen time of week


def _with_unseen(seed=3):
    e, t = _synthetic(60, seed=seed)
    gap = (e.index.dayofweek == 6) & (e.index.hour == 3)  # never a Sunday 03:00 in training
    return fit_towt_rmv2(e[~gap], t[~gap]), e.index[-1]


def test_unseen_time_of_week_predicts_nan():
    m, last = _with_unseen()
    idx = pd.date_range(last + pd.Timedelta(hours=1), periods=24 * 14, freq="1h")
    temp = np.full(len(idx), 55.0)
    out, counts = m.predict(idx, temp, return_counts=True)
    unseen = (idx.dayofweek == 6) & (idx.hour == 3)
    assert unseen.sum() == 2
    assert np.isnan(out[unseen]).all() and np.isfinite(out[~unseen]).all()
    assert counts["n_unseen_unfilled"] == 2
    assert not m.covers(idx)
    assert np.isnan(m.local_predictions(idx, temp)[unseen]).all()
    with pytest.raises(ValueError, match="never seen"):
        m.predict(idx, temp, unseen="raise")


def test_unseen_previous_fills_like_rmv2_approx_constant():
    m, last = _with_unseen()
    # ends on a Sunday 03:00: a trailing unseen row stays NaN (approx rule = 1)
    idx = pd.date_range("2023-03-12 00:00", "2023-03-19 03:00", freq="1h")
    temp = np.full(len(idx), 55.0)
    base = m.predict(idx, temp)
    out, counts = m.predict(idx, temp, unseen="previous", return_counts=True)
    first, last_row = idx.get_loc(pd.Timestamp("2023-03-12 03:00")), len(idx) - 1
    assert out[first] == base[first - 1]  # Sunday 03:00 takes Sunday 02:00's prediction
    assert np.isnan(out[last_row])
    assert counts["n_unseen_previous"] == 1 and counts["n_unseen_unfilled"] == 1


def test_unseen_fallback_is_opt_in_and_hourly_only():
    m, last = _with_unseen()
    idx = pd.date_range(last + pd.Timedelta(hours=1), periods=24 * 7, freq="1h")
    out, counts = m.predict(idx, np.full(len(idx), 55.0), unseen="fallback", return_counts=True)
    assert np.isfinite(out).all() and counts["n_unseen_fallback"] == 1
    e, t = _synthetic(30, freq="15min")
    gap = (e.index.dayofweek == 6) & (e.index.hour == 3)
    m15 = fit_towt_rmv2(e[~gap], t[~gap], interval_minutes=15)
    with pytest.raises(ValueError, match="interval_minutes=60"):
        m15.predict(e.index[gap][:2], [55.0, 55.0], unseen="fallback")
    with pytest.raises(ValueError, match="unseen must be"):
        m.predict(idx, np.full(len(idx), 55.0), unseen="zero")


# --------------------------------------------------------------------------- serialisation


@pytest.mark.parametrize("tz", [None, "America/New_York"])
def test_as_dict_round_trip(tz):
    e, t = _synthetic(45, seed=8, start="2024-02-20", tz=tz)  # spans a DST change when tz-aware
    m = fit_towt_rmv2(e, t)
    d = json.loads(json.dumps(m.as_dict()))
    assert d["type"] == "TOWTRMV2Model" and d["kind"] == "towt_rmv2"
    m2 = TOWTRMV2Model.from_dict(d)
    assert m2.centres.equals(m.centres)
    assert m2.occupied == m.occupied and m2.occ_bins == m.occ_bins
    idx = e.index[-200:]
    np.testing.assert_array_equal(m2.predict(idx, t[-200:]), m.predict(idx, t[-200:]))
    assert m2.as_dict() == d


def test_registry_row_and_label():
    row = N.mv_name("towt_rmv2")
    assert row.label == "TOWT, LBNL RMV2.0 variant"
    assert row.citation_id[:2] == ("lbnl-rmv2", "mathieu-2011-towt")
    assert row.callable == "camber.mandv.towt_rmv2.fit_towt_rmv2"
    e, t = _synthetic(30)
    m = fit_towt_rmv2(e, t)
    assert N.model_label(m) == N.model_label(m.as_dict()) == "TOWT, LBNL RMV2.0 variant"


def test_binds_with_towt_at_index():
    e, t = _synthetic(30)
    m = fit_towt_rmv2(e, t)
    b = TOWTAtIndex(m, e.index[-48:])
    np.testing.assert_array_equal(b.predict(t[-48:]), m.predict(e.index[-48:], t[-48:]))
    assert b.predict_counts["n_clipped"] == 0


# --------------------------------------------------------------------------- long horizon


def test_ensemble_beats_one_recency_weighted_fit_on_a_held_out_year():
    e, t = _synthetic(2 * 364, seed=0, seasonal_schedule=True)
    n = 364 * 24
    tr, te = slice(0, n), slice(n, None)
    actual = e.to_numpy()[te]

    def cvrmse(p):
        return 100 * np.sqrt(np.mean((actual - p) ** 2)) / actual.mean()

    ens = fit_towt_rmv2(e[tr], t[tr]).predict(e.index[te], t.to_numpy()[te])
    cfg = towt_lbnl_config()
    one = fit_towt(
        e[tr], t[tr], **cfg.fit_kwargs(), weights=cfg.time_weights(e.index[tr], e.index[n - 1])
    ).predict(e.index[te], t.to_numpy()[te], **cfg.predict_kwargs())
    assert np.isfinite(ens).all()
    assert cvrmse(ens) < cvrmse(one) - 1.5  # about 14.8 against 18.1


# --------------------------------------------------------------------------- RMV2.0 itself

_R_SCRIPT = r"""
args <- commandArgs(trailingOnly = TRUE)
if (nzchar(args[1])) {
  source(args[1])
} else {
  makeBaseline <- utils::getFromNamespace("makeBaseline", "RMV2.0")
}
tr <- read.csv(args[2]); pr <- read.csv(args[3])
fmt <- "%Y-%m-%d %H:%M:%S"
tt <- as.POSIXlt(as.POSIXct(tr$time, tz = "UTC", format = fmt))
pt <- as.POSIXlt(as.POSIXct(pr$time, tz = "UTC", format = fmt))
out <- makeBaseline(tt, tr$eload, tr$Temp, pt, pr$Temp, intervalMinutes = as.numeric(args[5]),
                    timescaleDays = 14, fahrenheit = TRUE, verbose = 0)
y <- approx(as.numeric(out$timeVec), out$Baseline, as.numeric(pt), method = "constant")$y
write.csv(data.frame(baseline = out$Baseline, approx = y), args[4], row.names = FALSE)
write.csv(data.frame(train = out$trainBaseline), paste0(args[4], ".train.csv"), row.names = FALSE)
"""


@pytest.mark.skipif(
    not os.environ.get("CAMBER_RMV2_RSCRIPT"),
    reason="dev-only cross-check: set CAMBER_RMV2_RSCRIPT (and CAMBER_RMV2_SOURCE or _LIB)",
)
@pytest.mark.parametrize(
    "days, freq, minutes, pred_days, gap",
    [
        (365, "1h", 60, 365, False),
        (365, "1h", 15, 30, False),
        (90, "1h", 60, 30, True),
        (60, "15min", 15, 14, False),
        (20, "1h", 60, 10, False),
    ],  # fmt: skip
)
def test_matches_rmv2_makebaseline(tmp_path, days, freq, minutes, pred_days, gap):
    step = pd.Timedelta(freq).total_seconds()
    e, t = _synthetic(days + pred_days, freq=freq, seed=days)
    n = int(days * 86400 / step)
    te, tt = e.iloc[:n], t.iloc[:n]
    if gap:
        keep = ~((te.index.dayofweek == 6) & (te.index.hour == 3))
        te, tt = te[keep], tt[keep]
    pidx, ptemp = e.index[n:], t.to_numpy()[n:]
    stamp = "%Y-%m-%d %H:%M:%S"
    pd.DataFrame({"time": te.index.strftime(stamp), "eload": te.to_numpy(), "Temp": tt.to_numpy()}
                 ).to_csv(tmp_path / "tr.csv", index=False)  # fmt: skip
    pd.DataFrame({"time": pidx.strftime(stamp), "eload": 0.0, "Temp": ptemp}).to_csv(
        tmp_path / "pr.csv", index=False
    )
    (tmp_path / "run.R").write_text(_R_SCRIPT)
    env = {**os.environ, "TZ": "UTC"}
    if os.environ.get("CAMBER_RMV2_LIB"):
        env["R_LIBS"] = os.environ["CAMBER_RMV2_LIB"]
    out = tmp_path / "out.csv"
    subprocess.run(
        [
            os.environ["CAMBER_RMV2_RSCRIPT"],
            str(tmp_path / "run.R"),
            os.environ.get("CAMBER_RMV2_SOURCE", ""),
            str(tmp_path / "tr.csv"),
            str(tmp_path / "pr.csv"),
            str(out),
            str(minutes),
        ],  # fmt: skip
        check=True,
        env=env,
        capture_output=True,
    )
    r = pd.read_csv(out)
    r_train = pd.read_csv(str(out) + ".train.csv")["train"].to_numpy()
    m = fit_towt_rmv2(te, tt, interval_minutes=minutes, temp_bounds=_NO_SCREEN)
    for ours, theirs in [
        (m.predict(pidx, ptemp), r["baseline"].to_numpy()),
        (m.predict(pidx, ptemp, unseen="previous"), r["approx"].to_numpy()),
        (m.predict(te.index, tt.to_numpy(), clip_negative=False), r_train),
    ]:
        np.testing.assert_array_equal(np.isnan(ours), np.isnan(theirs))
        ok = ~np.isnan(ours)
        np.testing.assert_allclose(ours[ok], theirs[ok], rtol=1e-8, atol=1e-8)
