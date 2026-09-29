"""Bill-based M&V (issue #72): selected degree-day bases, calendarization, cost and versioned bills.

Synthetic buildings with known heating and cooling bases (58 °F and 68 °F), bills of odd lengths
(28--35 days, a mid-month start, an estimated read), a known saving, and the versioned
``camber mv`` verbs on a billing entry.
"""

import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.cli import main  # noqa: E402
from camber.config import load_config, run_config, run_mv_config  # noqa: E402
from camber.mandv import _mvform  # noqa: E402
from camber.mandv.basetemp import (  # noqa: E402
    BaseSearch,
    BillingDegreeDayModel,
    bill_degree_days,
    compare_models,
    fit_bill_degree_day,
    rows_at_bases,
    select_bases,
)
from camber.mandv.billing import BillingSeries, calendarize  # noqa: E402
from camber.mandv.rebaseline import mv_model_from_dict  # noqa: E402
from camber.mandv.stats import fit_stats, model_regression_tests  # noqa: E402

HB, CB = 58.0, 68.0


def _building(*, seed=0, a=5.0, b=8.0, noise=5.0, years=("2019-01-02", "2021-12-31"),
              save=0.0, save_from=None, estimated_at=None):  # fmt: skip
    """Daily truth ``100 + a*HDD(58) + b*CDD(68)`` cut into 28--35 day bills from a mid-month
    start; ``estimated_at`` marks one bill as an estimated read."""
    rng = np.random.default_rng(seed)
    days = pd.date_range(*years, freq="D")
    doy = days.dayofyear.to_numpy()
    T = 55 - 22 * np.cos(2 * np.pi * (doy - 15) / 365.25) + rng.normal(0, 7, len(days))
    use = 100 + a * np.clip(HB - T, 0, None) + b * np.clip(T - CB, 0, None)
    use = use + rng.normal(0, noise, len(days))
    if save_from is not None:
        use = np.where(days >= save_from, use * (1 - save), use)
    rows, i, k = [], 13, 0  # the first bill starts mid-month
    while i < len(days):
        n = int(rng.integers(28, 36))
        j = min(i + n, len(days))
        if save_from is not None:
            cut = int(np.argmax(days >= save_from))
            if i < cut < j:
                j = cut
        rows.append(
            {
                "start": days[i].date(),
                "end": days[j - 1].date(),
                "kwh": round(float(use[i:j].sum()), 2),
                "cost": round(float(use[i:j].sum()) * (0.12 + 0.01 * (k % 3)), 2),
                "estimated": "E" if k == estimated_at else "A",
            }
        )
        i, k = j, k + 1
    return pd.DataFrame(rows), pd.Series(T, index=days)


def _frame(df, temp, **kw):
    bs = BillingSeries.from_frame(df, energy="kwh")
    return bs, bs.energy_vs_temp(temp, **kw)


# --------------------------------------------------------------------------- base selection


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_known_heating_and_cooling_bases_are_recovered_within_one_step(seed):
    df, temp = _building(seed=seed)
    _bs, fr = _frame(df, temp)
    sel = select_bases(fr, temp)
    assert sel.kind == "DD-HC"
    assert abs(sel.heating_base_f - HB) <= 1.0 and abs(sel.cooling_base_f - CB) <= 1.0
    lo, hi = sel.heating_range
    assert lo <= sel.heating_base_f <= hi and lo <= HB <= hi
    assert sel.cooling_range[0] <= CB <= sel.cooling_range[1]
    assert not sel.flat and not sel.at_edge
    # the profile: one row per candidate base, with R2 next to CV(RMSE), NMBE and BIC
    prof = sel.profiles["heating"]
    assert len(prof) == 31 and {"r2", "adj_r2", "cv_rmse", "nmbe", "bic", "sse"} <= set(prof[18])
    best = min((r for r in prof if r["valid"]), key=lambda r: r["sse"])
    assert best["base_f"] == sel.heating_base_f
    assert best["r2"] == max(r["r2"] for r in prof if r["valid"])  # same p: max R2 = min SSE
    # DD-C on a building that mostly heats has a negative slope everywhere: refused
    c = {r["kind"]: r for r in sel.candidates}
    assert c["DD-HC"]["selected"] and c["DD-HC"]["p"] == 5


