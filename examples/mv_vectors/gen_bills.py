"""The bill-based part of the shared M&V vectors (imported by ``generate.py``).

Each bill case is a utility-bill table and a daily outdoor-temperature file. The bills have
irregular read periods (28-35 days, starting mid-month), one estimated read (trued up by the next
actual read) and one missing bill (a gap). CAMBER runs them through its own config path for bills
(an ``mv`` entry with ``bills``, ``base_f: "auto"``, ``calendarize`` and ``avoided_cost: "bills"``)
and the expected outputs are read from the findings it reports, plus the fitted model's
coefficients, the degree days built both ways and a calendarization of an overlapping table.

The costs are a **synthetic tariff** (a seeded rate per bill): BDG2 publishes no costs.
"""

from __future__ import annotations

import os
import shutil
import tempfile

import fetch_bdg2
import numpy as np
import pandas as pd
from fetch_bdg2 import bills_from_daily

from camber.config import run_config
from camber.mandv.basetemp import bill_degree_days, fit_bill_degree_day
from camber.mandv.billing import BillingSeries, calendarize
from camber.mandv.stats import fit_stats
from camber.mvbilling import fit_billing_baseline

CALSIM_MONTHLY = {"nmbe_max_pct": 5.0, "cvrmse_max_pct": 15.0}

BILL_CASES = [
    {
        "id": "bills_syn_dd_hc_55_68",
        "source": "synthetic",
        "unit": "kWh",
        "rate": 0.13,
        "seed": 201,
        "first_start": "2020-12-15",
        "last_day": "2022-12-31",
        "truth": {
            "kind": "DD-HC",
            "intercept_per_day": 300.0,
            "heating_slope": 10.0,
            "cooling_slope": 15.0,
            "heating_base_f": 55.0,
            "cooling_base_f": 68.0,
            "daily_noise_sd": 30.0,
            "degree_days_from": "each day's mean OAT",
        },
        "note": "daily degree-day truth (heating base 55 F, cooling base 68 F) read as bills",
    },
]
BILL_CASES += fetch_bdg2.BDG2_BILL_CASES

# A plain table (not a BillingSeries) with one overlap, one gap and one estimated read: the
# calendarization alone, with max_gap_days 0 and 10.
OVERLAP_ROWS = [
    ("2023-01-10", "2023-02-08", 3100.0, False),
    ("2023-02-09", "2023-03-12", 2950.0, False),
    ("2023-03-06", "2023-04-11", 3300.0, False),  # starts 7 days before the previous bill ends
    ("2023-04-12", "2023-05-10", 2600.0, True),
    ("2023-05-11", "2023-06-09", 2500.0, False),
    ("2023-06-14", "2023-07-12", 2700.0, False),  # 4 unserved days before it
]


def _sig(x, digits: int = 7):
    if x is None:
        return None
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    x = float(x)
    if not np.isfinite(x):
        return None
    if x == 0.0:
        return 0.0
    out = float(f"{x:.{digits}g}")
    return 0.0 if out == 0.0 else out


def _clean(o):
    """JSON-ready, 7 significant figures, no non-finite numbers."""
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (float, np.floating, int, np.integer, bool, np.bool_)):
        return _sig(o)
    if isinstance(o, pd.Timestamp):
        return str(o.date())
    return o


