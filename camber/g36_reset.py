"""G36 trim-and-respond reset + reset-request generation (clean-room).

Implements the standard control logic from ASHRAE Guideline 36-2021:
* **Trim & Respond (T&R)** setpoint reset (§5.1.14): every cycle the setpoint is
  trimmed a small amount; if enough "requests" arrive it responds in the opposite
  direction (bounded). Used for supply-air-temperature and duct-static reset.
* **OAT-based SAT reset map** (§5.16.2.2): supply-air-temp setpoint slides from
  Min_ClgSAT at high OAT up to a (T&R-reset) maximum at low OAT.
* **Zone reset-request generation** (§5.14.8): the demand-side rules a VAV box
  uses to vote for SAT/static resets.

The algorithm and request rules are control logic (not copyrightable); G36 section
numbers and the table parameters are cited. The point for our tool: compare what a
building's reset *should* be doing (per G36) to what it actually does, turning the
heuristic SAT-reset diagnostic into a deviation-from-G36 measure.

Title 24 note (§5.1.17.3): for California CZ15 the fixed-dry-bulb economizer high
limit is OAT > 75F -- the value used elsewhere in this tool.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

__all__ = [
    "TRParams",
    "SAT_TR",
    "STATIC_TR",
    "tr_step",
    "tr_simulate",
    "oat_sat_setpoint",
    "cooling_sat_requests",
    "static_pressure_requests",
    "SATResetComplianceResult",
    "sat_reset_compliance",
    "ResetEffectivenessResult",
    "reset_effectiveness",
    "RogueZoneCensusResult",
    "rogue_zone_census",
    "CohortStarvationResult",
    "cohort_starvation",
]


@dataclass
class TRParams:
    """Trim-&-Respond parameters (G36 §5.1.14). Signs encode direction:
    SP_trim and SP_res are opposite-signed; SP_res_max bounds one response step."""

    sp0: float  # initial setpoint
    sp_min: float
    sp_max: float
    ignored: int  # I: requests ignored before responding
    sp_trim: float  # trim per cycle (toward the energy-saving direction)
    sp_res: float  # response per request (toward meeting demand)
    sp_res_max: float  # max response magnitude per cycle (same sign as sp_res)


# Default parameter sets from G36 tables (converted to degF / in. w.c.).
SAT_TR = TRParams(
    sp0=65.0, sp_min=55.0, sp_max=65.0, ignored=2, sp_trim=+0.2, sp_res=-0.3, sp_res_max=-1.0
)  # Table 5.16.2.2
STATIC_TR = TRParams(
    sp0=0.5, sp_min=0.1, sp_max=1.5, ignored=2, sp_trim=-0.05, sp_res=+0.06, sp_res_max=+0.13
)  # Table 5.16.1.2


def tr_step(sp: float, requests: int, p: TRParams) -> float:
    """One trim-and-respond cycle: new setpoint from current ``sp`` and request count.

    If effective requests (requests - I) <= 0, trim by sp_trim. Otherwise respond by
    (requests - I) * sp_res, magnitude-capped at sp_res_max. Result clamped to range.
    """
    eff = requests - p.ignored
    if eff <= 0:
        nxt = sp + p.sp_trim
    else:
        resp = eff * p.sp_res
        # cap magnitude at sp_res_max (same sign)
        if p.sp_res > 0:
            resp = min(resp, p.sp_res_max)
        else:
            resp = max(resp, p.sp_res_max)
        nxt = sp + resp
    return float(min(p.sp_max, max(p.sp_min, nxt)))


def tr_simulate(requests, p: TRParams) -> np.ndarray:
    """Run T&R over a sequence of per-cycle request counts; return setpoint series."""
    sp = p.sp0
    out = np.empty(len(requests), dtype=float)
    for i, r in enumerate(requests):
        sp = tr_step(sp, int(r), p)
        out[i] = sp
    return out


def oat_sat_setpoint(oat, *, min_clg_sat=55.0, t_max=65.0, oat_min=60.0, oat_max=70.0):
    """OAT-based SAT setpoint map (G36 §5.16.2.2.b).

    SAT setpoint = min_clg_sat at OAT >= oat_max, rising linearly to ``t_max`` at
    OAT <= oat_min. (``t_max`` is itself T&R-reset between Min/Max_ClgSAT; pass the
    current T&R value, or Max_ClgSAT as a static upper bound.) Vectorized.
    """
    oat = np.asarray(oat, dtype=float)
    frac = (oat_max - oat) / (oat_max - oat_min)
    frac = np.clip(frac, 0.0, 1.0)
    return min_clg_sat + frac * (t_max - min_clg_sat)


# ---- zone reset-request generation (G36 §5.14.8) ----


def cooling_sat_requests(zone_temp, cool_sp, cooling_loop=None, *, hi_f=5.0, mid_f=3.0):
    """SAT reset requests from one zone (§5.14.8.1).

    3 requests if zone temp exceeds cooling setpoint by >= hi_f (5F);
    else 2 if exceeds by >= mid_f (3F); else 1 if cooling-loop > 95%; else 0.
    ``cooling_loop`` (0-100%) optional.
    """
    over = zone_temp - cool_sp
    if over >= hi_f:
        return 3
    if over >= mid_f:
        return 2
    if cooling_loop is not None and cooling_loop > 95:
        return 1
    return 0


def static_pressure_requests(airflow, airflow_sp, damper, *, fan_thr=95.0):
    """Duct-static reset requests from one zone (§5.14.8.2).

    3 if airflow < 50% of setpoint while damper > 95%; else 2 if < 70% while
    damper > 95%; else 1 if damper > 95%; else 0.
    """
    if airflow_sp and airflow_sp > 0:
        ratio = airflow / airflow_sp
        if ratio < 0.50 and damper > fan_thr:
            return 3
        if ratio < 0.70 and damper > fan_thr:
            return 2
    if damper > fan_thr:
        return 1
    return 0


@dataclass
class SATResetComplianceResult:
    """SAT vs the G36 OAT-reset target: how often/much actual SAT runs too cold."""

    equip: str
    n: int
    pct_below_g36_target: float  # % hours actual SAT below the G36 OAT-reset target
    mean_gap_f: float  # mean (G36 target - actual SAT), degF (positive = too cold)
    actual_sat_median: float
    g36_target_median: float
    coverage_start: str
    coverage_end: str

    def as_dict(self):
        """Return the result as a plain dict."""
        from dataclasses import asdict

        return asdict(self)


def sat_reset_compliance(
    df, equip, *, sat_col="SAT", oat_col="OAT", tol_f=1.0, **reset_kwargs
) -> SATResetComplianceResult | None:
    """Compare actual SAT to the G36 OAT-based reset target.

    Flags how often the plant holds SAT colder than G36 would (a reheat/energy
    opportunity). Needs only SAT and OAT -- not zone requests -- so it works on
    typical trend exports. ``reset_kwargs`` pass through to oat_sat_setpoint.
    """
    if sat_col not in df.columns or oat_col not in df.columns:
        return None
    w = df[[sat_col, oat_col]].dropna()
    w = w[(w[sat_col] > 40) & (w[sat_col] < 90)]
    if len(w) < 10:
        return None
    target = oat_sat_setpoint(w[oat_col].values, **reset_kwargs)
    actual = w[sat_col].values
    gap = target - actual  # positive => actual colder than target
    below = gap > tol_f
    return SATResetComplianceResult(
        equip=equip,
        n=int(len(w)),
        pct_below_g36_target=round(100.0 * float(below.mean()), 1),
        mean_gap_f=round(float(gap.mean()), 2),
        actual_sat_median=round(float(np.median(actual)), 1),
        g36_target_median=round(float(np.median(target)), 1),
        coverage_start=str(df.index.min()),
        coverage_end=str(df.index.max()),
    )


@dataclass
class ResetEffectivenessResult:
    """Actual reset setpoint vs the T&R trajectory it should have produced from the requests.

    ``stuck`` is the setpoint barely moving while the request pattern demands movement;
    ``not_responding`` is the setpoint parked at the energy-saving (trim) end while zones are
    calling for the opposite; ``not_trimming`` is the setpoint parked at the demand end while zones
    are idle (wasting energy). ``diverges`` is the setpoint moving the wrong way vs the T&R command
    on most cycles. ``mean_abs_error_sp`` is informational only (a coarser trend cadence than the
    controller inflates it), so the verdict rests on the cadence-robust modes above.
    """

    equip: str
    n: int
    unit: str
    actual_sp_range: float
    expected_sp_range: float
    mean_abs_error_sp: float
    pct_cycles_wrong_direction: float
    pct_high_demand_unresponsive: float | None  # None when too few high-demand cycles to judge
    pct_idle_untrimmed: float | None  # None when too few idle cycles to judge
    stuck: bool
    not_responding: bool | None
    not_trimming: bool | None
    diverges: bool
    effective: bool
    reason: str
    coverage_start: str
    coverage_end: str

    def as_dict(self):
        """Return the result as a plain dict."""
        from dataclasses import asdict

        return asdict(self)


def reset_effectiveness(
    df,
    equip,
    *,
    sp_col,
    requests_col,
    params: TRParams,
    unit: str = "degF",
    min_cycles: int = 12,
    flat_frac: float = 0.10,
    expected_move_frac: float = 0.25,
    pinned_frac: float = 0.15,
    mode_frac: float = 0.60,
    min_mode_cycles: int = 10,
    wrong_dir_frac: float = 0.50,
) -> ResetEffectivenessResult | None:
    """Compare an actual reset setpoint to the G36 Trim-&-Respond trajectory its requests imply.

    Given the per-cycle request count (``requests_col``) and the actual reset setpoint (``sp_col``),
    runs :func:`tr_simulate` to get the setpoint T&R *should* have produced, then scores whether the
    reset is **stuck** (flat while demand moves), **not responding** (parked at the trim end under
    demand), **not trimming** (parked at the demand end while idle), or **diverging** (moving the
    wrong way). Reset-agnostic (SAT in °F with ``SAT_TR`` / static in in. w.c. with ``STATIC_TR``);
    returns ``None`` when a column is unmapped or there are fewer than ``min_cycles`` usable rows.
    """
    if sp_col not in df.columns or requests_col not in df.columns:
        return None
    band = params.sp_max - params.sp_min
    w = df[[sp_col, requests_col]].dropna()
    w = w[(w[sp_col] >= params.sp_min - band) & (w[sp_col] <= params.sp_max + band)]
    if len(w) < min_cycles:
        return None
    actual = w[sp_col].to_numpy(dtype=float)
    req = np.clip(np.round(w[requests_col].to_numpy(dtype=float)), 0, None).astype(int)
    expected = tr_simulate(req, params)

    actual_range = float(np.ptp(actual))
    expected_range = float(np.ptp(expected))
    trim_end = params.sp_max if params.sp_trim > 0 else params.sp_min  # energy-saving end
    demand_end = params.sp_min if params.sp_trim > 0 else params.sp_max  # meeting-demand end
    eff = req - params.ignored  # >0 = a net demand cycle

    # stuck: setpoint barely moves while the request pattern would have moved it
    stuck = actual_range <= flat_frac * band and expected_range >= expected_move_frac * band

    # not responding: under net demand, the setpoint sits at the trim (energy-saving) end
    hi = eff > 0
    pct_unresponsive: float | None = None
    not_responding: bool | None = None
    if int(hi.sum()) >= min_mode_cycles:
        at_trim = np.abs(actual[hi] - trim_end) <= pinned_frac * band
        unresp = round(100.0 * float(at_trim.mean()), 1)
        pct_unresponsive = unresp
        not_responding = unresp >= 100.0 * mode_frac

    # not trimming: while idle, the setpoint sits at the demand end (wasting energy)
    idle = eff <= 0
    pct_untrimmed: float | None = None
    not_trimming: bool | None = None
    if int(idle.sum()) >= min_mode_cycles:
        at_demand = np.abs(actual[idle] - demand_end) <= pinned_frac * band
        untrim = round(100.0 * float(at_demand.mean()), 1)
        pct_untrimmed = untrim
        not_trimming = untrim >= 100.0 * mode_frac

    # diverges: on cycles where T&R commanded a real move, the actual moved the opposite way
    exp_move = np.diff(expected)
    act_move = np.diff(actual)
    moved = np.abs(exp_move) > 1e-9
    if int(moved.sum()) > 0:
        pct_wrong = round(100.0 * float((act_move[moved] * exp_move[moved] < 0).mean()), 1)
    else:
        pct_wrong = 0.0
    diverges = pct_wrong >= 100.0 * wrong_dir_frac

    effective = not (stuck or not_responding is True or not_trimming is True or diverges)
    if stuck:
        reason = "stuck"
    elif not_responding is True:
        reason = "not_responding"
    elif not_trimming is True:
        reason = "not_trimming"
    elif diverges:
        reason = "diverges"
    else:
        reason = "effective"

    return ResetEffectivenessResult(
        equip=equip,
        n=int(len(w)),
        unit=unit,
        actual_sp_range=round(actual_range, 3),
        expected_sp_range=round(expected_range, 3),
        mean_abs_error_sp=round(float(np.mean(np.abs(actual - expected))), 3),
        pct_cycles_wrong_direction=pct_wrong,
        pct_high_demand_unresponsive=pct_unresponsive,
        pct_idle_untrimmed=pct_untrimmed,
        stuck=bool(stuck),
        not_responding=not_responding,
        not_trimming=not_trimming,
        diverges=bool(diverges),
        effective=bool(effective),
        reason=reason,
        coverage_start=str(df.index.min()),
        coverage_end=str(df.index.max()),
    )


@dataclass
class RogueZoneCensusResult:
    """Which zone(s) monopolize an air handler's reset requests and drag the whole reset.

    In G36 the SAT / duct-static reset responds to the high-percentile of per-zone *requests*
    (§5.14.8), so one chronically over-demanding zone can hold the binding constraint and force a
    colder / higher setpoint than the rest of the fleet needs. A zone is a **rogue** when it both
    holds the binding (maximum) request a dominant fraction of the active cycles **and** commands a
    disproportionate share of the group's total requests. Without a zone->AHU topology the zones are
    pooled building-wide (``grouped=False``) and this is a screening signal only -- see ``caveats``.
    """

    reset: str
    grouped: bool
    n_zones_evaluated: int
    n_groups: int
    total_requests: int
    zone_request_rate: dict
    zone_request_share: dict
    zone_binding_frac: dict
    rogues: list
    rogue_by_group: dict
    worst_zone: str | None
    worst_zone_share: float | None
    unevaluable_zones: list
    caveats: list
    coverage_start: str
    coverage_end: str

    def as_dict(self):
        """Return the result as a plain dict."""
        from dataclasses import asdict

        return asdict(self)


def _group_of(groups, zone: str) -> str:
    """Resolve a zone's group key: a callable, a {zone: group} dict, or the single pool."""
    if groups is None:
        return "<fleet>"
    if callable(groups):
        return str(groups(zone))
    return str(groups.get(zone, "<ungrouped>"))