def test_data_driven_grid_and_search_validation():
    df, temp = _building(seed=3)
    _bs, fr = _frame(df, temp)
    sel = select_bases(fr, temp, search=BaseSearch(grid="data", step=1.0))
    assert sel.grid["kind"] == "data" and sel.grid["heating"][0] < 40
    assert abs(sel.heating_base_f - HB) <= 1 and abs(sel.cooling_base_f - CB) <= 1
    for bad in ({"heating": [70, 40]}, {"step": 0}, {"grid": "x"}, {"kinds": ["DD-X"]},
                {"flat_share": 0}, {"tolerance": -1}, {"r2_min": 1.5}, {"nope": 1}):  # fmt: skip
        with pytest.raises(ValueError):
            BaseSearch.from_dict(bad)
    assert BaseSearch.from_dict({"kinds": ["DD-H"]}).kinds == ("DD-H",)


def test_a_flat_profile_is_flagged():
    df, temp = _building(seed=4, a=0.0, b=0.0, noise=10.0)  # no weather dependence at all
    _bs, fr = _frame(df, temp)
    sel = select_bases(fr, temp)
    assert sel.flat
    assert any("poorly determined" in c for c in sel.caveats)


def test_a_base_at_the_edge_of_the_range_is_a_caveat():
    df, temp = _building(seed=5)
    _bs, fr = _frame(df, temp)
    sel = select_bases(fr, temp, search=BaseSearch(heating=(40, 52), cooling=(60, 80)))
    assert "heating" in sel.at_edge and sel.heating_base_f == 52.0
    assert any("edge of the search range" in c for c in sel.caveats)


def test_every_kind_refused_declines():
    df, temp = _building(seed=6, a=-1.5, b=0.0)  # use falls as it gets colder
    _bs, fr = _frame(df, temp)
    sel = select_bases(fr, temp, search=BaseSearch(kinds=("DD-H",)))
    assert sel.model is None and sel.declined_reason
    assert "DD-H: a slope has the wrong" in sel.declined_reason
    assert sel.as_dict()["declined_reason"] == sel.declined_reason


def test_degree_days_from_each_day_beat_the_bill_mean_in_shoulder_months():
    better = []
    for seed in range(6):
        df, temp = _building(seed=seed, noise=10.0)
        _bs, fr = _frame(df, temp)
        mean = fr["oat"].to_numpy()
        y, d = fr["energy"].to_numpy(), fr["days"].to_numpy()
        shoulder = (np.abs(mean - HB) <= 8) | (np.abs(mean - CB) <= 8)
        D = np.column_stack(
            [
                bill_degree_days(temp, fr["start"], fr["end"], [HB], leg="heating")[:, 0],
                bill_degree_days(temp, fr["start"], fr["end"], [CB], leg="cooling")[:, 0],
            ]
        )
        M = np.column_stack([np.clip(HB - mean, 0, None), np.clip(mean - CB, 0, None)])
        err = []
        for X in (D, M):
            m = fit_bill_degree_day(
                X, y, kind="DD-HC", heating_base_f=HB, cooling_base_f=CB, days=d
            )
            err.append(float(np.sqrt(np.mean((m.predict(X) - y)[shoulder] ** 2))))
        better.append(err[0] < 0.6 * err[1])
    assert all(better)


def test_bill_degree_days_match_period_weather():
    df, temp = _building(seed=7)
    bs, fr = _frame(df, temp, heating_base_f=57.0, cooling_base_f=69.0)
    h = bill_degree_days(temp, fr["start"], fr["end"], [57.0], leg="heating")[:, 0]
    c = bill_degree_days(temp, fr["start"], fr["end"], [69.0], leg="cooling")[:, 0]
    assert np.allclose(h * fr["days"], fr["hdd"]) and np.allclose(c * fr["days"], fr["cdd"])
    assert fr.attrs["heating_base_f"] == 57.0 and fr.attrs["cooling_base_f"] == 69.0
    hourly = temp.resample("1h").ffill()
    hh = bill_degree_days(hourly, fr["start"], fr["end"], [57.0], leg="heating")
    assert hh.shape == (len(fr), 1)
    with pytest.raises(ValueError):
        bill_degree_days(temp, fr["start"], fr["end"], [57.0], leg="both")
    empty = bill_degree_days(pd.Series(dtype=float), fr["start"], fr["end"], [57.0], leg="heating")
    assert np.isnan(empty).all()


