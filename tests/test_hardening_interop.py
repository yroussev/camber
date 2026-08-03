"""Adversarial hardening of the hand-rolled interop parsers (Brick / Haystack / 223P).

These parse UNTRUSTED external files. On malformed input they must degrade gracefully -- return
a partial/empty result, or raise a clear ValueError -- never leak a raw IndexError / rdflib
BadSyntax / AssertionError, and never hang.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.interop import haystack_semantic as hs  # noqa: E402
from camber.interop import semantic223 as s223  # noqa: E402
from camber.interop.brick import (  # noqa: E402
    mapping_from_brick,
    parse_triples,
    roles_from_brick,
)

# malformed / degenerate Turtle a real-world .ttl might contain
BAD_TTL = [
    "",
    "   \n\t  ",
    "<a> <b> <c>",  # missing final '.'
    "<a> <b> <c> ;",  # dangling predicate-object list
    "<a> <b> <c> , ",
    "<http://x/a.b> <p> <o> .",  # '.' inside an IRI
    '<a> <p> "v;w" .',  # ';' inside a literal
    "<a> <b> .",  # no object
    "<a> .",
    "<a> <b> éèê .",
    "<a>;;;,,,... <b> <c>",
    "<a <b> <c> .",  # unclosed angle bracket
    '<a> <p> "vvv .',  # unclosed quote
    "a b" * 5000,  # large / pathological
]


def _no_hard_crash(fn):
    """Call fn(); a ValueError is acceptable (clear error), a returned value is acceptable
    (graceful). Anything else propagates and fails the test."""
    try:
        return fn()
    except ValueError:
        return None


@pytest.mark.parametrize("ttl", BAD_TTL)
def test_brick_roles_and_mapping_degrade(ttl):
    _no_hard_crash(lambda: roles_from_brick(ttl))  # auto backend (rdflib if present)
    _no_hard_crash(lambda: mapping_from_brick(ttl))
    _no_hard_crash(lambda: roles_from_brick(ttl, backend="minimal"))  # zero-dep path


@pytest.mark.parametrize("ttl", BAD_TTL)
def test_brick_minimal_parser_never_crashes(ttl):
    # the minimal reader must always return the (types, has_point) shape, even on garbage
    types, has_point = parse_triples(ttl)
    assert isinstance(types, dict) and isinstance(has_point, dict)


def test_brick_valid_still_parses():
    ttl = (
        "bldg:AHU1 a brick:AHU ; brick:hasPoint bldg:MAT .\n"
        "bldg:MAT a brick:Mixed_Air_Temperature_Sensor .\n"
    )
    roles = roles_from_brick(ttl, backend="minimal")
    assert roles.get("MAT") is not None  # a real mapping is unaffected by the hardening


BAD_TAGS = [{}, {"a": None, "b": None}, {"tags": 123}, [], [{}], [None, 1, {}], "  "]


@pytest.mark.parametrize("tags", BAD_TAGS)
def test_haystack_role_from_tags_degrades(tags):
    out = hs.role_from_tags(tags)
    assert out is None or hasattr(out, "value")  # a Role or None, never a crash


@pytest.mark.parametrize("points", [{}, {"a": None}, {"tags": 123}, [], [{}], ["x", 1, None]])
def test_haystack_roles_from_haystack_degrades(points):
    out = hs.roles_from_haystack(points)
    assert isinstance(out, dict)  # skips malformed points, never crashes


@pytest.mark.parametrize("ttl", BAD_TTL)
def test_semantic223_degrades(ttl):
    _no_hard_crash(lambda: s223.site_from_223(ttl))
