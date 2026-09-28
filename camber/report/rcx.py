"""RCx-style report layout (provisional): one printable HTML document per config-driven run.

The retro-commissioning deliverable a commissioning engineer hands over: a cover with the data's
provenance, a one-page executive summary of ranked *issues* (not a wall of findings), the data
coverage and sensor health the verdicts rest on, a representative week, economizer / SAT-reset /
air-distribution pages, M&V / drift, one page per issue with its evidence, and appendices listing
everything that was **declined** and every assumption that was **used**.

It is a layout over the existing analytics -- :func:`camber.rules.triage.link_findings` builds the
issues (sensor precedence, union hours, max-not-sum cost, confidence), the rule library supplies
the evidence -- and adds no new detector or cost estimator. Not to be confused with
:mod:`camber.rcx`, the unrelated functional-test / before-after MBCx primitives.

The builder (:func:`build_rcx_report`) does all the analysis and renders every chart into an
:class:`RcxReport`; the HTML renderer (:meth:`RcxReport.to_html`) reads only that object -- no JS,
charts inlined as base64 PNG (or SVG for line charts), print CSS for browser print-to-PDF. See
docs/RCX-REPORT.md for the layout, the week-selection algorithm, the dedup / cost rules, the
confidence grade and the engineer-notes schema.
"""

from __future__ import annotations

import base64
import hashlib
import html as _html
import io
import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from ..model.roles import Role
from ..schedules import FAN_GATE_NONE, effective_occupied_mask, fan_on_mask

if TYPE_CHECKING:  # pragma: no cover
    from ..fault_economics import EnergyPrice

__all__ = [
    "RcxOptions",
    "RcxReport",
    "WeekChoice",
    "build_rcx_report",
    "select_week",
    "load_notes",
    "notes_template",
    "P3_FAMILIES",
    "PLANT_FAMILIES",
    "WEEK_MODES",
]

#: Representative-week panels: one unit family each, never normalized onto a shared axis.
P3_FAMILIES = (
    (
        "°F",
        "Temperatures",
        (
            Role.OAT,
            Role.SUPPLY_AIR_TEMP,
            Role.SUPPLY_AIR_TEMP_SP,
            Role.MIXED_AIR_TEMP,
            Role.RETURN_AIR_TEMP,
        ),
    ),
    (
        "%",
        "Positions and speeds",
        (Role.OA_DAMPER, Role.COOL_VALVE, Role.HEAT_VALVE, Role.SUPPLY_FAN_SPEED),
    ),
    ("in.w.c.", "Static pressure", (Role.DUCT_STATIC, Role.DUCT_STATIC_SP)),
)
_P3_ROLES = tuple(r for _u, _t, roles in P3_FAMILIES for r in roles)
# roles that make an equipment "air side" for the report (OAT alone does not)
_AIR_ROLES = frozenset(_P3_ROLES) - {Role.OAT}

#: Representative-week panels for a water-side plant (chilled water, condenser water, hot water):
#: an equipment with none of the air-side roles but some of these is charted and scored with these.
#: Loop differential pressure is shown in the site's own units (psi, ftH2O or kPa as trended).
PLANT_FAMILIES = (
    (
        "°F",
        "Water temperatures",
        (
            Role.OAT,
            Role.CHW_SUPPLY_TEMP,
            Role.CHW_SUPPLY_TEMP_SP,
            Role.CHW_RETURN_TEMP,
            Role.CW_SUPPLY_TEMP,
            Role.CW_RETURN_TEMP,
            Role.HW_SUPPLY_TEMP,
            Role.HW_RETURN_TEMP,
        ),
    ),
    (
        "DP (as trended)",
        "Loop differential pressure",
        (Role.CHW_DIFF_PRESS, Role.CHW_DIFF_PRESS_SP, Role.HW_DIFF_PRESS, Role.HW_DIFF_PRESS_SP),
    ),
    ("kW", "Electric power", (Role.POWER,)),
    ("0-1", "Status", (Role.PUMP_STATUS, Role.BOILER_STATUS, Role.COMPRESSOR_STATUS)),
)
_PLANT_ROLES = tuple(r for _u, _t, roles in PLANT_FAMILIES for r in roles)
# roles that make a non-air equipment a "plant" for the week view (OAT and power alone do not: an
# air handler or a meter can carry those)
_PLANT_MARKERS = frozenset(_PLANT_ROLES) - {Role.OAT, Role.POWER}

WEEK_MODES = ("evidence", "oat_range", "typical", "fixed:YYYY-MM-DD")

_SEV_CLASS = {"fault": "sev-fault", "warn": "sev-warn", "info": "sev-info", "ok": "sev-ok"}


# ============================================================================ options


@dataclass
class RcxOptions:
    """What to put in the RCx report and how to print it (all optional).

    ``week`` is a :data:`WEEK_MODES` value (``"auto"`` means ``"evidence"``; ``"oat-range"`` is
    accepted for ``"oat_range"``; a bare ``YYYY-MM-DD`` means ``fixed:``). ``chart_format="svg"``
    renders line charts as SVG (dense scatters stay PNG). ``sections`` lists the section ids to
    include (default: all). ``price`` / ``loads`` feed the existing cost estimators
    (:class:`~camber.fault_economics.EnergyPrice`, ``{equip: EquipmentLoad}``). ``occupancy``
    overrides the assumed schedule (``{"start_hour", "end_hour", "days"}``) where no occupancy
    point is trended. ``oat_reference`` compares the BAS OAT to a reference: ``{"csv": path}``
    (offline, default) or ``{"fetch": "nasa_power", "latitude", "longitude", "tz"}`` (opt-in
    network). ``sequence`` declares the site's SAT reset (``{"sat_reset": {"oat": [lo, hi], "sat":
    [at_lo, at_hi], "tol_f": 2}}``). ``g36_reference`` draws the G36 map on the no-sequence tier,
    labelled a reference, never a verdict. ``lifecycle`` pulls notes from the fault store.
    """

    top_n: int = 8
    week: str = "evidence"
    paper: str = "letter"
    chart_format: str = "png"
    dpi: int = 150
    sections: tuple | None = None
    price: EnergyPrice | None = None
    loads: dict | None = None
    cost_params: dict | None = None
    occupancy: dict | None = None
    oat_reference: dict | None = None
    sequence: dict | None = None
    g36_reference: bool = False
    corroboration: bool = True
    lifecycle: bool = False
    title: str = ""

    @classmethod
    def from_config(cls, report: dict | None, *, base_dir: str = ".") -> RcxOptions:
        """Options from a config's ``report`` section (``report.rcx`` plus ``report.loads`` /
        ``report.price`` at the top level for compatibility with the audit layout)."""
        from ..fault_economics import EnergyPrice, EquipmentLoad

        report = dict(report or {})
        spec = dict(report.get("rcx") or {})
        opts = cls()
        for key in ("top_n", "week", "paper", "chart_format", "dpi", "title"):
            if key in spec:
                setattr(opts, key, spec[key])
        if spec.get("sections"):
            opts.sections = tuple(spec["sections"])
        for key in ("occupancy", "sequence", "cost_params"):
            if spec.get(key):
                setattr(opts, key, dict(spec[key]))
        for key in ("g36_reference", "corroboration", "lifecycle"):
            if key in spec:
                setattr(opts, key, bool(spec[key]))
        ref = spec.get("oat_reference")
        if ref:
            ref = dict(ref)
            if ref.get("csv") and not os.path.isabs(ref["csv"]):
                ref["csv"] = os.path.join(base_dir, ref["csv"])
            opts.oat_reference = ref
        price = spec.get("price") or report.get("price")
        if price:
            known = {"electricity_per_kwh", "gas_per_therm"}
            opts.price = EnergyPrice(**{k: v for k, v in price.items() if k in known})
        loads = spec.get("loads") or report.get("loads")
        if loads:
            opts.loads = {e: EquipmentLoad(**dict(v)) for e, v in loads.items()}
        return opts

    def week_mode(self) -> str:
        """The normalized week mode (``auto`` -> ``evidence``; a bare date -> ``fixed:``)."""
        w = str(self.week or "evidence").strip()
        if w in ("auto", ""):
            return "evidence"
        if w == "oat-range":
            return "oat_range"
        if len(w) == 10 and w[4] == "-" and w[7] == "-":
            return f"fixed:{w}"
        return w


# ============================================================================ week selection


@dataclass
class WeekChoice:
    """The representative week :func:`select_week` chose -- or why it declined to choose one."""

    start: pd.Timestamp | None
    end: pd.Timestamp | None
    mode: str
    score: float | None = None
    components: dict = field(default_factory=dict)
    runner_up: dict | None = None
    explanation: str = ""
    declined: bool = False
    reason: str = ""
    candidates: list = field(default_factory=list)  # every window scored, oldest first

    def as_dict(self) -> dict:
        def _ts(t):
            return None if t is None else str(t)

        return {
            "start": _ts(self.start),
            "end": _ts(self.end),
            "mode": self.mode,
            "score": self.score,
            "components": dict(self.components),
            "runner_up": dict(self.runner_up) if self.runner_up else None,
            "explanation": self.explanation,
            "declined": self.declined,
            "reason": self.reason,
            "candidates": [dict(c, start=_ts(c.get("start"))) for c in self.candidates],
        }


def _col(frame, role):
    if frame is None:
        return None
    for key in (role, getattr(role, "value", role)):
        if key in frame.columns:
            return frame[key]
    return None


def _on(mask, index=None) -> pd.Series:
    """A clean boolean mask (NaN -> False), optionally on ``index`` (missing -> False)."""
    m = pd.Series(mask)
    m = m.where(m.notna(), False).astype(bool)
    return m if index is None else m.reindex(index, fill_value=False)


def _oat_of(frames: dict):
    """One OAT series for the site: the first equipment frame that carries one."""
    for _e, fr in sorted(frames.items()):
        s = _col(fr, Role.OAT)
        if s is not None and s.notna().any():
            return s
    return None


def _grid_step(frames: dict) -> pd.Timedelta:
    from ..timegrid import interval_hours

    for _e, fr in sorted(frames.items()):
        if fr is not None and len(fr.index) > 1:
            return pd.Timedelta(hours=float(interval_hours(fr.index)))
    return pd.Timedelta(hours=1)


