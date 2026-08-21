"""Tests for the physics-grounded synthetic condenser validator (camber.condensersim).

These run the *real* condenser drift suite and diagnosis over physically consistent synthetic
frames, so they double as an end-to-end check of the heat-rejection drift stack — above all the
corroboration money test (tube scaling co-moves the condenser approach and head pressure through the
shared condensing temperature) and the head-pressure confound (an ambient CW rise with a quiet tower
must NOT be flagged as a heat-rejection fault). Deterministic from a seed; no measured dataset.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.condenserdrift import CondenserDriftDiagnosis  # noqa: E402
from camber.condensersim import (  # noqa: E402
    FAULTS,
    CauseConfusion,
    SimulatedCase,
    cause_confusion,
    diagnose_condenser_frames,
    make_cases,
    simulate_case,
)
from camber.driftvalidation import evaluate  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.chiller_cw_range_rule import ChillerCwRangeDrift  # noqa: E402
from camber.rules.chiller_drift_rule import ChillerApproachDrift  # noqa: E402
from camber.rules.chiller_head_pressure_rule import ChillerHeadPressureDrift  # noqa: E402
from camber.rules.coolingtower_drift_rule import CoolingTowerApproachDrift  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402

_CAUSE_SCALING = "condenser tube fouling or scale"
_CAUSE_TOWER = "cooling-tower heat rejection degrading"
_CAUSE_HEAD = "condenser high-side pressure rising (fouling / non-condensables)"


def _cur_base(name, sev, seed):
    c = simulate_case(name, sev, seed=seed)
    return c.baseline, c.current


# --------------------------------------------------------------------------- generation + coupling


def test_simulate_case_shapes():
    healthy = simulate_case(None, 0, seed=1)
    assert healthy.is_fault is False and healthy.expected_cause == ""
    scaling = simulate_case("tube_scaling", 4, seed=1)
    assert isinstance(scaling, SimulatedCase) and scaling.expected_cause == _CAUSE_SCALING
    assert scaling.expected_corroborated is True


def test_make_cases_counts():
    cases = make_cases(severities=(2, 4), n_healthy=3, n_per_fault=1)
    assert sum(1 for c in cases if not c.is_fault) == 3
    assert sum(1 for c in cases if c.is_fault) == len(FAULTS) * 2


def test_tube_scaling_raises_both_condenser_approach_and_head_pressure():
    """The money coupling: scaling widens the condenser approach AND raises head pressure."""
    c = simulate_case("tube_scaling", 4, seed=3)
    assert c.current[Role.COND_APPROACH_TEMP].mean() > c.baseline[Role.COND_APPROACH_TEMP].mean()
    assert c.current[Role.DISCHARGE_PRESSURE].mean() > c.baseline[Role.DISCHARGE_PRESSURE].mean()


def test_tower_fouling_raises_tower_approach_and_cw_supply():
    c = simulate_case("tower_fouling", 4, seed=3)
    tower_cur = (c.current[Role.CW_SUPPLY_TEMP] - c.current[Role.WETBULB_TEMP]).mean()
    tower_base = (c.baseline[Role.CW_SUPPLY_TEMP] - c.baseline[Role.WETBULB_TEMP]).mean()
    assert tower_cur > tower_base
    assert c.current[Role.CW_SUPPLY_TEMP].mean() > c.baseline[Role.CW_SUPPLY_TEMP].mean()
    assert c.current[Role.DISCHARGE_PRESSURE].mean() > c.baseline[Role.DISCHARGE_PRESSURE].mean()


def test_cw_flow_reduction_widens_range_without_head_pressure():
    """A flow fault must not leak into the high side (they stay separable)."""
    c = simulate_case("cw_flow_reduction", 4, seed=3)
    range_cur = (c.current[Role.CW_RETURN_TEMP] - c.current[Role.CW_SUPPLY_TEMP]).mean()
    range_base = (c.baseline[Role.CW_RETURN_TEMP] - c.baseline[Role.CW_SUPPLY_TEMP]).mean()
    assert range_cur > range_base + 1.5
    dp = c.current[Role.DISCHARGE_PRESSURE].mean() - c.baseline[Role.DISCHARGE_PRESSURE].mean()
    assert abs(dp) < 1.0


def test_ambient_cw_rise_raises_cw_supply_but_not_tower_approach():
    c = simulate_case("ambient_cw_rise", 4, seed=3)
    assert c.current[Role.CW_SUPPLY_TEMP].mean() > c.baseline[Role.CW_SUPPLY_TEMP].mean() + 2.0
    tower_cur = (c.current[Role.CW_SUPPLY_TEMP] - c.current[Role.WETBULB_TEMP]).mean()
    tower_base = (c.baseline[Role.CW_SUPPLY_TEMP] - c.baseline[Role.WETBULB_TEMP]).mean()
    assert abs(tower_cur - tower_base) < 0.5  # the tower is quiet


# --------------------------------------------------------------------------- end-to-end


def test_diagnose_condenser_frames_returns_a_diagnosis():
    d = diagnose_condenser_frames(*_cur_base("noncondensables", 4, seed=5))
    assert isinstance(d, CondenserDriftDiagnosis) and d.equip == "COND_SIM"


def test_every_fault_family_names_its_expected_cause_at_high_severity():
    for name, spec in FAULTS.items():
        if not spec.expected_cause:  # the confound is checked separately
            continue
        d = diagnose_condenser_frames(*_cur_base(name, 4, seed=42))
        assert spec.expected_cause in d.causes, f"{name}: {spec.expected_cause} not in {d.causes}"


# --------------------------------------------------------------------------- the money tests


def test_tube_scaling_corroborates_system_side_scaling():
    d = diagnose_condenser_frames(*_cur_base("tube_scaling", 4, seed=11))
    assert d.corroborated is True
    assert _CAUSE_SCALING in d.causes and _CAUSE_HEAD in d.causes
    assert "multiple signals corroborate" in d.summary


def test_ambient_cw_rise_is_demoted_not_corroborated():
    """A CW/head rise with a quiet tower is likely ambient — must not be a corroborated fault."""
    d = diagnose_condenser_frames(*_cur_base("ambient_cw_rise", 4, seed=12))
    assert d.corroborated is False
    assert d.signals["cooling_tower_approach_drift"]["cause"] is None  # tower stayed quiet
    assert any("ambient / high-load" in c for c in d.caveats)


# --------------------------------------------------------------------------- cause confusion


def test_cause_confusion_strong_on_clear_faults():
    lc = cause_confusion(make_cases(seed0=100), min_severity=3)
    assert isinstance(lc, CauseConfusion) and isinstance(lc.as_dict(), dict)
    assert lc.n >= 30
    assert lc.accuracy >= 0.9 and lc.corroboration_accuracy >= 0.9


def test_cause_detection_degrades_gracefully_at_low_severity():
    weak = cause_confusion(make_cases(seed0=7, severities=(1,), n_healthy=0))
    strong = cause_confusion(make_cases(seed0=7, severities=(4,), n_healthy=0))
    assert weak.accuracy < strong.accuracy and strong.accuracy >= 0.9


def test_healthy_loops_do_not_false_alarm():
    for seed in range(0, 12, 2):
        d = diagnose_condenser_frames(*_cur_base(None, 0, seed=seed))
        assert d.severity == "ok" and not d.causes and d.corroborated is False


# --------------------------------------------------------------------------- per-detector ROC


def _roc(rule_factory, relevant):
    cases = make_cases(seed0=3)
    labeled = [c.to_labeled(relevant=relevant) for c in cases]
    return evaluate(rule_factory, labeled)


def test_condenser_approach_detector_scores_against_scaling():
    score = _roc(
        lambda: ChillerApproachDrift(BaselineStore(), site="S", run_id="R"), {"tube_scaling"}
    )
    assert score.recall >= 0.5 and score.confusion.false_positive_rate <= 0.4


def test_cw_range_detector_scores_against_flow_faults():
    score = _roc(
        lambda: ChillerCwRangeDrift(BaselineStore(), site="S", run_id="R"),
        {"cw_flow_reduction", "cw_bypass"},
    )
    assert score.recall >= 0.5 and score.confusion.false_positive_rate <= 0.4


def test_tower_detector_scores_against_tower_fouling():
    score = _roc(
        lambda: CoolingTowerApproachDrift(BaselineStore(), site="S", run_id="R"), {"tower_fouling"}
    )
    assert score.recall >= 0.5 and score.confusion.false_positive_rate <= 0.4


def test_head_pressure_detector_scores_against_the_pressure_raising_faults():
    score = _roc(
        lambda: ChillerHeadPressureDrift(BaselineStore(), site="S", run_id="R"),
        {"tube_scaling", "tower_fouling", "noncondensables"},
    )
    assert score.recall >= 0.5 and score.confusion.false_positive_rate <= 0.4
