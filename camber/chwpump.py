"""Variable-speed pump operation diagnostic: riding-the-curve and VFD-minimum (PNNL Ch.8).

Variable-speed distribution pumps (chilled- or hot-water) should slow at part load,
with the loop differential-pressure (DP) setpoint reset down as valves open, so the
pumps only work as hard as the load needs. Because pump power varies with roughly the
cube of speed, the speed *distribution* tells the story two ways:

* **Riding the curve** -- pinned near full speed at part load: no effective DP reset
  (or a DP setpoint held too high), wasting cube-law energy.
* **VFD minimum / oversized** -- pinned near the minimum speed most of the time: the
  pump (or its DP setpoint) is larger than the loop needs, so there is room to lower
  the minimum speed, trim the impeller, or downsize.

Operates on one pump-speed series; loop-agnostic, so the same function serves CHW and
HW pumps (see the ``analyze_pump`` alias).

**Running comes from the run status when there is one (0.105, #180).** A speed point does not
always fall to zero when the pump stops: on the ``lbnl-chiller`` plant the secondary pump's speed
holds the DP loop's last output (0.345) through every stopped hour, which reads as a pump idling at
its VFD floor. With a ``PumpStatus`` column only the samples the pump ran for most of (status duty
above 0.5, the #143 duty semantics: a resampled status is the share of the bin it was on) are
judged, and the VFD floor is learned from those samples. Without one, running is speed above
``run_thr`` as before, and the rule wrappers say a parked speed cannot be ruled out.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

from .schedules import occupied_mask

__all__ = [
    "CHWPumpResult",
    "analyze_chw_pump",
    "analyze_pump",
    "learn_vfd_floor",
]

#: ``near_min_pct="auto"`` falls back to this band when no VFD floor plateau is found, and never
#: uses a band below it
_DEFAULT_NEAR_MIN_PCT = 25.0


def learn_vfd_floor(
    running: pd.Series,
    *,
    floor_tol_pct: float = 1.0,
    min_share: float = 0.10,
    min_spread_pct: float = 20.0,
) -> float | None:
    """The pump's VFD minimum speed, learned from a plateau in its running speeds, or ``None``.

    A VFD with a minimum speed parks there whenever the load needs less, so the lowest speeds
    pile up on one value. The candidate floor is the 2nd percentile of the running speed; it is
    accepted when at least ``min_share`` of the running samples sit within ``floor_tol_pct`` of
    it (a plateau, not a tail) and the 90th percentile is at least ``min_spread_pct`` above it
    (the pump modulates above the floor, so the plateau is a minimum, not a fixed speed).
    """
    s = pd.to_numeric(running, errors="coerce").dropna()
    if len(s) == 0:
        return None
    floor = float(s.quantile(0.02))
    share = float(((s - floor).abs() <= floor_tol_pct).mean())
    if share < min_share or float(s.quantile(0.90)) < floor + min_spread_pct:
        return None
    return round(floor, 1)


@dataclass
class CHWPumpResult:
    """Variable-speed pump speed distribution + DP-setpoint reset for one pump."""

    equip: str
    n_running: int
    median_speed_pct: float
    pct_running_near_full: float  # % running hrs speed >= near_full_pct (riding the curve)
    pct_running_near_min: float  # % running hrs speed <= near_min_pct (oversized / VFD min)
    median_dp_sp: float  # median DP setpoint (if available)
    dp_sp_reset_present: bool | None  # None = not evaluated (no/insufficient DP setpoint)
    coverage_start: str
    coverage_end: str
    # 0.98 (#86): where the near-minimum band came from
    near_min_band_pct: float = 25.0  # the band actually used: speed <= this is "near min"
    near_min_source: str = "fixed"  # "fixed" | "learned" (VFD floor found) | "default" (not found)
    vfd_floor_pct: float | None = None  # the learned VFD floor (None when not learned)
    # 0.105 (#180): what "running" was read from, and (with a status) how many samples the speed
    # alone would have counted as running while the status says the pump was stopped
    run_source: str = "speed"  # "status" (PumpStatus duty > 0.5 and speed > run_thr) | "speed"
    n_stopped_speed_above_run: int | None = None  # None without a status

    def as_dict(self):
        """Return the result as a plain dict."""
        return asdict(self)


def analyze_chw_pump(
    df: pd.DataFrame,
    equip: str,
    *,
    run_thr: float = 5.0,  # speed above this == pump running
    near_full_pct: float = 90.0,  # speed at/above this == effectively full speed
    near_min_pct: float | str = 25.0,  # speed at/below this == effectively at the VFD minimum
    dp_sp_flat_std: float = 0.5,  # DP-SP std below this == flat (no reset)
    occupied_only: bool = False,  # pumps run on load, not occupancy; default all hrs
    floor_tol_pct: float = 1.0,  # "auto": a speed within this of the learned floor is at it
) -> CHWPumpResult | None:
    """Diagnose pump speed distribution / DP reset. ``df`` has 'PumpSpeed' (and optional
    'DiffPressSP' and 'PumpStatus'). Speeds are %; running = speed > run_thr, and, when
    'PumpStatus' has any reading, also status > 0.5 (0.105, #180).

    ``PumpStatus`` is a 0/1 run status or, on a resampled trend, its duty (0..1, the share of the
    bin the pump ran). A sample counts as running when the duty is above 0.5 -- the pump ran for
    most of it -- as :func:`camber.schedules.fan_on_mask` and the ``n_running`` of
    :func:`camber.plant.analyze_hw_pump_lockout` read a duty; a missing status reading counts as
    not running. The speed distribution, the learned VFD floor and the DP-setpoint reset are then
    read over those samples only, so a speed point that parks at its last value while the pump is
    stopped is not read as running at the floor. Without a status every sample above ``run_thr``
    counts, exactly as before 0.105.

    Thresholds are OUR judgment / PNNL Ch.8: near_full_pct=90 (a VFD pinned >=90% is
    effectively full speed, no cube-law benefit); near_min_pct=25 (pinned this low
    most of the time means the pump/DP setpoint is oversized); dp_sp_flat_std=0.5 (a DP
    setpoint that barely moves is not being reset).

    ``near_min_pct="auto"`` (0.98, #86) learns the pump's VFD floor from its running speeds
    (:func:`learn_vfd_floor`) and counts a speed at or below ``max(25, floor + floor_tol_pct)``
    as near the minimum, so a pump whose drive bottoms out above 25 % is still seen parked at its
    floor; without a clear floor it falls back to 25.
    """
    if "PumpSpeed" not in df.columns:
        return None
    work = df.copy()
    if occupied_only:
        work = work[occupied_mask(work.index)]
    spd = work["PumpSpeed"].dropna()
    run_source, n_parked, on = "speed", None, None
    if "PumpStatus" in work.columns:
        status = pd.to_numeric(work["PumpStatus"], errors="coerce")
        if status.notna().any():
            # 0.105 (#180): duty > 0.5 is running (#143); a missing reading is not
            on = (status > 0.5).fillna(False)
            stopped = ~on.reindex(spd.index, fill_value=False)
            n_parked = int((spd[stopped] > run_thr).sum())
            spd = spd[~stopped]
            run_source = "status"
    running = spd[spd > run_thr]
    n = len(running)
    if n == 0:
        return None
    median_speed = round(float(running.median()), 1)
    pct_full = round(100.0 * float((running >= near_full_pct).mean()), 1)
    floor = None
    if near_min_pct == "auto":
        floor = learn_vfd_floor(running, floor_tol_pct=floor_tol_pct)
        if floor is None:
            band, band_source = _DEFAULT_NEAR_MIN_PCT, "default"
        else:
            band, band_source = max(_DEFAULT_NEAR_MIN_PCT, floor + floor_tol_pct), "learned"
    elif isinstance(near_min_pct, str):
        raise ValueError(f"near_min_pct must be a number or 'auto', not {near_min_pct!r}")
    else:
        band, band_source = float(near_min_pct), "fixed"
    pct_min = round(100.0 * float((running <= band).mean()), 1)

    # DP-setpoint reset needs a usable DP setpoint. Without it (or too few samples) we
    # CANNOT evaluate reset -- report None, never a confident "flat / no reset" (False).
    if "DiffPressSP" in work.columns:
        sp = work["DiffPressSP"].dropna()
        if on is not None:  # 0.105 (#180): the setpoint the pump ran against, not a stopped one
            sp = sp[on.reindex(sp.index, fill_value=False)]
        sp = sp[sp > 0]
        if len(sp) >= 10:
            dp_std = float(sp.std())
            dp_med = round(float(sp.median()), 2)
            dp_reset = bool(dp_std >= dp_sp_flat_std)
        else:
            dp_med, dp_reset = float("nan"), None  # DP setpoint too thin -> not evaluated
    else:
        dp_med, dp_reset = float("nan"), None  # no DP setpoint -> not evaluated

    return CHWPumpResult(
        equip=equip,
        n_running=n,
        median_speed_pct=median_speed,
        pct_running_near_full=pct_full,
        pct_running_near_min=pct_min,
        median_dp_sp=dp_med,
        dp_sp_reset_present=dp_reset,
        coverage_start=str(df.index.min()),
        coverage_end=str(df.index.max()),
        near_min_band_pct=round(float(band), 1),
        near_min_source=band_source,
        vfd_floor_pct=floor,
        run_source=run_source,
        n_stopped_speed_above_run=n_parked,
    )


# Loop-agnostic alias: the diagnostic operates on a generic 'PumpSpeed' series, so the
# same function serves chilled-water and hot-water distribution pumps.
analyze_pump = analyze_chw_pump


def _check_near_min(near_min_pct):
    """Validate a rule's ``near_min_pct``: a number, or ``"auto"``."""
    if near_min_pct == "auto":
        return "auto"
    if isinstance(near_min_pct, bool) or not isinstance(near_min_pct, (int, float)):
        raise ValueError(f"near_min_pct must be a number or 'auto', not {near_min_pct!r}")
    return float(near_min_pct)


#: the caveat a pump rule carries when running is read from speed alone (0.105, #180)
_NO_STATUS_CAVEAT = (
    "no pump run status (pump_status) mapped: running is read from speed > 5 %, so hours a "
    "stopped pump's speed point holds its last value (some drives and simulations park it at "
    "the loop's last output) cannot be ruled out and would count as running near the minimum"
)


def _run_caveats(res: CHWPumpResult) -> list:
    """The caveat(s) on how running was read (0.105, #180)."""
    if res.run_source == "speed":
        return [_NO_STATUS_CAVEAT]
    if res.n_stopped_speed_above_run:
        return [
            f"{res.n_stopped_speed_above_run} samples read a speed above 5 % while the run status "
            "says the pump was stopped (a parked speed point); judged as stopped"
        ]
    return []


def _floor_note(res: CHWPumpResult) -> str:
    """The summary's note on where the near-minimum band came from ("" for a fixed band)."""
    if res.near_min_source == "learned":
        return f", learned VFD floor {res.vfd_floor_pct:g}%"
    if res.near_min_source == "default":
        return ", no VFD floor plateau found"
    return ""
