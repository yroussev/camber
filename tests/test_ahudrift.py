"""Tests for the per-AHU air-side drift co-movement diagnosis (camber.ahudrift).

Synthetic Findings stand in for the four AHU detectors; nothing runs the rules or touches data.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.ahudrift import AhuDriftDiagnosis, diagnose_ahu_drift  # noqa: E402
from camber.rules.base import Finding  # noqa: E402


def _f(rule, severity, *, summary="", **metrics):
    return Finding(rule=rule, equip="AHU_1", severity=severity, metrics=metrics, summary=summary)


def _fan(sev="fault", kw=1.5):
    return _f("fan_efficiency_drift", sev, fan_power_drift_kw=kw, fan_power_drift_direction="up")


def _filter(sev="fault", inwc=0.4):
    return _f(
        "filter_loading_drift", sev, filter_dp_drift_inwc=inwc, filter_dp_drift_direction="up"
    )


def _static(sev="fault", direction="up", inwc=0.4):
    return _f(
        "duct_static_drift", sev, duct_static_drift_direction=direction, duct_static_drift_inwc=inwc
    )


def _coil(sev="fault", which="cooling", pct=16.0):
    summary = f"AHU_1: {which}-coil valve creep"
    return _f(
        "coil_valve_drift",
        sev,
        summary=summary,
        coil_valve_which=which,
        coil_valve_drift_pct=pct,
        coil_valve_drift_direction="up",
    )


# --------------------------------------------------------------------------- steady


def test_no_findings_is_steady():
    d = diagnose_ahu_drift([])
    assert d.severity == "ok" and d.locus == "steady" and d.ahu_wide is False
    assert d.causes == [] and "steady" in d.summary
    assert isinstance(d, AhuDriftDiagnosis) and isinstance(d.as_dict(), dict)


# --------------------------------------------------------------------------- the fan disambiguation


def test_fan_power_alone_is_ambiguous_and_lands_on_the_fan():
    d = diagnose_ahu_drift([_fan("fault")])  # Case D: nothing to disambiguate with
    assert d.locus == "fan"
    assert any("no filter or duct-static point is mapped" in c for c in d.caveats)


def test_fan_power_with_clean_filter_and_steady_static_isolates_the_fan():
    d = diagnose_ahu_drift([_fan("fault"), _filter("ok"), _static("ok", "up", 0.0)])
    assert d.locus == "fan"
    assert any("isolates" in c and "fan itself" in c for c in d.caveats)


def test_fan_power_with_a_loading_filter_is_the_air_path():
    d = diagnose_ahu_drift([_fan("fault"), _filter("warn")])  # Case A
    assert d.locus == "air-path" and d.corroborated is True
    assert any("fix the air path before the fan" in c for c in d.caveats)


def test_fan_power_with_rising_static_is_the_air_path():
    d = diagnose_ahu_drift([_fan("fault"), _static("warn", "up")])  # Case A via static up
    assert d.locus == "air-path" and d.corroborated is True


def test_static_down_with_fan_power_is_fan_degradation():
    d = diagnose_ahu_drift([_fan("fault"), _static("fault", "down")])  # Case B
    assert d.locus == "fan" and d.corroborated is True
    assert any("losing static" in c for c in d.caveats)


# --------------------------------------------------------------------------- localization


def test_filter_loading_alone_is_the_air_path():
    d = diagnose_ahu_drift([_filter("fault")])
    assert d.locus == "air-path"
    assert any("air filter loading" in c for c in d.causes)


def test_static_up_alone_is_over_pressurization():
    d = diagnose_ahu_drift([_static("fault", "up")])
    assert d.locus == "air-path"
    assert any("over-pressurization" in c for c in d.causes)


def test_static_down_alone_is_the_fan():
    d = diagnose_ahu_drift([_static("fault", "down")])
    assert d.locus == "fan"
    assert any("cannot hold duct-static setpoint" in c for c in d.causes)


def test_cooling_coil_creep_is_the_coil():
    d = diagnose_ahu_drift([_coil("fault", "cooling")])
    assert d.locus == "coil"
    assert any("cooling-coil" in c for c in d.causes)


def test_two_coils_are_named_separately():
    d = diagnose_ahu_drift([_coil("fault", "cooling"), _coil("warn", "heating")])
    assert d.locus == "coil"  # both on the coil side -> not ahu-wide
    assert "coil_valve_drift:cooling" in d.signals and "coil_valve_drift:heating" in d.signals
    assert any("cooling-coil" in c for c in d.causes)
    assert any("heating-coil" in c for c in d.causes)


def test_two_coils_fall_back_to_the_summary_token():
    """A coil finding without coil_valve_which still names the coil from its summary."""
    cool = _f(
        "coil_valve_drift",
        "fault",
        summary="AHU_1: cooling-coil valve creep",
        coil_valve_drift_pct=16.0,
        coil_valve_drift_direction="up",
    )
    d = diagnose_ahu_drift([cool])
    assert "coil_valve_drift:cooling" in d.signals
    assert any("cooling-coil" in c for c in d.causes)


# --------------------------------------------------------------------------- ahu-wide / ranking


def test_ahu_wide_when_two_sides_degrade():
    d = diagnose_ahu_drift([_filter("fault"), _coil("warn", "cooling")])
    assert d.ahu_wide is True and d.locus == "ahu-wide"
    assert d.severity == "fault"  # worst of the two, tier not inflated by corroboration
    assert any("AHU-wide cause is more likely" in c for c in d.caveats)


def test_causes_rank_worst_first():
    d = diagnose_ahu_drift([_filter("warn"), _coil("fault", "cooling")])
    assert d.causes[0].startswith("cooling-coil")  # the fault outranks the warn


# --------------------------------------------------------------------------- declines / ignores


def test_a_declined_signal_is_a_caveat_not_a_cause():
    declined = _f(
        "filter_loading_drift", "info", declined=True, reason="filter_dp_or_airflow_not_mapped"
    )
    d = diagnose_ahu_drift([_coil("fault", "cooling"), declined])
    assert d.locus == "coil"
    assert any("filter_dp_or_airflow_not_mapped" in c for c in d.caveats)
    assert d.corroborated is False


def test_declined_filter_and_static_make_fan_power_ambiguous():
    declined_f = _f("filter_loading_drift", "info", declined=True, reason="x")
    declined_s = _f("duct_static_drift", "info", declined=True, reason="y")
    d = diagnose_ahu_drift([_fan("fault"), declined_f, declined_s])
    assert d.locus == "fan"
    assert any("no filter or duct-static point is mapped" in c for c in d.caveats)


def test_non_ahu_findings_are_ignored():
    other = _f("chiller_head_pressure_drift", "fault", head_pressure_drift_psi=8.0)
    d = diagnose_ahu_drift([other])
    assert d.causes == [] and d.severity == "ok" and d.locus == "steady"


def test_equip_is_taken_from_the_findings():
    d = diagnose_ahu_drift([_fan("fault")])
    assert d.equip == "AHU_1"
