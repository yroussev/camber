"""Tests for the physics-grounded synthetic AHU validator (camber.ahusim).

These run the *real* air-side drift suite and per-AHU diagnosis over physically consistent synthetic
frames, so they double as an end-to-end characterization of the AHU drift stack -- above all the
fan-power disambiguation (a loading filter, which the coupled physics makes raise fan power too,
localizes to the air path; a fan-mechanical fault localizes to the fan). Deterministic from a seed;
nothing is drawn from any measured dataset.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.ahudrift import AhuDriftDiagnosis  # noqa: E402
from camber.ahusim import (  # noqa: E402
    FAULTS,
    LocusConfusion,
    SimulatedCase,
    diagnose_ahu_frames,
    locus_confusion,
    make_cases,
    simulate_case,
)
from camber.driftvalidation import evaluate  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.fan_efficiency_rule import FanEfficiencyDrift  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402

# --------------------------------------------------------------------------- generation + coupling


def test_simulate_case_shapes():
    healthy = simulate_case(None, 0, seed=1)
    assert healthy.is_fault is False and healthy.expected_locus == "steady"
    faulted = simulate_case("filter_loading", 4, seed=1)
    assert isinstance(faulted, SimulatedCase) and faulted.expected_locus == "air-path"


def test_filter_loading_raises_both_filter_dp_and_fan_power():
    """The coupling: a loading filter must move BOTH channels (that's what makes Case A real)."""
    c = simulate_case("filter_loading", 4, seed=3)
    assert c.current[Role.FILTER_DIFF_PRESS].mean() > c.baseline[Role.FILTER_DIFF_PRESS].mean()
    assert c.current[Role.POWER].mean() > c.baseline[Role.POWER].mean()


def test_make_cases_counts():
    cases = make_cases(severities=(2, 4), n_healthy=3, n_per_fault=1)
    assert sum(1 for c in cases if not c.is_fault) == 3
    assert sum(1 for c in cases if c.is_fault) == len(FAULTS) * 2


# --------------------------------------------------------------------------- end-to-end


def test_diagnose_ahu_frames_returns_a_diagnosis():
    c = simulate_case("cooling_coil_fouling", 4, seed=5)
    d = diagnose_ahu_frames(c.baseline, c.current, equip=c.equip)
    assert isinstance(d, AhuDriftDiagnosis) and d.equip == c.equip


def test_every_fault_family_localizes_correctly_at_high_severity():
    """The headline: each fault at severity 4 lands on its expected AHU locus."""
    for name, spec in FAULTS.items():
        c = simulate_case(name, 4, seed=42)
        d = diagnose_ahu_frames(c.baseline, c.current, equip=c.equip)
        assert d.locus == spec.expected_locus, (
            f"{name}: expected {spec.expected_locus}, got {d.locus}"
        )


def test_healthy_and_static_reset_do_not_false_alarm():
    for seed in range(0, 12, 2):
        c = simulate_case(None, 0, seed=seed)
        assert diagnose_ahu_frames(c.baseline, c.current, equip=c.equip).locus == "steady"
    for sev in (2, 3, 4):  # a static-reset schedule is not a fault at any severity
        c = simulate_case("static_reset", sev, seed=30 + sev)
        assert diagnose_ahu_frames(c.baseline, c.current, equip=c.equip).locus == "steady"


# --------------------------------------------------------------------------- the money test


def test_fan_power_disambiguation_routes_correctly():
    """A loading filter (coupled power-up) -> air-path; a fan-mechanical fault -> fan."""
    fl = diagnose_ahu_frames(*_fp("filter_loading", 4, 11))
    belt = diagnose_ahu_frames(*_fp("fan_belt_slip", 4, 12))
    loss = diagnose_ahu_frames(*_fp("duct_static_loss", 4, 13))
    over = diagnose_ahu_frames(*_fp("over_pressurization", 4, 14))
    assert fl.locus == "air-path"  # Case A
    assert any("fix the air path before the fan" in c for c in fl.caveats)
    assert belt.locus == "fan"  # Case C
    assert any("isolates to the fan itself" in c for c in belt.caveats)
    assert loss.locus == "fan"  # Case B
    assert any("losing static" in c for c in loss.caveats)
    assert over.locus == "air-path"  # Case A, static arm


def _fp(name, sev, seed):
    c = simulate_case(name, sev, seed=seed)
    return (c.baseline, c.current)


# --------------------------------------------------------------------------- locus confusion


def test_locus_confusion_is_strong_on_clear_faults():
    lc = locus_confusion(make_cases(seed0=100), min_severity=3)
    assert isinstance(lc, LocusConfusion) and isinstance(lc.as_dict(), dict)
    assert lc.n >= 30
    assert lc.accuracy >= 0.9


def test_localization_degrades_gracefully_at_low_severity():
    weak = locus_confusion(make_cases(seed0=7, severities=(1,), n_healthy=0))
    strong = locus_confusion(make_cases(seed0=7, severities=(4,), n_healthy=0))
    assert weak.accuracy < strong.accuracy
    assert strong.accuracy >= 0.9


# --------------------------------------------------------------------------- per-detector ROC


def test_fan_power_detector_scores_against_the_power_raising_faults():
    cases = make_cases(seed0=3)
    target = frozenset(
        {
            "fan_belt_slip",
            "bearing_drag",
            "filter_loading",
            "duct_static_loss",
            "over_pressurization",
        }
    )
    labeled = [c.to_labeled(relevant=target) for c in cases]
    score = evaluate(lambda: FanEfficiencyDrift(BaselineStore(), site="S", run_id="R"), labeled)
    assert score.recall >= 0.5
    assert score.confusion.false_positive_rate <= 0.4
