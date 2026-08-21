"""Physics-grounded synthetic AHU generator -- characterize the air-side drift stack.

The air-side mirror of :mod:`camber.pumpsim` / :mod:`camber.driftsim`. The four AHU detectors +
:mod:`camber.ahudrift` diagnosis ship *screening-grade* thresholds whose false-alarm rate and
localization accuracy have never been measured, and real labelled AHU-fault data is scarce and
licence-encumbered -- scratch-only, never committed. This module fills the gap with a **physically
consistent synthetic generator** that produces ``(baseline, current)`` frame pairs for a healthy
air handler and for the standard air-side fault families, imposing each fault's known signature at a
graded severity, so the whole stack can be characterized end-to-end without shipping anyone's data.

Unlike the pump/chiller generators, the air-side channels are **coupled through the system curve**
(ΔP ∝ Q², per Chimack & Sellers). Total fan static = filter DP + coil DP + duct-static setpoint +
inlet DP; fan power tracks airflow × that total. So a *loading filter* raises the filter-DP
channel **and** fan power (the fan fights more upstream drop) -- the co-move the diagnosis needs:
filter loading presents as filter-up + power-up (→ air-path),
while a *fan-mechanical* fault raises power alone (→ fan). The coupling is emergent -- one filter
delta feeds both channels via the shared component-pressure model, not a hand-tuned pair.

The cooling coil is the single active coil (a heating regime is a documented follow-on), but the
mixing box **is** modeled: ``MIXED_AIR_TEMP`` is a genuine outdoor/return-air mix driven by a swept
OA-damper command, so the economizer detector's outdoor-air-fraction signal (and its ``outdoor-air``
locus) is exercised. To keep the cooling-coil signal invariant under that mix, ``SUPPLY_AIR_TEMP``
is derived as ``MAT - dt`` -- so the coil's air-ΔT ``MIXED_AIR_TEMP - SUPPLY_AIR_TEMP == dt`` by
construction, regardless of the mix. (A consequence: ``SUPPLY_AIR_TEMP`` is a derived, cosmetically
low value; it is only ever read as the difference ``MAT - SAT`` by the one coil detector, and no
suite gate checks its absolute level.) Deterministic given a seed; numpy / pandas only (core deps).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .ahudrift import AhuDriftDiagnosis, diagnose_ahu_drift
from .model.roles import Role

__all__ = [
    "AhuFault",
    "FAULTS",
    "SimulatedCase",
    "simulate_case",
    "make_cases",
    "build_ahu_suite",
    "diagnose_ahu_frames",
    "LocusConfusion",
    "locus_confusion",
]

# Healthy design points. Component pressures sit on the system curve (ΔP ∝ Q²); fan power tracks
# airflow x total static. Filter's share of total static (0.8 / 3.2 = 25%) sets the emergent
# filter->power coupling. Airflow swings only ±~18% of design so the quadratics stay locally near-
# linear for the detectors' degree-1 baseline fit.
_Q_DESIGN = 10000.0  # cfm
_Q_SWING = 1800.0
_FILT_DP0 = 0.8  # inH2O at design (the FILTER_DIFF_PRESS channel)
_COIL_DP0 = 0.6  # internal
_INLET0 = 0.3  # internal
_SP0 = 1.5  # duct-static setpoint, inH2O
_FAN_K = 8.0 / (_Q_DESIGN * (_FILT_DP0 + _COIL_DP0 + _SP0 + _INLET0))  # -> ~8 kW at design
_SAT = 55.0  # cooling supply-air setpoint
_DT_MID = 23.0  # design air-ΔT (MAT - SAT)
_DT_SWING = 8.0
_V0 = 15.0  # healthy cooling-valve intercept, %
_V_SLOPE = 1.5  # valve %/degF of ΔT
_CHWS = 44.0

# Outdoor-air / economizer mixing regime (the outdoor-air locus). MAT is a real OA/RA mix so the
# economizer detector reads OAF = 100*(RAT-MAT)/(RAT-OAT); OAT stays far enough from RAT that
# |RAT-OAT| clears the detector's DENOM_MIN_F=10 degenerate gate. The OA damper sits at a minimum-
# position band with whole-DAY economizing excursions (decorrelated from the 24h coil/airflow
# cycle), so a majority of samples stay non-economizing (the coil fit keeps its ΔT span) while the
# command still sweeps >10pp for the economizer fit.
_RAT = 74.0  # return-air temperature
_RAT_NOISE = 0.4
_OAT_MID = 56.0  # outdoor-air diurnal mean
_OAT_SWING = 6.0
_OAT_NOISE = 1.0
_OAT_CLIP = (48.0, 63.0)  # keeps |RAT-OAT| >= ~10 for the mixing ratio
_ECON_OAF_INTERCEPT = 8.0  # delivered OA fraction (%) at zero damper command
_ECON_OAF_SLOPE = 0.9  # delivered OA fraction (%) per % of damper command
_MAT_SENSOR_NOISE = 0.5  # mixed-air sensor noise (degF) -> the OAF residual scatter (~3pp)
_OA_MIN = 15.0  # minimum-position damper band floor, %
_ECON_SWING = 45.0  # economizing excursion amplitude, %
_ECON_PERIOD_D = 5.0  # whole-day economizing period (days)
_ECON_PHASE = 0.6
_DAMPER_NOISE = 1.0
_ECON_DAMPER_OPEN = 25.0  # economizing threshold (matches coil_valve's ECON_DAMPER_OPEN)

# Per-channel run-to-run noise (1-sigma), sized against the detector sigma floors.
_Q_NOISE = 120.0
_POWER_NOISE = 0.30
_FILT_NOISE = 0.025
_STATIC_NOISE = 0.03
_VALVE_NOISE = 2.5
_TEMP_NOISE = 0.3


@dataclass(frozen=True)
class AhuFault:
    """An air-side fault's signature: per-severity deltas on the channels it moves.

    ``expected_locus`` is the per-AHU verdict this fault should produce (the ground truth
    :func:`locus_confusion` scores against). ``d_filter_resist_frac`` is the shared resistance
    lever -- it feeds BOTH filter DP and fan power (the coupling); ``d_fan_power_frac`` is an
    independent fan-efficiency loss (power only); the rest are additive channel offsets. All are
    per unit of severity.
    """

    name: str
    expected_locus: str  # steady | fan | air-path | coil | outdoor-air | ahu-wide
    d_filter_resist_frac: float = 0.0  # filter resistance up -> filter DP AND fan power (coupled)
    d_fan_power_frac: float = 0.0  # fan efficiency loss -> fan power only
    d_static: float = 0.0  # additive duct-static residual at matched airflow (+up / -down)
    d_static_sp: float = 0.0  # additive setpoint change; static tracks it (a reset, must not alarm)
    d_cool_valve: float = 0.0  # additive cooling-valve % at matched ΔT (coil fouling)
    d_oa_fraction: float = 0.0  # delivered-OAF offset (pp) at matched command (+over / -under)
    extra_power_noise: float = 0.0  # bearing-drag roughness


# The standard air-side fault families and their coupled signatures. A loading filter moves filter
# DP AND fan power (→ air-path); a fan-mechanical fault moves power alone (→ fan); a static loss
# drops static and lifts power (→ fan); over-pressurization lifts static (→ air-path); coil fouling
# creeps the valve (→ coil); a static reset moves the setpoint, static tracking it (must NOT alarm).
FAULTS: dict[str, AhuFault] = {
    "fan_belt_slip": AhuFault("fan_belt_slip", "fan", d_fan_power_frac=0.05),
    "bearing_drag": AhuFault("bearing_drag", "fan", d_fan_power_frac=0.045, extra_power_noise=0.3),
    "filter_loading": AhuFault("filter_loading", "air-path", d_filter_resist_frac=0.18),
    "duct_static_loss": AhuFault("duct_static_loss", "fan", d_static=-0.13, d_fan_power_frac=0.035),
    "over_pressurization": AhuFault(
        "over_pressurization", "air-path", d_static=0.13, d_fan_power_frac=0.03
    ),
    "cooling_coil_fouling": AhuFault("cooling_coil_fouling", "coil", d_cool_valve=5.0),
    "econ_damper_leak": AhuFault("econ_damper_leak", "outdoor-air", d_oa_fraction=8.0),
    "econ_damper_stuck_closed": AhuFault(
        "econ_damper_stuck_closed", "outdoor-air", d_oa_fraction=-8.0
    ),
    "static_reset": AhuFault(
        "static_reset", "steady", d_static_sp=0.15
    ),  # negative: must not alarm
}


def _airflow(n: int, rng) -> np.ndarray:
    h = np.arange(n)
    q = _Q_DESIGN + _Q_SWING * np.sin((h % 24 - 8) / 24 * 2 * np.pi) + rng.normal(0, _Q_NOISE, n)
    return np.clip(q, 7000.0, 11500.0)


def _frame(n: int, *, start: str, seed: int, fault: AhuFault | None, severity: int):
    """One role-frame: healthy channels on the system curve, with ``fault`` deltas (x severity)."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="1h")
    q = _airflow(n, rng)
    qn = q / _Q_DESIGN
    s = float(severity)
    f = fault

    phi = f.d_filter_resist_frac * s if f else 0.0
    filt_dp_mean = _FILT_DP0 * (1 + phi) * qn * qn  # shared by the filter channel AND fan power
    power_tsp = filt_dp_mean + _COIL_DP0 * qn * qn + _SP0 + _INLET0 * qn * qn
    eta = 1.0 - (f.d_fan_power_frac * s if f else 0.0)
    power = _FAN_K * q * power_tsp / eta + rng.normal(
        0, _POWER_NOISE + (f.extra_power_noise * s if f else 0.0), n
    )

    d_static = f.d_static * s if f else 0.0
    d_static_sp = f.d_static_sp * s if f else 0.0
    static_sp = _SP0 + d_static_sp
    duct_static = static_sp + d_static + rng.normal(0, _STATIC_NOISE, n)

    dt = (
        _DT_MID
        + _DT_SWING * np.sin((np.arange(n) % 24 - 8) / 24 * 2 * np.pi)
        + rng.normal(0, _TEMP_NOISE, n)
    )
    valve = (
        _V0 + _V_SLOPE * dt + (f.d_cool_valve * s if f else 0.0) + rng.normal(0, _VALVE_NOISE, n)
    )
    # Hoisted out of the frame literal to lock its RNG-stream position (draw #6): all outdoor-air
    # mixing draws below are appended AFTER it, so the fan/filter/static channels stay bit-identical
    # to the pre-mixing generator and the existing four-locus confusion matrix is undisturbed.
    filt_channel = filt_dp_mean + rng.normal(0, _FILT_NOISE, n)

    # --- outdoor-air / economizer mixing (draws appended last; see the note above) ----------------
    hh = np.arange(n)
    oat = (
        _OAT_MID
        + _OAT_SWING * np.sin((hh % 24 - 15) / 24 * 2 * np.pi)
        + rng.normal(0, _OAT_NOISE, n)
    )
    oat = np.clip(oat, *_OAT_CLIP)
    rat = _RAT + rng.normal(0, _RAT_NOISE, n)
    day = hh // 24
    econ_frac = np.clip(np.sin(2 * np.pi * day / _ECON_PERIOD_D - _ECON_PHASE), 0.0, 1.0)
    oa_damper = np.clip(
        _OA_MIN + _ECON_SWING * econ_frac + rng.normal(0, _DAMPER_NOISE, n), 0.0, 100.0
    )
    d_oaf = f.d_oa_fraction * s if f else 0.0
    oaf_delivered = _ECON_OAF_INTERCEPT + _ECON_OAF_SLOPE * oa_damper + d_oaf
    mat = rat - (oaf_delivered / 100.0) * (rat - oat) + rng.normal(0, _MAT_SENSOR_NOISE, n)
    sat = mat - dt  # so the coil air-ΔT (MAT - SAT == dt) is invariant under the mix

    return pd.DataFrame(
        {
            Role.AIRFLOW: q,
            Role.SUPPLY_FAN_STATUS: np.ones(n),
            Role.POWER: power,
            Role.FILTER_DIFF_PRESS: filt_channel,
            Role.DUCT_STATIC: duct_static,
            Role.DUCT_STATIC_SP: np.full(n, static_sp),
            Role.COOL_VALVE: np.clip(valve, 0.0, 100.0),
            Role.MIXED_AIR_TEMP: mat,
            Role.SUPPLY_AIR_TEMP: sat,
            Role.OAT: oat,
            Role.RETURN_AIR_TEMP: rat,
            Role.CHW_SUPPLY_TEMP: np.full(n, _CHWS),
            Role.ECON_CMD: (oa_damper > _ECON_DAMPER_OPEN).astype(float),
            Role.OA_DAMPER: oa_damper,
        },
        index=idx,
    )


