"""Tests for the physics-grounded synthetic pump validator (camber.pumpsim).

These run the *real* pump drift suite and per-loop diagnosis over physically consistent synthetic
frames, so they double as an end-to-end characterization of the pump drift stack. All data is
generated deterministically from a seed; nothing is drawn from any measured dataset.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.driftvalidation import evaluate  # noqa: E402
from camber.pumpdrift import PumpDriftDiagnosis  # noqa: E402
from camber.pumpsim import (  # noqa: E402
    FAULTS,
    LocusConfusion,
    SimulatedCase,
    diagnose_pump_frames,
    locus_confusion,
    make_cases,
    simulate_case,
)
from camber.rules.pump_flow_rule import PumpFlowDrift  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402

# --------------------------------------------------------------------------- generation


def test_simulate_case_shapes():
    healthy = simulate_case(None, 0, seed=1)
    assert healthy.is_fault is False and healthy.expected_locus == "steady"
    faulted = simulate_case("impeller_wear", 4, seed=1)
    assert isinstance(faulted, SimulatedCase)
    assert faulted.is_fault is True and faulted.expected_locus == "pump"
    from camber.model.roles import Role

    # impeller wear cuts flow: the current period moves less at matched speed
    assert faulted.current[Role.CHW_FLOW].mean() < faulted.baseline[Role.CHW_FLOW].mean()


def test_make_cases_counts():
    cases = make_cases(severities=(2, 4), n_healthy=3, n_per_fault=1)
    assert sum(1 for c in cases if not c.is_fault) == 3
    assert sum(1 for c in cases if c.is_fault) == len(FAULTS) * 2


# --------------------------------------------------------------------------- end-to-end diagnosis


def test_diagnose_pump_frames_returns_a_loop_diagnosis():
    c = simulate_case("clogged_strainer", 4, seed=5)
    d = diagnose_pump_frames(c.baseline, c.current, equip=c.equip)
    assert isinstance(d, PumpDriftDiagnosis) and d.equip == c.equip


def test_every_fault_family_localizes_correctly_at_high_severity():
    """The headline: each fault at severity 4 lands on its expected loop locus."""
    for name, spec in FAULTS.items():
        c = simulate_case(name, 4, seed=42)
        d = diagnose_pump_frames(c.baseline, c.current, equip=c.equip)
        assert d.locus == spec.expected_locus, (
            f"{name}: expected {spec.expected_locus}, got {d.locus}"
        )


def test_healthy_and_dp_reset_do_not_false_alarm():
    """Healthy loops -- and a DP *reset* that moves the setpoint -- must stay steady."""
    for seed in range(0, 12, 2):
        c = simulate_case(None, 0, seed=seed)
        assert diagnose_pump_frames(c.baseline, c.current, equip=c.equip).locus == "steady"
    for sev in (2, 3, 4):  # a reset schedule is not a fault at any severity
        c = simulate_case("dp_reset", sev, seed=30 + sev)
        assert diagnose_pump_frames(c.baseline, c.current, equip=c.equip).locus == "steady"


# --------------------------------------------------------------------------- the disambiguation


def test_impeller_wear_is_the_pump_and_clogged_strainer_is_the_distribution():
    """The flow-vs-head call: both cut flow, but only the pump also loses head."""
    wear = diagnose_pump_frames(*_fp("impeller_wear", 4, 11))
    strainer = diagnose_pump_frames(*_fp("clogged_strainer", 4, 12))
    assert wear.locus == "pump"
    assert strainer.locus == "distribution"
    assert any("check the distribution" in c for c in strainer.caveats)


def _fp(name, sev, seed):
    c = simulate_case(name, sev, seed=seed)
    return (c.baseline, c.current)


# --------------------------------------------------------------------------- locus confusion


def test_locus_confusion_is_strong_on_clear_faults():
    lc = locus_confusion(make_cases(seed0=100), min_severity=3)
    assert isinstance(lc, LocusConfusion) and isinstance(lc.as_dict(), dict)
    assert lc.n >= 30
    assert lc.accuracy >= 0.9  # clear faults (severity >= 3) localize essentially perfectly


def test_localization_degrades_gracefully_at_low_severity():
    weak = locus_confusion(make_cases(seed0=7, severities=(1,), n_healthy=0))
    strong = locus_confusion(make_cases(seed0=7, severities=(4,), n_healthy=0))
    assert weak.accuracy < strong.accuracy
    assert strong.accuracy >= 0.9


# --------------------------------------------------------------------------- per-detector ROC


def test_flow_detector_scores_against_the_flow_deficit_faults():
    cases = make_cases(seed0=3)
    target = frozenset({"impeller_wear", "cavitation", "entrained_air", "clogged_strainer"})
    labeled = [c.to_labeled(relevant=target) for c in cases]
    score = evaluate(lambda: PumpFlowDrift(BaselineStore(), site="S", run_id="R"), labeled)
    assert score.recall >= 0.5  # catches most flow-deficit faults (pooled over all severities)
    assert score.confusion.false_positive_rate <= 0.4
