"""Tests for the per-loop pump/hydronic drift co-movement diagnosis (camber.pumpdrift).

Synthetic Findings stand in for the four detectors; nothing runs the rules or touches data.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.pumpdrift import PumpDriftDiagnosis, diagnose_pump_drift  # noqa: E402
from camber.rules.base import Finding  # noqa: E402


def _f(rule, severity, **metrics):
    return Finding(rule=rule, equip="P_1", severity=severity, metrics=metrics)


def _flow(sev="fault", gpm=-70.0):
    return _f("pump_flow_drift", sev, pump_flow_drift_gpm=gpm)


def _head(sev="fault", psi=-8.0):
    return _f("pump_head_drift", sev, pump_head_drift_psi=psi)


def _deltat(sev="fault", direction="down", drift_f=-3.0):
    return _f(
        "loop_deltat_drift", sev, loop_deltat_drift_direction=direction, loop_deltat_drift_f=drift_f
    )


def _dp(sev="fault", direction="up", drift=8.0):
    return _f("loop_dp_drift", sev, loop_dp_drift_direction=direction, loop_dp_drift=drift)


# --------------------------------------------------------------------------- steady


def test_no_findings_is_steady():
    d = diagnose_pump_drift([])
    assert d.severity == "ok" and d.locus == "steady" and d.loop_wide is False
    assert d.causes == [] and "steady" in d.summary
    assert isinstance(d, PumpDriftDiagnosis) and isinstance(d.as_dict(), dict)


# --------------------------------------------------------------------------- the disambiguation


def test_flow_and_head_both_down_is_the_pump():
    d = diagnose_pump_drift([_flow("fault"), _head("fault")])
    assert d.locus == "pump" and d.severity == "fault"
    assert any("pump wear" in c for c in d.causes)
    assert d.corroborated is True
    assert any("the pump itself, corroborated" in c for c in d.caveats)


def test_flow_down_with_steady_head_is_the_distribution():
    """The headline: a flow deficit with steady head points at system resistance, not the pump."""
    d = diagnose_pump_drift([_flow("fault"), _head("ok", psi=0.0)])
    assert d.locus == "distribution"
    assert any("system resistance" in c for c in d.causes)
    assert any("check the distribution, not the impeller" in c for c in d.caveats)


def test_flow_deficit_with_no_head_point_is_ambiguous():
    d = diagnose_pump_drift([_flow("fault")])  # no head finding at all
    assert any("pump wear or added system resistance" in c for c in d.causes)
    assert any("no pump-head point is mapped to disambiguate" in c for c in d.caveats)


def test_a_declined_head_is_treated_as_unmapped_for_disambiguation():
    declined = _f("pump_head_drift", "info", declined=True, reason="pump_head_not_mapped")
    d = diagnose_pump_drift([_flow("fault"), declined])
    assert any("no pump-head point is mapped" in c for c in d.caveats)
    assert any("pump_head_not_mapped" in c for c in d.caveats)


# --------------------------------------------------------------------------- localization


def test_head_deficit_alone_is_the_pump():
    d = diagnose_pump_drift([_head("fault")])
    assert d.locus == "pump" and d.causes == ["pump head deficit (worn impeller / cavitation)"]


def test_low_delta_t_is_the_distribution():
    d = diagnose_pump_drift([_deltat("fault", "down")])
    assert d.locus == "distribution"
    assert any("low-ΔT syndrome" in c for c in d.causes)


def test_rising_dp_is_the_distribution():
    d = diagnose_pump_drift([_dp("fault", "up")])
    assert d.locus == "distribution"
    assert any("rising system resistance" in c for c in d.causes)


# --------------------------------------------------------------------------- loop-wide


def test_pump_and_distribution_together_is_loop_wide():
    d = diagnose_pump_drift([_head("fault"), _dp("warn", "up")])
    assert d.loop_wide is True and d.locus == "loop-wide"
    assert d.severity == "fault"  # worst side
    assert any("loop-wide cause is more likely" in c for c in d.caveats)


def test_causes_rank_worst_first():
    d = diagnose_pump_drift([_head("warn"), _deltat("fault", "down")])
    assert d.causes[0].startswith("low-ΔT")  # the fault outranks the warn
    assert d.corroborated is True


# --------------------------------------------------------------------------- declines / ignores


def test_a_declined_signal_is_a_caveat_not_a_cause():
    declined = _f("loop_dp_drift", "info", declined=True, reason="dp_or_flow_not_mapped")
    d = diagnose_pump_drift([_deltat("fault", "up"), declined])
    assert any("underflow / starvation" in c for c in d.causes)
    assert any("dp_or_flow_not_mapped" in c for c in d.caveats)
    assert d.corroborated is False


def test_non_pump_findings_are_ignored():
    other = _f("chiller_head_pressure_drift", "fault", head_pressure_drift_psi=8.0)
    d = diagnose_pump_drift([other])
    assert d.causes == [] and d.severity == "ok" and d.locus == "steady"


def test_equip_is_taken_from_the_findings():
    d = diagnose_pump_drift([_head("fault")])
    assert d.equip == "P_1"
