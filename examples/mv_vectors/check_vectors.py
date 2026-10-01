"""Check change-point / Guideline 14 results against the shared M&V test vectors.

Standalone: needs numpy and pandas only (no CAMBER install). Three modes::

    python check_vectors.py --self-test          # rebuild CAMBER's expected numbers from the
                                                 # inputs and the stored coefficients
    python check_vectors.py results.json         # compare another implementation's results
    python check_vectors.py --template > r.json  # CAMBER's expected outputs in the results format

The results format, the field definitions and the tolerances are in ``SCHEMA.md``. Exit status is
1 when any check fails. Kind disagreements are reported as a NOTE (not a failure) when the two
best BIC values in ``expected.json`` are within ``KIND_CLOSE_BIC``.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))

# Comparison tolerances (SCHEMA.md, "Tolerances")
TOL = {
    "change_point_f": 2.0,  # +/- degF per change point (grid differences)
    "coef_rel": 0.05,  # +/- 5% of the expected base and slopes ...
    "coef_floor_frac": 0.005,  # ... or 0.5% of mean y (base) / 0.5% of mean y over the OAT span
    "r2_abs": 0.01,
    "cvrmse_pct_abs": 0.5,  # percentage points
    "nmbe_pct_abs": 0.1,  # percentage points
    "savings_frac_of_baseline": 0.01,  # +/- 1% of the expected baseline projection
    "self_consistency": 1e-3,  # a result's stats vs the stats of its own predictions
    "prediction_row_frac": 0.02,  # row-by-row vs CAMBER's predictions: informational only
}
SCHEMA = "mv_vectors/1"
KIND_CLOSE_BIC = 2.0

# Self-test tolerances: the stored coefficients carry 7 significant figures and CAMBER rounds
# R2 / CV(RMSE) to 4 decimals and NMBE to 5 (as fractions).
SELF = {"r2": 2e-4, "adj_r2": 2e-4, "cvrmse_pct": 0.02, "nmbe_pct": 0.002, "savings_rel": 1e-5}


# --------------------------------------------------------------------------- the model and stats


def predict(T, change_points, base, slopes_dEdT):
    """Convention-free change-point prediction.

    No change point: ``base + s0*T``. One (3PC / 3PH / 4P): ``base + sL*min(0, T - cp) +
    sR*max(0, T - cp)``. Two (5P): ``base + s0*min(0, T - lo) + s2*max(0, T - hi)`` (the middle
    slope is the flat dead-band).
    """
    T = np.asarray(T, dtype=float)
    cps = list(change_points or [])
    s = list(slopes_dEdT)
    if not cps:
        return base + s[0] * T
    if len(cps) == 1:
        c = cps[0]
        return base + s[0] * np.minimum(0.0, T - c) + s[1] * np.maximum(0.0, T - c)
    lo, hi = cps
    return base + s[0] * np.minimum(0.0, T - lo) + s[-1] * np.maximum(0.0, T - hi)


def g14_stats(y, yhat, p, weights=None) -> dict:
    """R2, adjusted R2, CV(RMSE) and NMBE (percent) with ``n - p`` degrees of freedom.

    Residuals are measured minus predicted. With ``weights`` (day counts) every sum is weighted
    and the weights are normalised to mean 1, as CAMBER fits monthly and bill tables.
    """
    y = np.asarray(y, dtype=float)
    yhat = np.asarray(yhat, dtype=float)
    n = len(y)
    w = np.ones(n) if weights is None else np.asarray(weights, dtype=float)
    w = w / w.mean()
    r = y - yhat
    ybar = float((w * y).sum() / w.sum())
    sse = float((w * r * r).sum())
    sst = float((w * (y - ybar) ** 2).sum())
    r2 = 1.0 - sse / sst if sst > 0 else float("nan")
    return {
        "n": n,
        "n_params": int(p),
        "r2": r2,
        "adj_r2": 1.0 - (1.0 - r2) * (n - 1) / (n - p),
        "cvrmse_pct": 100.0 * math.sqrt(sse / (n - p)) / ybar,
        "nmbe_pct": 100.0 * float((w * r).sum()) / ((n - p) * ybar),
    }


def load_expected(path=None) -> dict:
    with open(path or os.path.join(HERE, "expected.json"), encoding="utf-8") as fh:
        return json.load(fh)


def case_root(case: dict, root: str = HERE, local: str | None = None) -> str:
    """Where a case's files are: the vectors folder (``data: repo``) or the local BDG2 folder
    (``data: local``, written by ``fetch_bdg2.py``; default ``<root>/local``)."""
    if case.get("data", "repo") == "repo":
        return root
    return local or os.path.join(root, "local")


def has_inputs(case: dict, root: str = HERE, local: str | None = None) -> bool:
    """True when a case's input files are on disk (BDG2 cases need ``fetch_bdg2.py`` first)."""
    r = case_root(case, root, local)
    if "fits" in case:
        rels = [f["input"] for f in case["fits"].values()]
    else:
        rels = list(case["inputs"].values())
    return all(os.path.exists(os.path.join(r, rel)) for rel in rels)


