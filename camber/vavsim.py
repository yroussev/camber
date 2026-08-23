"""Physics-grounded synthetic VAV generator -- characterize the zone-terminal drift stack.

The terminal-box mirror of :mod:`camber.ahusim`. The two VAV detectors + :mod:`camber.vavdrift`
diagnosis ship *screening-grade* thresholds whose false-alarm rate and localization accuracy have
never been measured, and real labelled terminal-box-fault data is scarce -- scratch-only, never
committed. This module fills the gap with a **physically consistent synthetic generator** that
produces ``(baseline, current)`` frame pairs for a healthy box and for the standard terminal-box
fault families at a graded severity, so the whole stack can be characterized end-to-end without
shipping anyone's data.

Because :func:`camber.vavdrift.diagnose_vav_drift` returns a ``locus`` (steady · airflow · reheat ·
upstream · box-wide), this validator scores a :class:`LocusConfusion` (like ahusim), with
``upstream`` a first-class expected locus so the **plant-vs-box split is directly measured**.

**A VAV box runs in two regimes, and one frame carries both.** In occupied-daytime **cooling** the
commanded airflow sweeps and the damper modulates to track it while the reheat valve is closed --
regime :class:`camber.rules.vav_airflow_rule.VavAirflowDrift` scores (``DAMPER ~ f(command)``). In
night/morning **heating** the box drops to minimum airflow and the reheat valve modulates -- the
regime :class:`camber.rules.vav_reheat_valve_rule.VavReheatValveDrift` scores
(``valve ~ f(airflow x ΔT)``). Each detector's own gating carves out its regime: reheat is
double-gated out of cooling (valve = 0 AND reheat ΔT ~ 0), while the damper detector keeps both
regimes on one ``DAMPER = f(command)`` line (heating min flow stays above the min-command gate).

**The upstream disambiguation is the money test.** ``damper_authority_loss`` and
``upstream_starvation`` inject the *same* damper creep; the latter *also* drops the upstream
``DUCT_STATIC`` median, which trips the airflow detector's ``vav_upstream_starvation_suspected``
so the diagnosis attributes the creep to the plant (locus ``upstream``) rather than the box (locus
``airflow``) -- a single-variable contrast.

**One honest asymmetry:** the airflow detector cleanly re-routes an upstream cause to a distinct
locus, but the reheat detector only *caveats* a hot-water-reset creep (its HW confound is a caveat,
not a locus demotion). So a *strong* HW reset would fire the reheat detector as a ``reheat``
with a caveat; the generator therefore carries only a *mild* ``hw_reset`` as a ``steady`` negative,
and the fire-with-caveat behavior is covered by a dedicated test rather than the confusion matrix.

Deterministic given a seed; numpy / pandas only (core deps).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .model.roles import Role
from .vavdrift import VavDriftDiagnosis, diagnose_vav_drift

__all__ = [
    "VavFault",
    "FAULTS",
    "SimulatedCase",
    "simulate_case",
    "make_cases",
    "build_vav_suite",
    "diagnose_vav_frames",
    "LocusConfusion",
    "locus_confusion",
]

# Two-regime box design points (mirror the two rule tests' generators). Cooling: a swept command +
# damper on the line DAMPER = _D0 + _D_PER_CFM*command. Heating: min flow, reheat valve on the line
# HEAT_VALVE = _V0 + _V_PER_DUTY*duty. Heating min flow stays above the airflow 50-cfm gate
# so both regimes sit on one damper line.
_MIN_FLOW = 300.0  # heating minimum airflow (cfm)
_COOL_MID = 800.0  # cooling command midpoint
_COOL_SWING = 350.0
_FLOW_NOISE = 30.0
_D0 = 15.0  # healthy damper intercept, %
_D_PER_CFM = 0.06  # damper % per cfm of commanded airflow
_DAMPER_NOISE = 3.0
_ENTER = 55.0  # entering primary (cold AHU supply) temp = MIXED_AIR_TEMP, degF
_ENTER_NOISE = 0.3
_RISE_MID = 20.0  # reheat air-ΔT midpoint in heating, degF
_RISE_SWING = 8.0
_RISE_NOISE = 1.0
_V0 = 10.0  # healthy reheat-valve intercept, %
_V_PER_DUTY = 0.0035  # valve % per cfm·°F of reheat duty (kept below saturation)
_VALVE_NOISE = 3.0
_HW0 = 180.0  # hot-water supply temp, degF
_HW_NOISE = 0.5
_STATIC0 = 1.2  # upstream duct static, inH2O
_STATIC_NOISE = 0.03


@dataclass(frozen=True)
class VavFault:
    """A terminal-box fault's signature: per-severity deltas on the channels it moves.

    ``expected_locus`` is the per-box verdict this fault should produce (the ground truth
    :func:`locus_confusion` scores against). ``d_damper_creep`` opens the damper at matched command;
    ``d_duct_static`` shifts the upstream static (negative = starvation, which -- with a creep --
    trips the plant attribution); ``d_reheat_valve`` opens the reheat valve at matched duty;
    ``d_hw_supply`` shifts the hot-water supply (negative = a colder reset). All per unit severity.
    """

    name: str
    expected_locus: str
    d_damper_creep: float = 0.0
    d_duct_static: float = 0.0
    d_reheat_valve: float = 0.0
    d_hw_supply: float = 0.0


# The standard terminal-box fault families and their expected loci. damper_authority_loss and
# upstream_starvation inject the same damper creep -- the latter also drops upstream static, so the
# diagnosis attributes it to the plant (upstream) not the box (airflow). box_wide is both box sides.
# hw_reset is a mild steady negative (sub-floor valve creep; a strong one fires reheat-with-caveat).
FAULTS: dict[str, VavFault] = {
    "damper_authority_loss": VavFault("damper_authority_loss", "airflow", d_damper_creep=5.0),
    "upstream_starvation": VavFault(
        "upstream_starvation", "upstream", d_damper_creep=5.0, d_duct_static=-0.12
    ),
    "reheat_fouling": VavFault("reheat_fouling", "reheat", d_reheat_valve=5.0),
    "box_wide": VavFault("box_wide", "box-wide", d_damper_creep=5.0, d_reheat_valve=5.0),
    "hw_reset": VavFault(
        "hw_reset", "steady", d_reheat_valve=1.5, d_hw_supply=-1.0
    ),  # mild: sub-floor valve creep -> must not alarm
}


def _frame(n: int, *, start: str, seed: int, fault: VavFault | None, severity: int):
    """One role-frame: a two-regime box (cooling damper-control + heating reheat) with deltas."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="1h")
    hod = np.arange(n) % 24
    is_cool = (hod >= 9) & (hod < 17)
    is_heat = ~is_cool
    s = float(severity)
    f = fault

    d_creep = f.d_damper_creep * s if f else 0.0
    d_static = f.d_duct_static * s if f else 0.0
    d_valve = f.d_reheat_valve * s if f else 0.0
    d_hw = f.d_hw_supply * s if f else 0.0

    cool_cmd = _COOL_MID + _COOL_SWING * np.sin((hod - 9) / 8 * np.pi)  # 0->1->0 over 9..17
    command = np.where(is_cool, cool_cmd, _MIN_FLOW) + rng.normal(0, _FLOW_NOISE, n)
    command = np.clip(command, _MIN_FLOW * 0.8, _COOL_MID + _COOL_SWING + 200)
    damper = _D0 + _D_PER_CFM * command + d_creep + rng.normal(0, _DAMPER_NOISE, n)
    airflow = command + rng.normal(0, _FLOW_NOISE, n)
    mixed = _ENTER + rng.normal(0, _ENTER_NOISE, n)
    rise = _RISE_MID + _RISE_SWING * np.sin(hod / 24 * 2 * np.pi) + rng.normal(0, _RISE_NOISE, n)
    rise = np.where(is_heat, np.clip(rise, 4.0, 45.0), 0.0)  # cooling: no reheat rise
    supply = mixed + rise  # box discharge (warm); == mixed in cooling -> ΔT~0 -> reheat gated out
    duty = airflow * rise
    vnoise = rng.normal(0, _VALVE_NOISE, n)
    valve = np.where(is_heat, np.clip(_V0 + _V_PER_DUTY * duty + d_valve + vnoise, 0.0, 100.0), 0.0)
    hw = _HW0 + d_hw + rng.normal(0, _HW_NOISE, n)
    duct = _STATIC0 + d_static + rng.normal(0, _STATIC_NOISE, n)

    return pd.DataFrame(
        {
            Role.AIRFLOW_SP: command,
            Role.DAMPER: np.clip(damper, 0.0, 100.0),
            Role.AIRFLOW: airflow,
            Role.MIXED_AIR_TEMP: mixed,
            Role.SUPPLY_AIR_TEMP: supply,
            Role.HEAT_VALVE: valve,
            Role.HW_SUPPLY_TEMP: hw,
            Role.DUCT_STATIC: duct,
            Role.SUPPLY_FAN_STATUS: np.ones(n),
        },
        index=idx,
    )


