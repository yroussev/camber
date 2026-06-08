"""Brick Schema interop: derive CAMBER role mappings from a Brick model.

Brick (https://brickschema.org) describes points by class
(e.g. ``brick:Mixed_Air_Temperature_Sensor``) and equipment relationships
(``brick:hasPoint``, ``brick:hasPart``). Given a building's Brick model -- like the
``.ttl`` files shipped with the LBNL FDD datasets -- this derives a
point-name -> :class:`~camber.model.roles.Role` mapping automatically, so a
Brick-tagged building needs no hand-written ``mapping.json``.

Some point classes are unambiguous (a Mixed_Air_Temperature_Sensor is always
MIXED_AIR_TEMP); others need equipment context -- a Valve_Position_Sensor is a
cooling or heating valve depending on which coil owns it, a damper depends on its
location, a fan signal depends on whether it's the supply or return fan. Both are
handled here.

Includes a minimal Turtle reader for the subset Brick equipment/point models use
(typed statements with ``a`` and ``;``/``,`` predicate-object lists). It is NOT a
full RDF parser; for complex models, parse with rdflib and pass the triples to
:func:`roles_from_triples`.
"""

from __future__ import annotations

import re

from ..model.mapping import MappingProvider
from ..model.roles import Role

# Unambiguous Brick point class (local name) -> Role.
DIRECT_CLASS_TO_ROLE = {
    "Mixed_Air_Temperature_Sensor": Role.MIXED_AIR_TEMP,
    "Outside_Air_Temperature_Sensor": Role.OAT,
    "Return_Air_Temperature_Sensor": Role.RETURN_AIR_TEMP,
    "Supply_Air_Temperature_Sensor": Role.SUPPLY_AIR_TEMP,
    "Discharge_Air_Temperature_Sensor": Role.SUPPLY_AIR_TEMP,
    "Supply_Air_Temperature_Setpoint": Role.SUPPLY_AIR_TEMP_SP,
    "Supply_Air_Static_Pressure_Sensor": Role.DUCT_STATIC,
    "Supply_Air_Static_Pressure_Setpoint": Role.DUCT_STATIC_SP,
    "Supply_Air_Flow_Sensor": Role.AIRFLOW,
    "Zone_Air_Temperature_Sensor": Role.SPACE_TEMP,
    "Occupancy_Status": Role.OCCUPANCY,
}

# Point classes whose role depends on the owning equipment part.
_CONTEXT_CLASSES = {"Valve_Position_Sensor", "Damper_Position_Sensor",
                    "Speed_status", "Speed_Status", "Fan_On_Off_Status"}


def _local(token: str) -> str:
    """Local name of a prefixed name or full IRI.

    Handles ``bldg:OA_TEMP`` -> ``OA_TEMP`` and
    ``https://brickschema.org/schema/Brick#AHU`` -> ``AHU`` (splits on the last of
    ``#`` / ``/`` / ``:``), so it works for both the minimal parser's prefixed
    tokens and rdflib's expanded URIs.
    """
    return (token.strip().lstrip("<").rstrip(">")
            .split("#")[-1].split("/")[-1].split(":")[-1])


def parse_triples(ttl: str):
    """Parse the Brick-subset Turtle into (types, has_point).

    Returns ``types``: {subject_local -> class_local} and ``has_point``:
    {part_local -> [point_local, ...]}. Handles ``a``/``;``/``,`` lists; ignores
    ``@prefix`` and comments. Not a general RDF parser.
    """
    lines = [ln.strip() for ln in ttl.splitlines()
             if ln.strip() and not ln.strip().startswith(("@prefix", "#", "@base"))]
    text = " ".join(lines)
    types, has_point = {}, {}
    for stmt in re.split(r"\s\.\s", text + " "):
        stmt = stmt.strip().rstrip(".").strip()
        if not stmt:
            continue
        parts = stmt.split(None, 1)
        if len(parts) < 2:
            continue
        subj, rest = _local(parts[0]), parts[1]
        for grp in rest.split(";"):
            grp = grp.strip()
            if not grp:
                continue
            pp = grp.split(None, 1)
            if len(pp) < 2:
                continue
            pred, objs = pp[0], [o.strip() for o in pp[1].split(",") if o.strip()]
            if pred == "a":
                types[subj] = _local(objs[0])
            elif pred.endswith("hasPoint"):
                has_point.setdefault(subj, []).extend(_local(o) for o in objs)
    return types, has_point