def missing_local(exp: dict, root: str = HERE, local: str | None = None) -> list:
    """Ids of the cases whose inputs are not on disk."""
    cases = list(exp["cases"]) + list((exp.get("bills") or {}).get("cases", []))
    return [c["id"] for c in cases if not has_inputs(c, root, local)]


def _frames(fit: dict, root: str):
    df = pd.read_csv(os.path.join(root, fit["input"]))
    return df[df["period"] == "baseline"], df[df["period"] == "reporting"]


def _xyw(frame: pd.DataFrame, weighted: bool):
    T = frame["oat_f"].to_numpy(float)
    e = frame["energy"].to_numpy(float)
    if weighted:
        d = frame["days"].to_numpy(float)
        return T, e / d, d
    return T, e, None


# --------------------------------------------------------------------------- self-test


def self_test(exp: dict, root: str = HERE, local: str | None = None) -> list:
    """Rebuild every expected statistic and saving from the inputs and stored coefficients.

    Cases whose inputs are not on disk (BDG2 before ``fetch_bdg2.py``) are skipped; see
    :func:`missing_local`."""
    fails = []
    home = root
    for case in exp["cases"]:
        if not has_inputs(case, home, local):
            continue
        root = case_root(case, home, local)
        for interval, fit in case["fits"].items():
            tag = f"{case['id']}/{interval}"
            weighted = fit["weights"] == "days"
            base, rep = _frames(fit, root)
            T, y, w = _xyw(base, weighted)
            m = fit["model"]
            yhat = predict(T, m["change_points"], m["base"], m["slopes_dEdT"])
            st = g14_stats(y, yhat, m["n_params"], w)
            e = fit["stats"]
            for k, tol in (("r2", "r2"), ("adj_r2", "adj_r2"), ("cvrmse_pct", "cvrmse_pct")):
                if abs(st[k] - e[k]) > SELF[tol]:
                    fails.append(f"{tag}: {k} {st[k]:.6g} != expected {e[k]}")
            if abs(st["nmbe_pct"] - e["nmbe_pct"]) > SELF["nmbe_pct"]:
                fails.append(f"{tag}: nmbe_pct {st['nmbe_pct']:.6g} != expected {e['nmbe_pct']}")
            if st["n"] != e["n"]:
                fails.append(f"{tag}: n {st['n']} != expected {e['n']}")
            g = fit["baseline_gate"]
            ok = (
                e["r2"] >= g["r2_min"]
                and abs(e["nmbe_pct"]) <= g["nmbe_max_pct"]
                and e["cvrmse_pct"] <= g["cvrmse_max_pct"]
            )
            if ok != g["pass_"]:
                fails.append(f"{tag}: baseline gate verdict does not follow from the stats")
            c = fit["calsim_gate"]
            ok = abs(e["nmbe_pct"]) <= c["nmbe_max_pct"] and e["cvrmse_pct"] <= c["cvrmse_max_pct"]
            if ok != c["pass_"]:
                fails.append(f"{tag}: calibrated-simulation verdict does not follow from the stats")
            if fit.get("predictions"):
                fails += _prediction_fails(tag, fit, root, weighted)
            if "stats_totals_unweighted" in fit:
                tot = g14_stats(base["energy"].to_numpy(float), yhat * w, m["n_params"])
                for k in ("r2", "cvrmse_pct"):
                    if abs(tot[k] - fit["stats_totals_unweighted"][k]) > SELF[k]:
                        fails.append(f"{tag}: totals {k} {tot[k]:.6g} differs")
            oc = fit.get("option_c")
            if oc:
                Tr, _, dr = _xyw(rep, weighted)
                proj = predict(Tr, m["change_points"], m["base"], m["slopes_dEdT"])
                proj_tot = float((proj * dr).sum() if weighted else proj.sum())
                act = float(rep["energy"].sum())
                for label, f in (("raw", 1.0), ("injected", 0.9)):
                    s = oc[label]
                    if s["declined"]:
                        continue
                    got = proj_tot - f * act
                    if abs(got - s["savings_total"]) > SELF["savings_rel"] * abs(proj_tot):
                        fails.append(f"{tag}: {label} savings {got:.6g} != {s['savings_total']}")
                diff = oc["injected"]["savings_total"] - oc["raw"]["savings_total"]
                if abs(diff - oc["injected_saving_exact"]) > SELF["savings_rel"] * abs(proj_tot):
                    fails.append(f"{tag}: injected - raw savings {diff:.6g} is not 10% of actual")
    return fails + bills_self_test(exp, home, local)


