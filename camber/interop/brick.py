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
handled here. Real models also use non-standard classes and mis-typed points;
:func:`brick_mapping_report` says, per point, which mapped, which were accepted as an
alias (with a caveat), which were left unmapped as ambiguous (and why), and which classes
have no CAMBER role -- nothing is mis-mapped silently.

Includes a minimal Turtle reader for the subset Brick equipment/point models use
(typed statements with ``a`` and ``;``/``,`` predicate-object lists). It is NOT a
full RDF parser; for complex models, parse with rdflib and pass the triples to
:func:`roles_from_triples`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..model.mapping import MappingProvider
from ..model.roles import Role
from ..model.topology import Topology

# Unambiguous Brick point class (local name) -> Role.
DIRECT_CLASS_TO_ROLE = {
    # --- air side ---
    "Mixed_Air_Temperature_Sensor": Role.MIXED_AIR_TEMP,
    "Outside_Air_Temperature_Sensor": Role.OAT,
    "Outside_Air_Wet_Bulb_Temperature_Sensor": Role.WETBULB_TEMP,
    "Outside_Air_Humidity_Sensor": Role.OUTDOOR_RH,
    "Outside_Air_CO2_Sensor": Role.OUTDOOR_CO2,
    "Outside_Air_Flow_Sensor": Role.OA_AIRFLOW,
    "Return_Air_Temperature_Sensor": Role.RETURN_AIR_TEMP,
    "Return_Air_Humidity_Sensor": Role.RETURN_AIR_HUMIDITY,
    "Supply_Air_Temperature_Sensor": Role.SUPPLY_AIR_TEMP,
    "Discharge_Air_Temperature_Sensor": Role.SUPPLY_AIR_TEMP,
    "Supply_Air_Temperature_Setpoint": Role.SUPPLY_AIR_TEMP_SP,
    "Supply_Air_Humidity_Sensor": Role.SUPPLY_AIR_HUMIDITY,
    "Supply_Air_Static_Pressure_Sensor": Role.DUCT_STATIC,
    "Supply_Air_Static_Pressure_Setpoint": Role.DUCT_STATIC_SP,
    "Supply_Air_Flow_Sensor": Role.AIRFLOW,
    "Discharge_Air_Flow_Sensor": Role.AIRFLOW,
    "Supply_Air_Flow_Setpoint": Role.AIRFLOW_SP,
    "Discharge_Air_Flow_Setpoint": Role.AIRFLOW_SP,
    "Filter_Differential_Pressure_Sensor": Role.FILTER_DIFF_PRESS,
    "Zone_Air_Temperature_Sensor": Role.SPACE_TEMP,
    "Zone_Air_Cooling_Temperature_Setpoint": Role.COOL_SP,
    "Zone_Air_Heating_Temperature_Setpoint": Role.HEAT_SP,
    "CO2_Sensor": Role.CO2,
    "Zone_Air_CO2_Sensor": Role.CO2,
    "Occupancy_Status": Role.OCCUPANCY,
    # --- hot-water plant ---
    "Hot_Water_Supply_Temperature_Sensor": Role.HW_SUPPLY_TEMP,
    "Hot_Water_Return_Temperature_Sensor": Role.HW_RETURN_TEMP,
    "Hot_Water_Differential_Pressure_Sensor": Role.HW_DIFF_PRESS,
    "Hot_Water_Differential_Pressure_Setpoint": Role.HW_DIFF_PRESS_SP,
    "Hot_Water_Flow_Sensor": Role.HW_FLOW,
    # --- chilled-water plant ---
    "Chilled_Water_Supply_Temperature_Sensor": Role.CHW_SUPPLY_TEMP,
    "Chilled_Water_Return_Temperature_Sensor": Role.CHW_RETURN_TEMP,
    "Chilled_Water_Supply_Temperature_Setpoint": Role.CHW_SUPPLY_TEMP_SP,
    "Chilled_Water_Differential_Pressure_Sensor": Role.CHW_DIFF_PRESS,
    "Chilled_Water_Differential_Pressure_Setpoint": Role.CHW_DIFF_PRESS_SP,
    "Chilled_Water_Flow_Sensor": Role.CHW_FLOW,
    # --- condenser water (named relative to the chiller's condenser) ---
    "Entering_Condenser_Water_Temperature_Sensor": Role.CW_SUPPLY_TEMP,
    "Leaving_Condenser_Water_Temperature_Sensor": Role.CW_RETURN_TEMP,
}