def _sat_requests_series(frame, temp_col, cool_sp_col, *, hi_f, mid_f):
    """Vectorized cooling SAT-reset requests for one zone (G36 §5.14.8.1, tiers 3/2/0).

    The scalar's tier-1 (cooling-loop > 95%) is omitted -- there is no per-zone cooling-loop role --
    so a marginal caller below +``mid_f`` is under-counted by at most one request; it cannot
    manufacture a rogue (dominance rests on the +``mid_f`` / +``hi_f`` tiers).
    """
    w = frame[[temp_col, cool_sp_col]].dropna()
    over = (w[temp_col] - w[cool_sp_col]).to_numpy(dtype=float)
    req = np.where(over >= hi_f, 3.0, np.where(over >= mid_f, 2.0, 0.0))
    return pd.Series(req, index=w.index)


def _static_requests_series(frame, flow_col, flow_sp_col, damper_col, *, fan_thr):
    """Vectorized duct-static-reset requests for one zone (G36 §5.14.8.2, tiers 3/2/1/0)."""
    w = frame[[flow_col, flow_sp_col, damper_col]].dropna()
    flow = w[flow_col].to_numpy(dtype=float)
    sp = w[flow_sp_col].to_numpy(dtype=float)
    damper = w[damper_col].to_numpy(dtype=float)
    ratio = np.divide(
        flow, sp, out=np.full(flow.shape, np.inf), where=sp > 0
    )  # sp<=0 -> never <thr
    hot = damper > fan_thr
    req = np.where(
        hot & (ratio < 0.50),
        3.0,
        np.where(hot & (ratio < 0.70), 2.0, np.where(hot, 1.0, 0.0)),
    )
    return pd.Series(req, index=w.index)


