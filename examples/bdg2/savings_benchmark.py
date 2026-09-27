"""BDG2 M&V savings benchmark -- placebo coverage and injected-savings recovery on real meters.

The companion of ``benchmark.py`` (which scores whether a G14 baseline *fits*): this scores whether
CAMBER's **savings** and their **uncertainty bands** hold up on real whole-building meters
(Building Data Genome 2, CC-BY-SA 4.0). Baseline year 2016, reporting year 2017, the publisher's
cleaned meters, whole days only (at least 23 of 24 hourly readings) -- the same data rules as
``benchmark.py`` (issue #21 phase 21e, #49).

Four experiments, all seeded and deterministic:

1. **Placebo** (nothing injected; no building is known to have had a measure). Every saving is
   model error, so the share of buildings whose band covers zero is the band's real coverage. The
   metrics are those of Touzani, Granderson, Jump & Rebello, *Energy & Buildings* 193:216-225
   (2019): the error-uncertainty ratio ``EUR = (actual - predicted) / band`` (their Eq 15) and the
   uncertainty-interval coverage factor ``UICF`` = the share of buildings with ``|EUR| <= 1``
   (their Eq 16; here a fraction, not a percentage). Touzani et al. found ~71% at nominal 95% for
   the G14 method on daily linear models; **under-coverage is expected**, so the gate is on
   *regression against the committed baseline*, never on the nominal rate.
2. **Injected savings** of 5, 10 and 20%: every reporting-year reading is multiplied by
   ``1 - s`` (a whole-load measure). The true SEnPI is ``1 - s`` on every basis, so each method's
   ``savings_pct`` error is ``savings_pct - s`` and its band covers the truth when
   ``|enpi - (1 - s)| <= enpi_uncertainty``. Forecast recovery is exact by construction -- its
   error is the placebo error -- and is checked as a sanity identity; backcast (reporting model at
   baseline conditions) and standard conditions (both models at a two-year day-of-year normal
   of the site's temperatures) are the methods measured.
3. **Injected steps**: one step (10% or 20% of the building's mean daily energy, random sign and
   date) or two steps (20% each) planted in the reporting year. Scored: whether
   ``detect_step_changes`` finds each within 7 days, its date error, detections that are neither
   planted nor present in the un-injected series (spurious), the indicator NRA's recovery of the
   planted effect ``delta`` at the true date (``estimate_nre_indicator``, reporting-period fit)
   and whether its 90% interval covers ``delta``; and whether the detector's own step band covers
   it. The real series carries its own level shifts, which are part of the noise.
4. **Injected static-factor change**: a floor-area increase ``r = 1.25`` affecting a stated share
   ``f = 0.6`` of the load (a ``1.15`` multiplier) from the start of the reporting year, or from
   1 July. The proportional ``StaticFactorAdjustment`` should restore the placebo saving; scored
   are its recovery error, and the band's coverage of the true zero saving with and without the
   adjustment.

**Sampling.** Placebo and injected savings run on every eligible meter: at least 328 whole days in
*each* year (at most 37 of 365 missing, the CalTRACK 2.0 §2.2.1.2 data-sufficiency rule), and a
meter that reads one constant value all year (a dead meter reads 0) is left out. Steps and
static factors run on a deterministic subsample of ``--sample`` meters per meter type (default
150; ``--sample 0`` = all), drawn with a fixed seed from the sorted eligible list, and every
building's injections use a seed derived from its id -- so a run is reproducible and a building's
draws do not depend on which others were sampled. The subsample keeps the CI job within a few
minutes; it is stated in the output and in the metrics (``*.n_series``).

The gated metrics go to ``savings-benchmark-baseline.json`` (separate from
``benchmark-baseline.json``, whose keys this script never touches). Signed biases and medians of
the signed EUR are printed and written under ``info.`` keys by ``--json`` but are not gated
(neither direction is "better"). The pure metric functions below are unit-tested on synthetic
records with no download (``tests/test_bdg2_savings_benchmark.py``).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import zlib

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from camber.eval import baseline_report, check_against_baseline  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "_data", "bdg2")
BASELINE_YEAR = ("2016-01-01", "2016-12-31")
REPORTING_YEAR = ("2017-01-01", "2017-12-31")
MIN_DAYS = 328  # whole days per year: CalTRACK 2.0 §2.2.1.2 allows at most 37 of 365 missing
CONFIDENCE = 0.90
SEED = 2149
SAVINGS = (0.05, 0.10, 0.20)
STEP_SIZES = (0.10, 0.20)  # of the building's mean daily reporting energy
DOUBLE_STEP_SIZE = 0.20
STEP_TOLERANCE_DAYS = 7  # a planted step counts as found when a detection is this close
STEP_MARGIN_DAYS = 45  # planted steps stay this far from the ends of the year
STATIC_RATIO = 1.25  # floor area 1.25x ...
STATIC_SHARE = 0.60  # ... affecting 60% of the load: multiplier 1 + 0.25 * 0.6 = 1.15
DEFAULT_SAMPLE = 150
IDENTITY_TOL = 1e-4  # relative deviation allowed in the forecast identity (result rounding)
METERS = [
    ("cleaned/electricity_cleaned.csv", "electricity"),
    ("cleaned/chilledwater_cleaned.csv", "chilledwater"),
]
#: method/kernel pairs scored in the placebo and injection experiments
METHOD_KERNELS = (
    ("forecast", "g14"),
    ("forecast", "exact"),
    ("backcast", "g14"),
    ("backcast", "exact"),
    ("standard_conditions", "exact"),
)
#: metric-name tokens whose value regresses when it RISES (everything else regresses on a fall)
LOWER_IS_BETTER = ("error", "spurious", "eur_abs", "declined_rate")


# --------------------------------------------------------------------- pure metrics (unit-tested)


def _q(values, frac: float) -> float:
    """Nearest-rank quantile of a non-empty list (the convention of ``benchmark.rho_metrics``)."""
    v = sorted(values)
    return v[min(len(v) - 1, max(0, int(round(frac * (len(v) - 1)))))]


def _r4(x) -> float:
    return round(float(x), 4)


def eur(actual: float, predicted: float, band: float | None) -> float | None:
    """Touzani et al. 2019 Eq 15: ``(actual - predicted) / band``; ``None`` without a band."""
    if band is None or not math.isfinite(band) or band <= 0:
        return None
    return (float(actual) - float(predicted)) / float(band)


def placebo_metrics(records, label: str) -> dict:
    """Coverage of the placebo (no-measure) savings bands -> gated and ``info.`` metrics.

    ``records`` = ``[{"eur": float | None, "declined": bool}, ...]`` for one method and kernel.
    ``uicf`` is the share of undeclined results with ``|EUR| <= 1`` (a result without a finite
    band counts as not covered), ``eur_abs_p50`` / ``eur_abs_p90`` the quantiles of ``|EUR|``,
    ``declined_rate`` the share declined for severe extrapolation. The signed median EUR (bias
    direction) is informational.
    """
    n_all = len(records)
    live = [r for r in records if not r.get("declined")]
    n = len(live)
    out = {
        f"{label}.n": n,
        f"{label}.declined_rate": _r4((n_all - n) / n_all) if n_all else 0.0,
    }
    if not n:
        return out
    eurs = [r["eur"] for r in live if r.get("eur") is not None]
    covered = sum(1 for e in eurs if abs(e) <= 1.0)
    out[f"{label}.uicf"] = _r4(covered / n)
    if eurs:
        a = [abs(e) for e in eurs]
        out[f"{label}.eur_abs_p50"] = _r4(_q(a, 0.5))
        out[f"{label}.eur_abs_p90"] = _r4(_q(a, 0.9))
        out[f"info.{label}.eur_median"] = _r4(statistics.median(eurs))
    return out


def recovery_metrics(records, label: str) -> dict:
    """Recovery of an injected saving -> gated and ``info.`` metrics.

    ``records`` = ``[{"err": savings_pct - s, "covered": bool, "significant": bool,
    "declined": bool}, ...]``. ``abs_error_p50`` / ``_p90`` are quantiles of ``|err|`` (fractions
    of energy, not percentage points); ``coverage`` is the share whose SEnPI band covers the true
    ``1 - s``; ``significant_rate`` the share whose band lies wholly below 1 (a saving the band
    can distinguish from none). The signed median error is informational.
    """
    n_all = len(records)
    live = [r for r in records if not r.get("declined") and r.get("err") is not None]
    n = len(live)
    out = {
        f"{label}.n": n,
        f"{label}.declined_rate": _r4((n_all - n) / n_all) if n_all else 0.0,
    }
    if not n:
        return out
    errs = [r["err"] for r in live]
    a = [abs(e) for e in errs]
    out[f"{label}.abs_error_p50"] = _r4(_q(a, 0.5))
    out[f"{label}.abs_error_p90"] = _r4(_q(a, 0.9))
    out[f"{label}.coverage"] = _r4(sum(bool(r.get("covered")) for r in live) / n)
    out[f"{label}.significant_rate"] = _r4(sum(bool(r.get("significant")) for r in live) / n)
    out[f"info.{label}.bias_median"] = _r4(statistics.median(errs))
    return out


def match_steps(planted, detected, *, tol: int = STEP_TOLERANCE_DAYS):
    """Pair planted step days with detections: ``[(planted, nearest detected or None), ...]``.

    Days are integer day numbers. A planted step matches the nearest detection within ``tol``
    days; one detection matches at most one planted step (the closer one).
    """
    free = list(detected)
    out = []
    for p in sorted(planted):
        best = min(free, key=lambda d: abs(d - p), default=None)
        if best is not None and abs(best - p) <= tol:
            free.remove(best)
            out.append((p, best))
        else:
            out.append((p, None))
    return out


def spurious_count(detected, planted, native, *, tol: int = STEP_TOLERANCE_DAYS) -> int:
    """Detections within ``tol`` days of neither a planted step nor an un-injected detection."""
    ref = list(planted) + list(native)
    return sum(1 for d in detected if all(abs(d - r) > tol for r in ref))


def step_metrics(records, label: str) -> dict:
    """Step-detection and indicator-NRA recovery -> gated and ``info.`` metrics.

    ``records`` has one entry per series: ``{"steps": [{"found": bool, "date_error": int | None,
    "ind_rel_error": float | None, "ind_covered": bool | None, "det_covered": bool | None}, ...],
    "spurious": int, "native": int}``. ``detect_rate`` is the share of planted steps found within
    the tolerance; ``date_error_p50`` the median absolute date error (days) of the found steps;
    ``spurious_per_series`` the mean count of detections that are neither planted nor native;
    ``indicator_rel_error_p50`` the median ``|rate - delta| / |delta|`` of the indicator fitted at
    the true date and ``indicator_coverage`` how often its 90% interval covers ``delta``;
    ``detector_coverage`` how often the detector's own 90% step band covers ``delta`` (found
    steps only). ``info.native_rate`` is the share of un-injected real series with a detection.
    """
    n_series = len(records)
    steps = [s for r in records for s in r["steps"]]
    out = {f"{label}.n_series": n_series, f"{label}.n_steps": len(steps)}
    if not steps:
        return out
    found = [s for s in steps if s["found"]]
    out[f"{label}.detect_rate"] = _r4(len(found) / len(steps))
    if found:
        out[f"{label}.date_error_p50"] = _r4(_q([abs(s["date_error"]) for s in found], 0.5))
        dc = [s["det_covered"] for s in found if s.get("det_covered") is not None]
        if dc:
            out[f"{label}.detector_coverage"] = _r4(sum(dc) / len(dc))
    out[f"{label}.spurious_per_series"] = _r4(sum(r["spurious"] for r in records) / n_series)
    rel = [s["ind_rel_error"] for s in steps if s.get("ind_rel_error") is not None]
    if rel:
        out[f"{label}.indicator_rel_error_p50"] = _r4(_q(rel, 0.5))
        out[f"{label}.indicator_rel_error_p90"] = _r4(_q(rel, 0.9))
    cov = [s["ind_covered"] for s in steps if s.get("ind_covered") is not None]
    if cov:
        out[f"{label}.indicator_coverage"] = _r4(sum(cov) / len(cov))
    out[f"info.{label}.native_rate"] = _r4(sum(1 for r in records if r["native"]) / n_series)
    return out


def static_metrics(records, label: str) -> dict:
    """Static-factor recovery -> gated metrics.

    ``records`` = ``[{"recovery_error": |adjusted pct - placebo pct|, "unadjusted_error":
    |unadjusted pct - placebo pct|, "adjusted_covered": bool, "unadjusted_covered": bool}, ...]``;
    ``*_covered`` says whether the band covers the true saving (zero: no measure was injected).
    """
    n = len(records)
    out = {f"{label}.n": n}
    if not n:
        return out
    rec = [r["recovery_error"] for r in records]
    una = [r["unadjusted_error"] for r in records]
    out[f"{label}.recovery_error_p50"] = _r4(_q(rec, 0.5))
    out[f"{label}.recovery_error_p90"] = _r4(_q(rec, 0.9))
    out[f"{label}.unadjusted_error_p50"] = _r4(_q(una, 0.5))
    out[f"{label}.adjusted_uicf"] = _r4(sum(bool(r["adjusted_covered"]) for r in records) / n)
    out[f"{label}.unadjusted_uicf"] = _r4(sum(bool(r["unadjusted_covered"]) for r in records) / n)
    return out


def gated(metrics: dict) -> dict:
    """The metrics the gate compares: everything except the informational ``info.`` keys."""
    return {k: v for k, v in metrics.items() if not k.startswith("info.")}


def sample_ids(ids, n: int, seed: int = SEED) -> list:
    """A deterministic subsample of ``n`` ids (all of them when ``n`` is 0 or >= len)."""
    ids = sorted(ids)
    if not n or n >= len(ids):
        return ids
    rng = np.random.default_rng(seed)
    return sorted(rng.choice(ids, size=n, replace=False).tolist())


def building_seed(building: str, seed: int = SEED) -> int:
    """A per-building seed derived from its id, independent of which others are sampled."""
    return (zlib.crc32(building.encode("utf-8")) ^ seed) & 0xFFFFFFFF


# --------------------------------------------------------------------------- data preparation


def _acceptance_benchmark():
    """The sibling ``benchmark.py`` (its whole-day rule), loaded by path under a unique name: other
    examples also have a ``benchmark`` module, so a plain import can pick up the wrong one."""
    import importlib.util

    name = "bdg2_acceptance_benchmark"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, "benchmark.py"))
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        sys.modules[name] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return sys.modules[name]


def daily_frame(hourly_kwh, oat_f) -> pd.DataFrame:
    """Daily ``energy`` (whole days only, the acceptance benchmark's rule) and mean ``oat`` (°F)."""
    e = _acceptance_benchmark().complete_days(hourly_kwh)
    if e.empty:
        return pd.DataFrame(columns=["energy", "oat"])
    energy = e.groupby(e.index.normalize()).sum()
    oat = oat_f.groupby(oat_f.index.normalize()).mean()
    df = pd.DataFrame({"energy": energy, "oat": oat}).dropna()
    return df[df["energy"] >= 0]


def normal_year(oat_f) -> np.ndarray:
    """Standard conditions: the site's mean daily OAT for each day of the year over both years
    (29 February dropped), 365 values -- a two-year 'normal' built from the data at hand."""
    d = oat_f.groupby(oat_f.index.normalize()).mean().dropna()
    d = d[~((d.index.month == 2) & (d.index.day == 29))]
    doy = d.index.dayofyear - ((d.index.is_leap_year) & (d.index.month > 2)).astype(int)
    return d.groupby(doy).mean().to_numpy(dtype=float)


class _Fit:
    """A best change-point model on one period with the statistics the savings kernels need."""

    def __init__(self, df: pd.DataFrame):
        from camber.mandv.models import N_PARAMS, best_model
        from camber.mandv.stats import fit_stats

        self.T = df["oat"].to_numpy(dtype=float)
        self.y = df["energy"].to_numpy(dtype=float)
        self.index = df.index
        self.model = best_model(self.T, self.y, time_index=self.index)
        self.p = N_PARAMS[self.model.kind]
        st = fit_stats(self.y, self.model.predict(self.T), self.p, time_index=self.index)
        self.cv_rmse, self.n, self.rho = float(st.cv_rmse), int(st.n), st.rho_lag1


def _run_method(method, kernel, fb: _Fit, fr: _Fit, S, *, confidence=CONFIDENCE):
    from camber.mandv.methods import (
        backcast_savings,
        forecast_savings,
        standard_conditions_savings,
    )

    if method == "forecast":
        return forecast_savings(
            fb.model,
            fr.T,
            fr.y,
            cv_rmse=fb.cv_rmse,
            n_baseline=fb.n,
            p_baseline=fb.p,
            confidence=confidence,
            rho=fb.rho,
            kernel=kernel,
        )
    if method == "backcast":
        return backcast_savings(
            fr.model,
            fb.T,
            fb.y,
            cv_rmse=fr.cv_rmse,
            n_reporting=fr.n,
            p_reporting=fr.p,
            confidence=confidence,
            rho=fr.rho,
            kernel=kernel,
        )
    return standard_conditions_savings(
        fb.model,
        fr.model,
        S,
        confidence=confidence,
        kernel=kernel,
        rho_baseline=fb.rho,
        rho_reporting=fr.rho,
    )


def _placebo_eur(res, method: str):
    """EUR of a placebo result: (actual - predicted) / band on the method's own basis.

    Forecast: measured reporting minus the baseline projection (Touzani's Eq 15 exactly, ``-S``).
    Backcast: measured baseline minus the reporting model there (``+S``). Standard conditions:
    the reporting model minus the baseline model at standard conditions (``-S``).
    """
    if res.declined or res.savings is None:
        return None
    sign = 1.0 if method == "backcast" else -1.0
    return eur(sign * res.savings, 0.0, res.abs_uncertainty)


def _fin(x) -> bool:
    return x is not None and math.isfinite(x)


def savings_records(fb: _Fit, rep: pd.DataFrame, S) -> tuple:
    """Placebo and injected-savings records for one building.

    Returns ``(placebo, injected, identity_dev)``: ``placebo[mk]`` one record per method/kernel,
    ``injected[(s, mk)]`` likewise, and the largest relative deviation of the forecast identity
    ``S_f(s) - s * O_r == S_f(0)`` (zero up to rounding).
    """
    placebo, injected = {}, {}
    fr0 = _Fit(rep)
    base = {}
    for method, kernel in METHOD_KERNELS:
        mk = f"{method}_{kernel}"
        r = _run_method(method, kernel, fb, fr0, S)
        base[mk] = r
        placebo[mk] = {"eur": _placebo_eur(r, method), "declined": bool(r.declined)}
    r95 = _run_method("forecast", "g14", fb, fr0, S, confidence=0.95)
    placebo["forecast_g14_95"] = {"eur": _placebo_eur(r95, "forecast"), "declined": r95.declined}
    dev = 0.0
    O_r = float(rep["energy"].sum())
    for s in SAVINGS:
        fr = _Fit(rep.assign(energy=rep["energy"] * (1.0 - s)))
        for method, kernel in METHOD_KERNELS:
            mk = f"{method}_{kernel}"
            r = _run_method(method, kernel, fb, fr, S)
            ok = not r.declined and _fin(r.savings_pct) and _fin(r.enpi)
            rec = {"declined": not ok, "err": None, "covered": False, "significant": False}
            if ok:
                band = r.enpi_uncertainty if _fin(r.enpi_uncertainty) else None
                rec["err"] = float(r.savings_pct) - s
                if band is not None:
                    rec["covered"] = abs(r.enpi - (1.0 - s)) <= band
                    rec["significant"] = r.enpi + band < 1.0
            injected[(s, mk)] = rec
            b = base[mk]
            if method == "forecast" and ok and not b.declined and b.savings is not None and O_r:
                dev = max(dev, abs((r.savings - s * O_r) - b.savings) / O_r)
    return placebo, injected, dev


def _t90(df) -> float:
    from camber.mandv.stats import _t_value

    return _t_value(CONFIDENCE, df)


def step_record(rep: pd.DataFrame, rng, *, sizes) -> dict:
    """Plant ``len(sizes)`` steps (fractions of mean daily energy, random signs) in the reporting
    year and score detection, date error, spurious detections and indicator recovery."""
    from camber.mandv.adjustments import estimate_nre_indicator
    from camber.mandv.nonroutine import detect_step_changes

    idx = rep.index
    n = len(rep)
    y0 = rep["energy"].to_numpy(dtype=float)
    T = rep["oat"].to_numpy(dtype=float)
    mean = float(y0.mean())
    k = len(sizes)
    lo, hi = STEP_MARGIN_DAYS, n - STEP_MARGIN_DAYS
    if k == 1:
        pos = [int(rng.integers(lo, hi))]
    else:  # two steps at least 60 days apart
        a = int(rng.integers(lo, n // 2 - 30))
        pos = [a, int(rng.integers(max(a + 60, n // 2), hi))]
    deltas = [float(sz * mean * rng.choice((-1.0, 1.0))) for sz in sizes]
    y = y0.copy()
    for p, d in zip(pos, deltas):
        y[p:] += d
    day0 = idx[0]

    def days(steps):
        return [int((st.date - day0).days) for st in steps]

    native = detect_step_changes(pd.Series(y0, idx), pd.Series(T, idx)).steps
    det = detect_step_changes(pd.Series(y, idx), pd.Series(T, idx)).steps
    planted = [int((idx[p] - day0).days) for p in pos]
    by_day = {int((st.date - day0).days): st for st in det}
    pairs = match_steps(planted, list(by_day))
    steps = []
    for j, (pday, dday) in enumerate(pairs):
        s = {"found": dday is not None, "date_error": None, "det_covered": None}
        if dday is not None:
            st = by_day[dday]
            s["date_error"] = dday - pday
            if st.se is not None:
                # the detector's step is post minus pre of the whole level, native steps included
                s["det_covered"] = abs(st.delta - deltas[j]) <= _t90(None) * st.se
        # the indicator at the true date, in a window bounded by the neighbouring steps
        w0 = pos[j - 1] if j > 0 else 0
        w1 = pos[j + 1] if j + 1 < k else n
        try:
            a = estimate_nre_indicator(
                T[w0:w1],
                y[w0:w1],
                idx[w0:w1],
                start=idx[pos[j]],
                fit_period="reporting",
                reason="injected step",
            )
            s["ind_rel_error"] = abs(a.rate - deltas[j]) / abs(deltas[j])
            s["ind_covered"] = (
                bool(abs(a.rate - deltas[j]) <= _t90(a.fit.df) * a.rate_se)
                if _fin(a.rate_se)
                else None
            )
        except (ValueError, TypeError, np.linalg.LinAlgError):
            s["ind_rel_error"] = s["ind_covered"] = None
        steps.append(s)
    return {
        "steps": steps,
        "spurious": spurious_count(list(by_day), planted, days(native)),
        "native": len(native),
    }


def static_record(fb: _Fit, rep: pd.DataFrame, start: str) -> dict | None:
    """Plant a proportional static-factor change from ``start`` and restore it with the ledger."""
    from camber.mandv.adjustments import StaticFactorAdjustment, apply_adjustments
    from camber.mandv.methods import forecast_savings

    def fc(y):
        return forecast_savings(
            fb.model,
            rep["oat"].to_numpy(dtype=float),
            y,
            cv_rmse=fb.cv_rmse,
            n_baseline=fb.n,
            p_baseline=fb.p,
            confidence=CONFIDENCE,
            rho=fb.rho,
        )

    y0 = rep["energy"].to_numpy(dtype=float)
    after = np.asarray(rep.index >= pd.Timestamp(start))
    sf = StaticFactorAdjustment(
        factor="floor_area",
        method="proportional",
        start=start,
        reason="injected floor-area change",
        baseline_value=1.0,
        reporting_value=STATIC_RATIO,
        affected_share=STATIC_SHARE,
    )
    y = np.where(after, y0 * sf.multiplier, y0)
    r0, r1 = fc(y0), fc(y)
    if r0.declined or r1.declined or not _fin(r0.savings_pct) or not _fin(r1.savings_pct):
        return None
    adj = apply_adjustments(
        r1,
        [sf],
        index=rep.index,
        drivers=rep["oat"].to_numpy(dtype=float),
        measured=y,
        model=fb.model,
    )
    if adj.savings_pct is None or adj.abs_uncertainty is None:
        return None
    return {
        "recovery_error": abs(adj.savings_pct - r0.savings_pct),
        "unadjusted_error": abs(r1.savings_pct - r0.savings_pct),
        "adjusted_covered": abs(adj.savings) <= adj.abs_uncertainty,
        "unadjusted_covered": _fin(r1.abs_uncertainty) and abs(r1.savings) <= r1.abs_uncertainty,
    }


# --------------------------------------------------------------------------- scoring


def _oat_f(weather, site):
    from camber.mandv.weather import c_to_f

    s = weather[weather.site_id == site].set_index("timestamp")["airTemperature"]
    return c_to_f(s.loc[BASELINE_YEAR[0] : REPORTING_YEAR[1]])


def _live(d: pd.DataFrame) -> bool:
    """A period whose meter reads something and varies: a meter at one constant value all year
    (a dead meter reads 0; the cleaned set keeps a few) has no saving to measure."""
    e = d["energy"]
    return bool(e.sum() > 0 and e.std() > 0)


def score_building(job) -> dict | None:
    """Every experiment for one building: ``job = (building, base, rep, oat, in_subsample)``.

    A top-level function so that ``--jobs`` can run buildings in worker processes; each building's
    draws come from its own seed, so the result does not depend on the process it ran in.
    Returns ``None`` when no baseline model could be fitted.
    """
    b, base, rep, oat, in_sub = job
    try:
        fb = _Fit(base)
        pl, inj, dev = savings_records(fb, rep, normal_year(oat))
    except (ValueError, TypeError, np.linalg.LinAlgError):
        return None
    out = {"placebo": pl, "injected": inj, "dev": dev, "steps": {}, "static": {}}
    if in_sub:
        rng = np.random.default_rng(building_seed(b))
        for key, sizes in (
            ("single_10", (STEP_SIZES[0],)),
            ("single_20", (STEP_SIZES[1],)),
            ("double_20", (DOUBLE_STEP_SIZE, DOUBLE_STEP_SIZE)),
        ):
            try:
                out["steps"][key] = step_record(rep, rng, sizes=sizes)
            except (ValueError, TypeError, np.linalg.LinAlgError):
                pass
        for key, start in (("full", REPORTING_YEAR[0]), ("mid", "2017-07-01")):
            r = static_record(fb, rep, start)
            if r is not None:
                out["static"][key] = r
    return out


def score_meter(
    meta, weather, meter_csv, label, *, sample: int, jobs: int = 1, progress=False
) -> dict:
    """All four experiments on one meter type -> flat metrics (gated plus ``info.``)."""
    df = pd.read_csv(meter_csv, parse_dates=["timestamp"]).set_index("timestamp")
    eligible = {}
    for b in df.columns:
        if b not in meta.index:
            continue
        site = meta.loc[b, "site_id"]
        oat = _oat_f(weather, site)
        d = daily_frame(df[b].loc[BASELINE_YEAR[0] : REPORTING_YEAR[1]], oat)
        base = d.loc[BASELINE_YEAR[0] : BASELINE_YEAR[1]]
        rep = d.loc[REPORTING_YEAR[0] : REPORTING_YEAR[1]]
        if len(base) >= MIN_DAYS and len(rep) >= MIN_DAYS and _live(base) and _live(rep):
            eligible[b] = (base, rep, oat)
    sub = set(sample_ids(list(eligible), sample))
    work = [(b, *eligible[b], b in sub) for b in sorted(eligible)]
    placebo: dict = {}
    injected: dict = {}
    steps: dict = {"single_10": [], "single_20": [], "double_20": []}
    static: dict = {"full": [], "mid": []}
    identity_dev = 0.0
    n_fit_fail = 0
    if jobs > 1:
        from concurrent.futures import ProcessPoolExecutor

        pool = ProcessPoolExecutor(max_workers=jobs)
        results = pool.map(score_building, work, chunksize=8)  # in input order
    else:
        pool = None
        results = map(score_building, work)
    try:
        for i, res in enumerate(results):
            if progress and (i + 1) % 100 == 0:
                print(f"  {label}: {i + 1}/{len(work)}", file=sys.stderr)
            if res is None:
                n_fit_fail += 1
                continue
            identity_dev = max(identity_dev, res["dev"])
            for mk, r in res["placebo"].items():
                placebo.setdefault(mk, []).append(r)
            for key, r in res["injected"].items():
                injected.setdefault(key, []).append(r)
            for key, r in res["steps"].items():
                steps[key].append(r)
            for key, r in res["static"].items():
                static[key].append(r)
    finally:
        if pool is not None:
            pool.shutdown()
    m: dict = {
        f"{label}.n_eligible": len(eligible),
        f"info.{label}.n_fit_failed": n_fit_fail,
        f"info.{label}.forecast_identity_max_dev": float(f"{identity_dev:.3g}"),
    }
    for mk, recs in placebo.items():
        m.update(placebo_metrics(recs, f"{label}.placebo.{mk}"))
    for (s, mk), recs in injected.items():
        m.update(recovery_metrics(recs, f"{label}.inject{int(round(100 * s)):02d}.{mk}"))
    for key, recs in steps.items():
        m.update(step_metrics(recs, f"{label}.steps.{key}"))
    for key, recs in static.items():
        m.update(static_metrics(recs, f"{label}.static.{key}"))
    m["_identity_dev"] = identity_dev
    return m


def metrics_dict(*, sample: int = DEFAULT_SAMPLE, jobs: int = 1, progress=False) -> dict:
    """The full flat metrics dict over the fetched BDG2 data."""
    meta = pd.read_csv(os.path.join(DATA, "metadata.csv")).set_index("building_id")
    weather = pd.read_csv(
        os.path.join(DATA, "weather.csv"),
        usecols=["timestamp", "site_id", "airTemperature"],
        parse_dates=["timestamp"],
    )
    m: dict = {"info.sample_per_meter": sample}
    dev = 0.0
    for meter, label in METERS:
        path = os.path.join(DATA, meter)
        if not os.path.exists(path):
            continue
        mm = score_meter(meta, weather, path, label, sample=sample, jobs=jobs, progress=progress)
        dev = max(dev, mm.pop("_identity_dev"))
        m.update(mm)
    # the forecast identity is exact by construction: anything beyond the results' rounding (savings
    # are reported to 0.01 energy units, ~1e-5 of the smallest meter's year) is a bug
    m["forecast_identity_error"] = 0.0 if dev < IDENTITY_TOL else float(f"{dev:.3g}")
    return m


def _print_summary(m: dict) -> None:
    print("=== BDG2 M&V savings benchmark (baseline 2016, reporting 2017) ===")
    for _, label in METERS:
        if f"{label}.n_eligible" not in m:
            continue
        print(f"\n[{label}] eligible meters: {m[f'{label}.n_eligible']}")
        print("  placebo (no measure): UICF at nominal 90% (|EUR| p50 / p90), declined")
        for mk in [f"{a}_{b}" for a, b in METHOD_KERNELS] + ["forecast_g14_95"]:
            k = f"{label}.placebo.{mk}"
            if f"{k}.uicf" in m:
                nominal = "95%" if mk.endswith("_95") else "90%"
                print(
                    f"    {mk:28s} UICF {m[f'{k}.uicf']:.0%} at {nominal}  "
                    f"|EUR| {m.get(f'{k}.eur_abs_p50', float('nan')):.2f} / "
                    f"{m.get(f'{k}.eur_abs_p90', float('nan')):.2f}  "
                    f"declined {m[f'{k}.declined_rate']:.1%}  (n={m[f'{k}.n']})"
                )
        for s in SAVINGS:
            tag = f"inject{int(round(100 * s)):02d}"
            print(f"  injected {s:.0%} saving: median |error| (p90), coverage, significant")
            for a, b in METHOD_KERNELS:
                k = f"{label}.{tag}.{a}_{b}"
                if f"{k}.abs_error_p50" in m:
                    print(
                        f"    {a + '_' + b:28s} {m[f'{k}.abs_error_p50']:.1%} "
                        f"({m[f'{k}.abs_error_p90']:.1%})  bias "
                        f"{m[f'info.{k}.bias_median']:+.1%}  cover {m[f'{k}.coverage']:.0%}  "
                        f"signif {m[f'{k}.significant_rate']:.0%}"
                    )
        for key in ("single_10", "single_20", "double_20"):
            k = f"{label}.steps.{key}"
            if f"{k}.detect_rate" in m:
                print(
                    f"  steps {key:10s} detect {m[f'{k}.detect_rate']:.0%}  date error p50 "
                    f"{m.get(f'{k}.date_error_p50', float('nan')):.0f} d  spurious/series "
                    f"{m[f'{k}.spurious_per_series']:.2f}  indicator |err| p50 "
                    f"{m.get(f'{k}.indicator_rel_error_p50', float('nan')):.0%} cover "
                    f"{m.get(f'{k}.indicator_coverage', float('nan')):.0%}  detector cover "
                    f"{m.get(f'{k}.detector_coverage', float('nan')):.0%}  "
                    f"(n={m[f'{k}.n_series']}, native steps in "
                    f"{m[f'info.{k}.native_rate']:.0%})"
                )
        for key in ("full", "mid"):
            k = f"{label}.static.{key}"
            if f"{k}.recovery_error_p50" in m:
                print(
                    f"  static factor from {key:4s}: recovery |err| p50 "
                    f"{m[f'{k}.recovery_error_p50']:.2%} (unadjusted "
                    f"{m[f'{k}.unadjusted_error_p50']:.1%}); covers zero "
                    f"{m[f'{k}.adjusted_uicf']:.0%} adjusted vs "
                    f"{m[f'{k}.unadjusted_uicf']:.0%} unadjusted (n={m[f'{k}.n']})"
                )
    print(f"\nforecast identity error: {m['forecast_identity_error']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="BDG2 M&V savings benchmark (placebo + injection)")
    ap.add_argument("--json", metavar="PATH", help="write every metric, info.* included")
    ap.add_argument("--gate", metavar="PATH", help="gate against a committed baseline")
    ap.add_argument("--tol", type=float, default=0.05)
    ap.add_argument("--update-baseline", metavar="PATH", help="write the gated metrics")
    ap.add_argument(
        "--sample",
        type=int,
        default=DEFAULT_SAMPLE,
        help=f"meters per type for the step and static experiments (default {DEFAULT_SAMPLE}; "
        "0 = all). The gate needs the value the baseline was made with.",
    )
    ap.add_argument(
        "--jobs",
        type=int,
        default=os.cpu_count() or 1,
        help="worker processes (default: every CPU); the metrics do not depend on it",
    )
    ap.add_argument("--progress", action="store_true")
    args = ap.parse_args(argv)

    if not os.path.exists(os.path.join(DATA, METERS[0][0])) or not os.path.exists(
        os.path.join(DATA, "metadata.csv")
    ):
        print("Data not found. Run:  python examples/bdg2/fetch.py")
        return 1

    m = metrics_dict(sample=args.sample, jobs=max(1, args.jobs), progress=args.progress)
    _print_summary(m)
    if args.json:
        json.dump(m, open(args.json, "w"), indent=2, sort_keys=True)
    if args.update_baseline:
        json.dump(gated(m), open(args.update_baseline, "w"), indent=2, sort_keys=True)
        print(f"wrote baseline -> {args.update_baseline}")
    status = 0
    if m["forecast_identity_error"] != 0.0:
        print("✗ forecast recovery is not exact: S_f(s) - s*O_r != S_f(0)")
        status = 2
    if args.gate:
        chk = check_against_baseline(
            gated(m),
            json.load(open(args.gate)),
            tol=args.tol,
            lower_is_better=LOWER_IS_BETTER,
            strict_new=True,
        )
        print(baseline_report(chk, label="BDG2 savings benchmark", tol=args.tol))
        if not chk.passed:
            status = 2
    return status


if __name__ == "__main__":
    raise SystemExit(main())
