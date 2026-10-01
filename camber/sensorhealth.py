"""Sensor health / data-trust layer (capability-map foundations).

Sensor faults are not equipment faults: a drifting, stuck, railed, or out-of-range
sensor will make a perfectly healthy AHU *look* broken (or hide a real fault). This
layer scores how much each point can be trusted, so the diagnostics can lean on good
data and decline to fire on bad data.

It builds on :mod:`camber.ingest.quality` (coverage, gaps, flatline, robust outliers,
a composite score) and adds the pieces that need to know what a point *means*:

- **Role-aware physical bounds** -- a temperature reading of -999 or a valve at 5000%
  is not a statistical outlier, it is physically impossible; per-role plausible ranges
  catch the BAS error-sentinel and unit-scaling failures the robust test misses.
- **Cross-sensor physical consistency** -- e.g. mixed-air temperature must lie between
  outdoor- and return-air temperature (it is a blend of the two); a persistent
  violation means a temp sensor is miscalibrated or swapped.
- **A per-role trust roll-up + gate** -- combine the above into a trust score and
  verdict per role, and expose :func:`trusted_roles` so a rule runner can withhold a
  diagnostic whose inputs it cannot trust.
- **More cross-sensor physics** -- the flow-weighted mixing balance
  (:func:`mixing_flow_consistency`) and zone-vs-outdoor CO2 (:func:`co2_outdoor_consistency`).
- **Provenance screens** -- data that is plausible but was never measured: a point that is a copy
  of another (:func:`copied_signal_consistency`), gap-filled / imputed stretches
  (:func:`gapfill_signature`), one role implausibly identical across units
  (:func:`cross_unit_identity`), and a percent signal mapped to a cfm role
  (:func:`percent_scale_suspect`).

See docs/SENSOR-HEALTH.md for what each check can and cannot know.

Everything is our own method over public physical reasoning; pandas + numpy only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .ingest.quality import assess
from .model.roles import STATUS_ROLES, Role
from .units import PERCENT_ROLES

__all__ = [
    "PHYSICAL_BOUNDS",
    "range_violation_frac",
    "SensorTrust",
    "sensor_trust",
    "FAN_GATED_ROLES",
    "PLANT_GATED_ROLES",
    "PLANT_SETTLE",
    "plant_gates",
    "frame_sensor_health",
    "frame_checks",
    "STUCK_HOURS",
    "trusted_roles",
    "untrusted_roles",
    "ConsistencyResult",
    "mixing_consistency",
    "mixing_flow_consistency",
    "copied_signal_consistency",
    "gapfill_signature",
    "cross_unit_identity",
    "co2_outdoor_consistency",
    "percent_scale_suspect",
]

# Plausible physical bounds per role (degF for temps, % for valves/dampers/speeds/RH,
# native units otherwise). Generous on purpose: the goal is to catch impossible values
# (BAS error sentinels like -999/32767, unit-scaling blunders), not to second-guess
# legitimate operation. Roles absent here are simply not range-checked.
PHYSICAL_BOUNDS: dict = {
    Role.OAT: (-40.0, 140.0),
    Role.WETBULB_TEMP: (-40.0, 100.0),
    Role.SUPPLY_AIR_TEMP: (32.0, 160.0),
    Role.MIXED_AIR_TEMP: (20.0, 140.0),
    Role.RETURN_AIR_TEMP: (40.0, 120.0),
    Role.SPACE_TEMP: (40.0, 120.0),
    Role.COOL_SP: (45.0, 95.0),
    Role.HEAT_SP: (45.0, 95.0),
    Role.SUPPLY_AIR_TEMP_SP: (40.0, 120.0),
    Role.CHW_SUPPLY_TEMP: (30.0, 75.0),
    Role.CHW_RETURN_TEMP: (35.0, 85.0),
    Role.HW_SUPPLY_TEMP: (60.0, 250.0),
    Role.HW_RETURN_TEMP: (60.0, 230.0),
    Role.CW_SUPPLY_TEMP: (40.0, 120.0),
    Role.CW_RETURN_TEMP: (45.0, 130.0),
    Role.OUTDOOR_RH: (-2.0, 102.0),
    Role.HEAT_VALVE: (-2.0, 102.0),
    Role.HEAT_VALVE_POSITION: (-2.0, 102.0),  # 0.98 (#85)
    Role.COOL_VALVE: (-2.0, 102.0),
    Role.OA_DAMPER: (-2.0, 102.0),
    Role.DAMPER: (-2.0, 102.0),
    Role.SUPPLY_FAN_SPEED: (-2.0, 102.0),
    Role.CHW_PUMP_SPEED: (-2.0, 102.0),
    Role.HW_PUMP_SPEED: (-2.0, 102.0),
    Role.TOWER_FAN_SPEED: (-2.0, 102.0),
    Role.AIRFLOW: (-1.0, 1e6),
    Role.OA_AIRFLOW: (-1.0, 1e6),
    Role.CHW_FLOW: (-1.0, 1e6),
    Role.POWER: (-1.0, 1e7),
    Role.DUCT_STATIC: (-1.0, 20.0),
    # packaged / DX status + stages (binary or small integer)
    Role.COMPRESSOR_STATUS: (-0.1, 1.1),
    Role.COMPRESSOR_STAGE: (-0.1, 6.1),
    Role.CONDENSER_FAN_STATUS: (-0.1, 1.1),
    Role.HEAT_STAGE: (-0.1, 6.1),
    Role.REVERSING_VALVE_CMD: (-0.1, 1.1),
    # air-side humidity / filtration
    Role.FILTER_DIFF_PRESS: (-0.1, 10.0),
    Role.SUPPLY_AIR_HUMIDITY: (-2.0, 102.0),
    Role.RETURN_AIR_HUMIDITY: (-2.0, 102.0),
    # refrigerant-side approach / subcooling temperatures
    Role.COND_APPROACH_TEMP: (-5.0, 60.0),
    Role.EVAP_APPROACH_TEMP: (-5.0, 60.0),
    # Subcooling and superheat are a temperature minus a pressure-derived saturation temperature.
    # A two-phase state (flash gas in the liquid line; liquid floodback at the suction) reads ~0
    # and, with transducer/sensor error, a few degF *below* 0 -- those are the faults themselves,
    # so the floor must admit them. A starved evaporator can run superheat well past 50 degF. The
    # bounds only reject sentinel codes (-99, -999, 999, ...) and dead-channel arithmetic.
    Role.SUBCOOLING_TEMP: (-20.0, 80.0),
    Role.SUPERHEAT_TEMP: (-20.0, 150.0),
    # refrigerant-side pressures, psig. Wide and refrigerant-neutral: low-pressure refrigerants
    # (e.g. R-123) sit near or below atmospheric, R-410A runs several hundred psig, and a CO2
    # (R744) transcritical gas cooler runs ~1100-1750 psig with suction up to ~500 psig (standstill
    # higher). These bounds only reject sensor dropouts / sentinel codes (9999, 32767, 65535).
    Role.DISCHARGE_PRESSURE: (-15.0, 2000.0),
    Role.SUCTION_PRESSURE: (-15.0, 1000.0),
    # 0.93 (#39): refrigerant line temperatures / liquid pressure / discharge superheat. The line
    # temperatures span a low-temperature rack's suction (-40 degF and below) to a discharge line
    # well past 200 degF; discharge superheat runs ~20-100 degF on a healthy compressor and past
    # 150 degF on a starved one. Again only sentinels and dead channels fall outside.
    Role.LIQUID_LINE_TEMP: (-60.0, 200.0),
    Role.SUCTION_LINE_TEMP: (-80.0, 150.0),
    Role.DISCHARGE_LINE_TEMP: (-40.0, 350.0),
    Role.LIQUID_LINE_PRESSURE: (-15.0, 2000.0),
    Role.DISCHARGE_SUPERHEAT_TEMP: (-20.0, 250.0),
    Role.RETURN_AIR_DEWPOINT_TEMP: (-40.0, 100.0),  # 0.93 (#40)
    # 0.93 (#40): a heat-pump source loop -- a ground loop can run near freezing (antifreeze
    # below it) and a boiler/tower loop up to ~100 degF
    Role.SOURCE_LOOP_SUPPLY_TEMP: (0.0, 140.0),
    Role.SOURCE_LOOP_RETURN_TEMP: (0.0, 140.0),
    Role.SOURCE_LOOP_DIFF_PRESS: (-5.0, 300.0),
    Role.SOURCE_LOOP_PUMP_SPEED: (-2.0, 102.0),
    # hydronic flow (gpm) — same wide bound as the chilled-water flow role
    Role.HW_FLOW: (-1.0, 1e6),
    # pump differential head (psi) — wide; only rejects dropouts / impossible values
    Role.PUMP_HEAD: (-5.0, 300.0),
    # CO₂, ppm -- nothing real sits below ~250 (outdoor is ~420); above 10000 is a sentinel
    Role.CO2: (250.0, 10000.0),
    Role.OUTDOOR_CO2: (250.0, 1000.0),
    # 0.92 (#13): boiler gas input rate, kW -- wide; only rejects dropouts / sentinels
    Role.GAS_INPUT_RATE: (-1.0, 1e7),
    # 0.92 (#15): condenser water entering the chillers, and the tower-bypass valve
    Role.COND_ENTERING_WATER_TEMP: (40.0, 120.0),
    Role.CW_BYPASS_VALVE: (-2.0, 102.0),
    # 0.93 (#41, #42): coil leaving-air temperatures, degF (bounds of the supply air they feed)
    Role.HEAT_COIL_LEAVING_TEMP: (20.0, 160.0),
    Role.COOL_COIL_LEAVING_TEMP: (20.0, 140.0),
}

# Continuously-varying analog sensors, where a long flatline is a "stuck sensor"
# signal. For setpoints, status/commands, and valve/damper positions a constant value
# is normal, so flatline is NOT penalized there.
_SENSOR_ROLES: frozenset = frozenset(
    {
        Role.OAT,
        Role.WETBULB_TEMP,
        Role.SUPPLY_AIR_TEMP,
        Role.MIXED_AIR_TEMP,
        Role.RETURN_AIR_TEMP,
        Role.SPACE_TEMP,
        Role.CHW_SUPPLY_TEMP,
        Role.CHW_RETURN_TEMP,
        Role.HW_SUPPLY_TEMP,
        Role.HW_RETURN_TEMP,
        Role.CW_SUPPLY_TEMP,
        Role.CW_RETURN_TEMP,
        Role.COND_ENTERING_WATER_TEMP,  # 0.92 (#15)
        Role.HEAT_COIL_LEAVING_TEMP,  # 0.93 (#42)
        Role.COOL_COIL_LEAVING_TEMP,  # 0.93 (#41, #42)
        Role.OUTDOOR_RH,
        Role.AIRFLOW,
        Role.CHW_FLOW,
        Role.HW_FLOW,
        Role.PUMP_HEAD,
        Role.POWER,
        Role.DUCT_STATIC,
        Role.CO2,
    }
)


# Roles whose value distribution is legitimately **two-regime**: the equipment duty-cycles, so an
# "off" band and an "on" band are both normal operation rather than a data fault. The robust outlier
# test assumes one population, so for these roles it is read within each regime (see
# camber.ingest.quality). Deliberately a short allow-list -- for every other role a two-mode
# distribution is suspicious, not exonerating, and enabling this where it is not physically expected
# is the one way this layer could mask a real fault. Status points are 0/1 by definition, and five
# rules take one as a *required* role, so they are unioned in rather than re-listed.
_INTERMITTENT_ROLES: frozenset = (
    frozenset(
        {
            Role.ENERGY_RATE,  # BTU meter: near zero except during a heating/cooling event
            Role.HW_FLOW,
            Role.CHW_FLOW,
            Role.AIRFLOW,
            Role.OA_AIRFLOW,
            Role.POWER,
            Role.COMPRESSOR_STAGE,
            Role.HEAT_STAGE,
            Role.GAS_INPUT_RATE,  # 0.92 (#13): zero between firing cycles
        }
    )
    | STATUS_ROLES
)


# Temperature roles, canonical degF (PHYSICAL_BOUNDS is in degF).
_TEMP_ROLES: frozenset = frozenset(r for r in PHYSICAL_BOUNDS if r.value.endswith(("_temp", "oat")))

# Measurement-precision floor for the robust outlier scale (std-equivalent, canonical units): the
# smallest deviation that can mean anything about a sensor. Below it the MAD of a tightly controlled
# point (a supply temp held to 0.1 F, a loop DP at setpoint) turns control noise and float rounding
# into "outliers". Deliberately at the *small* end of real instrument accuracy -- a floor that is
# too small only leaves today's behaviour in place; one that is too large would hide real spikes:
#   * temperatures 0.5 degF -- a typical BAS thermistor/RTD is quoted +/-0.2..0.5 degC;
#   * percent points 1 %-pt -- actuator position feedback / VFD speed / RH resolution;
#   * CO2 30 ppm -- the fixed part of a typical NDIR spec (+/-30 ppm + 3 % of reading).
# Everything else (flows, pressures, power: units not canonical) gets a relative floor of 0.5 % of
# the series' own 99th-percentile magnitude -- transmitter accuracy is quoted as 0.25-1 % of span,
# and the observed P99 is a lower bound on the span.
_ABS_SCALE_FLOOR: dict = {
    **{r: 0.5 for r in _TEMP_ROLES},
    **{r: 1.0 for r in PERCENT_ROLES},
    Role.CO2: 30.0,
    Role.OUTDOOR_CO2: 30.0,
}
_REL_SCALE_FLOOR = 0.005


def _scale_floor(series: pd.Series, role) -> float | None:
    """The role's measurement-precision floor for the robust outlier scale (see above)."""
    if role in STATUS_ROLES:
        return None  # 0/1 by definition; there is no precision to speak of
    if role in _ABS_SCALE_FLOOR:
        return float(_ABS_SCALE_FLOOR[role])
    v = pd.to_numeric(series, errors="coerce").dropna().to_numpy(dtype="float64")
    if len(v) == 0:
        return None
    mag = float(np.percentile(np.abs(v), 99))
    return _REL_SCALE_FLOOR * mag if mag > 0 else None


