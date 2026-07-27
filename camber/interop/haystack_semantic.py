"""Project-Haystack tag → role import (the inverse of the Haystack export in :mod:`export`).

Export turns a role into its marker-tag set (`export.haystack_tags`, driven by
`model.roles.HAYSTACK_HINT`). This closes the round-trip: given the marker tags on a point, recover its
:class:`~camber.model.roles.Role`. A role matches when its hint tag-set is a **subset** of the point's
tags; on ties the **most specific** (largest hint) wins — the Haystack analogue of the Brick importer's
part-context disambiguation (`interop.brick`), so e.g. ``…temp sp`` beats ``…temp sensor``.

Zero dependencies (stdlib): a point is any ``(name, tags)`` pair or a Haystack-style tag dict.
"""

from __future__ import annotations

from ..model.roles import HAYSTACK_HINT, Role
from ..model.mapping import MappingProvider

# role -> its hint tag-set, largest (most specific) first so the first subset match wins the tie-break
_ROLE_TAGSETS = sorted(
    ((role, frozenset(hint.split())) for role, hint in HAYSTACK_HINT.items() if hint),
    key=lambda rt: (-len(rt[1]), rt[0].value),
)

# markers that are structural/identity, never part of a role's semantic tag set
_NON_SEMANTIC = frozenset({"id", "dis", "navName", "point", "his", "cur", "equip", "site", "kind",
                           "tz", "unit", "mod", "hisSize"})


def role_from_tags(tags) -> Role | None:
    """The role whose Haystack hint tags are a subset of ``tags`` (most-specific wins), or None.

    ``tags`` may be a set/list of marker strings or a space-separated string.
    """
    if isinstance(tags, str):
        tagset = frozenset(tags.split())
    else:
        tagset = frozenset(tags)
    for role, hint in _ROLE_TAGSETS:            # already ordered most-specific first
        if hint <= tagset:
            return role
    return None


def _point_tags(point) -> tuple:
    """Normalize a point to ``(name, tagset)``. Accepts ``(name, tags)`` or a Haystack tag dict."""
    if isinstance(point, dict):
        name = point.get("id") or point.get("dis") or point.get("navName") or ""
        tags = {k for k, v in point.items() if k not in _NON_SEMANTIC and (v is True or v == "M" or v == "✓")}
        return str(name), frozenset(tags)
    name, tags = point                          # (name, tags) pair
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
        {"aliases": {name: role.value for name, role in roles.items()}})
