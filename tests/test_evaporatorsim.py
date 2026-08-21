"""Tests for the physics-grounded synthetic evaporator validator (camber.evaporatorsim).

These run the *real* evaporator drift suite and diagnosis over physically consistent synthetic
frames, so they double as an end-to-end check of the low-side drift stack — above all the feed
cross-check (overfeed lowers superheat and raises suction through a shared feed latent, so the
diagnosis corroborates it) and the CHW-reset confound (a suction rise with quiet superheat must NOT
be corroborated). The disagree/ambiguous branch is out of scope here (covered by
test_evaporatordrift.py). Deterministic from a seed; no measured dataset.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.driftvalidation import evaluate  # noqa: E402
from camber.evaporatordrift import EvaporatorDriftDiagnosis  # noqa: E402
from camber.evaporatorsim import (  # noqa: E402
    FAULTS,
    CauseConfusion,
    SimulatedCase,
    cause_confusion,
    diagnose_evaporator_frames,
    make_cases,
    simulate_case,
)
from camber.model.roles import Role  # noqa: E402
from camber.rules.chiller_drift_rule import ChillerApproachDrift  # noqa: E402
from camber.rules.chiller_suction_pressure_rule import ChillerSuctionPressureDrift  # noqa: E402
from camber.rules.chiller_superheat_rule import ChillerSuperheatDrift  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402

_CAUSE_FOULING = "evaporator tube fouling or scale"
_CAUSE_OVERFED_SH = "evaporator overfed — liquid floodback risk"
_CAUSE_OVERFEED_SP = "evaporator overfeed / flooding"
_CAUSE_STARVED_SH = "evaporator starved / underfed (undercharge or restricted metering)"


def _cur_base(name, sev, seed):
    c = simulate_case(name, sev, seed=seed)
    return c.baseline, c.current


# --------------------------------------------------------------------------- generation + coupling


def test_simulate_case_shapes():
    healthy = simulate_case(None, 0, seed=1)
    assert healthy.is_fault is False and healthy.expected_cause == ""
    over = simulate_case("overfeed", 4, seed=1)
    assert isinstance(over, SimulatedCase) and over.expected_cause == _CAUSE_OVERFED_SH
    assert over.expected_corroborated is True


def test_make_cases_counts():
    cases = make_cases(severities=(2, 4), n_healthy=3, n_per_fault=1)
    assert sum(1 for c in cases if not c.is_fault) == 3
    assert sum(1 for c in cases if c.is_fault) == len(FAULTS) * 2


def test_overfeed_lowers_superheat_and_raises_suction():
    c = simulate_case("overfeed", 4, seed=3)
    assert c.current[Role.SUPERHEAT_TEMP].mean() < c.baseline[Role.SUPERHEAT_TEMP].mean()
    assert c.current[Role.SUCTION_PRESSURE].mean() > c.baseline[Role.SUCTION_PRESSURE].mean()


def test_starvation_raises_superheat_and_lowers_suction():
    c = simulate_case("starvation", 4, seed=3)
    assert c.current[Role.SUPERHEAT_TEMP].mean() > c.baseline[Role.SUPERHEAT_TEMP].mean()
    assert c.current[Role.SUCTION_PRESSURE].mean() < c.baseline[Role.SUCTION_PRESSURE].mean()


def test_evap_fouling_widens_approach_without_moving_the_feed():
    c = simulate_case("evap_fouling", 4, seed=3)
    assert (
        c.current[Role.EVAP_APPROACH_TEMP].mean() > c.baseline[Role.EVAP_APPROACH_TEMP].mean() + 1.5
    )
    sh = c.current[Role.SUPERHEAT_TEMP].mean() - c.baseline[Role.SUPERHEAT_TEMP].mean()
    sp = c.current[Role.SUCTION_PRESSURE].mean() - c.baseline[Role.SUCTION_PRESSURE].mean()
    assert abs(sh) < 0.5 and abs(sp) < 1.0  # the feed axis stays quiet


def test_chw_reset_lifts_suction_with_steady_superheat():
    c = simulate_case("chw_reset", 4, seed=3)
    assert c.current[Role.CHW_SUPPLY_TEMP].mean() > c.baseline[Role.CHW_SUPPLY_TEMP].mean() + 2.0
    assert c.current[Role.SUCTION_PRESSURE].mean() > c.baseline[Role.SUCTION_PRESSURE].mean()
    sh = c.current[Role.SUPERHEAT_TEMP].mean() - c.baseline[Role.SUPERHEAT_TEMP].mean()
    assert abs(sh) < 0.5  # superheat quiet


# --------------------------------------------------------------------------- end-to-end


def test_diagnose_evaporator_frames_returns_a_diagnosis():
    d = diagnose_evaporator_frames(*_cur_base("evap_fouling", 4, seed=5))
    assert isinstance(d, EvaporatorDriftDiagnosis) and d.equip == "EVAP_SIM"


def test_every_real_fault_names_its_cause_at_high_severity():
    for name, spec in FAULTS.items():
        if not spec.expected_cause:  # the confound is checked separately
            continue
        d = diagnose_evaporator_frames(*_cur_base(name, 4, seed=42))
        assert spec.expected_cause in d.causes, f"{name}: {spec.expected_cause} not in {d.causes}"


# --------------------------------------------------------------------------- the money tests


def test_overfeed_corroborates_and_feeds_agree():
    d = diagnose_evaporator_frames(*_cur_base("overfeed", 4, seed=11))
    assert d.corroborated is True
    assert _CAUSE_OVERFED_SH in d.causes and _CAUSE_OVERFEED_SP in d.causes
    assert any("agree the evaporator is overfed" in c for c in d.caveats)


def test_starvation_corroborates_and_feeds_agree():
    d = diagnose_evaporator_frames(*_cur_base("starvation", 4, seed=11))
    assert d.corroborated is True
    assert _CAUSE_STARVED_SH in d.causes
    assert any("agree the evaporator is starved" in c for c in d.caveats)


def test_chw_reset_is_not_corroborated():
    """A CHW-reset suction rise with quiet superheat -- the cross-check must not corroborate it."""
    d = diagnose_evaporator_frames(*_cur_base("chw_reset", 4, seed=12))
    assert d.corroborated is False
    assert d.signals["chiller_superheat_drift"]["cause"] is None  # superheat stayed quiet


# --------------------------------------------------------------------------- cause confusion


def test_cause_confusion_strong_on_clear_faults():
    cc = cause_confusion(make_cases(seed0=100), min_severity=3)
    assert isinstance(cc, CauseConfusion) and isinstance(cc.as_dict(), dict)
    assert cc.n >= 24
    assert cc.accuracy >= 0.9 and cc.corroboration_accuracy >= 0.9


def test_cause_detection_degrades_gracefully_at_low_severity():
    weak = cause_confusion(make_cases(seed0=7, severities=(1,), n_healthy=0))
    strong = cause_confusion(make_cases(seed0=7, severities=(4,), n_healthy=0))
    assert weak.accuracy < strong.accuracy and strong.accuracy >= 0.9


def test_healthy_loops_do_not_false_alarm():
    for seed in range(0, 12, 2):
        d = diagnose_evaporator_frames(*_cur_base(None, 0, seed=seed))
        assert d.severity == "ok" and not d.causes and d.corroborated is False


# --------------------------------------------------------------------------- per-detector ROC


def _roc(rule_factory, relevant):
    cases = make_cases(seed0=3)
    labeled = [c.to_labeled(relevant=relevant) for c in cases]
    return evaluate(rule_factory, labeled)


def test_evaporator_approach_detector_scores_against_fouling():
    score = _roc(
        lambda: ChillerApproachDrift(BaselineStore(), site="S", run_id="R"), {"evap_fouling"}
    )
    assert score.recall >= 0.5 and score.confusion.false_positive_rate <= 0.4


def test_superheat_detector_scores_against_the_feed_faults():
    score = _roc(
        lambda: ChillerSuperheatDrift(BaselineStore(), site="S", run_id="R"),
        {"overfeed", "starvation"},
    )
    assert score.recall >= 0.5 and score.confusion.false_positive_rate <= 0.4


def test_suction_detector_scores_against_the_feed_faults():
    score = _roc(
        lambda: ChillerSuctionPressureDrift(BaselineStore(), site="S", run_id="R"),
        {"overfeed", "starvation"},
    )
    assert score.recall >= 0.5 and score.confusion.false_positive_rate <= 0.4