def range_violation_frac(series: pd.Series, role) -> float:
    """Fraction of non-null samples physically outside the role's plausible bounds.

    Returns NaN if the role has no defined bounds (not range-checked).
    """
    if role not in PHYSICAL_BOUNDS:
        return float("nan")
    lo, hi = PHYSICAL_BOUNDS[role]
    s = series.dropna()
    if len(s) == 0:
        return float("nan")
    return round(float(((s < lo) | (s > hi)).mean()), 4)


# Volumetric-airflow roles, canonical cfm.
_CFM_ROLES: frozenset = frozenset({Role.AIRFLOW, Role.OA_AIRFLOW})


def percent_scale_suspect(series: pd.Series, role, *, min_samples: int = 24) -> bool | None:
    """True when an airflow-role series looks like a 0-100 % (or 0-1) signal, not cfm.

    A supply- or outdoor-air flow in cfm that never exceeds 100 over a whole record is below the
    design flow of the smallest commercial VAV terminal (~200 cfm for a 4-inch inlet), let alone an
    air handler -- so a series bounded to [0, 100] mapped to a cfm role is far more likely a fan
    speed, damper position or a fraction than an airflow (a building model that types fan-speed
    points as supply-air-flow sensors produces exactly this). The physical-bounds check cannot see
    it, because 0-100 is inside any airflow range.

    Screening-grade: a very small box trended in SI (100 L/s is ~212 cfm) would also trip it.
    Returns ``None`` when not applicable (not an airflow role, too few samples, or an all-zero
    series that says nothing about scale).
    """
    if role not in _CFM_ROLES:
        return None
    v = pd.to_numeric(series, errors="coerce").dropna()
    if len(v) < min_samples or float(v.abs().max()) == 0.0:
        return None
    return bool(float(v.min()) >= -1.0 and float(v.max()) <= 100.5)


# Outdoor CO2 has been above ~400 ppm everywhere since the mid-2010s and a building has no CO2
# sink, so an indoor sensor reading well under it is miscalibrated (typically an NDIR sensor's
# automatic baseline calibration). 380 ppm leaves ~30 ppm of sensor accuracy below the ~410 ppm
# background, so only a clear under-read counts.
_CO2_AMBIENT_FLOOR = 380.0


@dataclass
class SensorTrust:
    """How much one point can be trusted, with the reasons."""

    role: str
    n: int
    coverage: float
    flatline_frac: float
    outlier_frac: float
    range_violation_frac: float  # NaN if the role has no bounds
    trust: float  # 0..1 (1 = fully trustworthy)
    verdict: str  # "trusted" | "suspect" | "untrusted"
    flags: list = field(default_factory=list)
    # Provisional (#58). Stuck runs judged by absolute duration: the longest identical run in
    # hours, and every run longer than the role's limit as ``{"start", "end", "hours", "value"}``.
    longest_flat_hours: float | None = None
    stuck_intervals: list = field(default_factory=list)
    # The point's own span: its first valid sample, and coverage over the whole window it was
    # handed (``coverage`` above is over the point's own span, from ``first_valid`` on).
    first_valid: str | None = None
    window_coverage: float | None = None
    # Binary (status) points: how many state changes the series holds (None for other roles).
    n_state_changes: int | None = None
    # Frame-level findings that touched this point (all-points freeze, fan-off plausibility,
    # status-vs-speed), as ``{"check": ..., ...}`` dicts; filled by :func:`frame_sensor_health`.
    frame_checks: list = field(default_factory=list)
    # 0.92 (#66): what the plant run gate was read from ("chiller status", ...) when this
    # point was judged on its equipment's running samples only; None when it was not.
    run_gate: str | None = None

    def as_dict(self) -> dict:
        """Return the trust result as a plain dict."""
        d = self.__dict__.copy()
        d["flags"] = list(self.flags)
        d["stuck_intervals"] = [dict(x) for x in self.stuck_intervals]
        d["frame_checks"] = [dict(x) for x in self.frame_checks]
        return d


# Roles whose reading only means something while air moves past the sensor: with the supply fan
# off, a duct temperature settles to the plenum, and a flow / static transmitter sits at zero -- a
# long identical run there is the unit being off, not a stuck sensor. The gated trust mode judges
# flatline on fan-on samples for these roles only (an outdoor or space temperature keeps its
# meaning with the fan off, so it is never gated).
FAN_GATED_ROLES: frozenset = frozenset(
    {
        Role.SUPPLY_AIR_TEMP,
        Role.MIXED_AIR_TEMP,
        Role.RETURN_AIR_TEMP,
        Role.HEAT_COIL_LEAVING_TEMP,  # 0.93 (#42)
        Role.COOL_COIL_LEAVING_TEMP,  # 0.93 (#41, #42)
        Role.AIRFLOW,
        Role.OA_AIRFLOW,
        Role.DUCT_STATIC,
        Role.SUPPLY_AIR_HUMIDITY,
        Role.RETURN_AIR_HUMIDITY,
    }
)

# Fewer gated samples than this and the gated flatline read falls back to the whole series.
_MIN_GATED = 24


def _gated_flatline_frac(series: pd.Series, gate: pd.Series) -> float | None:
    """Longest identical run *within contiguous gated stretches*, over the gated sample count.

    A run is broken wherever the gate goes False, so hours of the unit sitting off never join a
    run. ``None`` when too few gated samples exist to judge.
    """
    g = pd.Series(gate).reindex(series.index).fillna(False).astype(bool)
    s = pd.to_numeric(series, errors="coerce")
    on = s[g].dropna()
    if len(on) < _MIN_GATED:
        return None
    seg = (~g).cumsum()[g].reindex(on.index)  # one id per contiguous gated stretch
    changed = on.ne(on.shift()) | seg.ne(seg.shift())
    longest = int(changed.cumsum().value_counts().max())
    return longest / len(on)


# --------------------------------------------------------------------------------------------- #
# 0.92 (#66): the plant run gate                                                            #
# --------------------------------------------------------------------------------------------- #

#: Plant roles whose reading only means something while the equipment runs, and the loop whose run
#: gate (:func:`camber.schedules.plant_run_mask`) judges them. With the chiller off, its leaving
#: chilled water drifts to the plant-room temperature and its condenser water to ambient; with the
#: boiler off, the hot-water loop cools. A drift, a range excursion or a long identical run there
#: is the plant being off, not a bad sensor -- so in the gated mode these roles are judged (range,
#: outliers, flatline, stuck runs) on running samples only; coverage stays over the whole span.
PLANT_GATED_ROLES: dict = {
    **{
        r: "chw"
        for r in (
            Role.CHW_SUPPLY_TEMP,
            Role.CHW_RETURN_TEMP,
            Role.CW_SUPPLY_TEMP,
            Role.CW_RETURN_TEMP,
            Role.COND_ENTERING_WATER_TEMP,  # 0.92 (#15)
            Role.COND_APPROACH_TEMP,
            Role.EVAP_APPROACH_TEMP,
            Role.SUBCOOLING_TEMP,
            Role.SUPERHEAT_TEMP,
        )
    },
    Role.HW_SUPPLY_TEMP: "hw",
    Role.HW_RETURN_TEMP: "hw",
}


