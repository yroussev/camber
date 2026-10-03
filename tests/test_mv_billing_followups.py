"""Bill-based M&V follow-ups (issue #74): the rebaseline window search over bills, the step test
on bills (trigger T1, opt-in) and the degree-day model in the SEP method proposal.

Synthetic buildings with known heating and cooling bases (58 / 68 °F) cut into 28--35 day bills.
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
from camber.mandv.billing import BillingSeries  # noqa: E402
from camber.mandv.billsteps import (  # noqa: E402
    DEFAULT_MIN_RUN,
    DEFAULT_THRESHOLD,
    BillStepRule,
    bill_steps,
    scan_statistic,
)
from camber.mandv.billwindow import new_bill_window  # noqa: E402
from camber.mandv.methods import select_method  # noqa: E402
from camber.mandv.rebaseline import (  # noqa: E402
    DeclaredChange,
    RebaselinePolicy,
    assess_triggers,
)
from camber.mandv.stats import fit_stats, model_regression_tests, sep_validity  # noqa: E402

HB, CB = 58.0, 68.0


def _building(*, seed=0, years=("2019-01-02", "2023-12-31"), noise=5.0, steps=(), drop=()):
    """Daily truth ``100 + 5*HDD(58) + 8*CDD(68)``, times ``(1 + f)`` from each ``(date, f)`` in
    ``steps``, cut into 28--35 day bills from a mid-month start; bills whose index is in ``drop``
    are left out (a gap)."""
    rng = np.random.default_rng(seed)
    days = pd.date_range(*years, freq="D")
    doy = days.dayofyear.to_numpy()
    T = 55 - 22 * np.cos(2 * np.pi * (doy - 15) / 365.25) + rng.normal(0, 7, len(days))
    use = 100 + 5.0 * np.clip(HB - T, 0, None) + 8.0 * np.clip(T - CB, 0, None)
    use = use + rng.normal(0, noise, len(days))
    for when, f in steps:
        use = np.where(days >= pd.Timestamp(when), use * (1 + f), use)
    rows, i, k = [], 13, 0
    while i < len(days):
        n = int(rng.integers(28, 36))
        j = min(i + n, len(days))
        if k not in drop:
            rows.append({"start": days[i].date(), "end": days[j - 1].date(),
                         "kwh": round(float(use[i:j].sum()), 2)})  # fmt: skip
        i, k = j, k + 1
    return pd.DataFrame(rows), pd.Series(T, index=days)


def _bills(df, temp, **kw):
    bs = BillingSeries.from_frame(df, energy="kwh")
    return bs, bs.energy_vs_temp(temp, **kw)


def _fit(frame, period):
    """What billing_fit_version returns, for a change-point fit on the bills inside ``period``."""
    from camber.mvbilling import slice_bills

    sub = slice_bills(frame, period)
    if len(sub) < 9:
        raise ValueError(f"only {len(sub)} bills")
    model = _mvform.fit(sub)
    X, y, d = _mvform.design_rows(sub, model), sub["energy"].to_numpy(float), sub["days"]
    st = fit_stats(y, model.predict(X), _mvform.n_params(model), cv_rmse_max=0.15,
                   time_index=sub.index, weights=d.to_numpy(float))  # fmt: skip
    tests = model_regression_tests(model, X, y, time_index=sub.index, weights=d.to_numpy(float))
    return {"model": model, "st": st, "tests": tests, "sep": sep_validity(tests), "sub": sub,
            "frame": frame, "billing": {"base_f": 65.0}}  # fmt: skip


# --------------------------------------------------------------------------- 1. window search


def test_window_search_takes_the_latest_full_service_year_of_whole_bills():
    df, temp = _building()
    _bs, fr = _bills(df, temp)
    pol = RebaselinePolicy.from_entry({"settle_days": 30})
    w = new_bill_window(fr, after="2021-06-01", fit=lambda p: _fit(fr, p), policy=pol)
    assert w.ok and w.valid and w.tried == 1
    s, e = pd.Timestamp(w.window[0]), pd.Timestamp(w.window[1])
    # the window's edges are bill edges: its last bill is the last one, its first starts a bill
    assert e == fr["end"].max() - pd.Timedelta(days=1)
    assert s in set(pd.DatetimeIndex(fr["start"]))
    span = (e - s).days + 1
    assert 365 <= span < 365 + 36  # the shortest run of whole bills covering a year
    assert w.n_bills == len(fr[(fr["start"] >= s) & (fr["end"] <= e + pd.Timedelta(days=1))])
    assert w.missing_frac == 0.0 and w.fit_frame_sha256 and "latest" in w.ranking
    d = w.as_dict()
    assert d["window"] == w.window and "_fit" not in d and d["n_bills"] == w.n_bills


def test_window_search_rules_settle_ecm_events_min_bills_and_gaps():
    df, temp = _building()
    _bs, fr = _bills(df, temp)
    last = fr["end"].max() - pd.Timedelta(days=1)
    fit = lambda p: _fit(fr, p)  # noqa: E731
    # too soon after the trigger: declined with the days still needed
    pol = RebaselinePolicy.from_entry({"settle_days": 60})
    w = new_bill_window(fr, after=str((last - pd.Timedelta(days=200)).date()), fit=fit, policy=pol)
    assert not w.ok and w.days_needed == 364 - 200 + 60 and "more days" in w.declined_reason
    # an ECM installed late: the window must end before its installation window
    pol = RebaselinePolicy.from_entry({"settle_days": 30, "ecm_dates": ["2023-09-01"]})
    w = new_bill_window(fr, after="2020-06-01", fit=fit, policy=pol)
    assert w.ok and pd.Timestamp(w.window[1]) < pd.Timestamp("2023-08-02")
    # a declared event in the same place is respected the same way
    pol = RebaselinePolicy.from_entry({"settle_days": 30})
    ev = (DeclaredChange("2023-09-01", "new tenant", "minor"),)
    w2 = new_bill_window(fr, after="2020-06-01", fit=fit, policy=pol, events=ev)
    assert w2.ok and w2.window == w.window
    # more bills required than a year holds: no window, and the reasons say why
    w = new_bill_window(fr, after="2020-06-01", fit=fit, policy=pol, min_bills=14)
    assert not w.ok and "bills (< 14)" in w.declined_reason
    # a gap of two missing bills near the end: the latest windows miss too many days
    df2, temp2 = _building(drop=(len(df) - 4, len(df) - 3))
    _bs, fr2 = _bills(df2, temp2)
    w = new_bill_window(fr2, after="2020-06-01", fit=lambda p: _fit(fr2, p), policy=pol)
    assert w.ok and any("unserved" in r for r in w.reasons)
    gap_start = pd.Timestamp(fr2["end"].iloc[-3])
    assert pd.Timestamp(w.window[1]) < gap_start
    assert w.missing_frac <= 0.10


def test_window_search_steps_back_past_a_window_that_fails_its_fit():
    df, temp = _building()
    _bs, fr = _bills(df, temp)
    pol = RebaselinePolicy.from_entry({"settle_days": 30})
    calls = []

    def fit(p):
        calls.append(p)
        if len(calls) < 3:
            raise ValueError("no change-point model could be fitted")
        return _fit(fr, p)

    w = new_bill_window(fr, after="2020-06-01", fit=fit, policy=pol)
    assert w.ok and w.tried == 3 and len(w.reasons) == 2
    ends = [pd.Timestamp(c[1]) for c in calls]
    assert ends[0] > ends[1] > ends[2]  # one bill back each time


def _cfg(**entry):
    e = {
        "bills": {"file": "elec.csv", "energy": "kwh", "units": "kWh"},
        "name": "Elec",
        "period": ["2019-01-01", "2020-12-31"],
        "reporting_period": ["2021-01-01", "2023-12-31"],
        "method": "forecast",
    }
    e.update(entry)
    return {"site": "Demo", "shared_oat": {"file": "oat.csv"}, "mv": [e], "mv_store": "store.json"}


def _write(tmp_path, cfg, **kw):
    df, temp = _building(**kw)
    df.to_csv(tmp_path / "elec.csv", index=False)
    pd.DataFrame({"timestamp": temp.index, "oat": np.round(temp.to_numpy(), 2)}).to_csv(
        tmp_path / "oat.csv", index=False
    )
    path = str(tmp_path / "cfg.json")
    json.dump(cfg, open(path, "w"))
    return path


def _mv(argv, capsys):
    rc = main(argv)
    return rc, capsys.readouterr().out


def test_propose_and_rebaseline_without_a_period(tmp_path, capsys):
    ev = {"events": [{"date": "2022-01-01", "description": "wing", "magnitude": "major"}]}
    path = _write(tmp_path, _cfg(base_f="auto", rebaseline=ev), steps=(("2022-01-01", 0.25),))
    rc, out = _mv(["mv", "freeze", path, "--reason", "initial", "--by", "ana", "--apply"], capsys)
    assert rc == 0 and "froze v1" in out
    pj = str(tmp_path / "p.json")
    rc, out = _mv(["mv", "propose", path, "--json", pj], capsys)
    assert "proposal: rebaseline" in out
    row = json.load(open(pj))["meters"][0]
    win = row["rebaseline"]["window"]
    assert win["window"][0] >= "2022-01-31" and win["n_bills"] >= 11
    assert win["bases"]["base_f"] == "auto" and win["bases"]["dd_kind"]
    assert any(
        "latest window that meets every rule wins" in c for c in row["rebaseline"]["caveats"]
    )
    # the SEP proposal on the version's bills offers the degree-day model too
    assert "degree-day model" in " ".join(row["method_proposal"]["caveats"])
    # rebaseline from the proposal: its window and model exactly, weighted monthly statistics
    rc, out = _mv(["mv", "rebaseline", path, "--equip", "Elec", "--by", "ana", "--reason", "wing",
                   "--from-proposal", pj, "--apply"], capsys)  # fmt: skip
    assert rc == 0 and "rebaselined v2" in out, out
    rec = json.load(open(tmp_path / "store.json"))["mv_baselines"][0]
    prov = rec["provenance"]
    assert prov["period"] == win["window"] and prov["from_proposal"] is True
    assert prov["model"] == win["model"] and prov["fit_frame_sha256"] == win["fit_frame_sha256"]
    assert prov["fit_stats"]["accept"] and prov["billing"]["dd_kind"] == win["bases"]["dd_kind"]
    # the run path measures the reporting bills after v2 against it
    fs = run_mv_config(load_config(path), base_dir=str(tmp_path))
    assert any(f.rule == "mv_baseline" for f in fs)


def test_rebaseline_without_a_period_declines_with_the_days_needed(tmp_path, capsys):
    ev = {"events": [{"date": "2023-06-01", "description": "wing", "magnitude": "major"}]}
    path = _write(tmp_path, _cfg(rebaseline=ev))
    _mv(["mv", "freeze", path, "--reason", "initial", "--by", "ana", "--apply"], capsys)
    rc, out = _mv(["mv", "rebaseline", path, "--equip", "Elec", "--by", "ana", "--reason", "w"],
                  capsys)  # fmt: skip
    assert "more days" in out and "rebaselined v2" not in out


# --------------------------------------------------------------------------- 2. step test


def test_scan_statistic_finds_a_step_and_ignores_noise():
    rng = np.random.default_rng(0)
    w = rng.integers(28, 36, 30).astype(float)
    x = rng.normal(0, 0.03, 30)
    t0, _k, _ = scan_statistic(x, w, 6)
    assert t0 < DEFAULT_THRESHOLD
    x[15:] += 0.20
    t1, k1, info = scan_statistic(x, w, 6)
    assert t1 > DEFAULT_THRESHOLD and k1 == 15
    assert info["mean_after"] - info["mean_before"] == pytest.approx(0.20, abs=0.03)
    assert scan_statistic(x[:11], w[:11], 6) == (0.0, None, {})  # fewer than 2 x min_run


def test_scan_threshold_is_calibrated_to_five_percent_over_36_bills():
    """The #74 calibration, re-checked on independent noise: with a look after every bill, a
    series of 36 bills with no change alarms in about 5% of meters."""
    rng = np.random.default_rng(74)
    alarms = 0
    n = 400
    for _ in range(n):
        w = rng.integers(28, 36, 36).astype(float)
        x = rng.normal(0, 1, 36) / np.sqrt(w / w.mean())
        for m in range(12, 37):
            if scan_statistic(x[:m], w[:m], DEFAULT_MIN_RUN)[0] > DEFAULT_THRESHOLD:
                alarms += 1
                break
    assert alarms / n < 0.08


def test_bill_step_rule_spec():
    assert BillStepRule.from_spec(None) is None and BillStepRule.from_spec("off") is None
    assert BillStepRule.from_spec("scan") == BillStepRule() == BillStepRule.from_spec(True)
    assert BillStepRule().calibrated and not BillStepRule(min_run=4).calibrated
    assert "unknown" in BillStepRule(threshold=3).describe()
    for bad in ({"min_run": 2}, {"threshold": 0}, {"nope": 1}, "yes", {"min_run": 4.5}):
        with pytest.raises(ValueError):
            BillStepRule.from_spec(bad)
    # the policy carries it, and leaves it out of its record when unset (stored provenance)
    pol = RebaselinePolicy.from_entry({"rebaseline": {"bill_steps": "scan"}})
    assert pol.as_dict()["bill_steps"] == {"min_run": 6, "threshold": 3.75, "calibrated": True}
    assert "bill_steps" not in RebaselinePolicy.from_entry({}).as_dict()


def _steps_case(step, seed=1):
    df, temp = _building(seed=seed, noise=20.0, steps=((step[0], step[1]),) if step else ())
    _bs, fr = _bills(df, temp)
    base = fr[fr["end"] <= pd.Timestamp("2021-01-01")]
    return fr, _mvform.fit(base)


def test_t1_on_bills_is_opt_in_and_finds_a_large_step():
    fr, model = _steps_case(("2022-07-01", 0.20))
    kw = dict(baseline=["2019-01-01", "2020-12-31"])
    off = assess_triggers(fr, model, policy=RebaselinePolicy.from_entry({}), **kw)
    assert not [t for t in off if t.id == "T1"]  # as before: too few bills for the daily test
    pol = RebaselinePolicy.from_entry({"rebaseline": {"bill_steps": "scan"}})
    on = assess_triggers(fr, model, policy=pol, **kw)
    (t1,) = [t for t in on if t.id == "T1"]
    assert abs((pd.Timestamp(t1.date) - pd.Timestamp("2022-07-01")).days) <= 70
    assert t1.outcome == "rebaseline" and t1.blocks  # |step| >= major_step_frac 0.20
    assert t1.evidence["z"] > 3.75 and t1.evidence["n_after"] >= 6
    assert "calibrated to a 5% false-alarm rate" in t1.basis
    # a 10% step is an indicator NRA, not a rebaseline
    fr, model = _steps_case(("2022-07-01", 0.10), seed=2)
    on = assess_triggers(fr, model, policy=pol, **kw)
    assert [t.outcome for t in on if t.id == "T1"] == ["nra_indicator"]


def test_ecm_and_declared_changes_are_not_redetected():
    fr, model = _steps_case(("2022-07-01", -0.20))
    kw = dict(baseline=["2019-01-01", "2020-12-31"])
    ecm = {"ecm_dates": ["2022-07-01"], "rebaseline": {"bill_steps": "scan"}}
    trig = assess_triggers(fr, model, policy=RebaselinePolicy.from_entry(ecm), **kw)
    assert not [t for t in trig if t.id == "T1"]
    ev = [{"date": "2022-07-01", "description": "wing closed", "magnitude": "major"}]
    entry = {"rebaseline": {"bill_steps": "scan", "events": ev}}
    from camber.mandv.rebaseline import events_from_entry

    trig = assess_triggers(fr, model, policy=RebaselinePolicy.from_entry(entry),
                           events=events_from_entry(entry)[0], **kw)  # fmt: skip
    assert [t.id for t in trig] == ["T2"]
    # with no change at all nothing is found
    fr, model = _steps_case(None)
    steps = bill_steps(fr[fr["start"] >= pd.Timestamp("2021-01-01")], model, BillStepRule())
    assert steps == []


def test_bill_steps_in_the_run_path(tmp_path, capsys):
    cfg = _cfg(rebaseline={"bill_steps": "scan"})
    path = _write(tmp_path, cfg, noise=20.0, steps=(("2022-07-01", 0.25),))
    _mv(["mv", "freeze", path, "--reason", "initial", "--by", "ana", "--apply"], capsys)
    fs = run_mv_config(load_config(path), base_dir=str(tmp_path))
    (t1,) = [f for f in fs if f.rule == "mv_trigger" and f.metrics["id"] == "T1"]
    (s,) = [f for f in fs if f.rule == "mv_savings"]
    assert s.metrics["partial"] and s.metrics["reporting_period"][1] < "2022-07-01"
    rc, out = _mv(["mv", "propose", path], capsys)
    assert "T1:" in out and "proposal: rebaseline" in out
    # a bad block is a config error
    cfg["mv"][0]["rebaseline"] = {"bill_steps": "cusum"}
    json.dump(cfg, open(path, "w"))
    with pytest.raises(ValueError, match="bill_steps"):
        run_config(load_config(path), base_dir=str(tmp_path))


# --------------------------------------------------------------------------- 3. the proposal


def test_select_method_offers_the_degree_day_model_at_the_selected_bases():
    from camber.mvbilling import billing_frame

    df, temp = _building(years=("2019-01-02", "2021-12-31"), steps=(("2021-01-01", -0.1),))
    bs = BillingSeries.from_frame(df, energy="kwh")
    fr = billing_frame(bs, temp, heating_base_f=HB, cooling_base_f=CB, dd_kind="DD-HC")
    kw = dict(baseline=["2019-01-01", "2020-12-31"], reporting=["2021-01-01", "2021-12-31"])
    p = select_method(fr, **kw)
    assert p.models["baseline"]["kind"] == "DD-HC" and p.models["baseline"]["p"] == 5
    assert p.models["baseline"]["heating_base_f"] == HB
    assert any(c.startswith("candidate models:") for c in p.caveats)
    fc = {r["method"]: r for r in p.sensitivity}["forecast"]
    assert fc["savings_pct"] == pytest.approx(0.1, abs=0.02)
    # degree_day=False is the change-point-only proposal, exactly as on a frame without the bases
    plain = BillingSeries.from_frame(df, energy="kwh").energy_vs_temp(temp)
    a = select_method(fr, degree_day=False, **kw).as_dict()
    b = select_method(plain, **kw).as_dict()
    assert a["models"]["baseline"]["kind"] == b["models"]["baseline"]["kind"] != "DD-HC"
    assert a["proposed"] == b["proposed"]
    with pytest.raises(ValueError, match="degree_day"):
        select_method(plain, degree_day=True, **kw)
    # standard conditions: one normal year drives both models of one form
    ny = list(55 - 22 * np.cos(2 * np.pi * (np.arange(1, 366) - 15) / 365.25))
    p = select_method(fr, standard_conditions=ny, **kw)
    sc = {s["method"]: s for s in p.steps}["standard_conditions"]
    assert sc["valid"], sc


def test_window_may_start_right_at_the_settle_days_after_the_event():
    df, temp = _building(years=("2019-01-02", "2021-03-31"))
    _bs, fr = _bills(df, temp)
    st, en = pd.DatetimeIndex(fr["start"]), pd.DatetimeIndex(fr["end"])
    # cut the bills where the latest full service year starts with the very first bill
    k = next(
        k for k in range(len(fr)) if (en[k] - st[0]).days >= 365 and (en[k] - st[1]).days < 365
    )
    fr = fr.iloc[: k + 1]
    pol = RebaselinePolicy.from_entry({"settle_days": 10})
    trig = st[0] - pd.Timedelta(days=10)  # the earliest start is exactly the first bill's start
    ev = (DeclaredChange(str(trig.date()), "the trigger itself", "major"),)
    w = new_bill_window(
        fr, after=str(trig.date()), fit=lambda p: _fit(fr, p), policy=pol, events=ev
    )
    assert w.ok and w.window[0] == str(st[0].date()), w.declined_reason
