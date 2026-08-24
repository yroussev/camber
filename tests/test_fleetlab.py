"""Tests for the generated multi-zone fleet + G36 reset validation harness (camber.fleetlab).

These always run (no download): they lock the generator's determinism and label correctness, prove
each fault archetype fires exactly its target detector with the *right* attribution, prove the
fault-free fleet and the cross-archetypes stay quiet (real FPR negatives), and prove the whole
benchmark reaches TPR 1.0 / FPR 0.0. Mirrors tests/test_faultlab.py + tests/test_lbnl_benchmark.py.
"""

import os
import sys

import pytest
from pandas.testing import assert_frame_equal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber import fleetlab  # noqa: E402
from camber.eval import benchmark  # noqa: E402


def _fired(finding):
    return finding.severity in ("warn", "fault")


# --------------------------------------------------------------------------- generation


def test_generate_fleet_is_deterministic():
    a = fleetlab.generate_fleet("rogue", reset="sat", seed=0)
    b = fleetlab.generate_fleet("rogue", reset="sat", seed=0)
    assert a.zone_frames.keys() == b.zone_frames.keys()
    for z in a.zone_frames:
        assert_frame_equal(a.zone_frames[z], b.zone_frames[z])
    for ahu in a.ahu_reset_frames:
        assert_frame_equal(a.ahu_reset_frames[ahu], b.ahu_reset_frames[ahu])


def test_topology_scopes_every_zone_to_its_ahu():
    fleet = fleetlab.generate_fleet("cohort", reset="static", n_ahus=2, zones_per_ahu=4)
    gm = fleet.topology.group_map(list(fleet.zone_frames))
    assert set(gm) == set(fleet.zone_frames)  # every zone covered
    assert gm["AHU1-Z1"] == "AHU1" and gm["AHU2-Z1"] == "AHU2"
    assert fleet.topology.provenance == "semantic"  # -> no heuristic caveat, per-AHU scoping


@pytest.mark.parametrize("reset", fleetlab.RESETS)
def test_label_matches_injection(reset):
    assert fleetlab.generate_fleet("none", reset=reset).label.fault == "none"
    rogue = fleetlab.generate_fleet("rogue", reset=reset).label
    assert rogue.rogue_zone == "AHU1-Z1" and rogue.starved_group is None
    cohort = fleetlab.generate_fleet("cohort", reset=reset).label
    assert cohort.starved_group == "AHU1" and cohort.rogue_zone is None
    stuck = fleetlab.generate_fleet("reset_stuck", reset=reset).label
    assert stuck.reset_equip == "AHU1" and stuck.reset_reason == "stuck"


def test_generate_fleet_rejects_unknown_archetype_and_reset():
    with pytest.raises(ValueError):
        fleetlab.generate_fleet("bogus")
    with pytest.raises(ValueError):
        fleetlab.generate_fleet("none", reset="pressure")


# --------------------------------------------------------------------------- per-archetype firing


@pytest.mark.parametrize("reset", fleetlab.RESETS)
def test_rogue_fires_rogue_and_attributes(reset):
    fleet = fleetlab.generate_fleet("rogue", reset=reset)
    findings = fleetlab.run_detectors(fleet)
    assert _fired(findings[f"{reset}_rogue_zone_census"])
    assert findings[f"{reset}_rogue_zone_census"].metrics["worst_zone"] == "AHU1-Z1"
    # cross-negatives: a lone rogue is neither a cohort nor a reset fault
    assert not _fired(findings[f"{reset}_cohort_starvation"])
    assert not _fired(findings[f"{reset}_reset_effectiveness"])


@pytest.mark.parametrize("reset", fleetlab.RESETS)
def test_cohort_fires_cohort_and_attributes(reset):
    fleet = fleetlab.generate_fleet("cohort", reset=reset)
    findings = fleetlab.run_detectors(fleet)
    assert _fired(findings[f"{reset}_cohort_starvation"])
    assert findings[f"{reset}_cohort_starvation"].metrics["worst_group"] == "AHU1"
    assert not _fired(findings[f"{reset}_rogue_zone_census"])  # even shares -> no rogue
    assert not _fired(findings[f"{reset}_reset_effectiveness"])


@pytest.mark.parametrize("reset", fleetlab.RESETS)
@pytest.mark.parametrize("arche,reason", list(fleetlab._RESET_REASON.items()))
def test_reset_archetype_fires_right_reason(reset, arche, reason):
    fleet = fleetlab.generate_fleet(arche, reset=reset)
    findings = fleetlab.run_detectors(fleet)
    re = findings[f"{reset}_reset_effectiveness"]
    assert _fired(re) and re.metrics["reason"] == reason
    # a reset fault is not a demand-side (rogue/cohort) fault
    assert not _fired(findings[f"{reset}_rogue_zone_census"])
    assert not _fired(findings[f"{reset}_cohort_starvation"])


@pytest.mark.parametrize("reset", fleetlab.RESETS)
def test_fault_free_fleet_is_quiet(reset):
    findings = fleetlab.run_detectors(fleetlab.generate_fleet("none", reset=reset))
    assert not any(_fired(f) for f in findings.values())  # 0 FPR on the fault-free fleet


# --------------------------------------------------------------------------- harness / benchmark


def test_labeled_records_shape_and_fault_free_truth():
    recs = fleetlab.labeled_records()
    assert len(recs) == len(fleetlab.RESETS) * len(fleetlab.ARCHETYPES)
    for r in recs:
        assert set(r) == {"truth", "fired"} and isinstance(r["fired"], set)
    # exactly the two fault-free fleets carry the empty (fault-free) truth
    assert sum(1 for r in recs if r["truth"] == "") == len(fleetlab.RESETS)


def test_benchmark_scores_perfect_with_real_negatives():
    rep = benchmark(fleetlab.labeled_records(), fleetlab.targets())
    assert rep.overall.true_positive_rate == 1.0
    assert rep.overall.false_positive_rate == 0.0
    assert rep.correct_diagnosis == 1.0
    for name, c in rep.per_detector.items():
        assert c.true_positive_rate == 1.0, name
        assert c.false_positive_rate == 0.0, name
        assert c.fp + c.tn > 0, f"{name} has no negatives -> FPR is unmeasured"


def test_attribution_is_perfect_and_covers_every_detector():
    attrib = fleetlab.attribution()
    assert set(attrib) == set(fleetlab.targets())
    assert all(v == 1.0 for v in attrib.values())


def test_coverage_lists_all_six_fleet_detectors():
    cov = fleetlab.coverage()
    assert cov["n_fleet_scored"] == 6
    assert set(cov["fleet_scored"]) == set(fleetlab.targets())
