"""Tests for the condenser-loop drift co-movement diagnosis (camber.condenserdrift).

Synthetic Findings stand in for the three condenser-side drift rules; nothing runs the rules or
touches data.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.condenserdrift import CondenserDriftDiagnosis, diagnose_condenser_drift  # noqa: E402
from camber.rules.base import Finding  # noqa: E402


def _f(rule, severity, **metrics):
    return Finding(rule=rule, equip="CH_1", severity=severity, metrics=metrics)


def _cond(sev="fault", drift_f=2.5, drift_sigma=4.0, **extra):
    return _f(
        "chiller_approach_drift", sev, cond_drift_f=drift_f, cond_drift_sigma=drift_sigma, **extra
    )


def _cwr(sev="fault", drift_f=2.5, direction="up"):
    return _f(
        "chiller_cw_range_drift", sev, cw_range_drift_f=drift_f, cw_range_drift_direction=direction
    )


def _tower(sev="warn", drift_f=2.0):
    return _f("cooling_tower_approach_drift", sev, tower_approach_drift_f=drift_f)


# --------------------------------------------------------------------------- empty / steady


def test_no_condenser_findings_is_steady():
    d = diagnose_condenser_drift([])
    assert d.severity == "ok" and d.causes == [] and d.corroborated is False
    assert "steady" in d.summary
    assert isinstance(d, CondenserDriftDiagnosis) and isinstance(d.as_dict(), dict)


def test_all_ok_signals_are_steady():
    d = diagnose_condenser_drift([_cond("ok", 0.1, 0.3), _cwr("ok", 0.1), _tower("ok", 0.1)])
    assert d.severity == "ok" and d.causes == [] and d.corroborated is False


# --------------------------------------------------------------------------- localization


def test_cw_range_widening_is_reduced_flow():
    d = diagnose_condenser_drift([_cwr("fault", 2.5, "up")])
    assert d.severity == "fault"
    assert d.causes == ["reduced condenser-water flow"]
    assert d.corroborated is False


def test_cw_range_narrowing_is_a_bypass():
    d = diagnose_condenser_drift([_cwr("warn", -2.0, "down")])
    assert d.causes == ["condenser-water bypass or short-circuit"] and d.severity == "warn"


def test_tower_widening_is_tower_heat_rejection():
    d = diagnose_condenser_drift([_tower("fault", 3.5)])
    assert d.causes == ["cooling-tower heat rejection degrading"] and d.severity == "fault"


def test_condenser_approach_widening_is_tube_fouling():
    d = diagnose_condenser_drift([_cond("fault", 2.5, 4.0)])
    assert d.causes == ["condenser tube fouling or scale"] and d.severity == "fault"


# --------------------------------------------------------------------------- isolation invariant


def test_an_evaporator_driven_finding_is_not_a_condenser_cause():
    """The chiller approach rule scores cond + evap in one Finding; an *evaporator*-driven fault
    must not be attributed to the condenser. The cond leg is re-derived from its own drift."""
    finding = _cond(
        sev="fault", drift_f=0.1, drift_sigma=0.4, evap_drift_f=3.0, evap_drift_sigma=5.0
    )
    d = diagnose_condenser_drift([finding])
    assert d.causes == [] and d.severity == "ok"  # cond leg itself is quiet
    assert d.signals["chiller_approach_drift"]["cause"] is None


# --------------------------------------------------------------------------- corroboration


def test_two_signals_corroborate_and_rank_worst_first():
    d = diagnose_condenser_drift([_cond("fault", 2.5, 4.0), _tower("warn", 2.0)])
    assert d.corroborated is True
    assert d.severity == "fault"  # worst of the two
    assert d.causes == [
        "condenser tube fouling or scale",  # fault first
        "cooling-tower heat rejection degrading",  # warn second
    ]
    assert any("drifting together" in c for c in d.caveats)
    assert "corroborate" in d.summary


def test_a_single_degrading_signal_is_not_corroborated():
    d = diagnose_condenser_drift([_cond("fault", 2.5, 4.0), _tower("ok", 0.1), _cwr("ok", 0.1)])
    assert d.corroborated is False and d.causes == ["condenser tube fouling or scale"]


# --------------------------------------------------------------------------- declines / equip


def test_a_declined_signal_becomes_a_caveat_not_a_cause():
    declined = _f("chiller_cw_range_drift", "info", declined=True, reason="cw_range_not_mapped")
    d = diagnose_condenser_drift([_tower("fault", 3.5), declined])
    assert d.causes == ["cooling-tower heat rejection degrading"]  # tower only
    assert any("cw_range_not_mapped" in c for c in d.caveats)
    assert d.corroborated is False  # the declined signal doesn't count toward corroboration


def test_equip_is_taken_from_the_findings():
    d = diagnose_condenser_drift([_cwr("fault")])
    assert d.equip == "CH_1"


def test_non_condenser_findings_are_ignored():
    other = _f("chiller_subcooling_drift", "fault", subcooling_drift_f=3.0)
    d = diagnose_condenser_drift([other])
    assert d.causes == [] and d.severity == "ok"  # subcooling isn't a condenser-loop signal