def _write(df: pd.DataFrame, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_csv(path, index=False, lineterminator="\n")


def _convention_free(m) -> dict:
    """``base`` and signed ``slopes_dEdT`` (left to right) of a CAMBER change-point model."""
    c, k = m.coeffs, m.kind
    if not m.change_points:  # 2P, or a 5P that fell back to a line
        return {"base": c["base"], "slopes_dEdT": [c["slope"]]}
    if k == "3PC":
        return {"base": c["base"], "slopes_dEdT": [0.0, c["cool_slope"]]}
    if k == "3PH":
        return {"base": c["base"], "slopes_dEdT": [-c["heat_slope"], 0.0]}
    if k == "4P":
        return {"base": c["base"], "slopes_dEdT": [c["left_slope"], c["right_slope"]]}
    if k == "5P":
        return {"base": c["base"], "slopes_dEdT": [-c["heat_slope"], 0.0, c["cool_slope"]]}
    raise ValueError(k)


# --------------------------------------------------------------------------- inputs


def _synthetic_daily(case: dict) -> pd.DataFrame:
    t = case["truth"]
    rng = np.random.default_rng(case["seed"])
    days = pd.date_range(
        pd.Timestamp(case["first_start"]) - pd.Timedelta(days=14), case["last_day"], freq="D"
    )
    doy = days.dayofyear.to_numpy(float)
    oat = np.round(
        58.0 + 24.0 * np.sin(2 * np.pi * (doy - 105.0) / 365.25) + rng.normal(0, 5, len(days)), 1
    )
    e = (
        t["intercept_per_day"]
        + t["heating_slope"] * np.maximum(0.0, t["heating_base_f"] - oat)
        + t["cooling_slope"] * np.maximum(0.0, oat - t["cooling_base_f"])
        + rng.normal(0, t["daily_noise_sd"], len(days))
    )
    return pd.DataFrame({"date": days, "oat_f": oat, "energy": e})


def build_bill_inputs(out: str) -> dict:
    """Write the synthetic bill case's bills and daily OAT, and the overlap table; returns
    ``{case_id: {bills, oat}}`` (the BDG2 bill cases are built by ``fetch_bdg2.py``)."""
    files: dict = {}
    for c in BILL_CASES:
        if c["source"] != "synthetic":
            continue
        daily = _synthetic_daily(c)
        daily["energy"] = daily["energy"].round(2)
        rel_b = f"inputs/bills/{c['id']}_bills.csv"
        rel_t = f"inputs/bills/{c['id']}_oat.csv"
        _write(bills_from_daily(c, daily), os.path.join(out, rel_b))
        oat = pd.DataFrame({"date": daily["date"].dt.strftime("%Y-%m-%d"), "oat_f": daily["oat_f"]})
        _write(oat, os.path.join(out, rel_t))
        files[c["id"]] = {"bills": rel_b, "oat": rel_t}
    ov = pd.DataFrame(OVERLAP_ROWS, columns=["start", "end", "energy", "estimated"])
    ov["cost"] = (ov["energy"] * 0.12).round(2)
    rel = "inputs/bills/calendarize_overlap.csv"
    _write(ov, os.path.join(out, rel))
    files["calendarize_overlap"] = {"bills": rel}
    return files


# --------------------------------------------------------------------------- expected outputs


def _entry(bills: pd.DataFrame, energy: str, cost: str) -> dict:
    base = bills[bills["period"] == "baseline"]
    rep = bills[bills["period"] == "reporting"]
    return {
        "bills": {
            "file": "bills.csv",
            "energy": energy,
            "estimated": "estimated",
            "cost": cost,
        },
        "oat": {"file": "oat.csv"},
        "name": "meter",
        "period": [base["start"].iloc[0], base["end"].iloc[-1]],
        "reporting_period": [rep["start"].iloc[0], rep["end"].iloc[-1]],
        "method": "forecast",
        "kernel": "g14",
        "validity": "both",
        "base_f": "auto",
        "calendarize": True,
        "avoided_cost": "bills",
    }


def _model_block(model) -> dict:
    if hasattr(model, "heating_base_f"):  # BillingDegreeDayModel
        return {
            "family": "degree_day",
            "kind": model.kind,
            "n_params": int(model.p),
            "intercept_per_day": model.intercept,
            "heating_slope": model.heating_slope if model.kind in ("DD-H", "DD-HC") else None,
            "cooling_slope": model.cooling_slope if model.kind in ("DD-C", "DD-HC") else None,
            "heating_base_f": model.heating_base_f,
            "cooling_base_f": model.cooling_base_f,
            "predicts": "energy per day = intercept + heating_slope x HDD/day + cooling_slope x "
            "CDD/day, degree days built from each day's OAT at the bases",
        }
    from camber.mandv.models import N_PARAMS

    cf = _convention_free(model)
    return {
        "family": "change_point",
        "kind": model.kind,
        "n_params": N_PARAMS[model.kind],
        "change_points": list(model.change_points),
        "base": cf["base"],
        "slopes_dEdT": cf["slopes_dEdT"],
        "predicts": "energy per day from the bill's mean OAT",
    }


def _degree_days(frame: pd.DataFrame, oat: pd.Series, model, sel_kind) -> dict:
    """Per-bill degree days at the selected bases, built from each day vs from the bill's mean
    OAT, and the degree-day model refitted on the mean-built ones."""
    hb = frame.attrs.get("heating_base_f")
    cb = frame.attrs.get("cooling_base_f")
    starts, ends = frame["start"], frame["end"]
    d = frame["days"].to_numpy(float)
    T = frame["oat"].to_numpy(float)
    h_day = bill_degree_days(oat, starts, ends, [hb], leg="heating")[:, 0]
    c_day = bill_degree_days(oat, starts, ends, [cb], leg="cooling")[:, 0]
    h_mean = np.maximum(0.0, hb - T)
    c_mean = np.maximum(0.0, T - cb)
    rows = [
        {
            "start": str(s.date()),
            "end": str((e - pd.Timedelta(days=1)).date()),
            "days": int(n),
            "oat_mean_f": t,
            "hdd_from_daily": hd * n,
            "hdd_from_mean": hm * n,
            "cdd_from_daily": cd * n,
            "cdd_from_mean": cm * n,
        }
        for s, e, n, t, hd, hm, cd, cm in zip(starts, ends, d, T, h_day, h_mean, c_day, c_mean)
    ]
    out = {
        "heating_base_f": hb,
        "cooling_base_f": cb,
        "per_bill": rows,
        "totals": {
            "hdd_from_daily": float((h_day * d).sum()),
            "hdd_from_mean": float((h_mean * d).sum()),
            "cdd_from_daily": float((c_day * d).sum()),
            "cdd_from_mean": float((c_mean * d).sum()),
        },
    }
    if sel_kind:
        y = frame["energy"].to_numpy(float)
        cols = {"DD-H": [h_day], "DD-C": [c_day], "DD-HC": [h_day, c_day]}[sel_kind]
        colm = {"DD-H": [h_mean], "DD-C": [c_mean], "DD-HC": [h_mean, c_mean]}[sel_kind]
        fits = {}
        for label, cc in (("from_daily", cols), ("from_mean", colm)):
            X = np.column_stack(cc)
            m = fit_bill_degree_day(
                X, y, kind=sel_kind, heating_base_f=hb, cooling_base_f=cb, days=d
            )
            st = fit_stats(y, m.predict(X), m.p, cv_rmse_max=0.15, weights=d)
            n = st.n
            sse = float(st.rmse) ** 2 * (n - m.p)
            fits[label] = {
                "kind": sel_kind,
                "n_params": m.p,
                "r2": st.r2,
                "cvrmse_pct": st.cv_rmse * 100.0,
                "bic": n * np.log(sse / n + 1e-12) + m.p * np.log(n),
                "intercept_per_day": m.intercept,
                "heating_slope": m.heating_slope,
                "cooling_slope": m.cooling_slope,
            }
        out["degree_day_model_on_baseline"] = fits
    return out


def _savings(fnd) -> dict:
    m = fnd.metrics
    keep = (
        "avoided_energy",
        "baseline_projected",
        "reporting_actual",
        "savings_pct",
        "fsu",
        "abs_uncertainty",
        "confidence",
        "kernel",
        "rho",
        "coverage_tier",
        "declined",
        "n_report_bills",
        "n_report_days",
        "avoided_cost",
        "avoided_cost_rate",
        "avoided_cost_uncertainty",
        "avoided_cost_basis",
    )
    out = {k: m.get(k) for k in keep}
    out["savings_total"] = out.pop("avoided_energy")
    out["savings_fraction"] = out.pop("savings_pct")
    out["sep_valid"] = m.get("sep_valid")
    out["sep_failures"] = (m.get("sep_validity") or {}).get("baseline", {}).get("sep_failures")
    return out


def _calendarized(cal: dict) -> dict:
    keep = (
        "month",
        "days_in_month",
        "days_covered",
        "complete",
        "energy",
        "cost",
        "n_bills",
        "estimated",
        "oat",
        "hdd",
        "cdd",
    )
    return {
        "method": cal["method"],
        "max_gap_days": cal["max_gap_days"],
        "heating_base_f": cal["heating_base_f"],
        "cooling_base_f": cal["cooling_base_f"],
        "declined": cal["declined"],
        "declined_reason": cal["declined_reason"],
        "gaps": cal["gaps"],
        "overlaps": cal["overlaps"],
        "months": [{k: r.get(k) for k in keep if k in r} for r in cal["months"]],
        "annual": cal["annual"],
    }


def _run(bills_path: str, oat_path: str, entry: dict):
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copyfile(bills_path, os.path.join(tmp, "bills.csv"))
        shutil.copyfile(oat_path, os.path.join(tmp, "oat.csv"))
        res = run_config({"site": "Vectors", "mv": [entry]}, base_dir=tmp)
        bills = BillingSeries.from_csv(
            os.path.join(tmp, "bills.csv"),
            energy=entry["bills"]["energy"],
            cost=entry["bills"]["cost"],
        ).merge_estimated()
    by = {f.rule: f for f in res.findings}
    return by, bills


def bill_case_expected(case: dict, files: dict, out: str) -> dict:
    bills_path = os.path.join(out, files["bills"])
    oat_path = os.path.join(out, files["oat"])
    table = pd.read_csv(bills_path)
    oat_df = pd.read_csv(oat_path)
    oat = pd.Series(oat_df["oat_f"].to_numpy(float), index=pd.to_datetime(oat_df["date"]))
    runs = {}
    for label, en, co in (
        ("raw", "energy", "cost"),
        ("injected", "energy_injected", "cost_injected"),
    ):
        entry = _entry(table, en, co)
        by, bills = _run(bills_path, oat_path, entry)
        runs[label] = (entry, by, bills)
    entry, by, bills = runs["raw"]
    fb = by["mv_baseline"]
    m = fb.metrics
    fitted = fit_billing_baseline(bills, oat, entry, entry["period"])
    model = fitted["model"]
    if model.kind != m["model"]:
        raise RuntimeError(f"{case['id']}: refit {model.kind} != reported {m['model']}")
    sel = m.get("base_selection") or {}
    nmbe_pct = round(m["nmbe"] * 100.0, 4)
    cv_pct = m["cv_rmse"] * 100.0
    calsim_ok = abs(nmbe_pct) <= CALSIM_MONTHLY["nmbe_max_pct"] and (
        cv_pct <= CALSIM_MONTHLY["cvrmse_max_pct"]
    )
    rows = m["model_comparison"]
    cand = [r for r in rows if not r["label"].endswith(" F")]  # the fixed-base row is a reference
    bics = sorted(r["bic"] for r in cand)
    sav = {k: _savings(runs[k][1]["mv_savings"]) for k in ("raw", "injected")}
    rep = table[table["period"] == "reporting"]
    sav["injected_saving_exact"] = float((rep["energy"] - rep["energy_injected"]).sum())
    sav["method"] = "forecast (Option C), G14 kernel, 90% confidence"
    exp = {
        "id": case["id"],
        "data": "repo" if case["source"] == "synthetic" else "local",
        "source": case["source"],
        "unit": case["unit"],
        "note": case["note"],
        "inputs": dict(files),
        "config_entry": {k: v for k, v in entry.items() if k not in ("bills", "oat")},
        "estimated_reads": {
            "merged": m["estimated_merged"],
            "dropped": m["estimated_dropped"],
            "spans": [x for x in bills.merged],
        },
        "baseline": {
            "n_bills": m["n_bills"],
            "n_days": m["n_days"],
            "mean_bill_days": m["mean_bill_days"],
            "bills_dropped": m["bills_dropped"],
            "short_baseline": m["short_baseline"],
            "model": _model_block(model),
            "r2": m["r2"],
            "adj_r2": m["adj_r2"],
            "cvrmse_pct": cv_pct,
            "nmbe_pct": nmbe_pct,
            "n_params": m["n_params"],
            "rho": m["rho"],
            "hdd_total": m["hdd_total"],
            "cdd_total": m["cdd_total"],
        },
        "base_selection": {
            "kind": sel.get("kind"),
            "heating_base_f": sel.get("heating_base_f"),
            "cooling_base_f": sel.get("cooling_base_f"),
            "heating_range": sel.get("heating_range"),
            "cooling_range": sel.get("cooling_range"),
            "flat": sel.get("flat"),
            "at_edge": sel.get("at_edge"),
            "grid": sel.get("grid"),
            "candidates": sel.get("candidates"),
            "profiles": {
                leg: [
                    {k: r.get(k) for k in ("base_f", "valid", "other_base_f", "sse", "bic", "r2")}
                    for r in prof
                ]
                for leg, prof in (sel.get("profiles") or {}).items()
            },
        },
        "degree_days": _degree_days(fitted["base"], oat, model, sel.get("kind")),
        "model_comparison": {
            "criterion": "BIC = n ln(wSSE/n + 1e-12) + p ln(n), p counting change points and "
            "fitted bases; the 'fixed 65 F' row is shown for reference and is not a candidate",
            "selected": m["model"],
            "bic_gap": bics[1] - bics[0] if len(bics) > 1 else None,
            "rows": [
                {
                    "label": r["label"],
                    "kind": r["model"],
                    "n_params": r["p"],
                    "r2": r["r2"],
                    "adj_r2": r["adj_r2"],
                    "cvrmse_pct": r["cv_rmse"] * 100.0,
                    "nmbe_pct": round(r["nmbe"] * 100.0, 4),
                    "bic": r["bic"],
                    "baseline_gate": r["g14_accept"],
                    "selected": r["selected"],
                    "bases_or_change_points": (
                        [r.get("heating_base_f"), r.get("cooling_base_f")]
                        if "heating_base_f" in r
                        else r.get("change_points")
                    ),
                }
                for r in rows
            ],
            "fixed_base": {
                k: v for k, v in (m.get("fixed_base") or {}).items() if k not in ("model", "fitted")
            },
        },
        "baseline_gate": {
            "pass_": bool(m["accept"]),
            "interval": "monthly",
            "nmbe_max_pct": 0.5,
            "cvrmse_max_pct": 15.0,
            "r2_min": 0.75,
        },
        "calsim_gate": {"pass_": bool(calsim_ok), "tolerance_row": "monthly", **CALSIM_MONTHLY},
        "sep_validity": {
            "valid": sav["raw"]["sep_valid"],
            "failures": sav["raw"]["sep_failures"],
        },
        "calendarized": _calendarized(m["calendarized"]),
        "unit_cost": m.get("unit_cost"),
        "billed_cost_baseline": m.get("billed_cost"),
        "option_c": sav,
    }
    exp["predictions"] = _bill_predictions(case, fitted, model, entry, out)
    if case["source"] == "synthetic":
        exp["truth"] = case["truth"]
    exp["gates_disagree"] = exp["baseline_gate"]["pass_"] != exp["calsim_gate"]["pass_"]
    return _clean(exp)


def _bill_predictions(case: dict, fitted: dict, model, entry: dict, out: str) -> str:
    """CAMBER's predicted series for every bill it fitted or projected (estimated reads merged)."""
    from camber.mandv import _mvform

    f = fitted["frame"]
    per_day = np.asarray(model.predict(_mvform.design_rows(f, model)), dtype=float)
    p0, p1 = (pd.Timestamp(x) for x in entry["period"])
    last = f["end"] - pd.Timedelta(days=1)
    period = np.where((f["start"] >= p0) & (last <= p1), "baseline", "reporting")
    p = pd.DataFrame(
        {
            "start": f["start"].dt.strftime("%Y-%m-%d"),
            "end": last.dt.strftime("%Y-%m-%d"),
            "days": f["days"].astype(int),
            "period": period,
            "predicted_per_day": [_sig(v) for v in per_day],
            "predicted": [_sig(v) for v in per_day * f["days"].to_numpy(float)],
        }
    )
    rel = f"predictions/{case['id']}.csv"
    _write(p, os.path.join(out, rel))
    return rel


def overlap_expected(rel: str, out: str) -> dict:
    t = pd.read_csv(os.path.join(out, rel))
    f = pd.DataFrame(
        {
            "start": pd.to_datetime(t["start"]),
            "end": pd.to_datetime(t["end"]) + pd.Timedelta(days=1),  # printed end is inclusive
            "energy": t["energy"],
            "estimated": t["estimated"],
            "cost": t["cost"],
        }
    )
    views = {}
    for mg in (0, 10):
        cal = calendarize(f, max_gap_days=mg, units="kWh").as_dict()
        views[f"max_gap_days_{mg}"] = _calendarized(cal)
    return _clean(
        {
            "id": "calendarize_overlap",
            "input": rel,
            "note": "a plain bills table (not a BillingSeries, which refuses overlaps) with a "
            "7-day overlap, a 4-day gap and an estimated read; end dates are printed inclusive",
            **views,
        }
    )


def build_bill_expected(out: str, files: dict, local: str | None, previous: dict | None) -> dict:
    """The bill cases: synthetic from ``out``; BDG2 from ``local``, or kept from ``previous``
    (the committed ``expected.json``) when ``local`` is ``None``."""
    prev = {c["id"]: c for c in ((previous or {}).get("bills") or {}).get("cases", [])}
    cases = []
    for c in BILL_CASES:
        if c["source"] == "synthetic":
            cases.append(bill_case_expected(c, files[c["id"]], out))
        elif local is not None:
            cases.append(bill_case_expected(c, files[c["id"]], local))
        elif c["id"] in prev:
            cases.append(prev[c["id"]])
    return {
        "cases": cases,
        "calendarization": overlap_expected(files["calendarize_overlap"]["bills"], out),
    }