# Non-standard class names seen in published Brick models, accepted because the meaning is
# obvious -- but reported with a caveat (see :func:`brick_mapping_report`), never silently.
ALIAS_CLASS_TO_ROLE = {
    "Outdoor_Air_Flow_Rate": (
        Role.OA_AIRFLOW,
        "non-standard class; read as Outside_Air_Flow_Sensor (outdoor-air volumetric flow)",
    ),
    "Outdoor_Air_Damper": (
        Role.OA_DAMPER,
        "a damper *equipment* class used as a point type; read as the outdoor-air damper "
        "position (%) -- confirm it is not a binary open/closed status",
    ),
    "Supply_Air_Fan_Speed": (
        Role.SUPPLY_FAN_SPEED,
        "non-standard class; read as Speed_Status on the supply fan (%)",
    ),
    "Hot_Water_Supply_Flow_Rate": (
        Role.HW_FLOW,
        "non-standard class; read as Hot_Water_Flow_Sensor",
    ),
    "Chilled_Water_Supply_Flow_Rate": (
        Role.CHW_FLOW,
        "non-standard class; read as Chilled_Water_Flow_Sensor",
    ),
}

# Classes a CAMBER role *could* come from but whose meaning the class alone does not pin down.
# They are never mapped; the report says why, so the reviewer can map them by hand.
AMBIGUOUS_CLASSES = {
    "Hot_Water_Temperature_Sensor": "hot-water temperature without supply/return -- map by hand",
    "Chilled_Water_Temperature_Sensor": (
        "chilled-water temperature without supply/return -- map by hand"
    ),
    "Occupant_Count": "a head count, not the binary occupied/unoccupied OCCUPANCY signal",
    "Enable_Status": "an enable is not proof of operation -- not mapped to a running status",
    "Enable_Command": "an enable is not proof of operation -- not mapped to a running status",
}

# Point classes whose role depends on the owning equipment part.
_CONTEXT_CLASSES = {
    "Valve_Position_Sensor",
    "Damper_Position_Sensor",
    "Speed_status",
    "Speed_Status",
    "Fan_On_Off_Status",
    "Pump_On_Off_Status",
    "Pump_Status",
    "Run_Status",
    "On_Off_Status",
    "Electrical_Power_Sensor",
    "Heating_Temperature_Setpoint",
    "Cooling_Temperature_Setpoint",
    "Leaving_Hot_Water_Temperature_Sensor",
    "Entering_Hot_Water_Temperature_Sensor",
    "Leaving_Chilled_Water_Temperature_Sensor",
    "Entering_Chilled_Water_Temperature_Sensor",
}

# Command classes: mapped (via the same owner context as their sensor counterpart) only when the
# owner carries no measured point for the same role -- a position/speed feedback always wins.
_COMMAND_FALLBACK = {
    "Valve_Position_Command": "Valve_Position_Sensor",
    "Valve_Command": "Valve_Position_Sensor",
    "Damper_Position_Command": "Damper_Position_Sensor",
    "Damper_Command": "Damper_Position_Sensor",
    "Speed_Command": "Speed_Status",
}

# Volumetric-flow roles (cfm / gpm). A point typed as one of these whose *name* says it is a
# speed / percent is a mis-typed point: mapping it would feed a 0-100 % series to a cfm rule.
_FLOW_ROLES = frozenset(
    {Role.AIRFLOW, Role.OA_AIRFLOW, Role.AIRFLOW_SP, Role.HW_FLOW, Role.CHW_FLOW}
)
_SPEEDLIKE_TOKENS = frozenset({"spd", "speed", "pct", "percent", "vfd", "hz", "freq"})

# Equipment kinds whose own electrical power is the equip-frame POWER role (not a component).
_POWER_OWNERS = ("Pump", "Chiller", "Boiler", "Cooling_Tower", "Heat_Pump", "Rooftop_Unit")
# Owners whose leaving/entering water is the plant supply/return (a coil's is the reverse).
_PLANT_OWNERS = ("Boiler", "Chiller", "Hot_Water_System", "Chilled_Water_System", "Plant", "Loop")

_POINT_SUFFIXES = (
    "_Sensor",
    "_Setpoint",
    "_Status",
    "_status",
    "_Command",
    "_Count",
    "_Rate",
    "_Speed",
)


