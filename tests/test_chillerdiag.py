"""Tests for the whole-machine chiller drift roll-up (camber.chillerdiag).

Synthetic Findings stand in for the underlying drift rules; the roll-up composes the two side
diagnoses and the charge signal. Nothing runs the rules or touches data.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.chillerdiag import ChillerDriftDiagnosis, diagnose_chiller_drift  # noqa: E402
from camber.rules.base import Finding  # noqa: E402


def _f(rule, severity, **metrics):
    return Finding(rule=rule, equip="CH_1", severity=severity, metrics=metrics)


# --- condenser-side signals ---
def _hp(sev="fault", drift_psi=8.0):  # head / condensing pressure
    return _f("chiller_head_pressure_drift", sev, head_pressure_drift_psi=drift_psi)


def _tower(sev="fault", drift_f=3.5):
    return _f("cooling_tower_approach_drift", sev, tower_approach_drift_f=drift_f)


# --- evaporator-side signals ---
def _sh(sev="fault", direction="up", drift_f=3.0):  # superheat
    return _f(
        "chiller_superheat_drift",
        sev,
        superheat_drift_direction=direction,
        superheat_drift_f=drift_f,
    )


def _sp(sev="fault", direction="down", drift_psi=-8.0):  # suction pressure
    return _f(
        "chiller_suction_pressure_drift",
        sev,
        suction_pressure_drift_direction=direction,
        suction_pressure_drift_psi=drift_psi,
    )


# --- charge signal ---
def _sub(sev="fault", direction="down", drift_f=-2.5):  # subcooling
    return _f(
        "chiller_subcooling_drift",
        sev,
        subcooling_drift_direction=direction,
        subcooling_drift_f=drift_f,
    )


# --------------------------------------------------------------------------- steady


def test_no_findings_is_steady():
    d = diagnose_chiller_drift([])
    assert d.severity == "ok" and d.locus == "steady" and d.machine_wide is False
    assert d.causes == [] and "steady" in d.summary
    assert isinstance(d, ChillerDriftDiagnosis) and isinstance(d.as_dict(), dict)


def test_the_side_diagnoses_are_nested_and_serializable():
    d = diagnose_chiller_drift([_hp("fault", 8.0)])
    dd = d.as_dict()
    assert isinstance(dd["condenser"], dict) and isinstance(dd["evaporator"], dict)
    assert dd["condenser"]["severity"] == "fault"


# --------------------------------------------------------------------------- one side only


def test_condenser_only_localizes_to_the_condenser():
    d = diagnose_chiller_drift([_hp("fault", 8.0)])
    assert d.locus == "condenser" and d.machine_wide is False and d.severity == "fault"
    assert d.causes == [
        "condenser: condenser high-side pressure rising (fouling / non-condensables)"
    ]


def test_evaporator_only_localizes_to_the_evaporator():
    d = diagnose_chiller_drift([_sh("fault", "up", 3.0)])
    assert d.locus == "evaporator" and d.machine_wide is False and d.severity == "fault"
    assert d.causes[0].startswith("evaporator: ")


# ------------------------------------------------------------------- both sides -> machine-wide


def test_both_sides_drifting_is_a_whole_machine_verdict():
    d = diagnose_chiller_drift([_hp("fault", 8.0), _sh("fault", "up", 3.0)])
    assert d.machine_wide is True and d.locus == "whole-machine"
    assert d.severity == "fault"
    assert any("circuit-wide" in c for c in d.caveats)
    assert "gauge the whole machine" in d.summary


def test_machine_wide_with_subcooling_corroborates_a_charge_problem():
    d = diagnose_chiller_drift(
        [_hp("fault", 8.0), _sh("fault", "up", 3.0), _sub("warn", "up", 2.5)]
    )
    assert d.machine_wide is True
    assert d.charge is not None and "non-condensables" in d.charge["cause"]
    assert any("corroborating a charge" in c for c in d.caveats)


def test_worst_side_ranks_first_in_the_combined_causes():
    d = diagnose_chiller_drift([_hp("warn", 3.0), _sh("fault", "up", 3.0)])
    assert d.severity == "fault" and d.machine_wide is True
    assert d.causes[0].startswith("evaporator: ")  # the fault side outranks the warn side
    assert d.causes[-1].startswith("condenser: ")


# --------------------------------------------------------------------------- charge signal


def test_charge_only_localizes_to_charge():
    d = diagnose_chiller_drift([_sub("fault", "down", -2.5)])
    assert d.locus == "charge" and d.machine_wide is False and d.severity == "fault"
    assert d.causes == ["charge: refrigerant undercharge or leak"]
    assert any("barely moves an approach" in c for c in d.caveats)


def test_charge_plus_one_side_is_read_together_with_that_side():
    d = diagnose_chiller_drift([_hp("fault", 8.0), _sub("warn", "up", 2.5)])
    assert d.locus == "condenser" and d.machine_wide is False
    assert any("alongside a one-sided condenser signal" in c for c in d.caveats)


def test_a_declined_subcooling_is_a_caveat_not_a_charge_cause():
    declined = _f("chiller_subcooling_drift", "info", declined=True, reason="subcooling_not_mapped")
    d = diagnose_chiller_drift([_hp("fault", 8.0), declined])
    assert d.charge is None
    assert any("subcooling_not_mapped" in c for c in d.caveats)


# --------------------------------------------------------------------------- severity / equip


def test_severity_is_the_worst_across_both_sides_and_charge():
    d = diagnose_chiller_drift([_tower("warn", 2.0), _sp("fault", "down", -8.0)])
    assert d.severity == "fault"  # evaporator fault beats condenser warn


def test_equip_is_taken_from_the_findings():
    d = diagnose_chiller_drift([_sh("fault", "up", 3.0)])
    assert d.equip == "CH_1"
