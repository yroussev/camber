"""Answer key: workbook exercise ``mv-baselines`` (docs/workbook/mv-baselines.md).

Real-data figures were recorded from::

    camber datasets fetch cofactor-drammen
    camber datasets fetch valladolid-uva
    camber datasets fetch bdg2
    camber datasets ingest cofactor-drammen valladolid-uva bdg2 --store lab_store
    camber datasets config cofactor-drammen --exercise mv-baselines--forecast
        --store lab_store --out fc.json
    camber run fc.json --out fc_out
    camber datasets config cofactor-drammen --exercise mv-baselines--backcast
        --store lab_store --out bc.json
    camber run bc.json --out bc_out
    camber datasets config valladolid-uva --exercise mv-baselines --store lab_store --out chain.json
    camber run chain.json --out chain_out
    camber datasets config cofactor-drammen --exercise mv-baselines--covid
        --store lab_store --out covid.json
    camber run covid.json --out covid_out
    python make_bills.py lab_store Fox_lodging_Stephen__chilledwater bills.csv
    camber run bills.json --out bills_out

(``make_bills.py`` and ``bills.json`` are the page's; ``_bills_frame`` / ``_bills_config``
below are the same code. The electricity bills repeat the last two commands with
``Fox_lodging_Stephen__electricity``.)

(CAMBER 0.97.0-dev, the cofactor-drammen, valladolid-uva and bdg2 default subsets,
2026-09-29.)
"""

from __future__ import annotations

import os
import tempfile

import pandas as pd
from _practice_standins import bdg2_fox, cofactor, valladolid
from _workbook import REAL, Check, Exercise, Finding, Metric, Run

from camber.config import run_config
from camber.store import ParquetStore

# --------------------------------------------------------------------------- synthetic bills


def _bills_frame(store: str, equip: str) -> pd.DataFrame:
    """Synthetic utility bills from one BDG2 Fox meter (the page's make_bills.py, verbatim logic):
    whole days only, read-to-read periods of 28-35 days in a fixed cycle, and a bill that holds
    a missing day is dropped (as an unbillable period would be)."""
    frame = ParquetStore(store).read_role_frame(facility_id="ds-bdg2-fox", equip=equip)
    daily = frame.iloc[:, 0].resample("1D").sum(min_count=24)  # NaN unless all 24 hours
    lengths = [30, 33, 28, 31, 35, 29, 32]  # read-to-read days, repeating
    rows, start, k = [], daily.index[0], 0
    while start <= daily.index[-1]:
        end = start + pd.Timedelta(days=lengths[k % len(lengths)] - 1)
        days = daily[start:end]
        if len(days) == (end - start).days + 1 and days.notna().all():
            rows.append({"start": start.date(), "end": end.date(), "energy": round(days.sum(), 1)})
        start, k = end + pd.Timedelta(days=1), k + 1
    return pd.DataFrame(rows)


def _bills_config(store: str, name: str) -> dict:
    """The page's bills.json: 2016 bills as the baseline, 2017 bills as the reporting period."""
    return {
        "source": {"kind": "store", "store": store, "facility_id": "ds-bdg2-fox"},
        "shared_oat": {"equip": "weather", "role": "oat"},
        "rules": [],
        "mv": [
            {
                "bills": {"file": "bills.csv", "energy": "energy"},
                "name": name,
                "period": ["2016-01-01", "2016-12-31"],
                "reporting_period": ["2017-01-01", "2017-12-31"],
                "method": "forecast",
                "base_f": "auto",
                "calendarize": True,
                "validity": "both",
            }
        ],
    }


def _bills(ctx, equip: str) -> dict:
    """``{rule: finding}`` of the bills run for one meter (cached on the context)."""
    cache = ctx.__dict__.setdefault("_bills", {})
    if equip not in cache:
        with tempfile.TemporaryDirectory() as tmp:
            _bills_frame(ctx.store, equip).to_csv(os.path.join(tmp, "bills.csv"), index=False)
            res = run_config(_bills_config(ctx.store, "synthetic bills"), base_dir=tmp)
        cache[equip] = {f.rule: f for f in res.findings}
    return cache[equip]


CHW, EL = "Fox_lodging_Stephen__chilledwater", "Fox_lodging_Stephen__electricity"