@dataclass(frozen=True)
class SimulatedCase:
    """A healthy-baseline / faulted-current frame pair with its ground-truth label."""

    equip: str
    baseline: object  # pandas.DataFrame
    current: object  # pandas.DataFrame
    fault_name: str  # "" for a healthy case
    severity: int
    expected_locus: str
    is_fault: bool

    def to_labeled(self, *, relevant=None):
        """A :class:`camber.driftvalidation.LabeledCase` for per-detector scoring.

        ``relevant`` restricts what counts as a positive for *this* detector (a set of fault names);
        default: any injected fault other than the ``static_reset`` negative.
        """
        from .driftvalidation import LabeledCase

        if relevant is None:
            fault = self.is_fault and self.expected_locus != "steady"
        else:
            fault = self.fault_name in relevant
        return LabeledCase(
            equip=self.equip,
            baseline=self.baseline,
            current=self.current,
            fault=fault,
            name=f"{self.fault_name or 'healthy'}@{self.severity}",
        )


def simulate_case(
    fault_name: str | None = None,
    severity: int = 0,
    *,
    equip: str = "AHU_SIM",
    n: int = 24 * 30,
    seed: int = 0,
    baseline_start: str = "2025-05-01",
    current_start: str = "2025-06-01",
) -> SimulatedCase:
    """One case: a healthy baseline and a current period (healthy or ``fault_name`` at that sev)."""
    fault = None if (fault_name is None or severity <= 0) else FAULTS[fault_name]
    baseline = _frame(n, start=baseline_start, seed=seed, fault=None, severity=0)
    current = _frame(n, start=current_start, seed=seed + 1, fault=fault, severity=severity)
    return SimulatedCase(
        equip=equip,
        baseline=baseline,
        current=current,
        fault_name="" if fault is None else fault.name,
        severity=0 if fault is None else severity,
        expected_locus="steady" if fault is None else fault.expected_locus,
        is_fault=fault is not None,
    )


