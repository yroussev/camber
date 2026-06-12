"""Brick SITE-model round-trip: serialize/parse a whole CAMBER :class:`Site`.

:mod:`camber.interop.brick` imports a single building's Brick model into a point
-> role mapping, and :mod:`camber.interop.export` emits one equipment's roles as a
Brick (Turtle) fragment. This module joins both ends into a full site round-trip:
a :class:`~camber.model.entities.Site` (its :class:`~camber.model.entities.Equip`
and :class:`~camber.model.entities.Point` children) goes out to one Brick Turtle
document and comes back in to an equal model.

The serialization follows Brick (https://brickschema.org): the site is a
``brick:Site``; each equipment is typed with its Brick equipment class and tied to
the site with ``brick:isPartOf`` (the inverse of ``brick:hasSite``/``hasPart``);
each point is typed with the Brick point class for its role
(:data:`camber.interop.export.ROLE_TO_BRICK_POINT_CLASS` /
:data:`~camber.interop.export.ROLE_TO_BRICK_PART`) and linked with
``brick:hasPoint``. Context-dependent roles (valves, dampers, fan signals) keep the
intermediate Brick part node :func:`~camber.interop.export.to_brick` emits, so they
re-import to the right role.

Parsing reuses :mod:`camber.interop.brick` -- its minimal Turtle reader by default
and its rdflib path when the optional ``brick`` extra is installed. No new
dependency is introduced; rdflib is imported lazily inside ``brick``.
"""

from __future__ import annotations

import re

from ..model.entities import Equip, Point, Site
from . import brick as _brick
from .export import ROLE_TO_BRICK_PART, to_brick

# Brick part classes the export layer interposes between an equipment and a
# context-dependent point; they are not CAMBER equipment, so importing skips them.
_PART_CLASSES = frozenset(pc for _, pc, _ in ROLE_TO_BRICK_PART.values())

# The IRI a serialized equipment uses to point back at its site. ``isPartOf`` is the
# standard Brick inverse of the site's ``hasPart``; we read it back during import.
_IS_PART_OF = "brick:isPartOf"

_SITE_PREFIX = ('@prefix bldg: <bldg#> .\n'
                '@prefix brick: <https://brickschema.org/schema/Brick#> .\n\n')


def _equip_fragment(equip: Site) -> str:  # equip: Equip
    """Brick Turtle for one equipment, reusing :func:`to_brick`, with the
    equipment additionally declared ``brick:isPartOf`` its site.

    :func:`to_brick` already emits the equipment typing, its parts, points and the
    per-point typings re-importable to the same roles; we only need to drop its
    per-fragment ``@prefix`` block (the site document declares the prefixes once)
    and splice in the ``isPartOf`` link on the equipment node.
    """
    point_names = {p.role: p.name for p in equip.points}
    frag = to_brick(equip.id, equip.equip_class, equip.roles(),
                    point_names=point_names)
    # strip the fragment's own prefix lines; the site document declares them once.
    body = frag.split("\n\n", 1)[1] if "\n\n" in frag else frag
    body = body.lstrip("\n")
    if equip.site:
        # splice isPartOf onto the equipment node, before its first " ." or " ;".
        anchor = f"bldg:{equip.id} a brick:{equip.equip_class}"
        link = f" ;\n    {_IS_PART_OF} bldg:{equip.site}"
        if anchor in body:
            body = body.replace(anchor, anchor + link, 1)
    return body


def site_to_ttl(site: Site) -> str:
    """Serialize a CAMBER :class:`Site` to a Brick (Turtle) string.

    The site is emitted as a ``brick:Site``; every :class:`Equip` is typed with its
    Brick equipment class, declared ``brick:isPartOf`` the site, and carries its
    points (and any disambiguating part nodes). The result re-imports via
    :func:`site_from_ttl` to an equal model and re-imports point-wise via
    :func:`camber.interop.brick.roles_from_brick`.
    """
    lines = [_SITE_PREFIX, f"bldg:{site.id} a brick:Site .\n\n"]
    for equip in site.equips:
        # ensure each equipment knows its site even if the field was left blank.
        eq = equip if equip.site == site.id else Equip(
            id=equip.id, equip_class=equip.equip_class, points=equip.points,
            site=site.id, space=equip.space)
        lines.append(_equip_fragment(eq))
        if not lines[-1].endswith("\n"):
            lines.append("\n")
        lines.append("\n")
    return "".join(lines)