def _prediction_fails(tag: str, fit: dict, root: str, weighted: bool) -> list:
    """CAMBER's predicted series file matches the stored model on every input row."""
    if not os.path.exists(os.path.join(root, fit["predictions"])):
        return []  # a BDG2 series is written locally, and only on request
    df = pd.read_csv(os.path.join(root, fit["input"]))
    pr = pd.read_csv(os.path.join(root, fit["predictions"]))
    m = fit["model"]
    got = predict(df["oat_f"].to_numpy(float), m["change_points"], m["base"], m["slopes_dEdT"])
    if weighted:
        got = got * df["days"].to_numpy(float)
    key = "start" if weighted else "date"
    if len(pr) != len(df) or not (pr[key].to_numpy() == df[key].to_numpy()).all():
        return [f"{tag}: predictions rows do not match the input rows"]
    exp = pr["predicted"].to_numpy(float)
    # the stored coefficients carry 7 significant figures
    bad = np.abs(got - exp) > 1e-5 * max(1.0, float(np.mean(np.abs(exp))))
    return [f"{tag}: {int(bad.sum())} predicted values differ from the model"] if bad.any() else []


def fit_predictions(fit: dict, root: str) -> pd.DataFrame:
    """CAMBER's predicted series of one fit, rebuilt from its stored coefficients."""
    df = pd.read_csv(os.path.join(root, fit["input"]))
    m = fit["model"]
    per_day = predict(df["oat_f"].to_numpy(float), m["change_points"], m["base"], m["slopes_dEdT"])
    if fit["weights"] == "days":
        out = df[["start", "end", "period"]].copy()
        out["predicted_per_day"] = per_day
        out["predicted"] = per_day * df["days"].to_numpy(float)
        return out
    out = df[["date", "period"]].copy()
    out["predicted"] = per_day
    return out


def camber_predictions(fit: dict, root: str = HERE, period: str | None = "baseline") -> np.ndarray:
    """CAMBER's predicted series of one fit (period totals for period tables): the file when it is
    on disk, else rebuilt from the stored coefficients."""
    path = os.path.join(root, fit["predictions"])
    pr = pd.read_csv(path) if os.path.exists(path) else fit_predictions(fit, root)
    if period is not None:
        pr = pr[pr["period"] == period]
    return pr["predicted"].to_numpy(float)


# --------------------------------------------------------------------------- comparison


def _coef_floor(fit: dict) -> tuple:
    """0.5% of mean y (base) and 0.5% of mean y over the 5th-95th percentile OAT span (slopes),
    stored with each fit so no input file is needed."""
    f = fit["coef_tolerance_floor"]
    return f["base"], f["slope"]


def _bic_gap_to(sel: dict, kind) -> float:
    """How far CAMBER's BIC for ``kind`` sits above its best (inf for an unknown kind)."""
    bics = {c["kind"]: c["bic"] for c in sel["candidates"]}
    if kind not in bics:
        return float("inf")
    return bics[kind] - min(bics.values())


