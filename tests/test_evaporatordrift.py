"""Tests for the evaporator-loop drift co-movement diagnosis (camber.evaporatordrift).

Synthetic Findings stand in for the three evaporator-side drift rules; nothing runs the rules or
touches data.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.evaporatordrift import EvaporatorDriftDiagnosis, diagnose_evaporator_drift  # noqa: E402
from camber.rules.base import Finding  # noqa: E402


def _f(rule, severity, **metrics):
    return Finding(rule=rule, equip="CH_1", severity=severity, metrics=metrics)


def _evap(sev="fault", drift_f=2.5, drift_sigma=4.0, **extra):
    return _f(
        "chiller_approach_drift", sev, evap_drift_f=drift_f, evap_drift_sigma=drift_sigma, **extra
    )


def _sh(sev="fault", direction="down", drift_f=-3.0):
    return _f(
        "chiller_superheat_drift",
        sev,
        superheat_drift_direction=direction,
        superheat_drift_f=drift_f,
    )


def _sp(sev="fault", direction="down", drift_psi=-8.0):
    return _f(
        "chiller_suction_pressure_drift",
        sev,
        suction_pressure_drift_direction=direction,
        suction_pressure_drift_psi=drift_psi,
    )


# --------------------------------------------------------------------------- empty / steady


def test_no_evaporator_findings_is_steady():
    d = diagnose_evaporator_drift([])
    assert d.severity == "ok" and d.causes == [] and d.corroborated is False
    assert "steady" in d.summary
    assert isinstance(d, EvaporatorDriftDiagnosis) and isinstance(d.as_dict(), dict)


def test_all_ok_signals_are_steady():
    d = diagnose_evaporator_drift(
        [_evap("ok", 0.1, 0.3), _sh("ok", "down", -0.1), _sp("ok", "up", 0.1)]
    )
    assert d.severity == "ok" and d.causes == [] and d.corroborated is False


# --------------------------------------------------------------------------- localization


def test_evaporator_approach_widening_is_tube_fouling():
    d = diagnose_evaporator_drift([_evap("fault", 2.5, 4.0)])
    assert d.causes == ["evaporator tube fouling or scale"] and d.severity == "fault"


def test_falling_superheat_is_overfeed_floodback():
    d = diagnose_evaporator_drift([_sh("fault", "down", -3.0)])
    assert d.causes == ["evaporator overfed — liquid floodback risk"] and d.severity == "fault"


def test_rising_superheat_is_starvation():
    d = diagnose_evaporator_drift([_sh("warn", "up", 2.0)])
    assert d.causes == ["evaporator starved / underfed (undercharge or restricted metering)"]
    assert d.severity == "warn"


def test_falling_suction_is_heat_transfer_loss():
    d = diagnose_evaporator_drift([_sp("fault", "down", -8.0)])
    assert d.causes == ["evaporator heat-transfer loss or low charge"] and d.severity == "fault"


def test_rising_suction_is_overfeed_flooding():
    d = diagnose_evaporator_drift([_sp("warn", "up", 6.0)])
    assert d.causes == ["evaporator overfeed / flooding"] and d.severity == "warn"


# --------------------------------------------------------------------------- isolation invariant


def test_a_condenser_driven_finding_is_not_an_evaporator_cause():
    """The chiller approach rule scores cond + evap in one Finding; a *condenser*-driven fault
    must not be attributed to the evaporator. The evap leg is re-derived from its own drift."""
    finding = _evap(
        sev="fault", drift_f=0.1, drift_sigma=0.4, cond_drift_f=3.0, cond_drift_sigma=5.0
    )
    d = diagnose_evaporator_drift([finding])
    assert d.causes == [] and d.severity == "ok"  # evap leg itself is quiet
    assert d.signals["chiller_approach_drift"]["cause"] is None


# --------------------------------------------------------------------------- corroboration


def test_two_signals_corroborate_and_rank_worst_first():
    d = diagnose_evaporator_drift([_evap("fault", 2.5, 4.0), _sh("warn", "up", 2.0)])
    assert d.corroborated is True
    assert d.severity == "fault"
    assert d.causes == [
        "evaporator tube fouling or scale",  # fault first
        "evaporator starved / underfed (undercharge or restricted metering)",  # warn second
    ]
    assert any("drifting together" in c for c in d.caveats)
    assert "corroborate" in d.summary


def test_a_single_degrading_signal_is_not_corroborated():
    d = diagnose_evaporator_drift([_sp("fault", "down", -8.0), _sh("ok", "down", -0.1)])
    assert d.corroborated is False and d.causes == ["evaporator heat-transfer loss or low charge"]


# --------------------------------------------------------------------------- feed cross-check


def test_superheat_and_suction_agree_on_overfeed():
    """Falling superheat + rising suction both read overfeed -> a strong, specific diagnosis."""
    d = diagnose_evaporator_drift([_sh("fault", "down", -3.0), _sp("fault", "up", 6.0)])
    assert d.corroborated is True
    assert any("agree the evaporator is overfed" in c for c in d.caveats)


def test_superheat_and_suction_agree_on_starvation():
    """Rising superheat + falling suction both read starvation."""
    d = diagnose_evaporator_drift([_sh("fault", "up", 3.0), _sp("fault", "down", -8.0)])
    assert any("agree the evaporator is starved" in c for c in d.caveats)


def test_superheat_and_suction_disagreeing_is_called_ambiguous():
    """Falling superheat (overfed) + falling suction (starved) contradict -> ambiguous."""
    d = diagnose_evaporator_drift([_sh("fault", "down", -3.0), _sp("fault", "down", -8.0)])
    assert any("ambiguous" in c for c in d.caveats)
    assert not any("agree the evaporator" in c for c in d.caveats)


def test_no_feed_cross_check_when_only_one_feed_read_degrades():
    """Superheat degrading but suction quiet -> no agree/disagree caveat (need both)."""
    d = diagnose_evaporator_drift([_sh("fault", "down", -3.0), _sp("ok", "up", 0.1)])
    assert not any("agree the evaporator" in c or "ambiguous" in c for c in d.caveats)


# --------------------------------------------------------------------------- declines / equip


def test_a_declined_signal_becomes_a_caveat_not_a_cause():
    declined = _f(
        "chiller_suction_pressure_drift", "info", declined=True, reason="suction_not_mapped"
    )
    d = diagnose_evaporator_drift([_sh("fault", "up", 3.0), declined])
    assert d.causes == ["evaporator starved / underfed (undercharge or restricted metering)"]
    assert any("suction_not_mapped" in c for c in d.caveats)
    assert d.corroborated is False  # the declined signal doesn't count toward corroboration


def test_equip_is_taken_from_the_findings():
    d = diagnose_evaporator_drift([_sh("fault", "up", 3.0)])
    assert d.equip == "CH_1"


def test_non_evaporator_findings_are_ignored():
    other = _f("cooling_tower_approach_drift", "fault", tower_approach_drift_f=3.0)
    d = diagnose_evaporator_drift([other])
    assert d.causes == [] and d.severity == "ok"  # the tower is a condenser-side signal
