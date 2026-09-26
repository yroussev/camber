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
    "frame_sensor_health",
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
    # hydronic flow (gpm) — same wide bound as the chilled-water flow role
    Role.HW_FLOW: (-1.0, 1e6),
    # pump differential head (psi) — wide; only rejects dropouts / impossible values
    Role.PUMP_HEAD: (-5.0, 300.0),
    # CO₂, ppm -- nothing real sits below ~250 (outdoor is ~420); above 10000 is a sentinel
    Role.CO2: (250.0, 10000.0),
    Role.OUTDOOR_CO2: (250.0, 1000.0),
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

    def as_dict(self) -> dict:
        """Return the trust result as a plain dict."""
        d = self.__dict__.copy()
        d["flags"] = list(self.flags)
        return d


def sensor_trust(series: pd.Series, role, *, expected_freq=None) -> SensorTrust:
    """Score one point's trustworthiness from quality stats + physical-range checks."""
    intermittent = role in _INTERMITTENT_ROLES
    q = assess(
        series,
        expected_freq,
        regime_aware=intermittent,
        shape_aware=True,
        scale_floor=_scale_floor(series, role),
    )
    rng = range_violation_frac(series, role)
    rng_pen = 0.0 if rng != rng else min(rng * 3.0, 1.0)  # out-of-range is serious
    trust = q.score * (1.0 - rng_pen)

    flags = []
    if q.coverage < 0.9:
        flags.append("low_coverage")
    if q.n_gaps > 0:
        flags.append("gaps")
    out_frac = q.outlier_frac
    if intermittent and q.n_regimes == 2 and q.regime_outlier_frac is not None:
        out_frac = q.regime_outlier_frac  # judged within each regime, so a duty cycle isn't a fault
    elif q.shape_outlier_frac is not None:
        # floored at the sensor's precision and two-sided for a skewed operating tail, so a
        # tightly-controlled point or a pump idling then ramping with load isn't a fault
        out_frac = q.shape_outlier_frac
    if out_frac > 0.05:
        flags.append("outliers")
    if rng == rng and rng > 0.01:
        flags.append("out_of_range")
    if role in _SENSOR_ROLES and q.flatline_frac > 0.5:
        flags.append("stuck")
        trust *= 0.5  # a stuck analog sensor is bad
    if percent_scale_suspect(series, role):
        flags.append("scale_suspect")  # a 0-100 signal mapped to a cfm role: check the mapping
    if role == Role.CO2:
        v = pd.to_numeric(series, errors="coerce").dropna()
        if len(v) and float((v < _CO2_AMBIENT_FLOOR).mean()) > 0.05:
            flags.append("below_ambient")  # reads under outdoor background: calibration suspect
    if q.n_regimes == 2:
        # Two meanings, both honest, neither a penalty: for a duty-cycled role this explains why
        # the outliers were read within-regime; for anything else it is new information -- a point
        # that should have one population has two, which is a "look here", not a verdict.
        flags.append("intermittent" if intermittent else "bimodal")

    trust = round(float(max(0.0, min(1.0, trust))), 4)
    verdict = "trusted" if trust >= 0.8 else ("suspect" if trust >= 0.5 else "untrusted")
    return SensorTrust(
        role=role.value if isinstance(role, Role) else str(role),
        n=q.n,
        coverage=q.coverage,
        flatline_frac=q.flatline_frac,
        outlier_frac=q.outlier_frac,
        range_violation_frac=rng,
        trust=trust,
        verdict=verdict,
        flags=flags,
    )


def frame_sensor_health(frame: pd.DataFrame, *, expected_freq=None) -> dict:
    """Trust score every role-column of a role-frame -> ``{Role: SensorTrust}``."""
    return {
        role: sensor_trust(frame[role], role, expected_freq=expected_freq) for role in frame.columns
    }


def trusted_roles(frame: pd.DataFrame, *, min_trust: float = 0.5, expected_freq=None) -> set:
    """Roles whose data is trustworthy enough to diagnose on (trust >= ``min_trust``).

    A rule runner can intersect this with a rule's required roles and skip the rule
    when an input it depends on is below the bar -- "decline to fire on data we don't
    trust" rather than emit a fault that is really a sensor problem.
    """
    health = frame_sensor_health(frame, expected_freq=expected_freq)
    return {role for role, t in health.items() if t.trust >= min_trust}


def untrusted_roles(
    frame: pd.DataFrame, roles, *, min_trust: float = 0.5, expected_freq=None
) -> list:
    """Which of ``roles`` present in ``frame`` fall below the trust bar.

    Roles absent from the frame are skipped (their absence is handled separately by the
    rule runner). Returns the offending roles in the order given, for gating a rule's
    required inputs.
    """
    out = []
    for r in roles:
        if r not in frame.columns:
            continue
        if sensor_trust(frame[r], r, expected_freq=expected_freq).trust < min_trust:
            out.append(r)
    return out


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
