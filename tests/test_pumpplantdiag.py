"""Tests for the per-plant pump drift roll-up (camber.pumpplantdiag)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.pumpdrift import PumpDriftDiagnosis  # noqa: E402
from camber.pumpplantdiag import PumpPlantDiagnosis, diagnose_pump_plant  # noqa: E402


def _pump(equip, severity="fault", locus="pump"):
    degrading = severity in ("warn", "fault")
    return PumpDriftDiagnosis(
        equip=equip,
        severity=severity,
        locus=locus,
        loop_wide=(locus == "loop-wide"),
        causes=["a cause"] if degrading else [],
        signals={},
        corroborated=False,
        summary=f"{equip}: {locus}",
        caveats=[],
    )


# --------------------------------------------------------------------------- steady


def test_no_pumps_is_steady():
    d = diagnose_pump_plant([], plant="CHW plant")
    assert d.severity == "ok" and d.locus == "steady"
    assert d.n_pumps == 0 and d.n_degrading == 0
    assert isinstance(d, PumpPlantDiagnosis) and isinstance(d.as_dict(), dict)


def test_all_steady_pumps_is_steady():
    d = diagnose_pump_plant([_pump("P1", "ok", "steady"), _pump("P2", "ok", "steady")])
    assert d.locus == "steady" and d.n_pumps == 2 and d.n_degrading == 0


# --------------------------------------------------------------------------- single pump


def test_one_drifting_pump_is_single_pump():
    d = diagnose_pump_plant(
        [_pump("P1", "fault", "pump"), _pump("P2", "ok", "steady")], plant="CHW"
    )
    assert d.locus == "single-pump" and d.severity == "fault"
    assert d.n_degrading == 1
    assert "P1" in d.recommendation and "service or stage" in d.recommendation
    assert "P1" in d.summary


# --------------------------------------------------------------------------- shared distribution


def test_two_loops_on_the_distribution_side_is_a_central_cause():
    d = diagnose_pump_plant(
        [_pump("P1", "fault", "distribution"), _pump("P2", "warn", "loop-wide")]
    )
    assert d.locus == "distribution" and d.severity == "fault"
    assert "central distribution" in d.recommendation
    assert any("shared or central hydraulic cause" in c for c in d.caveats)


# --------------------------------------------------------------------------- plant-wide


def test_two_pumps_on_the_mechanical_side_is_plant_wide():
    d = diagnose_pump_plant([_pump("P1", "fault", "pump"), _pump("P2", "warn", "pump")])
    assert d.locus == "plant-wide"
    assert "common-mode" in d.recommendation


def test_a_pump_and_a_distribution_loop_is_plant_wide():
    """Only one distribution-side loop -> not a shared distribution cause -> plant-wide."""
    d = diagnose_pump_plant([_pump("P1", "fault", "pump"), _pump("P2", "fault", "distribution")])
    assert d.locus == "plant-wide"


# --------------------------------------------------------------------------- structure


def test_severity_is_the_worst_across_pumps():
    d = diagnose_pump_plant([_pump("P1", "warn", "pump"), _pump("P2", "ok", "steady")])
    assert d.severity == "warn"


def test_pumps_are_nested_and_serializable():
    d = diagnose_pump_plant([_pump("P1", "fault", "pump")], plant="Plant A")
    dd = d.as_dict()
    assert dd["plant"] == "Plant A"
    assert isinstance(dd["pumps"], list) and dd["pumps"][0]["equip"] == "P1"
    assert dd["pumps"][0]["locus"] == "pump"
