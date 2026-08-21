"""Physics-grounded synthetic evaporator generator -- characterize the chilled-water / low side.

The low-side mirror of :mod:`camber.condensersim`. The three evaporator-side drift detectors +
:mod:`camber.evaporatordrift` diagnosis ship *screening-grade* thresholds whose false-alarm rate and
cause-detection accuracy have never been measured, and real labelled low-side-fault data is scarce
-- scratch-only, never committed. This module fills the gap with a **physically consistent synthetic
generator** that produces ``(baseline, current)`` frame pairs for a healthy loop and for the
standard low-side fault families at a graded severity, so the whole stack can be characterized
without shipping anyone's data.

**Like the condenser diagnosis, the evaporator diagnosis has no** ``locus`` **-- it is a
cause-detection + corroboration diagnosis** (an
:class:`camber.evaporatordrift.EvaporatorDriftDiagnosis`
names each cause and flags corroboration when two or more agree). So this validator scores a
:class:`CauseConfusion` (did the diagnosis name the expected cause, with the right corroboration
flag?), not a locus confusion.

The low side is coupled through **one shared "feed" latent** (the refrigerant feed / charge state):
an **overfeed** lowers superheat **and** raises suction pressure together; a **starvation** raises
superheat **and** lowers suction pressure together. So the two feed reads co-move emergently, and
the diagnosis's superheat-vs-suction cross-check fires for real. **Evaporator fouling** widens the
evaporator approach alone (heat-transfer loss). The negative confound **chw_reset** shifts the
chilled-water supply temperature, which lifts suction pressure through the evaporating-temperature
chain while superheat stays quiet -- so the cross-check correctly does *not* corroborate it (the
low-side twin of condensersim's ``ambient_cw_rise``).

Every detector derives load as chiller tons from the chilled-water side (``tons = gpm x dT / 24``),
so the frame carries a swept CHW load. A steady ``COND_APPROACH_TEMP`` is emitted because the shared
approach detector requires it (it scores both legs); only the evaporator leg is exercised here.
A separable "low charge, suction-only" fault is deliberately omitted -- in a single-feed-latent
model it is indistinguishable from starvation, whose feature is the co-moving superheat rise.
Deterministic given a seed; numpy / pandas only (core deps).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .evaporatordrift import EvaporatorDriftDiagnosis, diagnose_evaporator_drift
from .model.roles import Role

__all__ = [
    "EvaporatorFault",
    "FAULTS",
    "SimulatedCase",
    "simulate_case",
    "make_cases",
    "build_evaporator_suite",
    "diagnose_evaporator_frames",
    "CauseConfusion",
    "cause_confusion",
]

# Healthy design points. Load is a swept chiller part-load; every detector derives tons from the
# chilled-water side (tons = gpm x dT / 24), so the CHW dT sweep gives all three fits their span.
_CHW_GPM = 600.0  # chilled-water flow, gpm (constant) -> tons = 25 x dT
_CHWS = 44.0  # chilled-water supply temp (constant; shifted only by the reset confound)
_CHW_DT_DESIGN = 10.0  # design chilled-water dT at full load
_PL_MID = 0.75  # mean part-load fraction
_PL_SWING = 0.22
_PL_NOISE = 0.02

# Evaporator approach (refrigerant -> water) and a steady condenser approach (required by the shared
# approach detector; kept flat so its leg never alarms).
_EVAP_APPROACH0 = 2.0  # evaporator approach intercept, degF
_EA_LOAD = 3.0  # evaporator approach rise with part-load
_EA_NOISE = 0.25
_COND_APPROACH0 = 2.0  # steady condenser approach (nuisance channel, no fault delta)
_CA_LOAD = 3.0
_CA_NOISE = 0.25

# The low-side feed reads. A single feed latent drives both with opposite signs (overfeed: superheat
# down, suction up; starvation: superheat up, suction down), so they co-move emergently.
_SH0 = 10.0  # superheat at mean load, degF
_SH_LOAD = 1.5  # superheat rise with part-load above the mean
_SH_NOISE = 0.5
_K_SH_FEED = 1.3  # superheat degF per unit feed (negative coupling)
_P0 = 70.0  # suction pressure at design evaporating temp, psig
_P_SLOPE = 1.5  # suction pressure per degF of evaporating temperature
_P_NOISE = 0.8
_K_SP_FEED = 1.7  # suction psi per unit feed (positive coupling)
# Design evaporating temperature (mean load, no fault/noise) -- the suction curve's reference.
_TEVAP0 = _CHWS - (_EVAP_APPROACH0 + _EA_LOAD * _PL_MID)


@dataclass(frozen=True)
class EvaporatorFault:
    """A low-side fault's signature: per-severity deltas on the channels it moves.

    ``expected_cause`` is the localized cause string the diagnosis should name (``""`` for the
    chw-reset confound, which must NOT be corroborated); ``expected_corroborated`` is the
    ground-truth corroboration flag. ``d_evap_fouling`` widens the evaporator approach alone;
    ``d_feed`` is the shared feed latent (+ overfeed -> superheat down / suction up; - starvation ->
    superheat up / suction down); ``d_chw_reset`` shifts chilled-water supply, lifting suction via
    the evaporating temperature with superheat quiet (the confound).
    """

    name: str
    expected_cause: str
    expected_corroborated: bool = False
    is_confound: bool = False
    d_evap_fouling: float = 0.0
    d_feed: float = 0.0
    d_chw_reset: float = 0.0


_CAUSE_FOULING = "evaporator tube fouling or scale"
_CAUSE_OVERFED_SH = "evaporator overfed — liquid floodback risk"
_CAUSE_STARVED_SH = "evaporator starved / underfed (undercharge or restricted metering)"
_CAUSE_OVERFEED_SP = "evaporator overfeed / flooding"
_CAUSE_HTLOSS_SP = "evaporator heat-transfer loss or low charge"

# The standard low-side fault families. overfeed and starvation move superheat AND suction together
# through the shared feed latent, so the diagnosis corroborates them (the feed cross-check); fouling
# is single-signal (approach only); chw_reset is the confound negative (a CHW-supply shift lifts
# suction with superheat quiet, which the cross-check must NOT corroborate as a fault).
FAULTS: dict[str, EvaporatorFault] = {
    "evap_fouling": EvaporatorFault("evap_fouling", _CAUSE_FOULING, d_evap_fouling=0.7),
    "overfeed": EvaporatorFault(
        "overfeed", _CAUSE_OVERFED_SH, expected_corroborated=True, d_feed=1.0
    ),
    "starvation": EvaporatorFault(
        "starvation", _CAUSE_STARVED_SH, expected_corroborated=True, d_feed=-1.0
    ),
    "chw_reset": EvaporatorFault(
        "chw_reset", "", is_confound=True, d_chw_reset=1.0
    ),  # negative: a CHW-reset suction rise with a quiet superheat -- must not corroborate
}


def _frame(n: int, *, start: str, seed: int, fault: EvaporatorFault | None, severity: int):
    """One role-frame: healthy low-side channels on the coupled model, plus ``fault`` deltas."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="1h")
    h = np.arange(n)
    s = float(severity)
    f = fault

    pl = _PL_MID + _PL_SWING * np.sin((h % 24 - 8) / 24 * 2 * np.pi) + rng.normal(0, _PL_NOISE, n)
    pl = np.clip(pl, 0.4, 1.0)

    d_foul = f.d_evap_fouling * s if f else 0.0
    d_feed = f.d_feed * s if f else 0.0
    d_reset = f.d_chw_reset * s if f else 0.0

    chws = _CHWS + d_reset
    chwr = chws + _CHW_DT_DESIGN * pl  # dT preserved -> tons is load-only, reset-invariant
    cond_approach = _COND_APPROACH0 + _CA_LOAD * pl + rng.normal(0, _CA_NOISE, n)
    evap_approach = _EVAP_APPROACH0 + _EA_LOAD * pl + d_foul + rng.normal(0, _EA_NOISE, n)
    superheat = _SH0 + _SH_LOAD * (pl - _PL_MID) - _K_SH_FEED * d_feed + rng.normal(0, _SH_NOISE, n)
    t_evap_clean = chws - (
        _EVAP_APPROACH0 + _EA_LOAD * pl
    )  # load + reset, but not the fouling delta
    suction = (
        _P0 + _P_SLOPE * (t_evap_clean - _TEVAP0) + _K_SP_FEED * d_feed + rng.normal(0, _P_NOISE, n)
    )

    return pd.DataFrame(
        {
            Role.CHW_FLOW: np.full(n, _CHW_GPM),
            Role.CHW_SUPPLY_TEMP: np.full(n, chws),
            Role.CHW_RETURN_TEMP: chwr,
            Role.COND_APPROACH_TEMP: cond_approach,
            Role.EVAP_APPROACH_TEMP: evap_approach,
            Role.SUPERHEAT_TEMP: superheat,
            Role.SUCTION_PRESSURE: suction,
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
    expected_cause: str
    expected_corroborated: bool
    is_fault: bool
    is_confound: bool

    def to_labeled(self, *, relevant=None):
        """A :class:`camber.driftvalidation.LabeledCase` for per-detector scoring.

        ``relevant`` restricts what counts as a positive for *this* detector (a set of fault names);
        default: any injected fault other than the ``chw_reset`` confound negative.
        """
        from .driftvalidation import LabeledCase

        if relevant is None:
            fault = self.is_fault and not self.is_confound
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
    equip: str = "EVAP_SIM",
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
        expected_cause="" if fault is None else fault.expected_cause,
        expected_corroborated=False if fault is None else fault.expected_corroborated,
        is_fault=fault is not None,
        is_confound=bool(fault.is_confound) if fault is not None else False,
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


def build_evaporator_suite(store, *, site: str = "SIM", run_id: str = "SIM") -> list:
    """The three evaporator-side drift detectors that feed the diagnosis, sharing one ``store``."""
    from .rules.chiller_drift_rule import ChillerApproachDrift
    from .rules.chiller_suction_pressure_rule import ChillerSuctionPressureDrift
    from .rules.chiller_superheat_rule import ChillerSuperheatDrift

    return [
        ChillerApproachDrift(store, site=site, run_id=run_id),
        ChillerSuperheatDrift(store, site=site, run_id=run_id),
        ChillerSuctionPressureDrift(store, site=site, run_id=run_id),
    ]


def diagnose_evaporator_frames(
    baseline,
    current,
    *,
    equip: str = "EVAP_SIM",
    site: str = "SIM",
    run_id: str = "SIM",
) -> EvaporatorDriftDiagnosis:
    """Run the evaporator drift suite on one ``(baseline, current)`` pair and diagnose the loop."""
    from .store.modelstore import BaselineStore

    store = BaselineStore()
    suite = build_evaporator_suite(store, site=site, run_id=run_id)
    findings = [rule.analyze_periods(equip, baseline, current) for rule in suite]
    return diagnose_evaporator_drift(findings, equip=equip)


@dataclass
class CauseConfusion:
    """How well the evaporator diagnosis' cause + corroboration match ground truth over the cases.

    ``accuracy`` is the fraction of cases whose expected cause was named (or, for the confound,
    whose CHW-reset suction rise was correctly not corroborated); ``corroboration_accuracy`` is the
    fraction whose ``corroborated`` flag matched ground truth. ``matrix`` maps each expected cause
    to a counter of the observed *primary* (worst-first) cause.
    """

    n: int
    accuracy: float
    corroboration_accuracy: float
    matrix: dict = field(default_factory=dict)  # expected_cause -> {observed primary cause: count}

    def as_dict(self) -> dict:
        """Flat, JSON-friendly view (matrix rows become plain dicts)."""
        return {
            "n": self.n,
            "accuracy": self.accuracy,
            "corroboration_accuracy": self.corroboration_accuracy,
            "matrix": {k: dict(v) for k, v in self.matrix.items()},
        }


def cause_confusion(
    cases, *, min_severity: int = 0, site: str = "SIM", run_id: str = "SIM"
) -> CauseConfusion:
    """Score the evaporator diagnosis' cause + corroboration against each case's ground truth.

    ``min_severity`` keeps only fault cases at or above that severity (healthy / confound cases are
    always kept). A real fault is correct when its ``expected_cause`` appears in the diagnosis'
    causes; the confound negative is correct when its suction rise is NOT corroborated as a fault.
    """
    matrix: dict = {}
    n = 0
    correct = 0
    corr_correct = 0
    for case in cases:
        if case.is_fault and not case.is_confound and case.severity < min_severity:
            continue
        diag = diagnose_evaporator_frames(
            case.baseline, case.current, equip=case.equip, site=site, run_id=run_id
        )
        observed = diag.causes[0] if diag.causes else "none"
        matrix.setdefault(case.expected_cause, Counter())[observed] += 1
        n += 1
        if case.expected_cause:
            ok = case.expected_cause in diag.causes
        else:  # steady or the chw-reset confound: correct iff not corroborated as a fault
            ok = not diag.corroborated
        correct += 1 if ok else 0
        corr_correct += 1 if diag.corroborated == case.expected_corroborated else 0
    accuracy = round(correct / n, 4) if n else float("nan")
    corroboration_accuracy = round(corr_correct / n, 4) if n else float("nan")
    return CauseConfusion(
        n=n, accuracy=accuracy, corroboration_accuracy=corroboration_accuracy, matrix=matrix
    )