def _bills_chw(ctx) -> None:
    """Q6: the chilled-water bills: weather explains most of the load (R2 high, a cooling-only
    fit with a cooling base chosen from the bills); the null test's band spans zero."""
    b = _bills(ctx, CHW)
    base, sav = b["mv_baseline"].metrics, b["mv_savings"].metrics
    assert base["billing"] and base["weighted_by_days"], base
    assert base["r2"] > 0.75 and base["cooling_base_f"] is not None, base
    assert abs(sav["avoided_energy"]) < sav["abs_uncertainty"], sav
    if ctx.mode == REAL:
        assert (base["n_bills"], sav["n_report_bills"]) == (10, 11), base
        assert round(base["r2"], 2) == 0.90 and round(base["cv_rmse"], 3) == 0.227, base
        assert base["accept"] is False and base["cooling_base_f"] == 63.0, base


def _bills_el(ctx) -> None:
    """Q7: the electricity bills: the load barely moves with weather, R2 under SEP's 0.50."""
    b = _bills(ctx, EL)
    base, sav = b["mv_baseline"].metrics, b["mv_savings"].metrics
    assert base["r2"] < 0.5, base["r2"]
    assert any("below 0.50" in c for c in b["mv_baseline"].caveats), b["mv_baseline"].caveats
    assert sav["sep_valid"] is False, sav
    assert abs(sav["avoided_energy"]) < sav["abs_uncertainty"], sav
    if ctx.mode == REAL:
        assert round(base["r2"], 2) == 0.48 and round(base["cv_rmse"], 3) == 0.146, base


# --------------------------------------------------------------------------- the M&V runs


def _forecast_vs_backcast(ctx) -> None:
    """Q2: the school b6400 uses less in 2019 than its 2018 model predicts, by more than the
    band, and the backcast agrees; the nursing home's change is inside its band."""
    fc = ctx.finding("mv_savings", "b6400_ElImp", "forecast").metrics
    bc = ctx.finding("mv_savings", "b6400_ElImp", "backcast").metrics
    assert (fc["method"], bc["method"]) == ("forecast", "backcast"), (fc, bc)
    assert fc["avoided_energy"] > fc["abs_uncertainty"], fc
    assert bc["savings"] > 0, bc
    home = ctx.finding("mv_savings", "b6410_ElImp", "forecast").metrics
    assert abs(home["avoided_energy"]) < home["abs_uncertainty"], home


def _b6404_sep(ctx) -> None:
    """Q1: SEP rejects b6404's 2018 model on the logical-sign test (a cooling slope below 0)."""
    f = ctx.finding("mv_savings", "b6404_ElImp", "forecast")
    assert f.metrics["sep_valid"] is False, f.metrics["sep_valid"]
    assert any("cool_slope" in c and "not positive" in c for c in f.caveats), f.caveats


def _covid(ctx) -> None:
    """Q5: the closure indicator is material and negative for the schools; the adjusted chain
    moves the schools' saving down (the closure had been flattering it)."""
    for eq in ("b6400_ElImp", "b6404_ElImp"):
        m = ctx.finding("mv_savings", eq, "covid").metrics
        assert m["method"] == "chaining" and m["adjusted"], m
        (entry,) = m["adjustments"]
        assert entry["material"] and entry["resolved_amount"] < 0, entry
        assert m["adjusted_savings"] < m["savings"], m


def _chain_uva(ctx) -> None:
    """Q4: the chain is SEP-valid for both buildings but neither baseline meets CAMBER's G14
    gate; building B's saving is well above A's."""
    a = ctx.finding("mv_savings", "UVA_A", "uva").metrics
    b = ctx.finding("mv_savings", "UVA_B", "uva").metrics
    assert a["method"] == b["method"] == "chaining", (a, b)
    assert b["savings_pct"] > a["savings_pct"] + 0.05, (a["savings_pct"], b["savings_pct"])


def standin(store) -> None:
    """ds-cofactor-drammen, ds-valladolid-uva and ds-bdg2-fox (see _practice_standins)."""
    cofactor(store)
    valladolid(store)
    bdg2_fox(store)


def _fc(eq, metric, value, tol, quote):
    return Metric("mv_savings", eq, metric, value, tol, run="forecast", on=REAL, quote=quote)