#: A plant point is judged once its equipment has settled: the samples within this long of a start
#: are left out of the running set (a loop pulling down from standby is not a sensor fault).
PLANT_SETTLE = pd.Timedelta("30min")


def _settled(mask: pd.Series, settle: pd.Timedelta = PLANT_SETTLE) -> pd.Series:
    """``mask`` without the samples less than ``settle`` after each off -> on transition."""
    m = mask.astype(bool)
    if not isinstance(m.index, pd.DatetimeIndex) or not m.any():
        return m
    starts = m & ~m.shift(fill_value=False)
    t = pd.Series(m.index, index=m.index)
    last_start = t.where(starts).ffill()
    return m & ((t - last_start) >= settle)


def plant_gates(frame: pd.DataFrame) -> dict:
    """``{Role: (running mask, source)}`` for the plant-gated roles ``frame`` carries.

    Each role in :data:`PLANT_GATED_ROLES` gets its loop's gate, less the first
    :data:`PLANT_SETTLE` after every start. A chiller's ``power`` is gated
    too when the chilled-water gate comes from a run status (a status says when the machine ran,
    so a power reading stuck at zero while it ran is a fault; a power-derived gate cannot judge
    the power it was derived from). Roles without a gate are absent.
    """
    from .schedules import plant_run_mask

    have = {c for c in frame.columns if isinstance(c, Role)}
    loops = {PLANT_GATED_ROLES[r] for r in have if r in PLANT_GATED_ROLES}
    masks = {}
    for loop in sorted(loops):
        m, src = plant_run_mask(frame, loop)
        if m is not None:
            masks[loop] = (_settled(m), src)
    out = {r: masks[PLANT_GATED_ROLES[r]] for r in have if PLANT_GATED_ROLES.get(r) in masks}
    if Role.POWER in have and "chw" in masks and masks["chw"][1] == "chiller status":
        out[Role.POWER] = masks["chw"]
    return out


# --------------------------------------------------------------------------------------------- #
# Stuck runs by absolute duration (#58)                                                         #
# --------------------------------------------------------------------------------------------- #

#: Longest believable identical run, in hours, per analog role. The share-of-series flatline read
#: (``flatline_frac > 0.5``) dilutes with series length -- a zone temperature pinned for 12 days of
#: a year is 3 % of the series -- so a run is also judged on its own duration. A real temperature,
#: humidity or CO2 reading moves within a day; flows, static and power sit at an "off" value for a
#: weekend, so for those an idle run (near zero) is never counted (see :data:`_IDLE_ROLES`). The
#: fan-dependent roles (:data:`FAN_GATED_ROLES`) are judged only in the gated mode, on fan-on
#: stretches. Provisional: tune per site with ``stuck_hours=``.
STUCK_HOURS: dict = {
    **{
        r: 24.0
        for r in (
            Role.OAT,
            Role.WETBULB_TEMP,
            Role.SUPPLY_AIR_TEMP,
            Role.MIXED_AIR_TEMP,
            Role.RETURN_AIR_TEMP,
            Role.SPACE_TEMP,
            Role.CHW_SUPPLY_TEMP,
            Role.CHW_RETURN_TEMP,
            Role.HW_SUPPLY_TEMP,
            Role.HW_RETURN_TEMP,
            Role.CW_SUPPLY_TEMP,
            Role.CW_RETURN_TEMP,
            Role.COND_ENTERING_WATER_TEMP,  # 0.92 (#15)
            Role.HEAT_COIL_LEAVING_TEMP,  # 0.93 (#42)
            Role.COOL_COIL_LEAVING_TEMP,  # 0.93 (#41, #42)
            Role.OUTDOOR_RH,
            Role.AIRFLOW,
            Role.CHW_FLOW,
            Role.HW_FLOW,
            Role.PUMP_HEAD,
            Role.POWER,
            Role.DUCT_STATIC,
        )
    },
    Role.CO2: 48.0,  # an empty building over a weekend can sit at the outdoor background
}

# Roles that legitimately hold an "off" reading (near zero) for days; an idle run is not stuck.
_IDLE_ROLES: frozenset = frozenset(
    {
        Role.AIRFLOW,
        Role.OA_AIRFLOW,
        Role.CHW_FLOW,
        Role.HW_FLOW,
        Role.POWER,
        Role.DUCT_STATIC,
        Role.PUMP_HEAD,
        Role.GAS_INPUT_RATE,  # 0.92 (#13): a boiler sits unfired all summer
    }
)
_IDLE_FRAC = 0.02  # |value| at or below this share of the series' P99 magnitude reads as idle

# Stuck runs cap the trust at "suspect": the point may be fine outside the interval, but a rule
# run over the whole window would read the stuck stretch as data.
_SUSPECT_CAP = 0.75


def _step(index) -> pd.Timedelta:
    idx = pd.DatetimeIndex(index)
    if len(idx) < 2:
        return pd.Timedelta(0)
    d = pd.Series(idx).diff().dropna()
    d = d[d > pd.Timedelta(0)]
    return d.median() if len(d) else pd.Timedelta(0)


def _value_runs(series: pd.Series, gate=None, *, join=False) -> pd.DataFrame:
    """Identical-value runs of ``series`` (non-null) as a frame: start, end, n, value, hours.

    With ``gate`` (boolean, same index) only gated samples count and a run breaks wherever the
    gate goes False, as in the gated flatline read. With ``join=True`` (0.92, #66) a run is
    *not* broken where the gate goes False -- a value identical across successive running
    stretches is one run -- and its ``hours`` are the gated hours it spans (samples x step), not
    the wall-clock time the off stretches would add.
    """
    s = pd.to_numeric(series, errors="coerce")
    step = _step(s.index)
    if gate is not None:
        g = pd.Series(gate).reindex(s.index).fillna(False).astype(bool)
        seg = None if join else (~g).cumsum()[g]
        s = s[g]
    else:
        seg = None
    s = s.dropna()
    if s.empty or not isinstance(s.index, pd.DatetimeIndex):
        return pd.DataFrame(columns=["start", "end", "n", "value", "hours"])
    changed = s.ne(s.shift())
    if seg is not None:
        sg = seg.reindex(s.index)
        changed = changed | sg.ne(sg.shift())
    rid = changed.cumsum()
    ts = pd.Series(s.index, index=s.index)
    g2 = pd.DataFrame({"ts": ts, "v": s, "rid": rid}).groupby("rid")
    out = pd.DataFrame(
        {
            "start": g2["ts"].min(),
            "end": g2["ts"].max(),
            "n": g2["v"].size(),
            "value": g2["v"].first(),
        }
    )
    if join and gate is not None:
        out["hours"] = out["n"] * step.total_seconds() / 3600.0
    else:
        out["hours"] = (out["end"] - out["start"] + step).dt.total_seconds() / 3600.0
    return out.reset_index(drop=True)


def _stuck_runs(series: pd.Series, role, gate=None, stuck_hours=None, run_gate=None):
    """``(longest_hours, [interval dicts], stuck sample count)`` for a role with a stuck limit."""
    limits = STUCK_HOURS if stuck_hours is None else {**STUCK_HOURS, **stuck_hours}
    if role not in limits:
        return None, [], 0
    use_gate = None
    if run_gate is not None:  # 0.92 (#66): runs judged within running stretches only
        use_gate = run_gate
    elif role in FAN_GATED_ROLES:
        if gate is None:
            # a duct reading holds still whenever the fan is off (a weekend), so its run length
            # says nothing without knowing when the fan ran: judged in the gated mode only
            return None, [], 0
        use_gate = gate
    runs = _value_runs(series, use_gate, join=run_gate is not None)
    if runs.empty:
        return None, [], 0
    if role in _IDLE_ROLES:
        v = pd.to_numeric(series, errors="coerce").dropna().abs()
        p99 = float(np.percentile(v, 99)) if len(v) else 0.0
        runs = runs[runs["value"].abs() > _IDLE_FRAC * p99] if p99 > 0 else runs.iloc[0:0]
    if runs.empty:
        return 0.0, [], 0
    longest = round(float(runs["hours"].max()), 2)
    over = runs[runs["hours"] > float(limits[role])]
    intervals = [
        {
            "start": str(r.start),
            "end": str(r.end),
            "hours": round(float(r.hours), 2),
            "value": float(r.value),
        }
        for r in over.itertuples()
    ]
    return longest, intervals, int(over["n"].sum())


# Run-status points whose state should change: a supply fan that never changes over two weeks is
# either a dead point or a unit nobody runs -- both worth a look. Pumps, compressors and boilers
# legitimately sit off for a whole season, so for them the count is reported, never penalized.
_CHANGE_CHECKED: frozenset = frozenset({Role.SUPPLY_FAN_STATUS})
_MIN_CHANGE_SPAN_DAYS = 14.0
_FRACTIONAL_MAX_STEP = pd.Timedelta("15min")  # a coarser grid is a duty resample: fractions fine


def _status_checks(series: pd.Series, role, expected_freq=None):
    """``(n_changes, flags, cap)`` for a binary status point."""
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return 0, [], None
    flags: list = []
    cap = None
    v = s.to_numpy(dtype="float64")
    frac = (v > 1e-6) & (v < 1 - 1e-6)
    step = pd.Timedelta(expected_freq) if expected_freq is not None else _step(s.index)
    if float(frac.mean()) > 0.05 and pd.Timedelta(0) < step <= _FRACTIONAL_MAX_STEP:
        # a native-rate status holding values between 0 and 1: interpolated, not logged, data
        flags.append("fractional_status")
        cap = _SUSPECT_CAP
    state = np.where(v >= 0.5, 1, 0)
    n_changes = int((np.diff(state) != 0).sum())
    span_days = (s.index[-1] - s.index[0]).total_seconds() / 86400.0 if len(s) > 1 else 0.0
    if n_changes == 0 and span_days >= _MIN_CHANGE_SPAN_DAYS:
        flags.append("never_changes")
        if role in _CHANGE_CHECKED:
            cap = _SUSPECT_CAP
    return n_changes, flags, cap


def _verdict(trust: float) -> str:
    return "trusted" if trust >= 0.8 else ("suspect" if trust >= 0.5 else "untrusted")


