"""Chilled-water plant diagnostics: CHWST reset + low-deltaT (PNNL Ch.8).

Two part-load efficiency faults on a chilled-water plant:

1. **No chilled-water supply-temp (CHWST) reset.** Holding CHWST low at part load
   wastes chiller energy (~2% per degF it could be raised). A working reset raises
   CHWST when load/OAT falls; a flat CHWST vs OAT (and CHWST pinned low) = no reset.
2. **Low loop delta-T.** Loop deltaT (return - supply) well below design indicates
   degraded plant efficiency (low-deltaT syndrome): the loop moves a lot of water
   for little heat transfer. PNNL design example ~11.5F, bad < ~8F.

Method: restrict to *plant-running* hours, then regress CHWST on OAT and summarize deltaT.
"Running" comes from the chiller's run status / command when the caller has one (``running=``);
without it, from CHWST sitting in a plausible chilled range -- a weaker proxy: a chiller that
never ran still reads a "chilled" supply temperature when the loop is cold or shared with a
running machine. OAT is typically building-level (pass via the rule layer's ``shared`` channel).

:func:`analyze_chw_tracking` (0.91) asks the other plant question: while the plant runs, does
CHWST actually reach its trended setpoint?

Note: a CHW flow point is intentionally NOT required -- on some buildings it is
dead/untrended, so this diagnostic relies on temperatures, not flow.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .schedules import occupied_mask

__all__ = [
    "CHWPLANT_MEASURES",
    "CHWPlantResult",
    "CHWTrackingResult",
    "analyze_chw_plant",
    "analyze_chw_tracking",
    "chiller_running",
    "chw_tracking_mask",
]

CHWPLANT_MEASURES = ["CHWS_Temp", "CHWR_Temp", "CHWS_SP", "OAT", "PumpSpeed"]


@dataclass
class CHWPlantResult:
    """Chilled-water plant CHWST reset and delta-T diagnostics for one plant."""

    equip: str
    n_running: int  # hours plant judged running
    chwst_median_f: float
    chwst_slope_per_F: float | None  # d(CHWST)/d(OAT); None = not evaluated (no OAT)
    chwst_reset_present: bool | None  # None = could not evaluate (no/insufficient OAT)
    pct_chwst_low: float  # % running hrs CHWST below low threshold
    deltaT_median_f: float
    low_deltaT_pct: float  # % running hrs deltaT < design_min
    design_deltaT_min_f: float
    coverage_start: str
    coverage_end: str
    run_source: str = "temperature"  # "status" (run status / command) | "temperature" (proxy)

    def as_dict(self):
        """Return the result as a plain dict."""
        return asdict(self)


def chiller_running(status: pd.Series | None, *, index=None) -> pd.Series | None:
    """A boolean "chiller running" mask from a run status / command series, or ``None``.

    A resampled status is a duty fraction; the chiller counts as running in an interval when it
    ran for more than half of it (the same rule as the fan gate), so a start-up or shut-down hour
    is not judged as steady running. A status that is never finite gives ``None`` (no signal).
    """
    if status is None:
        return None
    s = pd.to_numeric(status, errors="coerce")
    if index is not None:
        s = s.reindex(index)
    if not s.notna().any():
        return None
    return (s > 0.5).fillna(False)


def _as_run(running, index) -> pd.Series:
    """A run mask on ``index``: a Series is aligned by time, an array taken positionally."""
    if isinstance(running, pd.Series):
        s = running.reindex(index)
    else:
        s = pd.Series(np.asarray(running), index=index)
    return s.where(s.notna(), False).astype(bool)


def _ols_slope(x, y):
    if len(x) < 10 or np.std(x) == 0:
        return float("nan")
    b, _ = np.polyfit(x, y, 1)
    return float(b)


def analyze_chw_plant(
    df: pd.DataFrame,
    equip: str,
    *,
    chwst_running_lo_f: float = 38.0,  # plausible chilled-water supply range...
    chwst_running_hi_f: float = 58.0,  # ...used to gate "plant running" (flow is dead)
    chwst_low_f: float = 46.0,  # CHWST at/below this = held low
    chwst_reset_slope_flat: float = 0.05,
    design_deltaT_min_f: float = 8.0,  # PNNL low-deltaT threshold
    occupied_only: bool = True,
    running=None,
) -> CHWPlantResult | None:
    """Diagnose a chilled-water plant. ``df`` columns are measure names.

    ``running`` (0.91) is an optional boolean Series of run status (see :func:`chiller_running`).
    When given it decides which hours are judged -- a chiller that never ran is judged on no
    hours, whatever its supply temperature reads -- and only sensor garbage (CHWST outside
    30-80F) is dropped. Without it the 38-58F CHWST window below is the (weaker) proxy.

    Thresholds are OUR engineering judgment / PNNL Ch.8 guidance:
      running gate 38-58F  -- CHWST outside this is plant-off or sensor dropout.
      chwst_low_f=46F      -- typical low CHWST design; at/below = "held low".
      reset_slope_flat=.05 -- |d(CHWST)/d(OAT)| below this = effectively no reset.
      design_deltaT_min=8F -- loop deltaT below this = low-deltaT syndrome (Ch.8).
    """
    if "CHWS_Temp" not in df.columns:
        return None
    work = df.copy()
    run = None
    if running is not None:
        run = _as_run(running, df.index)
    if occupied_only:
        occ = occupied_mask(work.index)
        work = work[occ]
        if run is not None:
            run = run[occ]
    sup = work["CHWS_Temp"]
    if run is not None:
        # gate on the chiller's run status; drop only sensor garbage
        gate = run.to_numpy() & sup.between(30.0, 80.0).to_numpy()
        source = "status"
    else:
        # no status: plant-running via plausible CHWST (flow point is unreliable)
        gate = sup.between(chwst_running_lo_f, chwst_running_hi_f).to_numpy()
        source = "temperature"
    work = work[gate]
    n = len(work)
    if n == 0:
        return None
    sup = work["CHWS_Temp"]

    chwst_median = round(float(sup.median()), 1)
    pct_low = round(100.0 * float((sup <= chwst_low_f).mean()), 1)

    # Reset is CHWST-vs-OAT modulation. Without OAT (or without enough overlapping data)
    # we CANNOT evaluate it -- report None, never a confident "no reset" (False).
    if "OAT" in work.columns:
        r = work[["CHWS_Temp", "OAT"]].dropna()
        slope = _ols_slope(r["OAT"].values, r["CHWS_Temp"].values) if len(r) >= 10 else float("nan")
    else:
        slope = float("nan")
    if np.isnan(slope):
        slope_out = None
        reset_present = None  # not evaluated (no/insufficient OAT) -- NOT "no reset"
    else:
        slope_out = round(slope, 3)
        reset_present = bool(abs(slope) >= chwst_reset_slope_flat)

    if "CHWR_Temp" in work.columns:
        dt = (work["CHWR_Temp"] - work["CHWS_Temp"]).dropna()
        dt = dt[dt.between(-2, 40)]  # drop nonsense from sensor dropouts
        dt_median = round(float(dt.median()), 1) if len(dt) else float("nan")
        low_dt_pct = (
            round(100.0 * float((dt < design_deltaT_min_f).mean()), 1) if len(dt) else float("nan")
        )
    else:
        dt_median = low_dt_pct = float("nan")

    return CHWPlantResult(
        equip=equip,
        n_running=n,
        chwst_median_f=chwst_median,
        chwst_slope_per_F=slope_out,
        chwst_reset_present=reset_present,
        pct_chwst_low=pct_low,
        deltaT_median_f=dt_median,
        low_deltaT_pct=low_dt_pct,
        design_deltaT_min_f=float(design_deltaT_min_f),
        coverage_start=str(df.index.min()),
        coverage_end=str(df.index.max()),
        run_source=source,
    )


@dataclass
class CHWTrackingResult:
    """Chilled-water supply temperature against its setpoint over the plant's running hours."""

    equip: str
    run_source: str  # "status" | "temperature" (proxy: CHWST in a chilled range)
    n_running: int  # running samples with both CHWST and its setpoint
    running_hours: float
    above_pct: float | None  # % of running samples with CHWST > setpoint + above_f
    above_hours: float
    above_f: float
    mean_excess_f: float | None  # mean CHWST - setpoint over the running samples
    median_excess_above_f: float | None  # median CHWST - setpoint over the samples above
    chwst_median_f: float | None
    setpoint_median_f: float | None
    deltaT_median_f: float | None  # loop deltaT (return - supply) over the running samples
    deltaT_median_above_f: float | None  # ... over the samples above setpoint
    n_status_on: int | None  # samples the status says running (None without a status)
    coverage_start: str
    coverage_end: str

    def as_dict(self):
        """Return the result as a plain dict."""
        return asdict(self)