def compare(exp: dict, results: dict, root: str = HERE, local: str | None = None) -> tuple:
    """``(lines, n_fail)``: one line per check, each PASS / NOTE / FAIL."""
    lines: list = []
    n_fail = 0
    by_id = {c["id"]: c for c in exp["cases"]}
    res = results.get("results", {})

    def out(status, tag, msg):
        nonlocal n_fail
        n_fail += status == "FAIL"
        lines.append(f"{status:4s} {tag}: {msg}")

    if results.get("schema", SCHEMA) != SCHEMA:
        out("FAIL", "schema", f"results schema {results.get('schema')!r}, expected {SCHEMA!r}")
    if exp.get("schema") != SCHEMA:
        out("FAIL", "schema", f"expected.json schema {exp.get('schema')!r}, expected {SCHEMA!r}")

    for cid, intervals in res.items():
        if cid not in by_id:
            out("FAIL", cid, "unknown case id")
            continue
        for interval, r in intervals.items():
            tag = f"{cid}/{interval}"
            fit = by_id[cid]["fits"].get(interval)
            if fit is None:
                out("FAIL", tag, "unknown interval")
                continue
            weighted = fit["weights"] == "days"
            m, sel = fit["model"], fit["selection"]
            here = has_inputs(by_id[cid], root, local)
            croot = case_root(by_id[cid], root, local)
            same_kind = r.get("kind") == m["kind"]
            if "kind" in r:
                if same_kind:
                    out("PASS", tag, f"kind {m['kind']} (BIC gap {sel['bic_gap']:.2f})")
                elif _bic_gap_to(sel, r.get("kind")) < KIND_CLOSE_BIC:
                    out(
                        "NOTE",
                        tag,
                        f"kind {r['kind']} vs CAMBER {m['kind']}: CAMBER's BIC for {r['kind']} is "
                        f"{_bic_gap_to(sel, r['kind']):.2f} above the best (< {KIND_CLOSE_BIC:g}), "
                        "a legitimate disagreement",
                    )
                else:
                    out(
                        "FAIL",
                        tag,
                        f"kind {r['kind']} vs CAMBER {m['kind']} (BIC gap {sel['bic_gap']:.2f}; "
                        f"runner-up {sel['runner_up']})",
                    )
            per_day = r.get("y", "per_day" if weighted else "energy") != "totals"
            if same_kind and "change_points" in r:
                for a, b in zip(r["change_points"], m["change_points"]):
                    ok = abs(a - b) <= TOL["change_point_f"]
                    out("PASS" if ok else "FAIL", tag, f"change point {a:.2f} vs {b:.2f} F")
            if same_kind and per_day and ("base" in r or "slopes_dEdT" in r):
                fb, fs = _coef_floor(fit)
                pairs = [("base", r.get("base"), m["base"], fb)]
                for i, (a, b) in enumerate(zip(r.get("slopes_dEdT", []), m["slopes_dEdT"])):
                    pairs.append((f"slope[{i}]", a, b, fs))
                for name, a, b, floor in pairs:
                    if a is None:
                        continue
                    tol = max(TOL["coef_rel"] * abs(b), floor)
                    ok = abs(a - b) <= tol
                    out("PASS" if ok else "FAIL", tag, f"{name} {a:.6g} vs {b:.6g} (+/-{tol:.3g})")
            ref = fit["stats"] if per_day else fit.get("stats_totals_unweighted", fit["stats"])
            if "predictions" in r and not here:
                out(
                    "NOTE",
                    tag,
                    "inputs not on disk (run fetch_bdg2.py): statistics not recomputed from "
                    "the predictions",
                )
            elif "predictions" in r:
                base, _ = _frames(fit, croot)
                T, y, w = _xyw(base, weighted)
                pred = np.asarray(r["predictions"], dtype=float)
                if len(pred) != len(base):
                    out("FAIL", tag, f"{len(pred)} predictions for {len(base)} baseline rows")
                else:
                    if fit.get("predictions"):
                        cp = camber_predictions(fit, croot)
                        dev = float(np.max(np.abs(pred - cp)) / np.mean(np.abs(cp)))
                        ok = dev <= TOL["prediction_row_frac"]
                        out(
                            "PASS" if ok else "NOTE",
                            tag,
                            f"predictions row by row: max deviation {dev:.2%} of the mean "
                            "(informational)",
                        )
                    p = int(r.get("n_params", m["n_params"]))
                    if per_day:
                        own = g14_stats(y, pred / w if weighted else pred, p, w)
                    else:
                        own = g14_stats(base["energy"].to_numpy(float), pred, p)
                    for k in ("r2", "cvrmse_pct", "nmbe_pct"):
                        if k in r and abs(own[k] - r[k]) > TOL["self_consistency"] * max(
                            1.0, abs(own[k])
                        ):
                            out(
                                "FAIL",
                                tag,
                                f"reported {k} {r[k]} != {own[k]:.6g} from its own predictions",
                            )
                    r = {**own, **{k: v for k, v in r.items() if k not in own}}
            for k, tk in (("r2", "r2_abs"), ("cvrmse_pct", "cvrmse_pct_abs")):
                if k in r:
                    ok = abs(r[k] - ref[k]) <= TOL[tk]
                    out("PASS" if ok else "FAIL", tag, f"{k} {r[k]:.4g} vs {ref[k]:.4g}")
            if "nmbe_pct" in r:
                ok = abs(r["nmbe_pct"] - ref["nmbe_pct"]) <= TOL["nmbe_pct_abs"]
                out(
                    "PASS" if ok else "FAIL",
                    tag,
                    f"nmbe_pct {r['nmbe_pct']:.4g} vs {ref['nmbe_pct']}",
                )
            for key, gate in (("pass_calsim", "calsim_gate"), ("pass_baseline", "baseline_gate")):
                if key in r:
                    ok = bool(r[key]) == fit[gate]["pass_"]
                    out("PASS" if ok else "FAIL", tag, f"{key} {r[key]} vs {fit[gate]['pass_']}")
            oc = fit.get("option_c")
            for label, val in (r.get("savings_total") or {}).items():
                if not oc or label not in ("raw", "injected"):
                    out("FAIL", tag, f"no expected savings '{label}'")
                    continue
                e = oc[label]
                if e["declined"]:
                    out("NOTE", tag, f"savings {label}: CAMBER declined ({e['declined_reason']})")
                    continue
                tol = TOL["savings_frac_of_baseline"] * abs(e["baseline_projected"])
                ok = abs(val - e["savings_total"]) <= tol
                out(
                    "PASS" if ok else "FAIL",
                    tag,
                    f"savings {label} {val:.6g} vs {e['savings_total']:.6g} (+/-{tol:.3g})",
                )
    bills_compare(exp, results.get("bills") or {}, out)
    return lines, n_fail


# --------------------------------------------------------------------------- bills

BILL_TOL = {
    "base_f": 2.0,  # +/- degF per selected degree-day base or change point
    "calendar_rel": 1e-3,  # calendarized month energy / cost, relative
    "cost_frac_of_baseline": 0.01,  # avoided cost: +/- 1% of the baseline projection's cost
}


def read_bills(path: str, energy: str = "energy", cost: str = "cost") -> pd.DataFrame:
    """Bills with ``start``, exclusive ``end``, ``days``, ``energy``, ``cost``, ``estimated``."""
    t = pd.read_csv(path)
    f = pd.DataFrame(
        {
            "start": pd.to_datetime(t["start"]),
            "end": pd.to_datetime(t["end"]) + pd.Timedelta(days=1),  # printed end is inclusive
            "energy": t[energy].astype(float),
            "cost": t[cost].astype(float),
            "estimated": t["estimated"].astype(str).str.lower().isin(["true", "1", "yes", "e"]),
        }
    )
    f["days"] = (f["end"] - f["start"]).dt.days
    return f