def select_week(
    frames: dict,
    *,
    issues=(),
    mode: str = "evidence",
    roles=_P3_ROLES,
    gate_for=None,
    occupied_for=None,
    high_limit_f: float = 65.0,
    min_coverage: float = 0.8,
    min_occupied_days: int = 3,
    roles_for=None,
) -> WeekChoice:
    """Pick the representative week for the report, deterministically, and say why in numbers.

    Candidates are the Monday-00:00 (local, i.e. the frames' own clock) 7-day windows. A window is
    **eligible** when the ``roles`` present cover >= ``min_coverage`` of its *gated* samples (fan-on
    via ``gate_for(equip) -> mask | None``, default :func:`camber.schedules.fan_on_mask`; ungated
    where a unit trends no fan signal) and it has >= ``min_occupied_days`` occupied days
    (``occupied_for(equip) -> mask``; default the trended occupancy, else the assumed schedule).
    Otherwise -- or when nothing is eligible -- the result is ``declined`` with the reason.
    ``roles_for(equip) -> roles`` (provisional) overrides ``roles`` per equipment, so air handlers
    and water-side plants (:data:`PLANT_FAMILIES`) can share one choice of week.

    Scores (every mode then adds ``0.1 x coverage``; ties go to the earliest week):

    * ``evidence`` -- sum over ``issues`` (ranked :class:`camber.rules.triage.Issue`) of
      ``(1 / rank) x (gated violation hours in the window / the issue's total)``, half weight for a
      conditional issue; issues without a violation mask contribute nothing.
    * ``oat_range`` -- the share of the period's OAT deciles present in the window, +1 when the
      window crosses ``high_limit_f`` (economizer high limit).
    * ``typical`` -- minus the RMS distance of the window's daily-mean OAT from the period's median
      daily mean (the most ordinary week scores highest).
    * ``fixed:YYYY-MM-DD`` -- the window containing that date.
    """
    mode = str(mode or "evidence")
    fixed = None
    if mode.startswith("fixed:"):
        fixed = pd.Timestamp(mode.split(":", 1)[1])
    elif mode not in ("evidence", "oat_range", "typical"):
        raise ValueError(f"unknown week mode {mode!r}; use one of {', '.join(WEEK_MODES)}")
    frames = {e: f for e, f in frames.items() if f is not None and not f.empty}
    if not frames:
        return WeekChoice(
            None, None, mode, declined=True, reason="no equipment data to choose from"
        )

    step = _grid_step(frames)
    lo = min(f.index.min() for f in frames.values())
    hi = max(f.index.max() for f in frames.values())
    first = (lo - pd.Timedelta(days=int(lo.dayofweek))).normalize()
    starts = []
    s = first
    while s <= hi:
        starts.append(s)
        s = s + pd.Timedelta(days=7)

    gates, occs = {}, {}
    for e, fr in frames.items():
        g = gate_for(e) if gate_for is not None else fan_on_mask(fr)[0]
        gates[e] = g
        if occupied_for is not None:
            occs[e] = occupied_for(e)
        else:
            occs[e] = effective_occupied_mask(fr.index, occ=_col(fr, Role.OCCUPANCY))

    oat = _oat_of(frames)
    edges = None
    daily_median = None
    if oat is not None and oat.notna().sum() >= 10:
        edges = np.unique(np.nanquantile(oat.dropna().to_numpy(float), np.linspace(0, 1, 11)))
        daily_median = float(oat.resample("1D").mean().median())

    cands = []
    span = pd.Timedelta(days=7)
    stats = _week_stats(frames, starts, step, roles, gates, occs, roles_for)
    ev = _evidence_counts(issues, starts, span) if mode == "evidence" else None
    oat_pos = _window_positions(oat, starts, span) if oat is not None else None
    for k, st in enumerate(starts):
        en = st + span
        n_ok, n_all, n_occ_days = stats[k]
        coverage = (n_ok / n_all) if n_all else 0.0
        c = {
            "start": st,
            "coverage": round(coverage, 4),
            "occupied_days": n_occ_days,
        }
        c["eligible"] = coverage >= min_coverage and n_occ_days >= min_occupied_days
        comp = {}
        if mode == "evidence":
            total = 0.0
            for iss, tot, inws in ev or ():
                inw = float(inws[k])
                weight = 0.5 if getattr(iss, "conditional", False) else 1.0
                total += weight * (1.0 / max(int(getattr(iss, "rank", 1) or 1), 1)) * (inw / tot)
            comp["evidence"] = round(total, 4)
        elif mode == "oat_range":
            val = 0.0
            if oat is not None and edges is not None and len(edges) > 1:
                wo = _window(oat, oat_pos, k, st, en).dropna().to_numpy(float)
                if len(wo):
                    bins = np.clip(np.searchsorted(edges, wo, side="right") - 1, 0, len(edges) - 2)
                    comp["deciles_present"] = round(len(np.unique(bins)) / (len(edges) - 1), 4)
                    comp["crosses_high_limit"] = 1.0 if wo.min() <= high_limit_f < wo.max() else 0.0
                    val = comp["deciles_present"] + comp["crosses_high_limit"]
            comp.setdefault("deciles_present", 0.0)
            comp.setdefault("crosses_high_limit", 0.0)
            comp["oat_range"] = round(val, 4)
        elif mode == "typical":
            val = float("-inf")
            if oat is not None and daily_median is not None:
                d = _window(oat, oat_pos, k, st, en).resample("1D").mean().dropna()
                if len(d):
                    rms = float(np.sqrt(np.mean((d.to_numpy(float) - daily_median) ** 2)))
                    comp["rms_from_median_f"] = round(rms, 4)
                    val = -rms
            comp["typical"] = round(val, 4) if np.isfinite(val) else val
        c["components"] = comp
        main = next(
            iter(v for k, v in comp.items() if k in ("evidence", "oat_range", "typical")), 0.0
        )
        c["score"] = round(main + 0.1 * coverage, 4) if fixed is None else round(0.1 * coverage, 4)
        cands.append(c)

    n_elig = sum(1 for c in cands if c["eligible"])
    if fixed is not None:
        want = (fixed - pd.Timedelta(days=int(fixed.dayofweek))).normalize()
        match = [c for c in cands if c["start"] == want]
        if not match:
            return WeekChoice(
                None,
                None,
                mode,
                declined=True,
                reason=f"the fixed week of {want:%Y-%m-%d} is outside the data "
                f"({lo:%Y-%m-%d} to {hi:%Y-%m-%d})",
                candidates=cands,
            )
        c = match[0]
        if not c["eligible"]:
            return WeekChoice(
                c["start"],
                c["start"] + pd.Timedelta(days=7),
                mode,
                score=c["score"],
                components={**c["components"], "coverage": c["coverage"]},
                declined=True,
                reason=_ineligible(c, min_coverage, min_occupied_days),
                candidates=cands,
            )
        expl = (
            f"Fixed week of {c['start']:%Y-%m-%d} as requested: coverage {c['coverage']:.2f} "
            f"of gated samples, {c['occupied_days']} occupied days."
        )
        return WeekChoice(
            c["start"],
            c["start"] + pd.Timedelta(days=7),
            mode,
            score=c["score"],
            components={"coverage": c["coverage"], "occupied_days": c["occupied_days"]},
            explanation=expl,
            candidates=cands,
        )

    elig = sorted((c for c in cands if c["eligible"]), key=lambda c: (-c["score"], c["start"]))
    if not elig:
        best = max(cands, key=lambda c: (c["coverage"], c["occupied_days"])) if cands else None
        reason = (
            f"no 7-day window has >= {min_coverage:.0%} coverage of the charted roles on gated "
            f"samples and >= {min_occupied_days} occupied days"
        )
        if best is not None:
            reason += (
                f" (best: week of {best['start']:%Y-%m-%d} at {best['coverage']:.0%} coverage, "
                f"{best['occupied_days']} occupied days)"
            )
        return WeekChoice(None, None, mode, declined=True, reason=reason, candidates=cands)
    win = elig[0]
    ru = elig[1] if len(elig) > 1 else None
    comp_name = mode
    comp_val = win["components"].get(mode, 0.0)
    expl = (
        f"Chose the week of {win['start']:%Y-%m-%d} by {mode.replace('_', '-')}: score "
        f"{win['score']:.3f} = {comp_name} {comp_val:.3f} + 0.1 x coverage {win['coverage']:.2f}; "
        f"{win['occupied_days']} occupied days"
    )
    if ru is not None:
        expl += f"; runner-up week of {ru['start']:%Y-%m-%d} scored {ru['score']:.3f}"
        if ru["score"] == win["score"]:
            expl += " (tie -> earliest)"
    expl += f"; {n_elig} of {len(cands)} candidate weeks eligible."
    return WeekChoice(
        win["start"],
        win["start"] + pd.Timedelta(days=7),
        mode,
        score=win["score"],
        components={**win["components"], "coverage": win["coverage"]},
        runner_up=({"start": str(ru["start"]), "score": ru["score"]} if ru is not None else None),
        explanation=expl,
        candidates=cands,
    )


# ---- week scoring internals (#35). Every window statistic is computed once per series from the
# timestamps it actually holds -- never by reindexing each series onto each candidate window's grid,
# which cost O(weeks x grid) and ran for minutes on long or irregular trends. Each helper returns
# exactly what the per-window reindexing did; exotic inputs (duplicate or non-datetime indexes,
# naive/aware clashes) fall back to that per-window path so errors and edge cases stay the same.


def _ns(index) -> np.ndarray:
    """int64 UTC nanoseconds of a DatetimeIndex (NaT -> int64 min, below every window)."""
    return pd.DatetimeIndex(index).as_unit("ns").asi8


