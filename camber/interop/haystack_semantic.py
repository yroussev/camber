"""Project-Haystack tag → role import (the inverse of the Haystack export in :mod:`export`).

Export turns a role into its marker-tag set (`export.haystack_tags`, driven by
`model.roles.HAYSTACK_HINT`). This closes the round-trip: given the marker tags on a point,
recover its :class:`~camber.model.roles.Role`. A role matches when its hint tag-set is a **subset**
of the point's tags; on ties the **most specific** (largest hint) wins — the Haystack analogue of
the Brick importer's part-context disambiguation (`interop.brick`), so e.g. ``…temp sp`` beats
``…temp sensor``.

Zero dependencies (stdlib): a point is any ``(name, tags)`` pair or a Haystack-style tag dict.
"""

from __future__ import annotations

from ..model.mapping import MappingProvider
from ..model.roles import HAYSTACK_HINT, Role
from ..model.topology import Topology

# role -> its hint tag-set, largest (most specific) first so the first subset match wins the
# tie-break
_ROLE_TAGSETS = sorted(
    ((role, frozenset(hint.split())) for role, hint in HAYSTACK_HINT.items() if hint),
    key=lambda rt: (-len(rt[1]), rt[0].value),
)

# markers that are structural/identity, never part of a role's semantic tag set
_NON_SEMANTIC = frozenset(
    {
        "id",
        "dis",
        "navName",
        "point",
        "his",
        "cur",
        "equip",
        "site",
        "kind",
        "tz",
        "unit",
        "mod",
        "hisSize",
    }
)


def role_from_tags(tags) -> Role | None:
    """The role whose Haystack hint tags are a subset of ``tags`` (most-specific wins), or None.

    ``tags`` may be a set/list of marker strings or a space-separated string.
    """
    if isinstance(tags, str):
        tagset = frozenset(tags.split())
    else:
        # tolerate a malformed collection: keep only string markers (a dict/None element would
        # be unhashable or meaningless), so an unresolvable set simply yields None.
        tagset = frozenset(t for t in tags if isinstance(t, str))
    for role, hint in _ROLE_TAGSETS:  # already ordered most-specific first
        if hint <= tagset:
            return role
    return None


def _point_tags(point) -> tuple:
    """Normalize a point to ``(name, tagset)``. Accepts ``(name, tags)`` or a Haystack tag dict."""
    if isinstance(point, dict):
        name = point.get("id") or point.get("dis") or point.get("navName") or ""
        tags = {
            k
            for k, v in point.items()
            if k not in _NON_SEMANTIC and (v is True or v == "M" or v == "✓")
        }
        return str(name), frozenset(tags)
    try:
        name, tags = point  # (name, tags) pair
    except (TypeError, ValueError):
        return "", frozenset()  # not a dict or (name, tags) pair -> unresolvable (skipped)
    tagset = frozenset(tags.split()) if isinstance(tags, str) else frozenset(tags)
    return str(name), tagset


def roles_from_haystack(points) -> dict:
    """Parse Haystack points and return ``{point_name -> Role}`` for those that resolve.

    ``points`` is an iterable of ``(name, tags)`` pairs or Haystack tag dicts (marker tags as keys).
    """
    out = {}
    for p in points:
        name, tagset = _point_tags(p)
        role = role_from_tags(tagset)
        if role is not None and name:
            out[name] = role
    return out


def mapping_from_haystack(points) -> MappingProvider:
    """Build a :class:`MappingProvider` directly from Haystack points (tag → role aliases)."""
    roles = roles_from_haystack(points)
    return MappingProvider.from_dict(
        {"aliases": {name: role.value for name, role in roles.items()}}
    )


def _entity_id(entity) -> str:
    """The subject id of a Haystack entity dict (``id`` / ``dis`` / ``navName``), or ``""``."""
    if isinstance(entity, dict):
        return str(entity.get("id") or entity.get("dis") or entity.get("navName") or "")
    return ""


def _ref_target(value):
    """Resolve a Haystack ``Ref`` to its target id (``"@AHU_1"`` or ``{"val": "@AHU_1"}`` -> id)."""
    if isinstance(value, str):
        return value[1:] if value.startswith("@") else value or None
    if isinstance(value, dict):
        inner = value.get("val") or value.get("id")
        if isinstance(inner, str):
            return inner[1:] if inner.startswith("@") else inner or None
    return None


def topology_from_haystack(entities, *, parent_refs=("ahuRef", "equipRef")) -> Topology:
    """Build a served-by :class:`~camber.model.topology.Topology` from Haystack reference tags.

    Reads ``ahuRef`` (a terminal served by an air handler) and ``equipRef`` (equipment nested under
    parent equipment) into edges ``(parent, child)`` with ``provenance="semantic"``. ``equipRef`` is
    only followed for entities carrying the ``equip`` marker: on a *point*, ``equipRef`` is point
    ownership (handled by :func:`roles_from_haystack`), not a served-by relation. ``siteRef`` /
    ``spaceRef`` are not served-by and are ignored. Entities with no id, and refs whose value is not
    a resolvable id, are skipped -- incomplete tagging degrades to a partial graph, never a crash.
    """
    edges: list = []
    for e in entities:
        child = _entity_id(e)
        if not child:
            continue
        is_equip = isinstance(e, dict) and e.get("equip") in (True, "M", "✓")
        for ref in parent_refs:
            if not isinstance(e, dict) or ref not in e:
                continue
            if ref == "equipRef" and not is_equip:
                continue  # a point's equipRef is ownership, not served-by
            parent = _ref_target(e.get(ref))
            if parent:
                edges.append((parent, child))
    return Topology.from_edges(edges, provenance="semantic")