def test_r2_matches_fit_stats_and_adjusted_r2_counts_the_bases():
    df, temp = _building(seed=8)
    _bs, fr = _frame(df, temp, heating_base_f=HB, cooling_base_f=CB)
    X = rows_at_bases(fr, "DD-HC", HB, CB)
    d = fr["days"].to_numpy(float)
    m = fit_bill_degree_day(X, fr["energy"], kind="DD-HC", heating_base_f=HB, cooling_base_f=CB,
                            days=d)  # fmt: skip
    assert m.p == 5 and _mvform.n_params(m) == 5
    st = fit_stats(fr["energy"].values, m.predict(X), 5, weights=d)
    rows = compare_models(fr, [("dd", m, fr)])
    assert rows[0]["r2"] == st.r2 and rows[0]["adj_r2"] == st.adj_r2
    n = st.n
    assert st.adj_r2 == pytest.approx(1 - (1 - st.r2) * (n - 1) / (n - 5), abs=1e-4)
    # the regression tests count the two fitted bases as parameters (conditional on them)
    t = model_regression_tests(m, X, fr["energy"].values, weights=d)
    assert t.n_params == 5 and t.conditional_on_change_points
    assert t.names == ("base", "heating_slope", "cooling_slope")
    # a fixed-base model does not count its bases
    mf = fit_bill_degree_day(X, fr["energy"], kind="DD-HC", heating_base_f=HB, cooling_base_f=CB,
                             days=d, bases_fitted=False)  # fmt: skip
    assert mf.p == 3


def test_model_round_trips_and_reads_its_own_rows():
    df, temp = _building(seed=9)
    _bs, fr = _frame(df, temp, heating_base_f=HB, cooling_base_f=CB)
    X = rows_at_bases(fr, "DD-HC", HB, CB)
    m = fit_bill_degree_day(X, fr["energy"], kind="DD-HC", heating_base_f=HB, cooling_base_f=CB,
                            days=fr["days"], time_index=fr.index)  # fmt: skip
    m2 = mv_model_from_dict(json.loads(json.dumps(m.as_dict())))
    assert isinstance(m2, BillingDegreeDayModel)
    assert np.array_equal(m.predict(X), m2.predict(X))
    assert m2.change_points == (HB, CB) and m2.columns == ("hdd", "cdd")
    assert m.coverage(X).tier == "in_range"
    assert np.allclose(_mvform.design_rows(fr, m), X)
    assert m.rows_from_temps([50.0, 70.0]).tolist() == [[8.0, 0.0], [0.0, 2.0]]
    _bs, other = _frame(df, temp, heating_base_f=60.0, cooling_base_f=CB)
    with pytest.raises(ValueError, match="base is 58"):
        _mvform.design_rows(other, m)
    h = fit_bill_degree_day(X[:, 0], fr["energy"], kind="DD-H", heating_base_f=HB, days=fr["days"])
    assert np.ndim(h.predict(X[:3, 0])) == 1 and h.coeffs.keys() == {"base", "heating_slope"}
    with pytest.raises(ValueError):
        fit_bill_degree_day(X, fr["energy"], kind="DD-X")
    with pytest.raises(ValueError, match="needs its cooling base"):
        fit_bill_degree_day(X, fr["energy"], kind="DD-HC", heating_base_f=HB)
    with pytest.raises(ValueError, match="more than 5"):
        fit_bill_degree_day(X[:4], fr["energy"][:4], kind="DD-HC", heating_base_f=HB,
                            cooling_base_f=CB)  # fmt: skip


# --------------------------------------------------------------------------- calendarization