def _build_request_series(
    frames,
    *,
    reset,
    temp_col,
    cool_sp_col,
    flow_col,
    flow_sp_col,
    damper_col,
    hi_f,
    mid_f,
    fan_thr,
    min_active_cycles,
):
    """Per-zone reset-request series for a fleet -> ``(series, unevaluable, starts, ends)``.

    Shared by :func:`rogue_zone_census` and :func:`cohort_starvation`. A zone with no frame, missing
    role columns, or fewer than ``min_active_cycles`` usable rows is added to ``unevaluable`` and
    skipped; duplicate timestamps are deduped (keep last).
    """
    series: dict = {}
    unevaluable: list = []
    starts, ends = [], []
    for zone, frame in frames.items():
        if frame is None or getattr(frame, "empty", True):
            unevaluable.append(zone)
            continue
        if reset == "sat":
            have = temp_col in frame.columns and cool_sp_col in frame.columns
            s = (
                _sat_requests_series(frame, temp_col, cool_sp_col, hi_f=hi_f, mid_f=mid_f)
                if have
                else None
            )
        else:
            have = all(c in frame.columns for c in (flow_col, flow_sp_col, damper_col))
            s = (
                _static_requests_series(frame, flow_col, flow_sp_col, damper_col, fan_thr=fan_thr)
                if have
                else None
            )
        if s is None:
            unevaluable.append(zone)
            continue
        s = s[~s.index.duplicated(keep="last")]
        if len(s) < min_active_cycles:
            unevaluable.append(zone)
            continue
        series[zone] = s
        starts.append(s.index.min())
        ends.append(s.index.max())
    return series, unevaluable, starts, ends