def make_cases(
    *,
    faults: list | None = None,
    severities: tuple = (1, 2, 3, 4),
    n_healthy: int = 8,
    n_per_fault: int = 2,
    seed0: int = 0,
) -> list:
    """A labelled set: ``n_healthy`` negatives plus ``n_per_fault`` cases per fault x severity."""
    names = list(FAULTS) if faults is None else list(faults)
    cases: list = []
    seed = seed0
    for _ in range(n_healthy):
        cases.append(simulate_case(None, 0, seed=seed))
        seed += 2
    for name in names:
        for sev in severities:
            for _ in range(n_per_fault):
                cases.append(simulate_case(name, sev, seed=seed))
                seed += 2
    return cases


def build_ahu_suite(store, *, site: str = "SIM", run_id: str = "SIM", coils=("cooling",)) -> list:
    """The air-side drift detectors that feed the AHU diagnosis, sharing one ``store``."""
    from .rules.coil_valve_rule import CoilValveDrift
    from .rules.duct_static_rule import DuctStaticControlDrift
    from .rules.economizer_damper_rule import EconomizerDamperDrift
    from .rules.fan_efficiency_rule import FanEfficiencyDrift
    from .rules.filter_loading_rule import FilterLoadingDrift

    suite = [
        FanEfficiencyDrift(store, site=site, run_id=run_id),
        FilterLoadingDrift(store, site=site, run_id=run_id),
        DuctStaticControlDrift(store, site=site, run_id=run_id),
        EconomizerDamperDrift(store, site=site, run_id=run_id),
    ]
    suite += [CoilValveDrift(store, site=site, run_id=run_id, coil=c) for c in coils]
    return suite