def test_calendarization_prorates_by_day_and_conserves_energy():
    df, temp = _building(seed=10, estimated_at=5)
    df = df.assign(estimated=df["estimated"].eq("E"))
    bs = BillingSeries.from_frame(df, energy="kwh", estimated="estimated", cost="cost")
    bs = BillingSeries(bs.frame.reset_index(drop=True), units="kWh").merge_estimated()
    cal = bs.calendarize(temp)
    m = cal.months
    assert not cal.declined and cal.gaps == [] and cal.overlaps == []
    assert m["energy"].sum() == pytest.approx(bs.frame["energy"].sum())
    assert m["cost"].sum() == pytest.approx(bs.frame["cost"].sum())
    assert not m["complete"].iloc[0]  # the first bill starts mid-month
    assert m["complete"].iloc[1:-1].all()
    assert m["estimated"].sum() >= 1  # the merged estimate's months are flagged
    # the first full month: its days from the bills that cover it, at each bill's per-day rate
    month = m.index[1]
    f = bs.frame
    exp = 0.0
    for r in f.itertuples(index=False):
        a = max(r.start, month)
        b = min(r.end, month + pd.offsets.MonthBegin(1))
        if b > a:
            exp += r.energy / r.days * (b - a).days
    assert m.loc[month, "energy"] == pytest.approx(exp)
    ann = cal.annual()
    assert [a["year"] for a in ann] == [2020, 2021]  # 2019's first bill starts mid-January
    assert ann[0]["days"] == 366 and ann[0]["hdd"] > 0
    t = cal.total("2020-02-01", "2020-03-31")
    assert t["months"] == ["2020-02", "2020-03"] and t["days"] == 60
    assert cal.total("2019-01-01", "2019-03-31") is None  # January 2019 incomplete
    d = cal.as_dict()
    assert d["months"][1]["complete"] is True and "Portfolio Manager" in d["method"]


def test_calendarization_declines_on_a_gap_or_an_overlap():
    f = pd.DataFrame(
        {
            "start": pd.to_datetime(["2023-01-01", "2023-02-01", "2023-03-10"]),
            "end": pd.to_datetime(["2023-02-01", "2023-03-01", "2023-04-01"]),
            "energy": [31.0, 28.0, 22.0],
        }
    )
    cal = calendarize(f)
    assert cal.gaps == [{"start": "2023-03-01", "end": "2023-03-09", "days": 9}]
    assert cal.declined and "gap" in cal.declined_reason and cal.annual() == []
    assert cal.total("2023-01-01", "2023-02-28") is None
    assert not calendarize(f, max_gap_days=9).declined
    f.loc[2, "start"] = pd.Timestamp("2023-02-20")
    cal = calendarize(f)
    assert cal.overlaps and cal.overlaps[0]["days"] == 9 and "overlap" in cal.declined_reason
    bad = f.copy()
    bad.loc[0, "end"] = bad.loc[0, "start"]
    with pytest.raises(ValueError):
        calendarize(bad)


# --------------------------------------------------------------------------- the config path


def _write(tmp_path, **kw):
    df, temp = _building(**kw)
    df.to_csv(tmp_path / "elec.csv", index=False)
    pd.DataFrame({"timestamp": temp.index, "oat": np.round(temp.to_numpy(), 2)}).to_csv(
        tmp_path / "oat.csv", index=False
    )
    return df


def _cfg(**entry):
    e = {
        "bills": {"file": "elec.csv", "energy": "kwh", "units": "kWh", "estimated": "estimated"},
        "name": "Elec",
        "period": ["2019-01-01", "2020-12-31"],
        "reporting_period": ["2021-01-01", "2021-12-31"],
        "method": "forecast",
        "kernel": "exact",
    }
    e.update(entry)
    return {"site": "Demo", "shared_oat": {"file": "oat.csv"}, "mv": [e]}


def _find(res, rule):
    return [f for f in res.findings if f.rule == rule]