def _day_key(index) -> np.ndarray:
    """An integer per calendar date on the index's own (local) clock."""
    idx = pd.DatetimeIndex(index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    return idx.as_unit("ns").asi8 // 86_400_000_000_000


def _same_clock(index, ref) -> bool:
    """A unique DatetimeIndex whose naive/aware-ness matches ``ref`` (a Timestamp)."""
    return (
        isinstance(index, pd.DatetimeIndex)
        and index.is_unique
        and ((index.tz is None) == (getattr(ref, "tz", None) is None))
    )


def _week_of(ns: np.ndarray, starts_ns: np.ndarray, span_ns: int) -> np.ndarray:
    """Window number of each timestamp (``-1`` outside every window)."""
    k = np.searchsorted(starts_ns, ns, side="right") - 1
    k[ns >= starts_ns[-1] + span_ns] = -1  # the windows are contiguous: only the ends matter
    return k


def _week_stats_loop(frames, starts, step, roles, gates, occs, roles_for=None) -> list:
    """The reference per-window computation (reindex every series onto each window's grid)."""
    out = []
    for st in starts:
        en = st + pd.Timedelta(days=7)
        grid = pd.date_range(st, en, freq=step, inclusive="left")
        n_ok = n_all = 0
        occ_days: set = set()
        for e, fr in frames.items():
            w = fr.reindex(grid)
            g = gates[e]
            gm = pd.Series(True, index=grid) if g is None else _on(g, grid)
            want = roles if roles_for is None else roles_for(e)
            present = [r for r in want if _col(fr, r) is not None]
            if not present:
                continue
            vals = pd.DataFrame({str(r): _col(w, r) for r in present}, index=grid)
            gv = vals[gm.to_numpy()]
            n_all += gv.size
            n_ok += int(gv.notna().to_numpy().sum())
            om = _on(occs[e], grid)
            has = vals.notna().any(axis=1) & gm & om
            occ_days |= {d.date() for d in grid[has.to_numpy()]}
        out.append((n_ok, n_all, len(occ_days)))
    return out


def _week_stats(frames, starts, step, roles, gates, occs, roles_for=None) -> list:
    """``[(n_ok, n_all, occupied_days)]`` per window: charted-role samples present / expected on
    the window's gated grid, and the distinct dates with an occupied, gated, non-empty sample."""
    ref = starts[0]
    stride = int(pd.Timedelta(step).value)
    fast = stride > 0
    for e, fr in frames.items():
        masks = [m for m in (gates[e], occs[e]) if m is not None]
        if not _same_clock(fr.index, ref) or fr.columns.has_duplicates:
            fast = False
        for m in masks:
            if not isinstance(m, pd.Series) or not _same_clock(m.index, ref):
                fast = False
    if not fast:
        return _week_stats_loop(frames, starts, step, roles, gates, occs, roles_for)

    n = len(starts)
    starts_ns = _ns(pd.DatetimeIndex(list(starts)))
    span_ns = int(pd.Timedelta(days=7).value)
    per_window = -(-span_ns // stride)  # grid points in one window: ceil(7 days / step)
    n_ok = np.zeros(n, dtype=np.int64)
    n_all = np.zeros(n, dtype=np.int64)
    day_keys, day_weeks = [], []

    def on_grid(index):
        ns = _ns(index)
        k = _week_of(ns, starts_ns, span_ns)
        hit = k >= 0
        hit[hit] = (ns[hit] - starts_ns[k[hit]]) % stride == 0
        return hit, k

    for e, fr in frames.items():
        want = roles if roles_for is None else roles_for(e)
        present = [r for r in want if _col(fr, r) is not None]
        if not present:
            continue
        cols = {str(r): _col(fr, r) for r in present}
        g = gates[e]
        # expected samples: the gated grid points of each window, times the charted columns
        gated: np.ndarray
        if g is None:
            gated = np.full(n, per_window, dtype=np.int64)
        else:
            gc = _on(g)
            hit, k = on_grid(gc.index)
            hit &= gc.to_numpy()
            gated = np.bincount(k[hit], minlength=n)
        n_all += gated * len(cols)
        # present samples: the frame's own on-grid rows (a grid point it lacks is NaN)
        hit, k = on_grid(fr.index)
        if not hit.any():
            continue
        rows = fr.index[hit]
        vals = pd.DataFrame(
            {c: pd.Series(s.to_numpy()[hit], index=rows) for c, s in cols.items()}, index=rows
        )
        gm = np.ones(len(rows), bool) if g is None else _on(g, rows).to_numpy()
        nn = vals.notna().to_numpy()
        n_ok += np.bincount(k[hit][gm], weights=nn[gm].sum(axis=1), minlength=n).astype(np.int64)
        om = _on(occs[e], rows).to_numpy()
        has = nn.any(axis=1) & gm & om
        day_keys.append(_day_key(rows[has]))
        day_weeks.append(k[hit][has])
    occ: np.ndarray = np.zeros(n, dtype=np.int64)
    if day_keys:
        pairs = np.unique(np.stack([np.concatenate(day_weeks), np.concatenate(day_keys)]), axis=1)
        occ = np.bincount(pairs[0], minlength=n)
    return [(int(n_ok[i]), int(n_all[i]), int(occ[i])) for i in range(n)]


def _evidence_counts(issues, starts, span) -> list:
    """``[(issue, total violation hours, per-window hours)]`` for the issues that have any."""
    out = []
    starts_ns = _ns(pd.DatetimeIndex(list(starts)))
    for iss in issues or ():
        m = getattr(iss, "mask", None)
        if m is None:
            continue
        m = _on(m)
        tot = float(m.sum())
        if tot <= 0:
            continue
        idx = m.index
        inws: np.ndarray | list
        if isinstance(idx, pd.DatetimeIndex) and (idx.tz is None) == (starts[0].tz is None):
            t = np.sort(_ns(idx[m.to_numpy()]))
            lo = np.searchsorted(t, starts_ns, side="left")
            hi = np.searchsorted(t, starts_ns + int(span.value), side="left")
            inws = hi - lo
        else:  # the per-window comparison (raises on a naive/aware clash, as it always did)
            inws = [int(m[(idx >= st) & (idx < st + span)].sum()) for st in starts]
        out.append((iss, tot, inws))
    return out


def _window_positions(s, starts, span):
    """Row positions of ``s`` in each window (original order), or ``None`` to slice per window."""
    idx = s.index
    if not isinstance(idx, pd.DatetimeIndex) or (idx.tz is None) != (starts[0].tz is None):
        return None
    k = _week_of(_ns(idx), _ns(pd.DatetimeIndex(list(starts))), int(span.value))
    order = np.argsort(k, kind="stable")
    bounds = np.searchsorted(k[order], np.arange(len(starts) + 1), side="left")
    return order, bounds


def _window(s, pos, k, st, en):
    if pos is None:
        return s[(s.index >= st) & (s.index < en)]
    order, bounds = pos
    return s.iloc[order[bounds[k] : bounds[k + 1]]]


def _ineligible(c, min_coverage, min_days) -> str:
    bits = []
    if c["coverage"] < min_coverage:
        bits.append(f"coverage {c['coverage']:.0%} < {min_coverage:.0%} of gated samples")
    if c["occupied_days"] < min_days:
        bits.append(f"{c['occupied_days']} occupied days < {min_days}")
    return f"week of {c['start']:%Y-%m-%d} is not usable: " + "; ".join(bits)


# ============================================================================ notes


def load_notes(path: str | None) -> dict:
    """Load an engineer-notes JSON file (``{slot: note}``); ``{}`` for ``None``.

    Slots are ``exec_summary``, ``section:<id>`` and ``issue:<fingerprint>``. A note is a string,
    ``{"text", "author", "date"}``, or a list of either.
    """
    if not path:
        return {}
    with open(path) as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("an engineer-notes file must be a JSON object keyed by slot id")
    return data


def _note_list(value) -> list:
    """Normalize a note value to ``[{"text", "author", "date"}]`` (empty text dropped)."""
    items = value if isinstance(value, list) else [value]
    out = []
    for it in items:
        if isinstance(it, str):
            it = {"text": it}
        if not isinstance(it, dict):
            continue
        text = str(it.get("text") or "").strip()
        if text:
            out.append(
                {
                    "text": text,
                    "author": str(it.get("author") or ""),
                    "date": str(it.get("date") or ""),
                }
            )
    return out


def notes_template(report) -> dict:
    """An empty notes file covering every slot of ``report`` (what ``--notes-template`` writes)."""
    return {slot: {"text": "", "author": "", "date": ""} for slot in report.slots()}


# ============================================================================ report object


@dataclass
class RcxReport:
    """The fully-built RCx report. :meth:`to_html` renders only from this object.

    ``sections`` is an ordered list of ``{"id", "title", "slot", "blocks", "kind"}`` dicts; a block
    is ``{"kind": "p" | "banner" | "kpis" | "table" | "figure" | "list" | "html", ...}`` (``html``
    only for trusted, internally generated fragments -- the data-source block, the drift report,
    evidence figures). ``notes`` maps a slot id to its normalized engineer notes and ``orphans``
    lists notes whose slot does not exist in this report (appendix E).
    """

    title: str
    site: str
    facility_id: str | None
    period: tuple
    options: RcxOptions
    data_sources: list
    kpis: dict
    issues: list
    week: WeekChoice | None
    sections: list
    notes: dict = field(default_factory=dict)
    orphans: dict = field(default_factory=dict)
    version: str = ""

    def slots(self) -> list:
        """Every engineer-note slot id in this report, in page order."""
        out = ["exec_summary"]
        for s in self.sections:
            if s.get("slot") and s["slot"] not in out:
                out.append(s["slot"])
        return out

    def to_dict(self) -> dict:
        """A JSON-friendly summary: KPIs, ranked issues, the week choice, sections and notes."""

        def _block(b):
            d = {k: v for k, v in b.items() if k not in ("src", "html")}
            if "src" in b:
                d["bytes"] = len(b["src"])
            if "html" in b:
                d["html_bytes"] = len(b["html"])
            return d

        return {
            "title": self.title,
            "site": self.site,
            "facility_id": self.facility_id,
            "period": [str(p) for p in self.period],
            "kpis": dict(self.kpis),
            "issues": [_issue_dict(i) for i in self.issues],
            "week": self.week.as_dict() if self.week is not None else None,
            "sections": [
                {
                    "id": s["id"],
                    "title": s["title"],
                    "slot": s.get("slot"),
                    "blocks": [_block(b) for b in s["blocks"]],
                }
                for s in self.sections
            ],
            "notes": {k: list(v) for k, v in self.notes.items()},
            "orphans": {k: list(v) for k, v in self.orphans.items()},
            "slots": self.slots(),
        }

    def to_html(self) -> str:
        """The whole report as one self-contained, printable HTML document (no JS)."""
        return _render_html(self)


def _issue_dict(i) -> dict:
    return {
        "key": i.key,
        "rank": i.rank,
        "equip": i.equip,
        "severity": i.severity,
        "rules": i.rules,
        "chain": i.chain,
        "cost": i.cost,
        "cost_basis_note": i.cost_basis_note,
        "conditional_on": [c.label() for c in i.conditional_on],
        "dependents": [
            f"{getattr(f, 'rule', '')} @ {getattr(f, 'equip', '')}" for f in i.dependents
        ],
        "hours_union": i.hours_union,
        "fan_on_hours": i.fan_on_hours,
        "fan_gate": i.fan_gate,
        "pct_runtime": i.pct_runtime,
        "confidence": i.confidence,
        "confidence_components": dict(i.confidence_components),
        "why": list(i.why),
    }


# ============================================================================ chart rendering


def _render(fig, fmt: str = "png", dpi: int = 150) -> str:
    """Render a figure to a data URI and close it (tests patch this to inspect the Axes)."""
    import matplotlib.pyplot as plt

    if fmt == "svg":
        buf = io.BytesIO()
        fig.savefig(buf, format="svg", bbox_inches="tight")
        plt.close(fig)
        return "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    from .dashboard import fig_to_base64

    return fig_to_base64(fig, dpi=dpi)


def _figure(fig, *, alt: str, caption: str = "", fmt: str = "png", dpi: int = 150) -> dict:
    return {"kind": "figure", "src": _render(fig, fmt, dpi), "alt": alt, "caption": caption}


def _plt():
    """pyplot, on the non-interactive Agg backend unless the caller already chose one."""
    import sys

    import matplotlib

    if "matplotlib.pyplot" not in sys.modules:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


# ============================================================================ builder helpers


def _p(text: str) -> dict:
    return {"kind": "p", "text": str(text)}


def _table(header, rows, *, css: str = "") -> dict:
    return {
        "kind": "table",
        "header": [str(h) for h in header],
        "rows": [[("" if c is None else str(c)) for c in r] for r in rows],
        "css": css,
    }


def _fmt_usd(v) -> str:
    return "—" if v is None else f"${v:,.0f}"


def _humanize(rule: str) -> str:
    r = str(rule or "").replace("sensor_drift:", "sensor drift: ").replace("_", " ").strip()
    return r[:1].upper() + r[1:]


def _cut(text: str, n: int) -> str:
    t = " ".join(str(text or "").split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


@lru_cache(maxsize=1)
def _synthetic_per_rule() -> dict:
    """Per-rule TPR/FPR from the live synthetic benchmark (the dossier's first track)."""
    from .. import faultlab
    from ..eval import benchmark

    rep = benchmark(faultlab.labeled_records(), faultlab.targets())
    return {
        name: {
            "tpr": round(float(c.true_positive_rate), 3),
            "fpr": round(float(c.false_positive_rate), 3),
            "track": "the synthetic injected-fault benchmark",
        }
        for name, c in rep.per_detector.items()
    }


def _load_reference_oat(spec: dict, index) -> pd.Series | None:
    """A reference OAT series from ``spec`` (offline CSV, or an opt-in NASA POWER fetch)."""
    if spec.get("csv"):
        df = pd.read_csv(spec["csv"])
        tcol = spec.get("time_col") or df.columns[0]
        vcol = spec.get("value_col") or next(c for c in df.columns if c != tcol)
        s = pd.Series(
            pd.to_numeric(df[vcol], errors="coerce").to_numpy(),
            index=pd.to_datetime(df[tcol]),
            name="oat_ref",
        )
        return s.sort_index()
    if spec.get("fetch") == "nasa_power":
        from ..weather_source import oat_reference

        return oat_reference(
            spec["latitude"],
            spec["longitude"],
            index.min(),
            index.max(),
            tz=spec.get("tz", "UTC"),
        )
    return None


def _rule_param_overrides(config: dict | None) -> dict:
    """``{rule name: params}`` for the config's parameterized rule entries."""
    out = {}
    for entry in (config or {}).get("rules", []) or []:
        if isinstance(entry, dict) and entry.get("params"):
            out[entry["name"]] = dict(entry["params"])
    return out


class _Ctx:
    """Everything the section builders share (frames, gates, trust, issues ...)."""

    def __init__(self, run, options: RcxOptions):
        self.run = run
        self.o = options
        self.fmt = "svg" if str(options.chart_format).lower() == "svg" else "png"
        self.dpi = int(options.dpi)
        self.config = getattr(run, "config", None) or {}
        self.overrides = _rule_param_overrides(self.config)
        self.registry = getattr(run, "registry", None)
        self.frame_for = getattr(run, "frame_for", None) or (lambda _e, roles=None: None)
        self.refs = list(getattr(run, "refs", []) or [])
        self.equips = [r.equip for r in self.refs]
        self._frames: dict = {}
        self.findings = list(getattr(run, "findings", []) or [])
        self._evidence: dict = {}

    def frame(self, equip):
        if equip not in self._frames:
            self._frames[equip] = self.frame_for(equip)
        return self._frames[equip]

    def rule(self, name):
        if self.registry is None:
            return None
        try:
            return self.registry.get(name)
        except KeyError:
            return None

    def evidence(self, f):
        """The finding's Evidence (cached), or ``None``."""
        k = id(f)
        if k not in self._evidence:
            ev = None
            rule = self.rule(getattr(f, "rule", ""))
            frame = self.frame(getattr(f, "equip", ""))
            if rule is not None and frame is not None and not frame.empty:
                from ..charts.evidence import finding_evidence

                try:
                    ev = finding_evidence(rule, f.equip, frame)
                except Exception:  # noqa: BLE001 - a rule that cannot draw simply has no evidence
                    ev = None
            self._evidence[k] = ev
        return self._evidence[k]

    def mask_for(self, f):
        """The finding's violation mask from its evidence (rule-provided or rule-derived only)."""
        ev = self.evidence(f)
        if ev is None:
            return None
        if ev.mask is not None:
            return _on(ev.mask)
        if ev.frame is not None and ev.template is not None:
            from ..charts.diagnostic import template_violations

            return template_violations(ev.frame, ev.template)
        return None


def _occupancy_mask(frame, occupancy: dict | None):
    occ = _col(frame, Role.OCCUPANCY)
    kw = {}
    if occupancy:
        for k in ("start_hour", "end_hour"):
            if k in occupancy:
                kw[k] = occupancy[k]
        if "days" in occupancy:
            kw["days"] = tuple(occupancy["days"])
    trended = occ is not None and occ.notna().any()
    src = (
        "trended occupancy point"
        if trended
        else ("configured schedule" if occupancy else "assumed schedule (weekdays 07-18)")
    )
    return effective_occupied_mask(frame.index, occ=occ, **kw), src


# ============================================================================ the builder


def build_rcx_report(
    run, *, options: RcxOptions | None = None, notes=None, client=None
) -> RcxReport:
    """Build the RCx report for a config-driven run (:class:`camber.config.RunResult`).

    ``options`` defaults to the config's ``report.rcx`` section. ``notes`` is an engineer-notes dict
    (see :func:`load_notes`). ``client`` is reserved for the grounded AI prose slot (phase C); it
    is accepted and unused, so the slot renders nothing yet.
    """
    from .. import __version__
    from ..fault_economics import DEFAULTS, cost_findings
    from ..rules.triage import finding_confidence, issue_totals, link_findings
    from ..sensordrift import drift_finding
    from ..sensorhealth import frame_sensor_health, mixing_consistency

    base_dir = getattr(run, "base_dir", ".") or "."
    cfg = getattr(run, "config", None) or {}
    if options is None:
        options = RcxOptions.from_config(cfg.get("report"), base_dir=base_dir)
    ctx = _Ctx(run, options)
    o = options

    # ---- per-equipment operating state, trust and mixing
    air, plant, gates, gate_src, occ, occ_src = [], [], {}, {}, {}, {}
    trust_gated, trust_raw, mixing = {}, {}, {}
    for e in ctx.equips:
        fr = ctx.frame(e)
        if fr is None or fr.empty:
            continue
        g, src = fan_on_mask(fr)
        gates[e], gate_src[e] = g, src
        occ[e], occ_src[e] = _occupancy_mask(fr, o.occupancy)
        if any(_col(fr, r) is not None for r in _AIR_ROLES):
            air.append(e)
        elif any(_col(fr, r) is not None for r in _PLANT_MARKERS):
            plant.append(e)
        num = fr.select_dtypes(include="number")
        trust_raw[e] = frame_sensor_health(num)
        trust_gated[e] = frame_sensor_health(num, gate=g) if g is not None else trust_raw[e]
        gfr = fr[_on(g, fr.index).to_numpy()] if g is not None else fr
        mixing[e] = mixing_consistency(gfr)

    # ---- optional OAT reference comparison (adds a sensor_drift:oat finding for the report)
    findings = list(ctx.findings)
    ref_rows, ref_note = [], ""
    if o.oat_reference:
        site_oat = _oat_of({e: ctx.frame(e) for e in air or ctx.equips})
        if site_oat is None:
            ref_note = "An OAT reference is configured, but no equipment trends OAT."
        else:
            try:
                ref = _load_reference_oat(o.oat_reference, site_oat.index)
            except Exception as exc:  # noqa: BLE001 - a bad reference is reported, not fatal
                ref, ref_note = None, f"OAT reference could not be loaded: {exc}"
            if ref is not None:
                f = drift_finding(site_oat, ref, "site OAT", Role.OAT)
                findings.append(f)
                m = f.metrics
                ref_rows.append(
                    [
                        f.severity,
                        m.get("bias"),
                        m.get("drift_per_month"),
                        m.get("rmse"),
                        m.get("correlation"),
                        m.get("n"),
                    ]
                )

    # ---- SAT reset tiers (decides which compliance findings may be priced)
    seq = (o.sequence or {}).get("sat_reset")
    soo_classes = {s.get("class") for s in cfg.get("soo", []) or []}
    tiers = {}
    for e in air:
        fr = ctx.frame(e)
        if (
            _col(fr, Role.SUPPLY_AIR_TEMP_SP) is not None
            and _col(fr, Role.SUPPLY_AIR_TEMP_SP).notna().any()
        ):
            tiers[e] = 1
        elif seq or soo_classes:
            tiers[e] = 2
        else:
            tiers[e] = 3
    compliance_site = "supply_air_reset_compliance" in ctx.overrides

    # A declared site sequence re-judges the compliance findings against *that* map, so the
    # verdict (and its dollars) come from the site, not from the G36 defaults.
    if seq and not compliance_site:
        findings = _rejudge_compliance(ctx, findings, seq, [e for e in air if tiers[e] == 2])

    def exclude_cost(f):
        if getattr(f, "rule", "") != "supply_air_reset_compliance":
            return None
        if compliance_site or (f.metrics or {}).get("reset_source") not in (None, "g36_default"):
            return None
        if tiers.get(f.equip, 3) == 1:
            return "G36 default target, not the unit's trended setpoint (reference only)"
        return "G36 default target; no site sequence known (reference only)"

    # ---- mapping confidence (one level for the run)
    kind = str((cfg.get("source") or {}).get("kind") or "")
    if kind == "store":
        mapping = ("H", "roles recorded at ingest by the dataset adapter")
    else:
        mapping = ("M", "tag-to-role mapping from the config; not independently verified")
    corro = _synthetic_per_rule() if o.corroboration else {}

    def confidence_for(iss):
        root = iss.root
        rule = ctx.rule(getattr(root, "rule", ""))
        roles = [getattr(r, "value", str(r)) for r in getattr(rule, "roles_required", ())]
        tmap = trust_gated.get(iss.equip, {})
        trust = {r.value if isinstance(r, Role) else str(r): t for r, t in tmap.items()}
        trust = {r: t for r, t in trust.items() if r in roles}
        cov = [t.coverage for t in trust.values()]
        rname = getattr(root, "rule", "")
        if rname in ctx.overrides:
            assume = (
                "H",
                f"site-configured parameters {json.dumps(ctx.overrides[rname], sort_keys=True)}",
            )
        elif rname == "supply_air_reset_compliance" and exclude_cost(root):
            assume = ("L", str(exclude_cost(root)))
        elif rname.startswith("sensor_drift:"):
            assume = ("M", "reference series and default bias / drift thresholds")
        else:
            assume = ("M", "rule defaults; no site parameters configured")
        m = mapping
        if any("scale_suspect" in (t.flags or []) for t in trust.values()):
            m = ("L", "a point looks like a percent signal mapped to a flow role")
        return finding_confidence(
            root,
            trust=trust or None,
            mapping=m,
            assumptions=assume,
            coverage=(min(cov) if cov else None),
            corroboration=corro.get(rname),
            conditional_on=iss.conditional_on,
        )

    costs = cost_findings(findings, o.loads, o.price, params=o.cost_params)
    trust_for_causes = {
        e: {getattr(r, "value", str(r)): t for r, t in tm.items()} for e, tm in trust_gated.items()
    }
    issues = link_findings(
        findings,
        rules=ctx.registry,
        costs=costs,
        exclude_cost=exclude_cost,
        mask_for=ctx.mask_for,
        runtime=lambda e: (gates.get(e), gate_src.get(e, FAN_GATE_NONE)),
        trust=trust_for_causes,
        mixing=mixing,
        confidence_for=confidence_for,
        facility_id=getattr(run, "facility_id", None) or "",
        site=getattr(run, "site", "") or "",
    )
    totals = issue_totals(issues)

    # ---- coverage + declines
    covs = []
    for e in air:
        fr = ctx.frame(e)
        for r in _P3_ROLES:
            s = _col(fr, r)
            if s is not None:
                covs.append(float(s.notna().mean()))
    coverage = float(np.mean(covs)) if covs else float("nan")
    declined = [f for f in findings if (getattr(f, "metrics", None) or {}).get("declined")]

    # ---- representative week
    hl = 65.0
    econ_rule = ctx.rule("economizer_high_limit")
    if econ_rule is not None:
        hl = float(getattr(econ_rule, "high_limit_f", 65.0))
    # The week is scored on the air handlers; a site with none (a plant-only report) is scored on
    # its water-side plants and their panels instead (#32). A mixed site keeps its air-side week, so
    # a plant's logging gaps never disqualify it; the plant panels are drawn for the same week.
    plant_set = set() if air else set(plant)
    week = select_week(
        {e: ctx.frame(e) for e in (air or plant)},
        issues=issues,
        mode=o.week_mode(),
        gate_for=gates.get,
        occupied_for=occ.get,
        high_limit_f=hl,
        roles_for=lambda e: _PLANT_ROLES if e in plant_set else _P3_ROLES,
    )

    idx_all = [
        ctx.frame(e).index for e in ctx.equips if ctx.frame(e) is not None and len(ctx.frame(e))
    ]
    period = (
        (min(i.min() for i in idx_all), max(i.max() for i in idx_all)) if idx_all else (None, None)
    )
    kpis = {
        "annual_cost_usd": totals["annual_cost_usd"] if totals["n_costed"] else None,
        "n_costed": totals["n_costed"],
        "at_risk_usd": totals["at_risk_usd"] or None,
        "n_issues": totals["n_issues"],
        "n_uncosted": totals["n_uncosted"],
        "n_conditional": totals["n_conditional"],
        "coverage_pct": None if coverage != coverage else round(100 * coverage, 1),
        "n_declined": len(declined),
    }

    S = {
        "ctx": ctx,
        "o": o,
        "air": air,
        "plant": plant,
        "gates": gates,
        "gate_src": gate_src,
        "occ": occ,
        "occ_src": occ_src,
        "trust_gated": trust_gated,
        "trust_raw": trust_raw,
        "mixing": mixing,
        "issues": issues,
        "totals": totals,
        "week": week,
        "tiers": tiers,
        "seq": seq,
        "soo": soo_classes,
        "findings": findings,
        "declined": declined,
        "ref_rows": ref_rows,
        "ref_note": ref_note,
        "costs": costs,
        "cost_defaults": {**DEFAULTS, **(o.cost_params or {})},
        "exclude_cost": exclude_cost,
        "kpis": kpis,
        "hl": hl,
        "g36_declared": _g36_declared_for(cfg, ctx.refs),
    }
    wanted = set(o.sections) if o.sections else None

    def want(sid):
        return wanted is None or sid in wanted

    sections: list = []
    builders = (
        ("cover", _sec_cover),
        ("summary", _sec_summary),
        ("data", _sec_data),
        ("week", _sec_week),
        ("economizer", _sec_economizer),
        ("sat", _sec_sat),
        ("air", _sec_air),
        ("mv", _sec_mv),
    )
    for sid, fn in builders:
        if want(sid):
            sec = fn(S)
            if sec is not None:
                sections.append(sec)
    if want("issues"):
        sections += [_sec_issue(S, iss) for iss in issues]
    if want("appendix"):
        sections += _sec_appendices(S)

    title = o.title or f"Retro-commissioning report — {getattr(run, 'site', '') or 'site'}"
    rep = RcxReport(
        title=title,
        site=getattr(run, "site", "") or "",
        facility_id=getattr(run, "facility_id", None),
        period=period,
        options=o,
        data_sources=list(getattr(run, "data_sources", []) or []),
        kpis=kpis,
        issues=issues,
        week=week,
        sections=sections,
        version=__version__,
    )

    # ---- notes: file notes + (optionally) the fault lifecycle's own notes
    raw = dict(notes or {})
    if o.lifecycle:
        for k, v in _lifecycle_notes(run, issues).items():
            raw[k] = _note_list(raw.get(k, [])) + v
    slots = set(rep.slots())
    for k, v in raw.items():
        items = _note_list(v)
        if not items:
            continue
        (rep.notes if k in slots else rep.orphans)[k] = items
    if rep.orphans and want("appendix"):
        for s in rep.sections:
            if s["id"] == "appendix-e":
                s["blocks"] = _orphan_blocks(rep.orphans)
    return rep


def _rejudge_compliance(ctx, findings, seq: dict, equips: list) -> list:
    """Replace G36-default ``supply_air_reset_compliance`` findings on ``equips`` with a re-run
    against the declared site sequence (``{"oat": [lo, hi], "sat": [at_lo, at_hi]}``)."""
    from ..rules.satreset_compliance_rule import SupplyAirResetCompliance

    (o1, o2), (s1, s2) = seq["oat"], seq["sat"]
    lo, hi = (float(o1), float(s1)), (float(o2), float(s2))
    if lo[0] > hi[0]:
        lo, hi = hi, lo
    rule = SupplyAirResetCompliance(
        min_clg_sat=hi[1],
        t_max=lo[1],
        oat_min=lo[0],
        oat_max=hi[0],
        tol_f=float(seq.get("tol_f", 1.0)),
        reset_source="site sequence (report.rcx.sequence)",
    )
    out = []
    for f in findings:
        declined_class = "applies_to_classes" in (getattr(f, "metrics", None) or {})
        if (
            getattr(f, "rule", "") == "supply_air_reset_compliance"
            and f.equip in equips
            and not declined_class  # a class decline stands whatever the sequence
        ):
            fr = ctx.frame(f.equip)
            if fr is not None and not fr.empty:
                f = rule.analyze(f.equip, fr)
        out.append(f)
    return out


def _lifecycle_notes(run, issues) -> dict:
    """``{issue:<key>: [notes]}`` from the run's fault store (facility-keyed where available)."""
    from ..config import _FacilityCtx, _path, _state_path
    from ..faultlifecycle import FaultLifecycle
    from ..integrate.tickets import fingerprint

    cfg = getattr(run, "config", None) or {}
    spec = cfg.get("faults") or {}
    ctx = _FacilityCtx(getattr(run, "facility_id", None), getattr(run, "workspace", None), run.site)
    path = (
        _path(getattr(run, "base_dir", "."), spec["store"])
        if spec.get("store")
        else _state_path(ctx, "faults.json")
    )
    if not path or not os.path.exists(path):
        return {}
    lc = FaultLifecycle.load(path, facility_id=ctx.bound)
    recs = {r.fingerprint: r for r in lc.records()}
    for r in lc.records():
        for a in r.aliases or []:
            recs.setdefault(a, r)
    out: dict = {}
    for iss in issues:
        cands = [iss.key]
        eq, rule = iss.equip, getattr(iss.root, "rule", "")
        cands += [
            fingerprint(k, eq, rule) for k in (run.site, getattr(run, "facility_id", "")) if k
        ]
        for k in cands:
            r = recs.get(k)
            if r is not None and r.notes:
                out[f"issue:{iss.key}"] = [
                    {"text": str(n), "author": "fault lifecycle", "date": r.last_seen or ""}
                    for n in r.notes
                ]
                break
    return out


# ============================================================================ sections


def _section(sid: str, title: str, blocks: list, *, slot: bool = True, kind: str = "section"):
    return {
        "id": sid,
        "title": title,
        "blocks": blocks,
        "slot": f"section:{sid}" if slot else None,
        "kind": kind,
    }


def _sec_cover(S) -> dict:
    ctx = S["ctx"]
    run = ctx.run
    blocks = []
    rows = [
        ["Site", getattr(run, "site", "")],
        ["Facility id", getattr(run, "facility_id", "") or "—"],
        ["Equipment analysed", f"{len(ctx.equips)} ({len(S['air'])} air-side)"],
        ["Rules run", str(len(getattr(run, "rules_run", []) or []))],
        ["Findings", str(len(S["findings"]))],
    ]
    if ctx.config.get("source"):
        src = ctx.config["source"]
        rows.append(["Data source", str(src.get("kind") or "per-point CSV folders")])
    blocks.append(_table(["", ""], rows, css="kv"))
    blocks.append(
        _p(
            "Advisory report built from trend data by CAMBER, read-only toward the building "
            "automation system. Every verdict names its evidence; checks that could not be "
            "evaluated are listed in Appendix A, and every assumption used in Appendix B."
        )
    )
    return _section("cover", "Cover and provenance", blocks)


def _sec_summary(S) -> dict:
    from ..aso import recommend

    o, issues, k = S["o"], S["issues"], S["kpis"]
    # a dollar figure only when something is costed -- "$0" would read as "nothing to fix"
    costed = (
        ("Costed issues, $/yr", _fmt_usd(k["annual_cost_usd"]))
        if k["n_costed"]
        else ("$/yr: no costed issues", "—")
    )
    kp = [
        costed,
        ("Uncosted / conditional issues", f"{k['n_uncosted']} / {k['n_conditional']}"),
        (
            "Data coverage",
            "—" if k["coverage_pct"] is None else f"{k['coverage_pct']:.0f}%",
        ),
        ("Declined checks", str(k["n_declined"])),
    ]
    blocks = [{"kind": "kpis", "items": [{"label": a, "value": b} for a, b in kp]}]
    if k["at_risk_usd"]:
        blocks.append(
            _p(f"A further {_fmt_usd(k['at_risk_usd'])}/yr is at risk pending a sensor fix.")
        )
    rows = []
    for iss in issues[: max(int(o.top_n), 0)]:
        title, action, _sug = _advice(S, iss, recommend(iss.root))
        action = action[:1].upper() + action[1:] if action else ""
        action = action or "Engineer to specify (no packaged action)."
        cost = _fmt_usd(iss.cost) if iss.cost is not None else _cut(iss.cost_basis_note, 60)
        if iss.conditional:
            cost += " (at risk)" if iss.cost is not None else " (conditional)"
        rows.append(
            [
                f"<a href='#issue-{iss.key}'>{iss.rank}</a>",
                _cut(title, 60),
                iss.equip,
                cost,
                iss.severity,
                iss.confidence,
                _cut(action, 110),
            ]
        )
    if rows:
        tbl = _table(["#", "Issue", "Equipment", "$/yr", "Severity", "Conf.", "Action"], rows)
        tbl["link_col"] = 0  # the first column carries an internally built anchor
        blocks.append(tbl)
        if len(issues) > len(rows):
            blocks.append(_p(f"{len(issues) - len(rows)} more issue(s) follow on their own pages."))
    else:
        blocks.append(_p("No actionable issues were found in this period."))
    sec = _section("summary", "Executive summary", blocks)
    sec["slot"] = "exec_summary"
    return sec


def _trust_cell(t) -> str:
    if t is None:
        return "—"
    flags = ", ".join(t.flags) if t.flags else "no flags"
    return f"{t.verdict} ({t.trust:.2f}; {flags})"


def _sec_data(S) -> dict:
    plt = _plt()
    from ..charts.readiness import readiness_ribbon

    ctx = S["ctx"]
    blocks: list = []
    for e in S["air"]:
        fr = ctx.frame(e)
        cols = [r for r in _P3_ROLES if _col(fr, r) is not None]
        if cols:
            fig, ax = plt.subplots(figsize=(10, 0.35 * len(cols) + 1.4))
            readiness_ribbon(fr[cols], ax=ax, title=f"{e}: data readiness")
            blocks.append(_figure(fig, alt=f"{e} readiness", fmt="png", dpi=ctx.dpi))
    if S["ref_rows"]:
        blocks.append(_p("BAS outdoor-air temperature vs the configured reference:"))
        blocks.append(
            _table(
                ["Result", "Bias °F", "Drift °F/month", "RMSE °F", "r", "Samples"], S["ref_rows"]
            )
        )
    elif S["ref_note"]:
        blocks.append(_p(S["ref_note"]))
    else:
        blocks.append(
            _p(
                "No OAT reference configured: the building OAT sensor is not checked against "
                "weather."
            )
        )
    mrows = [
        [
            e,
            m.severity,
            m.n_checked,
            "—" if m.violation_frac != m.violation_frac else f"{m.violation_frac:.1%}",
            m.summary,
        ]
        for e, m in sorted(S["mixing"].items())
        if e in S["air"]
    ]
    if mrows:
        blocks.append(_p("Mixing consistency (mixed air between outdoor and return air, fan-on):"))
        blocks.append(_table(["Equipment", "Result", "Samples", "Outside", "Detail"], mrows))
    rows = []
    for e in sorted(S["trust_raw"]):
        for r, t in S["trust_raw"][e].items():
            if r not in _P3_ROLES and r not in (Role.AIRFLOW, Role.SUPPLY_FAN_STATUS):
                continue
            gt = S["trust_gated"][e].get(r)
            gated = S["gates"].get(e) is not None
            rows.append(
                [
                    e,
                    getattr(r, "value", str(r)),
                    _trust_cell(gt) if gated else "—",
                    _trust_cell(t),
                    S["gate_src"].get(e, FAN_GATE_NONE),
                ]
            )
    if rows:
        blocks.append(_p("Sensor trust, scored on fan-on samples (gated) and on all samples:"))
        blocks.append(_table(["Equipment", "Point", "Gated", "Ungated", "Gate used"], rows))
    return _section("data", "Data coverage and sensor health", blocks)


def _sec_week(S) -> dict:
    plt = _plt()
    from ..charts.multitrend import fault_multitrend

    ctx, w = S["ctx"], S["week"]
    blocks: list = []
    if w is None or w.declined:
        blocks.append(
            {"kind": "banner", "text": "Representative week declined: " + (w.reason if w else "")}
        )
        return _section("week", "Representative week", blocks)
    blocks.append(_p(w.explanation))
    by_equip: dict = {}
    for iss in S["issues"]:
        if iss.mask is not None:
            by_equip.setdefault(iss.equip, []).append(iss)
    order = sorted(S["air"], key=lambda e: (-len(by_equip.get(e, [])), e))
    plant = sorted(S.get("plant", ()), key=lambda e: (-len(by_equip.get(e, [])), e))
    # up to four air handlers, then up to two water-side plants on their own panels (#32)
    charted = [(e, P3_FAMILIES) for e in order[:4]] + [(e, PLANT_FAMILIES) for e in plant[:2]]
    for e, families in charted:
        fr = ctx.frame(e)
        win = fr[(fr.index >= w.start) & (fr.index < w.end)]
        fams = [(u, t, [r for r in roles if _col(win, r) is not None]) for u, t, roles in families]
        fams = [f for f in fams if f[2]]
        if not fams or win.empty:
            continue
        gate = S["gates"].get(e)
        ok = _on(S["occ"][e], win.index)
        if gate is not None:
            ok &= _on(gate, win.index)
        spans = {}
        for iss in by_equip.get(e, [])[:3]:
            m = _on(iss.mask, win.index) & ok
            if m.any():
                spans[f"#{iss.rank} {_humanize(getattr(iss.root, 'rule', ''))}"] = m
        fig, axes = plt.subplots(
            len(fams), 1, figsize=(10, 2.3 * len(fams)), sharex=True, squeeze=False
        )
        for ax, (unit, label, roles) in zip(axes[:, 0], fams):
            data = pd.DataFrame(
                {getattr(r, "value", str(r)): _col(win, r) for r in roles}, index=win.index
            )
            fault_multitrend(
                data, list(data.columns), spans=spans, ax=ax, normalize=False, title=f"{e}: {label}"
            )
            ax.set_ylabel(unit)
            ax.set_xlabel("")
        blocks.append(
            _figure(
                fig,
                alt=f"{e} representative week",
                caption=(
                    (
                        "Shaded: violations during occupied, fan-on time only."
                        if families is P3_FAMILIES or gate is not None
                        else "Shaded: violations during occupied time only."
                    )
                    if spans
                    else "No masked violations in this week."
                ),
                fmt=ctx.fmt,
                dpi=ctx.dpi,
            )
        )
    return _section("week", "Representative week", blocks)


def _sec_economizer(S) -> dict | None:
    plt = _plt()
    from ..charts.diagnostic import diagnostic_scatter
    from ..freecooling import free_cooling_opportunity
    from ..rules.economizer_lockout_rule import EconomizerHighLimit
    from ..units import normalize_percent

    ctx = S["ctx"]
    rule = ctx.rule("economizer_high_limit")
    configured = rule is not None and "economizer_high_limit" in ctx.overrides
    rule = rule if rule is not None else EconomizerHighLimit()
    blocks: list = []
    oat_issue = [
        c
        for iss in S["issues"]
        for c in iss.conditional_on
        if "oat" in c.roles and c.kind in ("sensor_drift", "trust")
    ]
    if oat_issue:
        blocks.append(
            {
                "kind": "banner",
                "text": "Economizer verdicts below are conditional on OAT: "
                + "; ".join(sorted({c.label() for c in oat_issue})),
            }
        )
    rows_fc, rows_mix = [], []
    for e in S["air"]:
        fr = ctx.frame(e)
        if _col(fr, Role.OAT) is None:
            continue
        has_temps = (
            _col(fr, Role.MIXED_AIR_TEMP) is not None and _col(fr, Role.RETURN_AIR_TEMP) is not None
        )
        if not has_temps and _col(fr, Role.OA_DAMPER) is None:
            continue
        ev = rule.evidence(e, fr)
        f = next(
            (x for x in S["findings"] if x.rule == "economizer_high_limit" and x.equip == e), None
        )
        if ev is not None:
            fig, ax = plt.subplots(figsize=(7, 4.2))
            diagnostic_scatter(ev.frame, ev.template, ax=ax)
            ax.set_title(f"{e}: " + ax.get_title(), fontsize=9)
            verdict = (
                f"Verdict: {f.severity} — {f.summary}"
                if f is not None
                else "economizer_high_limit was not run on this unit; the chart applies its "
                "envelope."
            )
            params = (
                f"high limit {rule.high_limit_f:g}°F, differential "
                f"{'on' if rule.differential else 'off'} "
                f"({'site-configured' if configured else 'rule defaults'})"
            )
            m = (f.metrics or {}) if f is not None else {}
            if "n_masked_fan_off" in m:
                params += (
                    f"; not judged: {m['n_masked_fan_off']} hot fan-off samples, "
                    f"{m['n_masked_small_delta_t']} with |OAT − RAT| < {m['denom_min_f']:g} °F, "
                    f"{m['n_masked_out_of_range']} with OA fraction outside −20…120 %"
                )
            blocks.append(
                _figure(
                    fig,
                    alt=f"{e} economizer",
                    caption=f"{params}. {verdict}",
                    fmt="png",
                    dpi=ctx.dpi,
                )
            )
        m = S["mixing"].get(e)
        if m is not None:
            rows_mix.append(
                [
                    e,
                    m.severity,
                    m.n_checked,
                    "—" if m.violation_frac != m.violation_frac else f"{m.violation_frac:.1%}",
                ]
            )
        cool = _col(fr, Role.COOL_VALVE)
        if cool is not None:
            gate = S["gates"].get(e)
            on = _on(gate, fr.index) if gate is not None else pd.Series(True, index=fr.index)
            oat = _col(fr, Role.OAT)[on]
            sig = (normalize_percent(cool) / 100.0)[on]
            # Mechanical cooling while the unit is already on (nearly) 100 % outside air is an
            # integrated economizer doing its job, not missed free cooling. Judge that on the
            # measured OA fraction where the temperature balance is stable (a damper *command*
            # can read open while the damper is stuck), else on the damper signal.
            econ = pd.Series(False, index=fr.index)
            damper = _col(fr, Role.OA_DAMPER)
            if damper is not None:
                econ = normalize_percent(damper).fillna(0) >= 90.0
            mat, rat = _col(fr, Role.MIXED_AIR_TEMP), _col(fr, Role.RETURN_AIR_TEMP)
            if mat is not None and rat is not None:
                oat_all = _col(fr, Role.OAT)
                dt = rat - oat_all
                stable = dt.abs() >= 5.0
                oaf = 100.0 * (rat - mat) / dt.where(stable)
                econ = econ.where(~stable, oaf >= 80.0)
            sig = sig.where(~econ[on].fillna(False).astype(bool), 0.0)
            fc = free_cooling_opportunity(oat, sig, high_limit_f=float(rule.high_limit_f))
            rows_fc.append(
                [
                    e,
                    f"{fc.hours_available:,.0f}",
                    f"{fc.hours_missed:,.0f}",
                    "—"
                    if fc.missed_fraction != fc.missed_fraction
                    else f"{fc.missed_fraction:.0%}",
                ]
            )
    if not blocks and not rows_fc and not rows_mix:
        return None
    if rows_mix:
        blocks.append(
            _p("Mixed air must sit between outdoor and return air (±5 °F, fan-on samples):")
        )
        blocks.append(_table(["Equipment", "Result", "Samples", "Outside"], rows_mix))
    if rows_fc:
        blocks.append(
            _p(
                f"Free cooling (fan-on hours with OAT below the "
                f"{float(rule.high_limit_f):g}°F high "
                "limit while the cooling valve was open and the unit was not already near 100 % "
                "outside air -- an integrated economizer is not counted; OA fraction from the "
                "temperature balance where |OAT − RAT| ≥ 5 °F, else the damper signal ≥ 90 %):"
            )
        )
        blocks.append(_table(["Equipment", "Hours available", "Hours missed", "Missed"], rows_fc))
    return _section("economizer", "Economizer", blocks)


def _sat_tier_blocks(S, e) -> list:
    plt = _plt()
    from ..charts.diagnostic import diagnostic_scatter, reset_line, template_violations
    from ..charts.oat_scatter import oat_scatter
    from ..g36_reset import oat_sat_setpoint
    from ..satreset import analyze_satreset

    ctx = S["ctx"]
    fr = ctx.frame(e)
    tier = S["tiers"][e]
    gate = S["gates"].get(e)
    ok = _on(S["occ"][e], fr.index)
    if gate is not None:
        ok &= _on(gate, fr.index)
    g = fr[ok.to_numpy()]
    gate_txt = f"{S['gate_src'].get(e, FAN_GATE_NONE)}; {S['occ_src'].get(e, '')}"
    sat, oat = _col(g, Role.SUPPLY_AIR_TEMP), _col(g, Role.OAT)
    blocks: list = []
    if tier == 1:
        sp = _col(g, Role.SUPPLY_AIR_TEMP_SP)
        err = (sat - sp).dropna() if sat is not None else pd.Series(dtype=float)
        blocks.append(
            _p(
                f"{e} — tier 1 (trended SAT setpoint): mean |SAT − SP| {err.abs().mean():.1f} °F, "
                f"{(err.abs() > 2).mean():.0%} of {len(err)} gated samples off by more than 2 °F "
                f"({gate_txt})."
                if len(err)
                else f"{e} — tier 1 (trended SAT setpoint): no gated samples with both SAT and SP."
            )
        )
        blocks.append(
            _p(
                "The unit's own setpoint is the reference here: a G36-default reset-compliance "
                "finding is shown as a reference only and is not priced."
            )
        )
        if oat is not None and sp.notna().sum() >= 5:
            fig, ax = plt.subplots(figsize=(7, 4))
            flat = float(sp.std()) < 0.05  # a fixed setpoint: no change-point to fit
            oat_scatter(
                sp,
                oat,
                ax=ax,
                changepoint=False if flat else "auto",
                classify=not flat,
                ylabel="SAT setpoint (°F)",
                title=f"{e}: SAT setpoint vs OAT" + (" (fixed setpoint)" if flat else ""),
            )
            if flat:
                mid = float(sp.median())
                ax.set_ylim(mid - 3, mid + 3)
            ax.ticklabel_format(axis="y", useOffset=False)
            blocks.append(_figure(fig, alt=f"{e} SAT SP vs OAT", fmt="png", dpi=ctx.dpi))
        return blocks
    if tier == 2 and S["seq"] and sat is not None and oat is not None:
        q = S["seq"]
        (o1, o2), (s1, s2) = q["oat"], q["sat"]
        tmpl = reset_line(
            Role.OAT,
            Role.SUPPLY_AIR_TEMP,
            p1=(float(o1), float(s1)),
            p2=(float(o2), float(s2)),
            tol=float(q.get("tol_f", 2.0)),
            name="declared site SAT reset",
            cite="site sequence (config)",
            xlabel="OAT (°F)",
            ylabel="SAT (°F)",
        )
        viol = template_violations(g, tmpl)
        blocks.append(
            _p(
                f"{e} — tier 2 (declared site sequence): SAT outside the declared reset band "
                f"(±{float(q.get('tol_f', 2.0)):g} °F) for {viol.mean():.0%} of {len(viol)} "
                f"fan-on, occupied hours ({gate_txt})."
                if len(viol)
                else f"{e} — tier 2: no fan-on, occupied samples to census."
            )
        )
        fig, ax = plt.subplots(figsize=(7, 4))
        diagnostic_scatter(g, tmpl, ax=ax)
        blocks.append(_figure(fig, alt=f"{e} SAT reset census", fmt="png", dpi=ctx.dpi))
        return blocks
    if tier == 2:
        blocks.append(
            _p(
                f"{e} — tier 2 (declared via the config's SOO spec): the sequence-of-operations "
                "conformance findings carry the verdict (see the issue pages)."
            )
        )
        return blocks
    # tier 3: neither a trended setpoint nor a declared sequence -> descriptors, verdict declined
    blocks.append(
        {
            "kind": "banner",
            "text": f"{e} — SAT reset verdict declined: no site sequence known (tier 3). "
            "Descriptors only; supply_air_reset_compliance is excluded from the $ totals.",
        }
    )
    if sat is not None and oat is not None and sat.notna().sum() >= 5:
        fig, ax = plt.subplots(figsize=(7, 4))
        oat_scatter(sat, oat, ax=ax, ylabel="SAT (°F)", title=f"{e}: SAT vs OAT (fan-on, occupied)")
        if S["o"].g36_reference:
            xs = np.linspace(float(np.nanmin(oat)), float(np.nanmax(oat)), 50)
            ax.plot(
                xs, oat_sat_setpoint(xs), "k--", lw=1, label="G36 map — reference, not a verdict"
            )
            ax.legend(fontsize=7)
        blocks.append(_figure(fig, alt=f"{e} SAT vs OAT", fmt="png", dpi=ctx.dpi))
        legacy = pd.DataFrame({"SupplyAir": _col(fr, Role.SUPPLY_AIR_TEMP)}, index=fr.index)
        cv = _col(fr, Role.COOL_VALVE)
        if cv is not None:
            legacy["CHW_Valve"] = cv
        occ_pt = _col(fr, Role.OCCUPANCY)
        if occ_pt is not None:
            legacy["Occupancy"] = occ_pt
        res = analyze_satreset(legacy, e, oat=_col(fr, Role.OAT), gate=S["gates"].get(e))
        if res is not None:
            slope = "—" if res.slope_per_F is None else f"{res.slope_per_F:+.2f} °F/°F"
            blocks.append(
                _table(
                    ["Descriptor", "Value"],
                    [
                        [
                            "SAT median (p05–p95)",
                            f"{res.sat_median:.1f} ({res.sat_p05:.1f}–{res.sat_p95:.1f}) °F",
                        ],
                        ["SAT spread (std)", f"{res.sat_std:.1f} °F"],
                        ["Slope vs OAT", slope],
                        ["Cooling hours below 58 °F", f"{res.pct_sat_below_58:.0f}%"],
                        ["Samples", str(res.n_considered)],
                    ],
                    css="kv",
                )
            )
    return blocks


def _sec_sat(S) -> dict | None:
    blocks: list = []
    for e in S["air"]:
        if _col(S["ctx"].frame(e), Role.SUPPLY_AIR_TEMP) is None:
            continue
        blocks += _sat_tier_blocks(S, e)
    if not blocks:
        return None
    blocks.insert(
        0,
        _p(
            "Tiers: 1 = a trended SAT setpoint (tracking error); 2 = a declared site sequence "
            "(census against it); 3 = neither (descriptors only, verdict declined)."
        ),
    )
    return _section("sat", "Supply-air temperature reset census", blocks)


_STATIC_RULES = (
    "static_pressure_reset",
    "static_reset_effectiveness",
    "static_rogue_zone_census",
    "damper_census",
    "duct_static_drift",
)


def _sec_air(S) -> dict | None:
    plt = _plt()
    from ..charts.boxhour import box_by_hour

    ctx = S["ctx"]
    blocks: list = []
    for e in S["air"]:
        fr = ctx.frame(e)
        st = _col(fr, Role.DUCT_STATIC)
        if st is None or st.notna().sum() < 24:
            continue
        gate = S["gates"].get(e)
        fig, ax = plt.subplots(figsize=(9, 3.2))
        box_by_hour(
            st,
            mask=gate,
            ax=ax,
            ylabel="Duct static (in.w.c.)",
            title=f"{e}: duct static by hour ({S['gate_src'].get(e, FAN_GATE_NONE)})",
        )
        blocks.append(_figure(fig, alt=f"{e} duct static by hour", fmt="png", dpi=ctx.dpi))
    rows = [
        [f.equip, f.rule, f.severity, _cut(f.summary, 160)]
        for f in S["findings"]
        if f.rule in _STATIC_RULES
    ]
    if rows:
        blocks.append(_table(["Equipment", "Rule", "Result", "Finding"], rows))
    if not blocks:
        return None
    return _section("air", "Air distribution", blocks)


def _sec_mv(S) -> dict | None:
    run = S["ctx"].run
    blocks: list = []
    drift = getattr(run, "drift", None)
    if drift is not None:
        from .drift import drift_report_html

        blocks.append({"kind": "html", "html": drift_report_html(drift, standalone=False)})
    mv = [f for f in S["findings"] if f.rule == "mv_baseline"]
    if mv:
        rows = []
        for f in mv:
            m = f.metrics or {}
            rows.append(
                [
                    f.equip,
                    "declined" if m.get("declined") else f.severity,
                    m.get("model", "—"),
                    "—" if m.get("r2") is None else f"{m['r2']:.2f}",
                    "—" if m.get("cv_rmse") is None else f"{m['cv_rmse']:.1%}",
                    f.summary,
                ]
            )
        blocks.append(_table(["Meter", "Result", "Model", "R²", "CV(RMSE)", "Summary"], rows))
    if not blocks:
        return None
    return _section("mv", "M&V and drift", blocks)


# ---- G36 advice only where a G36 sequence is declared (#32)

_G36_NOT_DECLARED = "no ASHRAE Guideline 36 sequence is declared for this unit"


def _g36_rule(rule) -> bool:
    """A check that assumes a Guideline 36 sequence (by the ``_g36`` / ``g36_`` naming)."""
    r = str(rule or "")
    return r.endswith("_g36") or r.startswith("g36_")


def _g36_declared_for(cfg: dict, refs) -> Callable:
    """``declared(equip) -> bool``: the config declares a G36 sequence for the unit -- a ``soo``
    entry with a ``g36_*`` library for its class. A terminal unit counts when its air system (the
    ``AHU`` class) declares one, since G36 sequences the terminals with their air handler."""
    from ..resolve import TERMINAL_CLASSES

    classes = {
        str(e.get("class") or "")
        for e in (cfg.get("soo") or [])
        if isinstance(e, dict) and str(e.get("library") or "").startswith("g36")
    }
    cls_of = {r.equip: str(getattr(r, "equip_class", "") or "") for r in refs or ()}

    def declared(equip) -> bool:
        c = cls_of.get(equip, "")
        if c in classes:
            return True
        return (c in TERMINAL_CLASSES or c == "TERMINAL") and "AHU" in classes

    return declared


def _advice(S, iss, rec) -> tuple:
    """``(title, action, suggested)`` for an issue from its packaged recommendation (``action`` is
    ``""`` when there is none). With no G36 sequence declared for the unit, a check that assumes one
    gets no packaged action, and an action that prescribes a G36 sequence is qualified as reference
    practice rather than stated as the fix."""
    rule = getattr(iss.root, "rule", "")
    title = rec.title if rec is not None else _humanize(rule)
    if rec is None:
        return title, "", ""
    action = rec.action
    suggested = f"{rec.parameter} → {rec.suggested}" if rec.suggested else ""
    declared = S.get("g36_declared")
    if declared is None or declared(iss.equip):
        return title, action, suggested
    if _g36_rule(rule):
        return (
            _humanize(rule),
            "engineer to specify -- this check assumes a Guideline 36 sequence and "
            f"{_G36_NOT_DECLARED}, so no G36 action is given. Confirm the unit's actual sequence "
            "first (declare one with a g36_* library `soo` entry to get the G36 advice).",
            "",
        )
    if "G36" in action:
        action = (
            f"{action} (G36 reference practice: {_G36_NOT_DECLARED}; check it against the "
            "unit's own sequence first.)"
        )
    return title, action, suggested


def _sec_issue(S, iss) -> dict:
    from ..aso import recommend
    from .dashboard import render_evidence_blocks

    ctx = S["ctx"]
    root = iss.root
    title, action, suggested = _advice(S, iss, recommend(root, frame=ctx.frame(iss.equip)))
    blocks: list = []
    if iss.conditional:
        blocks.append(
            {
                "kind": "banner",
                "text": "Conditional pending a sensor fix: "
                + "; ".join(c.label() for c in iss.conditional_on),
            }
        )
    head = [
        ["Equipment", iss.equip],
        ["Severity", iss.severity],
        [
            "$/yr",
            (_fmt_usd(iss.cost) + (" (at risk)" if iss.conditional else ""))
            if iss.cost is not None
            else "—",
        ],
        ["Cost basis", iss.cost_basis_note or "—"],
    ]
    idx = next(
        (
            i
            for i, f in enumerate(iss.members)
            if iss.member_costs
            and iss.member_costs[i] is not None
            and getattr(iss.member_costs[i], "costed", False)
        ),
        None,
    )
    if idx is not None and iss.member_costs[idx].assumptions:
        head.append(["Assumptions", json.dumps(iss.member_costs[idx].assumptions, sort_keys=True)])
    if iss.hours_union is not None:
        pct = iss.pct_runtime
        head.append(
            [
                "Violation hours",
                f"{iss.hours_union:,.0f} h (union of members)"
                + (f" = {pct:.1f}% of {iss.fan_on_hours:,.0f} fan-on h" if pct is not None else "")
                + f"; gate: {iss.fan_gate or FAN_GATE_NONE}",
            ]
        )
    else:
        head.append(["Violation hours", "not measured (no member exposes a violation mask)"])
    head.append(["Confidence", iss.confidence])
    blocks.append(_table(["", ""], head, css="kv"))

    class _R:  # render_evidence_blocks reads .finding
        def __init__(self, f):
            self.finding = f

    if ctx.registry is not None:
        from .dashboard import _rules_map

        # a reference-only member (e.g. a G36-default target with no site sequence) gets no chart
        shown = [_R(f) for f in iss.members if not S["exclude_cost"](f)]
        ev_html = render_evidence_blocks(shown, _rules_map(ctx.registry), ctx.frame, dpi=ctx.dpi)
        if ev_html:
            blocks.append({"kind": "html", "html": ev_html})
    excl = S["exclude_cost"]
    mrows = []
    for i, (f, c) in enumerate(zip(iss.members, iss.member_costs or [None] * len(iss.members))):
        why = excl(f)
        summ = _cut(getattr(f, "summary", ""), 160)
        mrows.append(
            [
                "root" if i == 0 else "member",
                getattr(f, "rule", ""),
                getattr(f, "severity", ""),
                "excluded" if why else (_fmt_usd(c.annual_cost_usd) if c and c.costed else "—"),
                f"[{why}] {summ}" if why else summ,
            ]
        )
    blocks.append(_table(["Role", "Rule", "Severity", "Estimate $/yr", "Finding"], mrows))
    if action:
        blocks.append(_p(f"Recommended action: {action}"))
        if suggested:
            blocks.append(_p(f"Suggested: {suggested}"))
    else:
        blocks.append(_p("Recommended action: engineer to specify (no packaged recommendation)."))
    blocks.append(_p(f"Confidence {iss.confidence} — why we believe this:"))
    blocks.append({"kind": "list", "items": list(iss.why)})
    if iss.dependents:
        blocks.append(_p("Findings conditional on this sensor issue (kept, demoted, not deleted):"))
        blocks.append(
            {
                "kind": "list",
                "items": [
                    f"{getattr(f, 'rule', '')} on {getattr(f, 'equip', '')}" for f in iss.dependents
                ],
            }
        )
    caveats = []
    for f in iss.members:
        for c in getattr(f, "caveats", None) or []:
            if c not in caveats:
                caveats.append(c)
    if caveats:
        blocks.append(_p("Caveats:"))
        blocks.append({"kind": "list", "items": caveats})
    ai = _ai_prose(iss, None)
    if ai:
        blocks.append(_p(ai))
    sec = _section(f"issue-{iss.key}", f"Issue {iss.rank}: {title}", blocks, kind="issue")
    sec["slot"] = f"issue:{iss.key}"
    return sec


def _ai_prose(issue, client) -> str:
    """Phase-C hook for grounded AI prose on an issue page; renders nothing in phase B."""
    return ""


def _sec_appendices(S) -> list:
    ctx, o = S["ctx"], S["o"]
    run = ctx.run
    out = []
    # A. declines and data gaps
    rows = []
    for f in S["declined"]:
        m = f.metrics or {}
        why = m.get("reason") or (
            "untrusted input(s): " + ", ".join(m.get("untrusted_roles", []))
            if m.get("untrusted_roles")
            else f.summary
        )
        rows.append([f.rule, f.equip, _cut(why, 160)])
    blocks = []
    if rows:
        blocks.append(_p("Checks that declined to reach a verdict:"))
        blocks.append(_table(["Rule", "Equipment", "Why"], rows))
    miss = []
    for f in S["findings"]:
        mo = (f.metrics or {}).get("_missing_optional")
        if mo:
            miss.append([f.rule, f.equip, ", ".join(mo)])
    if miss:
        blocks.append(_p("Optional inputs that were missing (sub-checks not evaluated):"))
        blocks.append(_table(["Rule", "Equipment", "Missing"], miss))
    cav = []
    for f in S["findings"]:
        for c in getattr(f, "caveats", None) or []:
            if c not in cav:
                cav.append(c)
    if cav:
        blocks.append(_p("Caveats:"))
        blocks.append({"kind": "list", "items": cav})
    seen = {getattr(f, "equip", "") for f in S["findings"]}
    not_eval = [e for e in ctx.equips if e not in seen]
    if not_eval:
        blocks.append(
            _p("Equipment discovered but not evaluated by any rule: " + ", ".join(not_eval))
        )
    if not blocks:
        blocks.append(_p("Nothing was declined."))
    out.append(_section("appendix-a", "Appendix A — Declines and data gaps", blocks, slot=False))

    # B. assumptions actually used
    blocks = [
        _table(
            ["Cost assumption", "Value"],
            [[k, v] for k, v in sorted(S["cost_defaults"].items())],
            css="kv",
        )
    ]
    price = o.price
    if price is not None:
        blocks.append(
            _p(
                f"Energy price: ${price.electricity_per_kwh}/kWh, ${price.gas_per_therm}/therm "
                "(configured)."
            )
        )
    else:
        blocks.append(
            _p("Energy price: package placeholders ($0.15/kWh, $1.20/therm) — set yours.")
        )
    if o.loads:
        blocks.append(_p("Equipment sizing supplied for: " + ", ".join(sorted(o.loads))))
    else:
        blocks.append(_p("No equipment sizing supplied: sizing-dependent issues stay uncosted."))
    rows = [
        [e, S["gate_src"].get(e, FAN_GATE_NONE), S["occ_src"].get(e, "")]
        for e in sorted(S["gate_src"])
    ]
    if rows:
        blocks.append(_table(["Equipment", "Fan gate", "Occupancy"], rows))
    w = S["week"]
    if w is not None and w.candidates:
        wrows = [
            [
                f"{c['start']:%Y-%m-%d}",
                f"{c['coverage']:.2f}",
                c["occupied_days"],
                "yes" if c["eligible"] else "no",
                f"{c['score']:.3f}",
            ]
            for c in w.candidates
        ]
        blocks.append(_p(f"Week-selection scores (mode {w.mode}):"))
        blocks.append(_table(["Week of", "Coverage", "Occupied days", "Eligible", "Score"], wrows))
    out.append(_section("appendix-b", "Appendix B — Assumptions used", blocks, slot=False))

    # C. rules run + config hash
    cfg_hash = hashlib.sha256(
        json.dumps(ctx.config, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]
    blocks = [
        _p(f"Config hash (sha256, first 16): {cfg_hash}"),
        {"kind": "list", "items": list(getattr(run, "rules_run", []) or [])},
    ]
    out.append(_section("appendix-c", "Appendix C — Rules run", blocks, slot=False))
    out.append(
        _section(
            "appendix-d",
            "Appendix D — Fact index",
            [_p("Reserved for the grounded fact index that AI prose will cite (not yet built).")],
            slot=False,
        )
    )
    out.append(
        _section("appendix-e", "Appendix E — Orphaned engineer notes", [_p("None.")], slot=False)
    )
    return out


def _orphan_blocks(orphans: dict) -> list:
    blocks = [
        _p(
            "These notes name a slot this report does not have (an issue that is resolved or "
            "renamed, or a section left out):"
        )
    ]
    for k in sorted(orphans):
        blocks.append({"kind": "note", "slot": k, "notes": orphans[k], "show_slot": True})
    return blocks


# ============================================================================ HTML


_PAPER = {"letter": "letter", "a4": "A4"}
# sections that continue on the current printed page instead of starting a new one
_CONTINUED = frozenset({"appendix-c", "appendix-d", "appendix-e"})

_CSS = """
:root{--ink:#1d2330;--muted:#5b6475;--rule:#d9dde5;--fault:#b3261e;--warn:#9a5b00;--ok:#1e6b3a;
--accent:#0b5a6b;--bg:#ffffff;--band:#f3f5f8}
*{box-sizing:border-box}
body{font-family:system-ui,-apple-system,"Segoe UI",Arial,sans-serif;color:var(--ink);
background:var(--bg);
margin:0 auto;max-width:1040px;padding:16px 24px;line-height:1.45;font-size:14px}
h1{font-size:24px;margin:8px 0 4px}h2{font-size:18px;margin:0 0 10px;padding-bottom:4px;
border-bottom:2px solid var(--accent)}
section{margin:28px 0}
table{border-collapse:collapse;width:100%;font-size:12.5px;margin:8px 0}
th,td{border:1px solid var(--rule);padding:4px 6px;text-align:left;vertical-align:top}
th{background:var(--band)}
table.kv td:first-child{width:28%;color:var(--muted)}
table.kv thead{display:none}
figure{margin:10px 0}figure img{max-width:100%;height:auto;border:1px solid var(--rule)}
figcaption{font-size:12px;color:var(--muted)}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:8px 0 12px}
.kpi{border:1px solid var(--rule);border-radius:6px;padding:8px;background:var(--band)}
.kpi .v{font-size:20px;font-weight:700}.kpi .l{font-size:11.5px;color:var(--muted)}
.banner{border-left:5px solid var(--warn);background:#fff6e5;padding:8px 10px;margin:8px 0}
aside.note{border:1px dashed var(--accent);padding:8px 10px;margin:10px 0;background:#f2f8f9}
aside.note .who{font-size:11.5px;color:var(--muted);font-weight:600}
nav.toc{border:1px solid var(--rule);padding:8px 14px;margin:12px 0;font-size:13px}
nav.toc ol{margin:4px 0;padding-left:20px}
.meta{color:var(--muted)}
table.nc-wrap,table.nc-wrap>tbody,table.nc-wrap>tbody>tr,table.nc-wrap>tbody>tr>td{display:block;
border:0;padding:0;margin:0;width:auto}
table.nc-wrap>thead{display:none}
@media print{
  body{max-width:none;padding:0 3px;font-size:11px}
  nav.toc{display:none}
  section{break-before:page;margin:0}
  section#cover,section.appendix-cont{break-before:auto;margin-top:18px}
  figure,tr,li,.kpi,aside.note,.banner,div.keep{break-inside:avoid}
  figure img{max-height:3.9in;width:auto;max-width:100%}
  section.issue figure img{max-height:2.8in}
  h2,p.lead{break-after:avoid}
  thead{display:table-header-group}
  *{print-color-adjust:exact;-webkit-print-color-adjust:exact}
  .camber-nc-banner{position:fixed;top:0;left:0;right:0;background:#fff;z-index:10;
  margin:0!important;padding:4px 6px!important;font-size:9px}
  table.nc-wrap{display:table;width:100%;border-collapse:collapse;margin:0}
  table.nc-wrap>thead{display:table-header-group}
  table.nc-wrap>tbody{display:table-row-group}
  table.nc-wrap>tbody>tr,table.nc-wrap>thead>tr{display:table-row}
  table.nc-wrap>tbody>tr>td,table.nc-wrap>thead>tr>td{display:table-cell;border:0;padding:0}
  .nc-space{height:0.55in}
  section#summary{max-height:9.2in;overflow:hidden}
}
"""


def _page_css(paper: str, *, nc: bool = False) -> str:
    size = _PAPER.get(str(paper).lower(), "letter")
    top = "0.45in" if nc else "0.6in"  # the NC banner's spacer takes the rest
    return (
        f"@page{{size:{size};margin:{top} 0.55in 0.7in;"
        "@bottom-right{content:'Page ' counter(page) ' of ' counter(pages);font-size:9px;"
        "color:#5b6475}"
        "@bottom-left{content:'CAMBER RCx report';font-size:9px;color:#5b6475}}"
    )


def _esc(x) -> str:
    return _html.escape(str(x), quote=True)


def _note_html(notes: list, *, slot: str = "", show_slot: bool = False) -> str:
    parts = []
    for n in notes:
        who = ", ".join(x for x in (n.get("author", ""), n.get("date", "")) if x)
        head = "Engineer's note" + (f" — {who}" if who else "")
        if show_slot:
            head += f" [{slot}]"
        paras = [p.strip() for p in str(n.get("text", "")).split("\n\n") if p.strip()]
        body = "".join(f"<p>{_esc(p)}</p>" for p in paras)
        parts.append(f"<aside class='note'><div class='who'>{_esc(head)}</div>{body}</aside>")
    return "".join(parts)


def _block_html(b: dict) -> str:
    k = b.get("kind")
    if k == "p":
        return f"<p>{_esc(b['text'])}</p>"
    if k == "banner":
        return f"<div class='banner' role='note'>{_esc(b['text'])}</div>"
    if k == "kpis":
        cells = "".join(
            f"<div class='kpi'><div class='v'>{_esc(i['value'])}</div>"
            f"<div class='l'>{_esc(i['label'])}</div></div>"
            for i in b["items"]
        )
        return f"<div class='kpis'>{cells}</div>"
    if k == "list":
        return "<ul>" + "".join(f"<li>{_esc(i)}</li>" for i in b["items"]) + "</ul>"
    if k == "figure":
        cap = f"<figcaption>{_esc(b['caption'])}</figcaption>" if b.get("caption") else ""
        return f"<figure><img src='{b['src']}' alt='{_esc(b.get('alt', ''))}'>{cap}</figure>"
    if k == "table":
        link = b.get("link_col")
        css = f" class='{_esc(b['css'])}'" if b.get("css") else ""
        head = "".join(f"<th>{_esc(h)}</th>" for h in b["header"])
        rows = []
        for r in b["rows"]:
            tds = []
            for j, c in enumerate(r):
                # the link column holds an anchor this module built from an issue key (hex)
                tds.append(f"<td>{c}</td>" if j == link else f"<td>{_esc(c)}</td>")
            rows.append("<tr>" + "".join(tds) + "</tr>")
        return f"<table{css}><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"
    if k == "note":
        return _note_html(b["notes"], slot=b.get("slot", ""), show_slot=b.get("show_slot", False))
    if k == "html":
        return b["html"]
    return ""


# a lead-in paragraph and the table it introduces stay on one page when the table is this short
_KEEP_ROWS = 6


def _blocks_html(blocks: list) -> list:
    """Render a section's blocks, keeping each lead-in ("...:") paragraph with what it introduces.

    A lead-in and a figure or a short table go in one ``div.keep`` (``break-inside: avoid``);
    before a list or a longer table the lead-in only gets ``break-after: avoid``, so the list or
    table can still flow across pages (a table with its header repeated).
    """
    out, i = [], 0
    while i < len(blocks):
        b = blocks[i]
        nxt = blocks[i + 1] if i + 1 < len(blocks) else None
        lead = b.get("kind") == "p" and str(b.get("text", "")).rstrip().endswith(":")
        if lead and nxt is not None and nxt.get("kind") in ("table", "figure", "list"):
            kind = nxt.get("kind")
            short = kind == "figure" or (kind == "table" and len(nxt.get("rows", [])) <= _KEEP_ROWS)
            para = f"<p class='lead'>{_esc(b['text'])}</p>"
            if short:
                out.append(f"<div class='keep'>{para}{_block_html(nxt)}</div>")
            else:
                out.append(para + _block_html(nxt))
            i += 2
            continue
        out.append(_block_html(b))
        i += 1
    return out


def _render_html(rep: RcxReport) -> str:
    from .audit import data_sources_html

    src = data_sources_html(rep.data_sources)
    nc = "camber-nc-banner" in src
    p0, p1 = rep.period
    period = f"{p0:%Y-%m-%d} to {p1:%Y-%m-%d}" if p0 is not None else "no data"
    toc = "".join(
        f"<li><a href='#{_esc(s['id'])}'>{_esc(s['title'])}</a></li>" for s in rep.sections
    )
    out = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>{_esc(rep.title)}</title>",
        f"<style>{_page_css(rep.options.paper, nc=nc)}{_CSS}</style></head>",
        f"<body class='{'nc' if nc else ''}'>",
    ]
    if nc:
        # The banner leads the body; in print it is position:fixed at the top of every page, and a
        # one-cell wrapper table whose (repeating) header is an empty spacer keeps each page's
        # content clear of it.
        cut = src.index("</div>", src.index("camber-nc-banner")) + len("</div>")
        out.append(src[:cut])
        out.append(
            "<table class='nc-wrap'><thead><tr><td><div class='nc-space'></div></td></tr></thead>"
            "<tbody><tr><td>"
        )
        out.append(src[cut:])
    elif src:
        out.append(src)
    out.append(f"<h1>{_esc(rep.title)}</h1>")
    out.append(
        f"<p class='meta'>{_esc(rep.site)} · data {_esc(period)} · CAMBER {_esc(rep.version)}</p>"
    )
    out.append(f"<nav class='toc'><b>Contents</b><ol>{toc}</ol></nav>")
    for s in rep.sections:
        cls = (
            " class='appendix-cont'"
            if s["id"] in _CONTINUED
            else (" class='issue'" if s.get("kind") == "issue" else "")
        )
        out.append(f"<section id='{_esc(s['id'])}'{cls}><h2>{_esc(s['title'])}</h2>")
        out.extend(_blocks_html(s["blocks"]))
        slot = s.get("slot")
        if slot and slot in rep.notes:
            out.append(_note_html(rep.notes[slot]))
        out.append("</section>")
    if nc:
        out.append("</td></tr></tbody></table>")
    out.append("</body></html>")
    return "\n".join(out)
