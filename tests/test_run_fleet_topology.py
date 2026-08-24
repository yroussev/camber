"""Tests for run_fleet threading a served-by topology to grouping-aware fleet rules (0.60.0)."""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camber.rules.base as base  # noqa: E402
from camber.model.topology import Topology  # noqa: E402
from camber.resolve import EquipRef  # noqa: E402
from camber.rules.base import Finding, Registry, _heuristic_topology  # noqa: E402

_REFS = [
    EquipRef(equip="AHU_1", equip_class="AHU", folder="f"),
    EquipRef(equip="AHU_1_VAV_1", equip_class="VAV", folder="f"),
    EquipRef(equip="AHU_1_VAV_2", equip_class="VAV", folder="f"),
]


class _RecordingFleet:
    name = "recording"
    roles_required = ()
    roles_optional = ()
    wants_topology = True

    def __init__(self):
        self.seen = "UNSET"

    def analyze_fleet(self, frames, *, topology=None):
        self.seen = topology
        return Finding(rule=self.name, equip="<fleet>", severity="ok", summary="ok")


class _TopologyIndifferent(_RecordingFleet):
    name = "indifferent"
    wants_topology = False


def _reg_with(rule, monkeypatch):
    monkeypatch.setattr(
        base,
        "resolve",
        lambda ref, mapping, load, resample="1h": pd.DataFrame(
            {"x": [1.0]}, index=pd.date_range("2025-01-01", periods=1, freq="1h")
        ),
    )
    reg = Registry()
    reg.register(rule)
    return reg


def test_heuristic_topology_from_refs():
    t = _heuristic_topology(_REFS)
    assert set(t.edges) == {("AHU_1", "AHU_1_VAV_1"), ("AHU_1", "AHU_1_VAV_2")}
    assert t.provenance == "heuristic"


def test_explicit_topology_forwarded_verbatim(monkeypatch):
    rule = _RecordingFleet()
    reg = _reg_with(rule, monkeypatch)
    topo = Topology.from_parent_map({"a": "b"}, provenance="semantic")
    reg.run_fleet("recording", _REFS, None, topology=topo)
    assert rule.seen is topo  # passed through unchanged


def test_auto_builds_heuristic_when_none_and_rule_wants_it(monkeypatch):
    rule = _RecordingFleet()
    reg = _reg_with(rule, monkeypatch)
    reg.run_fleet("recording", _REFS, None)  # no explicit topology
    assert rule.seen is not None
    assert rule.seen.provenance == "heuristic"
    assert set(rule.seen.edges) == {("AHU_1", "AHU_1_VAV_1"), ("AHU_1", "AHU_1_VAV_2")}


def test_no_autobuild_when_rule_indifferent(monkeypatch):
    rule = _TopologyIndifferent()
    reg = _reg_with(rule, monkeypatch)
    reg.run_fleet("indifferent", _REFS, None)  # no wasted naming pass
    assert rule.seen is None


def test_end_to_end_census_auto_scopes_via_run_fleet(monkeypatch):
    # the real rogue census, run through run_fleet, auto-scopes on the heuristic naming grouping
    import numpy as np

    from camber.model.roles import Role
    from camber.rules.builtin import builtin_registry

    idx = pd.date_range("2025-07-07", periods=21 * 24, freq="1h")
    hot = (idx.hour >= 10) & (idx.hour < 17)

    def frame_for(equip):
        cool = np.full(len(idx), 74.0)
        over = 6.0 if equip.endswith("VAV_1") else 0.0  # AHU_1_VAV_1 is the rogue
        return pd.DataFrame(
            {Role.SPACE_TEMP: cool + np.where(hot, over, 0.0), Role.COOL_SP: cool}, index=idx
        )

    monkeypatch.setattr(
        base, "resolve", lambda ref, mapping, load, resample="1h": frame_for(ref.equip)
    )
    refs = [
        EquipRef(equip="AHU_1", equip_class="AHU", folder="f"),
        EquipRef(equip="AHU_1_VAV_1", equip_class="VAV", folder="f"),
        EquipRef(equip="AHU_1_VAV_2", equip_class="VAV", folder="f"),
    ]
    f = builtin_registry().run_fleet("sat_rogue_zone_census", refs, None)
    assert f.metrics["grouped"] is True
    assert f.metrics["grouping_provenance"] == "heuristic"
    assert any("inferred from equipment naming" in c for c in f.caveats)