@dataclass(frozen=True)
class SimulatedCase:
    """A healthy-baseline / faulted-current frame pair with its ground-truth locus label."""

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
        default: any injected fault whose expected locus is not ``steady`` (so ``hw_reset`` and
        healthy are negatives).
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
    equip: str = "VAV_SIM",
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


def build_vav_suite(store, *, site: str = "SIM", run_id: str = "SIM") -> list:
    """The two VAV drift detectors that feed the diagnosis, sharing one ``store``."""
    from .rules.vav_airflow_rule import VavAirflowDrift
    from .rules.vav_reheat_valve_rule import VavReheatValveDrift

    return [
        VavAirflowDrift(store, site=site, run_id=run_id),
        VavReheatValveDrift(store, site=site, run_id=run_id),
    ]


def diagnose_vav_frames(
    baseline,
    current,
    *,
    equip: str = "VAV_SIM",
    site: str = "SIM",
    run_id: str = "SIM",
) -> VavDriftDiagnosis:
    """Run the VAV drift suite on one ``(baseline, current)`` pair and diagnose the box."""
    from .store.modelstore import BaselineStore

    store = BaselineStore()
    suite = build_vav_suite(store, site=site, run_id=run_id)
    findings = [rule.analyze_periods(equip, baseline, current) for rule in suite]
    return diagnose_vav_drift(findings, equip=equip)


@dataclass
class LocusConfusion:
    """How well the VAV diagnosis' ``locus`` matches ground truth over a set of cases."""

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
    """Score the VAV diagnosis' ``locus`` against each case's ``expected_locus``.

    ``min_severity`` keeps only fault cases at or above that severity (healthy cases are always
    kept) -- to read localization accuracy on the clearer faults apart from the marginal ones.
    """
    matrix: dict = {}
    n = 0
    correct = 0
    for case in cases:
        if case.is_fault and case.severity < min_severity:
            continue
        diag = diagnose_vav_frames(
            case.baseline, case.current, equip=case.equip, site=site, run_id=run_id
        )
        row = matrix.setdefault(case.expected_locus, Counter())
        row[diag.locus] += 1
        n += 1
        if diag.locus == case.expected_locus:
            correct += 1
    accuracy = round(correct / n, 4) if n else float("nan")
    return LocusConfusion(n=n, accuracy=accuracy, matrix=matrix)
