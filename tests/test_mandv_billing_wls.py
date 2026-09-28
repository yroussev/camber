"""Days-weighted billing fits (issue #64): least squares weighted by each bill's days.

Equal weights must be neutral -- the unweighted fit, byte for byte -- and a weighted fit must be
the ordinary fit of the bills expanded to their days. Totals sum ``days * energy per day``, and
the exact kernel's noise is that of the days summed.
"""

import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv import _mvform  # noqa: E402
from camber.mandv._design import projection_variance  # noqa: E402
from camber.mandv.adjustments import IndicatorFit, estimate_nre_indicator  # noqa: E402
from camber.mandv.billing import BillingSeries  # noqa: E402
from camber.mandv.methods import (  # noqa: E402
    backcast_savings,
    chained_savings,
    forecast_savings,
    select_method,
)
from camber.mandv.models import (  # noqa: E402
    N_PARAMS,
    ChangePointModel,
    best_model,
    fit_model,
    fit_weights,
)
from camber.mandv.stats import (  # noqa: E402
    avoided_energy_savings,
    fit_stats,
    model_regression_tests,
    regression_tests,
)


def _daily(n=400, seed=3):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2023-01-01", periods=n, freq="D")
    T = 55 - 25 * np.cos(2 * np.pi * (idx.dayofyear - 15) / 365.25) + rng.normal(0, 6, n)
    y = 300 + 11 * np.maximum(0, 58 - T) + 4 * np.maximum(0, T - 70) + rng.normal(0, 25, n)
    return idx, T, y


def _bills(seed=0, years=("2020-01-01", "2023-12-31"), save=0.0, save_from="2023-01-01"):
    """Synthetic bills of uneven length (8-62 days) from a daily truth, with their weather."""
    rng = np.random.default_rng(seed)
    days = pd.date_range(*years, freq="D")
    T = 55 - 25 * np.cos(2 * np.pi * (days.dayofyear - 15) / 365.25) + rng.normal(0, 7, len(days))
    y = 900 - 8 * T + rng.normal(0, 40, len(days))
    y = np.where(days >= save_from, y * (1 - save), y)
    cut = int(np.argmax(days >= save_from)) if save else -1
    rows, i = [], 0
    while i < len(days):
        n = int(rng.choice([8, 12, 55, 62])) if rng.random() < 0.25 else int(rng.integers(25, 39))
        j = min(i + n, len(days))
        if i < cut < j:
            j = cut
        rows.append((days[i], days[j - 1], float(y[i:j].sum())))
        i = j
    bs = BillingSeries.from_frame(pd.DataFrame(rows, columns=["start", "end", "energy"]))
    return bs, pd.Series(T, index=days)


# --------------------------------------------------------------------------- neutrality


@pytest.mark.parametrize("kind", sorted(N_PARAMS))
def test_equal_weights_are_neutral_for_every_kind(kind):
    idx, T, y = _daily()
    a = fit_model(T, y, kind, time_index=idx)
    b = fit_model(T, y, kind, time_index=idx, weights=np.full(len(T), 30.0))
    da, db = a.as_dict(), b.as_dict()
    assert db["fit_record"].pop("weight_scale") == 30.0
    assert json.dumps(da, sort_keys=True) == json.dumps(db, sort_keys=True)  # byte for byte
    assert "weight_scale" not in da["fit_record"]  # unweighted records are as before 0.92
    pa, pb = a.predict(T), b.predict(T)
    assert np.array_equal(pa, pb)
    p = N_PARAMS[kind]
    assert fit_stats(y, pa, p, time_index=idx) == fit_stats(
        y, pb, p, time_index=idx, weights=np.ones(len(y))
    )


def test_equal_weights_neutral_for_tests_and_bands():
    idx, T, y = _daily()
    m = fit_model(T, y, "3PH", time_index=idx)
    t1 = model_regression_tests(m, T, y, time_index=idx)
    t2 = model_regression_tests(m, T, y, time_index=idx, weights=np.full(len(T), 7.0))
    assert t1 == t2
    X = np.column_stack([np.ones(len(T)), T])
    assert regression_tests(X, y, names=("a", "b")) == regression_tests(
        X, y, names=("a", "b"), weights=np.ones(len(y))
    )
    pv = vars(projection_variance(m, T[:50]))
    pv1 = vars(projection_variance(m, T[:50], days=np.ones(50)))  # one-day rows: identical
    assert np.array_equal(pv.pop("g"), pv1.pop("g")) and pv == pv1
    r1 = avoided_energy_savings(m, T[:50], y[:50] * 0.9, cv_rmse=0.1, n_baseline=400, p_baseline=3)
    r2 = avoided_energy_savings(
        m, T[:50], y[:50] * 0.9, cv_rmse=0.1, n_baseline=400, p_baseline=3, days=np.ones(50)
    )
    assert r1 == r2