def sensor_trust(
    series: pd.Series,
    role,
    *,
    expected_freq=None,
    gate=None,
    stuck_hours=None,
    run_gate=None,
    run_gate_source: str | None = None,
) -> SensorTrust:
    """Score one point's trustworthiness from quality stats + physical-range checks.

    ``gate`` (optional boolean Series, e.g. fan-on from :func:`camber.schedules.fan_on_mask`)
    switches on the **gated** mode for the roles in :data:`FAN_GATED_ROLES`: the flatline read (the
    ``stuck`` flag and its share of the score) is taken over the gated samples only, so a duct
    sensor holding a constant value while the unit is off is not called stuck. Coverage, range and
    outlier reads are unchanged. With ``gate=None`` (the default) nothing changes.

    Also (provisional, #58): an analog point is ``stuck`` when an identical run outlasts the role's
    :data:`STUCK_HOURS` limit (override per role with ``stuck_hours={Role: hours}``), whatever its
    share of the series; the runs are reported in ``stuck_intervals`` and cap the trust at
    "suspect". Coverage is judged over the point's **own span**, from its first valid sample
    (``first_valid``; a point that starts late is flagged ``late_start``, not ``low_coverage``);
    ``window_coverage`` keeps the whole-window figure. A binary status point reports its state
    changes, and is flagged ``never_changes`` (no change over 14 days or more; a supply-fan status
    is then "suspect") or ``fractional_status`` (values between 0 and 1 at a native <= 15 min rate:
    interpolated, not logged).

    ``run_gate`` (provisional, 0.92, #66; a boolean Series, e.g. from
    :func:`camber.schedules.plant_run_mask`) judges a plant point on its equipment's **running**
    samples: the range, outlier and flatline reads and the stuck runs are taken over running
    samples (a run breaks where the equipment stops), while coverage stays over the point's whole
    span. It applies to the roles in :data:`PLANT_GATED_ROLES` and to ``power``; others ignore
    it. When the equipment ran for fewer than 24 samples the point is flagged ``not_running`` and
    scored on coverage alone -- there is nothing to judge it on. ``run_gate_source`` names the
    gate in the result's ``run_gate``.
    """
    intermittent = role in _INTERMITTENT_ROLES
    full = series
    window_cov = round(float(full.notna().mean()), 4) if len(full) else 0.0
    first = full.first_valid_index()
    late = False
    if first is not None and len(full) and first != full.index[0]:
        lead = float((full.index < first).mean())
        if lead > 0.05:
            late = True
        series = full.loc[first:]
    q = assess(
        series,
        expected_freq,
        regime_aware=intermittent,
        shape_aware=True,
        scale_floor=_scale_floor(series, role),
    )
    # 0.92 (#66): a plant point is judged on its equipment's running samples
    qj, plant_g, not_running = q, None, False
    if run_gate is not None and (role in PLANT_GATED_ROLES or role == Role.POWER):
        plant_g = pd.Series(run_gate).reindex(series.index).fillna(False).astype(bool)
        on = pd.to_numeric(series, errors="coerce")[plant_g].dropna()
        if len(on) < _MIN_GATED:
            not_running = True
        else:
            # regime-aware: a gate that is really an enable (a boiler "on" through a mild month
            # with the loop cold) splits the running samples in two -- flagged "bimodal", not
            # read as outliers of the sensor
            qj = assess(
                on,
                None,
                regime_aware=True,
                shape_aware=True,
                scale_floor=_scale_floor(on, role),
            )
    if not_running:
        rng = float("nan")  # not judged: the equipment never ran long enough
        trust = q.coverage
        flat_frac = 0.0
    else:
        rng = range_violation_frac(series if plant_g is None else series[plant_g], role)
        rng_pen = 0.0 if rng != rng else min(rng * 3.0, 1.0)  # out-of-range is serious
        if plant_g is None:
            trust = q.score * (1.0 - rng_pen)
            flat_frac = q.flatline_frac
        else:
            # coverage over the whole span; everything else over the running samples, read as
            # one series -- a value held across successive running stretches is one flat run,
            # the off time between them is not counted
            quality = qj.score / qj.coverage if qj.coverage > 0 else 0.0
            trust = q.coverage * quality * (1.0 - rng_pen)
            flat_frac = qj.flatline_frac
    if gate is not None and role in FAN_GATED_ROLES:
        gated = _gated_flatline_frac(series, gate)
        if gated is not None:
            # swap the ungated flatline share of the composite score for the gated one
            trust = trust / (1.0 - 0.2 * q.flatline_frac) * (1.0 - 0.2 * gated)
            flat_frac = round(gated, 4)

    flags = []
    if late:
        flags.append("late_start")
    if q.coverage < 0.9:
        flags.append("low_coverage")
    if q.n_gaps > 0:
        flags.append("gaps")
    out_frac = qj.outlier_frac
    if (
        (intermittent or plant_g is not None)
        and qj.n_regimes == 2
        and qj.regime_outlier_frac is not None
    ):
        out_frac = qj.regime_outlier_frac  # judged within each regime: a duty cycle isn't a fault
    elif qj.shape_outlier_frac is not None:
        # floored at the sensor's precision and two-sided for a skewed operating tail, so a
        # tightly-controlled point or a pump idling then ramping with load isn't a fault
        out_frac = qj.shape_outlier_frac
    if not_running:
        flags.append("not_running")  # 0.92 (#66)
    elif out_frac > 0.05:
        flags.append("outliers")
    if rng == rng and rng > 0.01:
        flags.append("out_of_range")
    if role in _SENSOR_ROLES and flat_frac > 0.5:
        flags.append("stuck")
        trust *= 0.5  # a stuck analog sensor is bad
    if not_running:
        longest_h, stuck_iv, n_stuck = None, [], 0
    else:
        longest_h, stuck_iv, n_stuck = _stuck_runs(series, role, gate, stuck_hours, plant_g)
    if stuck_iv:
        if "stuck" not in flags:
            flags.append("stuck")
        share = n_stuck / qj.n if qj.n else 1.0
        trust = min(trust * (1.0 - share), _SUSPECT_CAP)
    n_changes = None
    if role in STATUS_ROLES:
        n_changes, sflags, cap = _status_checks(series, role, expected_freq)
        flags += sflags
        if cap is not None:
            trust = min(trust, cap)
    if percent_scale_suspect(series, role):
        flags.append("scale_suspect")  # a 0-100 signal mapped to a cfm role: check the mapping
    if role == Role.CO2:
        v = pd.to_numeric(series, errors="coerce").dropna()
        if len(v) and float((v < _CO2_AMBIENT_FLOOR).mean()) > 0.05:
            flags.append("below_ambient")  # reads under outdoor background: calibration suspect
    if qj.n_regimes == 2 and not not_running:
        # Two meanings, both honest, neither a penalty: for a duty-cycled role this explains why
        # the outliers were read within-regime; for anything else it is new information -- a point
        # that should have one population has two, which is a "look here", not a verdict.
        flags.append("intermittent" if intermittent else "bimodal")

    trust = round(float(max(0.0, min(1.0, trust))), 4)
    return SensorTrust(
        role=role.value if isinstance(role, Role) else str(role),
        n=q.n,
        coverage=q.coverage,
        flatline_frac=flat_frac,
        outlier_frac=qj.outlier_frac,
        range_violation_frac=rng,
        trust=trust,
        verdict=_verdict(trust),
        flags=flags,
        longest_flat_hours=longest_h,
        stuck_intervals=stuck_iv,
        first_valid=None if first is None else str(first),
        window_coverage=window_cov,
        n_state_changes=n_changes,
        run_gate=run_gate_source if plant_g is not None else None,
    )


# --------------------------------------------------------------------------------------------- #
# Frame-level trust checks (#58): points judged against each other                              #
# --------------------------------------------------------------------------------------------- #

#: Pressure roles that must read near zero with the supply fan off, and the plausible ceiling for
#: a fan-off reading (in.w.c.): a transmitter that holds several inches with the fan stopped is
#: offset, mis-scaled or mapped to the wrong point.
_FAN_OFF_PRESSURE: dict = {Role.DUCT_STATIC: 0.5, Role.FILTER_DIFF_PRESS: 0.3}
_STATUS_OFF = 0.05  # a status (or duty) at or below this is off for the whole sample
_STATUS_ON = 0.95
_SPEED_RUNNING_PCT = 20.0  # a drive above this is running, whatever the status says
_SPEED_STOPPED_PCT = 1.0
_FREEZE_HOURS = 12.0  # every varying sensor on the unit identical this long = collection outage


def _col(frame: pd.DataFrame, role):
    for key in (role, getattr(role, "value", role)):
        if key in frame.columns:
            s = pd.to_numeric(frame[key], errors="coerce")
            return s if s.notna().any() else None
    return None


def _mark(health: dict, role, flag: str, check: dict, *, cap=None, share=0.0) -> None:
    t = health.get(role)
    if t is None:
        return
    if flag not in t.flags:
        t.flags.append(flag)
    t.frame_checks.append(check)
    if cap is not None:
        t.trust = round(float(max(0.0, min(t.trust * (1.0 - share), cap))), 4)
        t.verdict = _verdict(t.trust)


def _fan_off(frame: pd.DataFrame):
    """Samples where the supply fan is clearly off: status and speed agree (either alone else)."""
    from .units import normalize_percent

    status, speed = _col(frame, Role.SUPPLY_FAN_STATUS), _col(frame, Role.SUPPLY_FAN_SPEED)
    off = None
    if status is not None:
        off = status <= _STATUS_OFF
    if speed is not None:
        sp_off = normalize_percent(speed) < _SPEED_STOPPED_PCT
        off = sp_off if off is None else (off & sp_off)
    return off


def _check_fan_off_pressure(frame: pd.DataFrame, health: dict) -> None:
    off = _fan_off(frame)
    if off is None:
        return
    for role, ceiling in _FAN_OFF_PRESSURE.items():
        p = _col(frame, role)
        if p is None or role not in health:
            continue
        both = off & p.notna()
        n_off = int(both.sum())
        if n_off < _MIN_GATED:
            continue
        bad = both & (p.abs() > ceiling)
        frac = float(bad.sum()) / n_off
        if frac > 0.5:
            check = {
                "check": "fan_off_pressure",
                "n_fan_off": n_off,
                "frac_implausible": round(frac, 4),
                "median_fan_off": round(float(p[both].median()), 3),
                "ceiling": ceiling,
            }
            share = float(bad.sum()) / max(int(p.notna().sum()), 1)
            _mark(health, role, "implausible_fan_off", check, cap=_SUSPECT_CAP, share=share)


def _check_status_speed(frame: pd.DataFrame, health: dict) -> None:
    from .units import normalize_percent

    status, speed = _col(frame, Role.SUPPLY_FAN_STATUS), _col(frame, Role.SUPPLY_FAN_SPEED)
    if status is None or speed is None:
        return
    pct = normalize_percent(speed)
    both = status.notna() & pct.notna()
    n = int(both.sum())
    if n < _MIN_GATED:
        return
    off_running = both & (status <= _STATUS_OFF) & (pct > _SPEED_RUNNING_PCT)
    on_stopped = both & (status >= _STATUS_ON) & (pct < _SPEED_STOPPED_PCT)
    bad = off_running | on_stopped
    k = int(bad.sum())
    if k >= 3 and k / n >= 0.01:
        check = {
            "check": "status_vs_speed",
            "n_checked": n,
            "n_status_off_speed_running": int(off_running.sum()),
            "n_status_on_speed_stopped": int(on_stopped.sum()),
            "frac_inconsistent": round(k / n, 4),
            "first": str(bad[bad].index[0]),
        }
        _mark(health, Role.SUPPLY_FAN_STATUS, "status_speed_mismatch", check, cap=_SUSPECT_CAP)
        _mark(health, Role.SUPPLY_FAN_SPEED, "status_speed_mismatch", check)