def merge_estimated(f: pd.DataFrame) -> tuple:
    """Each run of estimated bills joins the next contiguous actual bill; an estimate no actual
    read follows is dropped. Returns ``(bills, spans)``."""
    rows, spans, run = [], [], []
    for r in f.itertuples(index=False):
        if run and r.start != run[-1].end:
            spans += [{"action": "dropped"} for _ in run]
            run = []
        if r.estimated:
            run.append(r)
            continue
        if run:
            rows.append(
                (
                    run[0].start,
                    r.end,
                    sum(x.energy for x in run) + r.energy,
                    sum(x.cost for x in run) + r.cost,
                )
            )
            spans.append(
                {
                    "action": "merged",
                    "start": str(run[0].start.date()),
                    "end": str((r.end - pd.Timedelta(days=1)).date()),
                    "n_estimated": len(run),
                }
            )
            run = []
        else:
            rows.append((r.start, r.end, r.energy, r.cost))
    spans += [{"action": "dropped"} for _ in run]
    out = pd.DataFrame(rows, columns=["start", "end", "energy", "cost"])
    out["days"] = (out["end"] - out["start"]).dt.days
    return out, spans


def read_oat(path: str) -> pd.Series:
    t = pd.read_csv(path)
    return pd.Series(t["oat_f"].to_numpy(float), index=pd.to_datetime(t["date"]))


def bill_weather(bills: pd.DataFrame, oat: pd.Series, hb, cb) -> pd.DataFrame:
    """Each bill's mean OAT and its HDD / CDD per day built from each day's OAT (the mean over the
    days that have one), at bases ``hb`` / ``cb`` (``None`` skips a leg)."""
    out = []
    for r in bills.itertuples(index=False):
        t = oat.loc[r.start : r.end - pd.Timedelta(days=1)].to_numpy(float)
        out.append(
            {
                "oat": t.mean(),
                "coverage": len(t) / r.days,
                "hdd_day": np.maximum(0.0, hb - t).mean() if hb is not None else np.nan,
                "cdd_day": np.maximum(0.0, t - cb).mean() if cb is not None else np.nan,
            }
        )
    return pd.DataFrame(out, index=bills.index)


def bill_predict(model: dict, w: pd.DataFrame) -> np.ndarray:
    """Energy per day of each bill from a bill case's ``baseline.model``."""
    if model["family"] == "degree_day":
        y = np.full(len(w), float(model["intercept_per_day"]))
        if model.get("heating_slope") is not None:
            y = y + model["heating_slope"] * w["hdd_day"].to_numpy(float)
        if model.get("cooling_slope") is not None:
            y = y + model["cooling_slope"] * w["cdd_day"].to_numpy(float)
        return y
    return predict(
        w["oat"].to_numpy(float), model["change_points"], model["base"], model["slopes_dEdT"]
    )


def calendar_months(bills: pd.DataFrame, est_spans=()) -> pd.DataFrame:
    """Portfolio Manager proration: each bill's energy and cost per day, each day to its month."""
    lo, hi = bills["start"].min(), bills["end"].max()
    m0 = lo.to_period("M").to_timestamp()
    m1 = (hi - pd.Timedelta(days=1)).to_period("M").to_timestamp() + pd.offsets.MonthEnd(0)
    idx = pd.date_range(m0, m1, freq="D")
    d = pd.DataFrame({"served": 0, "energy": 0.0, "cost": 0.0, "est": False}, index=idx)
    for r in bills.itertuples(index=False):
        sl = slice(r.start, r.end - pd.Timedelta(days=1))
        d.loc[sl, "served"] += 1
        d.loc[sl, "energy"] += r.energy / r.days
        d.loc[sl, "cost"] += r.cost / r.days
        if getattr(r, "estimated", False):
            d.loc[sl, "est"] = True
    for a, b in est_spans:
        d.loc[a:b, "est"] = True
    g = d.groupby(d.index.to_period("M"))
    out = pd.DataFrame(
        {
            "days_in_month": g.size(),
            "days_covered": g["served"].apply(lambda v: int((v == 1).sum())),
            "energy": g["energy"].sum(),
            "cost": g["cost"].sum(),
            "estimated": g["est"].any(),
        }
    )
    out["complete"] = out["days_covered"] == out["days_in_month"]
    out.index = [str(p) for p in out.index]
    return out


def _cal_fails(tag: str, got: pd.DataFrame, exp: dict) -> list:
    fails = []
    rows = {r["month"]: r for r in exp["months"]}
    if sorted(rows) != sorted(got.index):
        return [f"{tag}: calendar months {sorted(got.index)} != {sorted(rows)}"]
    for mon, r in rows.items():
        g = got.loc[mon]
        for k in ("energy", "cost"):
            if k in r and abs(g[k] - r[k]) > BILL_TOL["calendar_rel"] * max(1.0, abs(r[k])):
                fails.append(f"{tag}: {mon} {k} {g[k]:.6g} != {r[k]}")
        for k in ("days_covered", "complete", "estimated"):
            if bool(g[k]) != bool(r[k]) if k != "days_covered" else int(g[k]) != r[k]:
                fails.append(f"{tag}: {mon} {k} {g[k]} != {r[k]}")
    return fails