def _is_part_of(ttl: str, backend: str) -> dict:
    """Parse ``brick:isPartOf`` (subject -> object local-name) from the Turtle.

    Mirrors the two backends :mod:`camber.interop.brick` uses: rdflib when present
    (handles arbitrary Turtle), else a minimal regex over the same statement shape
    the minimal reader accepts. ``brick.parse_triples`` only tracks ``a`` and
    ``hasPoint``, so the site link is recovered here.
    """
    use_rdflib = (backend == "rdflib"
                  or (backend == "auto" and _brick._have_rdflib()))
    if use_rdflib:
        import rdflib

        g = rdflib.Graph()
        g.parse(data=ttl, format="turtle")
        out = {}
        for s, p, o in g:
            if _brick._local(str(p)) == "isPartOf":
                out[_brick._local(str(s))] = _brick._local(str(o))
        return out
    return {s: objs[0] for s, objs in _minimal_links(ttl, "isPartOf").items()}


def _minimal_links(ttl: str, predicate: str) -> dict:
    """Recover {subject -> [object, ...]} for a predicate using the minimal-parser
    grammar -- splitting on statement terminators and predicate-object groups
    exactly like :func:`camber.interop.brick.parse_triples`, so it sees the same
    statements that reader does (``brick.parse_triples`` only tracks ``a`` and
    ``hasPoint``, so site/part links are recovered here).
    """
    lines = [ln.strip() for ln in ttl.splitlines()
             if ln.strip() and not ln.strip().startswith(("@prefix", "#", "@base"))]
    text = " ".join(lines)
    out = {}
    for stmt in re.split(r"\s\.\s", text + " "):
        stmt = stmt.strip().rstrip(".").strip()
        if not stmt:
            continue
        parts = stmt.split(None, 1)
        if len(parts) < 2:
            continue
        subj, rest = _brick._local(parts[0]), parts[1]
        for grp in rest.split(";"):
            grp = grp.strip()
            pp = grp.split(None, 1)
            if len(pp) < 2:
                continue
            pred, objs = pp[0], [o.strip() for o in pp[1].split(",") if o.strip()]
            if pred.endswith(predicate):
                out.setdefault(subj, []).extend(_brick._local(o) for o in objs)
    return out


def site_from_ttl(ttl: str, *, backend: str = "auto") -> Site:
    """Parse a Brick Turtle document into a CAMBER :class:`Site`.

    Reuses :mod:`camber.interop.brick` for both parsing backends: ``"auto"`` uses
    rdflib if installed and otherwise the built-in minimal reader; ``"rdflib"``
    forces the rdflib path (needs the ``brick`` extra); ``"minimal"`` forces the
    zero-dependency reader. Equipment classes and ``isPartOf`` links come from the
    parsed triples; each point's role comes from
    :func:`camber.interop.brick.roles_from_brick`, and points are attributed to the
    owning equipment via ``hasPoint`` (directly, or through an interposed part node
    linked by ``hasPart``).
    """
    types, has_point = _brick._parse(ttl, backend)
    roles = _brick.roles_from_brick(ttl, backend=backend)
    part_of = _is_part_of(ttl, backend)
    has_part = _has_part(ttl, backend)

    # site node: the subject typed as a Site (fall back to any isPartOf target).
    site_id = next((s for s, c in types.items() if c == "Site"), "")
    if not site_id and part_of:
        site_id = next(iter(part_of.values()))

    # an equipment is a typed subject that is not the site, not a Brick part class,
    # and not a point (points carry a role / appear as hasPoint objects).
    point_owners = {pt: part for part, pts in has_point.items() for pt in pts}
    equips = []
    for subj, cls in types.items():
        if subj == site_id or cls == "Site" or cls in _PART_CLASSES:
            continue
        if subj in roles or subj in point_owners and subj not in has_point:
            continue  # this subject is a point, not equipment
        if subj not in has_point and subj not in has_part and subj not in part_of:
            continue  # nothing attached -> not equipment we serialized
        # collect this equipment's points: those it hasPoint directly, plus those
        # on parts it hasPart.
        pt_names = list(has_point.get(subj, []))
        for part in has_part.get(subj, []):
            pt_names.extend(has_point.get(part, []))
        points = tuple(
            Point(name=pn, role=roles[pn]) for pn in pt_names if pn in roles)
        equips.append(Equip(id=subj, equip_class=cls, points=points,
                            site=part_of.get(subj, site_id)))

    equips.sort(key=lambda e: e.id)
    return Site(id=site_id, equips=tuple(equips))


def _has_part(ttl: str, backend: str) -> dict:
    """Recover {equip -> [part, ...]} from ``brick:hasPart``.

    Same two-backend strategy as :func:`_is_part_of`; lets the importer fold a
    part's points back onto the owning equipment.
    """
    use_rdflib = (backend == "rdflib"
                  or (backend == "auto" and _brick._have_rdflib()))
    if use_rdflib:
        import rdflib

        g = rdflib.Graph()
        g.parse(data=ttl, format="turtle")
        out = {}
        for s, p, o in g:
            if _brick._local(str(p)) == "hasPart":
                out.setdefault(_brick._local(str(s)), []).append(
                    _brick._local(str(o)))
        return out
    return _minimal_links(ttl, "hasPart")