def rogue_zone_census(
    frames,
    *,
    reset: str = "sat",
    groups=None,
    temp_col=None,
    cool_sp_col=None,
    flow_col=None,
    flow_sp_col=None,
    damper_col=None,
    dominance_frac: float = 0.50,
    share_mult: float = 2.0,
    min_share: float = 0.30,
    min_active_cycles: int = 10,
    min_zones_per_group: int = 2,
    hi_f: float = 5.0,
    mid_f: float = 3.0,
    fan_thr: float = 95.0,
) -> RogueZoneCensusResult | None:
    """Find the zone(s) monopolizing a G36 reset across a fleet of terminal zones.

    Given ``frames`` = ``{zone: role-frame}``, computes each zone's per-cycle reset-request series
    (SAT via temp/cool-sp when ``reset="sat"``, duct-static via flow/flow-sp/damper when
    ``reset="static"``), pools zones by ``groups`` (a ``{zone: group}`` dict, a ``zone->group``
    callable, or ``None`` = one building-wide ``<fleet>`` pool), and per group scores each zone by
    its share of the group's total requests and the fraction of *active* cycles it holds the binding
    (maximum) request. A zone is a **rogue** when ``zone_binding_frac >= dominance_frac`` and
    ``zone_request_share >= max(min_share, share_mult / n_zones)``. Returns ``None`` only for an
    empty fleet; otherwise a :class:`RogueZoneCensusResult` (possibly with no rogues, only caveats).
    Screening / opportunity-grade thresholds (provisional-untuned).
    """
    if reset not in ("sat", "static"):
        raise ValueError(f"reset must be 'sat' or 'static', got {reset!r}")
    if not frames:
        return None

    series, unevaluable, starts, ends = _build_request_series(
        frames,
        reset=reset,
        temp_col=temp_col,
        cool_sp_col=cool_sp_col,
        flow_col=flow_col,
        flow_sp_col=flow_sp_col,
        damper_col=damper_col,
        hi_f=hi_f,
        mid_f=mid_f,
        fan_thr=fan_thr,
        min_active_cycles=min_active_cycles,
    )

    zone_rate: dict = {}
    zone_share: dict = {}
    zone_binding: dict = {}
    rogue_by_group: dict = {}
    collapsed: list = []
    total_requests = 0.0

    groups_map: dict = {}
    for zone in series:
        groups_map.setdefault(_group_of(groups, zone), []).append(zone)

    for g, zones in groups_map.items():
        R = pd.concat({z: series[z] for z in zones}, axis=1)
        arr = R.to_numpy(dtype=float)
        keep = ~np.all(np.isnan(arr), axis=1)
        arr = arr[keep]
        grp_total = float(np.nansum(arr)) if arr.size else 0.0
        total_requests += grp_total
        if arr.size:
            row_max = np.nanmax(arr, axis=1)
            active = row_max > 0
            n_active = int(active.sum())
        else:
            row_max = np.empty(0)
            active = np.empty(0, dtype=bool)
            n_active = 0
        for zi, z in enumerate(R.columns):
            col = arr[:, zi] if arr.size else np.empty(0)
            zone_rate[z] = (
                round(float(np.nanmean(col)), 3) if col.size and not np.all(np.isnan(col)) else 0.0
            )
            zone_share[z] = round(float(np.nansum(col) / grp_total), 3) if grp_total > 0 else 0.0
            if n_active > 0:
                is_max = active & (col == row_max)  # NaN never equals row_max -> excluded
                zone_binding[z] = round(int(is_max.sum()) / n_active, 3)
            else:
                zone_binding[z] = 0.0
        # a group needs >= min_zones_per_group real zones to attribute a rogue
        if len(zones) < min_zones_per_group or grp_total <= 0:
            collapsed.append(g)
            continue
        share_thr = max(min_share, share_mult / len(zones))
        rg = sorted(
            z for z in zones if zone_binding[z] >= dominance_frac and zone_share[z] >= share_thr
        )
        if rg:
            rogue_by_group[g] = rg

    rogues = sorted({z for zs in rogue_by_group.values() for z in zs})
    worst = max(rogues, key=lambda z: (zone_binding[z], zone_share[z])) if rogues else None

    caveats: list = []
    if reset == "sat":
        caveats.append(
            "SAT request tier-1 (zone cooling-loop > 95%) not evaluated -- no per-zone "
            "cooling-loop signal; census may under-count marginal callers"
        )
    if total_requests <= 0 and series:
        caveats.append(
            "no zone generated any reset request in the window -- the reset is not demand-bound, "
            "so no zone can be dragging it"
        )
    if unevaluable:
        caveats.append(
            f"{len(unevaluable)} zone(s) not evaluated (missing request signals or too few rows): "
            f"census may under-count"
        )
    collapsed_real = [g for g in collapsed if len(groups_map.get(g, [])) < min_zones_per_group]
    if collapsed_real:
        caveats.append(
            f"{len(collapsed_real)} group(s) had too few zones to attribute a rogue "
            f"(need >= {min_zones_per_group})"
        )

    return RogueZoneCensusResult(
        reset=reset,
        grouped=groups is not None,
        n_zones_evaluated=len(series),
        n_groups=len(groups_map),
        total_requests=int(round(total_requests)),
        zone_request_rate=zone_rate,
        zone_request_share=zone_share,
        zone_binding_frac=zone_binding,
        rogues=rogues,
        rogue_by_group=rogue_by_group,
        worst_zone=worst,
        worst_zone_share=(zone_share[worst] if worst else None),
        unevaluable_zones=sorted(map(str, unevaluable)),
        caveats=caveats,
        coverage_start=str(min(starts)) if starts else "",
        coverage_end=str(max(ends)) if ends else "",
    )