def test_numeric_base_adds_no_keys(tmp_path):
    _write(tmp_path, estimated_at=7)
    (b,) = _find(run_config(_cfg(), base_dir=str(tmp_path)), "mv_baseline")
    (b65,) = _find(run_config(_cfg(base_f=65), base_dir=str(tmp_path)), "mv_baseline")
    assert b.as_dict() == b65.as_dict()
    for key in ("base_selection", "model_comparison", "heating_base_f", "adj_r2",
                "calendarized", "unit_cost"):  # fmt: skip
        assert key not in b.metrics


def test_auto_bases_recover_a_known_saving_on_odd_bills(tmp_path):
    _write(tmp_path, save=0.12, save_from="2021-01-01", estimated_at=7,
           years=("2019-01-02", "2021-12-31"))  # fmt: skip
    cfg = _cfg(base_f="auto", calendarize=True, avoided_cost="bills")
    cfg["mv"][0]["bills"]["cost"] = "cost"
    res = run_config(cfg, base_dir=str(tmp_path))
    (b,) = _find(res, "mv_baseline")
    m = b.metrics
    assert m["base_f"] == "auto" and m["model"] == "DD-HC"
    assert abs(m["heating_base_f"] - HB) <= 1 and abs(m["cooling_base_f"] - CB) <= 1
    assert m["n_params"] == 5 and m["adj_r2"] < m["r2"]
    assert m["estimated_merged"] == 1
    assert "heating base" in b.summary and "selected from the bills" in b.summary
    sel = m["base_selection"]
    assert sel["kind"] == "DD-HC" and sel["heating_range"][0] <= HB <= sel["heating_range"][1]
    labels = [r["label"] for r in m["model_comparison"]]
    assert labels[:5] == ["2P", "3PC", "3PH", "4P", "5P"]
    assert "DD-HC fitted bases" in labels and "DD-HC fixed 65 F" in labels
    for r in m["model_comparison"]:
        assert {"r2", "adj_r2", "cv_rmse", "nmbe", "bic", "p"} <= set(r)
    assert sum(r["selected"] for r in m["model_comparison"]) == 1
    fx = m["fixed_base"]
    assert fx["base_f"] == 65.0 and fx["hdd_total"] > m["hdd_total"]  # 65 F counts more HDD
    assert fx["fitted"]["bic"] < fx["model"]["bic"]  # the fitted bases fit better
    assert m["calendarized"]["annual"] and m["unit_cost"]["min"] >= 0.119
    (s,) = _find(res, "mv_savings")
    sm = s.metrics
    assert sm["savings_pct"] == pytest.approx(0.12, abs=0.02)
    assert sm["avoided_cost"] > 0 and "own rate" in sm["avoided_cost_basis"]
    assert sm["avoided_cost_rate"] == pytest.approx(0.13, abs=0.011)
    assert sm["avoided_cost_uncertainty"] > 0
    # a stated rate
    cfg = _cfg(base_f="auto", avoided_cost={"rate": 0.2})
    (s2,) = _find(run_config(cfg, base_dir=str(tmp_path)), "mv_savings")
    assert s2.metrics["avoided_cost"] == pytest.approx(0.2 * s2.metrics["avoided_energy"], rel=1e-3)


def test_auto_bases_with_other_methods(tmp_path):
    _write(tmp_path, save=0.1, save_from="2021-01-01")
    ny = list(55 - 22 * np.cos(2 * np.pi * (np.arange(1, 366) - 15) / 365.25))
    for kw in (
        dict(method="backcast"),
        dict(method="standard_conditions", normal_year=ny, validity="both"),
        dict(method="chaining", intermediate_period=["2020-01-01", "2020-12-31"],
             period=["2019-01-01", "2019-12-31"]),
    ):  # fmt: skip
        (s,) = _find(run_config(_cfg(base_f="auto", **kw), base_dir=str(tmp_path)), "mv_savings")
        assert not s.metrics["declined"], s.summary
        assert s.metrics["savings_pct"] == pytest.approx(0.1, abs=0.03), kw
    res = run_config(_cfg(base_f="auto", method="auto"), base_dir=str(tmp_path))
    (p,) = _find(res, "mv_method_proposal")
    # 0.95 (#74): the degree-day model at the selected bases is a candidate, criterion stated
    assert p.metrics["degree_day_candidate"]["kind"] == "DD-HC"
    assert "adjusted R²" in p.metrics["model_criterion"]
    assert any(c.startswith("candidate models:") and "§6.4.1" in c for c in p.caveats)