def bills_self_test(exp: dict, root: str = HERE, local: str | None = None) -> list:
    """Rebuild every bill case from its CSVs with numpy / pandas and the stored model."""
    fails: list = []
    sec = exp.get("bills") or {}
    home = root
    for c in sec.get("cases", []):
        if not has_inputs(c, home, local):
            continue
        root = case_root(c, home, local)
        tag = c["id"]
        oat = read_oat(os.path.join(root, c["inputs"]["oat"]))
        raw = read_bills(os.path.join(root, c["inputs"]["bills"]))
        merged, spans = merge_estimated(raw)
        if [s for s in spans if s["action"] == "merged"] != c["estimated_reads"]["spans"]:
            fails.append(f"{tag}: merged estimated reads {spans} differ")
        p0, p1 = (pd.Timestamp(x) for x in c["config_entry"]["period"])
        r0, r1 = (pd.Timestamp(x) for x in c["config_entry"]["reporting_period"])
        last = merged["end"] - pd.Timedelta(days=1)
        base = merged[(merged["start"] >= p0) & (last <= p1)].reset_index(drop=True)
        rep_mask = (merged["start"] >= r0) & (last <= r1)
        m = c["baseline"]["model"]
        hb, cb = c["degree_days"]["heating_base_f"], c["degree_days"]["cooling_base_f"]
        wb = bill_weather(base, oat, hb, cb)
        for i, row in enumerate(c["degree_days"]["per_bill"]):
            d = base.loc[i, "days"]
            for k, v in (
                ("hdd_from_daily", wb.loc[i, "hdd_day"] * d),
                ("hdd_from_mean", max(0.0, hb - wb.loc[i, "oat"]) * d),
                ("cdd_from_daily", wb.loc[i, "cdd_day"] * d),
                ("cdd_from_mean", max(0.0, wb.loc[i, "oat"] - cb) * d),
            ):
                if abs(v - row[k]) > 1e-4 * max(1.0, abs(row[k])):
                    fails.append(f"{tag}: bill {row['start']} {k} {v:.6g} != {row[k]}")
        y = (base["energy"] / base["days"]).to_numpy(float)
        st = g14_stats(y, bill_predict(m, wb), m["n_params"], base["days"].to_numpy(float))
        b = c["baseline"]
        for k in ("r2", "adj_r2", "cvrmse_pct", "nmbe_pct"):
            if abs(st[k] - b[k]) > SELF[k]:
                fails.append(f"{tag}: baseline {k} {st[k]:.6g} != expected {b[k]}")
        for label, en, co in (
            ("raw", "energy", "cost"),
            ("injected", "energy_injected", "cost_injected"),
        ):
            rb, _ = merge_estimated(read_bills(os.path.join(root, c["inputs"]["bills"]), en, co))
            rep = rb[rep_mask.to_numpy()].reset_index(drop=True)
            pred = bill_predict(m, bill_weather(rep, oat, hb, cb)) * rep["days"].to_numpy(float)
            e = rep["energy"].to_numpy(float)
            o = c["option_c"][label]
            sav = float(pred.sum() - e.sum())
            if abs(sav - o["savings_total"]) > SELF["savings_rel"] * abs(pred.sum()):
                fails.append(f"{tag}: {label} savings {sav:.6g} != {o['savings_total']}")
            cost = float(((pred - e) * rep["cost"].to_numpy(float) / e).sum())
            if abs(cost - o["avoided_cost"]) > 0.01 + SELF["savings_rel"] * abs(pred.sum()):
                fails.append(f"{tag}: {label} avoided cost {cost:.6g} != {o['avoided_cost']}")
        est = [
            (pd.Timestamp(s["start"]), pd.Timestamp(s["end"]))
            for s in c["estimated_reads"]["spans"]
        ]
        fails += _cal_fails(f"{tag} calendar", calendar_months(merged, est), c["calendarized"])
        ppath = os.path.join(root, c["predictions"])
        if os.path.exists(ppath):
            pr = pd.read_csv(ppath)
            mine = bill_predictions(c, root)
            got = mine["predicted"].to_numpy(float)
            if (
                len(pr) != len(mine)
                or not (pr["start"].to_numpy() == mine["start"].to_numpy()).all()
            ):
                fails.append(f"{tag}: bill predictions rows do not match the merged bills")
            elif np.any(
                np.abs(got - pr["predicted"].to_numpy(float)) > 1e-5 * np.mean(np.abs(got))
            ):
                fails.append(f"{tag}: bill predictions differ from the model")
    cv = sec.get("calendarization")
    if cv:
        f = read_bills(os.path.join(home, cv["input"]))
        for view in ("max_gap_days_0", "max_gap_days_10"):
            fails += _cal_fails(f"calendarize_overlap/{view}", calendar_months(f), cv[view])
    return fails