def _tracking_frame(df, running, *, settle_intervals: int):
    """The running samples of ``df`` (CHWS_Temp / CHWS_SP present) and where the gate came from."""
    sup, sp = df["CHWS_Temp"], df["CHWS_SP"]
    ok = sup.between(30.0, 80.0) & sp.between(30.0, 70.0)
    if running is not None:
        run = _as_run(running, df.index)
        if settle_intervals > 0:
            # the first interval(s) after a start are pull-down, not steady tracking
            started = run & ~run.shift(1, fill_value=False)
            settling = started.copy()
            for k in range(1, settle_intervals):
                settling |= started.shift(k, fill_value=False)
            run = run & ~settling
        return ok & run, "status"
    return ok & sup.between(38.0, 58.0), "temperature"


def analyze_chw_tracking(
    df: pd.DataFrame,
    equip: str,
    *,
    running=None,
    above_f: float = 3.0,
    settle_intervals: int = 1,
) -> CHWTrackingResult | None:
    """Does chilled-water supply temperature reach its setpoint while the plant runs?

    ``df`` columns are measure names: ``CHWS_Temp`` and ``CHWS_SP`` (required), ``CHWR_Temp``
    (optional, for loop deltaT). ``running`` is a boolean run-status Series
    (:func:`chiller_running`); the first ``settle_intervals`` samples after each start are left
    out as pull-down. Without ``running`` the plant is taken as running when CHWST sits in
    38-58F -- a proxy the caller must caveat. Returns ``None`` when the frame lacks the columns.

    ``above_f`` (default 3F): a healthy chilled-water loop holds supply within about 1F of
    setpoint, and a supply sensor is good to about 0.5F; hourly averaging folds in staging
    transients. Three degrees above setpoint is outside all of that together, so a sample above
    it is a plant that is not making its setpoint, not control noise -- yet a plant supplying 48F
    against a 40F setpoint clears it by a wide margin.
    """
    if "CHWS_Temp" not in df.columns or "CHWS_SP" not in df.columns:
        return None
    from .timegrid import interval_hours

    gate, source = _tracking_frame(df, running, settle_intervals=settle_intervals)
    work = df[gate.to_numpy()]
    n = len(work)
    step = float(interval_hours(df.index)) if len(df.index) > 1 else 1.0
    n_on = None
    if running is not None:
        n_on = int(_as_run(running, df.index).sum())
    excess = work["CHWS_Temp"] - work["CHWS_SP"]
    above = excess > above_f

    def _med(s):
        s = s.dropna()
        return round(float(s.median()), 1) if len(s) else None

    dt = dt_above = None
    if "CHWR_Temp" in work.columns:
        d = work["CHWR_Temp"] - work["CHWS_Temp"]
        d = d[d.between(-2, 40)]
        dt = _med(d)
        dt_above = _med(d[above.reindex(d.index, fill_value=False)])
    return CHWTrackingResult(
        equip=equip,
        run_source=source,
        n_running=n,
        running_hours=round(n * step, 2),
        above_pct=round(100.0 * float(above.mean()), 1) if n else None,
        above_hours=round(float(above.sum()) * step, 2),
        above_f=float(above_f),
        mean_excess_f=round(float(excess.mean()), 2) if n else None,
        median_excess_above_f=_med(excess[above]),
        chwst_median_f=_med(work["CHWS_Temp"]),
        setpoint_median_f=_med(work["CHWS_SP"]),
        deltaT_median_f=dt,
        deltaT_median_above_f=dt_above,
        n_status_on=n_on,
        coverage_start=str(df.index.min()),
        coverage_end=str(df.index.max()),
    )


def chw_tracking_mask(df: pd.DataFrame, *, running=None, above_f: float = 3.0, settle_intervals=1):
    """The boolean "running and CHWST above setpoint + ``above_f``" mask on ``df``'s index."""
    gate, _src = _tracking_frame(df, running, settle_intervals=settle_intervals)
    return (gate & ((df["CHWS_Temp"] - df["CHWS_SP"]) > above_f)).fillna(False)
