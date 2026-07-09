"""Pattern G — templated subsystem diagnostic scatters.

Each subsystem has an *expected signature*: SAT should track a reset schedule against OAT, the
economizer should open for free cooling and lock out when it's hot, heating and cooling valves
should never both be open. A **diagnostic scatter** plots the measured behavior with that
**expected region overlaid** and the **violating points shaded** — so the same figure is both a
browse-able chart and a rule's evidence.

A :class:`DiagnosticTemplate` names the two roles to plot and an ``expected(x) -> (low, high)`` band;
:func:`diagnostic_scatter` renders it and returns the **violating mask** (feeding pattern J — every
rule renders its evidence). A small packaged :data:`TEMPLATES` set covers the common subsystems, and
the constructors (:func:`band`, :func:`reset_line`, :func:`economizer_template`,
:func:`no_simultaneous_template`) build your own. Valve/damper signals are the normalized 0–1
fractions the ingest layer produces. numpy/pandas; matplotlib lazy-imported.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from ..model.roles import Role


@dataclass
class DiagnosticTemplate:
    """An expected-behavior signature for a subsystem: two roles + an acceptable y-band."""

    name: str
    x: object                    # Role | str — the x-axis point
    y: object                    # Role | str — the y-axis point
    expected: Callable           # expected(x: np.ndarray) -> (low: np.ndarray, high: np.ndarray)
    xlabel: str = ""
    ylabel: str = ""
    cite: str = ""


def _col(frame: pd.DataFrame, key):
    """Resolve a Role-or-str column from a role-frame (columns may be Role enums or strings)."""
    if key in frame.columns:
        return frame[key]
    want = getattr(key, "name", str(key))
    for c in frame.columns:
        if getattr(c, "name", str(c)) == want or str(c) == str(key):
            return frame[c]
    raise KeyError(f"template column {key!r} not in frame")


def diagnostic_scatter(frame: pd.DataFrame, template: DiagnosticTemplate, *, ax=None,
                       shade: bool = True, tolerance: float = 0.0):
    """Plot ``template``'s two roles with the expected band overlaid; shade violations.

    Returns ``(ax, violating_mask)`` — the mask is a boolean Series over the frame's index (points
    outside the expected band ± ``tolerance``), ready to feed pattern J / `mask_to_spans`.
    """
    import matplotlib.pyplot as plt

    df = pd.DataFrame({"x": _col(frame, template.x), "y": _col(frame, template.y)}).dropna()
    xv, yv = df["x"].to_numpy(float), df["y"].to_numpy(float)
    lo, hi = template.expected(xv)
    lo = np.asarray(lo, float) - tolerance
    hi = np.asarray(hi, float) + tolerance
    violating = (yv < lo) | (yv > hi)
    mask = pd.Series(violating, index=df.index)

    if ax is None:
        _, ax = plt.subplots(figsize=(7, 5))
    if len(xv):
        order = np.argsort(xv)
        ax.fill_between(xv[order], lo[order], hi[order], color="#8fd19e", alpha=0.25,
                        label="expected", zorder=1)
    ax.scatter(xv[~violating], yv[~violating], s=16, color="#3366cc", alpha=0.6,
               label="in-region", zorder=2)
    if shade and violating.any():
        ax.scatter(xv[violating], yv[violating], s=22, color="#d62728", alpha=0.85,
                   label="violating", zorder=3)

    ax.set_xlabel(template.xlabel or getattr(template.x, "name", str(template.x)))
    ax.set_ylabel(template.ylabel or getattr(template.y, "name", str(template.y)))
    frac = float(violating.mean()) if len(violating) else 0.0
    title = f"{template.name} — {frac:.0%} out of band"
    ax.set_title(title + (f"  · {template.cite}" if template.cite else ""))
    ax.legend(loc="best", fontsize=8)
    return ax, mask


# --------------------------------------------------------------------------- template constructors

def band(x, y, *, low: float, high: float, name: str = "band", cite: str = "",
         xlabel: str = "", ylabel: str = "") -> DiagnosticTemplate:
    """A constant acceptable band ``[low, high]`` regardless of x (e.g. cooling-tower approach)."""
    def expected(xv):
        return np.full(len(xv), float(low)), np.full(len(xv), float(high))
    return DiagnosticTemplate(name, x, y, expected, xlabel, ylabel, cite)


def reset_line(x, y, *, p1, p2, tol: float, name: str = "reset", cite: str = "",
               xlabel: str = "", ylabel: str = "") -> DiagnosticTemplate:
    """A linear reset schedule through ``p1``/``p2`` (``(x, y)`` each) with a ±``tol`` band.

    Outside ``[x1, x2]`` the schedule **clamps** at its endpoints (a real reset holds its min/max),
    rather than extrapolating the slope."""
    (x1, y1), (x2, y2) = p1, p2
    slope = (y2 - y1) / (x2 - x1) if x2 != x1 else 0.0
    xlo, xhi = (x1, x2) if x1 <= x2 else (x2, x1)

    def expected(xv):
        yhat = y1 + slope * (np.clip(xv, xlo, xhi) - x1)
        return yhat - tol, yhat + tol
    return DiagnosticTemplate(name, x, y, expected, xlabel, ylabel, cite)


def economizer_template(*, high_limit_f: float = 65.0, min_damper: float = 0.2,
                        open_min: float = 0.5) -> DiagnosticTemplate:
    """OA damper vs OAT: below the high limit expect the damper open for free cooling
    (``[open_min, 1]``); at/above it expect minimum OA (``[0, min_damper]``)."""
    def expected(xv):
        lo = np.where(xv < high_limit_f, open_min, 0.0)
        hi = np.where(xv < high_limit_f, 1.0, min_damper)
        return lo, hi
    return DiagnosticTemplate("economizer", Role.OAT, Role.OA_DAMPER, expected,
                              "OAT (°F)", "OA damper (0–1)", "ASHRAE G36 economizer")


def no_simultaneous_template(*, active: float = 0.05, y_max: float = 1.0) -> DiagnosticTemplate:
    """Heating valve vs cooling valve: when cooling is active (x > ``active``) the heating valve
    must be near zero (``[0, active]``); otherwise it may range up to ``y_max``."""
    def expected(xv):
        lo = np.zeros(len(xv))
        hi = np.where(xv > active, active, y_max)
        return lo, hi
    return DiagnosticTemplate("no_simultaneous_hc", Role.COOL_VALVE, Role.HEAT_VALVE, expected,
                              "cooling valve (0–1)", "heating valve (0–1)",
                              "ASHRAE G36 — no simultaneous heat/cool")


#: Ready-made templates for common subsystems (build your own with the constructors above).
TEMPLATES: dict[str, DiagnosticTemplate] = {
    "sat_reset": reset_line(Role.OAT, Role.SUPPLY_AIR_TEMP, p1=(0.0, 65.0), p2=(60.0, 55.0),
                            tol=2.0, name="sat_reset", cite="ASHRAE G36 SAT reset",
                            xlabel="OAT (°F)", ylabel="SAT (°F)"),
    "chw_reset": reset_line(Role.OAT, Role.CHW_SUPPLY_TEMP, p1=(50.0, 48.0), p2=(95.0, 42.0),
                            tol=2.0, name="chw_reset", cite="ASHRAE G36 CHW reset",
                            xlabel="OAT (°F)", ylabel="CHW supply (°F)"),
    "economizer": economizer_template(),
    "no_simultaneous_hc": no_simultaneous_template(),
}