def _check_all_frozen(frame: pd.DataFrame, health: dict) -> None:
    cols = []
    for c in frame.columns:
        role = c if isinstance(c, Role) else None
        if role is None or role not in _SENSOR_ROLES:
            continue
        v = pd.to_numeric(frame[c], errors="coerce")
        if v.nunique(dropna=True) > 1:
            cols.append(c)
    if len(cols) < 2 or not isinstance(frame.index, pd.DatetimeIndex) or len(frame) < 3:
        return
    w = frame[cols].apply(pd.to_numeric, errors="coerce")
    same = (w.diff() == 0).all(axis=1) & w.notna().all(axis=1) & w.shift().notna().all(axis=1)
    if not same.any():
        return
    step = _step(frame.index)
    # 0.92 (#66): points judged on a plant run gate hold still while the plant is off (a
    # change-of-value log records nothing) -- that is not a collection outage
    plant_on = None
    if any(getattr(health.get(c), "run_gate", None) for c in cols):
        masks = [m for m, _src in plant_gates(frame).values()]
        if masks:
            plant_on = pd.concat(masks, axis=1).any(axis=1)
    rid = (~same).cumsum()
    intervals, n_frozen = [], 0
    for _, grp in same[same].groupby(rid[same]):
        start = frame.index[frame.index.get_loc(grp.index[0]) - 1]  # the value held from here
        end = grp.index[-1]
        hours = (end - start + step).total_seconds() / 3600.0
        if hours >= _FREEZE_HOURS and not _plant_off_throughout(plant_on, start, end):
            intervals.append({"start": str(start), "end": str(end), "hours": round(hours, 2)})
            n_frozen += len(grp) + 1
    if not intervals:
        return
    roles: list = [c.value for c in cols]
    check: dict = {"check": "all_points_frozen", "roles": roles, "intervals": intervals}
    # Any other point that varies elsewhere but held still through every frozen interval froze
    # with them (a drive speed or valve command in a forward-filled outage).
    for c in frame.columns:
        if c in cols or not isinstance(c, Role):
            continue
        v = pd.to_numeric(frame[c], errors="coerce")
        if v.nunique(dropna=True) <= 1:
            continue
        if all(
            v.loc[iv["start"] : iv["end"]].notna().all()
            and v.loc[iv["start"] : iv["end"]].nunique() == 1
            for iv in intervals
        ):
            cols.append(c)
            roles.append(c.value)
    share = n_frozen / max(len(frame), 1)
    for c in cols:
        _mark(health, c, "all_points_frozen", dict(check), cap=_SUSPECT_CAP, share=share)


def _plant_off_throughout(plant_on, start, end) -> bool:
    """True when a plant run mask exists and shows the plant off for all of ``[start, end]``."""
    if plant_on is None:
        return False
    return not bool(plant_on.loc[start:end].any())


def frame_checks(frame: pd.DataFrame, health: dict) -> dict:
    """Apply the frame-level trust checks to ``health`` (``{Role: SensorTrust}``) in place.

    Provisional (#58). Each check judges points against the others on the same equipment and marks
    only the roles present in ``health``: a pressure point reading several inches with the supply
    fan off (``implausible_fan_off``), a fan status that disagrees with the drive speed
    (``status_speed_mismatch``), and every varying sensor on the unit holding its value at once
    for 12 h or more -- a forward-filled collection outage (``all_points_frozen``). A marked point
    records the check in ``frame_checks`` and is capped at "suspect". Returns ``health``.

    Since 0.92 (#16) two cross-sensor checks feed trust too: a measured point that carries another
    measured point's data (``copied_signal``, from :func:`copied_signal_consistency`) and a
    mixed-air temperature that fails the flow-weighted OA/RA balance (``mixing_balance``, from
    :func:`mixing_flow_consistency`). See :func:`_check_copied_signal` and
    :func:`_check_mixing_balance` for what each lowers.
    """
    _check_fan_off_pressure(frame, health)
    _check_status_speed(frame, health)
    _check_all_frozen(frame, health)
    # 0.92 (#16): cross-sensor physics into trust
    _check_copied_signal(frame, health)
    _check_mixing_balance(frame, health)
    return health


def frame_sensor_health(
    frame: pd.DataFrame, *, expected_freq=None, gate=None, plant_gate=None
) -> dict:
    """Trust score every role-column of a role-frame -> ``{Role: SensorTrust}``.

    ``gate`` is passed to :func:`sensor_trust` (gated mode for the fan-dependent roles); pass
    ``"fan"`` to derive it from the frame's own fan signal via
    :func:`camber.schedules.fan_on_mask` (ungated when the frame has none). The frame-level checks
    of :func:`frame_checks` are then applied.

    ``plant_gate`` (provisional, 0.92, #66): ``"auto"`` judges the plant roles
    (:data:`PLANT_GATED_ROLES`, and a chiller's power) on their equipment's running samples, with
    the gate read from the frame by :func:`plant_gates`; ``None`` (the default) leaves them
    ungated.
    """
    if isinstance(gate, str):
        if gate != "fan":
            raise ValueError(f"gate must be a boolean Series, 'fan' or None, got {gate!r}")
        from .schedules import fan_on_mask

        gate = fan_on_mask(frame)[0]
    if plant_gate not in (None, "auto"):
        raise ValueError(f"plant_gate must be 'auto' or None, got {plant_gate!r}")
    pg = plant_gates(frame) if plant_gate == "auto" else {}  # 0.92 (#66)
    health = {
        role: sensor_trust(
            frame[role],
            role,
            expected_freq=expected_freq,
            gate=gate,
            run_gate=pg[role][0] if role in pg else None,
            run_gate_source=pg[role][1] if role in pg else None,
        )
        for role in frame.columns
    }
    return frame_checks(frame, health)


def trusted_roles(frame: pd.DataFrame, *, min_trust: float = 0.5, expected_freq=None) -> set:
    """Roles whose data is trustworthy enough to diagnose on (trust >= ``min_trust``).

    A rule runner can intersect this with a rule's required roles and skip the rule
    when an input it depends on is below the bar -- "decline to fire on data we don't
    trust" rather than emit a fault that is really a sensor problem.
    """
    # 0.92 (#66): plant points judged on running samples
    health = frame_sensor_health(frame, expected_freq=expected_freq, plant_gate="auto")
    return {role for role, t in health.items() if t.trust >= min_trust}


def untrusted_roles(
    frame: pd.DataFrame, roles, *, min_trust: float = 0.5, expected_freq=None
) -> list:
    """Which of ``roles`` present in ``frame`` fall below the trust bar.

    Roles absent from the frame are skipped (their absence is handled separately by the
    rule runner). Returns the offending roles in the order given, for gating a rule's
    required inputs. Plant points are judged on their equipment's running samples when the frame
    carries a run signal (0.92, #66; see :func:`plant_gates`).
    """
    present = [r for r in roles if r in frame.columns]
    pg = plant_gates(frame)  # 0.92 (#66): plant points judged on running samples
    health = {
        r: sensor_trust(
            frame[r],
            r,
            expected_freq=expected_freq,
            run_gate=pg[r][0] if r in pg else None,
            run_gate_source=pg[r][1] if r in pg else None,
        )
        for r in present
    }
    frame_checks(frame, health)
    return [r for r in present if health[r].trust < min_trust]


@dataclass
class ConsistencyResult:
    """A cross-sensor physical-consistency check over a role-frame."""

    check: str
    n_checked: int
    violation_frac: float
    severity: str  # "ok" | "warn" | "fault" | "info"
    summary: str
    metrics: dict = field(default_factory=dict)
    caveats: list = field(default_factory=list)  # what could not be evaluated, and why

    def as_dict(self) -> dict:
        """Return the consistency result as a plain dict."""
        d = self.__dict__.copy()
        d["metrics"] = dict(self.metrics)
        d["caveats"] = list(self.caveats)
        return d


def mixing_consistency(
    frame: pd.DataFrame, *, tol_f: float = 5.0, warn_frac: float = 0.05, fault_frac: float = 0.20
) -> ConsistencyResult:
    """Mixed-air temp must lie between outdoor- and return-air temp (it is their blend).

    A persistent violation (MAT outside [min(OAT,RAT) - tol, max(OAT,RAT) + tol]) means
    one of the three temperature sensors is miscalibrated or swapped -- a sensor fault
    that would otherwise corrupt the OA-fraction and economizer diagnostics.
    """
    need = (Role.MIXED_AIR_TEMP, Role.OAT, Role.RETURN_AIR_TEMP)
    if any(r not in frame.columns for r in need):
        return ConsistencyResult(
            "mixing_temperature_order",
            0,
            float("nan"),
            "info",
            "need mixed-air, outdoor-air, and return-air temps",
        )
    w = frame[list(need)].dropna()
    if len(w) < 10:
        return ConsistencyResult(
            "mixing_temperature_order",
            len(w),
            float("nan"),
            "info",
            "insufficient overlapping samples",
        )
    lo = np.minimum(w[Role.OAT], w[Role.RETURN_AIR_TEMP]) - tol_f
    hi = np.maximum(w[Role.OAT], w[Role.RETURN_AIR_TEMP]) + tol_f
    viol = float(((w[Role.MIXED_AIR_TEMP] < lo) | (w[Role.MIXED_AIR_TEMP] > hi)).mean())
    if viol >= fault_frac:
        severity = "fault"
    elif viol >= warn_frac:
        severity = "warn"
    else:
        severity = "ok"
    return ConsistencyResult(
        check="mixing_temperature_order",
        n_checked=int(len(w)),
        violation_frac=round(viol, 4),
        severity=severity,
        summary=(
            f"mixed-air temp outside [OAT,RAT]±{tol_f:.0f}F for "
            f"{100 * viol:.0f}% of {len(w)} samples"
        ),
    )


# --------------------------------------------------------------------------------------------- #
# Copied points, gap-filled segments, and cross-unit identity                                   #
# --------------------------------------------------------------------------------------------- #

# Roles whose reading is a continuously-varying *measurement* -- the ones for which an exact copy
# of another role's data is impossible by coincidence.
_MEASURED_ROLES: frozenset = _SENSOR_ROLES | frozenset({Role.OA_AIRFLOW, Role.OUTDOOR_CO2})

