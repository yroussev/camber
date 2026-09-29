"""BDG2 daily meters re-expressed as monthly bills: billing-path savings against the daily path.

Not a gated benchmark (issue #72): it reports how far the bill-based M&V path
(:mod:`camber.mandv.billing`, days-weighted fits) agrees with the daily path on the same real
meters (Building Data Genome 2, CC-BY-SA 4.0), and how the fixed 65 °F and the fitted-base
(``base_f: "auto"``) billing baselines compare.

For every meter with at least 328 whole days in each of 2016 (baseline) and 2017 (reporting), as
in ``savings_benchmark.py``:

* the daily frame is cut into synthetic bills of 28--35 days (seeded per building); a bill that
  would contain a missing day ends before it, and the next starts after the gap, so a bill's
  energy is always the sum of whole days;
* a whole-load saving ``s`` (default 10%) is injected into every reporting-year day;
* **daily path**: the best change-point model on the baseline days, a forecast onto the reporting
  days (G14 kernel);
* **billing path, fixed base**: the best change-point model on the baseline bills, weighted by
  days, a forecast onto the reporting bills (``days=``);
* **billing path, auto**: the same with the degree-day bases selected from the bills and the
  degree-day model competing by BIC (:func:`camber.mvbilling.fit_billing_baseline`).

Printed per meter type: the mean and median of ``savings_pct(billing) - savings_pct(daily)``, the
mean absolute difference, the correlation, the share of meters whose billing saving lies inside
the daily path's 90% band, each path's error against the injected saving and its band's coverage
of it, and how often the degree-day model was selected. ``--json`` writes them (``info.`` keys:
nothing here is gated).

    python examples/bdg2/billing_agreement.py [--sample 200] [--save 0.10] [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import zlib

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "_data", "bdg2")
BASELINE = ("2016-01-01", "2016-12-31")
REPORTING = ("2017-01-01", "2017-12-31")
METERS = (("electricity.csv", "electricity"), ("chilledwater.csv", "chilledwater"))
SEED = 7206


def _savings_bench():
    import importlib.util

    name = "bdg2_savings_benchmark_for_bills"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, os.path.join(HERE, "savings_benchmark.py")
        )
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        sys.modules[name] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return sys.modules[name]


def to_bills(daily: pd.DataFrame, rng) -> pd.DataFrame:
    """Whole-day bills of 28--35 days from a daily ``energy`` frame (gaps end a bill)."""
    idx = pd.DatetimeIndex(daily.index)
    rows = []
    i = 0
    n = len(idx)
    while i < n:
        want = int(rng.integers(28, 36))
        j = i + 1
        while j < n and j - i < want and (idx[j] - idx[j - 1]).days == 1:
            j += 1
        if j - i >= 20:  # a stub shorter than 20 days after a gap is not a bill
            rows.append(
                {
                    "start": idx[i],
                    "end": idx[j - 1] + pd.Timedelta(days=1),
                    "energy": float(daily["energy"].iloc[i:j].sum()),
                }
            )
        i = j
    return pd.DataFrame(rows)


def _forecast(model, rows, y, st, days=None):
    from camber.mandv import _mvform
    from camber.mandv.methods import forecast_savings

    return forecast_savings(
        model,
        rows,
        y,
        cv_rmse=st.cv_rmse,
        n_baseline=st.n,
        p_baseline=_mvform.n_params(model),
        rho=st.rho_lag1,
        kernel="g14",
        days=days,
    )


def score(job) -> dict | None:
    b, daily, oat_h, save = job
    from camber.mandv import _mvform
    from camber.mandv.billing import BillingSeries
    from camber.mandv.models import N_PARAMS, best_model
    from camber.mandv.stats import cv_rmse_max_for, fit_stats
    from camber.mvbilling import fit_billing_baseline

    rng = np.random.default_rng(zlib.crc32(b.encode()) ^ SEED)
    d = daily.copy()
    rep_mask = d.index >= REPORTING[0]
    d.loc[rep_mask, "energy"] *= 1.0 - save
    base_d, rep_d = d.loc[: BASELINE[1]], d.loc[REPORTING[0] :]
    out: dict = {"building": b}
    try:
        m = best_model(base_d["oat"].values, base_d["energy"].values, time_index=base_d.index)
        st = fit_stats(
            base_d["energy"].values,
            m.predict(base_d["oat"].values),
            N_PARAMS[m.kind],
            time_index=base_d.index,
        )
        r = _forecast(m, rep_d["oat"].values, rep_d["energy"].values, st)
        out["daily"] = (r.savings_pct, r.abs_uncertainty, r.projected)
        out["daily_cv"] = float(st.cv_rmse)
        bills = pd.concat(
            [to_bills(base_d, rng), to_bills(rep_d, rng)], ignore_index=True
        ).sort_values("start")
        bs = BillingSeries.from_frame(bills, end_inclusive=False)
        for label, entry in (("fixed", {}), ("auto", {"base_f": "auto"})):
            fitted = fit_billing_baseline(bs, oat_h, entry, list(BASELINE))
            model, base = fitted["model"], fitted["base"]
            if model is None:
                continue
            days_b = _mvform.row_days(base)
            stb = fit_stats(
                base["energy"].values,
                model.predict(_mvform.design_rows(base, model)),
                _mvform.n_params(model),
                cv_rmse_max=cv_rmse_max_for("monthly"),
                time_index=base.index,
                weights=days_b,
            )
            frame = fitted["frame"]
            rep = frame[(frame["start"] >= REPORTING[0]) & (frame["end"] <= "2018-01-01")]
            rb = _forecast(
                model,
                _mvform.design_rows(rep, model),
                rep["energy"].values,
                stb,
                days=_mvform.row_days(rep),
            )
            out[label] = (rb.savings_pct, rb.abs_uncertainty, rb.projected)
            if label == "auto":
                out["auto_model"] = model.kind
    except (ValueError, TypeError, np.linalg.LinAlgError):
        return None
    return out


def _stats(recs, save: float) -> dict:
    """Agreement and recovery statistics, on every meter and on the meters whose daily baseline
    has CV(RMSE) <= 30% (``g14cv.``; G14's daily threshold): a meter whose daily model explains
    little makes any saving percentage noise, and a near-zero projection makes it explode, so the
    medians, quantiles and shares-within are the robust figures."""
    out = _stats_one(recs, save)
    good = [r for r in recs if np.isfinite(r.get("daily_cv", np.nan)) and r["daily_cv"] <= 0.30]
    out.update({f"g14cv.{k}": v for k, v in _stats_one(good, save).items()})
    return out


def _stats_one(recs, save: float) -> dict:
    out: dict = {"n": len(recs)}
    if not recs:
        return out
    for path in ("daily", "fixed", "auto"):
        ok = [r for r in recs if r.get(path) and r[path][0] is not None]
        err = np.array([r[path][0] - save for r in ok])
        cov = [
            abs(r[path][0] - save) * r[path][2] <= r[path][1]
            for r in ok
            if r[path][1] is not None and r[path][2]
        ]
        out[f"{path}.n"] = len(ok)
        out[f"{path}.mean_error_pct_pts"] = round(100 * float(np.mean(err)), 3) if len(ok) else None
        out[f"{path}.mae_pct_pts"] = (
            round(100 * float(np.mean(np.abs(err))), 3) if len(ok) else None
        )
        out[f"{path}.median_abs_error_pct_pts"] = (
            round(100 * float(np.median(np.abs(err))), 3) if len(ok) else None
        )
        out[f"{path}.band_covers_truth"] = round(float(np.mean(cov)), 3) if cov else None
    for path in ("fixed", "auto"):
        pair = [r for r in recs if r.get(path) and r.get("daily") and r["daily"][0] is not None]
        pair = [r for r in pair if r[path][0] is not None]
        if not pair:
            continue
        diff = np.array([r[path][0] - r["daily"][0] for r in pair])
        a = np.array([r[path][0] for r in pair])
        bday = np.array([r["daily"][0] for r in pair])
        inside = [
            abs(r[path][0] - r["daily"][0]) * r["daily"][2] <= r["daily"][1]
            for r in pair
            if r["daily"][1] is not None and r["daily"][2]
        ]
        out[f"{path}_vs_daily.mean_diff_pct_pts"] = round(100 * float(diff.mean()), 3)
        out[f"{path}_vs_daily.median_diff_pct_pts"] = round(100 * float(np.median(diff)), 3)
        out[f"{path}_vs_daily.mean_abs_diff_pct_pts"] = round(100 * float(np.abs(diff).mean()), 3)
        out[f"{path}_vs_daily.p90_abs_diff_pct_pts"] = round(
            100 * float(np.percentile(np.abs(diff), 90)), 3
        )
        out[f"{path}_vs_daily.corr"] = round(float(np.corrcoef(a, bday)[0, 1]), 4)
        out[f"{path}_vs_daily.spearman"] = round(
            float(pd.Series(a).rank().corr(pd.Series(bday).rank())), 4
        )
        for tol in (1, 2, 5):
            out[f"{path}_vs_daily.share_within_{tol}pt"] = round(
                float(np.mean(np.abs(diff) <= tol / 100.0)), 3
            )
        out[f"{path}_vs_daily.inside_daily_band"] = round(float(np.mean(inside)), 3)
    kinds = [r.get("auto_model") for r in recs if r.get("auto_model")]
    out["auto.dd_model_selected_share"] = (
        round(float(np.mean([str(k).startswith("DD") for k in kinds])), 3) if kinds else None
    )
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sample", type=int, default=200, help="meters per type (0 = all)")
    ap.add_argument("--save", type=float, default=0.10)
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    sb = _savings_bench()
    meta = pd.read_csv(os.path.join(DATA, "metadata.csv")).set_index("building_id")
    weather = pd.read_csv(
        os.path.join(DATA, "weather.csv"),
        usecols=["timestamp", "site_id", "airTemperature"],
        parse_dates=["timestamp"],
    )
    result: dict = {"info.save": a.save, "info.sample": a.sample}
    for meter, label in METERS:
        path = os.path.join(DATA, meter)
        if not os.path.exists(path):
            continue
        df = pd.read_csv(path, parse_dates=["timestamp"]).set_index("timestamp")
        jobs = []
        for b in df.columns:
            if b not in meta.index:
                continue
            oat = sb._oat_f(weather, meta.loc[b, "site_id"])
            d = sb.daily_frame(df[b].loc[BASELINE[0] : REPORTING[1]], oat)
            base, rep = d.loc[: BASELINE[1]], d.loc[REPORTING[0] :]
            if len(base) >= sb.MIN_DAYS and len(rep) >= sb.MIN_DAYS and sb._live(base):
                jobs.append((b, d, oat.dropna(), a.save))
        if a.sample:
            keep = set(sb.sample_ids([j[0] for j in jobs], a.sample))
            jobs = [j for j in jobs if j[0] in keep]
        if a.jobs > 1:
            from concurrent.futures import ProcessPoolExecutor

            with ProcessPoolExecutor(max_workers=a.jobs) as pool:
                recs = [r for r in pool.map(score, jobs, chunksize=4) if r is not None]
        else:
            recs = [r for r in map(score, jobs) if r is not None]
        st = _stats(recs, a.save)
        print(f"=== {label}: {st['n']} meters ===")
        for k, v in st.items():
            print(f"  {k}: {v}")
            result[f"info.{label}.{k}"] = v
    if a.json:
        with open(a.json, "w") as fh:
            json.dump(result, fh, indent=2, sort_keys=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