def _local(token: str) -> str:
    """Local name of a prefixed name or full IRI.

    Handles ``bldg:OA_TEMP`` -> ``OA_TEMP`` and
    ``https://brickschema.org/schema/Brick#AHU`` -> ``AHU`` (splits on the last of
    ``#`` / ``/`` / ``:``), so it works for both the minimal parser's prefixed
    tokens and rdflib's expanded URIs.
    """
    return token.strip().lstrip("<").rstrip(">").split("#")[-1].split("/")[-1].split(":")[-1]


def parse_triples(ttl: str):
    """Parse the Brick-subset Turtle into (types, has_point).

    Returns ``types``: {subject_local -> class_local} and ``has_point``:
    {part_local -> [point_local, ...]}. Handles ``a``/``;``/``,`` lists; ignores
    ``@prefix`` and comments. Not a general RDF parser.
    """
    lines = [
        ln.strip()
        for ln in ttl.splitlines()
        if ln.strip() and not ln.strip().startswith(("@prefix", "#", "@base"))
    ]
    text = " ".join(lines)
    types: dict = {}
    has_point: dict = {}
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
            if pred == "a" and objs:
                types[subj] = _local(objs[0])
            elif pred.endswith("hasPoint"):
                has_point.setdefault(subj, []).extend(_local(o) for o in objs)
    return types, has_point


def _context_role(point: str, pcls: str, owner_cls: str, owner_name: str):
    """Resolve a context-dependent point class to ``(role, note)`` using its owning part.

    ``role`` is None when the context does not pin the meaning down; ``note`` then says why (empty
    when the class simply has no CAMBER role in that context).
    """
    oc, on = (owner_cls or ""), (owner_name or "")
    if pcls == "Valve_Position_Sensor":
        if "Chilled" in oc or "Cooling" in oc:
            return Role.COOL_VALVE, ""
        if "Hot" in oc or "Heating" in oc:
            return Role.HEAT_VALVE, ""
        return None, "valve position whose owner is not a heating/cooling coil or valve"
    if pcls == "Damper_Position_Sensor":
        if "Outside" in oc or "Outdoor" in oc:
            return Role.OA_DAMPER, ""
        return None, ""
    if pcls in ("Speed_status", "Speed_Status"):
        if on.startswith("Supply") or oc in ("Supply_Fan", "Discharge_Fan"):
            return Role.SUPPLY_FAN_SPEED, ""
        if "Hot_Water_Pump" in oc:
            return Role.HW_PUMP_SPEED, ""
        if "Chilled_Water_Pump" in oc:
            return Role.CHW_PUMP_SPEED, ""
        if "Cooling_Tower" in oc:
            return Role.TOWER_FAN_SPEED, ""
        if "Pump" in oc:
            return None, "pump speed whose loop (hot/chilled water) the owner class does not say"
        return None, ""
    if pcls == "Fan_On_Off_Status":
        if on.startswith("Supply") or oc in ("Supply_Fan", "Discharge_Fan"):
            return Role.SUPPLY_FAN_STATUS, ""
        return None, ""
    if pcls in ("Pump_On_Off_Status", "Pump_Status"):
        return Role.PUMP_STATUS, ""
    if pcls in ("Run_Status", "On_Off_Status"):
        if "Pump" in oc:
            return Role.PUMP_STATUS, ""
        if "Boiler" in oc:
            if pcls == "Run_Status":
                return Role.BOILER_STATUS, ""
            # An on/off status on a boiler is, in published models, often the plant *enable*
            # (held on all season) rather than burner firing; mapping it to BOILER_STATUS makes
            # every enabled-but-idle hour look like firing (e.g. a summer-lockout fault).
            return None, (
                "boiler on/off status may be the enable rather than burner firing; confirm from "
                "the data before mapping it to boiler_status"
            )
        return None, ""
    if pcls == "Electrical_Power_Sensor":
        if any(k in oc for k in _POWER_OWNERS):
            return Role.POWER, ""
        return None, (
            "component power (e.g. a fan motor inside an air handler) is not the equipment's "
            "power role"
        )
    if pcls in ("Heating_Temperature_Setpoint", "Cooling_Temperature_Setpoint"):
        if any(k in oc for k in ("Zone", "VAV", "Terminal")):
            return (Role.HEAT_SP if pcls.startswith("Heating") else Role.COOL_SP), ""
        return None, "temperature setpoint whose owner is not a zone or terminal unit"
    if pcls.endswith("_Water_Temperature_Sensor"):  # Leaving/Entering hot/chilled water
        if not any(k in oc for k in _PLANT_OWNERS):
            return None, (
                "leaving/entering water temperature is plant supply/return only on a boiler, "
                "chiller or loop owner (a coil's leaving water is its return)"
            )
        leaving = pcls.startswith("Leaving")
        if "Hot" in pcls:
            return (Role.HW_SUPPLY_TEMP if leaving else Role.HW_RETURN_TEMP), ""
        return (Role.CHW_SUPPLY_TEMP if leaving else Role.CHW_RETURN_TEMP), ""
    return None, ""