# Roles that measure a quantity every unit on a site shares (the same outdoor air), so a very high
# cross-unit correlation is expected physics rather than a copied or filled source.
_SHARED_AMBIENT_ROLES: frozenset = frozenset(
    {Role.OAT, Role.WETBULB_TEMP, Role.OUTDOOR_RH, Role.OUTDOOR_CO2}
)


def _slug(role) -> str:
    return role.value if isinstance(role, Role) else str(role)


def _runs(mask: np.ndarray):
    """``(start, end)`` index pairs (end exclusive) of the runs of True in ``mask``."""
    padded = np.concatenate(([0], mask.astype("int8"), [0]))
    edges = np.diff(padded)
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)))


def mixing_flow_consistency(
    frame: pd.DataFrame,
    *,
    min_delta_t: float = 10.0,
    min_airflow_frac: float = 0.2,
    ratio_uncertainty: float = 0.10,
    sensor_tol_f: float = 2.0,
    min_samples: int = 48,
) -> ConsistencyResult:
    """Mixed-air temp vs the flow-weighted blend of outdoor and return air.

    :func:`mixing_consistency` only asks whether MAT lies *between* OAT and RAT, which a sensor
    reading several degrees warm usually still does. When the unit also has an outdoor-air flow
    station (``OA_AIRFLOW``) and a supply flow (``AIRFLOW``), energy balance gives the value MAT
    *should* read: with the outdoor-air fraction ``f = OA / SA``,

        expected MAT = f * OAT + (1 - f) * RAT

    and the median residual ``MAT - expected`` is the bias of the mixing measurement set.

    Samples are used only while the fan is meaningfully on (supply flow at least
    ``min_airflow_frac`` of its 95th percentile), with ``0 <= f <= 1``, and with
    ``|OAT - RAT| >= min_delta_t`` so the balance can distinguish anything. The share of running
    samples with ``OA > SA`` -- physically impossible -- is reported separately as evidence
    against the flow stations.

    **The flow stations' own accuracy is unknown**, so the result is screening-grade: a
    ``ratio_uncertainty`` (default +/-10 % of ``f``, a middling airflow-station figure) is
    propagated into an expected-MAT band ``ratio_uncertainty * f * |OAT - RAT|``, and severity is
    ``warn`` only when the bias clears that band plus ``sensor_tol_f``; never ``fault``. The bias
    belongs to the *set* (MAT, OAT, RAT and both flows): an OAT sensor reading low pulls the
    expected MAT low by ``f`` times its error -- check it with
    :func:`camber.sensordrift.compare_to_reference`. A bias that grows in cold weather is the
    classic single-point MAT sensor stratification signature; the colder- and warmer-half biases
    are reported for that reason.
    """
    check = "mixing_flow_balance"
    need = (
        Role.MIXED_AIR_TEMP,
        Role.OAT,
        Role.RETURN_AIR_TEMP,
        Role.OA_AIRFLOW,
        Role.AIRFLOW,
    )
    missing = [_slug(r) for r in need if r not in frame.columns]
    if missing:
        return ConsistencyResult(
            check,
            0,
            float("nan"),
            "info",
            f"need {', '.join(missing)}",
            caveats=[f"not evaluated: {', '.join(missing)} not mapped"],
        )
    probe = [r for r in (*need, Role.SUPPLY_AIR_TEMP) if r in frame.columns]
    copied = copied_signal_consistency(frame[probe])
    if copied.severity == "fault":
        a, b = copied.metrics["pairs"][0]["roles"]
        return ConsistencyResult(
            check,
            0,
            float("nan"),
            "info",
            f"{a} and {b} carry the same data",
            caveats=[f"not evaluated: {a} is a copy of {b} (or vice versa) -- {copied.summary}"],
        )
    w = frame[list(need)].dropna()
    sa = w[Role.AIRFLOW]
    running = w[sa >= min_airflow_frac * float(sa.quantile(0.95))] if len(w) else w
    if len(running) == 0:
        return ConsistencyResult(
            check,
            0,
            float("nan"),
            "info",
            "no samples with the fan running",
            caveats=["not evaluated: no running samples"],
        )
    f = running[Role.OA_AIRFLOW] / running[Role.AIRFLOW]
    oa_exceeds = float((f > 1.0).mean())
    dt = running[Role.OAT] - running[Role.RETURN_AIR_TEMP]
    use = f.between(0.0, 1.0) & (dt.abs() >= min_delta_t)
    u = running[use]
    fu, dtu = f[use], dt[use]
    caveats = [
        "flow-station accuracy unknown: the expected-MAT band assumes "
        f"+/-{100 * ratio_uncertainty:.0f} % on the OA fraction",
        "the bias belongs to the MAT/OAT/RAT/flow set -- an OAT or RAT error shifts it too",
    ]
    if len(u) < min_samples:
        return ConsistencyResult(
            check,
            int(len(u)),
            float("nan"),
            "info",
            f"only {len(u)} usable samples (< {min_samples}) with |OAT-RAT| >= {min_delta_t:g}F",
            metrics={"oa_exceeds_supply_frac": round(oa_exceeds, 4)},
            caveats=["not evaluated: too few informative samples", *caveats],
        )
    expected = fu * u[Role.OAT] + (1.0 - fu) * u[Role.RETURN_AIR_TEMP]
    resid = u[Role.MIXED_AIR_TEMP] - expected
    band = ratio_uncertainty * fu * dtu.abs()
    bias = float(resid.median())
    band_med = float(band.median())
    oat_mid = float(u[Role.OAT].median())
    cold, warm = resid[u[Role.OAT] <= oat_mid], resid[u[Role.OAT] > oat_mid]
    viol = float((resid.abs() > band + sensor_tol_f).mean())
    severity = "warn" if abs(bias) > band_med + sensor_tol_f else "ok"
    return ConsistencyResult(
        check=check,
        n_checked=int(len(u)),
        violation_frac=round(viol, 4),
        severity=severity,
        summary=(
            f"MAT reads {bias:+.1f}F vs the flow-weighted OA/RA blend (band +/-{band_med:.1f}F "
            f"from flow uncertainty) over {len(u)} samples; colder half {cold.median():+.1f}F, "
            f"warmer half {warm.median():+.1f}F"
        ),
        metrics={
            "bias_f": round(bias, 2),
            "bias_cold_half_f": round(float(cold.median()), 2),
            "bias_warm_half_f": round(float(warm.median()), 2),
            "oat_split_f": round(oat_mid, 1),
            "expected_band_f": round(band_med, 2),
            "median_oa_fraction": round(float(fu.median()), 3),
            "oa_exceeds_supply_frac": round(oa_exceeds, 4),
        },
        caveats=caveats,
    )


# NDIR CO2 accuracy is typically quoted as +/-(30 ppm + 3 % of reading); two independent sensors
# compared against each other combine in quadrature.
_CO2_ABS_ACC = 30.0
_CO2_REL_ACC = 0.03


def co2_outdoor_consistency(
    frame: pd.DataFrame, *, warn_frac: float = 0.05, fault_frac: float = 0.20
) -> ConsistencyResult:
    """Zone CO2 must not sit below outdoor CO2: a building has no CO2 sink.

    Indoor CO2 is outdoor air plus what occupants add, so it decays *towards* the outdoor level
    when a space empties and rises above it when occupied; it cannot persistently read below it.
    A sample counts as a violation when indoor is below outdoor by more than the two sensors'
    combined accuracy (``sqrt(2) * (30 ppm + 3 % of outdoor)``, ~60 ppm at 450 ppm). When an
    ``OCCUPANCY`` role is mapped only occupied samples are judged (where indoor must be *above*
    outdoor); otherwise all samples, with a caveat. Severity follows
    :func:`mixing_consistency`'s ``warn_frac`` / ``fault_frac`` convention, and on the occupied
    basis a *median* indoor reading at or below outdoor is at least ``warn`` on its own: occupants
    only add CO2, so that is a calibration offset between the two sensors of at least the median
    gap even when every single sample is within the sensors' accuracy.

    The check cannot say which sensor is wrong. Two common causes are named in the caveats: an
    indoor NDIR sensor with automatic baseline calibration (ABC) assumes it regularly sees ~400
    ppm and drags its floor there, below a local ambient that is often 420-480 ppm; or the outdoor
    sensor reads high.
    """
    check = "co2_indoor_vs_outdoor"
    if Role.CO2 not in frame.columns or Role.OUTDOOR_CO2 not in frame.columns:
        return ConsistencyResult(
            check,
            0,
            float("nan"),
            "info",
            "need zone and outdoor CO2",
            caveats=["not evaluated: zone or outdoor CO2 not mapped"],
        )
    w = frame[[Role.CO2, Role.OUTDOOR_CO2]].dropna()
    caveats = [
        "cannot tell which sensor is wrong: an indoor NDIR with automatic baseline calibration "
        "pins its floor near 400 ppm, or the outdoor sensor reads high"
    ]
    if Role.OCCUPANCY in frame.columns:
        occ = frame[Role.OCCUPANCY].reindex(w.index) > 0.5
        judged = w[occ.fillna(False).to_numpy()]
        basis = "occupied"
    else:
        judged = w
        basis = "all"
        caveats.append("no occupancy mapped: judged on all samples")
    if len(judged) < 10:
        return ConsistencyResult(
            check,
            int(len(judged)),
            float("nan"),
            "info",
            "insufficient overlapping samples",
            caveats=["not evaluated: too few overlapping samples", *caveats],
        )
    d = judged[Role.CO2] - judged[Role.OUTDOOR_CO2]
    tol = np.sqrt(2.0) * (_CO2_ABS_ACC + _CO2_REL_ACC * judged[Role.OUTDOOR_CO2])
    viol = float((d < -tol).mean())
    severity = "fault" if viol >= fault_frac else ("warn" if viol >= warn_frac else "ok")
    med = float(d.median())
    offset_note = ""
    if basis == "occupied" and med <= 0.0 and severity == "ok":
        # occupants only add CO2: a typical occupied reading at/below outdoor is a calibration
        # offset between the two of at least |median|, even when each sample is within spec
        severity = "warn"
        offset_note = f"; occupied median at/below outdoor => offset of at least {-med:.0f} ppm"
    return ConsistencyResult(
        check=check,
        n_checked=int(len(judged)),
        violation_frac=round(viol, 4),
        severity=severity,
        summary=(
            f"zone CO2 below outdoor by more than sensor accuracy for {100 * viol:.0f}% of "
            f"{len(judged)} {basis} samples (median indoor-outdoor {med:+.0f} ppm){offset_note}"
        ),
        metrics={
            "basis": basis,
            "median_indoor_minus_outdoor_ppm": round(med, 1),
            "at_or_below_outdoor_frac": round(float((d <= 0).mean()), 4),
            "median_tolerance_ppm": round(float(tol.median()), 1),
        },
        caveats=caveats,
    )


