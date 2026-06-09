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

Everything is our own method over public physical reasoning; pandas + numpy only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .ingest.quality import assess
from .model.roles import Role

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
    Role.CHW_FLOW: (-1.0, 1e6),
    Role.POWER: (-1.0, 1e7),
    Role.DUCT_STATIC: (-1.0, 20.0),
}

# Continuously-varying analog sensors, where a long flatline is a "stuck sensor"
# signal. For setpoints, status/commands, and valve/damper positions a constant value
# is normal, so flatline is NOT penalized there.
_SENSOR_ROLES: frozenset = frozenset({
    Role.OAT, Role.WETBULB_TEMP, Role.SUPPLY_AIR_TEMP, Role.MIXED_AIR_TEMP,
    Role.RETURN_AIR_TEMP, Role.SPACE_TEMP, Role.CHW_SUPPLY_TEMP, Role.CHW_RETURN_TEMP,
    Role.HW_SUPPLY_TEMP, Role.HW_RETURN_TEMP, Role.CW_SUPPLY_TEMP, Role.CW_RETURN_TEMP,
    Role.OUTDOOR_RH, Role.AIRFLOW, Role.CHW_FLOW, Role.POWER, Role.DUCT_STATIC,
})


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


@dataclass
class SensorTrust:
    """How much one point can be trusted, with the reasons."""

    role: str
    n: int
    coverage: float
    flatline_frac: float
    outlier_frac: float
    range_violation_frac: float   # NaN if the role has no bounds
    trust: float                  # 0..1 (1 = fully trustworthy)
    verdict: str                  # "trusted" | "suspect" | "untrusted"
    flags: list = field(default_factory=list)

    def as_dict(self) -> dict:
        """Return the trust result as a plain dict."""
        d = self.__dict__.copy()
        d["flags"] = list(self.flags)
        return d


def sensor_trust(series: pd.Series, role, *, expected_freq=None) -> SensorTrust:
    """Score one point's trustworthiness from quality stats + physical-range checks."""
    q = assess(series, expected_freq)
    rng = range_violation_frac(series, role)
    rng_pen = 0.0 if rng != rng else min(rng * 3.0, 1.0)   # out-of-range is serious
    trust = q.score * (1.0 - rng_pen)

    flags = []
    if q.coverage < 0.9:
        flags.append("low_coverage")
    if q.n_gaps > 0:
        flags.append("gaps")
    if q.outlier_frac > 0.05:
        flags.append("outliers")
    if rng == rng and rng > 0.01:
        flags.append("out_of_range")
    if role in _SENSOR_ROLES and q.flatline_frac > 0.5:
        flags.append("stuck")
        trust *= 0.5                                       # a stuck analog sensor is bad

    trust = round(float(max(0.0, min(1.0, trust))), 4)
    verdict = "trusted" if trust >= 0.8 else ("suspect" if trust >= 0.5 else "untrusted")
    return SensorTrust(
        role=role.value if isinstance(role, Role) else str(role),
        n=q.n, coverage=q.coverage, flatline_frac=q.flatline_frac,
        outlier_frac=q.outlier_frac,
        range_violation_frac=rng, trust=trust, verdict=verdict, flags=flags,
    )


def frame_sensor_health(frame: pd.DataFrame, *, expected_freq=None) -> dict:
    """Trust score every role-column of a role-frame -> ``{Role: SensorTrust}``."""
    return {role: sensor_trust(frame[role], role, expected_freq=expected_freq)
            for role in frame.columns}


def trusted_roles(frame: pd.DataFrame, *, min_trust: float = 0.5, expected_freq=None) -> set:
    """Roles whose data is trustworthy enough to diagnose on (trust >= ``min_trust``).

    A rule runner can intersect this with a rule's required roles and skip the rule
    when an input it depends on is below the bar -- "decline to fire on data we don't
    trust" rather than emit a fault that is really a sensor problem.
    """
    health = frame_sensor_health(frame, expected_freq=expected_freq)
    return {role for role, t in health.items() if t.trust >= min_trust}


@dataclass
class ConsistencyResult:
    """A cross-sensor physical-consistency check over a role-frame."""

    check: str
    n_checked: int
    violation_frac: float
    severity: str                 # "ok" | "warn" | "fault" | "info"
    summary: str

    def as_dict(self) -> dict:
        """Return the consistency result as a plain dict."""
        return self.__dict__.copy()


def mixing_consistency(frame: pd.DataFrame, *, tol_f: float = 5.0,
                       warn_frac: float = 0.05, fault_frac: float = 0.20) -> ConsistencyResult:
    """Mixed-air temp must lie between outdoor- and return-air temp (it is their blend).

    A persistent violation (MAT outside [min(OAT,RAT) - tol, max(OAT,RAT) + tol]) means
    one of the three temperature sensors is miscalibrated or swapped -- a sensor fault
    that would otherwise corrupt the OA-fraction and economizer diagnostics.
    """
    need = (Role.MIXED_AIR_TEMP, Role.OAT, Role.RETURN_AIR_TEMP)
    if any(r not in frame.columns for r in need):
        return ConsistencyResult("mixing_temperature_order", 0, float("nan"), "info",
                                 "need mixed-air, outdoor-air, and return-air temps")
    w = frame[list(need)].dropna()
    if len(w) < 10:
        return ConsistencyResult("mixing_temperature_order", len(w), float("nan"), "info",
                                 "insufficient overlapping samples")
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
        summary=(f"mixed-air temp outside [OAT,RAT]±{tol_f:.0f}F for "
                 f"{100 * viol:.0f}% of {len(w)} samples"),
    )
