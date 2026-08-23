"""Tests for the physics-grounded synthetic VAV validator (camber.vavsim).

These run the *real* VAV drift suite and diagnosis over physically consistent synthetic frames, so
they double as an end-to-end characterization of the terminal-box drift stack — above all the
upstream-vs-box disambiguation (the same damper creep localizes to `airflow` on its own but to
`upstream` when it co-moves with a duct-static fall). Deterministic; no measured dataset.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.driftvalidation import evaluate  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.vav_airflow_rule import VavAirflowDrift  # noqa: E402
from camber.rules.vav_reheat_valve_rule import VavReheatValveDrift  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402
from camber.vavdrift import VavDriftDiagnosis  # noqa: E402
from camber.vavsim import (  # noqa: E402
    FAULTS,
    LocusConfusion,
    SimulatedCase,
    diagnose_vav_frames,
    locus_confusion,
    make_cases,
    simulate_case,
)


def _cur_base(name, sev, seed):
    c = simulate_case(name, sev, seed=seed)
    return c.baseline, c.current


def _heating(frame):
    return frame[frame[Role.HEAT_VALVE] > 5]


# --------------------------------------------------------------------------- generation + coupling


def test_simulate_case_shapes():
    healthy = simulate_case(None, 0, seed=1)
    assert healthy.is_fault is False and healthy.expected_locus == "steady"
    up = simulate_case("upstream_starvation", 4, seed=1)
    assert isinstance(up, SimulatedCase) and up.expected_locus == "upstream"


def test_make_cases_counts():
    cases = make_cases(severities=(2, 4), n_healthy=3, n_per_fault=1)
    assert sum(1 for c in cases if not c.is_fault) == 3
    assert sum(1 for c in cases if c.is_fault) == len(FAULTS) * 2


def test_damper_creep_raises_damper():
    c = simulate_case("damper_authority_loss", 4, seed=3)
    assert c.current[Role.DAMPER].mean() > c.baseline[Role.DAMPER].mean() + 10
    assert abs(c.current[Role.DUCT_STATIC].median() - c.baseline[Role.DUCT_STATIC].median()) < 0.1


def test_upstream_starvation_drops_static_and_raises_damper():
    c = simulate_case("upstream_starvation", 4, seed=3)
    assert c.current[Role.DAMPER].mean() > c.baseline[Role.DAMPER].mean() + 10
    assert c.current[Role.DUCT_STATIC].median() < c.baseline[Role.DUCT_STATIC].median() - 0.3


def test_reheat_fouling_raises_valve_in_heating():
    c = simulate_case("reheat_fouling", 4, seed=3)
    assert (
        _heating(c.current)[Role.HEAT_VALVE].mean()
        > _heating(c.baseline)[Role.HEAT_VALVE].mean() + 10
    )


# ----------------------------------------------------------------------- end-to-end localization


def test_diagnose_vav_frames_returns_a_diagnosis():
    d = diagnose_vav_frames(*_cur_base("reheat_fouling", 4, seed=5))
    assert isinstance(d, VavDriftDiagnosis) and d.equip == "VAV_SIM"


def test_every_fault_localizes_at_sev4():
    for name, spec in FAULTS.items():
        d = diagnose_vav_frames(*_cur_base(name, 4, seed=42))
        assert d.locus == spec.expected_locus, (
            f"{name}: expected {spec.expected_locus}, got {d.locus}"
        )


# --------------------------------------------------------------------------- the money test


def test_upstream_starvation_localizes_to_upstream_not_airflow():
    d = diagnose_vav_frames(*_cur_base("upstream_starvation", 4, seed=11))
    assert d.locus == "upstream"
    assert d.signals["vav_airflow_drift"]["side"] == "upstream"
    assert any("plant-side starvation" in c for c in d.caveats)


def test_damper_authority_loss_localizes_to_airflow():
    """Same damper creep as upstream_starvation but no static fall -> the box, not the plant."""
    d = diagnose_vav_frames(*_cur_base("damper_authority_loss", 4, seed=11))
    assert d.locus == "airflow"
    assert d.signals["vav_airflow_drift"]["side"] == "airflow"


def test_box_wide_localizes_and_corroborates():
    d = diagnose_vav_frames(*_cur_base("box_wide", 4, seed=11))
    assert d.locus == "box-wide" and d.box_wide is True and d.corroborated is True


# --------------------------------------------------------------------------- hw_reset asymmetry


def test_mild_hw_reset_stays_steady():
    for sev in (2, 3, 4):
        d = diagnose_vav_frames(*_cur_base("hw_reset", sev, seed=30 + sev))
        assert d.locus == "steady"


def test_strong_hw_reset_fires_reheat_with_caveat():
    """A strong HW reset fires the reheat detector as a reheat positive WITH a waterside caveat."""
    base = simulate_case(None, 0, seed=50).baseline
    # a current period with a real reheat-valve creep AND a large HW-supply fall
    cur = simulate_case("reheat_fouling", 4, seed=51).current.copy()
    cur[Role.HW_SUPPLY_TEMP] = cur[Role.HW_SUPPLY_TEMP] - 8.0
    d = diagnose_vav_frames(base, cur)
    assert d.locus == "reheat"
    assert any("waterside-reset" in c for c in d.caveats)


# ----------------------------------------------------------------------- no false alarm / gradient


def test_healthy_does_not_false_alarm():
    for seed in range(0, 12, 2):
        d = diagnose_vav_frames(*_cur_base(None, 0, seed=seed))
        assert d.locus == "steady" and not d.causes and d.corroborated is False


def test_localization_degrades_at_low_severity():
    weak = locus_confusion(make_cases(seed0=7, severities=(1,), n_healthy=0))
    strong = locus_confusion(make_cases(seed0=7, severities=(4,), n_healthy=0))
    assert weak.accuracy < strong.accuracy and strong.accuracy >= 0.9


def test_locus_confusion_is_strong_on_clear_faults():
    lc = locus_confusion(make_cases(seed0=100), min_severity=3)
    assert isinstance(lc, LocusConfusion) and isinstance(lc.as_dict(), dict)
    assert lc.n >= 28
    assert lc.accuracy >= 0.9


# --------------------------------------------------------------------------- per-detector ROC


def test_airflow_detector_scores_against_the_damper_faults():
    cases = make_cases(seed0=3)
    # upstream_starvation IS a positive for the detector (it fires it); the upstream/airflow split
    # is the diagnosis' job, not the detector's.
    relevant = {"damper_authority_loss", "upstream_starvation", "box_wide"}
    labeled = [c.to_labeled(relevant=relevant) for c in cases]
    score = evaluate(lambda: VavAirflowDrift(BaselineStore(), site="S", run_id="R"), labeled)
    assert score.recall >= 0.5 and score.confusion.false_positive_rate <= 0.4


def test_reheat_detector_scores_against_the_reheat_faults():
    cases = make_cases(seed0=3)
    relevant = {"reheat_fouling", "box_wide"}
    labeled = [c.to_labeled(relevant=relevant) for c in cases]
    score = evaluate(lambda: VavReheatValveDrift(BaselineStore(), site="S", run_id="R"), labeled)
    assert score.recall >= 0.5 and score.confusion.false_positive_rate <= 0.4
