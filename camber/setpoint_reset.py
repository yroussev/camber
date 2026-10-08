"""Is a trended setpoint *reset*, or was it just changed? (#63)

A demand-driven reset moves its setpoint again and again with its driver: zone requests (G36
trim-and-respond), the outdoor air temperature (an OAT reset), or supply airflow for a duct-static
reset. A setpoint that is flat for weeks and then steps once -- an operator edit, a sequence change,
a seasonal switch-over -- also has a large range over the window, so "the range is wide" is not
evidence of a reset. :func:`classify_setpoint_reset` separates the cases the static-pressure and
supply-air reset rules need:

``flat``
    the setpoint's range over the window is below ``min_range`` -- no reset.
``step``
    it moved on too few days to be a reset (fewer than ``min_active_days`` and fewer than
    ``min_active_frac`` of the days), and it otherwise sits at a few held levels: a one-time step or
    a handful of manual changes -- **not** a reset.
``reset``
    it moved on enough days (or, in a window shorter than ``min_active_days``, through at least
    ``min_cycles`` up/down cycles) **and** moved with its driver: ``|Spearman rho| >= min_corr``
    between setpoint and driver on the days it moved.
``varies``
    it moved repeatedly, but either no driver was supplied (``driver_checked`` is False) or it did
    not move with the driver supplied -- a schedule, a manual habit, or a reset whose driver isn't
    trended. Rules decide how far to trust it.
``unclear``
    it moved, but neither repeatedly nor as a clean step (for example a single ramp in a short
    window) -- not enough to call it either way.
``insufficient``
    fewer than ``min_samples`` usable samples.

**Why these thresholds.** A day "moved" when the setpoint's range that day reaches ``move_min``
*and* fewer than 90 % of that day's samples sit at its modal value (so a day whose only movement is
one step, or one brief excursion, is still a *held* day). Three moving days is the fewest that shows
the movement recurs rather than happened once or twice, and the 10 % floor keeps a months-long
window from being called "reset" on the strength of a few edits. ``min_corr = 0.3`` is the
conventional floor of a moderate monotonic association: a real reset tracks its driver far more
tightly (rho 0.5-0.9 on hourly data), and 0.3 still admits trim-and-respond's lag and hysteresis.
The sign is not imposed -- sequences reset in either direction (an OAT reset raises SAT in cool
weather; a static reset can be tuned against fan speed or airflow either way). Movement is judged on
the samples the caller passes (the rules pass fan-on samples when a fan signal exists): an
occupied/unoccupied setpoint swap while the fan is off is not a reset.

numpy/pandas. Provisional API (0.91, #63).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

__all__ = [
    "SetpointResetBehaviour",
    "classify_setpoint_reset",
]

_HELD_SHARE = 0.90  # a day with >= 90 % of samples at its modal value is a "held" day
_STEP_HELD_SHARE = 0.80  # a "step" series spends >= 80 % of samples at its top-3 held levels
_MIN_DAY_SAMPLES = 4  # a calendar day needs this many samples to be judged


@dataclass
class SetpointResetBehaviour:
    """How a trended setpoint behaved over the window (see the module docstring for ``kind``)."""

    kind: str  # flat | step | reset | varies | unclear | insufficient
    sp_range: float | None  # max - min over the samples judged
    n_samples: int
    n_days: int  # calendar days with >= 4 samples
    n_moving_days: int  # days the setpoint moved (range >= move_min and not held)
    n_cycles: int  # up/down reversals of moves >= move_min, halved
    n_levels: int  # distinct held levels (>= 1 % of samples each)
    levels: list  # the held levels, most-used first (at most 3)
    driver: str | None  # the driver's label, when one was supplied
    driver_rho: float | None  # Spearman rho, setpoint vs driver, on the moving days
    driver_checked: bool  # a driver was supplied and had enough overlap to test
    label: str  # a short human-readable description

    def as_dict(self) -> dict:
        """Return the result as a plain dict."""
        return asdict(self)

    @property
    def is_reset(self) -> bool:
        """True only when the setpoint moved repeatedly *with* its driver."""
        return self.kind == "reset"


def _modal(x: pd.Series, tol: float) -> tuple[float, float]:
    """``(modal value, share of samples within tol of it)``."""
    q = np.round(x.to_numpy(dtype=float) / tol) * tol
    vals, counts = np.unique(q, return_counts=True)
    mode = float(vals[np.argmax(counts)])
    return mode, float(np.mean(np.abs(x.to_numpy(dtype=float) - mode) <= tol))


def _levels(x: pd.Series, tol: float) -> tuple[list, float]:
    """Held levels (>= 1 % of samples, most-used first) and the share at the top three."""
    q = np.round(x.to_numpy(dtype=float) / tol) * tol
    vals, counts = np.unique(q, return_counts=True)
    order = np.argsort(-counts)
    share = counts[order] / counts.sum()
    levels = [float(vals[i]) for i, s in zip(order, share) if s >= 0.01]
    return levels, float(share[:3].sum())


def _cycles(x: np.ndarray, move_min: float) -> int:
    """Up/down cycles: direction reversals between moves of at least ``move_min``, halved."""
    if len(x) == 0:
        return 0
    anchor, direction, reversals = x[0], 0, 0
    for v in x[1:]:
        d = v - anchor
        if abs(d) >= move_min:
            s = 1 if d > 0 else -1
            if direction and s != direction:
                reversals += 1
            direction, anchor = s, v
    return reversals // 2


def _judged_days(s: pd.Series, move_min: float) -> tuple[list, list]:
    """``(days, moving)``: the ``(day, samples)`` pairs judged (>= 4 samples) and the days among
    them the setpoint moved on (range >= ``move_min`` and not held at its modal value)."""
    tol = move_min / 2.0
    days = [(d, x) for d, x in s.groupby(s.index.normalize()) if len(x) >= _MIN_DAY_SAMPLES]
    moving = []
    for d, x in days:
        if float(x.max() - x.min()) < move_min:
            continue
        if _modal(x, tol)[1] < _HELD_SHARE:
            moving.append(d)
    return days, moving


def _held_samples(sp, *, move_min: float) -> pd.Series:
    """The samples of ``sp`` on days the setpoint did **not** move (0.102, #119).

    A boolean Series on ``sp``'s index: True for every non-missing sample outside the moving days
    :func:`classify_setpoint_reset` counts (a day with too few samples to judge is not a moving
    day). What a "no reset" evidence chart shades.
    """
    raw = pd.Series(sp)
    s = pd.Series(sp, dtype=float).dropna()
    if not isinstance(s.index, pd.DatetimeIndex):
        s.index = pd.to_datetime(s.index)
    _days, moving = _judged_days(s.sort_index(), move_min)
    idx = pd.DatetimeIndex(pd.to_datetime(raw.index))
    held = ~idx.normalize().isin(moving) & pd.to_numeric(raw, errors="coerce").notna().to_numpy()
    return pd.Series(held, index=raw.index)


def classify_setpoint_reset(
    sp,
    driver=None,
    *,
    driver_label: str | None = None,
    min_range: float,
    move_min: float,
    min_active_days: int = 3,
    min_active_frac: float = 0.10,
    min_cycles: int = 3,
    min_corr: float = 0.3,
    min_samples: int = 12,
    units: str = "",
) -> SetpointResetBehaviour:
    """Classify a trended setpoint as flat / step / reset / varies / unclear / insufficient.

    ``sp`` is the setpoint series (a DatetimeIndex; pass only the samples to judge, e.g. fan-on).
    ``driver`` (optional, aligned by index) is what a reset should follow -- zone requests, OAT or
    supply airflow; ``driver_label`` names it in the result. ``min_range`` is the whole-window range
    below which the setpoint is flat and ``move_min`` the per-day movement that counts as moving
    (both in the setpoint's units). See the module docstring for the other thresholds.
    """
    s = pd.Series(sp, dtype=float).dropna()
    if not isinstance(s.index, pd.DatetimeIndex):
        s.index = pd.to_datetime(s.index)
    s = s.sort_index()
    n = int(len(s))
    empty = SetpointResetBehaviour(
        "insufficient", None, n, 0, 0, 0, 0, [], driver_label, None, False, ""
    )
    if n < min_samples:
        empty.label = f"too few setpoint samples ({n}) to judge"
        return empty
    rng = float(s.max() - s.min())
    tol = move_min / 2.0
    days, moving = _judged_days(s, move_min)
    n_days, n_moving = len(days), len(moving)
    cycles = _cycles(s.to_numpy(dtype=float), move_min)
    levels, held_share = _levels(s, tol)
    top = [round(v, 3) for v in levels[:3]]

    def result(kind: str, label: str, rho: float | None = None) -> SetpointResetBehaviour:
        return SetpointResetBehaviour(
            kind=kind,
            sp_range=round(rng, 4),
            n_samples=n,
            n_days=n_days,
            n_moving_days=n_moving,
            n_cycles=cycles,
            n_levels=len(levels),
            levels=top,
            driver=driver_label,
            driver_rho=rho,
            driver_checked=rho is not None,
            label=label,
        )

    u = f" {units}" if units else ""
    if rng < min_range:
        return result(
            "flat",
            f"flat (range {rng:.2f}{u} < {min_range:g}{u})",
        )
    if n_days >= min_active_days:
        repeated = n_moving >= max(min_active_days, math.ceil(min_active_frac * n_days))
    else:  # a short window: judge on cycles instead of days
        repeated = cycles >= min_cycles
    if not repeated:
        if held_share >= _STEP_HELD_SHARE:
            lv = ", ".join(f"{v:g}" for v in levels[:3])
            what = "one-time step" if len(levels) <= 2 else "a few manual changes"
            return result(
                "step",
                (
                    f"{what} between held levels ({lv}{u}), moving on {n_moving} of {n_days} "
                    "days -- a step or manual change, not a reset"
                ),
            )
        return result(
            "unclear",
            (
                f"moves (range {rng:.2f}{u}) but on only {n_moving} of {n_days} days and "
                f"{cycles} cycles -- too little to call a reset"
            ),
        )
    rho = None
    if driver is not None:
        dv = pd.Series(driver, dtype=float)
        if not isinstance(dv.index, pd.DatetimeIndex):
            dv.index = pd.to_datetime(dv.index)
        pair = pd.DataFrame({"sp": s, "d": dv.reindex(s.index)}).dropna()
        if n_days >= min_active_days:
            pair = pair[pair.index.normalize().isin(moving)]
        if len(pair) >= min_samples and pair["sp"].nunique() > 1 and pair["d"].nunique() > 1:
            r = pair["sp"].rank().corr(pair["d"].rank())
            rho = None if r is None or not np.isfinite(r) else round(float(r), 3)
    span = (
        f"moves on {n_moving} of {n_days} days"
        if n_days >= min_active_days
        else (f"{cycles} up/down cycles")
    )
    if rho is None:
        why = "no driver trended" if driver is None else f"too little overlap with {driver_label}"
        return result(
            "varies",
            f"varies repeatedly ({span}); {why} to confirm it is demand-driven",
        )
    if abs(rho) >= min_corr:
        return result(
            "reset",
            f"resets with {driver_label} ({span}; rho {rho:+.2f})",
            rho,
        )
    return result(
        "varies",
        (
            f"varies repeatedly ({span}) but not with {driver_label} (rho {rho:+.2f}, "
            f"|rho| < {min_corr:g}) -- not confirmed as a reset"
        ),
        rho,
    )
