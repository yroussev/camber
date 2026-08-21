"""Physics-grounded synthetic condenser-loop generator -- characterize the heat-rejection stack.

The condenser-water / cooling-tower mirror of :mod:`camber.ahusim` / :mod:`camber.pumpsim`. The four
condenser-side drift detectors + :mod:`camber.condenserdrift` diagnosis ship *screening-grade*
thresholds whose false-alarm rate and cause-detection accuracy have never been measured, and real
labelled heat-rejection-fault data is scarce -- scratch-only, never committed. This module fills the
gap with a **physically consistent synthetic generator** that produces ``(baseline, current)`` frame
pairs for a healthy loop and for the standard heat-rejection fault families at a graded severity, so
the whole stack can be characterized end-to-end without shipping anyone's data.

**Unlike ahusim, the condenser diagnosis has no** ``locus`` **-- it is a cause-detection +
corroboration diagnosis** (:class:`camber.condenserdrift.CondenserDriftDiagnosis` names each
signal's localized cause and flags corroboration when two or more agree). So this validator scores a
:class:`CauseConfusion` (did the diagnosis name the expected cause, with the right corroboration
flag?), not a locus confusion.

The heat-rejection channels are **coupled through two shared physical quantities**: the entering
condenser-water temperature ``CWS = wet-bulb + tower approach`` and the condensing temperature
``TCOND = CWS + condenser approach``. Head/discharge pressure is driven by ``TCOND`` only, so:

* **tube scaling** widens the condenser approach **and** (via ``TCOND``) raises head pressure -- the
  co-move the diagnosis corroborates as system-side scaling;
* a **fouling tower** widens the tower approach, raising ``CWS`` and so lifting head pressure -- the
  head-pressure rule's CW-supply confound sees this and the *degrading tower corroborates it*;
* an **ambient wet-bulb rise** raises ``CWS`` (and head pressure) with a **quiet tower** -- the same
  confound must then demote the head-pressure rise to likely-ambient, not a fault (the negative
  case, the condenser analog of ahusim's ``static_reset``).

The co-movement is emergent through the shared quantities, not a hand-tuned pair. Every detector
derives load as chiller tons from the chilled-water side (``tons = gpm x dT / 24``), so the frame
carries a swept CHW load. Deterministic given a seed; numpy / pandas only (core deps).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .condenserdrift import CondenserDriftDiagnosis, diagnose_condenser_drift
from .model.roles import Role

__all__ = [
    "CondenserFault",
    "FAULTS",
    "SimulatedCase",
    "simulate_case",
    "make_cases",
    "build_condenser_suite",
    "diagnose_condenser_frames",
    "CauseConfusion",
    "cause_confusion",
]

# Healthy design points. Load is a swept chiller part-load; every detector derives tons from the
# chilled-water side (tons = gpm x dT / 24), so the CHW dT sweep gives all four fits their span.
_CHW_GPM = 600.0  # chilled-water flow, gpm (constant) -> tons = 25 x dT
_CHWS = 44.0  # chilled-water supply temp (constant)
_CHW_DT_DESIGN = 10.0  # design chilled-water dT at full load
_PL_MID = 0.75  # mean part-load fraction
_PL_SWING = 0.22
_PL_NOISE = 0.02

# The heat-rejection chain. CWS = wet-bulb + tower approach; TCOND = CWS + condenser approach; head
# pressure tracks TCOND. Wet-bulb rises with load (weather drives both), so CWS/TCOND are load-
# explained and the head-pressure baseline fit is clean.
_WB_MID = 60.0  # wet-bulb at mean load, degF
_WB_LOAD = 15.0  # wet-bulb rise per unit part-load above the mean
_WB_NOISE = 0.25
_TOWER_APPROACH0 = 3.0  # tower approach (CWS - wet-bulb) intercept, degF
_TA_LOAD = 4.0  # tower approach rise with part-load
_TA_NOISE = 0.25
_CW_GPM = 900.0  # condenser-water flow, gpm (healthy constant; reduced/raised by a flow fault)
_RANGE_DESIGN = 10.0  # condenser-water range (CWR - CWS) at full load, degF
_RANGE_NOISE = 0.3
_COND_APPROACH0 = 2.0  # chiller condenser approach (refrigerant -> water) intercept, degF
_CA_LOAD = 3.0  # condenser approach rise with part-load
_CA_NOISE = 0.25
_P0 = 180.0  # discharge/condensing pressure at design TCOND, psig
_P_SLOPE = 3.0  # discharge pressure rise per degF of condensing temperature
_P_NOISE = 0.8
# Design condensing temperature (mean load, no fault/noise) -- the pressure curve's reference.
_TCOND0 = _WB_MID + (_TOWER_APPROACH0 + _TA_LOAD * _PL_MID) + (_COND_APPROACH0 + _CA_LOAD * _PL_MID)


@dataclass(frozen=True)
class CondenserFault:
    """A heat-rejection fault's signature: per-severity deltas on the channels it moves.

    ``expected_cause`` is the localized cause string the diagnosis should name (``""`` for the
    ambient confound, which must NOT be flagged as a heat-rejection fault);
    ``expected_corroborated`` is the ground-truth corroboration flag. The levers are additive per
    unit of severity: scaling widens the condenser approach (and, via ``TCOND``, head pressure); a
    tower delta widens the tower approach (and, via ``CWS``, head pressure); a CW-flow fraction
    changes the condenser-water range (+ raises flow / narrows range, - drops flow / widens range);
    non-condensables lift head pressure alone; a wet-bulb delta raises ``CWS`` (ambient), lifting
    head pressure with a quiet tower.
    """

    name: str
    expected_cause: str
    expected_corroborated: bool = False
    is_confound: bool = False
    d_cond_scaling: float = 0.0  # condenser approach up -> approach AND head pressure (coupled)
    d_tower_approach: float = 0.0  # tower approach up -> tower AND head pressure (via CWS)
    d_cw_flow_frac: float = 0.0  # condenser-water flow change -> range (- widens / + narrows)
    d_noncond: float = 0.0  # non-condensables -> head pressure only
    d_wetbulb: float = 0.0  # ambient wet-bulb rise -> CWS/head pressure, tower quiet (the confound)


_CAUSE_SCALING = "condenser tube fouling or scale"
_CAUSE_TOWER = "cooling-tower heat rejection degrading"
_CAUSE_FLOW_LOW = "reduced condenser-water flow"
_CAUSE_BYPASS = "condenser-water bypass or short-circuit"
_CAUSE_HEAD = "condenser high-side pressure rising (fouling / non-condensables)"

# The standard heat-rejection fault families. tube_scaling and tower_fouling co-move a 2nd channel
# (head pressure) through the shared quantities, so the diagnosis corroborates them; the CW-flow and
# non-condensable faults are single-signal; ambient_cw_rise is the confound negative (a CW/head rise
# with a quiet tower, which must be demoted to likely-ambient rather than flagged).
FAULTS: dict[str, CondenserFault] = {
    "tube_scaling": CondenserFault(
        "tube_scaling", _CAUSE_SCALING, expected_corroborated=True, d_cond_scaling=0.7
    ),
    "tower_fouling": CondenserFault(
        "tower_fouling", _CAUSE_TOWER, expected_corroborated=True, d_tower_approach=1.0
    ),
    "cw_flow_reduction": CondenserFault("cw_flow_reduction", _CAUSE_FLOW_LOW, d_cw_flow_frac=-0.07),
    "cw_bypass": CondenserFault("cw_bypass", _CAUSE_BYPASS, d_cw_flow_frac=0.11),
    "noncondensables": CondenserFault("noncondensables", _CAUSE_HEAD, d_noncond=2.5),
    "ambient_cw_rise": CondenserFault(
        "ambient_cw_rise", "", is_confound=True, d_wetbulb=1.0
    ),  # negative: a CW/head rise with a quiet tower -- must not be a heat-rejection fault
}


def _frame(n: int, *, start: str, seed: int, fault: CondenserFault | None, severity: int):
    """One role-frame: heat-rejection channels on the coupled model, with ``fault`` deltas."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="1h")
    h = np.arange(n)
    s = float(severity)
    f = fault

    pl = _PL_MID + _PL_SWING * np.sin((h % 24 - 8) / 24 * 2 * np.pi) + rng.normal(0, _PL_NOISE, n)
    pl = np.clip(pl, 0.4, 1.0)
    chw_dt = _CHW_DT_DESIGN * pl
    chws = np.full(n, _CHWS)
    chwr = chws + chw_dt

    d_wb = f.d_wetbulb * s if f else 0.0
    d_ta = f.d_tower_approach * s if f else 0.0
    d_flow = f.d_cw_flow_frac * s if f else 0.0
    d_scale = f.d_cond_scaling * s if f else 0.0
    d_noncond = f.d_noncond * s if f else 0.0

    wb = _WB_MID + _WB_LOAD * (pl - _PL_MID) + d_wb + rng.normal(0, _WB_NOISE, n)
    ta = _TOWER_APPROACH0 + _TA_LOAD * pl + d_ta + rng.normal(0, _TA_NOISE, n)
    cws = wb + ta
    cw_gpm = _CW_GPM * (1.0 + d_flow)
    cw_range = _RANGE_DESIGN * pl * (_CW_GPM / cw_gpm) + rng.normal(0, _RANGE_NOISE, n)
    cwr = cws + cw_range
    cond_approach = _COND_APPROACH0 + _CA_LOAD * pl + d_scale + rng.normal(0, _CA_NOISE, n)
    tcond = cws + cond_approach
    discharge = _P0 + _P_SLOPE * (tcond - _TCOND0) + d_noncond + rng.normal(0, _P_NOISE, n)

    return pd.DataFrame(
        {
            Role.CHW_FLOW: np.full(n, _CHW_GPM),
            Role.CHW_SUPPLY_TEMP: chws,
            Role.CHW_RETURN_TEMP: chwr,
            Role.CW_SUPPLY_TEMP: cws,
            Role.CW_RETURN_TEMP: cwr,
            Role.WETBULB_TEMP: wb,
            Role.COND_APPROACH_TEMP: cond_approach,
            Role.DISCHARGE_PRESSURE: discharge,
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
        default: any injected fault other than the ``ambient_cw_rise`` confound negative.
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
    equip: str = "COND_SIM",
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


def build_condenser_suite(store, *, site: str = "SIM", run_id: str = "SIM") -> list:
    """The four condenser-side drift detectors that feed the diagnosis, sharing one ``store``."""
    from .rules.chiller_cw_range_rule import ChillerCwRangeDrift
    from .rules.chiller_drift_rule import ChillerApproachDrift
    from .rules.chiller_head_pressure_rule import ChillerHeadPressureDrift
    from .rules.coolingtower_drift_rule import CoolingTowerApproachDrift

    return [
        ChillerApproachDrift(store, site=site, run_id=run_id),
        ChillerCwRangeDrift(store, site=site, run_id=run_id),
        CoolingTowerApproachDrift(store, site=site, run_id=run_id),
        ChillerHeadPressureDrift(store, site=site, run_id=run_id),
    ]


def diagnose_condenser_frames(
    baseline,
    current,
    *,
    equip: str = "COND_SIM",
    site: str = "SIM",
    run_id: str = "SIM",
) -> CondenserDriftDiagnosis:
    """Run the condenser drift suite on one ``(baseline, current)`` pair and diagnose the loop."""
    from .store.modelstore import BaselineStore

    store = BaselineStore()
    suite = build_condenser_suite(store, site=site, run_id=run_id)
    findings = [rule.analyze_periods(equip, baseline, current) for rule in suite]
    return diagnose_condenser_drift(findings, equip=equip)


@dataclass
class CauseConfusion:
    """How well the condenser diagnosis' cause + corroboration match ground truth over the cases.

    ``accuracy`` is the fraction of cases whose expected cause was named (or, for the confound,
    whose CW/head rise was correctly not corroborated); ``corroboration_accuracy`` is the fraction
    whose ``corroborated`` flag matched ground truth. ``matrix`` maps each expected cause to a
    counter of the observed *primary* (worst-first) cause.
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
    """Score the condenser diagnosis' cause + corroboration against each case's ground truth.

    ``min_severity`` keeps only fault cases at or above that severity (healthy / confound cases are
    always kept) -- to read cause-detection accuracy on the clearer faults apart from the marginal
    ones. A real fault is correct when its ``expected_cause`` appears in the diagnosis' causes; the
    confound negative is correct when its CW/head rise is NOT corroborated as a fault.
    """
    matrix: dict = {}
    n = 0
    correct = 0
    corr_correct = 0
    for case in cases:
        if case.is_fault and not case.is_confound and case.severity < min_severity:
            continue
        diag = diagnose_condenser_frames(
            case.baseline, case.current, equip=case.equip, site=site, run_id=run_id
        )
        observed = diag.causes[0] if diag.causes else "none"
        matrix.setdefault(case.expected_cause, Counter())[observed] += 1
        n += 1
        if case.expected_cause:
            ok = case.expected_cause in diag.causes
        else:  # steady or the ambient confound: correct iff not flagged as a corroborated fault
            ok = not diag.corroborated
        correct += 1 if ok else 0
        corr_correct += 1 if diag.corroborated == case.expected_corroborated else 0
    accuracy = round(correct / n, 4) if n else float("nan")
    corroboration_accuracy = round(corr_correct / n, 4) if n else float("nan")
    return CauseConfusion(
        n=n, accuracy=accuracy, corroboration_accuracy=corroboration_accuracy, matrix=matrix
    )
