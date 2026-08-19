"""Physics-grounded synthetic pump/hydronic generator -- characterize the pump drift stack.

The mirror of :mod:`camber.driftsim` for the pump / hydronic family. The pump detectors and the
:mod:`camber.pumpdrift` / :mod:`camber.pumpplantdiag` diagnoses ship *screening-grade* thresholds
whose false-alarm rate and localization accuracy have never been measured, and real labelled pump
fault data (like the chiller side's) is scarce and licence-encumbered -- scratch-only, never
committed. This module fills the gap with a **physically consistent synthetic generator**: it
produces ``(baseline, current)`` role-frame pairs for a healthy pump loop and for the standard
pump/hydronic fault families, imposing each fault's known signature on the right channels at a
graded severity, so the whole stack can be characterized end-to-end without shipping anyone's data.

It is a *signature* model built on the affinity laws (Q ∝ N, H ∝ N², P ∝ Q) and the system curve: a
healthy pump's flow, head and power follow its speed; each fault imposes the direction-and-magnitude
deltas that fault is known to produce (impeller wear cuts flow and head; a clogged strainer cuts
flow while the pump's head stays healthy and loop DP rises; overpumping collapses ΔT; a DP-reset
that moves the setpoint must *not* alarm). That is enough to exercise every detector, the
flow-vs-head disambiguation, and -- the headline -- the roll-up's ``locus`` assignment.

Deterministic given a seed; numpy / pandas only (core deps).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .model.roles import Role
from .pumpdrift import PumpDriftDiagnosis, diagnose_pump_drift

__all__ = [
    "PumpFault",
    "FAULTS",
    "SimulatedCase",
    "simulate_case",
    "make_cases",
    "build_pump_suite",
    "diagnose_pump_frames",
    "LocusConfusion",
    "locus_confusion",
]

# Healthy design coefficients (chilled-water loop).
_Q_COEF = 10.0  # gpm per % speed  -> ~1000 gpm near full
_H_COEF = 0.005  # psi per %^2      -> ~50 psi near full
_DT0 = 12.0  # design loop ΔT, degF
_DP0 = 12.0  # design loop DP (held at setpoint)
_P_COEF = 0.02  # kW per gpm
_CHWS = 44.0  # chilled-water supply temp

# Per-channel run-to-run noise (1-sigma).
_FLOW_NOISE = 12.0
_HEAD_NOISE = 1.5
_DT_NOISE = 0.5
_DP_NOISE = 0.5
_POWER_NOISE = 0.4
_TEMP_NOISE = 0.3


@dataclass(frozen=True)
class PumpFault:
    """A pump/hydronic fault's signature: per-severity deltas on the channels it moves.

    ``expected_locus`` is the per-loop verdict this fault should produce (the ground truth
    :func:`locus_confusion` scores against). Fractional deltas scale the healthy channel; additive
    DP deltas are in the DP's units; all are per unit of severity.
    """

    name: str
    expected_locus: str  # steady | pump | distribution | loop-wide
    d_flow_frac: float = 0.0  # fractional flow change (negative = deficit)
    d_head_frac: float = 0.0  # fractional head change (negative = deficit)
    d_power_frac: float = 0.0  # fractional power change (positive = efficiency loss)
    d_deltat_frac: float = 0.0  # fractional ΔT change (negative = collapse)
    d_dp: float = 0.0  # additive loop-DP change above setpoint (added resistance)
    d_dp_sp: float = 0.0  # additive DP-setpoint change (DP tracks it -- a reset, not a fault)
    extra_flow_noise: float = 0.0  # added flow noise (cavitation / entrained air)


# The standard pump/hydronic fault families. Directions follow the physics: impeller wear /
# cavitation cut flow AND head (the pump); a clogged strainer cuts flow while the pump's head stays
# healthy and loop DP rises (the distribution -- the disambiguation test); overpumping collapses ΔT;
# valve-authority loss collapses ΔT and lifts DP; bearing drag raises power/flow; a reset moves SP
# with DP tracking it (must NOT alarm).
FAULTS: dict[str, PumpFault] = {
    "impeller_wear": PumpFault("impeller_wear", "pump", d_flow_frac=-0.035, d_head_frac=-0.10),
    "cavitation": PumpFault(
        "cavitation", "pump", d_flow_frac=-0.035, d_head_frac=-0.10, extra_flow_noise=6.0
    ),
    "bearing_drag": PumpFault("bearing_drag", "pump", d_power_frac=0.06),
    "entrained_air": PumpFault(
        "entrained_air", "pump", d_flow_frac=-0.03, d_head_frac=-0.10, extra_flow_noise=5.0
    ),
    "clogged_strainer": PumpFault("clogged_strainer", "distribution", d_flow_frac=-0.03, d_dp=1.2),
    "overpumping": PumpFault("overpumping", "distribution", d_deltat_frac=-0.06, d_flow_frac=0.02),
    "valve_authority_loss": PumpFault(
        "valve_authority_loss", "distribution", d_deltat_frac=-0.05, d_dp=1.0
    ),
    "dp_reset": PumpFault("dp_reset", "steady", d_dp_sp=1.5),  # negative case: must not alarm
}


def _speed(n: int, rng) -> np.ndarray:
    h = np.arange(n)
    s = 68 + 30 * np.sin((h % 24 - 8) / 24 * 2 * np.pi) + rng.normal(0, 4, n)
    return np.clip(s, 35.0, 100.0)


def _frame(n: int, *, start: str, seed: int, fault: PumpFault | None, severity: int):
    """One role-frame: healthy channels from speed, with ``fault`` deltas (x severity) imposed."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="1h")
    spd = _speed(n, rng)
    s = float(severity)
    f = fault

    flow = _Q_COEF * spd * (1 + (f.d_flow_frac * s if f else 0.0)) + rng.normal(
        0, _FLOW_NOISE + (f.extra_flow_noise * s if f else 0.0), n
    )
    head = _H_COEF * spd * spd * (1 + (f.d_head_frac * s if f else 0.0)) + rng.normal(
        0, _HEAD_NOISE, n
    )
    dt = _DT0 * (1 + (f.d_deltat_frac * s if f else 0.0)) + rng.normal(0, _DT_NOISE, n)
    dp_sp = _DP0 + (f.d_dp_sp * s if f else 0.0)
    dp = dp_sp + (f.d_dp * s if f else 0.0) + rng.normal(0, _DP_NOISE, n)
    power = _P_COEF * flow * (1 + (f.d_power_frac * s if f else 0.0)) + rng.normal(
        0, _POWER_NOISE, n
    )
    supply = np.full(n, _CHWS) + rng.normal(0, _TEMP_NOISE, n)

    return pd.DataFrame(
        {
            Role.CHW_PUMP_SPEED: spd,
            Role.CHW_FLOW: flow,
            Role.PUMP_HEAD: head,
            Role.CHW_SUPPLY_TEMP: supply,
            Role.CHW_RETURN_TEMP: supply + dt,
            Role.CHW_DIFF_PRESS: dp,
            Role.CHW_DIFF_PRESS_SP: np.full(n, dp_sp),
            Role.POWER: power,
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
        default: any injected fault other than the ``dp_reset`` negative.
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
    equip: str = "P_SIM",
    n: int = 24 * 30,
    seed: int = 0,
    baseline_start: str = "2025-05-01",
    current_start: str = "2025-06-01",
) -> SimulatedCase:
    """One case: healthy baseline, current period (healthy or ``fault_name`` at that sev)."""
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


def build_pump_suite(store, *, site: str = "SIM", run_id: str = "SIM") -> list:
    """The five pump/hydronic drift detectors feeding the loop diagnosis, sharing one ``store``."""
    from .rules.loop_deltat_rule import LoopDeltaTDrift
    from .rules.loop_dp_rule import LoopDPDrift
    from .rules.pump_flow_rule import PumpFlowDrift
    from .rules.pump_head_rule import PumpHeadDrift
    from .rules.pump_power_rule import PumpPowerDrift

    classes = (PumpFlowDrift, PumpHeadDrift, LoopDeltaTDrift, LoopDPDrift, PumpPowerDrift)
    return [cls(store, site=site, run_id=run_id) for cls in classes]


def diagnose_pump_frames(
    baseline, current, *, equip: str = "P_SIM", site: str = "SIM", run_id: str = "SIM"
) -> PumpDriftDiagnosis:
    """Run the whole pump drift suite on one ``(baseline, current)`` pair and diagnose the loop."""
    from .store.modelstore import BaselineStore

    store = BaselineStore()
    suite = build_pump_suite(store, site=site, run_id=run_id)
    findings = [rule.analyze_periods(equip, baseline, current) for rule in suite]
    return diagnose_pump_drift(findings, equip=equip)


@dataclass
class LocusConfusion:
    """How well the loop diagnosis' ``locus`` matches ground truth over a set of cases."""

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
    """Score the loop diagnosis' ``locus`` against each case's ``expected_locus``.

    ``min_severity`` keeps only fault cases at or above that severity (healthy cases are always
    kept) -- to read localization accuracy on the clearer faults apart from the marginal ones.
    """
    matrix: dict = {}
    n = 0
    correct = 0
    for case in cases:
        if case.is_fault and case.severity < min_severity:
            continue
        diag = diagnose_pump_frames(
            case.baseline, case.current, equip=case.equip, site=site, run_id=run_id
        )
        row = matrix.setdefault(case.expected_locus, Counter())
        row[diag.locus] += 1
        n += 1
        if diag.locus == case.expected_locus:
            correct += 1
    accuracy = round(correct / n, 4) if n else float("nan")
    return LocusConfusion(n=n, accuracy=accuracy, matrix=matrix)
