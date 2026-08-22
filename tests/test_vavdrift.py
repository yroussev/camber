"""Tests for the per-box VAV drift co-movement diagnosis (camber.vavdrift).

Synthetic Findings stand in for the two VAV detectors; nothing runs the rules or touches data.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.rules.base import Finding  # noqa: E402
from camber.vavdrift import VavDriftDiagnosis, diagnose_vav_drift  # noqa: E402


def _f(rule, severity, *, summary="", **metrics):
    return Finding(rule=rule, equip="VAV_1", severity=severity, metrics=metrics, summary=summary)


def _damper(sev="fault", pct=18.0, direction="up", starved=False):
    m = dict(
        vav_airflow_drift_pct=pct,
        vav_airflow_drift_direction=direction,
        vav_airflow_which="damper_authority",
    )
    if starved:
        m["vav_upstream_starvation_suspected"] = True
    return _f("vav_airflow_drift", sev, **m)


def _reheat(sev="fault", pct=18.0, water_shift=None):
    m = dict(
        vav_reheat_valve_drift_pct=pct,
        vav_reheat_valve_drift_direction="up",
        vav_reheat_which="reheat",
    )
    if water_shift is not None:
        m["water_supply_shift_f"] = water_shift
    return _f("vav_reheat_valve_drift", sev, **m)


# --------------------------------------------------------------------------- steady


def test_no_findings_is_steady():
    d = diagnose_vav_drift([])
    assert d.severity == "ok" and d.locus == "steady" and d.box_wide is False
    assert d.causes == [] and "steady" in d.summary
    assert isinstance(d, VavDriftDiagnosis) and isinstance(d.as_dict(), dict)


# --------------------------------------------------------------------------- single-signal loci


def test_damper_creep_alone_is_airflow():
    d = diagnose_vav_drift([_damper("fault")])
    assert d.locus == "airflow" and d.box_wide is False
    assert any("box damper authority" in c for c in d.causes)
    assert not any("plant-side starvation" in c for c in d.caveats)


def test_damper_creep_with_starvation_is_upstream():
    d = diagnose_vav_drift([_damper("fault", starved=True)])
    assert d.locus == "upstream" and d.box_wide is False
    assert any("upstream duct-static starvation" in c for c in d.causes)
    assert any("check the AHU before servicing the box" in c for c in d.caveats)


def test_reheat_creep_alone_is_reheat():
    d = diagnose_vav_drift([_reheat("fault")])
    assert d.locus == "reheat"
    assert any("reheat coil fouling" in c for c in d.causes)


def test_reheat_with_hw_reset_is_caveated():
    d = diagnose_vav_drift([_reheat("fault", water_shift=-3.0)])
    assert d.locus == "reheat"
    assert any("waterside-reset effect" in c for c in d.caveats)


def test_reheat_with_hw_rise_is_not_caveated():
    d = diagnose_vav_drift([_reheat("fault", water_shift=3.0)])
    assert not any("waterside-reset" in c for c in d.caveats)


# ------------------------------------------------------------------- box-wide / upstream mix


def test_airflow_and_reheat_is_box_wide_and_corroborated():
    d = diagnose_vav_drift([_damper("fault"), _reheat("warn")])
    assert d.box_wide is True and d.locus == "box-wide" and d.corroborated is True
    assert d.severity == "fault"  # worst of the two, tier not inflated by corroboration
    assert any("box-wide cause" in c for c in d.caveats)


def test_upstream_and_reheat_is_reheat_not_box_wide():
    """A plant symptom + a box fault: two different problems, not a box-wide verdict."""
    d = diagnose_vav_drift([_damper("fault", starved=True), _reheat("fault")])
    assert d.locus == "reheat" and d.box_wide is False and d.corroborated is True
    assert any("check the AHU" in c for c in d.caveats)
    assert any("two different problems" in c for c in d.caveats)


def test_causes_rank_worst_first():
    d = diagnose_vav_drift([_damper("warn"), _reheat("fault")])
    assert d.causes[0].startswith("reheat coil")  # the fault outranks the warn


# --------------------------------------------------------------------------- declines / ignores


def test_ok_damper_is_a_signal_not_a_cause():
    d = diagnose_vav_drift([_damper("ok")])
    assert d.locus == "steady" and d.causes == []
    assert "vav_airflow_drift" in d.signals
    assert d.signals["vav_airflow_drift"]["cause"] is None


def test_declined_damper_is_a_caveat_not_a_cause():
    declined = _f("vav_airflow_drift", "info", declined=True, reason="vav_damper_inputs_not_mapped")
    d = diagnose_vav_drift([declined, _reheat("fault")])
    assert d.locus == "reheat" and d.corroborated is False
    assert any("vav_damper_inputs_not_mapped" in c for c in d.caveats)


def test_declined_reheat_is_a_caveat_not_a_cause():
    declined = _f(
        "vav_reheat_valve_drift", "info", declined=True, reason="vav_reheat_valve_inputs_not_mapped"
    )
    d = diagnose_vav_drift([_damper("fault"), declined])
    assert d.locus == "airflow" and d.corroborated is False
    assert any("vav_reheat_valve_inputs_not_mapped" in c for c in d.caveats)


def test_non_vav_findings_are_ignored():
    other = _f("chiller_head_pressure_drift", "fault", head_pressure_drift_psi=8.0)
    d = diagnose_vav_drift([other])
    assert d.causes == [] and d.severity == "ok" and d.locus == "steady"


def test_equip_is_taken_from_the_findings():
    d = diagnose_vav_drift([_damper("fault")])
    assert d.equip == "VAV_1"
    d2 = diagnose_vav_drift([_damper("fault")], equip="VAV_9")
    assert d2.equip == "VAV_9"
