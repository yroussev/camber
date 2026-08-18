"""Tests for the physics-grounded synthetic chiller validator (camber.driftsim).

These run the *real* drift suite and roll-up over physically consistent synthetic frames, so they
double as an end-to-end characterization of the drift stack: each fault family, imposed at a graded
severity, should localize to the right roll-up ``locus``, and healthy periods should stay steady.
All data is generated deterministically from a seed; nothing is drawn from any measured dataset.
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.chillerdiag import ChillerDriftDiagnosis  # noqa: E402
from camber.driftsim import (  # noqa: E402
    FAULTS,
    LocusConfusion,
    SimulatedCase,
    diagnose_frames,
    locus_confusion,
    make_cases,
    saturation_psig,
    simulate_case,
)
from camber.driftvalidation import evaluate  # noqa: E402
from camber.rules.chiller_head_pressure_rule import ChillerHeadPressureDrift  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402

# --------------------------------------------------------------------------- the saturation curve


def test_saturation_curve_is_monotone_and_realistic():
    temps = np.array([0.0, 40.0, 90.0, 120.0])
    p = saturation_psig(temps)
    assert np.all(np.diff(p) > 0)  # strictly increasing
    assert 25.0 < saturation_psig(40.0) < 45.0  # low-side ballpark
    assert 100.0 < saturation_psig(90.0) < 160.0  # high-side ballpark


# --------------------------------------------------------------------------- generation


def test_simulate_case_healthy_vs_fault_shapes():
    healthy = simulate_case(None, 0, seed=1)
    assert healthy.is_fault is False and healthy.expected_locus == "steady"
    assert healthy.fault_name == "" and healthy.severity == 0

    faulted = simulate_case("condenser_fouling", 3, seed=1)
    assert isinstance(faulted, SimulatedCase)
    assert faulted.is_fault is True and faulted.severity == 3
    assert faulted.expected_locus == "condenser"
    # baseline is healthy; the fault only lifts the current period's head pressure
    from camber.model.roles import Role

    assert (
        faulted.current[Role.DISCHARGE_PRESSURE].mean()
        > faulted.baseline[Role.DISCHARGE_PRESSURE].mean()
    )


def test_make_cases_counts():
    cases = make_cases(severities=(2, 4), n_healthy=3, n_per_fault=1)
    assert sum(1 for c in cases if not c.is_fault) == 3
    assert sum(1 for c in cases if c.is_fault) == len(FAULTS) * 2
    assert all(isinstance(c, SimulatedCase) for c in cases)


# --------------------------------------------------------------------------- end-to-end diagnosis


def test_diagnose_frames_returns_a_rollup():
    c = simulate_case("evaporator_fouling", 4, seed=5)
    d = diagnose_frames(c.baseline, c.current, equip=c.equip)
    assert isinstance(d, ChillerDriftDiagnosis)
    assert d.equip == c.equip


def test_every_fault_family_localizes_correctly_at_high_severity():
    """The headline validation: each fault at severity 4 lands on its expected roll-up locus."""
    for name, spec in FAULTS.items():
        c = simulate_case(name, 4, seed=42)
        d = diagnose_frames(c.baseline, c.current, equip=c.equip)
        assert d.locus == spec.expected_locus, (
            f"{name}: expected {spec.expected_locus}, got {d.locus}"
        )
        assert d.severity == "fault"


def test_healthy_periods_do_not_false_alarm():
    for seed in range(0, 16, 2):
        c = simulate_case(None, 0, seed=seed)
        d = diagnose_frames(c.baseline, c.current, equip=c.equip)
        assert d.locus == "steady" and d.severity == "ok"


# --------------------------------------------------------------------------- confound / isolation


def test_condenser_fouling_lights_the_high_side_cleanly():
    """cond approach + head pressure up, CW supply flat -> no ambient confound; tower quiet."""
    c = simulate_case("condenser_fouling", 4, seed=11)
    d = diagnose_frames(c.baseline, c.current, equip=c.equip)
    assert d.locus == "condenser"
    assert d.condenser.signals["chiller_head_pressure_drift"]["severity"] in ("warn", "fault")
    # a clean high-side fault carries no heat-rejection/ambient confound caveat
    assert not any("ambient" in cav for cav in d.condenser.caveats)


def test_tower_degradation_reads_as_corroborated_not_confounded():
    """CW supply rises -> head pressure AND tower approach both move -> corroborating."""
    c = simulate_case("tower_degradation", 4, seed=12)
    d = diagnose_frames(c.baseline, c.current, equip=c.equip)
    assert d.locus == "condenser"
    assert any("corroborating, not confounded" in cav for cav in d.condenser.caveats)


def test_undercharge_is_machine_wide_with_a_charge_corroboration():
    c = simulate_case("refrigerant_undercharge", 4, seed=13)
    d = diagnose_frames(c.baseline, c.current, equip=c.equip)
    assert d.locus == "whole-machine" and d.machine_wide is True
    assert d.charge is not None  # subcooling drifted (down = undercharge)
    assert any("circuit-wide" in cav for cav in d.caveats)


# --------------------------------------------------------------------------- locus confusion matrix


def test_locus_confusion_is_perfect_on_clear_faults():
    lc = locus_confusion(make_cases(seed0=100), min_severity=3)
    assert isinstance(lc, LocusConfusion) and isinstance(lc.as_dict(), dict)
    assert lc.n >= 30
    assert lc.accuracy >= 0.9  # clear faults (severity >= 3) localize essentially perfectly


def test_localization_degrades_gracefully_at_low_severity():
    """An honest validator: marginal faults are hard, clear faults are localized."""
    weak = locus_confusion(make_cases(seed0=7, severities=(1,), n_healthy=0))
    strong = locus_confusion(make_cases(seed0=7, severities=(4,), n_healthy=0))
    assert weak.accuracy < strong.accuracy
    assert strong.accuracy >= 0.9


# --------------------------------------------------------------------------- per-detector ROC


def test_head_pressure_detector_scores_against_its_target_faults():
    """to_labeled(relevant=) + evaluate: the head-pressure rule catches high-side faults, mostly."""
    cases = make_cases(seed0=3)
    target = frozenset(
        {
            "condenser_fouling",
            "reduced_cw_flow",
            "tower_degradation",
            "non_condensables",
            "refrigerant_overcharge",
        }
    )
    labeled = [c.to_labeled(relevant=target) for c in cases]
    score = evaluate(
        lambda: ChillerHeadPressureDrift(BaselineStore(), site="S", run_id="R"), labeled
    )
    assert score.recall >= 0.5  # catches most of the high-side faults (pooled over all severities)
    assert score.confusion.false_positive_rate <= 0.4  # stays reasonably quiet on the rest
