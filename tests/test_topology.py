"""Tests for the served-by topology graph (camber.model.topology)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model import Topology as TopologyReexport  # noqa: E402
from camber.model.entities import Site  # noqa: E402
from camber.model.topology import Topology  # noqa: E402


def _plant_ahu_zone():
    # CHW -> AHU-1 -> {VAV-1, VAV-2}; CHW -> AHU-2 -> VAV-3
    return Topology.from_parent_map(
        {"VAV-1": "AHU-1", "VAV-2": "AHU-1", "VAV-3": "AHU-2", "AHU-1": "CHW", "AHU-2": "CHW"}
    )


def test_from_edges_and_neighbours():
    t = Topology.from_edges([("AHU-1", "VAV-1"), ("AHU-1", "VAV-2")])
    assert t.children_of("AHU-1") == ("VAV-1", "VAV-2")
    assert t.parents_of("VAV-1") == ("AHU-1",)
    assert t.children_of("VAV-1") == ()  # leaf
    assert t.parents_of("AHU-1") == ()  # root


def test_dedup_and_self_loops_dropped():
    t = Topology.from_edges([("a", "b"), ("a", "b"), ("x", "x")])
    assert t.edges == (("a", "b"),)  # duplicate collapsed, self-loop removed


def test_from_parent_map_inverts():
    t = Topology.from_parent_map({"VAV-1": "AHU-1"})
    assert t.edges == (("AHU-1", "VAV-1"),)


def test_transitive_descendants_and_ancestors():
    t = _plant_ahu_zone()
    assert t.descendants("CHW") == frozenset({"AHU-1", "AHU-2", "VAV-1", "VAV-2", "VAV-3"})
    assert t.ancestors("VAV-1") == frozenset({"AHU-1", "CHW"})
    assert t.descendants("VAV-1") == frozenset()


def test_roots_and_leaves():
    t = _plant_ahu_zone()
    assert t.roots() == ("CHW",)
    assert t.leaves() == ("VAV-1", "VAV-2", "VAV-3")


def test_zones_of_returns_leaf_descendants():
    t = _plant_ahu_zone()
    assert t.zones_of("AHU-1") == ("VAV-1", "VAV-2")
    assert t.zones_of("CHW") == ("VAV-1", "VAV-2", "VAV-3")  # transitive, leaves only
    assert t.zones_of("VAV-1") == ()  # a leaf serves nothing


def test_group_of_default_single_parent():
    t = _plant_ahu_zone()
    assert t.group_of("VAV-1") == "AHU-1"
    assert t.group_of("CHW") is None  # a root has no group


def test_group_of_with_predicate_walks_up():
    # a reheat terminal below a VAV below an AHU: nearest AHU ancestor is AHU-1
    t = Topology.from_parent_map({"RH-1": "VAV-1", "VAV-1": "AHU-1", "AHU-1": "CHW"})
    assert t.group_of("RH-1", pred=lambda e: e.startswith("AHU")) == "AHU-1"
    assert t.nearest_ancestor("RH-1", lambda e: e == "CHW") == "CHW"
    assert t.nearest_ancestor("RH-1", lambda e: e == "NOPE") is None


def test_group_map_omits_ungrouped():
    t = _plant_ahu_zone()
    gm = t.group_map(["VAV-1", "VAV-2", "VAV-3", "ORPHAN"])
    assert gm == {"VAV-1": "AHU-1", "VAV-2": "AHU-1", "VAV-3": "AHU-2"}  # ORPHAN omitted


def test_unknown_ids_return_empty():
    t = _plant_ahu_zone()
    assert t.children_of("NOPE") == ()
    assert t.parents_of("NOPE") == ()
    assert t.descendants("NOPE") == frozenset()
    assert t.group_of("NOPE") is None


def test_cycle_is_broken_into_dag_without_hanging():
    t = Topology.from_edges([("a", "b"), ("b", "c"), ("c", "a")])
    assert ("c", "a") in t.dropped_cycle_edges
    assert t.edges == (("a", "b"), ("b", "c"))
    # queries still terminate on the (former) cycle
    assert t.descendants("a") == frozenset({"b", "c"})


def test_self_referential_and_partial_graph_safe():
    # a graph covering only some zones -> group_map has only the covered ones
    t = Topology.from_parent_map({"VAV-1": "AHU-1"})
    assert t.group_map(["VAV-1", "VAV-2", "VAV-3"]) == {"VAV-1": "AHU-1"}


def test_provenance_roundtrips():
    assert Topology().provenance == "explicit"
    assert Topology.from_edges([("a", "b")], provenance="semantic").provenance == "semantic"
    assert Topology.from_parent_map({"z": "a"}, provenance="heuristic").provenance == "heuristic"


def test_empty_topology_all_queries_empty():
    t = Topology()
    assert t.edges == ()
    assert t.roots() == () and t.leaves() == ()
    assert t.zones_of("anything") == ()
    assert t.group_map(["a", "b"]) == {}


def test_equality_and_hash_by_value():
    a = Topology.from_parent_map({"z": "p"})
    b = Topology.from_parent_map({"z": "p"})
    assert a == b
    assert hash(a) == hash(b)  # frozen + value-equal -> usable as a dict key / in a set


def test_site_carries_topology_with_empty_default():
    assert Site(id="S").topology.edges == ()  # defaulted, no churn to existing constructors
    t = _plant_ahu_zone()
    s = Site(id="S", topology=t)
    assert Topology.from_site(s) is t
    assert Topology.from_site(Site(id="bare")).edges == ()  # safe accessor on an empty site


def test_diamond_graph_dedups_visits():
    # ROOT -> A -> {B, C} -> D: D and (searching higher) A are each reached two ways -> visited once
    t = Topology.from_edges(
        [("ROOT", "A"), ("A", "B"), ("A", "C"), ("B", "D"), ("C", "D")]
    )
    assert t.descendants("A") == frozenset({"B", "C", "D"})
    assert t.ancestors("D") == frozenset({"A", "B", "C", "ROOT"})
    assert t.nearest_ancestor("D", lambda e: e == "A") == "A"  # BFS dedups the two paths to A
    # searching past the convergence forces A onto the frontier twice -> the visited-dedup branch
    assert t.nearest_ancestor("D", lambda e: e == "ROOT") == "ROOT"
    assert t.zones_of("ROOT") == ("D",)  # single leaf despite two paths


def test_reexport_is_the_same_class():
    assert TopologyReexport is Topology
