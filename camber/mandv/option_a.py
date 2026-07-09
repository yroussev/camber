"""IPMVP Option A — retrofit isolation with key-parameter measurement.

Option A measures the parameter(s) that most affect savings and **stipulates** the rest. The classic
case: a lighting or motor retrofit where you *measure* the connected power before and after and
*stipulate* the operating hours. Savings = (measured Δpower) × (stipulated duty).

This complements the shipped Option B (:mod:`camber.mandv.retrofit_isolation`, a metered driver
model) and Option C (:mod:`camber.mandv.stats`, whole-facility). It is deliberately honest about the
stipulation: the result names what was measured vs stipulated, and the stipulated portion carries
uncertainty this method does not quantify. numpy only.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np


def _mean(x) -> float:
    """Scalar as-is, or the mean of an array/Series (ignoring NaNs)."""
    arr = np.asarray(x, dtype=float).reshape(-1)
    return float(np.nanmean(arr)) if arr.size else float("nan")


@dataclass
class OptionAResult:
    """Measured-parameter savings with a stipulated duty (IPMVP Option A)."""

    baseline_measured: float         # measured parameter, baseline (e.g. kW)
    reporting_measured: float        # measured parameter, reporting
    measured_delta: float            # baseline − reporting (positive = reduction)
    stipulated_factor: float         # the stipulated duty (e.g. annual operating hours)
    savings: float                   # measured_delta × stipulated_factor
    unit: str                        # unit of savings (e.g. "kWh")
    basis: str                       # what was measured vs stipulated
    reduction_pct: float             # measured_delta / baseline_measured

    def as_dict(self) -> dict:
        return asdict(self)


def option_a_savings(baseline_measured, reporting_measured, *, stipulated_factor: float,
                     unit: str = "kWh", measured_name: str = "power (kW)",
                     stipulated_name: str = "annual operating hours") -> OptionAResult:
    """IPMVP Option A savings: measured Δparameter × a stipulated duty.

    ``baseline_measured`` / ``reporting_measured`` are the measured parameter (scalar, or an array/
    Series whose mean is taken — e.g. sampled kW). ``stipulated_factor`` is the stipulated duty
    (e.g. annual hours). Savings is their product; the ``basis`` records the measured-vs-stipulated
    split for auditability.
    """
    b = _mean(baseline_measured)
    r = _mean(reporting_measured)
    delta = b - r
    savings = delta * stipulated_factor
    pct = delta / b if b not in (0.0, float("nan")) and np.isfinite(b) and b != 0 else float("nan")
    return OptionAResult(
        baseline_measured=round(b, 4), reporting_measured=round(r, 4),
        measured_delta=round(delta, 4), stipulated_factor=float(stipulated_factor),
        savings=round(savings, 2), unit=unit,
        basis=f"measured {measured_name} (Δ={delta:.4g}); stipulated {stipulated_name}"
              f"={stipulated_factor:g}",
        reduction_pct=round(pct, 4) if np.isfinite(pct) else float("nan"))


def stipulated_annual_hours(hours_per_day: float, days_per_week: float = 5.0,
                            weeks_per_year: float = 52.0) -> float:
    """A stipulated annual-hours factor from a simple schedule (a common Option-A stipulation)."""
    return float(hours_per_day * days_per_week * weeks_per_year)
