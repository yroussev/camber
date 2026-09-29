"""AHU night/weekend setback diagnostic (PNNL Re-tuning Ch.5).

The cheapest large saver: an air handler that runs 24/7 with no night or weekend
setback wastes fan energy (and the heating/cooling to condition air nobody needs).
This flags the supply fan running during *unoccupied* hours.

Unoccupied = the complement of the occupied window: a trended ``Occupancy`` point
when present (it *replaces* the schedule), otherwise the configured schedule
(default weekday 07:00-18:00 -- a generic office assumption; pass ``start_hour`` /
``end_hour`` / ``occupied_days`` for the real one). "Running" can be read from a fan
status point (preferred) or, if only speed is trended, from speed above a small
threshold.

**Duty, not "any-on".** A resampled status point is a *fraction* of the bin the fan ran
(:func:`camber.realio.load_status` averages the step series), so the run percentages
here average that fraction instead of thresholding it at 0.5 -- a fan cycling a third of
every unoccupied hour runs ~33 % of unoccupied time at 1-minute, 15-minute or hourly
resolution alike.

Headline metric: fraction of unoccupied hours the fan is running. A well-scheduled
AHU is near 0%; continuous operation is ~100%. We also report the occupied-vs-
unoccupied run ratio so a partial/ineffective setback is visible.

**A fan cycling to hold a setback is a working setback (0.93, #43).** A unit scheduled off at
night still has to come on when a zone falls to its unoccupied heating setpoint (or rises to its
cooling set-up). Runtime alone cannot tell that apart from a missing setback: in a cold week the
fan may cycle on for half of every night hour. When the runtime test fails, the zone temperature
decides. The setback counts as held when both of these are true:

* the fan *cycles* during unoccupied hours: in the unoccupied clock hours in which it runs, its
  mean duty is below ``max_hold_duty`` (default 90 %). A fan that runs the whole night is not
  holding a setback, whatever the zone does; and
* the zone sits at setback, not at occupied comfort, while the fan runs unoccupied: its median
  lies at least ``min_setback_depth_f`` (default 3 F) below (heating) or above (cooling) the
  occupied reference -- the occupied setpoint when it is trended, otherwise the zone's own occupied
  median. With a known unoccupied setpoint (a trended ``HeatSP``/``CoolSP`` read in unoccupied
  hours, or a configured ``unoccupied_heat_sp_f``/``unoccupied_cool_sp_f``), a zone on the setback
  side of the midpoint between the reference and that setpoint also counts, and the two setpoints
  must be at least ``min_setback_depth_f`` apart: a trended setpoint that never sets back vetoes
  the test. The cooling side always needs a known unoccupied setpoint: a zone warmer at night
  while the fan runs can be a unit heating it at night rather than one holding a set-up.

The duty is read within each clock hour, so it needs a sub-hourly status (or the duty-resampled
series :func:`camber.realio.load_status` produces); a fan that runs whole hours or whole nights
is not cycling, whatever the zone does.

The zone temperature is ``ZoneTemp`` when mapped on the unit. Otherwise the return-air
temperature stands in, read only while the fan runs; the result says which one was used.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

from .schedules import effective_occupied_mask

__all__ = [
    "SETBACK_MEASURES",
    "SetbackResult",
    "analyze_setback",
]

SETBACK_MEASURES = [
    "SupplyFanStatus",
    "SupplyFanSpeed",
    "Occupancy",
    "ZoneTemp",
    "ReturnAir",
    "HeatSP",
    "CoolSP",
]


@dataclass
class SetbackResult:
    """Unoccupied-setback diagnostics: fan runtime occupied vs unoccupied."""

    equip: str
    n_occupied: int
    n_unoccupied: int
    fan_run_occupied_pct: float  # % occupied hrs fan running (sanity: should be high)
    fan_run_unoccupied_pct: float  # % unoccupied hrs fan running (the fault metric)
    setback_effective: bool  # unoccupied run immaterial, or materially below occupied run
    coverage_start: str
    coverage_end: str
    # unoccupied / occupied run ratio (None when the fan never ran occupied); provisional
    unoccupied_to_occupied_ratio: float | None = None
    # the absolute unoccupied-runtime floor the verdict used (%); provisional
    min_unoccupied_run_pct: float | None = None
    # --- 0.93 (#43): the held-setback test (provisional) ---
    # "runtime" (the fan-runtime verdict decided) or "held_setback" (the fan ran unoccupied, but
    # cycled to hold the zone at setback)
    setback_basis: str = "runtime"
    # mean fan duty (%) in the unoccupied clock hours in which the fan ran; None if it never ran
    unoccupied_duty_when_running_pct: float | None = None
    zone_temp_source: str | None = None  # "ZoneTemp" | "ReturnAir" | None (no zone signal)
    zone_temp_unoccupied_f: float | None = None  # median while the fan runs unoccupied
    zone_temp_occupied_f: float | None = None  # median while the fan runs occupied
    unoccupied_heat_sp_f: float | None = None  # the unoccupied heating setpoint used (data/config)
    unoccupied_cool_sp_f: float | None = None  # the unoccupied cooling setpoint used (data/config)
    held_side: str | None = None  # "heating" | "cooling" when the setback was held

    def as_dict(self):
        """Return the result as a plain dict."""
        return asdict(self)


def _running(work: pd.DataFrame, speed_thr: float):
    """Fan-running *fraction* per sample (0..1): status duty (preferred) or speed > threshold.

    A status column may hold a resampled duty fraction (e.g. 0.33 = on a third of the bin), so it
    is clipped to [0, 1] and averaged, never re-thresholded at 0.5 (which would make the verdict
    depend on the resample interval). NaN samples are dropped from the average, not read as off.
    """
    if "SupplyFanStatus" in work.columns and work["SupplyFanStatus"].notna().any():
        return work["SupplyFanStatus"].astype(float).clip(0.0, 1.0)
    if "SupplyFanSpeed" in work.columns:
        spd = work["SupplyFanSpeed"]
        return (spd > speed_thr).astype(float).where(spd.notna())
    return None


def _median(s) -> float | None:
    s = pd.Series(s).dropna()
    return round(float(s.median()), 2) if len(s) else None


def _hourly_duty_when_running(run: pd.Series, unocc: pd.Series) -> float | None:
    """Mean duty (0..1) over the unoccupied clock hours in which the fan ran at all."""
    r = run.where(unocc)
    if not isinstance(r.index, pd.DatetimeIndex) or r.notna().sum() == 0:
        return None
    hourly = r.resample("1h").mean().dropna()
    active = hourly[hourly > 0.05]  # a stray minute or two is not "running this hour"
    return float(active.mean()) if len(active) else None


def _held_side(
    zone_un, zone_occ, *, occ_sp, unocc_sp, depth, heating: bool
) -> tuple[bool, float | None]:
    """Is the zone at setback on one side (heating: colder; cooling: warmer)?

    Returns ``(held, unocc_sp_used)``. The occupied reference is the occupied setpoint, else the
    zone's occupied median. The zone is held at setback when it sits ``depth`` or more beyond that
    reference. With an unoccupied setpoint, the setpoints must themselves be ``depth`` apart (a
    trended setpoint with no setback in it vetoes the test), and a zone on the setback side of the
    midpoint between reference and setpoint also counts: a return-air or averaged zone signal sits
    above the coldest zone, the one the fan cycles to hold.
    """
    sign = 1.0 if heating else -1.0  # heating: setback is colder (reference - zone > 0)
    ref = occ_sp if occ_sp is not None else zone_occ
    if zone_un is None or ref is None:
        return False, unocc_sp
    deep = sign * (ref - zone_un) >= depth
    if unocc_sp is not None:
        if sign * (ref - unocc_sp) < depth:
            return False, unocc_sp  # no real setback between the two setpoints
        mid = (ref + unocc_sp) / 2.0
        return bool(deep or sign * (mid - zone_un) >= 0.0), unocc_sp
    return bool(deep), None


def analyze_setback(
    df: pd.DataFrame,
    equip: str,
    *,
    speed_thr: float = 5.0,  # fan speed above this counts as running
    setback_ratio: float = 0.5,  # unoccupied run < this * occupied run == effective
    start_hour: float = 7,
    end_hour: float = 18,
    occupied_days=(0, 1, 2, 3, 4),
    min_unoccupied_run_pct: float = 5.0,
    unoccupied_heat_sp_f: float | None = None,
    unoccupied_cool_sp_f: float | None = None,
    min_setback_depth_f: float = 3.0,
    max_hold_duty: float = 0.9,
) -> SetbackResult | None:
    """Detect missing night/weekend setback for one AHU.

    ``setback_ratio`` is OUR judgment threshold: a setback is "effective" only if
    unoccupied run fraction is below half the occupied run fraction. ``min_unoccupied_run_pct``
    is an absolute floor: an unoccupied run below it (default 5 % of unoccupied time) is
    immaterial, so the setback counts as effective whatever the ratio -- a nearly idle unit
    that ran a few scattered hours is not "missing" its setback. ``speed_thr``
    is the run deadband when only fan speed is available. A populated ``Occupancy`` column
    replaces the ``start_hour``/``end_hour``/``occupied_days`` schedule.

    When the runtime test fails, the held-setback test (module docstring) can still find the
    setback effective: the fan cycles (``max_hold_duty``, a 0..1 fraction) to hold the zone
    (``ZoneTemp``, else ``ReturnAir``) at its unoccupied setpoint -- ``HeatSP``/``CoolSP`` in
    unoccupied hours, else ``unoccupied_heat_sp_f``/``unoccupied_cool_sp_f`` -- or, with no
    setpoint, at least ``min_setback_depth_f`` beyond its occupied temperature.
    """
    run = _running(df, speed_thr)
    if run is None:
        return None
    occ = effective_occupied_mask(
        df.index,
        occ=df["Occupancy"] if "Occupancy" in df.columns else None,
        start_hour=start_hour,
        end_hour=end_hour,
        days=occupied_days,
    )
    have = run.notna()
    occ = occ & have
    unocc = ~occ & have
    n_occ = int(occ.sum())
    n_un = int(unocc.sum())
    if n_un == 0:
        return None

    occ_run = round(100.0 * float(run[occ].mean()), 2) if n_occ else 0.0
    un_run = round(100.0 * float(run[unocc].mean()), 2)
    floor = float(min_unoccupied_run_pct)
    ratio_ok = un_run < setback_ratio * occ_run if occ_run > 0 else False
    effective = un_run < floor or ratio_ok
    ratio = round(un_run / occ_run, 3) if occ_run > 0 else None

    # --- 0.93 (#43): the held-setback test, only when the runtime test failed
    basis, side = "runtime", None
    duty = _hourly_duty_when_running(run, unocc)
    src = next(
        (c for c in ("ZoneTemp", "ReturnAir") if c in df.columns and df[c].notna().any()), None
    )
    z_un = z_occ = None
    heat_un_sp = unoccupied_heat_sp_f
    cool_un_sp = unoccupied_cool_sp_f
    if src is not None:
        zone = df[src].astype(float)
        # read while the fan runs: return air only reads the zones when the fan ran most of the
        # sample; a zone sensor reads the space whenever the fan ran at all
        running = run > (0.05 if src == "ZoneTemp" else 0.5)
        z_un = _median(zone[unocc & running])
        z_occ = _median(zone[occ & running])
    if not effective and src is not None and duty is not None and duty < max_hold_duty:
        sides = []
        for col, cfg_sp, heating in (
            ("HeatSP", unoccupied_heat_sp_f, True),
            ("CoolSP", unoccupied_cool_sp_f, False),
        ):
            sp = df[col].astype(float) if col in df.columns else None
            occ_sp = _median(sp[occ]) if sp is not None else None
            un_sp = _median(sp[unocc]) if sp is not None else None
            un_sp = un_sp if un_sp is not None else cfg_sp
            if not heating and un_sp is None:
                # a zone warmer at night with the fan running may be a unit heating it at night,
                # not one holding a cooling set-up: the cooling side needs a known setpoint
                continue
            held, used = _held_side(
                z_un,
                z_occ,
                occ_sp=occ_sp,
                unocc_sp=un_sp,
                depth=min_setback_depth_f,
                heating=heating,
            )
            if heating:
                heat_un_sp = used
            else:
                cool_un_sp = used
            if held:
                sides.append("heating" if heating else "cooling")
        if sides:
            effective, basis, side = True, "held_setback", sides[0]

    return SetbackResult(
        equip=equip,
        n_occupied=n_occ,
        n_unoccupied=n_un,
        fan_run_occupied_pct=occ_run,
        fan_run_unoccupied_pct=un_run,
        setback_effective=bool(effective),
        coverage_start=str(df.index.min()),
        coverage_end=str(df.index.max()),
        unoccupied_to_occupied_ratio=ratio,
        min_unoccupied_run_pct=floor,
        setback_basis=basis,
        unoccupied_duty_when_running_pct=round(100.0 * duty, 1) if duty is not None else None,
        zone_temp_source=src,
        zone_temp_unoccupied_f=z_un,
        zone_temp_occupied_f=z_occ,
        unoccupied_heat_sp_f=heat_un_sp,
        unoccupied_cool_sp_f=cool_un_sp,
        held_side=side,
    )