def test_fit_weights_contract():
    assert fit_weights(None) == (None, None)
    w, scale = fit_weights([10.0, 30.0, 50.0])
    assert scale == 30.0 and np.allclose(w, [1 / 3, 1, 5 / 3])
    assert fit_weights([4.0, 4.0]) == (None, 4.0)
    assert fit_weights([1.0, 9.0], mask=[True, False]) == (None, 1.0)
    with pytest.raises(ValueError, match="positive"):
        fit_weights([1.0, 0.0])
    with pytest.raises(ValueError, match="positive"):
        fit_weights([1.0, np.nan])


# --------------------------------------------------------------------------- what the weights mean


def test_weighted_fit_is_the_fit_of_the_bills_expanded_to_their_days():
    bs, temp = _bills()
    f = bs.energy_vs_temp(temp)
    d = f["days"].to_numpy(int)
    m = fit_model(f["oat"], f["energy"], "2P", weights=d)
    # OLS on every day of every bill (each day carrying its bill's mean temperature and energy)
    Te, ye = np.repeat(f["oat"].to_numpy(), d), np.repeat(f["energy"].to_numpy(), d)
    ref = fit_model(Te, ye, "2P")
    assert m.coeffs["base"] == pytest.approx(ref.coeffs["base"], rel=1e-10)
    assert m.coeffs["slope"] == pytest.approx(ref.coeffs["slope"], rel=1e-10)
    assert m.sse == pytest.approx(ref.sse / d.mean(), rel=1e-9)  # weights normalised to mean 1
    assert m._fit_record.weight_scale == pytest.approx(d.mean())
    # a record with weights survives the JSON round trip
    back = ChangePointModel.from_dict(json.loads(json.dumps(m.as_dict())))
    assert back._fit_record.weight_scale == m._fit_record.weight_scale
    assert np.allclose(back._fit_record.xtx_pinv, m._fit_record.xtx_pinv)


def test_weighted_stats_describe_the_bill_totals():
    bs, temp = _bills()
    f = bs.energy_vs_temp(temp)
    d = f["days"].to_numpy(float)
    m = best_model(f["oat"], f["energy"], time_index=f.index, weights=d)
    st = fit_stats(f["energy"], m.predict(f["oat"]), N_PARAMS[m.kind], weights=d)
    assert abs(st.nmbe) < 1e-9  # an intercept model reproduces the total energy exactly
    pred_total = float((m.predict(f["oat"]) * d).sum())
    assert pred_total == pytest.approx(bs.total(), rel=1e-9)
    tests = model_regression_tests(m, f["oat"].values, f["energy"].values, weights=d)
    assert tests.r2 == pytest.approx(st.r2, abs=1e-4)
    assert tests.n == st.n == len(f)


def test_projection_variance_sums_days():
    bs, temp = _bills()
    f = bs.energy_vs_temp(temp)
    d = f["days"].to_numpy(float)
    m = fit_model(f["oat"], f["energy"], "2P", weights=d)
    rep = f.iloc[-12:]
    dr = rep["days"].to_numpy(float)
    pv = projection_variance(m, rep["oat"].values, days=dr)
    rec = m._fit_record
    X = np.column_stack([np.ones(len(rep)), rep["oat"].values])
    g = (X * dr[:, None]).sum(axis=0)
    assert np.allclose(pv.g, g) and pv.m == 12
    assert pv.v_noise == pytest.approx(rec.s2 * rec.weight_scale * dr.sum())
    assert pv.v_param == pytest.approx(rec.s2 * g @ rec.xtx_pinv @ g)
    assert pv.total == pytest.approx(float((m.predict(rep["oat"].values) * dr).sum()))
    with pytest.raises(ValueError, match="days has 3 values"):
        projection_variance(m, rep["oat"].values, days=[30, 30, 30])


def test_savings_sum_days_and_the_exact_band_is_calibrated():
    """Monte Carlo on uneven bills: the days-weighted exact band covers the planted saving."""
    hits, errs = [], []
    for seed in range(120):
        bs, temp = _bills(seed=seed, save=0.1)
        f = bs.energy_vs_temp(temp)
        base, rep = f.loc[:"2022-12-31"], f.loc["2023-01-01":]
        d = base["days"].to_numpy(float)
        m = fit_model(base["oat"], base["energy"], "2P", time_index=base.index, weights=d)
        st = fit_stats(base["energy"], m.predict(base["oat"]), 2, time_index=base.index, weights=d)
        dr = rep["days"].to_numpy(float)
        r = forecast_savings(
            m, rep["oat"].values, rep["energy"].values, cv_rmse=st.cv_rmse, n_baseline=st.n,
            p_baseline=2, rho=st.rho_lag1, kernel="exact", days=dr,
        )  # fmt: skip
        assert r.measured == pytest.approx(float((rep["energy"] * dr).sum()), rel=1e-6)
        truth = 0.1 / 0.9 * r.measured  # the planted 10% of the (unobserved) baseline use
        hits.append(abs(r.savings - truth) <= r.abs_uncertainty)
        errs.append(r.savings - truth)
    assert 0.82 <= np.mean(hits) <= 0.97  # nominal 90%
    assert abs(np.mean(errs)) < 0.3 * np.std(errs)  # unbiased


