"""Size-relative gates and constant-speed handling for the pump / loop drift rules (private, #150).

The five pump and loop drift detectors were written with absolute gates: a 50 gpm flow floor, a
10 gpm identifiable flow span, a 15 % speed floor, a 15 / 30 gpm flow-deficit floor and a 1 / 2 kW
power-excess floor. Those are reasonable for a pump moving several hundred gpm and meaningless for a
40 gpm one -- it never reaches 50 gpm, so three detectors decline, and a worn impeller's whole
deficit sits under the 15 gpm floor. This module scales them to the pump in front of the rule, the
way :func:`camber.chillerbaseline.size_relative_load_gates` (0.84) does for chillers:

* **Flow gates** are 10 % / 20 % of the baseline's p95 flow, capped at the 50 / 10 gpm the rules
  always had, so every pump whose baseline p95 is 500 gpm or more is gated exactly as before.
* **Speed gates** sit just above the pump's learned VFD floor (:func:`camber.chwpump.
  learn_vfd_floor`): a pump parked at its minimum speed is held there by the drive, not by the
  load, so those samples say nothing about flow or head *at* a speed. Without a clear floor the
  gate falls back to the 15 % the rules always used.
* **Magnitude backstops** (the gpm deficit and kW excess floors) become fractions of the baseline's
  own p95 flow / power, capped at today's values. The sigma floors still carry the weight.
* **Constant-speed pumps** -- a normalizer that barely moves over the baseline (coefficient of
  variation <= 2 %), or no speed point at all on a pump whose status shows it running -- cannot
  identify a slope. Rather than decline, they are compared as a flat level inside the band they
  were observed at (``fit_load_baseline(level_fallback=True)``), with a caveat saying so.

An explicit constructor value always wins over the size-relative one.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..chillerbaseline import size_relative_load_gates
from ..chwpump import learn_vfd_floor
from ..model.roles import Role

# The absolute gates the rules were written with; the size-relative gates never exceed them.
FLOW_GATE_CAP = 50.0  # gpm: the former MIN_LOAD of loop_deltat / loop_dp / pump_power
FLOW_SPAN_CAP = 10.0  # gpm: the former identifiable-span gate (fit_load_baseline's default)
SPEED_SPAN = 10.0  # % speed: the identifiable speed span (scale-free, unchanged)
SPEED_FALLBACK = 15.0  # % speed: the former MIN_SPEED, used when no VFD floor is learned
SPEED_FLOOR_TOL_PCT = 2.0  # % speed above the learned floor: its 1-point plateau + drive jitter

# A normalizer whose coefficient of variation over the running baseline is at or under this is
# constant (a constant-speed pump, or a constant-flow loop).
CONSTANT_CV = 0.02
# A flat-level baseline is valid this fraction of the median normalizer either side of its band.
LEVEL_BAND_FRAC = 0.05

# Magnitude backstops as fractions of the baseline p95 (capped at the former absolute values),
# chosen from the 20-1000 gpm pumpsim sweep (docs/PUMP-DRIFT.md). On the original 1000 gpm pump
# (p95 983-993 gpm, 19.7-19.9 kW) the old floors were 1.5 % of flow and 5.1 % of power, so any
# fraction at or above those leaves every full-size result unchanged.
# - Flow: the sigma floor binds for every fraction from 1.6 % to 3 % at every size, so the backstop
#   only matters on a very tight baseline; 2 % / 4 % keeps it at or above typical flow-meter
#   accuracy (about 1-2 % of reading) and reaches the 15 / 30 gpm cap at a 750 gpm pump.
# - Power: the kW floor does bind on small pumps (6 % delays first detection of bearing drag by
#   0.125-0.25 severity against full size); 5.5 % / 11 % keeps it within 0.125 and reaches the
#   1 / 2 kW cap at an 18.2 kW pump.
FLOW_WARN_FRAC = 0.02
FLOW_FAULT_FRAC = 0.04
POWER_WARN_FRAC = 0.055
POWER_FAULT_FRAC = 0.11

_SPEED_ROLES = frozenset({Role.CHW_PUMP_SPEED, Role.HW_PUMP_SPEED})


@dataclass(frozen=True)
class Gates:
    """The (minimum normalizer, identifiable span) gates a rule uses, and where they came from."""

    min_load: float
    min_span: float
    source: str  # explicit | size_relative | learned_vfd_floor | default
    vfd_floor: float | None = None


def is_speed(role) -> bool:
    """True when ``role`` is a pump-speed normalizer (% of full speed) rather than a flow."""
    if role in _SPEED_ROLES:
        return True
    return str(getattr(role, "value", role)).endswith("_speed")


def _positive(series) -> pd.Series:
    s = pd.to_numeric(pd.Series(series), errors="coerce")
    return s[np.isfinite(s) & (s > 0)]


def p95(series) -> float:
    """The 95th percentile of the positive samples (NaN when there are none)."""
    s = _positive(series)
    return float(np.percentile(s.to_numpy(dtype=float), 95.0)) if len(s) else float("nan")


def speed_gate(speed, min_speed: float | None) -> Gates:
    """The speed gate: explicit, else just above the learned VFD floor, else 15 %."""
    if min_speed is not None:
        return Gates(float(min_speed), SPEED_SPAN, "explicit")
    floor = learn_vfd_floor(_positive(speed)) if speed is not None else None
    if floor is None:
        return Gates(SPEED_FALLBACK, SPEED_SPAN, "default")
    return Gates(round(floor + SPEED_FLOOR_TOL_PCT, 4), SPEED_SPAN, "learned_vfd_floor", floor)


def normalizer_gates(series, role, min_load: float | None, min_span: float | None) -> Gates:
    """Gates for a flow normalizer (size-relative) or a speed normalizer (:func:`speed_gate`)."""
    if is_speed(role):
        g = speed_gate(series, min_load)
        span = float(min_span) if min_span is not None else g.min_span
        return Gates(g.min_load, span, g.source, g.vfd_floor)
    vals = _positive(series) if series is not None else pd.Series(dtype=float)
    lo, span = size_relative_load_gates(
        vals,
        min_load=min_load,
        min_load_span=min_span,
        full_min_load=FLOW_GATE_CAP,
        full_min_load_span=FLOW_SPAN_CAP,
    )
    explicit = min_load is not None and min_span is not None
    return Gates(lo, span, "explicit" if explicit else "size_relative")


def scaled_floor(series, explicit: float | None, frac: float, cap: float) -> float:
    """``explicit``, else ``min(cap, frac * p95(series))`` (``cap`` when there are no samples)."""
    if explicit is not None:
        return float(explicit)
    top = p95(series) if series is not None else float("nan")
    if not top == top:
        return float(cap)
    return round(min(float(cap), frac * top), 6)


def normalizer_cv(series) -> float:
    """Coefficient of variation (std / mean) of the positive samples; NaN with fewer than 2."""
    s = _positive(series)
    if len(s) < 2:
        return float("nan")
    return float(s.std() / s.mean())


def level_span(frame: pd.DataFrame, load_col, metric_col, min_load: float) -> float:
    """``min_load_span`` that forces a flat-level fit valid ``LEVEL_BAND_FRAC`` either side.

    Passed to ``fit_load_baseline(level_fallback=True)``: always wider than the observed span, so
    the fit is a level, and the excess becomes the level's ``load_band`` (half on each side).
    """
    load = pd.to_numeric(frame[load_col], errors="coerce") if load_col in frame else None
    metric = pd.to_numeric(frame[metric_col], errors="coerce") if metric_col in frame else None
    if load is None or metric is None:
        return FLOW_SPAN_CAP
    x = load[metric.notna() & load.notna() & (load >= min_load)]
    if not len(x):
        return FLOW_SPAN_CAP
    span = float(x.max() - x.min())
    return span + 2.0 * LEVEL_BAND_FRAC * float(x.median())


#: The fixed "speed" a status-only constant-speed pump is scored at (the column is synthetic).
STATUS_ONLY_SPEED = 100.0


def constant_speed_caveat(signal: str, mode: str, cv: float) -> str:
    """The caveat a constant-speed pump's flat-level comparison carries."""
    if mode == "status_only":
        why = "no speed point is mapped and its status shows it running"
    else:
        why = f"its speed varied only {100.0 * cv:.1f} % over the baseline"
    return (
        f"constant-speed pump ({why}): {signal} is compared as a flat level at its fixed speed, "
        "not along a speed curve. A level change is pump wear or a change in the system curve -- "
        "a throttled or re-set balancing valve moves a constant-speed pump back along its curve "
        "just as wear does -- so check the valves before condemning the impeller"
    )


def constant_flow_caveat(signal: str, cv: float, lo: float, hi: float, unit: str) -> str:
    """The caveat a constant-normalizer loop rule's flat-level comparison carries."""
    return (
        f"the loop's normalizer barely moved over the baseline (coefficient of variation "
        f"{100.0 * cv:.2f} %), so {signal} is compared as a flat level valid for "
        f"{lo:.4g}-{hi:.4g} {unit}, not along a curve; samples outside that band are not scored"
    )


def signed(value: float, decimals: int) -> str:
    """``value`` with its sign, as an ``ok`` summary prints it; a shift that rounds to zero is 0."""
    text = f"{value:+.{decimals}f}"
    return text[1:] if float(text) == 0.0 else text