@dataclass
class CohortStarvationResult:
    """Whether whole *cohorts* of an air handler's zones are demand-starved at once.

    The common-mode twin of :class:`RogueZoneCensusResult`: where a rogue is one zone dominating the
    requests, a **starved cohort** is most/all of an AHU's zones requesting the reset simultaneously
    -- a pattern that points at one upstream fault (duct-static setpoint capped, supply fan maxed, a
    restricted upstream damper) rather than N independent zone faults. ``group_sustained_frac`` is
    the fraction of active cycles on which >= ``cohort_frac`` of a group's zones request at once.
    """

    reset: str
    grouped: bool
    n_zones_evaluated: int
    n_groups: int
    total_requests: int
    group_sustained_frac: dict
    group_zone_count: dict
    group_active_cycles: dict
    starved_groups: list
    starved_detail: dict
    worst_group: str | None
    worst_group_frac: float | None
    unevaluable_zones: list
    caveats: list
    coverage_start: str
    coverage_end: str

    def as_dict(self):
        """Return the result as a plain dict."""
        from dataclasses import asdict

        return asdict(self)


def cohort_starvation(
    frames,
    *,
    reset: str = "static",
    groups=None,
    temp_col=None,
    cool_sp_col=None,
    flow_col=None,
    flow_sp_col=None,
    damper_col=None,
    cohort_frac: float = 0.75,
    sustained_frac: float = 0.50,
    min_active_cycles: int = 10,
    min_zones_per_group: int = 3,
    hi_f: float = 5.0,
    mid_f: float = 3.0,
    fan_thr: float = 95.0,
) -> CohortStarvationResult | None:
    """Find air handlers whose whole zone cohort is demand-starved at once (an upstream fault).

    Given ``frames`` = ``{zone: role-frame}``, computes each zone's per-cycle reset-request series
    (``reset="static"`` via flow/flow-sp/damper -- primary -- or ``"sat"`` via temp/cool-sp),
    pools zones by ``groups`` (``{zone: group}`` dict / ``zone->group`` callable / ``None`` = one
    building-wide pool), and per group measures ``group_sustained_frac`` -- the fraction of *active*
    cycles on which at least ``cohort_frac`` of the group's zones request at once. A group is
    **starved** when it has >= ``min_zones_per_group`` zones, >= ``min_active_cycles`` active rows,
    and ``group_sustained_frac >= sustained_frac``. This is the opposite shape to
    :func:`rogue_zone_census` (a lone dominant zone never reaches ``cohort_frac``; a starved cohort
    shares requests too evenly to be a rogue). Returns ``None`` only for an empty fleet. Screening /
    opportunity-grade thresholds (provisional-untuned).
    """
    if reset not in ("sat", "static"):
        raise ValueError(f"reset must be 'sat' or 'static', got {reset!r}")
    if not frames:
        return None

    series, unevaluable, starts, ends = _build_request_series(
        frames,
        reset=reset,
        temp_col=temp_col,
        cool_sp_col=cool_sp_col,
        flow_col=flow_col,
        flow_sp_col=flow_sp_col,
        damper_col=damper_col,
        hi_f=hi_f,
        mid_f=mid_f,
        fan_thr=fan_thr,
        min_active_cycles=min_active_cycles,
    )

    groups_map: dict = {}
    for zone in series:
        groups_map.setdefault(_group_of(groups, zone), []).append(zone)

    group_sustained: dict = {}
    group_count: dict = {}
    group_active: dict = {}
    starved_detail: dict = {}
    starved: list = []
    collapsed: list = []
    total_requests = 0.0

    for g, zones in groups_map.items():
        R = pd.concat({z: series[z] for z in zones}, axis=1)
        arr = R.to_numpy(dtype=float)
        keep = ~np.all(np.isnan(arr), axis=1)
        arr = arr[keep]
        total_requests += float(np.nansum(arr)) if arr.size else 0.0
        group_count[g] = len(zones)
        if arr.size:
            present = np.sum(~np.isnan(arr), axis=1)  # zones with data this cycle
            requesting = np.nansum(arr >= 1, axis=1)  # NaN >= 1 is False -> excluded
            row_max = np.nanmax(arr, axis=1)
            active = (present > 0) & (row_max > 0)
            n_active = int(active.sum())
            if n_active > 0:
                frac = np.divide(
                    requesting,
                    present,
                    out=np.zeros_like(requesting, dtype=float),
                    where=present > 0,
                )
                cohort_demand = active & (frac >= cohort_frac)
                sustained = int(cohort_demand.sum()) / n_active
            else:
                sustained = 0.0
        else:
            n_active = 0
            sustained = 0.0
        group_active[g] = n_active
        group_sustained[g] = round(sustained, 3)
        if len(zones) < min_zones_per_group:
            collapsed.append(g)
            continue
        if n_active >= min_active_cycles and sustained >= sustained_frac:
            starved.append(g)
            starved_detail[g] = {
                "n_zones": len(zones),
                "sustained_frac": round(sustained, 3),
                "n_active": n_active,
            }

    starved = sorted(starved)
    worst = max(starved, key=lambda g: group_sustained[g]) if starved else None

    caveats: list = []
    if reset == "sat":
        caveats.append(
            "SAT-side cohort demand can reflect a genuinely hot outdoor condition (design-day) "
            "rather than a capacity/reset fault; corroborate with OAT / supply_air_reset_compliance"
        )
    if total_requests <= 0 and series:
        caveats.append(
            "no zone generated any reset request in the window -- the reset is not demand-bound, "
            "so no cohort can be starved"
        )
    if unevaluable:
        caveats.append(
            f"{len(unevaluable)} zone(s) not evaluated (missing request signals or too few rows): "
            f"census may under-count"
        )
    collapsed_real = [g for g in collapsed if len(groups_map.get(g, [])) < min_zones_per_group]
    if collapsed_real:
        caveats.append(
            f"{len(collapsed_real)} group(s) had too few zones to judge a cohort "
            f"(need >= {min_zones_per_group})"
        )

    return CohortStarvationResult(
        reset=reset,
        grouped=groups is not None,
        n_zones_evaluated=len(series),
        n_groups=len(groups_map),
        total_requests=int(round(total_requests)),
        group_sustained_frac=group_sustained,
        group_zone_count=group_count,
        group_active_cycles=group_active,
        starved_groups=starved,
        starved_detail=starved_detail,
        worst_group=worst,
        worst_group_frac=(group_sustained[worst] if worst else None),
        unevaluable_zones=sorted(map(str, unevaluable)),
        caveats=caveats,
        coverage_start=str(min(starts)) if starts else "",
        coverage_end=str(max(ends)) if ends else "",
    )