@dataclass(frozen=True)
class BrickPointMapping:
    """How one Brick point resolved to a CAMBER role.

    ``status`` is ``"mapped"`` (a standard class), ``"alias"`` (a non-standard class accepted
    with a caveat in ``note``), ``"ambiguous"`` (a role is plausible but the model does not pin
    it down -- **not** mapped; ``note`` says why) or ``"unmapped"`` (no CAMBER role).
    """

    point: str
    brick_class: str
    role: Role | None
    status: str
    note: str = ""


@dataclass(frozen=True)
class BrickMappingReport:
    """Per-point outcome of importing a Brick model: what mapped, and what a human must look at."""

    points: tuple = ()

    @property
    def roles(self) -> dict:
        """``{point -> Role}`` for the mapped and alias-mapped points (what the importer uses)."""
        return {p.point: p.role for p in self.points if p.role is not None}

    def with_status(self, status: str) -> list:
        """The points with the given ``status`` (mapped / alias / ambiguous / unmapped)."""
        return [p for p in self.points if p.status == status]

    def counts(self) -> dict:
        """``{status -> n}`` over every point, plus ``"points"`` (the total)."""
        out = {k: 0 for k in ("mapped", "alias", "ambiguous", "unmapped")}
        for p in self.points:
            out[p.status] += 1
        out["points"] = len(self.points)
        return out

    def summary(self) -> str:
        """A short human-readable review: counts, then every alias/ambiguous point and the
        unmapped classes (with how many points carry each)."""
        c = self.counts()
        lines = [
            f"{c['mapped'] + c['alias']} of {c['points']} points mapped "
            f"({c['alias']} via non-standard aliases); {c['ambiguous']} ambiguous, "
            f"{c['unmapped']} with no CAMBER role"
        ]
        for status in ("alias", "ambiguous"):
            groups: dict = {}  # identical (class, role, note) collapse to one line
            for p in self.with_status(status):
                groups.setdefault((p.brick_class, p.role, p.note), []).append(p.point)
            for (cls, role, note), names in groups.items():
                shown = ", ".join(names[:3]) + (
                    f" and {len(names) - 3} more" if len(names) > 3 else ""
                )
                arrow = f" -> {role.value}" if role is not None else ""
                lines.append(f"  [{status}] {cls}{arrow} x{len(names)} ({shown}): {note}")
        unmapped: dict = {}
        for p in self.with_status("unmapped"):
            unmapped[p.brick_class] = unmapped.get(p.brick_class, 0) + 1
        for cls, n in sorted(unmapped.items()):
            lines.append(f"  [unmapped] {cls} x{n}")
        return "\n".join(lines)

    def as_dict(self) -> dict:
        """Plain-dict form (roles as slugs) for JSON output."""
        return {
            "counts": self.counts(),
            "points": [
                {
                    "point": p.point,
                    "brick_class": p.brick_class,
                    "role": p.role.value if p.role is not None else None,
                    "status": p.status,
                    "note": p.note,
                }
                for p in self.points
            ],
        }


def _name_tokens(name: str) -> set:
    """Lower-case word tokens of a point name (splits snake/kebab/camelCase and digits)."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", name)
    return {w for w in re.split(r"[^a-z]+", spaced.lower()) if w}


def _resolve_point(point: str, cls: str, owner_cls: str, owner_name: str) -> BrickPointMapping:
    """Resolve one point's class (plus owner context) to a :class:`BrickPointMapping`."""
    role, status, note = None, "unmapped", ""
    if cls in DIRECT_CLASS_TO_ROLE:
        role, status = DIRECT_CLASS_TO_ROLE[cls], "mapped"
    elif cls in ALIAS_CLASS_TO_ROLE:
        role, note = ALIAS_CLASS_TO_ROLE[cls]
        status = "alias"
    elif cls.startswith("Outdoor_") and "Outside_" + cls[8:] in DIRECT_CLASS_TO_ROLE:
        role, status = DIRECT_CLASS_TO_ROLE["Outside_" + cls[8:]], "alias"
        note = f"'Outdoor_' spelling of Brick's Outside_{cls[8:]}"
    elif cls in AMBIGUOUS_CLASSES:
        status, note = "ambiguous", AMBIGUOUS_CLASSES[cls]
    elif cls in _CONTEXT_CLASSES:
        role, note = _context_role(point, cls, owner_cls, owner_name)
        status = "mapped" if role is not None else ("ambiguous" if note else "unmapped")
    if role in _FLOW_ROLES and _name_tokens(point) & _SPEEDLIKE_TOKENS:
        # a flow class on a point named like a speed/percent: never feed it to a cfm/gpm rule
        note = (
            f"typed {cls} ({role.value}) but the point name reads as a speed/percent; not mapped "
            "-- check the unit (a 0-100 % series is not a volumetric flow)"
        )
        role, status = None, "ambiguous"
    return BrickPointMapping(point, cls, role, status, note)