def _context_role(point: str, pcls: str, owner_cls: str, owner_name: str):
    """Resolve a context-dependent point class to a role using its owning part."""
    oc, on = (owner_cls or ""), (owner_name or "")
    if pcls == "Valve_Position_Sensor":
        if "Chilled" in oc or "Cooling" in oc:
            return Role.COOL_VALVE
        if "Hot" in oc or "Heating" in oc:
            return Role.HEAT_VALVE
    elif pcls == "Damper_Position_Sensor":
        if "Outside" in oc or "Outdoor" in oc:
            return Role.OA_DAMPER
    elif pcls in ("Speed_status", "Speed_Status"):
        if on.startswith("Supply"):
            return Role.SUPPLY_FAN_SPEED
    elif pcls == "Fan_On_Off_Status":
        if on.startswith("Supply"):
            return Role.SUPPLY_FAN_STATUS
    return None


def roles_from_triples(types: dict, has_point: dict) -> dict:
    """Map point local-names to roles from parsed Brick triples.

    Prefers measured *Sensor/Status points; command points (``*_Command``) and
    classes with no CAMBER role are skipped. Returns {point_name -> Role}.
    """
    # invert hasPoint: point -> owning part
    owner = {}
    for part, pts in has_point.items():
        for p in pts:
            owner.setdefault(p, part)
    out = {}
    for subj, cls in types.items():
        if cls in DIRECT_CLASS_TO_ROLE:
            out[subj] = DIRECT_CLASS_TO_ROLE[cls]
        elif cls in _CONTEXT_CLASSES:
            part = owner.get(subj)
            role = _context_role(subj, cls, types.get(part, ""), part or "")
            if role is not None:
                out[subj] = role
    return out


def _have_rdflib() -> bool:
    try:
        import rdflib  # noqa: F401
        return True
    except ImportError:
        return False


def parse_triples_rdflib(ttl: str):
    """Parse Brick Turtle with rdflib -> (types, has_point), same shape as the
    minimal parser.

    A full RDF parser, so it handles arbitrary real-world Turtle (blank nodes,
    multiple namespaces, full IRIs, odd formatting) that the minimal reader can't.
    Requires the ``brick`` extra (``pip install camber[brick]``).
    """
    import rdflib

    g = rdflib.Graph()
    g.parse(data=ttl, format="turtle")
    types, has_point = {}, {}
    for s, p, o in g:
        pl = _local(str(p))
        if pl == "type":                       # rdf:type
            types[_local(str(s))] = _local(str(o))
        elif pl == "hasPoint":                 # brick:hasPoint (any Brick version)
            has_point.setdefault(_local(str(s)), []).append(_local(str(o)))
    return types, has_point


def _parse(ttl: str, backend: str):
    """Dispatch to the requested parser backend; returns (types, has_point)."""
    if backend == "minimal":
        return parse_triples(ttl)
    if backend == "rdflib":
        if not _have_rdflib():
            raise ImportError("rdflib not installed; `pip install camber[brick]` "
                              "or use backend='minimal'")
        return parse_triples_rdflib(ttl)
    if backend == "auto":
        return parse_triples_rdflib(ttl) if _have_rdflib() else parse_triples(ttl)
    raise ValueError(f"unknown backend {backend!r} (use auto/rdflib/minimal)")


def roles_from_brick(ttl: str, *, backend: str = "auto") -> dict:
    """Parse a Brick Turtle string and return {point_name -> Role}.

    ``backend``: ``"auto"`` uses rdflib if installed (robust, handles any Brick
    model) and falls back to the built-in minimal parser; ``"rdflib"`` forces the
    rdflib path (needs the ``brick`` extra); ``"minimal"`` forces the zero-dependency
    reader (good for the common, well-formed equipment/point models).
    """
    types, has_point = _parse(ttl, backend)
    return roles_from_triples(types, has_point)


def mapping_from_brick(ttl: str, *, backend: str = "auto") -> MappingProvider:
    """Build a :class:`MappingProvider` directly from a Brick Turtle model."""
    roles = roles_from_brick(ttl, backend=backend)
    return MappingProvider.from_dict(
        {"aliases": {name: role.value for name, role in roles.items()}})