def bill_predictions(c: dict, root: str) -> pd.DataFrame:
    """A bill case's predicted series (estimated reads merged; bills with under 90% of their days
    covered by temperature left out, as CAMBER drops them), from the stored model."""
    oat = read_oat(os.path.join(root, c["inputs"]["oat"]))
    merged, _ = merge_estimated(read_bills(os.path.join(root, c["inputs"]["bills"])))
    hb, cb = c["degree_days"]["heating_base_f"], c["degree_days"]["cooling_base_f"]
    w = bill_weather(merged, oat, hb, cb)
    keep = (w["coverage"] >= 0.9).to_numpy()
    b = merged[keep].reset_index(drop=True)
    per_day = bill_predict(c["baseline"]["model"], w[keep].reset_index(drop=True))
    p0, p1 = (pd.Timestamp(x) for x in c["config_entry"]["period"])
    last = b["end"] - pd.Timedelta(days=1)
    return pd.DataFrame(
        {
            "start": b["start"].dt.strftime("%Y-%m-%d"),
            "end": last.dt.strftime("%Y-%m-%d"),
            "days": b["days"].astype(int),
            "period": np.where((b["start"] >= p0) & (last <= p1), "baseline", "reporting"),
            "predicted_per_day": per_day,
            "predicted": per_day * b["days"].to_numpy(float),
        }
    )


def write_local_predictions(exp: dict, local: str) -> int:
    """Write the predicted series of every ``data: local`` case under ``local``, rebuilt from the
    stored coefficients (7 significant figures); returns how many were written."""
    n = 0
    for case in exp["cases"]:
        if case.get("data") != "local" or not has_inputs(case, HERE, local):
            continue
        for fit in case["fits"].values():
            _write_series(fit_predictions(fit, local), os.path.join(local, fit["predictions"]))
            n += 1
    for c in (exp.get("bills") or {}).get("cases", []):
        if c.get("data") == "local" and has_inputs(c, HERE, local):
            _write_series(bill_predictions(c, local), os.path.join(local, c["predictions"]))
            n += 1
    return n


def _write_series(df: pd.DataFrame, path: str) -> None:
    df = df.copy()
    for col in ("predicted", "predicted_per_day"):
        if col in df.columns:
            df[col] = [float(f"{v:.7g}") for v in df[col]]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_csv(path, index=False, lineterminator="\n")


def bills_compare(exp: dict, res: dict, out) -> None:
    """Compare a results ``bills`` block (SCHEMA.md) with the expected bill cases."""
    by_id = {c["id"]: c for c in (exp.get("bills") or {}).get("cases", [])}
    for cid, r in res.items():
        tag = f"{cid}/bills"
        c = by_id.get(cid)
        if c is None:
            out("FAIL", tag, "unknown bill case id")
            continue
        b, mc = c["baseline"], c["model_comparison"]
        kind = b["model"]["kind"]
        bics = {row["kind"]: row["bic"] for row in mc["rows"] if not row["label"].endswith(" F")}
        if "kind" in r:
            if r["kind"] == kind:
                out("PASS", tag, f"kind {kind} (BIC gap {mc['bic_gap']:.2f})")
            elif r["kind"] in bics and bics[r["kind"]] - min(bics.values()) < KIND_CLOSE_BIC:
                out(
                    "NOTE",
                    tag,
                    f"kind {r['kind']} vs CAMBER {kind}: within {KIND_CLOSE_BIC:g} "
                    "BIC, a legitimate disagreement",
                )
            else:
                out("FAIL", tag, f"kind {r['kind']} vs CAMBER {kind}")
        sel = c["base_selection"]
        for k in ("heating_base_f", "cooling_base_f"):
            if r.get(k) is not None and sel.get(k) is not None:
                ok = abs(r[k] - sel[k]) <= BILL_TOL["base_f"]
                out("PASS" if ok else "FAIL", tag, f"{k} {r[k]:g} vs {sel[k]:g}")
        if r.get("kind") == kind and "change_points" in r and b["model"].get("change_points"):
            for a, e in zip(r["change_points"], b["model"]["change_points"]):
                ok = abs(a - e) <= BILL_TOL["base_f"]
                out("PASS" if ok else "FAIL", tag, f"change point {a:.2f} vs {e:.2f} F")
        for k, tk in (
            ("r2", "r2_abs"),
            ("cvrmse_pct", "cvrmse_pct_abs"),
            ("nmbe_pct", "nmbe_pct_abs"),
        ):
            if k in r:
                ok = abs(r[k] - b[k]) <= TOL[tk]
                out("PASS" if ok else "FAIL", tag, f"{k} {r[k]:.4g} vs {b[k]:.4g}")
        for label, val in (r.get("savings_total") or {}).items():
            e = c["option_c"][label]
            tol = TOL["savings_frac_of_baseline"] * abs(e["baseline_projected"])
            ok = abs(val - e["savings_total"]) <= tol
            msg = f"savings {label} {val:.6g} vs {e['savings_total']:.6g} (+/-{tol:.3g})"
            out("PASS" if ok else "FAIL", tag, msg)
        for label, val in (r.get("avoided_cost") or {}).items():
            e = c["option_c"][label]
            tol = BILL_TOL["cost_frac_of_baseline"] * abs(
                e["baseline_projected"] * e["avoided_cost_rate"]
            )
            ok = abs(val - e["avoided_cost"]) <= tol
            msg = f"avoided cost {label} {val:.6g} vs {e['avoided_cost']:.6g} (+/-{tol:.3g})"
            out("PASS" if ok else "FAIL", tag, msg)
        months = {m["month"]: m for m in c["calendarized"]["months"]}
        for mon, val in (r.get("calendarized") or {}).items():
            if mon not in months:
                out("FAIL", tag, f"calendar month {mon} not expected")
                continue
            e = months[mon]["energy"]
            ok = abs(val - e) <= BILL_TOL["calendar_rel"] * max(1.0, abs(e))
            out("PASS" if ok else "FAIL", tag, f"calendar {mon} {val:.6g} vs {e:.6g}")


