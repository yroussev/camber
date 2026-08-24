"""Tests for served-by Topology extracted from a Brick model (feeds / isFedBy)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.interop.brick import topology_from_brick  # noqa: E402
from camber.interop.site_model import site_from_ttl  # noqa: E402
from camber.model.topology import Topology  # noqa: E402

_PREFIX = """@prefix brick: <https://brickschema.org/schema/Brick#> .
@prefix bldg: <urn:bldg#> .
"""

_FEEDS = (
    _PREFIX
    + """
bldg:AHU_1 a brick:AHU ; brick:feeds bldg:VAV_1, bldg:VAV_2 .
bldg:CHW  a brick:Chilled_Water_System ; brick:feeds bldg:AHU_1 .
"""
)

_FEDBY = _PREFIX + "bldg:VAV_3 a brick:VAV ; brick:isFedBy bldg:AHU_2 .\n"

_MIXED = (
    _PREFIX
    + """
bldg:AHU_1 a brick:AHU ; brick:feeds bldg:VAV_1 .
bldg:VAV_2 a brick:VAV ; brick:isFedBy bldg:AHU_1 .
"""
)


def test_feeds_edges_minimal():
    t = topology_from_brick(_FEEDS, backend="minimal")
    assert t.provenance == "semantic"
    assert set(t.edges) == {("AHU_1", "VAV_1"), ("AHU_1", "VAV_2"), ("CHW", "AHU_1")}
    assert t.zones_of("CHW") == ("VAV_1", "VAV_2")  # transitive


def test_isfedby_inverts_direction():
    t = topology_from_brick(_FEDBY, backend="minimal")
    assert set(t.edges) == {("AHU_2", "VAV_3")}  # parent->child regardless of relation direction


def test_feeds_and_isfedby_merge():
    t = topology_from_brick(_MIXED, backend="minimal")
    assert set(t.edges) == {("AHU_1", "VAV_1"), ("AHU_1", "VAV_2")}
    assert t.group_map(["VAV_1", "VAV_2"]) == {"VAV_1": "AHU_1", "VAV_2": "AHU_1"}


def test_no_flow_relations_is_empty():
    t = topology_from_brick(_PREFIX + "bldg:AHU_1 a brick:AHU .", backend="minimal")
    assert t.edges == ()
    assert t.provenance == "semantic"


def test_cyclic_feeds_broken_into_dag():
    ttl = _PREFIX + "bldg:A brick:feeds bldg:B . bldg:B brick:feeds bldg:A .\n"
    t = topology_from_brick(ttl, backend="minimal")
    assert len(t.dropped_cycle_edges) == 1  # one back-edge removed, no hang
    assert ("A", "B") in t.edges or ("B", "A") in t.edges


def test_rdflib_matches_minimal():
    pytest.importorskip("rdflib")
    a = topology_from_brick(_FEEDS, backend="rdflib")
    b = topology_from_brick(_FEEDS, backend="minimal")
    assert set(a.edges) == set(b.edges)
    assert a.provenance == b.provenance == "semantic"


def test_malformed_ttl_raises_valueerror():
    pytest.importorskip("rdflib")
    with pytest.raises(ValueError):
        topology_from_brick("bldg:X brick:feeds bldg:Y .", backend="rdflib")  # no @prefix


def test_site_from_ttl_auto_populates_topology():
    site = site_from_ttl(_FEEDS, backend="minimal")
    assert set(site.topology.edges) == {("AHU_1", "VAV_1"), ("AHU_1", "VAV_2"), ("CHW", "AHU_1")}
    assert Topology.from_site(site).provenance == "semantic"


def test_site_from_ttl_no_feeds_has_empty_topology():
    # a points-only Brick model (the existing site_model path) -> empty topology, no regression
    ttl = _PREFIX + (
        "bldg:AHU_1 a brick:AHU ; brick:hasPoint bldg:SAT .\n"
        "bldg:SAT a brick:Supply_Air_Temperature_Sensor .\n"
    )
    site = site_from_ttl(ttl, backend="minimal")
    assert site.topology.edges == ()
    assert len(site.equips) == 1  # equipment still parsed as before
