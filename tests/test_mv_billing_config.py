"""The config ``mv`` billing path (issue #64): a bills CSV through the full M&V flow.

Synthetic gas bills (a heating meter, 15% saved from 2023) with uneven bill lengths and estimated
reads, paired with a daily outdoor temperature file. No trended equipment and no ``source``.
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.config import run_config, run_mv_config  # noqa: E402
from camber.mandv.billing import BillingSeries  # noqa: E402

SAVE = 0.15


def _write(tmp_path, *, estimated=True, seed=1):
    rng = np.random.default_rng(seed)
    days = pd.date_range("2019-01-01", "2023-12-31", freq="D")
    T = 52 - 24 * np.cos(2 * np.pi * (days.dayofyear - 15) / 365.25) + rng.normal(0, 6, len(days))
    use = 20 + 6.0 * np.maximum(0, 60 - T) + rng.normal(0, 15, len(days))
    use = np.where(days >= "2023-01-01", use * (1 - SAVE), use)
    rows, i, k = [], 0, 0
    b23 = int(np.argmax(days >= "2023-01-01"))
    while i < len(days):
        n = int(rng.integers(27, 35)) if rng.random() > 0.15 else int(rng.choice([15, 50]))
        j = min(i + n, len(days))
        if i < b23 < j:
            j = b23
        est = estimated and k in (7, 30)  # two estimated reads, each trued up by the next bill
        rows.append(
            {
                "start": days[i].date(),
                "end": days[j - 1].date(),
                "therms": round(float(use[i:j].sum()), 1),
                "units": "therm",
                "estimated": "E" if est else "A",
            }
        )
        i, k = j, k + 1
    pd.DataFrame(rows).to_csv(tmp_path / "gas.csv", index=False)
    pd.DataFrame({"timestamp": days, "oat": np.round(T, 2)}).to_csv(
        tmp_path / "oat.csv", index=False
    )
    return rows


def _cfg(**entry):
    base = {
        "bills": {"file": "gas.csv", "energy": "therms"},
        "name": "Gas meter",
        "period": ["2019-01-01", "2021-12-31"],
        "reporting_period": ["2023-01-01", "2023-12-31"],
        "method": "forecast",
        "kernel": "exact",
    }
    base.update(entry)
    return {"site": "Demo", "shared_oat": {"file": "oat.csv"}, "mv": [base]}


def _find(res, rule):
    return [f for f in res.findings if f.rule == rule]


def test_forecast_on_bills_recovers_the_planted_saving(tmp_path):
    _write(tmp_path)
    res = run_config(_cfg(), base_dir=str(tmp_path))
    assert res.rules_run == ["mv:bills:Gas meter"] and res.equipment == 0
    (b,) = _find(res, "mv_baseline")
    m = b.metrics
    assert m["billing"] and m["weighted_by_days"] and m["units"] == "therm"
    assert m["model"] in ("3PH", "4P", "5P", "2P") and m["g14_interval"] == "monthly"
    assert m["n_bills"] >= 30 and m["n_days"] >= 1000
    assert m["hdd_total"] > 1000 and m["oat_source"] == "shared_oat"
    assert m["estimated_merged"] == 2  # both estimated reads in the file, trued up
    assert "monthly G14" in b.summary and "days-weighted" in b.summary
    assert any("estimated read(s) merged" in c for c in b.caveats)
    (s,) = _find(res, "mv_savings")
    sm = s.metrics
    assert sm["billing"] and sm["n_report_bills"] >= 10 and sm["n_report_days"] == 365
    assert sm["kernel"] == "exact" and not sm["declined"]
    assert sm["savings_pct"] == pytest.approx(SAVE, abs=0.04)
    assert sm["savings"] if "savings" in sm else sm["avoided_energy"] > 0
    band = sm["abs_uncertainty"]
    assert 0 < band < 0.5 * sm["avoided_energy"]
    assert "reporting days (" in s.summary and "bills)" in s.summary


def test_other_methods_validity_and_proposal(tmp_path):
    _write(tmp_path)
    ny = list(52 - 24 * np.cos(2 * np.pi * (np.arange(1, 366) - 15) / 365.25))
    runs = {
        "backcast": dict(method="backcast", kernel="exact"),
        "chaining": dict(method="chaining", intermediate_period=["2022-01-01", "2022-12-31"],
                         period=["2021-01-01", "2021-12-31"]),
        "standard_conditions": dict(method="standard_conditions", normal_year=ny),
    }  # fmt: skip
    for name, kw in runs.items():
        res = run_config(_cfg(validity="both", **kw), base_dir=str(tmp_path))
        (s,) = _find(res, "mv_savings")
        assert s.metrics["method"] == name, s.summary
        assert not s.metrics["declined"], s.summary
        assert s.metrics["billing"] and s.metrics["validity"] == "both"
        assert s.metrics["sep_valid"] in (True, False)
        if name != "standard_conditions":
            assert s.metrics["savings_pct"] == pytest.approx(SAVE, abs=0.06), name
    res = run_config(_cfg(method="auto"), base_dir=str(tmp_path))
    (p,) = _find(res, "mv_method_proposal")
    assert p.metrics["proposed"] == "forecast"
    fc = next(r for r in p.metrics["sensitivity"] if r["method"] == "forecast")
    assert fc["savings_pct"] == pytest.approx(SAVE, abs=0.04)


def test_adjustments_ledger_on_bills(tmp_path):
    _write(tmp_path)
    adj = [
        {"kind": "nra", "method": "engineering", "start": "2023-06-01", "amount": 150.0,
         "se": 40.0, "reason": "added a small kitchen load", "evidence": "sub-meter log"},
        {"kind": "nra", "method": "indicator", "start": "2023-09-01", "reason": "occupancy change",
         "fit_period": "reporting"},
    ]  # fmt: skip
    res = run_config(_cfg(adjustments=adj), base_dir=str(tmp_path))
    (s,) = _find(res, "mv_savings")
    m = s.metrics
    assert m["adjusted"] is True, m.get("adjustments_refused")
    assert len(m["adjustments"]) == 2
    ind = next(a for a in m["adjustments"] if a["method"] == "indicator")
    assert ind["rows_affected"] == 122  # dated to the day: Sep 1 .. Dec 31
    assert m["adjusted_savings"] != m["avoided_energy"]


def test_entry_oat_file_min_bills_and_declines(tmp_path):
    _write(tmp_path)
    cfg = _cfg(oat={"file": "oat.csv"})
    del cfg["shared_oat"]
    res = run_config(cfg, base_dir=str(tmp_path))
    assert _find(res, "mv_baseline")[0].metrics["oat_source"] == "file oat.csv"
    del cfg["mv"][0]["oat"]
    res = run_config(cfg, base_dir=str(tmp_path))
    b, s = _find(res, "mv_baseline")[0], _find(res, "mv_savings")[0]
    assert b.metrics["declined"] and "no outdoor temperature" in b.metrics["declined_reason"]
    assert s.metrics["declined"]
    res = run_config(_cfg(min_bills=60), base_dir=str(tmp_path))
    assert "usable bill(s) in the baseline (< 60)" in _find(res, "mv_baseline")[0].summary
    out = run_mv_config(_cfg(reporting_period=None, method=None), base_dir=str(tmp_path))
    assert [f.rule for f in out] == ["mv_baseline"]


def test_merge_off_keeps_estimates_flagged(tmp_path):
    _write(tmp_path)
    res = run_config(
        _cfg(bills={"file": "gas.csv", "energy": "therms", "merge_estimated": False}),
        base_dir=str(tmp_path),
    )
    b = _find(res, "mv_baseline")[0]
    assert b.metrics["estimated_merged"] == 0
    assert any("used as billed" in c for c in b.caveats)


def test_billing_entry_config_errors(tmp_path):
    _write(tmp_path)
    for bad, msg in (
        (dict(model="cp_driver", drivers=["weekday"]), "change_point"),
        (dict(colour="red"), "unknown key"),
        (dict(bills={"file": "gas.csv", "sheet": 1}), "unknown key"),
        (dict(bills={"energy": "therms"}), "file path"),
        (dict(interval="hourly"), "interval"),
    ):
        with pytest.raises(ValueError, match=msg):
            run_config(_cfg(**bad), base_dir=str(tmp_path))


def test_versioned_verbs_skip_billing_entries(tmp_path):
    from camber.mvrun import meter_series

    _write(tmp_path)
    assert meter_series(_cfg(), base_dir=str(tmp_path)) == []


# ------------------------------------------------------------------------ BillingSeries additions


def test_from_csv_units_and_flags(tmp_path):
    rows = _write(tmp_path)
    bs = BillingSeries.from_csv(tmp_path / "gas.csv", energy="therms")
    assert bs.units == "therm" and len(bs) == len(rows)
    assert int(bs.frame["estimated"].sum()) == 2
    df = pd.read_csv(tmp_path / "gas.csv")
    df.loc[3, "units"] = "kWh"
    df.to_csv(tmp_path / "mixed.csv", index=False)
    with pytest.raises(ValueError, match="mixes energy units"):
        BillingSeries.from_csv(tmp_path / "mixed.csv", energy="therms")
    with pytest.raises(ValueError, match="no column"):
        BillingSeries.from_csv(tmp_path / "gas.csv")
    df = pd.read_csv(tmp_path / "gas.csv")
    df.loc[0, "estimated"] = "maybe"
    df.to_csv(tmp_path / "flag.csv", index=False)
    with pytest.raises(ValueError, match="estimated-read flag"):
        BillingSeries.from_csv(tmp_path / "flag.csv", energy="therms")
    df = pd.read_csv(tmp_path / "gas.csv")
    df.loc[0, "start"] = "not a date"
    df.to_csv(tmp_path / "date.csv", index=False)
    with pytest.raises(ValueError, match="unreadable date"):
        BillingSeries.from_csv(tmp_path / "date.csv", energy="therms")
    plain = pd.read_csv(tmp_path / "gas.csv").drop(columns=["estimated", "units"])
    plain["estimated"] = [True, 1, 0.0, "yes", None] + ["n"] * (len(plain) - 5)
    plain.to_csv(tmp_path / "plain.csv", index=False)
    b2 = BillingSeries.from_csv(tmp_path / "plain.csv", energy="therms", units="therm")
    assert b2.frame["estimated"].tolist()[:6] == [True, True, False, True, False, False]


def test_merge_estimated_runs_gaps_and_trailing():
    f = pd.DataFrame(
        {
            "start": ["2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01", "2024-06-01",
                      "2024-07-01", "2024-08-01"],
            "end": ["2024-01-31", "2024-02-29", "2024-03-31", "2024-04-30", "2024-06-30",
                    "2024-07-31", "2024-08-31"],
            "energy": [100.0, 50.0, 60.0, 190.0, 80.0, 90.0, 40.0],
            "estimated": [False, True, True, False, True, False, True],
        }
    )  # fmt: skip
    # Feb + Mar estimated, trued up by April; the June estimate follows a gap (May missing) but
    # is itself followed by July -> merged; the August estimate is the last bill -> dropped
    bs = BillingSeries.from_frame(f, estimated="estimated").merge_estimated()
    out = bs.frame
    assert out["start"].dt.strftime("%m-%d").tolist() == ["01-01", "02-01", "06-01"]
    assert out["energy"].tolist() == [100.0, 300.0, 170.0]
    assert not out["estimated"].any()
    assert [m["action"] for m in bs.merged] == ["merged", "merged", "dropped"]
    assert bs.merged[0] == {
        "action": "merged", "start": "2024-02-01", "end": "2024-04-30", "n_estimated": 2
    }  # fmt: skip
    assert bs.merged[2] == {"action": "dropped", "start": "2024-08-01", "end": "2024-08-31"}
    # an estimate before a gap cannot be trued up by the bill after the gap
    g = f.iloc[[0, 1, 4 - 0]].copy()
    g.loc[g.index[2], "estimated"] = False
    gb = BillingSeries.from_frame(g.reset_index(drop=True), estimated="estimated").merge_estimated()
    assert gb.frame["start"].dt.strftime("%m-%d").tolist() == ["01-01", "06-01"]
    assert gb.merged == [{"action": "dropped", "start": "2024-02-01", "end": "2024-02-29"}]
    plain = BillingSeries.from_frame(f.assign(estimated=False), estimated="estimated")
    assert plain.merge_estimated() is plain