def _is_point(subj: str, cls: str, owned: set) -> bool:
    if subj in owned:
        return True
    known = (
        cls in DIRECT_CLASS_TO_ROLE
        or cls in ALIAS_CLASS_TO_ROLE
        or cls in AMBIGUOUS_CLASSES
        or cls in _CONTEXT_CLASSES
        or cls in _COMMAND_FALLBACK
    )
    return known or cls.endswith(_POINT_SUFFIXES)


def report_from_triples(types: dict, has_point: dict) -> BrickMappingReport:
    """Per-point mapping report from parsed Brick triples (see :func:`brick_mapping_report`)."""
    owner: dict = {}
    for part, pts in has_point.items():
        for p in pts:
            owner.setdefault(p, part)
    owned = set(owner)
    results: dict = {}
    commands = []
    for subj, cls in types.items():
        if not _is_point(subj, cls, owned):
            continue
        if cls in _COMMAND_FALLBACK:
            commands.append((subj, cls))
            continue
        part = owner.get(subj)
        results[subj] = _resolve_point(subj, cls, types.get(part, ""), part or "")
    # commands: mapped only when their owner has no measured point with the same role
    for subj, cls in commands:
        part = owner.get(subj)
        r = _resolve_point(subj, _COMMAND_FALLBACK[cls], types.get(part, ""), part or "")
        siblings = {results[p].role for p in has_point.get(part, []) if p in results and p != subj}
        if r.role is not None and r.role not in siblings:
            results[subj] = BrickPointMapping(
                subj, cls, r.role, "mapped", "command used: the owner has no position feedback"
            )
        else:
            results[subj] = BrickPointMapping(subj, cls, None, "unmapped", "")
    return BrickMappingReport(tuple(results[k] for k in sorted(results)))