def copied_signal_consistency(
    frame: pd.DataFrame, *, min_changing: int = 24, rel_tol: float = 1e-6
) -> ConsistencyResult:
    """Two distinct measured roles on one piece of equipment carrying the *same* data.

    A return-air sensor that reads bit-for-bit what the supply-air sensor reads, sample after
    sample while both change, is not measuring return air: the point was re-bound to the wrong
    object, or a trend was copied. Two independent sensors on different quantities cannot agree
    exactly by coincidence -- they have independent noise and measure different air -- so the
    check needs no statistics: it looks for runs of consecutive samples where the two roles are
    equal (to ``rel_tol``) and counts how many of those samples were *changing*. Co-flat stretches
    (both zero while the unit is off, both at a limit) don't count, because equality there is
    not evidence. A run carrying ``min_changing`` (default 24: a day of hourly data) changing
    samples is flagged as a fault.

    Only measured roles are compared -- a setpoint equal to another setpoint, or a command equal to
    its feedback, is normal. The check cannot say *which* of the pair is the copy; its summary
    names both.
    """
    check = "copied_signal"
    cols = [c for c in frame.columns if c in _MEASURED_ROLES]
    if len(cols) < 2:
        return ConsistencyResult(
            check,
            0,
            float("nan"),
            "info",
            "need two or more measured roles to compare",
            caveats=["not evaluated: fewer than two measured roles on this equipment"],
        )
    pairs: list[dict[str, Any]] = []
    n_checked = 0
    for i, a in enumerate(cols):
        for b in cols[i + 1 :]:
            w = frame[[a, b]].dropna()
            if len(w) < 2:
                continue
            n_checked += len(w)
            x = w[a].to_numpy(dtype="float64")
            y = w[b].to_numpy(dtype="float64")
            same = np.abs(x - y) <= rel_tol * np.maximum(1.0, np.abs(x))
            changing = np.concatenate(([False], np.diff(x) != 0))
            best: tuple[int, Any, Any] = (0, None, None)
            for s, e in _runs(same):
                k = int(changing[s:e].sum())
                if k > best[0]:
                    best = (k, w.index[s], w.index[e - 1])
            n_changing = int(changing.sum())
            share = float((same & changing).sum() / n_changing) if n_changing else float("nan")
            pairs.append(
                {
                    "roles": (_slug(a), _slug(b)),
                    "identical_changing_frac": round(share, 4) if share == share else share,
                    "longest_identical_changing": best[0],
                    "start": None if best[1] is None else str(best[1]),
                    "end": None if best[2] is None else str(best[2]),
                }
            )
    if not pairs:
        return ConsistencyResult(
            check,
            0,
            float("nan"),
            "info",
            "insufficient overlapping samples",
            caveats=["not evaluated: no overlapping samples between measured roles"],
        )
    pairs.sort(key=lambda p: p["longest_identical_changing"], reverse=True)
    flagged = [p for p in pairs if p["longest_identical_changing"] >= min_changing]
    worst = pairs[0]
    frac = worst["identical_changing_frac"]
    if flagged:
        a, b = worst["roles"]
        summary = (
            f"{a} and {b} are identical for {worst['longest_identical_changing']} consecutive "
            f"changing samples ({worst['start']} .. {worst['end']}); one is a copy of the other"
        )
        severity = "fault"
    else:
        summary = f"no copied measured signals among {len(cols)} roles"
        severity = "ok"
    return ConsistencyResult(
        check=check,
        n_checked=n_checked,
        violation_frac=frac,
        severity=severity,
        summary=summary,
        metrics={"pairs": flagged or pairs[:1], "n_pairs_flagged": len(flagged)},
        caveats=["cannot tell which of the pair is the copy"] if flagged else [],
    )


def _repeat_share(values: np.ndarray) -> float:
    """Share of samples whose exact value occurs more than once in ``values``."""
    _, inverse, counts = np.unique(values, return_inverse=True, return_counts=True)
    return float((counts[inverse] > 1).mean())


# Granularity classes for gapfill_signature: a window is "quantised" when at least half its samples
# repeat a value seen elsewhere in it (the instrument's / trend's reporting resolution showing
# through), "continuous" when almost none do (every value unique -- interpolated, imputed or
# model-generated data, or a genuinely high-resolution float trend).
_QUANTISED_SHARE = 0.5
_CONTINUOUS_SHARE = 0.05


def gapfill_signature(
    series: pd.Series,
    *,
    window: str = "30D",
    min_window_samples: int = 500,
    min_day_samples: int = 12,
) -> ConsistencyResult:
    """Screen one raw point for the fingerprints of gap-filled or synthesized data.

    Data that was filled in rather than measured can be internally plausible -- right range, right
    shape, even well correlated with its neighbours -- so no single-series trust statistic sees
    it. Two fingerprints are both *physically* grounded and cheap:

    * **a change in value granularity.** A sensor's reported values share a finite resolution (the
      instrument, the BAS's COV increment, the trend's rounding), so in any month most samples
      repeat a value seen elsewhere that month. Interpolated, imputed or model-generated data is
      continuous: almost every value is unique. A point whose windows switch between the two
      classes was produced by two different processes; the continuous stretch is the suspect one.
    * **repeated days.** A measured, varying signal never reproduces a whole day exactly; a
      "copy last week's Tuesday" fill does.

    **Screening-grade, and honest about it.** This needs the *raw* trend: hourly (or any) means
    destroy the granularity signature, so a result where every window is continuous is reported as
    not evaluable, not as clean. A high-resolution float trend is continuous throughout and is
    likewise not evaluable. A granularity change can also be a trend reconfiguration or a sensor
    replacement -- the check says the segments came from different processes, not which one is
    true. Severity is at most ``warn``.
    """
    check = "gapfill_signature"
    s = pd.to_numeric(series, errors="coerce").dropna()
    s = s[~s.index.duplicated(keep="last")].sort_index()
    if len(s) < min_window_samples:
        return ConsistencyResult(
            check,
            len(s),
            float("nan"),
            "info",
            "insufficient samples",
            caveats=["not evaluated: too few samples"],
        )
    wins = []
    for _start, v in s.groupby(pd.Grouper(freq=window)):
        if len(v) < min_window_samples or v.nunique() < 3:
            continue
        share = _repeat_share(v.to_numpy(dtype="float64"))
        cls = (
            "quantised"
            if share >= _QUANTISED_SHARE
            else ("continuous" if share <= _CONTINUOUS_SHARE else "mixed")
        )
        wins.append((v.index[0], v.index[-1], round(share, 3), cls))

    # repeated non-constant days (exact values at the same times of day)
    seen: dict = {}
    repeated = []
    for day, v in s.groupby(s.index.normalize()):
        if len(v) < min_day_samples or v.nunique() < 3:
            continue
        key = (tuple(v.index - day), tuple(v.to_numpy(dtype="float64")))
        if key in seen:
            repeated.append((str(seen[key].date()), str(day.date())))
        else:
            seen[key] = day
    n_days = len(seen) + len(repeated)

    classes = {w[3] for w in wins}
    caveats = []
    findings = []
    continuous = [w for w in wins if w[3] == "continuous"]
    if "quantised" in classes and continuous:
        findings.append(
            f"value granularity changes: {len(continuous)} of {len(wins)} windows are continuous "
            f"(every value unique; {continuous[0][0].date()} .. {continuous[-1][1].date()}) while "
            "others repeat the sensor's reporting resolution -- the continuous stretch looks "
            "interpolated / imputed / model-filled"
        )
    elif wins and classes == {"continuous"}:
        caveats.append(
            "granularity not evaluable: every window is continuous (resampled means, a "
            "high-resolution float trend, or a series synthesized throughout)"
        )
    if repeated:
        findings.append(f"{len(repeated)} non-constant days repeat another day exactly")
    caveats.append(
        "screening-grade: a granularity change can also be a trend reconfiguration or a sensor "
        "swap; run on the raw trend, not resampled means"
    )
    susp = sum(1 for w in wins if w[3] == "continuous") if findings and continuous else 0
    frac = (susp / len(wins)) if wins else float("nan")
    return ConsistencyResult(
        check=check,
        n_checked=int(len(s)),
        violation_frac=round(frac, 4) if frac == frac else frac,
        severity="warn" if findings else ("info" if not wins else "ok"),
        summary="; ".join(findings) if findings else "no gap-fill signature found",
        metrics={
            "windows": [
                {"start": str(a), "end": str(b), "repeat_share": sh, "class": c}
                for a, b, sh, c in wins
            ],
            "n_days": n_days,
            "repeated_days": repeated[:20],
        },
        caveats=caveats,
    )


def cross_unit_identity(
    series_by_equip: dict,
    role=None,
    *,
    max_r: float = 0.995,
    window: str = "30D",
    resample: str | None = "1h",
    min_samples: int = 168,
) -> ConsistencyResult:
    """Screen one role across several units for implausibly identical behaviour.

    Four rooftop units each measure their *own* outdoor-air flow: their dampers, return
    conditions and flow stations are independent, so their readings share a schedule and the
    weather but not their noise. Per window (default 30 days) of hourly means, a pairwise Pearson
    ``r >= max_r`` (default 0.995 -- under 1 % of one unit's variance left unexplained by a
    straight line through another's) is more agreement than independent measurement allows, and is
    the signature of a shared, copied, modelled or gap-filled source (a matrix-factorisation fill
    reconstructs every unit from the same few daily factors).

    Screening-grade: units driven by one common command can legitimately track each other
    closely, so a hit says "look at where this data came from", not "fault" -- severity is at most
    ``warn``. Roles that measure shared ambient air (OAT, outdoor RH/wet-bulb/CO2) are not
    evaluated: identical readings there are physics. Neither are commands, positions and speeds
    (only when ``role`` is given): tracking a shared command is their job.
    """
    check = "cross_unit_identity"
    if role is not None and role not in _MEASURED_ROLES:
        return ConsistencyResult(
            check,
            0,
            float("nan"),
            "info",
            f"{_slug(role)} is not a measured quantity",
            caveats=[
                "not evaluated: commands, positions and speeds legitimately track a shared "
                "command across units"
            ],
        )
    if role in _SHARED_AMBIENT_ROLES:
        return ConsistencyResult(
            check,
            0,
            float("nan"),
            "info",
            f"{_slug(role)} measures shared ambient air",
            caveats=["not evaluated: every unit legitimately reads the same outdoor air"],
        )
    series = {k: pd.to_numeric(v, errors="coerce").dropna() for k, v in series_by_equip.items()}
    series = {k: v[~v.index.duplicated(keep="last")] for k, v in series.items() if len(v)}
    if len(series) < 2:
        return ConsistencyResult(
            check,
            0,
            float("nan"),
            "info",
            "need the same role on two or more units",
            caveats=["not evaluated: fewer than two units"],
        )
    frame = pd.DataFrame(series)
    if resample:
        frame = frame.resample(resample).mean()
    names = list(frame.columns)
    hits: list[dict[str, Any]] = []
    n_tested = 0
    for start, w in frame.groupby(pd.Grouper(freq=window)):
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                p = w[[a, b]].dropna()
                if len(p) < min_samples or p[a].std() == 0 or p[b].std() == 0:
                    continue
                n_tested += 1
                r = float(np.corrcoef(p[a], p[b])[0, 1])
                if r >= max_r:
                    hits.append(
                        {"units": (str(a), str(b)), "start": str(start.date()), "r": round(r, 4)}
                    )
    if n_tested == 0:
        return ConsistencyResult(
            check,
            0,
            float("nan"),
            "info",
            "insufficient overlapping samples",
            caveats=["not evaluated: no window had enough overlap between units"],
        )
    caveats = [
        "screening-grade: units under one shared command can legitimately track each other; "
        "check the data's provenance (gap-fill, copied or modelled points) before acting"
    ]
    if hits:
        starts = sorted({h["start"] for h in hits})
        summary = (
            f"{len(hits)} of {n_tested} unit-pair windows agree at r >= {max_r} "
            f"({starts[0]} .. {starts[-1]}): implausibly identical for independent sensors"
        )
    else:
        summary = f"no unit pair agrees at r >= {max_r} in any window"
    return ConsistencyResult(
        check=check,
        n_checked=n_tested,
        violation_frac=round(len(hits) / n_tested, 4),
        severity="warn" if hits else "ok",
        summary=summary,
        metrics={"hits": hits[:50], "n_windows_tested": n_tested},
        caveats=caveats,
    )