def test_low_r2_is_a_caveat_and_flat_bases_are_warned(tmp_path):
    _write(tmp_path, a=0.3, b=0.0, noise=25.0)
    (b,) = _find(run_config(_cfg(base_f="auto"), base_dir=str(tmp_path)), "mv_baseline")
    text = " ".join(b.caveats)
    assert b.metrics["r2"] < 0.5
    assert "weather explains little" in text and "§6.4.1" in text
    assert b.metrics["base_selection"]["flat"] and "poorly determined" in text


def test_config_errors(tmp_path):
    _write(tmp_path)
    for kw, msg in (
        (dict(base_search={"step": 1}), "needs base_f"),
        (dict(base_f="warm"), "number or"),
        (dict(base_f="auto", base_search={"step": -1}), "step"),
        (dict(avoided_cost="yes"), "avoided_cost"),
        (dict(avoided_cost="bills"), "bills.cost"),
        (dict(calendarize="monthly"), "calendarize"),
    ):
        with pytest.raises(ValueError, match=msg):
            run_config(_cfg(**kw), base_dir=str(tmp_path))


# --------------------------------------------------------------------------- versioned bills


def _mv(argv, capsys):
    rc = main(argv)
    return rc, capsys.readouterr().out


def test_versioned_billing_baselines(tmp_path, capsys):
    df = _write(tmp_path, save=0.1, save_from="2021-01-01", years=("2019-01-02", "2023-06-30"))
    cfg = _cfg(base_f="auto", avoided_cost="bills", calendarize=True,
               reporting_period=["2021-01-01", "2023-06-30"],
               rebaseline={"events": [{"date": "2022-01-01", "description": "wing added",
                                       "magnitude": "major", "id": "E1"}]})  # fmt: skip
    cfg["mv"][0]["bills"]["cost"] = "cost"
    cfg["mv_store"] = "store.json"
    path = str(tmp_path / "cfg.json")
    json.dump(cfg, open(path, "w"))
    store = tmp_path / "store.json"

    rc, out = _mv(["mv", "freeze", path, "--reason", "initial", "--by", "ana", "--apply"], capsys)
    assert rc == 0 and "Elec/mv_bills: froze v1" in out and "DD-HC" in out
    doc = json.load(open(store))
    prov = doc["mv_baselines"][0]["provenance"]
    bill = prov["billing"]
    assert bill["dd_kind"] == "DD-HC" and abs(bill["heating_base_f"] - HB) <= 1
    assert bill["units"] == "kWh" and len(bill["bills"]) == len(df)
    assert {"start", "end", "days", "energy", "estimated", "cost"} <= set(bill["bills"][0])
    assert bill["weather_basis"]["oat_source"] == "shared_oat"
    assert prov["model"]["type"] == "BillingDegreeDayModel"

    # the run path reads the frozen version, at its bases; the event cuts the saving
    fs = run_mv_config(load_config(path), base_dir=str(tmp_path))
    (b,) = [f for f in fs if f.rule == "mv_baseline"]
    assert b.metrics["baseline_version"] == "v1" and b.metrics["billing"]
    assert b.metrics["heating_base_f"] == bill["heating_base_f"] and b.metrics["n_bills"] >= 20
    (s,) = [f for f in fs if f.rule == "mv_savings"]
    assert s.metrics["partial"] and s.metrics["savings_pct"] == pytest.approx(0.1, abs=0.03)
    assert s.metrics["reporting_period"][1] == "2021-12-31" and s.metrics["avoided_cost"] > 0
    assert any(f.rule == "mv_trigger" and f.metrics["id"] == "T2" for f in fs)

    # propose: 0.95 (#74) searches a window of whole bills after the event
    pj = str(tmp_path / "prop.json")
    rc, out = _mv(["mv", "propose", path, "--json", pj], capsys)
    assert "proposal: rebaseline" in out and "new window" in out
    win = json.load(open(pj))["meters"][0]["rebaseline"]["window"]
    assert win["window"][0] >= "2022-01-31" and win["window"][1] == "2023-06-30"
    assert win["n_bills"] >= 9 and win["bases"]["dd_kind"] == "DD-HC"
    rc, out = _mv(["mv", "rebaseline", path, "--equip", "Elec", "--by", "ana", "--reason", "wing"],
                  capsys)  # fmt: skip
    assert f"window {win['window'][0]}..{win['window'][1]}" in out and "dry run" in out

    # adjust: an engineering NRA on v1
    spec = str(tmp_path / "adj.json")
    nra = {"kind": "nra", "method": "engineering", "start": "2021-06-01", "amount": 300.0,
           "se": 30.0, "reason": "rental", "evidence": "invoice"}  # fmt: skip
    json.dump([nra], open(spec, "w"))
    rc, out = _mv(["mv", "adjust", path, "--equip", "Elec", "--spec", spec, "--by", "ana",
                   "--reason", "rental", "--apply"], capsys)  # fmt: skip
    assert rc == 0 and "adjusted v1" in out

    # rebaseline over a named window: the bases are selected afresh and the move is recorded
    rc, out = _mv(["mv", "rebaseline", path, "--equip", "Elec", "--by", "ana", "--reason",
                   "wing", "--period", "2022-01-20", "2023-06-30", "--apply"], capsys)  # fmt: skip
    assert rc == 0 and "rebaselined v2" in out, out
    doc = json.load(open(store))
    rec = doc["mv_baselines"][0]
    assert rec["provenance"]["version"] == 2 and rec["provenance"]["billing"]["dd_kind"]
    moved = rec["provenance"].get("bases_changed")
    if moved:
        assert "degree-day bases change" in out

    # report: bills, bases per version, avoided cost and calendarized months
    html, rj = str(tmp_path / "r.html"), str(tmp_path / "r.json")
    rc, out = _mv(["mv", "report", path, "--out", html, "--json", rj], capsys)
    assert rc == 0
    m = json.load(open(rj))["meters"][0]
    assert [v["version"] for v in m["versions"]] == ["v1", "v2"]
    assert [b["version"] for b in m["billing"]["bases"]] == ["v1", "v2"]
    assert m["billing"]["avoided_cost"][0]["avoided_cost"] > 0
    assert m["billing"]["calendarized"]["months"]
    assert m["links"][0]["savings_pct"] == pytest.approx(0.1, abs=0.03)
    assert m["adjusted"][0] is not None
    page = open(html).read()
    assert "Calendarized months" in page and "Heating base" in page
    rc, out = _mv(["mv", "list", path], capsys)
    assert "Elec/mv_bills: 2 version(s)" in out


