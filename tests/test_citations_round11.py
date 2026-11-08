"""Round 11 of the citation checker (PLAN-CITE-EVAL section 6).

1. The two round-10 host escapes closed in the facts: a diagnostic evidence chart's fact names
   its axes, and the sensor_trust summary names the role or equipment filter of the call.
"""

from __future__ import annotations

from types import SimpleNamespace

from camber.capabilities._cap_rule_run import _chart
from camber.capabilities._cap_sensor_trust import _selection_words


def test_diagnostic_chart_fact_names_its_axes():
    ev = SimpleNamespace(
        roles=[],
        template=SimpleNamespace(name="economizer lockout", x="oat", y="oa_fraction_pct"),
        renderer="diagnostic",
        title="",
        label="",
    )
    out = _chart(ev)
    assert out["roles"] == ["oat", "oa_fraction_pct"]
    assert out["description"] == (
        "A diagnostic scatter (economizer lockout) of oa_fraction_pct (outdoor-air fraction, y) "
        "against oat (outdoor-air temperature, x), with the violating samples marked"
    )


def test_diagnostic_chart_axis_without_words_is_its_role():
    ev = SimpleNamespace(
        roles=["heat_valve", "cool_valve"],
        template=SimpleNamespace(name="no_simultaneous_hc", x="heat_valve", y="cool_valve"),
        renderer="diagnostic",
        title="",
        label="",
    )
    assert (
        "of cool_valve (cool valve, y) against heat_valve (heat valve, x),"
        in (_chart(ev)["description"])
    )


def test_trust_summary_says_the_selection():
    assert _selection_words(None, None) == "points"
    assert _selection_words(None, ["supply_air_temp"]) == "supply_air_temp points"
    assert _selection_words(["AHU-1"], None) == "points on AHU-1"
    assert (
        _selection_words(["AHU-1", "AHU-2"], ["supply_air_temp", "mixed_air_temp"])
        == "supply_air_temp or mixed_air_temp points on AHU-1, AHU-2"
    )