# --------------------------------------------------------------------------------------------- #
# 0.92 (#16): the copied-signal and mixed-air flow-balance checks feed sensor trust      #
# --------------------------------------------------------------------------------------------- #

#: A copied stretch is judged at its edges: this much of each point's data just outside the stretch
#: against as much just inside it.
_COPY_EDGE = pd.Timedelta(days=7)
_COPY_EDGE_MIN_SAMPLES = 12
#: The copy is the point that closes at least this share of the pair's gap at an edge of the
#: stretch, and moves at least this many times as far as the other point.
_COPY_GAP_SHARE = 0.5
_COPY_SHIFT_RATIO = 2.0

#: The roles the mixed-air balance ``MAT = f*OAT + (1-f)*RAT`` ties together.
_MIXING_TEMP_ROLES = (Role.MIXED_AIR_TEMP, Role.OAT, Role.RETURN_AIR_TEMP)


def _as_role(slug):
    try:
        return Role(slug)
    except ValueError:
        return slug


def _edge(series: pd.Series, lo, hi, *, left_open: bool = False, right_open: bool = False):
    """Median of ``series`` on ``[lo, hi]`` (open ends dropped), ``None`` with too few samples."""
    s = pd.to_numeric(series, errors="coerce").dropna().loc[lo:hi]
    if left_open and len(s) and s.index[0] == pd.Timestamp(lo):
        s = s.iloc[1:]
    if right_open and len(s) and s.index[-1] == pd.Timestamp(hi):
        s = s.iloc[:-1]
    return float(s.median()) if len(s) >= _COPY_EDGE_MIN_SAMPLES else None


def _copy_blame(frame: pd.DataFrame, a, b, start, end) -> tuple[list, str]:
    """Which of a copied pair is the copy: ``([roles to blame], basis)``.

    Before (and after) the identical stretch the two points read different quantities, so there
    is a gap between them; inside it they agree. The original keeps measuring through the edge,
    so it barely moves there, while the copy jumps across the gap to the other point's level (a
    return air suddenly reading supply-air temperatures). At each edge with data on both sides,
    the median of the week just outside is compared with the week just inside. The point that
    closes at least half the gap and moves at least twice as far as the other is the copy; when
    neither stands out -- or the stretch has no data around it -- both are blamed: the check
    cannot tell which is the copy.
    """
    t0, t1 = pd.Timestamp(start), pd.Timestamp(end)
    move = {a: 0.0, b: 0.0}
    gap = 0.0
    judged = False
    for out_lo, out_hi, in_lo, in_hi, before in (
        (t0 - _COPY_EDGE, t0, t0, t0 + _COPY_EDGE, True),
        (t1, t1 + _COPY_EDGE, t1 - _COPY_EDGE, t1, False),
    ):
        o = {
            r: _edge(frame[r], out_lo, out_hi, right_open=before, left_open=not before)
            for r in (a, b)
        }
        i = {r: _edge(frame[r], in_lo, in_hi) for r in (a, b)}
        if any(v is None for v in (*o.values(), *i.values())):
            continue
        judged = True
        gap += abs(o[a] - o[b])
        for r in (a, b):
            move[r] += abs(i[r] - o[r])
    if judged and gap > 0:
        for r, other in ((a, b), (b, a)):
            if move[r] >= _COPY_GAP_SHARE * gap and move[r] >= _COPY_SHIFT_RATIO * move[other]:
                return [r], "level_shift"
    return [a, b], "undetermined"


def _copied_stretch(frame: pd.DataFrame, a, b, *, min_changing: int = 24, rel_tol: float = 1e-6):
    """``(mask, first, last)`` for every identical stretch of ``a`` and ``b`` that carries at least
    ``min_changing`` changing samples (the flagging rule of :func:`copied_signal_consistency`, over
    *all* such stretches rather than the longest); ``mask`` is on ``frame.index``."""
    w = frame[[a, b]].apply(pd.to_numeric, errors="coerce").dropna()
    mask = pd.Series(False, index=frame.index)
    if len(w) < 2:
        return mask, None, None
    x = w[a].to_numpy(dtype="float64")
    y = w[b].to_numpy(dtype="float64")
    same = np.abs(x - y) <= rel_tol * np.maximum(1.0, np.abs(x))
    changing = np.concatenate(([False], np.diff(x) != 0))
    hit = np.zeros(len(w), dtype=bool)
    for s0, e0 in _runs(same):
        if int(changing[s0:e0].sum()) >= min_changing:
            hit[s0:e0] = True
    if not hit.any():
        return mask, None, None
    mask.loc[w.index[hit]] = True
    return mask, w.index[hit][0], w.index[hit][-1]


def _check_copied_signal(frame: pd.DataFrame, health: dict) -> None:
    """A measured point that carries another measured point's data is not measuring its quantity.

    :func:`copied_signal_consistency` finds a copied pair; every identical stretch of the pair
    long enough to flag is then collected (a copy is often interrupted by gaps or a few samples
    that differ), and :func:`_copy_blame` decides which of the two is the copy. The copy is flagged
    ``copied_signal`` and its trust scaled by the share of its samples inside those stretches,
    capped at "suspect": the same treatment as a stuck run, since outside the stretches the point
    may be fine. A copy covering most of the window therefore reads "untrusted". When the check
    cannot tell which is the copy, both points are flagged and capped at "suspect", unscaled.
    """
    cols = [c for c in frame.columns if c in _MEASURED_ROLES]
    if len(cols) < 2 or not any(c in health for c in cols):
        return
    if not isinstance(frame.index, pd.DatetimeIndex):
        return
    res = copied_signal_consistency(frame[cols])
    if res.severity != "fault":
        return
    for pair in res.metrics.get("pairs", []):
        a, b = (_as_role(x) for x in pair["roles"])
        if a not in frame.columns or b not in frame.columns:
            continue
        mask, first, last = _copied_stretch(frame, a, b)
        if first is None:
            continue
        blamed, basis = _copy_blame(frame, a, b, first, last)
        for role in blamed:
            if role not in health:
                continue
            valid = pd.to_numeric(frame[role], errors="coerce").notna()
            share = float((mask & valid).sum()) / max(int(valid.sum()), 1)
            other = b if role == a else a
            check = {
                "check": "copied_signal",
                "copy_of": _slug(other),
                "start": str(first),
                "end": str(last),
                "longest_identical_changing": pair["longest_identical_changing"],
                "n_identical_samples": int(mask.sum()),
                "share_of_samples": round(share, 4),
                "blame": basis,  # "level_shift" (this point is the copy) | "undetermined"
            }
            # the identified copy loses the share of its samples that are the other point's; when
            # the check cannot tell which is the copy, both are only capped at "suspect" (and
            # flagged, so triage makes their findings conditional) -- neither is known to be bad
            _mark(
                health,
                role,
                "copied_signal",
                check,
                cap=_SUSPECT_CAP,
                share=share if basis == "level_shift" else 0.0,
            )


def _check_mixing_balance(frame: pd.DataFrame, health: dict) -> None:
    """A mixed-air temperature that fails the flow-weighted OA/RA balance lowers trust.

    Needs what :func:`mixing_flow_consistency` needs (MAT, OAT, RAT, OA and supply airflow). On a
    ``warn`` the mixed-air temperature is flagged ``mixing_balance`` and capped at "suspect": a
    single-point MAT sensor in a stratified mixing plenum is the usual culprit, and the check is
    screening-grade (the flow stations' accuracy is unknown), so it never makes a point
    "untrusted" on its own. OAT and RAT are flagged too, with the same check recorded, but their
    scores are left alone: the balance cannot say which of the set is wrong (an OAT reading low
    shifts it as well -- compare OAT with a reference), and the flag is enough for
    :func:`camber.rules.triage.sensor_causes` to make the unit's dependent findings conditional.
    """
    need = (*_MIXING_TEMP_ROLES, Role.OA_AIRFLOW, Role.AIRFLOW)
    if any(r not in frame.columns for r in need):
        return
    if not any(r in health for r in _MIXING_TEMP_ROLES):
        return
    res = mixing_flow_consistency(frame)
    if res.severity != "warn":
        return
    m = res.metrics
    check = {
        "check": "mixing_flow_balance",
        "bias_f": m.get("bias_f"),
        "expected_band_f": m.get("expected_band_f"),
        "bias_cold_half_f": m.get("bias_cold_half_f"),
        "bias_warm_half_f": m.get("bias_warm_half_f"),
        "median_oa_fraction": m.get("median_oa_fraction"),
        "n_checked": res.n_checked,
        "summary": res.summary,
    }
    _mark(health, Role.MIXED_AIR_TEMP, "mixing_balance", dict(check), cap=_SUSPECT_CAP)
    for role in (Role.OAT, Role.RETURN_AIR_TEMP):
        _mark(health, role, "mixing_balance", dict(check))
