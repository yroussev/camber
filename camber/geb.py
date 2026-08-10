"""Grid-interactive efficient buildings (GEB) analytics — demand response, flexibility, carbon.

Beyond *using less* energy (efficiency), a grid-interactive building can *shift and shed* load in
response to grid signals. This module quantifies that potential from interval load:

- **Demand response** — during an event window, how much load was shed versus an expected
  baseline (kW, kWh, %), and the post-event **rebound**.
- **Flexibility** — the sheddable load above baseload, and the peak-to-average headroom.
- **Carbon-aware shifting** — the CO₂ saved by moving energy from high- to low-emissions hours,
  given an hourly grid-emissions-factor series.

Dependency-light (numpy/pandas); pairs with `camber.demand` (peak analytics) and `camber.carbon`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .timegrid import interval_hours as _interval_hours

__all__ = [
    "DemandResponseResult",
    "demand_response",
    "FlexibilityResult",
    "flexibility",
    "carbon_aware_shift",
    "OperationScore",
    "operation_score",
]


@dataclass
class DemandResponseResult:
    """Load shed during an event vs the expected baseline, plus rebound."""

    event_hours: float
    baseline_kwh: float  # expected energy over the event absent the DR action
    actual_kwh: float
    energy_shed_kwh: float  # baseline − actual (positive = shed)
    avg_shed_kw: float
    peak_shed_kw: float
    pct_shed: float  # of baseline
    rebound_kwh: float  # post-event overshoot above baseline (snap-back)

    def as_dict(self) -> dict:
        return asdict(self)


def demand_response(
    load_kw: pd.Series, baseline_kw, *, event_start, event_end, rebound_hours: float = 2.0
) -> DemandResponseResult:
    """Quantify a demand-response event against an expected baseline.

    ``load_kw`` is metered demand; ``baseline_kw`` is the expected load absent the event — a scalar
    or a Series aligned to ``load_kw`` (e.g. a typical-day profile or a model projection). Shed is
    ``baseline − actual`` over ``[event_start, event_end]``; rebound is energy *above* baseline in
    the ``rebound_hours`` after the event (the snap-back that erodes net benefit).
    """
    s = load_kw.dropna()
    idx = pd.DatetimeIndex(s.index)
    if np.isscalar(baseline_kw):
        base = pd.Series(float(baseline_kw), index=s.index)  # type: ignore[arg-type]  # scalar
    elif isinstance(baseline_kw, pd.Series):
        base = baseline_kw.reindex(s.index).ffill()
    else:  # array/list: must be one value per load sample
        arr = np.asarray(baseline_kw, dtype=float)
        if arr.shape != (len(load_kw),):
            raise ValueError(
                "baseline_kw array must have one value per load_kw sample; "
                "pass a scalar or an index-aligned Series instead"
            )
        base = pd.Series(arr, index=load_kw.index).reindex(s.index)
    start, end = pd.Timestamp(event_start), pd.Timestamp(event_end)
    hours = pd.Series(_interval_hours(idx), index=s.index)  # interval width per sample, aligned

    ev = (idx >= start) & (idx <= end)
    shed_kw = base[ev] - s[ev]
    energy_shed = float((shed_kw * hours[ev]).sum())
    base_kwh = float((base[ev] * hours[ev]).sum())
    actual_kwh = float((s[ev] * hours[ev]).sum())
    ev_hours = float(hours[ev].sum())

    post = (idx > end) & (idx <= end + pd.Timedelta(hours=rebound_hours))
    rebound = (
        float(((s[post] - base[post]).clip(lower=0) * hours[post]).sum()) if post.any() else 0.0
    )

    return DemandResponseResult(
        event_hours=round(ev_hours, 2),
        baseline_kwh=round(base_kwh, 2),
        actual_kwh=round(actual_kwh, 2),
        energy_shed_kwh=round(energy_shed, 2),
        avg_shed_kw=round(float(shed_kw.mean()) if len(shed_kw) else 0.0, 2),
        peak_shed_kw=round(float(shed_kw.max()) if len(shed_kw) else 0.0, 2),
        pct_shed=round(energy_shed / base_kwh, 4) if base_kwh else float("nan"),
        rebound_kwh=round(rebound, 2),
    )


@dataclass
class FlexibilityResult:
    """Static load-flexibility headroom from an interval-load profile."""

    baseload_kw: float  # low-percentile (always-on) load
    peak_kw: float
    mean_kw: float
    sheddable_kw: float  # mean above baseload (the flexible portion)
    sheddable_frac: float  # sheddable / mean
    peak_to_average: float

    def as_dict(self) -> dict:
        return asdict(self)


def flexibility(load_kw: pd.Series, *, baseload_pct: float = 10.0) -> FlexibilityResult:
    """Estimate flexible (sheddable) load above baseload. ``baseload_pct`` is the percentile used
    as the always-on floor (default 10th)."""
    s = load_kw.dropna()
    if s.empty:
        return FlexibilityResult(0, 0, 0, 0, float("nan"), float("nan"))
    base = float(np.percentile(s, baseload_pct))
    mean = float(s.mean())
    peak = float(s.max())
    sheddable = max(0.0, mean - base)
    return FlexibilityResult(
        baseload_kw=round(base, 2),
        peak_kw=round(peak, 2),
        mean_kw=round(mean, 2),
        sheddable_kw=round(sheddable, 2),
        sheddable_frac=round(sheddable / mean, 4) if mean else float("nan"),
        peak_to_average=round(peak / mean, 3) if mean else float("nan"),
    )


def carbon_aware_shift(load_kw: pd.Series, emissions_factor, shift_kwh: float) -> dict:
    """CO₂ saved by shifting ``shift_kwh`` from the highest- to the lowest-emissions hours.

    ``emissions_factor`` is an hourly grid factor (kgCO₂/kWh) — a Series aligned to ``load_kw`` or
    an array. Shifting load from the dirtiest to the cleanest hours saves
    ``shift_kwh × (EF_high_mean − EF_low_mean)``; this bounds the carbon value of flexibility.
    """
    ef = (
        emissions_factor.reindex(load_kw.index)
        if isinstance(emissions_factor, pd.Series)
        else pd.Series(np.asarray(emissions_factor, dtype=float), index=load_kw.index)
    )
    ef = ef.dropna()
    if ef.empty or shift_kwh <= 0:
        return {"co2_saved_kg": 0.0, "ef_high": float("nan"), "ef_low": float("nan")}
    n = max(1, int(len(ef) * 0.1))  # top/bottom decile of hours
    hi = float(ef.nlargest(n).mean())
    lo = float(ef.nsmallest(n).mean())
    return {
        "co2_saved_kg": round(shift_kwh * (hi - lo), 3),
        "ef_high": round(hi, 4),
        "ef_low": round(lo, 4),
        "spread_kg_per_kwh": round(hi - lo, 4),
    }


@dataclass
class OperationScore:
    """How well load is timed against a cost/carbon signal (1 = ideal, 0 = worst-case)."""

    signal: str  # "price" | "carbon" (label only)
    load_weighted_avg: (
        float  # Σ(load·signal)/Σload — what the building actually paid/emitted per kWh
    )
    flat_avg: float  # time-average of the signal (a naive as-if-constant operation)
    best_case: float  # load-weighted avg if all energy landed in the cheapest hours
    worst_case: float  # ...in the most expensive hours
    score: float  # (worst − actual)/(worst − best), clamped 0..1
    vs_flat_pct: float  # % better(−)/worse(+) than flat operation

    def as_dict(self) -> dict:
        return asdict(self)


def operation_score(load_kw: pd.Series, signal, *, label: str = "price") -> OperationScore:
    """Score how well load timing aligns with a cost or carbon ``signal`` (per-kWh, hourly).

    ``signal`` is a $/kWh or kgCO₂/kWh Series/array aligned to ``load_kw``. The **load-weighted
    average** signal is what the building actually incurred per kWh; ``score`` places it between the
    worst case (all energy in the priciest hours) and best case (cheapest hours). 1 = every kWh
    landed in the cleanest/cheapest hours; 0.5 ≈ flat/indifferent operation.
    """
    sig = (
        signal.reindex(load_kw.index)
        if isinstance(signal, pd.Series)
        else pd.Series(np.asarray(signal, dtype=float), index=load_kw.index)
    )
    df = pd.DataFrame({"l": load_kw, "s": sig}).dropna()
    if df.empty or df["l"].sum() <= 0:
        return OperationScore(
            label,
            float("nan"),
            float("nan"),
            float("nan"),
            float("nan"),
            float("nan"),
            float("nan"),
        )
    total = float(df["l"].sum())
    lwa = float((df["l"] * df["s"]).sum() / total)
    flat = float(df["s"].mean())
    # Best/worst bound: keep the SAME set of load magnitudes and the SAME signal values, but pair
    # them optimally. By the rearrangement inequality, Σ(load·signal) is minimized when the
    # largest loads meet the smallest signal values (best), maximized when they meet the largest
    # (worst). So sort load ascending and pair with signal descending (best) / ascending (worst).
    loads = np.sort(df["l"].to_numpy())
    sig_asc = np.sort(df["s"].to_numpy())
    best = float((loads * sig_asc[::-1]).sum() / total)  # big loads × small signal
    worst = float((loads * sig_asc).sum() / total)  # big loads × big signal
    span = worst - best
    score = (worst - lwa) / span if span > 0 else float("nan")
    score = min(1.0, max(0.0, score)) if score == score else float("nan")
    return OperationScore(
        signal=label,
        load_weighted_avg=round(lwa, 5),
        flat_avg=round(flat, 5),
        best_case=round(best, 5),
        worst_case=round(worst, 5),
        score=round(score, 4) if score == score else float("nan"),
        vs_flat_pct=round(100.0 * (lwa - flat) / flat, 2) if flat else float("nan"),
    )
