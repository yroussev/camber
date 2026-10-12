"""CalTRACK 2.0 Daily and Billing methods (0.105, #156).

A faithful implementation of the CalTRACK 2.0 daily and billing methods: the data-sufficiency
rules, the usage-per-day and temperature preparation, the balance-point grid search over
intercept-only, HDD-only, CDD-only and HDD+CDD candidates with CalTRACK's qualification tests,
adjusted-R^2 selection with eemeter's tie order, billing weighted least squares, prediction,
avoided energy use, the CalTRACK metrics and the CalTRACK fractional savings uncertainty.

Every rule cites its id in the CAMBER spec (``CT-D-*`` for daily/billing, ``CT-C-*`` common) and
every constant lives in :mod:`camber.mandv._caltrack` with its source in the CalTRACK documents
(``M``, ``A``) or eemeter 2.10.0 (``E``, the reference implementation, Apache-2.0).

**Defaults follow the published CalTRACK 2.0 documents.** ``eemeter_compat=True`` reproduces
eemeter 2.10 wherever the two disagree (the spec's [DIFF] rules), for cross-checks:

=====================  ==================================  =====================================
rule                   default (CalTRACK documents)        ``eemeter_compat=True`` (eemeter 2.10)
=====================  ==================================  =====================================
CT-D-1.2 missing days  at most 37 of 365                   valid / 365 >= 0.9 (at most 36)
CT-D-1.5 negatives     flagged, sufficiency passes          sufficiency fails
CT-D-2.1 day usage     kept at >= 50 % interval coverage   kept at > 50 %
CT-D-3.1 estimates     combined first, 70-day cap          length filter first, no cap
CT-D-4.1 day mean T    >= 50 % of the day's hours          >= 1 hour (fit), > 90 % (sufficiency);
                                                           billing: > 12 hours
CT-D-4.2 T gaps        <= 6 h interpolated                 not interpolated
CT-D-5.2 grid step     3 degF (30, 33, ..., 90)            1 degF (30, 31, ..., 90)
=====================  ==================================  =====================================

Where the spec chose eemeter's reading for both modes it says so: the degree-day mean is over the
days with a valid temperature (CT-D-4.4), the billing weight is those days (CT-D-6.5) and a
coefficient of exactly 0 passes the sign test (CT-D-7.2).

Inputs follow CAMBER's conventions: naive timestamps are the site's wall clock, ``timezone``
names it (so a daylight-saving day has 23 or 25 hours), temperatures are degF and screened to
the plausible outdoor range first (:func:`camber.mandv._temperature.screen_temperature`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from . import _caltrack as K
from ._caltrack import CalTRACKResult, ProjectionView, caltrack_fsu, caltrack_metrics, wls
from ._temperature import screen_temperature, screened_caveat

__all__ = [
    "MODEL_TYPES",
    "CalTRACKBillingModel",
    "CalTRACKCandidate",
    "CalTRACKDailyModel",
    "balance_point_grid",
    "billing_temperature",
    "caltrack_billing_savings",
    "caltrack_daily_savings",
    "clean_billing_periods",
    "daily_temperature",
    "daily_usage",
    "degree_day_sufficient",
    "fit_caltrack_billing",
    "fit_caltrack_daily",
    "select_candidate",
]

# candidate statuses (CT-D-7)
NOT_ATTEMPTED, ERROR, DISQUALIFIED, QUALIFIED = (
    "NOT ATTEMPTED",
    "ERROR",
    "DISQUALIFIED",
    "QUALIFIED",
)
MODEL_TYPES = ("intercept_only", "hdd_only", "cdd_only", "cdd_hdd")


# =========================================================================== data preparation
def _dedupe_or_raise(s: pd.Series, timezone: str | None, what: str) -> pd.Series:
    """Duplicate timestamps (E18): a fall-back hour in ``timezone`` is averaged (two readings of
    the repeated hour); any other duplicate may be two meters in one series and raises."""
    idx = pd.DatetimeIndex(s.index)
    if not idx.has_duplicates:
        return s
    dup = idx[idx.duplicated()].unique()
    from .intervalfit import repeated_hour_weights

    w = repeated_hour_weights(dup, timezone)
    if w is None or not np.all(w == 2.0):
        raise ValueError(
            f"{what} has {len(dup)} duplicate timestamp(s) outside a daylight-saving fall-back "
            "hour: possibly more than one meter in one series (CalTRACK M#L198); pass the site's "
            "timezone= if they are the repeated hour"
        )
    return s.groupby(level=0).mean()


def daily_usage(
    energy: pd.Series,
    *,
    fuel: str = "electricity",
    rate_is_energy_rate: bool = False,
    timezone: str | None = None,
    eemeter_compat: bool = False,
) -> pd.DataFrame:
    """Usage per local-midnight day from interval (or daily) meter data (CT-D-2.1, CT-D-1.3).

    A day whose present intervals cover at least half of it is kept and its missing intervals are
    filled with the mean of the present ones, so its total is ``sum / coverage`` (``coverage`` the
    covered share of the day, :func:`camber.mandv.intervalfit.day_coverage`, which counts a
    daylight-saving day's own 23 or 25 hours with ``timezone``). eemeter keeps a day only above
    half (``eemeter_compat``). For electricity a reading of exactly 0 is missing (CT-D-1.3). Daily
    input is used as it is. Returns columns ``usage`` and ``coverage`` indexed by day;
    ``attrs`` count ``n_zero_masked``, ``n_negative`` and ``n_days_dropped`` (partial days).
    """
    K.check_fuel(fuel)
    from ..timegrid import interval_hours
    from .intervalfit import _energy_sum, day_coverage, rate_to_energy

    v, n_zero, n_neg = K.mask_meter_values(energy, fuel)
    v = _dedupe_or_raise(v.sort_index(), timezone, "the meter series")
    present = v.dropna()
    out = pd.DataFrame(columns=["usage", "coverage"], dtype=float)
    if not present.empty:
        step = interval_hours(present.index)
        if step >= 24.0 - 1e-9:
            usage = present.groupby(pd.DatetimeIndex(present.index).normalize()).sum()
            out = pd.DataFrame({"usage": usage, "coverage": 1.0})
        else:
            if rate_is_energy_rate:
                total = rate_to_energy(present, "D", timezone=timezone)
            else:
                total = _energy_sum(present, "D", timezone)
            cov = day_coverage(present, timezone=timezone).reindex(total.index)
            frame = pd.DataFrame({"total": total, "coverage": cov}).dropna()
            thr = K.DAY_USAGE_MIN_COVERAGE
            keep = (
                frame["coverage"] > thr + 1e-9
                if eemeter_compat
                else frame["coverage"] >= thr - 1e-9
            )
            dropped = int((~keep).sum())
            frame = frame[keep]
            out = pd.DataFrame(
                {"usage": frame["total"] / frame["coverage"], "coverage": frame["coverage"]}
            )
            out.attrs["n_days_dropped"] = dropped
    out.index.name = "day"
    out.attrs.setdefault("n_days_dropped", 0)
    out.attrs["n_zero_masked"] = n_zero
    out.attrs["n_negative"] = n_neg
    return out


def _hourly_temps(temp: pd.Series, *, timezone, eemeter_compat, temp_bounds):
    """Screened temperature on an hourly grid (``None`` for daily-or-coarser input), plus counts."""
    from ..timegrid import interval_hours

    t, n_screened = screen_temperature(
        pd.to_numeric(temp, errors="coerce").astype(float), temp_bounds
    )
    t = _dedupe_or_raise(t.sort_index(), timezone, "the temperature series")
    present = t.dropna()
    if present.empty:
        return None, t, n_screened, 0
    if interval_hours(present.index) >= 24.0 - 1e-9:
        return None, t, n_screened, 0
    hourly, n_filled = K.hourly_temperature(t, interpolate=not eemeter_compat)
    return hourly, t, n_screened, n_filled


def daily_temperature(
    temp: pd.Series,
    *,
    timezone: str | None = None,
    eemeter_compat: bool = False,
    temp_bounds: tuple | None = None,
) -> pd.DataFrame:
    """Daily mean temperature with its validity (CT-D-4.1, CT-D-4.2, CT-D-4.3).

    From hourly means (a sub-hourly series is spread to minutes first, CT-D-4.3; gaps of up to six
    hours are interpolated, CT-D-4.2): a day's mean is valid when no more than half of its hours
    are missing (``n_hours >= hours_in_day / 2``; ``hours_in_day`` is 23 or 25 on a daylight-saving
    day with ``timezone``). Under ``eemeter_compat`` no interpolation is done, the mean is taken
    over any hour present (eemeter's daily design matrix) and ``valid_sufficiency`` is eemeter's
    stricter ``n_hours / hours_in_day > 0.9``. A daily series is taken as the days' means.

    Columns: ``temp`` (NaN when not valid), ``n_hours``, ``hours_in_day``, ``valid_sufficiency``.
    ``attrs``: ``n_temp_screened``, ``n_temp_interpolated``.
    """
    from .intervalfit import day_hours

    hourly, t, n_screened, n_filled = _hourly_temps(
        temp, timezone=timezone, eemeter_compat=eemeter_compat, temp_bounds=temp_bounds
    )
    if hourly is None:
        d = t.dropna()
        d = d.groupby(pd.DatetimeIndex(d.index).normalize()).mean()
        hrs = day_hours(d.index, timezone)
        out = pd.DataFrame(
            {"temp": d, "n_hours": hrs, "hours_in_day": hrs, "valid_sufficiency": True}
        )
    else:
        g = hourly.resample("D")
        n_hours = g.count().astype(float)
        mean = g.mean()
        hrs = day_hours(n_hours.index, timezone)
        if eemeter_compat:
            ok = n_hours >= 1
            suff = (n_hours / hrs) > K.EEMETER_TEMP_SUFFICIENCY_FRACTION
        else:
            ok = n_hours >= K.DAY_TEMP_MIN_FRACTION * hrs - 1e-9
            suff = ok
        out = pd.DataFrame(
            {
                "temp": mean.where(ok),
                "n_hours": n_hours,
                "hours_in_day": hrs,
                "valid_sufficiency": suff & ok,
            }
        )
    out.index.name = "day"
    out.attrs["n_temp_screened"] = n_screened
    out.attrs["n_temp_interpolated"] = n_filled
    return out


# --------------------------------------------------------------------------- billing periods
def _billing_frame(bills) -> pd.DataFrame:
    """Bills as a frame of ``start``, ``end`` (exclusive), ``days``, ``energy``, ``estimated``."""
    from .billing import BillingSeries

    if isinstance(bills, BillingSeries):
        f = bills.frame
    elif isinstance(bills, pd.DataFrame):
        f = BillingSeries(bills.reset_index(drop=True)).frame
    else:
        raise TypeError(
            "billing input must be a BillingSeries or a DataFrame with start, end (exclusive) and "
            "energy columns (and optionally estimated)"
        )
    f = f[["start", "end", "days", "energy", "estimated"]].reset_index(drop=True).copy()
    return f


def _combine_estimated(f: pd.DataFrame, cap: int | None) -> tuple:
    """Each estimated bill folded into the next actual bill (CT-D-3.1, eemeter's look-ahead).

    An estimated bill followed, contiguously, by an actual read is merged into it (one period, the
    summed energy). An estimated bill followed by another estimate, or by nothing, is missing
    (CT-D-1.4): its energy becomes NaN. eemeter's docstring example is reproduced exactly
    (values ``[2,3,5,4,6,3,4]``, estimated ``[F,F,T,F,T,T,F]`` -> ``2, 3, 9, NaN, 7``). With
    ``cap`` a merged period longer than ``cap`` days is dropped. Returns ``(frame, notes)``.
    """
    rows = f.to_dict("records")
    out: list = []
    notes = {"merged": 0, "estimated_missing": 0, "over_cap": 0}
    i = 0
    while i < len(rows):
        r = dict(rows[i])
        if r["estimated"]:
            nxt = rows[i + 1] if i + 1 < len(rows) else None
            if (
                nxt is not None
                and not nxt["estimated"]
                and pd.Timestamp(nxt["start"]) == pd.Timestamp(r["end"])
                and np.isfinite(nxt["energy"])
            ):
                merged = dict(nxt)
                merged["start"] = r["start"]
                merged["energy"] = float(r["energy"]) + float(nxt["energy"])
                merged["days"] = int(r["days"]) + int(nxt["days"])
                merged["estimated"] = False
                notes["merged"] += 1
                if cap is not None and merged["days"] > cap:
                    notes["over_cap"] += 1
                else:
                    out.append(merged)
                i += 2
                continue
            r["energy"] = float("nan")
            notes["estimated_missing"] += 1
        out.append(r)
        i += 1
    g = pd.DataFrame(out, columns=f.columns)
    return g, notes


def _cycle(f: pd.DataFrame, billing_cycle: str | None) -> str:
    if billing_cycle is not None:
        if billing_cycle not in ("monthly", "bimonthly"):
            raise ValueError("billing_cycle must be 'monthly', 'bimonthly' or None")
        return billing_cycle
    med = float(np.median(f["days"])) if len(f) else 30.0
    return "bimonthly" if med > 45 else "monthly"


def _length_ok(days, cycle: str) -> np.ndarray:
    hi = K.MONTHLY_MAX_DAYS if cycle == "monthly" else K.BIMONTHLY_MAX_DAYS
    d = np.asarray(days, dtype=float)
    return (d >= K.OFF_CYCLE_MIN_DAYS) & (d <= hi)


def clean_billing_periods(
    bills,
    *,
    period: str = "baseline",
    billing_cycle: str | None = None,
    eemeter_compat: bool = False,
) -> pd.DataFrame:
    """Bills cleaned by the CalTRACK billing rules (CT-D-3.1, CT-D-3.2, CT-D-3.4, CT-D-1.4).

    **Baseline** (``period="baseline"``): estimated reads are combined with the next period
    (:func:`_combine_estimated`) and a combined period over 70 days is dropped; then bills shorter
    than 25 days (off-cycle) and monthly bills over 35 days (bi-monthly over 70) are dropped, both
    ends inclusive. ``eemeter_compat`` filters lengths first, on the original bills, and does not
    cap the combined period (eemeter 2.10). ``billing_cycle`` is ``"monthly"``, ``"bimonthly"``
    or ``None`` (from the median bill length: over 45 days is bi-monthly).

    **Reporting** (``period="reporting"``): estimated reads are combined the same way; an off-cycle
    bill under 25 days is combined with the next one while the result stays within 70 days; long
    bills are kept and flagged (``flag_long``) rather than dropped.

    Returns the cleaned frame (``start``, ``end``, ``days``, ``energy``, ``estimated``, and for
    reporting ``flag_long``); ``attrs`` count what each rule did.
    """
    f = _billing_frame(bills)
    cycle = _cycle(f, billing_cycle)
    notes: dict[str, Any] = {"billing_cycle": cycle}
    if period == "baseline":
        if eemeter_compat:
            ok = _length_ok(f["days"], cycle)
            notes["n_length_dropped"] = int((~ok).sum())
            f.loc[~ok, "energy"] = np.nan
            f, n2 = _combine_estimated(f, cap=None)
            notes.update(n2)
        else:
            f, n2 = _combine_estimated(f, cap=K.ESTIMATED_COMBINE_MAX_DAYS)
            notes.update(n2)
            ok = _length_ok(f["days"], cycle)
            notes["n_length_dropped"] = int((~ok).sum())
            f = f[ok]
    elif period == "reporting":
        f, n2 = _combine_estimated(f, cap=None)
        notes.update(n2)
        rows = f.to_dict("records")
        out: list = []
        n_off = 0
        i = 0
        while i < len(rows):
            r = dict(rows[i])
            while (
                r["days"] < K.OFF_CYCLE_MIN_DAYS
                and i + 1 < len(rows)
                and pd.Timestamp(rows[i + 1]["start"]) == pd.Timestamp(r["end"])
                and r["days"] + rows[i + 1]["days"] <= K.ESTIMATED_COMBINE_MAX_DAYS
            ):
                nxt = rows[i + 1]
                r = {
                    "start": r["start"],
                    "end": nxt["end"],
                    "days": int(r["days"]) + int(nxt["days"]),
                    "energy": float(r["energy"]) + float(nxt["energy"]),
                    "estimated": bool(nxt["estimated"]),
                }
                n_off += 1
                i += 1
            out.append(r)
            i += 1
        f = pd.DataFrame(out, columns=["start", "end", "days", "energy", "estimated"])
        hi = K.MONTHLY_MAX_DAYS if cycle == "monthly" else K.BIMONTHLY_MAX_DAYS
        f["flag_long"] = f["days"] > hi
        notes["n_off_cycle_combined"] = n_off
        notes["n_flag_long"] = int(f["flag_long"].sum())
    else:
        raise ValueError("period must be 'baseline' or 'reporting'")
    f = f.reset_index(drop=True)
    f["start"] = pd.DatetimeIndex(f["start"])
    f["end"] = pd.DatetimeIndex(f["end"])
    f.attrs.update(notes)
    return f


def billing_temperature(
    periods: pd.DataFrame,
    temp: pd.Series,
    *,
    timezone: str | None = None,
    eemeter_compat: bool = False,
    temp_bounds: tuple | None = None,
) -> pd.DataFrame:
    """Per-bill temperature: the kept days' daily means (CT-D-3.3, CT-D-4.1, CT-D-4.4).

    A bill's hourly temperature must cover at least 90 % of its hours, otherwise it has no
    degree days (CT-D-3.3). Within a covered bill a day's mean counts when at least half its hours
    are present (eemeter: more than 12). eemeter matches hourly temperatures to a bill only within
    35 days of its start (K7); ``eemeter_compat`` does too. Returns, per bill (same row order):
    ``coverage`` (share of the bill's hours present), ``n_days_kept`` and ``day_temps`` (the
    kept days' mean temperatures, an array per row), and ``temp_mean``.
    """
    from .intervalfit import day_hours

    hourly, t, n_screened, n_filled = _hourly_temps(
        temp, timezone=timezone, eemeter_compat=eemeter_compat, temp_bounds=temp_bounds
    )
    rows = []
    for _, p in periods.iterrows():
        start, end = pd.Timestamp(p["start"]), pd.Timestamp(p["end"])
        if hourly is None:  # daily temperatures: each day present is a whole day
            d = t.dropna()
            d = d.groupby(pd.DatetimeIndex(d.index).normalize()).mean()
            d = d[(d.index >= start) & (d.index < end)]
            n_days = max(int((end - start) / pd.Timedelta(days=1)), 1)
            cov = len(d) / n_days
            temps = d.to_numpy(float) if cov >= K.BILLING_TEMP_COVERAGE - 1e-9 else np.array([])
        else:
            h = hourly[(hourly.index >= start) & (hourly.index < end)]
            if eemeter_compat:
                h = h[h.index < start + pd.Timedelta(days=35)]
            days = pd.date_range(start, end - pd.Timedelta(days=1), freq="D")
            n_hours_total = float(day_hours(days, timezone).sum()) if len(days) else 0.0
            n_valid = float(h.notna().sum())
            cov = n_valid / n_hours_total if n_hours_total else 0.0
            if cov >= K.BILLING_TEMP_COVERAGE - 1e-9:
                g = h.resample("D")
                cnt = g.count()
                mean = g.mean()
                hrs = day_hours(cnt.index, timezone)
                keep = (
                    cnt > K.EEMETER_BILLING_DAY_HOURS
                    if eemeter_compat
                    else cnt >= K.DAY_TEMP_MIN_FRACTION * hrs - 1e-9
                )
                temps = mean[keep].to_numpy(float)
            else:
                temps = np.array([])
        rows.append(
            {
                "coverage": float(cov),
                "n_days_kept": len(temps),
                "day_temps": temps,
                "temp_mean": float(temps.mean()) if len(temps) else float("nan"),
            }
        )
    out = pd.DataFrame(rows, index=periods.index)
    out.attrs["n_temp_screened"] = n_screened
    out.attrs["n_temp_interpolated"] = n_filled
    return out


# =========================================================================== the model search
def balance_point_grid(step: int = K.BP_STEP_DOC) -> np.ndarray:
    """Candidate balance points, 30 to 90 degF inclusive (CT-D-5.1, CT-D-5.2).

    ``step`` is 3 (the CalTRACK documents' search increment: 21 points), 2, or 1 (eemeter's
    ``range(30, 91)``: 61 points); each keeps the maximum gap of 3 degF.
    """
    if step not in (1, 2, 3):
        raise ValueError("the balance-point step must be 1, 2 or 3 degF (max gap 3, CT-D-5.2)")
    return np.arange(K.BP_MIN, K.BP_MAX + 1, step, dtype=float)


@dataclass
class CalTRACKCandidate:
    """One candidate model of the search, with its status (CT-D-7, CT-D-8)."""

    model_type: str
    status: str
    heating_balance_point: float | None = None
    cooling_balance_point: float | None = None
    intercept: float | None = None
    beta_hdd: float | None = None
    beta_cdd: float | None = None
    r_squared_adj: float | None = None
    pvalues: dict = field(default_factory=dict)
    reason: str | None = None

    def as_dict(self) -> dict:
        from dataclasses import asdict

        return asdict(self)


def degree_day_sufficient(dd, weights=None, *, min_nonzero: int, min_total: float = K.MIN_TOTAL_DD):
    """``(ok, reason)``: the degree-day sufficiency of one HDD or CDD term (CT-D-7.1).

    At least ``min_nonzero`` rows with non-zero degree days (10 for daily, 0 for billing) and a
    total of at least 20 degree days, the total being ``sum(dd * weights)`` (per-day degree days
    times the bill's kept days for billing; plain sum for daily). Both tests fail on ``<``.
    """
    d = np.asarray(dd, dtype=float)
    w = np.ones(len(d)) if weights is None else np.asarray(weights, dtype=float)
    n_nonzero = int((d > 0).sum())
    total = float((d * w).sum())
    if n_nonzero < min_nonzero:
        return False, f"{n_nonzero} rows with non-zero degree days (< {min_nonzero})"
    if total < min_total:
        return False, f"{total:.4g} total degree days (< {min_total:g})"
    return True, None


def _dd(T_rows: list, bp: float, kind: str) -> np.ndarray:
    """Per-row mean daily degree days at ``bp`` over each row's kept days (CT-D-4.4)."""
    out = np.empty(len(T_rows))
    for i, t in enumerate(T_rows):
        if len(t) == 0:
            out[i] = np.nan
        elif kind == "hdd":
            out[i] = np.maximum(bp - t, 0.0).mean()
        else:
            out[i] = np.maximum(t - bp, 0.0).mean()
    return out


def _dd_matrix(T, grid, kind: str, rows: list | None) -> np.ndarray:
    """``(n_rows, len(grid))`` degree days: vectorised for daily rows, per bill otherwise."""
    if rows is None:
        T = np.asarray(T, dtype=float)[:, None]
        g = grid[None, :]
        return np.maximum(g - T, 0.0) if kind == "hdd" else np.maximum(T - g, 0.0)
    return np.column_stack([_dd(rows, bp, kind) for bp in grid])


def select_candidate(candidates: list) -> CalTRACKCandidate | None:
    """The selected candidate (CT-D-8.1, CT-D-8.2): scanning in the fixed candidate order, the
    first QUALIFIED candidate whose adjusted R^2 is strictly greater than the best so far (from
    ``-inf``). An exact tie keeps the earlier candidate; a NaN adjusted R^2 is never selected."""
    best, best_adj = None, -np.inf
    for c in candidates:
        if c.status != QUALIFIED or c.r_squared_adj is None:
            continue
        if c.r_squared_adj > best_adj:
            best, best_adj = c, c.r_squared_adj
    return best


def _search(
    upd: np.ndarray,
    T: np.ndarray | None,
    *,
    fuel: str,
    grid: np.ndarray,
    weights: np.ndarray | None,
    dd_rows: list | None,
    min_nonzero: int,
    max_p_value: float,
) -> list:
    """Every candidate in eemeter's scan order with its status (CT-D-6, CT-D-7, CT-D-8.2)."""
    n = len(upd)
    ones = np.ones(n)
    ddw = weights  # the degree-day total weight: kept days per bill (billing), 1 (daily)
    hdd = _dd_matrix(T, grid, "hdd", dd_rows)
    cdd = _dd_matrix(T, grid, "cdd", dd_rows) if fuel == "electricity" else None
    hdd_ok = [
        degree_day_sufficient(hdd[:, j], ddw, min_nonzero=min_nonzero) for j in range(len(grid))
    ]
    cdd_ok = (
        [degree_day_sufficient(cdd[:, j], ddw, min_nonzero=min_nonzero) for j in range(len(grid))]
        if cdd is not None
        else []
    )
    want_p = max_p_value < 1.0
    out: list = []

    def fit(model_type, cols, names, bh=None, bc=None):
        try:
            X = np.column_stack([ones, *cols])
            r = wls(X, upd, weights)
        except Exception as e:  # noqa: BLE001 -- CT-D-7.4: a failed fit is an ERROR candidate
            return CalTRACKCandidate(model_type, ERROR, bh, bc, reason=f"{type(e).__name__}: {e}")
        params = dict(zip(["intercept", *names], (float(x) for x in r.params), strict=True))
        adj = 0.0 if model_type == "intercept_only" else r.r_squared_adj
        c = CalTRACKCandidate(
            model_type,
            QUALIFIED,
            bh,
            bc,
            intercept=params["intercept"],
            beta_hdd=params.get("beta_hdd"),
            beta_cdd=params.get("beta_cdd"),
            r_squared_adj=float(adj) if np.isfinite(adj) else None,
        )
        neg = [k for k, v in params.items() if v < 0]  # CT-D-7.2: 0 is allowed
        if neg:
            c.status, c.reason = DISQUALIFIED, "negative " + ", ".join(neg)
            return c
        if want_p:  # CT-D-7.3: off at the default 1.0, never computed then
            pv = dict(zip(["intercept", *names], (float(x) for x in r.pvalues), strict=True))
            c.pvalues = {k: v for k, v in pv.items() if k != "intercept"}
            bad = [k for k, v in c.pvalues.items() if np.isfinite(v) and v > max_p_value]
            if bad:
                c.status, c.reason = (
                    DISQUALIFIED,
                    f"p-value above {max_p_value:g}: " + ", ".join(bad),
                )
        return c

    out.append(fit("intercept_only", [], []))
    for j, bh in enumerate(grid):
        ok, why = hdd_ok[j]
        if not ok:
            out.append(CalTRACKCandidate("hdd_only", NOT_ATTEMPTED, float(bh), None, reason=why))
            continue
        out.append(fit("hdd_only", [hdd[:, j]], ["beta_hdd"], bh=float(bh)))
    if cdd is not None:
        for j, bc in enumerate(grid):
            ok, why = cdd_ok[j]
            if not ok:
                out.append(
                    CalTRACKCandidate("cdd_only", NOT_ATTEMPTED, None, float(bc), reason=why)
                )
                continue
            out.append(fit("cdd_only", [cdd[:, j]], ["beta_cdd"], bc=float(bc)))
        for jc, bc in enumerate(grid):  # CT-D-8.2: outer loop cooling, inner heating, bh <= bc
            for jh, bh in enumerate(grid):
                if bh > bc:
                    continue
                okc, whyc = cdd_ok[jc]
                okh, whyh = hdd_ok[jh]
                if not (okc and okh):
                    out.append(
                        CalTRACKCandidate(
                            "cdd_hdd", NOT_ATTEMPTED, float(bh), float(bc), reason=whyh or whyc
                        )
                    )
                    continue
                out.append(
                    fit(
                        "cdd_hdd",
                        [hdd[:, jh], cdd[:, jc]],
                        ["beta_hdd", "beta_cdd"],
                        bh=float(bh),
                        bc=float(bc),
                    )
                )
    return out


# =========================================================================== the models
@dataclass
class CalTRACKDailyModel:
    """A fitted CalTRACK 2.0 daily model: usage per day ``mu + beta_H HDD(bh) + beta_C CDD(bc)``.

    ``model_type`` is ``intercept_only``, ``hdd_only``, ``cdd_only`` or ``cdd_hdd`` (CT-D-6); a
    term the type does not have has slope 0 and balance point ``None``. ``metrics`` are the
    CalTRACK metrics of the fit on usage per day, ``totals_metrics`` on period totals (identical
    for daily data; CT-C-1.4). Build one with :func:`fit_caltrack_daily`; :meth:`predict` projects
    it, :meth:`as_dict` / :meth:`from_dict` serialise it.
    """

    model_type: str
    intercept: float
    beta_hdd: float = 0.0
    beta_cdd: float = 0.0
    heating_balance_point: float | None = None
    cooling_balance_point: float | None = None
    r_squared_adj: float = 0.0
    fuel: str = "electricity"
    eemeter_compat: bool = False
    bp_step: int = K.BP_STEP_DOC
    n: int = 0
    metrics: dict = field(default_factory=dict)
    totals_metrics: dict = field(default_factory=dict)
    temperature_range: tuple | None = None
    granularity = "daily"

    @property
    def num_parameters(self) -> int:
        """``c``: explanatory variables excluding the intercept (0, 1 or 2)."""
        return {"intercept_only": 0, "hdd_only": 1, "cdd_only": 1, "cdd_hdd": 2}[self.model_type]

    def _hdd(self, t):
        if self.heating_balance_point is None:
            return np.zeros_like(t)
        return np.maximum(self.heating_balance_point - t, 0.0)

    def _cdd(self, t):
        if self.cooling_balance_point is None:
            return np.zeros_like(t)
        return np.maximum(t - self.cooling_balance_point, 0.0)

    def usage_per_day(self, daily_temp) -> np.ndarray:
        """Predicted usage per day at daily mean temperatures (NaN where the temperature is)."""
        t = np.asarray(daily_temp, dtype=float)
        out = self.intercept + self.beta_hdd * self._hdd(t) + self.beta_cdd * self._cdd(t)
        return np.where(np.isfinite(t), out, np.nan)

    def predict(self, index, temp, *, timezone: str | None = None, disaggregate: bool = False):
        """Predicted usage per day for the days of ``index`` (CT-D-10.1, CT-D-10.2, CT-D-10.5).

        ``temp`` is a temperature series (hourly or finer, or daily means), prepared like the
        baseline's (:func:`daily_temperature`), or an array of daily means aligned with ``index``.
        A day without a valid mean temperature predicts NaN (CT-D-10.3). With ``disaggregate``
        returns a frame of ``predicted``, ``base_load``, ``heating_load`` and ``cooling_load``.
        """
        days = pd.DatetimeIndex(index).normalize()
        if isinstance(temp, pd.Series) and isinstance(temp.index, pd.DatetimeIndex):
            dt = daily_temperature(temp, timezone=timezone, eemeter_compat=self.eemeter_compat)
            t = dt["temp"].reindex(days).to_numpy(float)
        else:
            t = np.asarray(temp, dtype=float)
            if len(t) != len(days):
                raise ValueError("temp must align with index")
        pred = pd.Series(self.usage_per_day(t), index=days, name="predicted")
        if not disaggregate:
            return pred
        fin = np.isfinite(t)
        return pd.DataFrame(
            {
                "predicted": pred,
                "base_load": np.where(fin, self.intercept, np.nan),
                "heating_load": np.where(fin, self.beta_hdd * self._hdd(t), np.nan),
                "cooling_load": np.where(fin, self.beta_cdd * self._cdd(t), np.nan),
            },
            index=days,
        )

    def as_dict(self) -> dict:
        """A JSON-safe dict; :meth:`from_dict` rebuilds the model."""
        return {
            "type": type(self).__name__,
            "method": f"CalTRACK 2.0 {self.granularity}",
            "model_type": self.model_type,
            "intercept": self.intercept,
            "beta_hdd": self.beta_hdd,
            "beta_cdd": self.beta_cdd,
            "heating_balance_point": self.heating_balance_point,
            "cooling_balance_point": self.cooling_balance_point,
            "r_squared_adj": self.r_squared_adj,
            "fuel": self.fuel,
            "eemeter_compat": self.eemeter_compat,
            "bp_step": self.bp_step,
            "n": self.n,
            "metrics": dict(self.metrics),
            "totals_metrics": dict(self.totals_metrics),
            "temperature_range": None
            if self.temperature_range is None
            else list(self.temperature_range),
        }

    @classmethod
    def from_dict(cls, d: dict):
        """Rebuild a model written by :meth:`as_dict`."""
        tr = d.get("temperature_range")
        return cls(
            model_type=d["model_type"],
            intercept=float(d["intercept"]),
            beta_hdd=float(d.get("beta_hdd", 0.0)),
            beta_cdd=float(d.get("beta_cdd", 0.0)),
            heating_balance_point=d.get("heating_balance_point"),
            cooling_balance_point=d.get("cooling_balance_point"),
            r_squared_adj=float(d.get("r_squared_adj", 0.0)),
            fuel=d.get("fuel", "electricity"),
            eemeter_compat=bool(d.get("eemeter_compat", False)),
            bp_step=int(d.get("bp_step", K.BP_STEP_DOC)),
            n=int(d.get("n", 0)),
            metrics=dict(d.get("metrics") or {}),
            totals_metrics=dict(d.get("totals_metrics") or {}),
            temperature_range=None if tr is None else tuple(tr),
        )


@dataclass
class CalTRACKBillingModel(CalTRACKDailyModel):
    """A fitted CalTRACK 2.0 billing model (CT-D-9): the daily model's form fitted to bills'
    usage per day by weighted least squares, each bill weighted by its days with a valid
    temperature. :meth:`predict` takes billing periods."""

    granularity = "billing"

    def predict(  # billing predicts per bill (``periods``), not per day
        self, periods, temp, *, timezone: str | None = None, disaggregate: bool = False
    ):
        """Predicted usage per bill (CT-D-10.1, CT-D-10.2): ``n_days * (mu + beta_H HDD_p +
        beta_C CDD_p)``, the degree days the mean over the bill's days with a valid temperature
        (so missing days count at that mean). A bill whose hourly temperature covers under 90 %
        of it predicts NaN (CT-D-3.3, E11). ``periods`` is a :class:`~camber.mandv.billing.
        BillingSeries` or a frame with ``start`` and ``end`` (exclusive). Indexed by ``start``.
        """
        if isinstance(periods, pd.DataFrame) and "energy" not in periods.columns:
            periods = periods.assign(energy=np.nan)
        f = _billing_frame(periods)
        bt = billing_temperature(f, temp, timezone=timezone, eemeter_compat=self.eemeter_compat)
        rows = list(bt["day_temps"])
        hdd = (
            _dd(rows, self.heating_balance_point, "hdd")
            if self.heating_balance_point is not None
            else np.where(bt["n_days_kept"] > 0, 0.0, np.nan)
        )
        cdd = (
            _dd(rows, self.cooling_balance_point, "cdd")
            if self.cooling_balance_point is not None
            else np.where(bt["n_days_kept"] > 0, 0.0, np.nan)
        )
        n_days = f["days"].to_numpy(float)
        base = self.intercept * n_days
        heat = self.beta_hdd * hdd * n_days
        cool = self.beta_cdd * cdd * n_days
        ok = bt["n_days_kept"].to_numpy() > 0
        pred = np.where(ok, base + heat + cool, np.nan)
        idx = pd.DatetimeIndex(f["start"], name="start")
        if not disaggregate:
            return pd.Series(pred, index=idx, name="predicted")
        return pd.DataFrame(
            {
                "predicted": pred,
                "base_load": np.where(ok, base, np.nan),
                "heating_load": np.where(ok, heat, np.nan),
                "cooling_load": np.where(ok, cool, np.nan),
                "n_days": n_days,
            },
            index=idx,
        )


def _model_from(cand, cls, **kw):
    return cls(
        model_type=cand.model_type,
        intercept=float(cand.intercept),
        beta_hdd=float(cand.beta_hdd or 0.0),
        beta_cdd=float(cand.beta_cdd or 0.0),
        heating_balance_point=cand.heating_balance_point,
        cooling_balance_point=cand.cooling_balance_point,
        r_squared_adj=float(cand.r_squared_adj if cand.r_squared_adj is not None else np.nan),
        **kw,
    )


def _drivers(model: CalTRACKDailyModel, dd_rows: list) -> tuple:
    """The selected model's per-row degree-day columns and the CAMBER design spec for them."""
    cols, names = [], []
    if model.heating_balance_point is not None:
        cols.append(_dd(dd_rows, model.heating_balance_point, "hdd"))
        names.append(f"hdd_{model.heating_balance_point:g}")
    if model.cooling_balance_point is not None:
        cols.append(_dd(dd_rows, model.cooling_balance_point, "cdd"))
        names.append(f"cdd_{model.cooling_balance_point:g}")
    if not cols:  # intercept-only: the temperature itself is the support coverage grades
        cols = [np.array([t.mean() if len(t) else np.nan for t in dd_rows])]
        return np.column_stack(cols), ("temperature",), None
    return np.column_stack(cols), tuple(names), ("bdd",)


def _view(model: CalTRACKDailyModel, D_base, names, design, *, n, p, s2, rho, weights=None):
    """A :class:`ProjectionView` of the model in its degree-day drivers, with its fit record."""
    from .coverage import _linear_fit_record, _safe
    from .models import fit_weights

    slopes = [
        b
        for b, bp in (
            (model.beta_hdd, model.heating_balance_point),
            (model.beta_cdd, model.cooling_balance_point),
        )
        if bp is not None
    ]

    def predict(D):
        D = np.asarray(D, dtype=float)
        D = D[:, None] if D.ndim == 1 else D
        fin = np.all(np.isfinite(D), axis=1)
        y = model.intercept + (D @ np.asarray(slopes) if slopes else 0.0)
        return np.where(fin, y, np.nan)

    w, scale = fit_weights(weights) if weights is not None else (None, None)
    rec = _safe(
        _linear_fit_record,
        D_base,
        names,
        design,
        s2=s2,
        n=n,
        p=p,
        rho=rho,
        weights=w,
        weight_scale=None if w is None else scale,
    )
    return ProjectionView(predict, rec)


# =========================================================================== sufficiency
def _sufficiency(
    n_total: int,
    n_meter: int,
    n_temp: int,
    n_both: int,
    *,
    n_negative: int,
    n_outliers: int,
    n_zero_masked: int,
    eemeter_compat: bool,
    extra: dict | None = None,
) -> dict:
    """The baseline data-sufficiency verdict (CT-D-1.1 .. CT-D-1.6, CT-D-1.8)."""
    criteria = []

    def check(name, n_valid):
        missing = n_total - n_valid
        if eemeter_compat:
            frac = n_valid / n_total if n_total else 0.0
            ok = frac >= K.EEMETER_MIN_VALID_FRACTION
            rule = f"valid fraction {frac:.4f} >= {K.EEMETER_MIN_VALID_FRACTION}"
        else:
            ok = missing <= K.MAX_MISSING_DAYS
            rule = f"{missing} missing days <= {K.MAX_MISSING_DAYS}"
        criteria.append(
            {"name": name, "passed": bool(ok), "valid_days": int(n_valid), "rule": rule}
        )

    check("valid_days", n_both)
    check("valid_meter_days", n_meter)
    check("valid_temperature_days", n_temp)
    warnings_: list = []
    if n_negative:
        warnings_.append(
            f"{n_negative} negative meter value(s): flagged for review (possible net metering, "
            "CT-D-1.5)" + ("; eemeter fails sufficiency on them" if eemeter_compat else "")
        )
        if eemeter_compat:
            criteria.append({"name": "no_negative_values", "passed": False, "rule": "eemeter"})
    if n_outliers:
        warnings_.append(
            f"{n_outliers} usage value(s) above median + 3 IQR: flagged for review (CT-D-1.6)"
        )
    if n_zero_masked:
        warnings_.append(
            f"{n_zero_masked} electricity meter value(s) of exactly 0 were treated as missing "
            "(CT-D-1.3)"
        )
    passed = all(c["passed"] for c in criteria)
    out = {
        "status": "PASS" if passed else "FAIL",
        "n_days_total": int(n_total),
        "n_days_valid": int(n_both),
        "n_days_meter_valid": int(n_meter),
        "n_days_temperature_valid": int(n_temp),
        "n_days_missing": int(n_total - n_both),
        "criteria": criteria,
        "warnings": warnings_,
        "n_negative": int(n_negative),
        "n_outliers": int(n_outliers),
        "n_zero_masked": int(n_zero_masked),
    }
    if extra:
        out.update(extra)
    return out


def _require(suff: dict, check: bool, label: str) -> list:
    """Raise :class:`~camber.mandv.sufficiency.InsufficientBaseline` on a failed sufficiency
    verdict when ``check``; otherwise return the caveat that says it failed."""
    if suff["status"] == "PASS":
        return []
    failed = [c for c in suff["criteria"] if not c["passed"]]
    msg = f"CalTRACK {label} baseline data sufficiency failed: " + "; ".join(
        f"{c['name']} ({c['rule']})" for c in failed
    )
    if check:
        from .sufficiency import InsufficientBaseline, baseline_need

        need_days = K.BASELINE_DAYS - K.MAX_MISSING_DAYS
        raise InsufficientBaseline(
            msg, baseline_need("daily", suff["n_days_valid"], min_n=need_days)
        )
    return [msg + " (check_sufficiency=False: fitted anyway; not CalTRACK-compliant)"]


def _metadata_checks(net_metering_changed: bool, ev_charger_installed: bool) -> list:
    """CT-D-1.7: a change of net-metering status excludes the project; an EV charger is flagged."""
    if net_metering_changed:
        raise ValueError(
            "CalTRACK excludes a project whose net-metering status changed in the baseline or "
            "reporting period (CT-D-1.7)"
        )
    if ev_charger_installed:
        return ["an EV charger was installed: flagged for review (CT-D-1.7)"]
    return []


def _window(index_end, intervention_start) -> tuple:
    end = (
        pd.Timestamp(intervention_start).normalize()
        if intervention_start is not None
        else pd.Timestamp(index_end).normalize() + pd.Timedelta(days=1)
    )
    return end - pd.Timedelta(days=K.BASELINE_DAYS), end


# =========================================================================== daily
def _daily_frame(energy, temp, *, fuel, rate_is_energy_rate, timezone, eemeter_compat, temp_bounds):
    u = daily_usage(
        energy,
        fuel=fuel,
        rate_is_energy_rate=rate_is_energy_rate,
        timezone=timezone,
        eemeter_compat=eemeter_compat,
    )
    t = daily_temperature(
        temp, timezone=timezone, eemeter_compat=eemeter_compat, temp_bounds=temp_bounds
    )
    f = u.join(t, how="outer")
    f.attrs = {**u.attrs, **t.attrs}
    return f


def fit_caltrack_daily(
    energy: pd.Series,
    temp: pd.Series,
    *,
    fuel: str = "electricity",
    eemeter_compat: bool = False,
    bp_step: int | None = None,
    max_p_value: float = K.MAX_P_VALUE,
    intervention_start=None,
    rate_is_energy_rate: bool = False,
    timezone: str | None = None,
    temp_bounds: tuple | None = None,
    check_sufficiency: bool = True,
) -> tuple:
    """Fit a CalTRACK 2.0 daily model on the 365 baseline days: ``(model, details)``.

    ``energy`` is interval (or daily) meter data, ``temp`` the outdoor temperature (degF, hourly or
    finer, or daily means). The baseline is the 365 days before ``intervention_start`` (default:
    the day after the last meter reading; CT-D-1.1); data outside it are not used. Sufficiency
    (CT-D-1) is checked first and a failure raises
    :class:`~camber.mandv.sufficiency.InsufficientBaseline` unless ``check_sufficiency=False``.

    The search fits intercept-only, then HDD-only, CDD-only and HDD+CDD on the balance-point grid
    (``bp_step`` 3 degF by default, 1 under ``eemeter_compat``; CT-D-5), skips terms without
    enough degree days (NOT ATTEMPTED, CT-D-7.1), disqualifies negative coefficients (CT-D-7.2)
    and, only when ``max_p_value`` is below its default 1.0, large p-values (CT-D-7.3), and selects
    the highest adjusted R^2 with eemeter's tie order (CT-D-8). Gas fits no cooling terms
    (CT-D-5.4). ``details`` holds ``frame`` (the baseline days), ``sufficiency``, ``candidates``
    and ``caveats``. A search where nothing qualifies raises ``ValueError`` (``NO MODEL``,
    CT-D-8.3).
    """
    K.check_fuel(fuel)
    f = _daily_frame(
        energy,
        temp,
        fuel=fuel,
        rate_is_energy_rate=rate_is_energy_rate,
        timezone=timezone,
        eemeter_compat=eemeter_compat,
        temp_bounds=temp_bounds,
    )
    if f["usage"].dropna().empty:
        raise ValueError("CalTRACK daily: no meter data (NO DATA, CT-D-8.3)")
    start, end = _window(f["usage"].dropna().index.max(), intervention_start)
    n_outside = int(f["usage"].notna()[(f.index < start) | (f.index >= end)].sum())
    f = f[(f.index >= start) & (f.index < end)]
    meter_ok = f["usage"].notna()
    temp_ok = f["valid_sufficiency"].fillna(False).astype(bool)
    both = meter_ok & f["temp"].notna()
    both_suff = meter_ok & temp_ok
    suff = _sufficiency(
        K.BASELINE_DAYS,
        int(meter_ok.sum()),
        int(temp_ok.sum()),
        int(both_suff.sum()),
        n_negative=int(f.attrs.get("n_negative", 0)),
        n_outliers=K.outlier_count(f["usage"]),
        n_zero_masked=int(f.attrs.get("n_zero_masked", 0)),
        eemeter_compat=eemeter_compat,
        extra={
            "baseline_start": str(start.date()),
            "baseline_end": str(end.date()),
            "n_days_outside_baseline": n_outside,
            "n_partial_days_dropped": int(f.attrs.get("n_days_dropped", 0)),
        },
    )
    caveats = _require(suff, check_sufficiency, "daily") + list(suff["warnings"])
    if n_outside:
        caveats.append(
            f"{n_outside} meter day(s) outside the 365-day baseline before the intervention were "
            "not used (CT-D-1.1)"
        )
    n_scr = int(f.attrs.get("n_temp_screened", 0))
    if n_scr:
        caveats.append(screened_caveat(n_scr, "baseline temperature value(s)"))
    fit_rows = f[both]
    step = (
        bp_step if bp_step is not None else (K.BP_STEP_EEMETER if eemeter_compat else K.BP_STEP_DOC)
    )
    grid = balance_point_grid(step)
    upd = fit_rows["usage"].to_numpy(float)
    T = fit_rows["temp"].to_numpy(float)
    if not len(upd):
        raise ValueError("CalTRACK daily: no day has both usage and temperature (NO DATA)")
    cands = _search(
        upd,
        T,
        fuel=fuel,
        grid=grid,
        weights=None,
        dd_rows=None,
        min_nonzero=K.MIN_NONZERO_DD_DAYS,
        max_p_value=max_p_value,
    )
    best = select_candidate(cands)
    if best is None:
        raise ValueError("CalTRACK daily: no candidate model qualified (NO MODEL, CT-D-8.3)")
    model = _model_from(
        best,
        CalTRACKDailyModel,
        fuel=fuel,
        eemeter_compat=eemeter_compat,
        bp_step=step,
        n=len(upd),
        temperature_range=(float(T.min()), float(T.max())),
    )
    pred = model.usage_per_day(T)
    model.metrics = caltrack_metrics(upd, pred, model.num_parameters)
    model.totals_metrics = dict(model.metrics)
    return model, {"frame": fit_rows, "sufficiency": suff, "candidates": cands, "caveats": caveats}


def _g14_fit(y, yhat, p, *, interval, time_index, weights=None):
    from .stats import cv_rmse_max_for, fit_stats

    return fit_stats(
        y, yhat, p, cv_rmse_max=cv_rmse_max_for(interval), time_index=time_index, weights=weights
    )


def caltrack_daily_savings(
    baseline_energy: pd.Series,
    baseline_temp: pd.Series,
    reporting_energy: pd.Series,
    reporting_temp: pd.Series,
    *,
    fuel: str = "electricity",
    eemeter_compat: bool = False,
    bp_step: int | None = None,
    max_p_value: float = K.MAX_P_VALUE,
    intervention_start=None,
    rate_is_energy_rate: bool = False,
    timezone: str | None = None,
    temp_bounds: tuple | None = None,
    confidence: float = 0.90,
    extrapolation=None,
    check_sufficiency: bool = True,
    net_metering_changed: bool = False,
    ev_charger_installed: bool = False,
) -> CalTRACKResult:
    """CalTRACK 2.0 **Daily** avoided energy use, as a :class:`CalTRACKResult`.

    Fits :func:`fit_caltrack_daily` on the baseline and projects the selected model onto each
    reporting day: ``AEU_d = predicted_d - observed_d`` (counterfactual minus observed, CT-D-10.1).
    A reporting day without a valid temperature has no counterfactual and a day without usage
    (electricity zeros included) no AEU; both are left out of the sums and counted (CT-D-10.3).
    The total is the sum over days; nothing is annualised (CT-D-10.4).

    ``result.savings`` is CAMBER's :class:`~camber.mandv.stats.SavingsResult` (Guideline 14 band,
    coverage of the reporting degree days by the baseline's, caveats); ``result.fsu`` is CalTRACK's
    own fractional savings uncertainty (CT-C-1.5) with the daily coefficients. ``eemeter_compat``
    reproduces eemeter 2.10 (see the module docstring). ``net_metering_changed=True`` raises (the
    project is excluded) and ``ev_charger_installed=True`` adds a review flag (CT-D-1.7).
    """
    notes = _metadata_checks(net_metering_changed, ev_charger_installed)
    model, info = fit_caltrack_daily(
        baseline_energy,
        baseline_temp,
        fuel=fuel,
        eemeter_compat=eemeter_compat,
        bp_step=bp_step,
        max_p_value=max_p_value,
        intervention_start=intervention_start,
        rate_is_energy_rate=rate_is_energy_rate,
        timezone=timezone,
        temp_bounds=temp_bounds,
        check_sufficiency=check_sufficiency,
    )
    base = info["frame"]
    rep = _daily_frame(
        reporting_energy,
        reporting_temp,
        fuel=fuel,
        rate_is_energy_rate=rate_is_energy_rate,
        timezone=timezone,
        eemeter_compat=eemeter_compat,
        temp_bounds=temp_bounds,
    )
    rep = rep[rep["usage"].notna() | rep["temp"].notna()]
    if rep["usage"].dropna().empty:
        raise ValueError("no usable reporting-period data")
    rows_b = [np.array([t]) for t in base["temp"].to_numpy(float)]
    rows_r = [
        np.array([t]) if np.isfinite(t) else np.array([]) for t in rep["temp"].to_numpy(float)
    ]
    y_b = base["usage"].to_numpy(float)
    yhat_b = model.usage_per_day(base["temp"].to_numpy(float))
    p = model.num_parameters + 1
    fit = _g14_fit(y_b, yhat_b, p, interval="daily", time_index=base.index)
    resid = y_b - yhat_b
    D_b, names, design = _drivers(model, rows_b)
    D_r, _, _ = _drivers(model, rows_r)
    view = _view(
        model,
        D_b,
        names,
        design,
        n=len(y_b),
        p=p,
        s2=float(resid @ resid) / max(len(y_b) - p, 1),
        rho=fit.rho_lag1,
    )
    from .stats import avoided_energy_savings

    y_r = rep["usage"].to_numpy(float)
    savings = avoided_energy_savings(
        view,
        D_r,
        y_r,
        cv_rmse=fit.cv_rmse,
        n_baseline=fit.n,
        p_baseline=p,
        confidence=confidence,
        rho=fit.rho_lag1,
        extrapolation=extrapolation,
    )
    pred_r = model.usage_per_day(rep["temp"].to_numpy(float))
    table = pd.DataFrame(
        {"observed": y_r, "predicted": pred_r, "temp": rep["temp"].to_numpy(float)},
        index=rep.index,
    )
    table["avoided"] = table["predicted"] - table["observed"]
    used = table["avoided"].notna()
    savings.caveats.extend(info["caveats"] + notes + _reporting_caveats(rep, table))
    fsu = None
    if savings.baseline_projected:
        frac = float(table.loc[used, "avoided"].sum() / table.loc[used, "predicted"].sum())
        q = int(used.sum())
        fsu = caltrack_fsu(
            granularity="daily",
            cvrmse_adj=model.totals_metrics.get("cvrmse_adj"),
            n_baseline=model.n,
            num_parameters=model.num_parameters,
            rho=model.totals_metrics.get("autocorr_resid"),
            n_reporting=q,
            reporting_months=q / K.FSU_DAYS_PER_MONTH,
            savings_fraction=frac,
            confidence=confidence,
        )
    return CalTRACKResult(
        method="CalTRACK 2.0 daily",
        model=model,
        savings=savings,
        sufficiency=info["sufficiency"],
        fit=fit,
        fsu=fsu,
        eemeter_compat=eemeter_compat,
        reporting=table,
        candidates=info["candidates"],
    )


def _reporting_caveats(rep: pd.DataFrame, table: pd.DataFrame) -> list:
    out = []
    no_t = int((table["observed"].notna() & table["predicted"].isna()).sum())
    no_u = int((table["observed"].isna() & table["predicted"].notna()).sum())
    if no_t:
        out.append(
            f"{no_t} reporting day(s) had usage but no valid temperature: no counterfactual, "
            "left out of the savings (CT-D-10.3)"
        )
    if no_u:
        out.append(
            f"{no_u} reporting day(s) had a temperature but no usage: no avoided energy computed "
            "(CT-D-10.3)"
        )
    n = int(rep.attrs.get("n_zero_masked", 0))
    if n:
        out.append(f"{n} reporting electricity value(s) of exactly 0 were treated as missing")
    n = int(rep.attrs.get("n_temp_screened", 0))
    if n:
        out.append(screened_caveat(n, "reporting temperature value(s)"))
    return out


# =========================================================================== billing
def _snap_window(f: pd.DataFrame, intervention_start) -> tuple:
    """The baseline bills: those ending by the intervention and starting on or after the bill
    boundary nearest to 365 days before it (eemeter's period overshoot, CT-D-1.1)."""
    end = (
        pd.Timestamp(intervention_start).normalize()
        if intervention_start is not None
        else pd.Timestamp(f["end"].max())
    )
    target = end - pd.Timedelta(days=K.BASELINE_DAYS)
    starts = pd.DatetimeIndex(f["start"])
    if len(starts):
        near = starts[np.argmin(np.abs((starts - target).asi8))]
        start = near if abs(near - target) <= pd.Timedelta(days=17) else target
    else:
        start = target
    return start, end


def fit_caltrack_billing(
    bills,
    temp: pd.Series,
    *,
    fuel: str = "electricity",
    eemeter_compat: bool = False,
    bp_step: int | None = None,
    max_p_value: float = K.MAX_P_VALUE,
    intervention_start=None,
    billing_cycle: str | None = None,
    timezone: str | None = None,
    temp_bounds: tuple | None = None,
    check_sufficiency: bool = True,
) -> tuple:
    """Fit a CalTRACK 2.0 billing model: ``(model, details)`` (CT-D-9).

    ``bills`` is a :class:`~camber.mandv.billing.BillingSeries` or a frame with ``start``, ``end``
    (exclusive), ``energy`` and optionally ``estimated``. The bills are cleaned
    (:func:`clean_billing_periods`), kept within the 365 days before ``intervention_start``
    (default: the last bill's end) and paired with their temperatures
    (:func:`billing_temperature`). The dependent variable is each bill's usage per day
    (``energy / days``), the regressors its mean daily degree days over the kept days, and the fit
    weighted least squares with weight ``n_days_kept`` (CT-D-6.5). The 10-day degree-day rule does
    not apply; the 20-degree-day total does, as ``sum(DD_p * n_days_kept_p)`` (CT-D-7.1). CalTRACK
    sets no minimum bill count beyond the 365-day/90 % rules (CT-D-1.8). The search and selection
    are the daily method's.
    """
    K.check_fuel(fuel)
    raw = _billing_frame(bills)
    v, n_zero, n_neg = K.mask_meter_values(raw["energy"], fuel)
    raw["energy"] = v.to_numpy()
    start, end = _snap_window(raw, intervention_start)
    raw = raw[(raw["start"] >= start) & (raw["end"] <= end)].reset_index(drop=True)
    f = clean_billing_periods(
        raw, period="baseline", billing_cycle=billing_cycle, eemeter_compat=eemeter_compat
    )
    if f.empty:
        raise ValueError("CalTRACK billing: no bill in the baseline (NO DATA, CT-D-8.3)")
    bt = billing_temperature(
        f, temp, timezone=timezone, eemeter_compat=eemeter_compat, temp_bounds=temp_bounds
    )
    f = f.join(bt)
    f["upd"] = f["energy"] / f["days"]
    meter_ok = f["upd"].notna()
    temp_ok = f["n_days_kept"] > 0
    n_meter = int(f.loc[meter_ok, "days"].sum())
    n_temp = int(f.loc[temp_ok, "n_days_kept"].sum())
    n_both = int(f.loc[meter_ok & temp_ok, "days"].sum())
    suff = _sufficiency(
        K.BASELINE_DAYS,
        min(n_meter, K.BASELINE_DAYS),
        min(n_temp, K.BASELINE_DAYS),
        min(n_both, K.BASELINE_DAYS),
        n_negative=n_neg,
        n_outliers=K.outlier_count(f["upd"]),
        n_zero_masked=n_zero,
        eemeter_compat=eemeter_compat,
        extra={
            "baseline_start": str(pd.Timestamp(start).date()),
            "baseline_end": str(pd.Timestamp(end).date()),
            "n_bills": int(len(f)),
            "billing_cycle": f.attrs.get("billing_cycle"),
            "n_estimated_merged": int(f.attrs.get("merged", 0)),
            "n_estimated_missing": int(f.attrs.get("estimated_missing", 0)),
            "n_over_70_days_dropped": int(f.attrs.get("over_cap", 0)),
            "n_length_dropped": int(f.attrs.get("n_length_dropped", 0)),
        },
    )
    caveats = _require(suff, check_sufficiency, "billing") + list(suff["warnings"])
    n_scr = int(bt.attrs.get("n_temp_screened", 0))
    if n_scr:
        caveats.append(screened_caveat(n_scr, "baseline temperature value(s)"))
    rows = f[meter_ok & temp_ok]
    if rows.empty:
        raise ValueError("CalTRACK billing: no bill has both usage and temperature (NO DATA)")
    upd = rows["upd"].to_numpy(float)
    w = rows["n_days_kept"].to_numpy(float)
    dd_rows = list(rows["day_temps"])
    step = (
        bp_step if bp_step is not None else (K.BP_STEP_EEMETER if eemeter_compat else K.BP_STEP_DOC)
    )
    cands = _search(
        upd,
        None,
        fuel=fuel,
        grid=balance_point_grid(step),
        weights=w,
        dd_rows=dd_rows,
        min_nonzero=K.MIN_NONZERO_DD_BILLING,
        max_p_value=max_p_value,
    )
    best = select_candidate(cands)
    if best is None:
        raise ValueError("CalTRACK billing: no candidate model qualified (NO MODEL, CT-D-8.3)")
    allT = np.concatenate([t for t in dd_rows if len(t)])
    model = _model_from(
        best,
        CalTRACKBillingModel,
        fuel=fuel,
        eemeter_compat=eemeter_compat,
        bp_step=step,
        n=len(upd),
        temperature_range=(float(allT.min()), float(allT.max())),
    )
    pred = _billing_upd(model, dd_rows)
    model.metrics = caltrack_metrics(upd, pred, model.num_parameters)
    days = rows["days"].to_numpy(float)
    model.totals_metrics = caltrack_metrics(upd * days, pred * days, model.num_parameters)
    return model, {"frame": rows, "sufficiency": suff, "candidates": cands, "caveats": caveats}


def _billing_upd(model: CalTRACKDailyModel, dd_rows: list) -> np.ndarray:
    h = (
        _dd(dd_rows, model.heating_balance_point, "hdd")
        if model.heating_balance_point is not None
        else 0.0
    )
    c = (
        _dd(dd_rows, model.cooling_balance_point, "cdd")
        if model.cooling_balance_point is not None
        else 0.0
    )
    ok = np.array([len(t) > 0 for t in dd_rows])
    return np.where(ok, model.intercept + model.beta_hdd * h + model.beta_cdd * c, np.nan)


def caltrack_billing_savings(
    baseline_bills,
    baseline_temp: pd.Series,
    reporting_bills,
    reporting_temp: pd.Series,
    *,
    fuel: str = "electricity",
    eemeter_compat: bool = False,
    bp_step: int | None = None,
    max_p_value: float = K.MAX_P_VALUE,
    intervention_start=None,
    billing_cycle: str | None = None,
    timezone: str | None = None,
    temp_bounds: tuple | None = None,
    confidence: float = 0.90,
    extrapolation=None,
    check_sufficiency: bool = True,
    net_metering_changed: bool = False,
    ev_charger_installed: bool = False,
) -> CalTRACKResult:
    """CalTRACK 2.0 **Billing** avoided energy use, as a :class:`CalTRACKResult`.

    Fits :func:`fit_caltrack_billing` and projects it onto the reporting bills, cleaned by the
    reporting rules (estimated and off-cycle reads combined, long bills flagged; CT-D-3.4):
    ``AEU_p = n_p (mu + beta_H HDD_p + beta_C CDD_p) - observed_p`` (CT-D-10.1, CT-D-10.2). A bill
    whose temperature covers under 90 % of its hours has no counterfactual (CT-D-3.3) and is left
    out of the sums. ``result.fsu`` uses the billing coefficients with ``Q`` the reporting bills
    and ``M`` the reporting days over 30. The rest is as :func:`caltrack_daily_savings`.
    """
    notes = _metadata_checks(net_metering_changed, ev_charger_installed)
    model, info = fit_caltrack_billing(
        baseline_bills,
        baseline_temp,
        fuel=fuel,
        eemeter_compat=eemeter_compat,
        bp_step=bp_step,
        max_p_value=max_p_value,
        intervention_start=intervention_start,
        billing_cycle=billing_cycle,
        timezone=timezone,
        temp_bounds=temp_bounds,
        check_sufficiency=check_sufficiency,
    )
    base = info["frame"]
    raw = _billing_frame(reporting_bills)
    v, n_zero, _ = K.mask_meter_values(raw["energy"], fuel)
    raw["energy"] = v.to_numpy()
    rep = clean_billing_periods(
        raw,
        period="reporting",
        billing_cycle=info["sufficiency"].get("billing_cycle") or billing_cycle,
        eemeter_compat=eemeter_compat,
    )
    if rep.empty or rep["energy"].dropna().empty:
        raise ValueError("no usable reporting-period data")
    bt = billing_temperature(
        rep,
        reporting_temp,
        timezone=timezone,
        eemeter_compat=eemeter_compat,
        temp_bounds=temp_bounds,
    )
    rep = rep.join(bt)
    rows_b = list(base["day_temps"])
    rows_r = list(rep["day_temps"])
    y_b = base["upd"].to_numpy(float)
    yhat_b = _billing_upd(model, rows_b)
    w_b = base["n_days_kept"].to_numpy(float)
    p = model.num_parameters + 1
    fit = _g14_fit(
        y_b, yhat_b, p, interval="monthly", time_index=pd.DatetimeIndex(base["start"]), weights=w_b
    )
    resid = y_b - yhat_b
    from .models import fit_weights

    wn, _scale = fit_weights(w_b)
    wn = np.ones(len(y_b)) if wn is None else wn
    D_b, names, design = _drivers(model, rows_b)
    D_r, _, _ = _drivers(model, rows_r)
    view = _view(
        model,
        D_b,
        names,
        design,
        n=len(y_b),
        p=p,
        s2=float((wn * resid) @ resid) / max(len(y_b) - p, 1),
        rho=fit.rho_lag1,
        weights=w_b,
    )
    from .stats import avoided_energy_savings

    days_r = rep["days"].to_numpy(float)
    upd_r = rep["energy"].to_numpy(float) / days_r
    savings = avoided_energy_savings(
        view,
        D_r,
        upd_r,
        cv_rmse=fit.cv_rmse,
        n_baseline=fit.n,
        p_baseline=p,
        confidence=confidence,
        rho=fit.rho_lag1,
        extrapolation=extrapolation,
        days=days_r,
    )
    pred_r = _billing_upd(model, rows_r) * days_r
    table = pd.DataFrame(
        {
            "start": rep["start"],
            "end": rep["end"],
            "days": days_r,
            "observed": rep["energy"].to_numpy(float),
            "predicted": pred_r,
            "temperature_coverage": rep["coverage"].to_numpy(float),
            "flag_long": rep["flag_long"].to_numpy(bool),
        }
    ).set_index(pd.DatetimeIndex(rep["start"], name="start"))
    table["avoided"] = table["predicted"] - table["observed"]
    used = table["avoided"].notna()
    caveats = list(info["caveats"]) + notes
    if n_zero:
        caveats.append(
            f"{n_zero} reporting electricity bill(s) of exactly 0 were treated as missing"
        )
    n_nc = int((table["observed"].notna() & table["predicted"].isna()).sum())
    if n_nc:
        caveats.append(
            f"{n_nc} reporting bill(s) had hourly temperature for under 90 % of their hours: no "
            "counterfactual, left out of the savings (CT-D-3.3)"
        )
    if rep.attrs.get("n_flag_long"):
        caveats.append(
            f"{rep.attrs['n_flag_long']} reporting bill(s) are longer than the "
            f"{rep.attrs.get('billing_cycle')} limit: flagged, not dropped (CT-D-3.4)"
        )
    if rep.attrs.get("n_off_cycle_combined"):
        caveats.append(
            f"{rep.attrs['n_off_cycle_combined']} off-cycle reporting read(s) under 25 days were "
            "combined with the next bill (CT-D-3.4)"
        )
    savings.caveats.extend(caveats)
    fsu = None
    if used.any() and table.loc[used, "predicted"].sum():
        frac = float(table.loc[used, "avoided"].sum() / table.loc[used, "predicted"].sum())
        fsu = caltrack_fsu(
            granularity="billing",
            cvrmse_adj=model.totals_metrics.get("cvrmse_adj"),
            n_baseline=model.n,
            num_parameters=model.num_parameters,
            rho=model.totals_metrics.get("autocorr_resid"),
            n_reporting=int(used.sum()),
            reporting_months=float(round(table.loc[used, "days"].sum() / K.FSU_DAYS_PER_MONTH)),
            savings_fraction=frac,
            confidence=confidence,
        )
    return CalTRACKResult(
        method="CalTRACK 2.0 billing",
        model=model,
        savings=savings,
        sufficiency=info["sufficiency"],
        fit=fit,
        fsu=fsu,
        eemeter_compat=eemeter_compat,
        reporting=table,
        candidates=info["candidates"],
    )
