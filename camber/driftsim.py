"""Physics-grounded synthetic chiller generator — characterize the drift stack without a dataset.

The chiller drift detectors and the :mod:`camber.chillerdiag` roll-up ship *screening-grade* /
*provisional-untuned* thresholds because their false-alarm rate and localization accuracy have never
been measured. Real labelled chiller-fault data with the refrigerant-side channels these detectors
read (approach temperatures, discharge / suction pressures, subcooling, superheat) is scarce and
usually licence-encumbered. This module fills the gap with a **physically consistent synthetic
generator**: it produces ``(baseline, current)`` role-frame pairs for a healthy chiller and for the
standard centrifugal-chiller fault families, with the fault's known signature imposed on the right
channels at a graded severity, so the whole stack can be characterized end-to-end and its thresholds
tuned (via :mod:`camber.driftvalidation`) without shipping anyone's data.

It is a *signature* model, not a full thermodynamic simulation: healthy channels are generated from
load, refrigerant pressures come from a monotone illustrative saturation curve
(:func:`saturation_psig`) applied to the condensing / evaporating temperatures, and each fault
imposes the direction-and-magnitude deltas that fault is known to produce (a fouled condenser widens
its approach and lifts head pressure; an undercharge starves the evaporator, drops subcooling and
degrades both heat exchangers; and so on). That is enough to exercise every detector, the confound
handling, the leg isolation, and — the headline — the roll-up's ``locus`` assignment.

Everything here is deterministic given a seed and depends only on numpy / pandas (core deps).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .chillerdiag import ChillerDriftDiagnosis, diagnose_chiller_drift
from .driftvalidation import LabeledCase
from .model.roles import Role

__all__ = [
    "saturation_psig",
    "ChillerFault",
    "FAULTS",
    "SimulatedCase",
    "simulate_case",
    "make_cases",
    "build_chiller_suite",
    "diagnose_frames",
    "LocusConfusion",
    "locus_confusion",
]

# Healthy design points (degF unless noted). Approaches grow gently with load; subcooling/superheat
# are set by charge/feed and are load-flat; CHW/CW supply and wet-bulb are the loop/ambient context.
_CHWS_F = 44.0
_CHW_DT_F = 12.0  # CHW return - supply at design (so tons = gpm * dT / 24 => gpm = 2 * tons)
_CWS_F = 85.0
_WETBULB_F = 70.0
_COND_APPROACH0 = 3.0
_COND_APPROACH_PER_TON = 0.010
_EVAP_APPROACH0 = 2.0
_EVAP_APPROACH_PER_TON = 0.008
_SUBCOOLING0 = 5.0
_SUPERHEAT0 = 8.0
_CW_RANGE0 = 10.0
_CW_RANGE_PER_TON = 0.010

# Per-channel run-to-run noise (1-sigma).
_APPROACH_NOISE = 0.3
_TEMP_NOISE = 0.3
_REFRIG_NOISE = 0.4
_PRESS_NOISE = 0.6


def saturation_psig(temp_f):
    """An illustrative, monotone refrigerant saturation pressure (psig) for a temperature (degF).

    A smooth low-pressure-refrigerant-like curve — *not* a specific refrigerant and not a
    thermodynamic property, only a monotone map so that a shift in condensing / evaporating
    temperature moves the modelled discharge / suction pressure the right way and by a realistic
    amount. Accepts a scalar or a numpy array.
    """
    t = np.asarray(temp_f, dtype=float)
    return 0.011 * t * t + 0.30 * t + 6.0


@dataclass(frozen=True)
class ChillerFault:
    """A fault's signature: per-severity-unit deltas on the channels it is known to move.

    ``expected_locus`` is the roll-up verdict this fault *should* produce (the ground truth the
    :func:`locus_confusion` matrix scores against). Deltas are per unit of severity; a case at
    severity ``s`` applies ``delta * s``.
    """

    name: str
    expected_locus: str  # steady | condenser | evaporator | charge | whole-machine
    d_cond_approach: float = 0.0
    d_evap_approach: float = 0.0
    d_subcooling: float = 0.0
    d_superheat: float = 0.0
    d_cw_range: float = 0.0  # widen CW return - supply (reduced condenser-water flow)
    d_cw_supply: float = 0.0  # lift entering CW temp (tower can't reject)
    d_discharge_bonus: float = (
        0.0  # direct high-side pressure add (non-condensables' partial press.)
    )


# The standard centrifugal-chiller fault families and the signature each imposes. Directions follow
# the textbook physics: fouling widens the affected approach; reduced CW flow widens the CW range;
# a degrading tower lifts entering CW temperature; undercharge starves the evaporator (superheat up,
# subcooling down) and degrades both exchangers; overcharge floods (subcooling up, superheat down)
# and lifts the high side; non-condensables lift head pressure and subcooling on the condenser side.
FAULTS: dict[str, ChillerFault] = {
    "condenser_fouling": ChillerFault("condenser_fouling", "condenser", d_cond_approach=0.9),
    "reduced_cw_flow": ChillerFault(
        "reduced_cw_flow", "condenser", d_cw_range=1.5, d_cond_approach=0.4
    ),
    "tower_degradation": ChillerFault("tower_degradation", "condenser", d_cw_supply=1.2),
    "evaporator_fouling": ChillerFault("evaporator_fouling", "evaporator", d_evap_approach=0.8),
    "refrigerant_undercharge": ChillerFault(
        "refrigerant_undercharge",
        "whole-machine",
        d_cond_approach=0.45,
        d_evap_approach=0.5,
        d_superheat=1.6,
        d_subcooling=-1.3,
    ),
    "refrigerant_overcharge": ChillerFault(
        "refrigerant_overcharge",
        "whole-machine",
        d_cond_approach=0.55,
        d_superheat=-1.3,
        d_subcooling=1.6,
    ),
    "non_condensables": ChillerFault(
        "non_condensables",
        "condenser",
        d_cond_approach=0.4,
        d_discharge_bonus=6.0,
        d_subcooling=1.2,
    ),
    "excess_oil": ChillerFault(
        "excess_oil", "whole-machine", d_cond_approach=0.55, d_evap_approach=0.5
    ),
}


def _tons(n: int, rng) -> np.ndarray:
    h = np.arange(n)
    t = 170 + 120 * np.sin((h % 24 - 8) / 24 * 2 * np.pi) + rng.normal(0, 12, n)
    return np.clip(t, 40.0, 320.0)


def _frame(n: int, *, start: str, seed: int, fault: ChillerFault | None, severity: int):
    """One role-frame: healthy channels from load, with ``fault`` deltas (x severity) imposed."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="1h")
    tons = _tons(n, rng)

    s = float(severity)
    f = fault
    cond_app = (
        _COND_APPROACH0
        + _COND_APPROACH_PER_TON * tons
        + (f.d_cond_approach * s if f else 0.0)
        + rng.normal(0, _APPROACH_NOISE, n)
    )
    evap_app = (
        _EVAP_APPROACH0
        + _EVAP_APPROACH_PER_TON * tons
        + (f.d_evap_approach * s if f else 0.0)
        + rng.normal(0, _APPROACH_NOISE, n)
    )
    subcool = _SUBCOOLING0 + (f.d_subcooling * s if f else 0.0) + rng.normal(0, _REFRIG_NOISE, n)
    superheat = _SUPERHEAT0 + (f.d_superheat * s if f else 0.0) + rng.normal(0, _REFRIG_NOISE, n)
    cw_supply = _CWS_F + (f.d_cw_supply * s if f else 0.0) + rng.normal(0, _TEMP_NOISE, n)
    cw_range = (
        _CW_RANGE0
        + _CW_RANGE_PER_TON * tons
        + (f.d_cw_range * s if f else 0.0)
        + rng.normal(0, _TEMP_NOISE, n)
    )
    discharge = (
        saturation_psig(cw_supply + cond_app)
        + (f.d_discharge_bonus * s if f else 0.0)
        + rng.normal(0, _PRESS_NOISE, n)
    )
    suction = saturation_psig(_CHWS_F - evap_app) + rng.normal(0, _PRESS_NOISE, n)

    return pd.DataFrame(
        {
            Role.CHW_FLOW: tons * 2.0,
            Role.CHW_SUPPLY_TEMP: np.full(n, _CHWS_F) + rng.normal(0, _TEMP_NOISE, n),
            Role.CHW_RETURN_TEMP: np.full(n, _CHWS_F + _CHW_DT_F) + rng.normal(0, _TEMP_NOISE, n),
            Role.COND_APPROACH_TEMP: cond_app,
            Role.EVAP_APPROACH_TEMP: evap_app,
            Role.SUBCOOLING_TEMP: subcool,
            Role.SUPERHEAT_TEMP: superheat,
            Role.DISCHARGE_PRESSURE: discharge,
            Role.SUCTION_PRESSURE: suction,
            Role.CW_SUPPLY_TEMP: cw_supply,
            Role.CW_RETURN_TEMP: cw_supply + cw_range,
            Role.WETBULB_TEMP: np.full(n, _WETBULB_F) + rng.normal(0, _TEMP_NOISE, n),
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

    def to_labeled(self, *, relevant: frozenset | set | None = None) -> LabeledCase:
        """A :class:`camber.driftvalidation.LabeledCase` for per-detector scoring.

        ``relevant`` restricts what counts as a positive for *this* detector: when given, the case
        is labelled faulty only if its ``fault_name`` is in the set (so a head-pressure detector is
        scored only against faults that should lift head pressure). Default: any injected fault.
        """
        fault = self.is_fault if relevant is None else (self.fault_name in relevant)
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
    equip: str = "CH_SIM",
    n: int = 24 * 30,
    seed: int = 0,
    baseline_start: str = "2025-05-01",
    current_start: str = "2025-06-01",
) -> SimulatedCase:
    """One case: a healthy baseline and a current period (healthy, or ``fault_name`` at severity).

    ``fault_name=None`` (or severity 0) yields a healthy current period — a negative case for
    measuring the false-alarm rate. A known ``fault_name`` imposes that fault's signature.
    """
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
    """A labelled case set: ``n_healthy`` negatives plus ``n_per_fault`` cases per fault x severity.

    ``faults`` defaults to every family in :data:`FAULTS`. Seeds are derived deterministically from
    ``seed0`` so the whole set is reproducible.
    """
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


def build_chiller_suite(store, *, site: str = "SIM", run_id: str = "SIM") -> list:
    """The seven chiller drift detectors that feed the roll-up, sharing one baseline ``store``."""
    from .rules.chiller_cw_range_rule import ChillerCwRangeDrift
    from .rules.chiller_drift_rule import ChillerApproachDrift
    from .rules.chiller_head_pressure_rule import ChillerHeadPressureDrift
    from .rules.chiller_subcooling_rule import ChillerSubcoolingDrift
    from .rules.chiller_suction_pressure_rule import ChillerSuctionPressureDrift
    from .rules.chiller_superheat_rule import ChillerSuperheatDrift
    from .rules.coolingtower_drift_rule import CoolingTowerApproachDrift

    classes = (
        ChillerApproachDrift,
        ChillerCwRangeDrift,
        CoolingTowerApproachDrift,
        ChillerHeadPressureDrift,
        ChillerSubcoolingDrift,
        ChillerSuperheatDrift,
        ChillerSuctionPressureDrift,
    )
    return [cls(store, site=site, run_id=run_id) for cls in classes]


def diagnose_frames(
    baseline,
    current,
    *,
    equip: str = "CH_SIM",
    site: str = "SIM",
    run_id: str = "SIM",
) -> ChillerDriftDiagnosis:
    """Run the whole drift suite on one ``(baseline, current)`` pair and roll it up.

    Builds a fresh :class:`camber.store.modelstore.BaselineStore`, freezes each detector's baseline
    from ``baseline``, scores ``current``, and passes the Findings to
    :func:`camber.chillerdiag.diagnose_chiller_drift`. A convenience for both validation and for
    actually running the chiller drift family on one machine.
    """
    from .store.modelstore import BaselineStore

    store = BaselineStore()
    suite = build_chiller_suite(store, site=site, run_id=run_id)
    findings = [rule.analyze_periods(equip, baseline, current) for rule in suite]
    return diagnose_chiller_drift(findings, equip=equip)


@dataclass
class LocusConfusion:
    """How well the roll-up's ``locus`` matches ground truth over a set of cases."""

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
    cases,
    *,
    min_severity: int = 0,
    site: str = "SIM",
    run_id: str = "SIM",
) -> LocusConfusion:
    """Score the roll-up ``locus`` against each case's ``expected_locus``.

    ``min_severity`` keeps only fault cases at or above that severity (healthy cases, severity 0,
    are always kept) — use it to read localization accuracy on the clearer faults apart from the
    marginal ones. Returns per-expected-locus prediction counts and the overall accuracy.
    """
    matrix: dict = {}
    n = 0
    correct = 0
    for case in cases:
        if case.is_fault and case.severity < min_severity:
            continue
        diag = diagnose_frames(
            case.baseline, case.current, equip=case.equip, site=site, run_id=run_id
        )
        row = matrix.setdefault(case.expected_locus, Counter())
        row[diag.locus] += 1
        n += 1
        if diag.locus == case.expected_locus:
            correct += 1
    accuracy = round(correct / n, 4) if n else float("nan")
    return LocusConfusion(n=n, accuracy=accuracy, matrix=matrix)