def diagnose_ahu_frames(
    baseline,
    current,
    *,
    equip: str = "AHU_SIM",
    site: str = "SIM",
    run_id: str = "SIM",
    coils=("cooling",),
) -> AhuDriftDiagnosis:
    """Run the whole AHU drift suite on one ``(baseline, current)`` pair and diagnose the AHU."""
    from .store.modelstore import BaselineStore

    store = BaselineStore()
    suite = build_ahu_suite(store, site=site, run_id=run_id, coils=coils)
    findings = [rule.analyze_periods(equip, baseline, current) for rule in suite]
    return diagnose_ahu_drift(findings, equip=equip)


@dataclass
class LocusConfusion:
    """How well the AHU diagnosis' ``locus`` matches ground truth over a set of cases."""

    n: int
    accuracy: float
    matrix: dict = field(default_factory=dict)  # expected_locus -> {predicted_locus: count}

    def as_dict(self) -> dict:
        """Flat, JSON-friendly view (matrix rows become plain dicts)."""
        return {
            "n": self.n,
            "accuracy": self.accuracy,
            "matrix": {k: dict(v) for k, v in self.matrix.items()},
        }


def locus_confusion(
    cases, *, min_severity: int = 0, site: str = "SIM", run_id: str = "SIM"
) -> LocusConfusion:
    """Score the AHU diagnosis' ``locus`` against each case's ``expected_locus``.

    ``min_severity`` keeps only fault cases at or above that severity (healthy cases are always
    kept) -- to read localization accuracy on the clearer faults apart from the marginal ones.
    """
    matrix: dict = {}
    n = 0
    correct = 0
    for case in cases:
        if case.is_fault and case.severity < min_severity:
            continue
        diag = diagnose_ahu_frames(
            case.baseline, case.current, equip=case.equip, site=site, run_id=run_id
        )
        row = matrix.setdefault(case.expected_locus, Counter())
        row[diag.locus] += 1
        n += 1
        if diag.locus == case.expected_locus:
            correct += 1
    accuracy = round(correct / n, 4) if n else float("nan")
    return LocusConfusion(n=n, accuracy=accuracy, matrix=matrix)