def test_backcast_and_chain_sum_days():
    bs, temp = _bills(seed=5, years=("2019-01-01", "2023-12-31"))
    f = bs.energy_vs_temp(temp)
    base, inter, rep = f.loc[:"2019-12-31"], f.loc["2021-01-01":"2021-12-31"], f.loc["2023-01-01":]
    dr = rep["days"].to_numpy(float)
    mr = fit_model(rep["oat"], rep["energy"], "2P", weights=dr)
    db = base["days"].to_numpy(float)
    b = backcast_savings(
        mr, base["oat"].values, base["energy"].values, cv_rmse=0.05, n_reporting=len(rep),
        p_reporting=2, kernel="exact", days=db,
    )  # fmt: skip
    assert b.measured == pytest.approx(float((base["energy"] * db).sum()), rel=1e-6)
    assert b.projected == pytest.approx(
        float((mr.predict(base["oat"].values) * db).sum()), rel=1e-6
    )
    mi = fit_model(inter["oat"], inter["energy"], "2P", weights=inter["days"].to_numpy(float))
    c = chained_savings(
        mi, base["oat"].values, base["energy"].values, rep["oat"].values, rep["energy"].values,
        periods={
            "baseline": ["2019-01-01", "2019-12-31"],
            "intermediate": ["2021-01-01", "2021-12-31"],
            "reporting": ["2023-01-01", "2023-12-31"],
        },
        days_baseline=db, days_reporting=dr,
    )  # fmt: skip
    assert c.measured == pytest.approx(float((rep["energy"] * dr).sum()), rel=1e-6)
    assert c.uncertainty_terms["v_noise_reporting"] == pytest.approx(
        mi._fit_record.s2 * mi._fit_record.weight_scale * dr.sum()
    )


def test_select_method_weights_billing_frames():
    bs, temp = _bills(seed=2, save=0.08)
    f = bs.energy_vs_temp(temp)
    assert f.attrs["billing"] is True
    prop = select_method(
        f, baseline=["2020-01-01", "2022-12-31"], reporting=["2023-01-01", "2023-12-31"]
    )
    fc = prop.results["forecast"]
    rep = f[(f["start"] >= "2023-01-01")]
    assert fc.measured == pytest.approx(float((rep["energy"] * rep["days"]).sum()), rel=1e-6)
    assert prop.fitted["baseline"]["fit_record"]["weight_scale"] > 20
    plain = select_method(
        f.drop(columns=["days"]), baseline=["2020-01-01", "2022-12-31"],
        reporting=["2023-01-01", "2023-12-31"],
    )  # fmt: skip
    assert "weight_scale" not in plain.fitted["baseline"]["fit_record"]


def test_indicator_weights_record_scale_and_stay_neutral():
    bs, temp = _bills(seed=4)
    f = bs.energy_vs_temp(temp)
    d = f["days"].to_numpy(float)
    kw = dict(start="2022-06-01", fit_period="baseline")
    a = estimate_nre_indicator(f["oat"].values, f["energy"].values, f.index, **kw)
    same = estimate_nre_indicator(
        f["oat"].values, f["energy"].values, f.index, weights=np.full(len(f), 30.0), **kw
    )
    assert a.rate == same.rate and a.rate_se == same.rate_se
    assert a.fit.weight_scale is None and "weight_scale" not in a.fit.as_dict()
    assert same.fit.weight_scale == 30.0 and same.fit.noise_scale == 30.0
    w = estimate_nre_indicator(f["oat"].values, f["energy"].values, f.index, weights=d, **kw)
    assert w.fit.weight_scale == pytest.approx(d.mean())
    assert IndicatorFit.from_dict(w.fit.as_dict()) == w.fit
    assert abs(w.rate) < 4 * w.rate_se  # no event planted


def test_ledger_rows_expand_bills_to_days():
    bs, temp = _bills()
    f = bs.energy_vs_temp(temp).iloc[:3]
    rows = _mvform.ledger_rows(f, None)
    n = int(f["days"].sum())
    assert len(rows["index"]) == len(rows["drivers"]) == len(rows["measured"]) == n
    assert rows["index"][0] == f["start"].iloc[0] and rows["index"].is_unique
    assert float(rows["measured"].sum()) == pytest.approx(float((f["energy"] * f["days"]).sum()))
    daily = pd.DataFrame(
        {"oat": [1.0, 2.0], "energy": [3.0, 4.0]}, index=pd.date_range("2024", periods=2)
    )
    assert _mvform.row_days(daily) is None
    assert len(_mvform.ledger_rows(daily, None)["index"]) == 2