EXERCISE = Exercise(
    id="mv-baselines",
    title="Change-point baselines and M&V: forecast, backcast, chaining and bills",
    issue=83,
    references=("pnnl-retuning-ch4", "pnnl-retuning-ch10"),
    datasets=("cofactor-drammen", "valladolid-uva", "bdg2"),
    runs=(
        Run(dataset="cofactor-drammen", name="forecast", config="mv-baselines--forecast"),
        Run(dataset="cofactor-drammen", name="backcast", config="mv-baselines--backcast"),
        Run(dataset="valladolid-uva", name="uva", config="mv-baselines"),
        Run(dataset="cofactor-drammen", name="covid", config="mv-baselines--covid"),
    ),
    commands=(
        "camber datasets fetch cofactor-drammen",
        "camber datasets fetch valladolid-uva",
        "camber datasets fetch bdg2",
        "camber datasets ingest cofactor-drammen valladolid-uva bdg2 --store lab_store",
        "camber datasets config cofactor-drammen --exercise mv-baselines--forecast "
        "--store lab_store --out fc.json",
        "camber run fc.json --out fc_out",
        "camber datasets config cofactor-drammen --exercise mv-baselines--backcast "
        "--store lab_store --out bc.json",
        "camber run bc.json --out bc_out",
        "camber datasets config valladolid-uva --exercise mv-baselines --store lab_store "
        "--out chain.json",
        "camber run chain.json --out chain_out",
        "camber datasets config cofactor-drammen --exercise mv-baselines--covid "
        "--store lab_store --out covid.json",
        "camber run covid.json --out covid_out",
        "python make_bills.py lab_store Fox_lodging_Stephen__chilledwater bills.csv",
        "camber run bills.json --out bills_out",
    ),
    expect=(
        # part 1: G14 acceptance of the 2018 baselines, then forecast vs backcast
        Finding("mv_baseline", "b6400_ElImp", severity=("ok",), run="forecast"),
        Finding("mv_baseline", "b6410_ElImp", severity=("ok",), run="forecast"),
        Metric(
            "mv_baseline", "b6404_ElImp", "r2", 0.72, 0.005, run="forecast", on=REAL, quote="0.72"
        ),
        Metric(
            "mv_baseline",
            "b6404_ElImp",
            "cv_rmse",
            0.225,
            0.0005,
            run="forecast",
            on=REAL,
            quote="22.5%",
        ),
        Finding("mv_baseline", "b6404_ElImp", severity=("info",), run="forecast", on=REAL),
        _fc("b6400_ElImp", "savings_pct", 0.0576, 0.0005, "5.8%"),
        _fc("b6400_ElImp", "abs_uncertainty", 24050.6, 1.0, "24,051"),
        _fc("b6410_ElImp", "savings_pct", 0.0083, 0.0005, "0.8%"),
        Metric(
            "mv_savings",
            "b6400_ElImp",
            "savings_pct",
            0.0515,
            0.0005,
            run="backcast",
            on=REAL,
            quote="5.1%",
        ),
        Check("forecast and backcast agree", _forecast_vs_backcast),
        Check("b6404 fails SEP's sign test", _b6404_sep, on=REAL),
        # part 2: chaining on the valladolid buildings
        Finding("mv_baseline", "UVA_A", severity=("info",), run="uva", on=REAL),
        Finding("mv_baseline", "UVA_B", severity=("info",), run="uva", on=REAL),
        Metric("mv_baseline", "UVA_B", "r2", 0.55, 0.005, run="uva", on=REAL, quote="0.55"),
        Metric("mv_baseline", "UVA_A", "r2", 0.66, 0.005, run="uva", on=REAL, quote="0.66"),
        Metric(
            "mv_savings", "UVA_B", "savings_pct", 0.126, 0.0005, run="uva", on=REAL, quote="12.6%"
        ),
        Metric(
            "mv_savings", "UVA_A", "savings_pct", -0.001, 0.0005, run="uva", on=REAL, quote="-0.1%"
        ),
        Metric("mv_savings", "UVA_B", "sep_valid", 1, 0, run="uva", on=REAL),
        Metric("mv_savings", "UVA_A", "sep_valid", 1, 0, run="uva", on=REAL),
        Check("chaining: building B saves, A does not", _chain_uva),
        # part 3: chaining across the COVID-19 closure, with a non-routine adjustment
        Check("the closure adjustment", _covid),
        Metric(
            "mv_savings",
            "b6400_ElImp",
            "savings_pct",
            -0.0093,
            0.0005,
            run="covid",
            on=REAL,
            quote="-0.9%",
        ),
        Metric(
            "mv_savings",
            "b6400_ElImp",
            "adjusted_savings_pct",
            -0.0807,
            0.0005,
            run="covid",
            on=REAL,
            quote="-8.1%",
        ),
        # part 4: bill-only M&V on synthetic bills
        Check("chilled-water bills", _bills_chw, quote="63 F"),
        Check("chilled-water bills: counts", _bills_chw, on=REAL, quote="10 bills"),
        Check("chilled-water bills: fit", _bills_chw, on=REAL, quote="22.7%"),
        Check("chilled-water bills: R2", _bills_chw, on=REAL, quote="R² 0.90"),
        Check("electricity bills", _bills_el, quote="0.48"),
    ),
    standin=standin,
)