def test_versioned_numeric_base_and_refusals(tmp_path, capsys):
    _write(tmp_path, years=("2019-01-02", "2021-12-31"))
    cfg = _cfg()
    cfg["mv_store"] = "store.json"
    path = str(tmp_path / "cfg.json")
    json.dump(cfg, open(path, "w"))
    rc, out = _mv(["mv", "freeze", path, "--reason", "r", "--apply"], capsys)
    assert "froze v1" in out
    prov = json.load(open(tmp_path / "store.json"))["mv_baselines"][0]["provenance"]
    assert prov["billing"]["base_f"] == 65.0 and "dd_kind" not in prov["billing"]
    assert prov["model"]["type"] == "ChangePointModel"
    fs = run_mv_config(load_config(path), base_dir=str(tmp_path))
    (s,) = [f for f in fs if f.rule == "mv_savings"]
    assert s.metrics["baseline_version"] == "v1" and not s.metrics["declined"]
    # a short window is refused
    cfg["mv"][0]["period"] = ["2019-01-01", "2019-06-30"]
    cfg["mv_store"] = "store2.json"
    json.dump(cfg, open(path, "w"))
    rc, out = _mv(["mv", "freeze", path, "--reason", "r", "--apply"], capsys)
    assert "not freezed" in out or "not frozen" in out or "allow-short" in out
