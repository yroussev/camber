"""Regenerate the shared M&V test vectors (inputs and CAMBER's expected outputs).

Two tiers of data:

* **Synthetic** cases are drawn from seeded generators with exact truth parameters and live in the
  repository (``inputs/``, ``predictions/``).
* **BDG2** cases are rebuilt locally, never committed: CAMBER redistributes no datasets.
  ``fetch_bdg2.py`` (standalone) downloads the publisher's files, checks their sha256 pins and
  writes the derived CSVs to ``local/``; this script does the same from ``examples/_data/bdg2``
  when those files are present, and writes CAMBER's BDG2 predicted series to ``local/`` too.

Every input table is read back from its CSV and fitted with CAMBER (change-point selection,
Guideline 14 statistics, the three gates, Option C savings, the billing path) into
``expected.json``. A BDG2 case whose inputs are not available keeps its entry from the existing
``expected.json``.

Usage::

    python examples/mv_vectors/generate.py            # everything (BDG2 when on disk)
    python examples/mv_vectors/generate.py --offline  # synthetic only; BDG2 entries kept

``--out DIR`` writes to another directory (the regression test regenerates into a temporary one
and compares); ``--local DIR`` is where BDG2 inputs are read from and written to.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import fetch_bdg2
import gen_bills
import numpy as np
import pandas as pd
from fetch_bdg2 import REAL, calendar_months, periodize, write_csv
from gen_bills import _convention_free

from camber.mandv.models import N_PARAMS, best_model, fit_model
from camber.mandv.stats import (
    avoided_energy_savings,
    cv_rmse_max_for,
    fit_stats,
    logical_signs,
    model_regression_tests,
    sep_validity,
)

VERSION_LABEL = "0.98.0-dev"
SCHEMA = "mv_vectors/1"
KINDS = ("2P", "3PC", "3PH", "4P", "5P")
INJECTED_SAVING = fetch_bdg2.INJECTED_SAVING  # reporting energy x 0.90
CONFIDENCE = 0.90
BDG2 = os.path.join(ROOT, "examples", "_data", "bdg2")
LOCAL = fetch_bdg2.DEFAULT_OUT

# The calibrated-simulation tolerances of ASHRAE Guideline 14 (as open-fdd gates with them).
CALSIM = {"monthly": {"nmbe_max_pct": 5.0, "cvrmse_max_pct": 15.0}}
CALSIM["hourly"] = {"nmbe_max_pct": 10.0, "cvrmse_max_pct": 30.0}

# --------------------------------------------------------------------------- synthetic cases

# Truth in the convention-free form: ``base`` is the flat level (3P / 5P), the intercept at
# 0 F (2P) or the value at the change point (4P); ``slopes_dEdT`` are signed dE/dT per segment,
# left to right. ``noise_frac`` is the Gaussian noise sd as a fraction of the mean truth.
SYNTHETIC = [
    {
        "id": "syn_2p",
        "kind": "2P",
        "base": 120.0,
        "slopes_dEdT": [6.0],
        "change_points": [],
        "noise_frac": 0.04,
        "seed": 101,
        "recover": True,
        "note": "straight line, energy rising with temperature",
    },
    {
        "id": "syn_3ph_58",
        "kind": "3PH",
        "base": 400.0,
        "slopes_dEdT": [-15.0, 0.0],
        "change_points": [58.0],
        "noise_frac": 0.04,
        "seed": 111,
        "recover": True,
        "note": "heating below 58 F, flat above",
    },
    {
        "id": "syn_3pc_65",
        "kind": "3PC",
        "base": 300.0,
        "slopes_dEdT": [0.0, 20.0],
        "change_points": [65.0],
        "noise_frac": 0.04,
        "seed": 103,
        "recover": True,
        "note": "flat below 65 F, cooling above",
    },
    {
        "id": "syn_4p_60",
        "kind": "4P",
        "base": 500.0,
        "slopes_dEdT": [-8.0, 12.0],
        "change_points": [60.0],
        "noise_frac": 0.03,
        "seed": 104,
        "recover": True,
        "note": "two slopes meeting at 60 F, no dead-band",
    },
    {
        "id": "syn_5p_55_68",
        "kind": "5P",
        "base": 350.0,
        "slopes_dEdT": [-12.0, 0.0, 18.0],
        "change_points": [55.0, 68.0],
        "noise_frac": 0.04,
        "seed": 105,
        "recover": True,
        "note": "heating below 55 F, dead-band, cooling above 68 F",
    },
    {
        "id": "syn_3pc_noise_pass",
        "kind": "3PC",
        "base": 300.0,
        "slopes_dEdT": [0.0, 20.0],
        "change_points": [65.0],
        "noise_frac": 0.15,
        "seed": 106,
        "recover": False,
        "note": "3PC at 15% noise: should still pass the baseline gate",
    },
    {
        "id": "syn_3pc_noise_fail",
        "kind": "3PC",
        "base": 300.0,
        "slopes_dEdT": [0.0, 20.0],
        "change_points": [65.0],
        "noise_frac": 0.45,
        "seed": 107,
        "recover": False,
        "note": "3PC at 45% noise: should fail the baseline gate",
    },
    {
        "id": "syn_weak_weather",
        "kind": "2P",
        "base": 900.0,
        "slopes_dEdT": [0.6],
        "change_points": [],
        "noise_frac": 0.06,
        "seed": 108,
        "recover": False,
        "note": "a weak weather signal under low noise: passes the calibrated-simulation "
        "tolerances, fails the baseline gate on R2",
    },
]
SYN_YEARS = (2021, 2022)  # baseline, reporting (both 365 days)

# --------------------------------------------------------------------------- helpers


def _sig(x, digits: int = 7):
    """``x`` rounded to ``digits`` significant figures for JSON (``None`` for non-finite)."""
    if x is None:
        return None
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    x = float(x)
    if not math.isfinite(x):
        return None
    if x == 0.0:
        return 0.0
    out = float(f"{x:.{digits}g}")
    return 0.0 if out == 0.0 else out


_write_csv = write_csv


def _truth(case: dict, T: np.ndarray) -> np.ndarray:
    k, b, s, cp = case["kind"], case["base"], case["slopes_dEdT"], case["change_points"]
    if k == "2P":
        return b + s[0] * T
    if k == "3PH":
        return b - s[0] * np.maximum(0.0, cp[0] - T)
    if k == "3PC":
        return b + s[1] * np.maximum(0.0, T - cp[0])
    if k == "4P":
        return b + s[0] * np.minimum(0.0, T - cp[0]) + s[1] * np.maximum(0.0, T - cp[0])
    if k == "5P":
        return b - s[0] * np.maximum(0.0, cp[0] - T) + s[2] * np.maximum(0.0, T - cp[1])
    raise ValueError(k)


def synthetic_daily(case: dict) -> pd.DataFrame:
    """One synthetic case's daily table: two 365-day years, seeded OAT and noise."""
    rng = np.random.default_rng(case["seed"])
    frames = []
    for period, year in zip(("baseline", "reporting"), SYN_YEARS):
        days = pd.date_range(f"{year}-01-01", f"{year}-12-31", freq="D")
        doy = np.arange(len(days), dtype=float)
        oat = 58.0 + 24.0 * np.sin(2 * np.pi * (doy - 105.0) / 365.25) + rng.normal(0, 5, len(days))
        oat = np.round(oat, 1)
        mu = _truth(case, oat)
        sd = case["noise_frac"] * float(np.mean(_truth(case, oat)))
        energy = np.round(mu + rng.normal(0, sd, len(days)), 2)
        frames.append(
            pd.DataFrame(
                {
                    "date": days.strftime("%Y-%m-%d"),
                    "period": period,
                    "oat_f": oat,
                    "energy": energy,
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


# --------------------------------------------------------------------------- inputs


def build_inputs(out: str, *, offline: bool, local: str = LOCAL, source: str | None = None) -> dict:
    """Write the synthetic input CSVs under ``out`` and, when the BDG2 source files are available
    (``source``, else ``examples/_data/bdg2``; never with ``offline``), the BDG2 ones under
    ``local``. Returns ``{case_id: {interval: relpath}}`` plus ``"_bills"`` and ``"_local"``
    (the folder BDG2 inputs are read from, or ``None`` when they are not available)."""
    files: dict = {}
    for c in SYNTHETIC:
        daily = synthetic_daily(c)
        rel = f"inputs/synthetic/{c['id']}_daily.csv"
        _write_csv(daily, os.path.join(out, rel))
        files[c["id"]] = {"daily": rel}
        rel = f"inputs/synthetic/{c['id']}_monthly.csv"
        _write_csv(periodize(daily, calendar_months(SYN_YEARS)), os.path.join(out, rel))
        files[c["id"]]["monthly"] = rel
    files["_bills"] = gen_bills.build_bill_inputs(out)
    src = source or BDG2
    have_raw = not offline and all(
        os.path.exists(os.path.join(src, *n.split("/"))) for n in fetch_bdg2.SOURCES
    )
    files["_local"] = None
    if have_raw:
        fetch_bdg2.ensure_sources(source=src)
        bdg2 = fetch_bdg2.build(local, src)
        files["_local"] = local
    elif fetch_bdg2.available(local) and not fetch_bdg2.verify(local):
        bdg2 = fetch_bdg2.derive(local)
        files["_local"] = local
    if files["_local"] is not None:
        for c in REAL:
            files[c["id"]] = bdg2[c["id"]]
        for b in fetch_bdg2.BDG2_BILL_CASES:
            files["_bills"][b["id"]] = bdg2[b["id"]]
    return files


# --------------------------------------------------------------------------- expected outputs


def _model_block(m) -> dict:
    cf = _convention_free(m)
    return {
        "kind": m.kind,
        "n_params": N_PARAMS[m.kind],
        "change_points": [_sig(v) for v in m.change_points],
        "coeffs_camber": {k: _sig(v) for k, v in m.coeffs.items()},
        "base": _sig(cf["base"]),
        "slopes_dEdT": [_sig(v) for v in cf["slopes_dEdT"]],
        "fell_back_to_line": bool(m.kind == "5P" and not m.change_points),
    }


def _bic(sse: float, n: int, p: int) -> float:
    return float(n * np.log(sse / n + 1e-12) + p * np.log(n))


def _stats_block(st) -> dict:
    return {
        "n": st.n,
        "n_params": st.p,
        "r2": _sig(st.r2),
        "adj_r2": _sig(st.adj_r2),
        "cvrmse_pct": _sig(st.cv_rmse * 100.0),
        "nmbe_pct": _sig(st.nmbe * 100.0),
        "rmse": _sig(st.rmse),
        "f_stat": _sig(st.f_stat),
        "rho_lag1": _sig(st.rho_lag1),
    }


def _totals_stats(y_tot: np.ndarray, yhat_tot: np.ndarray, p: int) -> dict:
    """Guideline 14 statistics of period totals, unweighted (a monthly scorer's view)."""
    n = len(y_tot)
    r = y_tot - yhat_tot
    ybar = float(y_tot.mean())
    sse = float(r @ r)
    sst = float(((y_tot - ybar) ** 2).sum())
    r2 = 1.0 - sse / sst
    return {
        "n": n,
        "n_params": p,
        "r2": _sig(r2),
        "adj_r2": _sig(1.0 - (1.0 - r2) * (n - 1) / (n - p)),
        "cvrmse_pct": _sig(100.0 * math.sqrt(sse / (n - p)) / ybar),
        # 4 decimals of a percent, like CAMBER's own NMBE: a least-squares fit with an intercept
        # has a residual sum of zero up to rounding noise, which would not reproduce exactly
        "nmbe_pct": _sig(round(100.0 * float(r.sum()) / ((n - p) * ybar), 4)),
    }


def _calsim(nmbe_pct: float, cv_pct: float, row: str) -> dict:
    t = CALSIM[row]
    ok = abs(nmbe_pct) <= t["nmbe_max_pct"] and cv_pct <= t["cvrmse_max_pct"]
    notes = []
    if cv_pct > t["cvrmse_max_pct"]:
        notes.append(f"CV(RMSE) {cv_pct:.2f}% > {t['cvrmse_max_pct']:.0f}%")
    if abs(nmbe_pct) > t["nmbe_max_pct"]:
        notes.append(f"|NMBE| {abs(nmbe_pct):.2f}% > {t['nmbe_max_pct']:.0f}%")
    return {"pass_": bool(ok), "tolerance_row": row, **t, "notes": "; ".join(notes) or "pass"}


def _option_c(model, rep: pd.DataFrame, st, weighted: bool, scale: float) -> dict:
    T = rep["oat_f"].to_numpy(float)
    days = rep["days"].to_numpy(float) if weighted else None
    out = {}
    for label, f in (("raw", 1.0), ("injected", 1.0 - INJECTED_SAVING)):
        e = rep["energy"].to_numpy(float) * f
        y = e / days if weighted else e
        s = avoided_energy_savings(
            model,
            T,
            y,
            cv_rmse=st.cv_rmse,
            n_baseline=st.n,
            p_baseline=st.p,
            confidence=CONFIDENCE,
            rho=st.rho_lag1,
            days=days,
        )
        out[label] = {
            "savings_total": _sig(s.avoided_energy),
            "baseline_projected": _sig(s.baseline_projected),
            "reporting_actual": _sig(s.reporting_actual),
            "savings_fraction": _sig(s.savings_pct),
            "fsu": _sig(s.fractional_uncertainty),
            "abs_uncertainty": _sig(s.abs_uncertainty),
            "rho": _sig(s.rho),
            "n_report": int(len(rep)),
            "coverage_tier": (s.coverage or {}).get("tier"),
            "declined": bool(s.declined),
            "declined_reason": s.declined_reason,
        }
    out["confidence"] = CONFIDENCE
    out["kernel"] = "g14"
    out["baseline_kind"] = model.kind
    # injected - raw savings is exactly INJECTED_SAVING x the raw reporting total (model-free)
    out["injected_saving_exact"] = _sig(INJECTED_SAVING * scale)
    return out


def fit_interval(df: pd.DataFrame, interval: str, pred: tuple | None = None) -> dict:
    """CAMBER's expected output for one input table (``daily``, ``monthly`` or ``bills``)."""
    weighted = interval in ("monthly", "bills")
    base = df[df["period"] == "baseline"].reset_index(drop=True)
    rep = df[df["period"] == "reporting"].reset_index(drop=True)
    T = base["oat_f"].to_numpy(float)
    if weighted:
        w = base["days"].to_numpy(float)
        y = base["energy"].to_numpy(float) / w
        idx = pd.DatetimeIndex(pd.to_datetime(base["start"]))
    else:
        w = None
        y = base["energy"].to_numpy(float)
        idx = pd.DatetimeIndex(pd.to_datetime(base["date"]))
    cands = []
    for k in KINDS:
        m = fit_model(T, y, k, time_index=idx, weights=w)
        p = N_PARAMS[k]
        cands.append(
            {
                "kind": k,
                "n_params": p,
                "sse": _sig(m.sse),
                "bic": _sig(_bic(m.sse, m.n, p)),
                "change_points": [_sig(v) for v in m.change_points],
                "_bic": _bic(m.sse, m.n, p),
            }
        )
    best = best_model(T, y, time_index=idx, weights=w)
    order = sorted(cands, key=lambda c: c["_bic"])
    if order[0]["kind"] != best.kind:
        raise RuntimeError(f"BIC ranking disagrees with best_model: {order[0]['kind']} {best.kind}")
    gap = order[1]["_bic"] - order[0]["_bic"]
    for c in cands:
        c.pop("_bic")
    p = N_PARAMS[best.kind]
    g14_interval = "daily" if interval == "daily" else "monthly"
    st = fit_stats(
        y,
        best.predict(T),
        p,
        cv_rmse_max=cv_rmse_max_for(g14_interval),
        time_index=idx,
        weights=w,
    )
    tests = model_regression_tests(best, T, y, time_index=idx, weights=w)
    val = sep_validity(tests, signs=logical_signs(best))
    stats = _stats_block(st)
    ybar = float(np.average(y, weights=w))
    span = float(np.percentile(T, 95) - np.percentile(T, 5)) or 1.0
    out = {
        "n_baseline": int(len(base)),
        "coef_tolerance_floor": {
            "base": _sig(0.005 * abs(ybar)),
            "slope": _sig(0.005 * abs(ybar) / span),
        },
        "n_reporting": int(len(rep)),
        "y": "energy / days (energy per day), weighted by days" if weighted else "energy",
        "weights": "days" if weighted else None,
        "selection": {
            "criterion": "BIC = n ln(SSE/n + 1e-12) + p ln(n); lowest wins",
            "best": best.kind,
            "runner_up": order[1]["kind"],
            "bic_gap": _sig(gap),
            "candidates": cands,
        },
        "model": _model_block(best),
        "stats": stats,
        "baseline_gate": {
            "pass_": bool(st.accept),
            "nmbe_max_pct": 0.5,
            "cvrmse_max_pct": round(cv_rmse_max_for(g14_interval) * 100.0, 1),
            "r2_min": 0.75,
            "notes": st.notes,
        },
        "calsim_gate": _calsim(
            stats["nmbe_pct"], stats["cvrmse_pct"], "monthly" if weighted else "hourly"
        ),
        "sep_validity": {
            "valid": bool(val.sep_valid),
            "failures": list(val.failures),
            "valid_rho_adjusted": val.sep_valid_rho_adjusted,
            "r2_min": 0.50,
            "f_p": _sig(tests.f_p),
            "slope_p": {
                nm: _sig(pv) for nm, pv in zip(tests.names, tests.p) if nm in tests.relevant
            },
        },
    }
    out["gates_disagree"] = out["baseline_gate"]["pass_"] != out["calsim_gate"]["pass_"]
    if weighted:
        e_tot = base["energy"].to_numpy(float)
        out["stats_totals_unweighted"] = _totals_stats(e_tot, best.predict(T) * w, p)
    if len(rep):
        out["option_c"] = _option_c(best, rep, st, weighted, float(rep["energy"].sum()))
    if pred is not None:
        out["predictions"] = write_predictions(best, df, weighted, *pred)
    return out


def write_predictions(model, df: pd.DataFrame, weighted: bool, out: str, rel: str) -> str:
    """CAMBER's predicted series for every row (baseline and reporting) of one input table."""
    per_day = np.asarray(model.predict(df["oat_f"].to_numpy(float)), dtype=float)
    if weighted:
        p = pd.DataFrame(
            {
                "start": df["start"],
                "end": df["end"],
                "period": df["period"],
                "predicted_per_day": [_sig(v) for v in per_day],
                "predicted": [_sig(v) for v in per_day * df["days"].to_numpy(float)],
            }
        )
    else:
        p = pd.DataFrame(
            {"date": df["date"], "period": df["period"], "predicted": [_sig(v) for v in per_day]}
        )
    _write_csv(p, os.path.join(out, rel))
    return rel


def _recovery(case: dict, model_block: dict) -> dict:
    """Did the daily fit recover the synthetic truth (change point +/-2 F, rest +/-5%)?"""
    ok_kind = model_block["kind"] == case["kind"]
    cps = model_block["change_points"]
    ok_cp = ok_kind and all(abs(a - b) <= 2.0 for a, b in zip(cps, case["change_points"]))
    tb = [case["base"]] + case["slopes_dEdT"]
    gb = [model_block["base"]] + model_block["slopes_dEdT"]
    ok_coef = ok_kind and all(
        (t == 0.0 and g == 0.0) or (t != 0.0 and abs(g - t) <= 0.05 * abs(t))
        for t, g in zip(tb, gb)
    )
    return {"kind": ok_kind, "change_points": ok_cp, "coefficients": ok_coef}


def _commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:  # noqa: BLE001 - no git: say so
        return "unknown"


METHOD = {
    "change_point_search": (
        "grid of 40 evenly spaced candidates from the 5th to the 95th percentile of the baseline "
        "OAT (numpy.percentile, linear interpolation; numpy.linspace); at each candidate the "
        "slopes and intercept are solved by least squares and the candidate with the lowest SSE "
        "is kept. 5P searches every pair (lo, hi) on the same grid with hi - lo >= one grid step "
        "(no zero-width dead-band); if no pair qualifies it falls back to a 2P line, still "
        "labelled 5P"
    ),
    "selection": (
        "fit 2P, 3PC, 3PH, 4P and 5P; keep the lowest BIC = n ln(SSE/n + 1e-12) + p ln(n) with p "
        "from n_params below (ties keep the earlier kind in that order)"
    ),
    "n_params": (
        "p counts the least-squares coefficients plus every grid-searched change point: 2P 2, "
        "3PC 3, 3PH 3, 4P 4, 5P 5. The same p is used in the BIC, CV(RMSE) and NMBE (n - p), "
        "adjusted R2 and the F-test"
    ),
    "day_weighting": (
        "monthly and bill tables are fitted as energy per day (energy / days) by weighted least "
        "squares with weight = days (normalised to mean 1). SSE, SST, the mean and the residual "
        "sum are day-weighted, so CV(RMSE) = sqrt(wSSE / (n - p)) / weighted mean and NMBE = "
        "weighted residual sum / ((n - p) x weighted mean); n is the number of rows. Savings "
        "multiply each row's per-day projection back by its days. Daily tables are unweighted"
    ),
    "statistics": (
        "R2 = 1 - SSE/SST; adj R2 = 1 - (1 - R2)(n - 1)/(n - p); CV(RMSE) = sqrt(SSE/(n - p)) / "
        "mean(y); NMBE = sum(y - yhat) / ((n - p) mean(y)); CAMBER rounds R2 and CV(RMSE) to "
        "4 decimals and NMBE to 5 (as fractions) before the gates"
    ),
    "option_c": (
        "avoided energy = sum(projected baseline) - sum(actual) over the reporting rows; FSU is "
        "the ASHRAE Guideline 14 Annex B form t x 1.26 x CV x sqrt((n/n') (1 + 2/n) / m) / F at "
        "90% confidence with t on n - p degrees of freedom (CAMBER's table, rounded down), "
        "n' = n(1 - rho)/(1 + rho) from the baseline residuals' lag-1 autocorrelation when it "
        "can be estimated (>= 30 adjacent pairs), else rho = 0"
    ),
    "injected_saving": (
        "the 'injected' reporting energy is the CSV energy x 0.90 in full precision (no "
        "rounding); 'raw' is the CSV energy as is"
    ),
}


def load_previous(path: str = os.path.join(HERE, "expected.json")) -> dict | None:
    """The committed ``expected.json`` (BDG2 entries are kept from it when their inputs are not
    on disk), or ``None``."""
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def build_expected(out: str, files: dict, previous: dict | None = None) -> dict:
    files = dict(files)
    bill_files = files.pop("_bills", None)
    local = files.pop("_local", None)
    if previous is None:
        previous = load_previous()
    prev = {c["id"]: c for c in (previous or {}).get("cases", [])}
    cases = []
    for c in SYNTHETIC + REAL:
        synthetic = c in SYNTHETIC
        if not synthetic and local is None:
            if c["id"] in prev:
                cases.append(prev[c["id"]])
            continue
        root = out if synthetic else local
        fits = {}
        for interval, rel in files[c["id"]].items():
            df = pd.read_csv(os.path.join(root, rel))
            prel = f"predictions/{c['id']}_{interval}.csv"
            fits[interval] = {"input": rel, **fit_interval(df, interval, (root, prel))}
        entry = {
            "id": c["id"],
            "data": "repo" if synthetic else "local",
            "source": "synthetic" if synthetic else "bdg2",
            "fuel": "synthetic" if synthetic else c["fuel"],
            "unit": "kWh" if synthetic else c["unit"],
            "note": c["note"],
        }
        if synthetic:
            entry["truth"] = {
                "kind": c["kind"],
                "base": c["base"],
                "slopes_dEdT": c["slopes_dEdT"],
                "change_points": c["change_points"],
                "noise_sd_fraction_of_mean": c["noise_frac"],
                "seed": c["seed"],
                "recovery_expected": c["recover"],
            }
            fits["daily"]["truth_recovery"] = _recovery(c, fits["daily"]["model"])
        else:
            entry["bdg2"] = {"building_id": c["building"], "meter": c["meter"]}
        entry["fits"] = fits
        cases.append(entry)
    return {
        "schema": SCHEMA,
        "generator": {
            "camber_version": f"{VERSION_LABEL} at {_commit()}",
            "script": "examples/mv_vectors/generate.py",
        },
        "method": METHOD,
        "gates": {
            "baseline": {
                "what": "CAMBER's regression-baseline acceptance (fit_stats.accept)",
                "nmbe_max_pct": 0.5,
                "cvrmse_max_pct": {"daily": 30.0, "monthly": 15.0, "bills": 15.0},
                "r2_min": 0.75,
            },
            "calibrated_simulation": {
                "what": "Guideline 14 calibrated-simulation tolerances; daily tables are judged "
                "on the hourly row (the standard has no daily row)",
                **CALSIM,
            },
            "sep": {
                "what": "DOE SEP 50001 M&V Protocol model validity (2019 Ed. 2, 6.4.1): F-test "
                "p < 0.10, every slope p < 0.20, one slope p < 0.10, R2 >= 0.50, physical slope "
                "signs",
            },
        },
        "cases": cases,
        "local_data": {
            "what": "the BDG2 cases' inputs and predicted series are not in the repository "
            "(CAMBER redistributes no datasets); fetch_bdg2.py rebuilds them from the publisher's "
            "files, sha256-verified, into a local folder (default local/)",
            "fetch": "examples/mv_vectors/fetch_bdg2.py",
            "default_dir": "local",
            "licence": "CC-BY-SA-4.0",
            "citation": fetch_bdg2.CITATION,
        },
        "bills": None
        if bill_files is None
        else gen_bills.build_bill_expected(out, bill_files, local, previous),
    }


def build_example(exp: dict, out: str) -> dict:
    """A tiny results file in the contract's shape, made from CAMBER's own outputs.

    One monthly fit is forced to its runner-up kind (3PH, within 2 BIC of CAMBER's 4P) to show
    how the checker reports a legitimate kind disagreement as a NOTE.
    """
    by = {c["id"]: c for c in exp["cases"]}
    res: dict = {}
    # 1. a daily fit, summary statistics and savings only
    f = by["bdg2_hog_office_napoleon_elec"]["fits"]["daily"]
    res["bdg2_hog_office_napoleon_elec"] = {
        "daily": {
            "kind": f["model"]["kind"],
            "n_params": f["model"]["n_params"],
            "base": f["model"]["base"],
            "slopes_dEdT": f["model"]["slopes_dEdT"],
            "r2": f["stats"]["r2"],
            "cvrmse_pct": f["stats"]["cvrmse_pct"],
            "nmbe_pct": f["stats"]["nmbe_pct"],
            "pass_calsim": f["calsim_gate"]["pass_"],
            "pass_baseline": f["baseline_gate"]["pass_"],
            "savings_total": {k: f["option_c"][k]["savings_total"] for k in ("raw", "injected")},
        }
    }
    # 2. a monthly fit of totals with its predicted series, at the runner-up kind
    rel = by["syn_3ph_58"]["fits"]["monthly"]["input"]
    df = pd.read_csv(os.path.join(out, rel))
    base = df[df["period"] == "baseline"]
    rep = df[df["period"] == "reporting"]
    d = base["days"].to_numpy(float)
    m = fit_model(
        base["oat_f"].to_numpy(float), base["energy"].to_numpy(float) / d, "3PH", weights=d
    )
    cf = _convention_free(m)
    proj = float((m.predict(rep["oat_f"].to_numpy(float)) * rep["days"].to_numpy(float)).sum())
    act = float(rep["energy"].sum())
    res["syn_3ph_58"] = {
        "monthly": {
            "kind": "3PH",
            "n_params": 3,
            "change_points": [_sig(v) for v in m.change_points],
            "base": _sig(cf["base"]),
            "slopes_dEdT": [_sig(v) for v in cf["slopes_dEdT"]],
            "y": "totals",
            "predictions": [_sig(v) for v in m.predict(base["oat_f"].to_numpy(float)) * d],
            "savings_total": {"raw": _sig(proj - act), "injected": _sig(proj - 0.9 * act)},
        }
    }
    out_ = {
        "schema": SCHEMA,
        "implementation": "example: CAMBER's own outputs, one monthly fit forced to its "
        "runner-up kind",
        "results": res,
    }
    if exp.get("bills"):
        b = exp["bills"]["cases"][0]
        out_["bills"] = {
            b["id"]: {
                "kind": b["baseline"]["model"]["kind"],
                "heating_base_f": b["base_selection"]["heating_base_f"],
                "cooling_base_f": b["base_selection"]["cooling_base_f"],
                "r2": b["baseline"]["r2"],
                "cvrmse_pct": b["baseline"]["cvrmse_pct"],
                "savings_total": {
                    k: b["option_c"][k]["savings_total"] for k in ("raw", "injected")
                },
                "avoided_cost": {k: b["option_c"][k]["avoided_cost"] for k in ("raw", "injected")},
                "calendarized": {r["month"]: r["energy"] for r in b["calendarized"]["months"][:3]},
            }
        }
    return out_


def _dump(obj, path: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, indent=1, sort_keys=False)
        fh.write("\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=HERE, help="output directory (default: this folder)")
    ap.add_argument("--offline", action="store_true", help="never read the raw BDG2 files")
    ap.add_argument("--local", default=LOCAL, help="the BDG2 folder (default: ./local)")
    ap.add_argument("--source", default=None, help="the BDG2 source files (default: _data)")
    a = ap.parse_args(argv)
    files = build_inputs(a.out, offline=a.offline, local=a.local, source=a.source)
    exp = build_expected(a.out, files)
    _dump(exp, os.path.join(a.out, "expected.json"))
    _dump(build_example(exp, a.out), os.path.join(a.out, "example_results.json"))
    print(f"wrote {len(exp['cases'])} cases to {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