def roles_from_triples(types: dict, has_point: dict) -> dict:
    """Map point local-names to roles from parsed Brick triples.

    Standard classes and non-standard aliases (see :data:`ALIAS_CLASS_TO_ROLE`) map; ambiguous
    points do not. Command points (``*_Command``) map only as a fallback when their owner has no
    measured counterpart. Returns {point_name -> Role}; :func:`report_from_triples` says why each
    remaining point did not map.
    """
    return report_from_triples(types, has_point).roles


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
    Requires the ``brick`` extra (``pip install camber-toolkit[brick]``).
    """
    import rdflib

    g = rdflib.Graph()
    g.parse(data=ttl, format="turtle")
    types: dict = {}
    has_point: dict = {}
    for s, p, o in g:
        pl = _local(str(p))
        if pl == "type":  # rdf:type
            types[_local(str(s))] = _local(str(o))
        elif pl == "hasPoint":  # brick:hasPoint (any Brick version)
            has_point.setdefault(_local(str(s)), []).append(_local(str(o)))
    return types, has_point


def _parse(ttl: str, backend: str):
    """Dispatch to the requested parser backend; returns (types, has_point).

    A malformed model raises a clear ``ValueError`` rather than leaking the backend's own
    exception (rdflib ``BadSyntax``/``AssertionError``, or a parse error from the minimal reader).
    """
    if backend == "minimal":
        parser = parse_triples
    elif backend == "rdflib":
        if not _have_rdflib():
            raise ImportError(
                "rdflib not installed; `pip install camber-toolkit[brick]` or use backend='minimal'"
            )
        parser = parse_triples_rdflib
    elif backend == "auto":
        parser = parse_triples_rdflib if _have_rdflib() else parse_triples
    else:
        raise ValueError(f"unknown backend {backend!r} (use auto/rdflib/minimal)")
    try:
        return parser(ttl)
    except Exception as e:  # normalize any backend parse failure into a clear error
        raise ValueError(f"could not parse Brick/Turtle ({backend} backend): {e}") from e


def roles_from_brick(ttl: str, *, backend: str = "auto") -> dict:
    """Parse a Brick Turtle string and return {point_name -> Role}.

    ``backend``: ``"auto"`` uses rdflib if installed (robust, handles any Brick
    model) and falls back to the built-in minimal parser; ``"rdflib"`` forces the
    rdflib path (needs the ``brick`` extra); ``"minimal"`` forces the zero-dependency
    reader (good for the common, well-formed equipment/point models).
    """
    types, has_point = _parse(ttl, backend)
    return roles_from_triples(types, has_point)


def brick_mapping_report(ttl: str, *, backend: str = "auto") -> BrickMappingReport:
    """Parse a Brick model and report, per point, how it mapped -- and what did not.

    The companion to :func:`roles_from_brick` for onboarding review: non-standard classes accepted
    as aliases carry a caveat, ambiguous points (e.g. a flow-typed point named like a speed, a
    boiler on/off status that may be an enable) are listed with the reason they were **not**
    mapped, and classes with no CAMBER role are counted. ``report.summary()`` prints it.
    """
    types, has_point = _parse(ttl, backend)
    return report_from_triples(types, has_point)


def mapping_from_brick(ttl: str, *, backend: str = "auto") -> MappingProvider:
    """Build a :class:`MappingProvider` directly from a Brick Turtle model."""
    roles = roles_from_brick(ttl, backend=backend)
    return MappingProvider.from_dict(
        {"aliases": {name: role.value for name, role in roles.items()}}
    )


def _predicate_links(ttl: str, predicate: str, backend: str) -> dict:
    """Return ``{subject_local -> [object_local, ...]}`` for a predicate, via the chosen backend.

    A generic served-by-relation reader (``feeds`` / ``isFedBy``) that mirrors the two backends the
    rest of this module uses -- rdflib when available (any Turtle), else the zero-dependency minimal
    grammar of :func:`parse_triples` (which tracks only ``a`` / ``hasPoint``, so relations are read
    here). Local names only, so prefix spelling does not matter.
    """
    use_rdflib = backend == "rdflib" or (backend == "auto" and _have_rdflib())
    if use_rdflib:
        import rdflib

        g = rdflib.Graph()
        try:
            g.parse(data=ttl, format="turtle")
        except Exception as e:
            raise ValueError(f"could not parse Turtle ({backend} backend): {e}") from e
        out: dict = {}
        for s, p, o in g:
            if _local(str(p)) == predicate:
                out.setdefault(_local(str(s)), []).append(_local(str(o)))
        return out
    # minimal parser: same statement grammar as parse_triples
    lines = [
        ln.strip()
        for ln in ttl.splitlines()
        if ln.strip() and not ln.strip().startswith(("@prefix", "#", "@base"))
    ]
    out = {}
    for stmt in re.split(r"\s\.\s", " ".join(lines) + " "):
        stmt = stmt.strip().rstrip(".").strip()
        parts = stmt.split(None, 1)
        if len(parts) < 2:
            continue
        subj, rest = _local(parts[0]), parts[1]
        for grp in rest.split(";"):
            pp = grp.strip().split(None, 1)
            if len(pp) < 2:
                continue
            pred, objs = pp[0], [o.strip() for o in pp[1].split(",") if o.strip()]
            if pred.endswith(predicate):
                out.setdefault(subj, []).extend(_local(o) for o in objs)
    return out


def topology_from_brick(ttl: str, *, backend: str = "auto") -> Topology:
    """Build a served-by :class:`~camber.model.topology.Topology` from a Brick model.

    Reads ``brick:feeds`` (edge parent->child as-is) and ``brick:isFedBy`` (edge inverted), the
    authoritative flow relations, into a served-by graph with ``provenance="semantic"``. Containment
    (``hasPart``) is deliberately not treated as served-by. ``backend`` behaves as in
    :func:`roles_from_brick`. A model with no flow relations yields an empty topology.
    """
    edges: list = []
    for parent, children in _predicate_links(ttl, "feeds", backend).items():
        edges.extend((parent, child) for child in children)
    for child, parents in _predicate_links(ttl, "isFedBy", backend).items():
        edges.extend((parent, child) for parent in parents)
    return Topology.from_edges(edges, provenance="semantic")