def bills_template(exp: dict) -> dict:
    res: dict = {}
    for c in (exp.get("bills") or {}).get("cases", []):
        b, sel, o = c["baseline"], c["base_selection"], c["option_c"]
        r = {
            "kind": b["model"]["kind"],
            "heating_base_f": sel.get("heating_base_f"),
            "cooling_base_f": sel.get("cooling_base_f"),
            "r2": b["r2"],
            "cvrmse_pct": b["cvrmse_pct"],
            "nmbe_pct": b["nmbe_pct"],
            "savings_total": {k: o[k]["savings_total"] for k in ("raw", "injected")},
            "avoided_cost": {k: o[k]["avoided_cost"] for k in ("raw", "injected")},
            "calendarized": {m["month"]: m["energy"] for m in c["calendarized"]["months"]},
        }
        if b["model"].get("change_points"):
            r["change_points"] = b["model"]["change_points"]
        res[c["id"]] = r
    return res


def template(exp: dict) -> dict:
    """CAMBER's expected outputs in the results format (comparing it with itself passes)."""
    res: dict = {}
    for case in exp["cases"]:
        for interval, fit in case["fits"].items():
            m, st = fit["model"], fit["stats"]
            r = {
                "kind": m["kind"],
                "n_params": m["n_params"],
                "change_points": m["change_points"],
                "base": m["base"],
                "slopes_dEdT": m["slopes_dEdT"],
                "r2": st["r2"],
                "cvrmse_pct": st["cvrmse_pct"],
                "nmbe_pct": st["nmbe_pct"],
                "pass_calsim": fit["calsim_gate"]["pass_"],
                "pass_baseline": fit["baseline_gate"]["pass_"],
            }
            if fit["weights"] == "days":
                r["y"] = "per_day"
            if fit.get("option_c"):
                r["savings_total"] = {
                    k: fit["option_c"][k]["savings_total"] for k in ("raw", "injected")
                }
            res.setdefault(case["id"], {})[interval] = r
    out = {"schema": SCHEMA, "implementation": exp["generator"]["camber_version"], "results": res}
    if exp.get("bills"):
        out["bills"] = bills_template(exp)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("results", nargs="?", help="a results JSON to compare (SCHEMA.md)")
    ap.add_argument("--self-test", action="store_true", help="rebuild the expected numbers")
    ap.add_argument("--template", action="store_true", help="print CAMBER's results JSON")
    ap.add_argument("--expected", default=None, help="expected.json (default: next to this file)")
    ap.add_argument("--quiet", action="store_true", help="print only NOTE and FAIL lines")
    ap.add_argument("--local", default=None, help="the BDG2 folder of fetch_bdg2.py (./local)")
    a = ap.parse_args(argv)
    exp = load_expected(a.expected)
    root = os.path.dirname(os.path.abspath(a.expected)) if a.expected else HERE
    if a.template:
        json.dump(template(exp), sys.stdout, indent=1)
        sys.stdout.write("\n")
        return 0
    status = 0
    if a.self_test:
        fails = self_test(exp, root, a.local)
        gone = missing_local(exp, root, a.local)
        if gone:
            print(f"skipped {len(gone)} BDG2 case(s) with no local inputs: run fetch_bdg2.py")
        for f in fails:
            print("FAIL", f)
        n = sum(len(c["fits"]) for c in exp["cases"])
        nb = len((exp.get("bills") or {}).get("cases", []))
        print(f"self-test: {n} fits and {nb} bill cases, {len(fails)} failures")
        status |= int(bool(fails))
    if a.results:
        with open(a.results, encoding="utf-8") as fh:
            results = json.load(fh)
        lines, n_fail = compare(exp, results, root, a.local)
        for ln in lines:
            if not a.quiet or not ln.startswith("PASS"):
                print(ln)
        notes = sum(ln.startswith("NOTE") for ln in lines)
        print(
            f"{results.get('implementation', '?')}: {len(lines)} checks, {n_fail} failed, "
            f"{notes} notes"
        )
        status |= int(n_fail > 0)
    if not (a.self_test or a.results):
        ap.print_help()
        return 2
    return status


if __name__ == "__main__":
    raise SystemExit(main())
