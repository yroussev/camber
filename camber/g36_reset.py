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
