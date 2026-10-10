"""Hot-water plant diagnostics: boiler summer-lockout, HW-temp reset, loop low-deltaT.

Per PNNL Re-tuning Ch.8 ("Boiler Lockout in Summer", "Boiler Trend Data
Analysis"): if a boiler serves only comfort reheat, it should be locked out in hot
weather, and hot-water supply temp should be reset down with load/OAT ("why heat
with 180F water when 80F will do"). Running the boiler in cooling weather is the
gas side of the simultaneous-heat/overcool problem.

Two indicators:
1. **Summer-lockout violation** -- fraction of boiler-running hours at OAT above a
   *climate-dependent* lockout threshold. That threshold is the one genuinely
   climate-sensitive knob here (a mild-coastal zone locks out lower than a hot
   desert), so it is an injected parameter, not a constant -- a climate-zone
   config supplies the per-zone value.
2. **HW-supply-temp reset** -- slope of HW supply temp vs OAT over boiler-running
   hours. A working reset lowers HWS as OAT rises (negative slope); a flat slope
   means no reset (heating with hotter water than needed).

OAT is typically a building-level point, so callers pass it via the rule layer's
``shared`` channel rather than expecting it per-plant.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .schedules import occupied_mask

__all__ = [
    "HWPLANT_MEASURES",
    "HWPlantResult",
    "HWPumpLockoutResult",
    "LOCKOUT_FAULT_PCT",
    "LOCKOUT_WARN_PCT",
    "SUMMER_LOCKOUT_OAT_F",
    "analyze_hw_plant",
    "analyze_hw_pump_lockout",
    "lockout_severity",
]

# Roles consumed (kept as strings here to avoid a hard import cycle with model;
# the rule wrapper maps Role -> these legacy column names).
HWPLANT_MEASURES = ["BoilerStatus", "HWS_Temp", "HWR_Temp", "HW_DiffPress", "OAT"]

#: The default warm-weather lockout (°F) of ``boiler_summer_lockout`` and
#: ``hw_pump_summer_lockout``: a generic mild-climate value, to be replaced by the site's own.
SUMMER_LOCKOUT_OAT_F = 65.0
#: Share (%) of running hours above the lockout at which a lockout rule warns, and faults.
LOCKOUT_WARN_PCT = 5.0
LOCKOUT_FAULT_PCT = 20.0


def lockout_severity(summer_run_pct) -> str:
    """``ok`` / ``warn`` / ``fault`` for a share of running hours above the lockout (``info``
    when the share was not evaluated, ``None``). Shared by both warm-weather lockout rules."""
    if summer_run_pct is None:
        return "info"
    if summer_run_pct >= LOCKOUT_FAULT_PCT:
        return "fault"
    return "warn" if summer_run_pct >= LOCKOUT_WARN_PCT else "ok"


@dataclass
class HWPlantResult:
    """Hot-water plant runtime, summer-lockout, and HWS reset diagnostics."""

    equip: str
    n_considered: int  # occupied hours with boiler data
    boiler_running_pct: float  # % of occupied hours boiler is running
    n_running: int
    summer_run_pct: float | None  # % running hrs at OAT>lockout; None = no OAT (not evaluated)
    lockout_oat_f: float  # the (climate-dependent) threshold used
    hws_median_f: float
    hws_slope_per_F: float | None  # d(HWS)/d(OAT); None = not evaluated (no/insufficient OAT)
    hws_reset_present: bool | None  # None = could not evaluate (no/insufficient OAT or no HWS)
    deltaT_median_f: float  # median loop dT (HWS - HWR) over running hours
    low_deltaT_pct: float  # % running hrs with loop dT < design_deltaT_min_f
    design_deltaT_min_f: float  # the design-minimum loop dT used
    dp_median: float
    coverage_start: str
    coverage_end: str
    # 0.103 (#133): the warmest outdoor temperature at which the boiler ran (None = no OAT or no
    # running hours): where the plant actually stops firing, to compare with the lockout
    max_oat_running_f: float | None = None

    def as_dict(self):
        """Return the result as a plain dict."""
        return asdict(self)


def _max_running_oat(work: pd.DataFrame, running: pd.Series) -> float | None:
    """The warmest OAT over the running samples of ``work`` (``None`` when there is none)."""
    if "OAT" not in work.columns or not bool(running.any()):
        return None
    oat = pd.to_numeric(work.loc[running, "OAT"], errors="coerce").dropna()
    return round(float(oat.max()), 1) if len(oat) else None


def _ols_slope(x: np.ndarray, y: np.ndarray):
    if len(x) < 10 or np.std(x) == 0:
        return float("nan")
    b, _ = np.polyfit(x, y, 1)
    return float(b)


def analyze_hw_plant(
    df: pd.DataFrame,
    equip: str,
    *,
    summer_lockout_oat_f: float = 65.0,  # climate-dependent; inject per climate zone
    hws_reset_slope_flat: float = 0.05,  # |slope| below this = effectively no reset
    design_deltaT_min_f: float = 20.0,  # HW loop dT below this = low-deltaT (overpumping)
    occupied_only: bool = True,
) -> HWPlantResult | None:
    """Diagnose a hot-water plant. ``df`` columns are measure names (see
    HWPLANT_MEASURES); ``BoilerStatus`` is 0/1, ``OAT`` building outdoor temp.

    ``summer_lockout_oat_f`` is the climate-zone knob: above this OAT a comfort-only
    boiler should be off. Default 65F is a generic mild threshold; a hot-desert zone
    (e.g. CA CZ15) would set this higher, a cool zone lower.
    """
    if "BoilerStatus" not in df.columns:
        return None
    work = df.copy()
    if occupied_only:
        work = work[occupied_mask(work.index)]
    work = work.dropna(subset=["BoilerStatus"])
    n = len(work)
    if n == 0:
        return None

    running = work["BoilerStatus"] > 0.5
    n_run = int(running.sum())
    boiler_running_pct = round(100.0 * n_run / n, 2)

    # summer-lockout violation (needs OAT). Without OAT we CANNOT evaluate it --
    # report None, never a confident 0% ("clean, locked out in summer"). When the
    # boiler never runs (n_run == 0) there are trivially no hot-weather running hours,
    # so 0% is an honest evaluated answer.
    if "OAT" not in work.columns:
        summer_run_pct = None
    elif n_run:
        oat_run = work.loc[running, "OAT"].dropna()
        summer_run_pct = (
            round(100.0 * float((oat_run > summer_lockout_oat_f).mean()), 2)
            if len(oat_run)
            else None  # OAT present but all-NaN over running hours -> not evaluated
        )
    else:
        summer_run_pct = 0.0

    # HW-supply-temp reset (slope vs OAT over running hours)
    if "HWS_Temp" in work.columns:
        hws = work["HWS_Temp"].dropna()
        hws_median = round(float(hws.median()), 1) if len(hws) else float("nan")
        if "OAT" in work.columns and n_run:
            r = work.loc[running, ["HWS_Temp", "OAT"]].dropna()
            slope = (
                _ols_slope(r["OAT"].values, r["HWS_Temp"].values) if len(r) >= 10 else float("nan")
            )
        else:
            slope = float("nan")
    else:
        hws_median = slope = float("nan")
    # Reset is HWS-vs-OAT modulation. Without OAT/HWS (or enough overlap) we CANNOT
    # evaluate it -- report None, never a confident "no reset" (False).
    if np.isnan(slope):
        slope_out = None
        reset_present = None  # not evaluated (no/insufficient OAT or no HWS)
    else:
        slope_out = round(slope, 3)
        reset_present = bool(slope < -hws_reset_slope_flat)

    # HW loop delta-T (HWS - HWR) over running hours. Low delta-T = overpumping:
    # the loop circulates a lot of water for little heat transfer (PNNL Ch.8, the
    # heating-side analogue of the chilled-water low-deltaT syndrome).
    deltaT_median = float("nan")
    low_dt_pct = float("nan")
    if {"HWS_Temp", "HWR_Temp"} <= set(work.columns) and n_run:
        d = work.loc[running, ["HWS_Temp", "HWR_Temp"]].dropna()
        dt = d["HWS_Temp"] - d["HWR_Temp"]
        dt = dt[(dt > -2) & (dt < 120)]  # physical-ish guard
        if len(dt) >= 10:
            deltaT_median = round(float(dt.median()), 1)
            low_dt_pct = round(100.0 * float((dt < design_deltaT_min_f).mean()), 1)

    dp_median = (
        round(float(work["HW_DiffPress"].median()), 2)
        if "HW_DiffPress" in work.columns and work["HW_DiffPress"].notna().any()
        else float("nan")
    )

    return HWPlantResult(
        equip=equip,
        n_considered=n,
        boiler_running_pct=boiler_running_pct,
        n_running=n_run,
        summer_run_pct=summer_run_pct,
        lockout_oat_f=float(summer_lockout_oat_f),
        hws_median_f=hws_median,
        hws_slope_per_F=slope_out,
        hws_reset_present=reset_present,
        deltaT_median_f=deltaT_median,
        low_deltaT_pct=low_dt_pct,
        design_deltaT_min_f=float(design_deltaT_min_f),
        dp_median=dp_median,
        coverage_start=str(df.index.min()),
        coverage_end=str(df.index.max()),
        max_oat_running_f=_max_running_oat(work, running),
    )


# --------------------------------------------------------------------------------------------- #
# 0.103 (#132): the hot-water pump's warm-weather lockout                                        #
# --------------------------------------------------------------------------------------------- #


@dataclass
class HWPumpLockoutResult:
    """Hot-water pump running hours against the warm-weather lockout (PNNL Re-tuning Ch.8)."""

    equip: str
    n_considered: int  # occupied samples with a pump run reading
    pump_running_pct: float  # % of those samples the pump runs
    n_running: int
    summer_run_pct: float | None  # % running samples at OAT > lockout; None = not evaluated
    lockout_oat_f: float
    max_oat_running_f: float | None  # warmest OAT at which the pump ran (None = no OAT / never)
    # % of the pump's samples above the lockout in which the boiler did not fire (None = no
    # boiler run signal, or no warm running samples): the pump left running on its own
    boiler_off_pct: float | None
    coverage_start: str
    coverage_end: str

    def as_dict(self):
        """Return the result as a plain dict."""
        return asdict(self)


def analyze_hw_pump_lockout(
    df: pd.DataFrame,
    equip: str,
    *,
    summer_lockout_oat_f: float = SUMMER_LOCKOUT_OAT_F,
    occupied_only: bool = True,
) -> HWPumpLockoutResult | None:
    """Share of a hot-water pump's running hours at OAT above the warm-weather lockout.

    ``df`` columns: ``PumpRun`` (0/1, the pump running; missing = no reading), ``OAT`` and,
    optionally, ``BoilerRun`` (0/1, the boiler firing). The same semantics as the boiler's check
    in :func:`analyze_hw_plant`: occupied samples only (the generic weekday schedule), "above"
    means OAT strictly greater than the lockout, and without OAT the share is ``None`` (not
    evaluated), never a confident 0 %. A pump that never runs scores an honest 0 %.
    """
    if "PumpRun" not in df.columns:
        return None
    work = df.copy()
    if occupied_only:
        work = work[occupied_mask(work.index)]
    work = work.dropna(subset=["PumpRun"])
    n = len(work)
    if n == 0:
        return None
    running = work["PumpRun"] > 0.5
    n_run = int(running.sum())
    boiler_off = None
    if "OAT" not in work.columns:
        summer = None
    elif n_run:
        oat_run = work.loc[running, "OAT"].dropna()
        warm = oat_run > summer_lockout_oat_f
        summer = round(100.0 * float(warm.mean()), 2) if len(oat_run) else None
        if "BoilerRun" in work.columns and bool(warm.any()):
            fire = work.loc[warm[warm].index, "BoilerRun"].dropna()
            if len(fire):
                boiler_off = round(100.0 * float((fire <= 0.5).mean()), 2)
    else:
        summer = 0.0
    return HWPumpLockoutResult(
        equip=equip,
        n_considered=n,
        pump_running_pct=round(100.0 * n_run / n, 2),
        n_running=n_run,
        summer_run_pct=summer,
        lockout_oat_f=float(summer_lockout_oat_f),
        max_oat_running_f=_max_running_oat(work, running),
        boiler_off_pct=boiler_off,
        coverage_start=str(df.index.min()),
        coverage_end=str(df.index.max()),
    )
