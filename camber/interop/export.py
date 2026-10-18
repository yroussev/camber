"""Export the CAMBER entity model to building ontologies (Haystack, Brick).

The companion to :mod:`camber.interop.brick` (import). Given an equipment's roles,
emit Project Haystack tag sets (from the role tag hints) or a Brick (Turtle) model.
Brick export reproduces the equipment relationships an importer needs to recover the
roles -- valves are attached to a chilled-/hot-water coil, dampers to an outside
damper, fan signals to a supply fan -- so a model exported here re-imports to the
same roles (verified by a round-trip test).
"""

from __future__ import annotations

import re

from ..model.roles import HAYSTACK_HINT, Role

# Direct role -> Brick point class. Every class here must import back to the same role through
# brick.DIRECT_CLASS_TO_ROLE (tests/test_export.py enumerates both tables so they cannot drift).
ROLE_TO_BRICK_POINT_CLASS = {
    Role.MIXED_AIR_TEMP: "Mixed_Air_Temperature_Sensor",
    Role.OAT: "Outside_Air_Temperature_Sensor",
    Role.RETURN_AIR_TEMP: "Return_Air_Temperature_Sensor",
    Role.SUPPLY_AIR_TEMP: "Supply_Air_Temperature_Sensor",
    Role.SUPPLY_AIR_TEMP_SP: "Supply_Air_Temperature_Setpoint",
    Role.DUCT_STATIC: "Supply_Air_Static_Pressure_Sensor",
    Role.DUCT_STATIC_SP: "Supply_Air_Static_Pressure_Setpoint",
    Role.AIRFLOW: "Supply_Air_Flow_Sensor",
    Role.AIRFLOW_SP: "Supply_Air_Flow_Setpoint",
    Role.OA_AIRFLOW: "Outside_Air_Flow_Sensor",
    Role.SPACE_TEMP: "Zone_Air_Temperature_Sensor",
    Role.OCCUPANCY: "Occupancy_Status",
    Role.CO2: "CO2_Sensor",
    Role.OUTDOOR_CO2: "Outside_Air_CO2_Sensor",
    Role.OUTDOOR_RH: "Outside_Air_Humidity_Sensor",
    Role.WETBULB_TEMP: "Outside_Air_Wet_Bulb_Temperature_Sensor",
    Role.SUPPLY_AIR_HUMIDITY: "Supply_Air_Humidity_Sensor",
    Role.RETURN_AIR_HUMIDITY: "Return_Air_Humidity_Sensor",
    Role.FILTER_DIFF_PRESS: "Filter_Differential_Pressure_Sensor",
    Role.COOL_SP: "Zone_Air_Cooling_Temperature_Setpoint",
    Role.HEAT_SP: "Zone_Air_Heating_Temperature_Setpoint",
    Role.HW_SUPPLY_TEMP: "Hot_Water_Supply_Temperature_Sensor",
    Role.HW_RETURN_TEMP: "Hot_Water_Return_Temperature_Sensor",
    Role.HW_DIFF_PRESS: "Hot_Water_Differential_Pressure_Sensor",
    Role.HW_DIFF_PRESS_SP: "Hot_Water_Differential_Pressure_Setpoint",
    Role.HW_FLOW: "Hot_Water_Flow_Sensor",
    Role.CHW_SUPPLY_TEMP: "Chilled_Water_Supply_Temperature_Sensor",
    Role.CHW_RETURN_TEMP: "Chilled_Water_Return_Temperature_Sensor",
    Role.CHW_SUPPLY_TEMP_SP: "Chilled_Water_Supply_Temperature_Setpoint",
    Role.CHW_DIFF_PRESS: "Chilled_Water_Differential_Pressure_Sensor",
    Role.CHW_DIFF_PRESS_SP: "Chilled_Water_Differential_Pressure_Setpoint",
    Role.CHW_FLOW: "Chilled_Water_Flow_Sensor",
    Role.CW_SUPPLY_TEMP: "Entering_Condenser_Water_Temperature_Sensor",
    Role.CW_RETURN_TEMP: "Leaving_Condenser_Water_Temperature_Sensor",
    Role.CW_FLOW: "Condenser_Water_Flow_Sensor",  # 0.106 (#184)
}

# Context roles -> (part name, part Brick class, point Brick class). The part is
# what lets an importer disambiguate the point's role.
ROLE_TO_BRICK_PART = {
    Role.COOL_VALVE: ("Cooling_Coil", "Chilled_Water_Coil", "Valve_Position_Sensor"),
    Role.HEAT_VALVE: ("Heating_Coil", "Hot_Water_Coil", "Valve_Position_Sensor"),
    # 0.98 (#85): HEAT_VALVE_POSITION (a heating valve's measured position, trended beside its
    # demand) is deliberately NOT exported as a Brick point. The importer maps a heating coil's
    # Valve_Position_Sensor to HEAT_VALVE (a position feedback wins over a command), so a lone
    # exported position would come back as HEAT_VALVE; the Haystack export carries it
    # ("heating valve sensor", which imports back to the same role).
    Role.OA_DAMPER: ("Outdoor_Air_Damper", "Outside_Damper", "Damper_Position_Sensor"),
    Role.SUPPLY_FAN_SPEED: ("Supply_Air_Fan", "Fan", "Speed_status"),
    Role.SUPPLY_FAN_STATUS: ("Supply_Air_Fan", "Fan", "Fan_On_Off_Status"),
}

_PREFIX = (
    "@prefix bldg: <bldg#> .\n"
    "@prefix brick: <https://brickschema.org/schema/Brick#> .\n"
    "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n\n"
)

# 0.108 (#254): the characters a name keeps as-is in a ``bldg:`` local name. Everything else --
# spaces, ``/``, ``:``, ``.``, ``%``, non-ASCII -- is percent-encoded (UTF-8), which Turtle's
# PN_LOCAL allows, so any point or equipment name gives a valid prefixed name. The importer
# (:func:`camber.interop.brick._local`) decodes it, and ``rdfs:label`` carries the exact name.
_SAFE_LOCAL = re.compile(r"[A-Za-z0-9_-]")
_PART_SEP = "."  # never in an encoded name, so a part IRI never collides with a point's


def _brick_local(name) -> str:
    """``name`` as a valid Turtle local name (``bldg:<this>``): ``[A-Za-z0-9_-]`` kept, every
    other character percent-encoded (a leading ``-`` too). ``"AHU 1/SA-T"`` ->
    ``"AHU%201%2FSA-T"``; :func:`camber.interop.brick._local` decodes it back (0.108, #254)."""
    out = []
    for i, ch in enumerate(str(name)):
        if _SAFE_LOCAL.fullmatch(ch) and not (i == 0 and ch == "-"):
            out.append(ch)
        else:
            out.append("".join(f"%{b:02X}" for b in ch.encode("utf-8")))
    return "".join(out)


def _turtle_literal(text) -> str:
    """``text`` as a Turtle string literal that the minimal reader cannot mis-split: letters,
    digits, space, ``_`` and ``-`` as-is, everything else as a ``\\uXXXX`` escape (0.108)."""
    out = []
    for ch in str(text):
        if _SAFE_LOCAL.fullmatch(ch) or ch == " ":
            out.append(ch)
        elif ord(ch) > 0xFFFF:
            out.append(f"\\U{ord(ch):08X}")
        else:
            out.append(f"\\u{ord(ch):04X}")
    return '"' + "".join(out) + '"'


def _part_local(equip_id, part_name: str) -> str:
    """The local name of an equipment's part node, scoped by the equipment so two air handlers
    never share one coil, damper or fan (0.108, #253): ``AHU_1.Cooling_Coil``."""
    return f"{_brick_local(equip_id)}{_PART_SEP}{part_name}"


def haystack_tags(role: Role) -> frozenset:
    """Project-Haystack marker tags for a role (from its tag hint).

    e.g. ``Role.OAT`` -> {"outside", "air", "temp", "sensor"}. Empty if the role
    has no hint.
    """
    return frozenset(HAYSTACK_HINT.get(role, "").split())


def equip_haystack_tags(roles) -> dict:
    """Map each role to its Haystack tag set, for an equipment's role list."""
    return {r: haystack_tags(r) for r in roles}


def to_brick(equip_id: str, equip_class: str, roles, *, point_names: dict | None = None) -> str:
    """Emit a Brick (Turtle) model for one equipment and its roles.

    ``point_names`` optionally maps a role to the point's name (default is the role
    slug). The output re-imports via :func:`camber.interop.brick` to the same set of
    roles.

    0.108: names are percent-encoded into valid local names (:func:`_brick_local`, #254) and
    carried exactly as ``rdfs:label``; part nodes are scoped by the equipment
    (:func:`_part_local`, #253), so several equipment export into one model without sharing
    a coil, damper or fan.
    """
    point_names = point_names or {}
    direct_pts = []  # (point_name, point_class) attached straight to equip
    parts: dict = {}  # part_name -> (part_class, [(point_name, point_class)])
    for r in roles:
        pname = point_names.get(r, r.value)
        if r in ROLE_TO_BRICK_POINT_CLASS:
            direct_pts.append((pname, ROLE_TO_BRICK_POINT_CLASS[r]))
        elif r in ROLE_TO_BRICK_PART:
            part_name, part_cls, pt_cls = ROLE_TO_BRICK_PART[r]
            parts.setdefault(part_name, (part_cls, []))[1].append((pname, pt_cls))

    def iri(name) -> str:
        return f"bldg:{_brick_local(name)}"

    def label(name) -> str:
        return f"    rdfs:label {_turtle_literal(name)}"

    lines = [_PREFIX]
    # equipment node with its parts and directly-attached points
    eq = [f"{iri(equip_id)} a brick:{equip_class}", label(equip_id)]
    if parts:
        eq.append(
            "    brick:hasPart "
            + ",\n        ".join(f"bldg:{_part_local(equip_id, p)}" for p in sorted(parts))
        )
    if direct_pts:
        eq.append("    brick:hasPoint " + ",\n        ".join(iri(n) for n, _ in direct_pts))
    lines.append(" ;\n".join(eq) + " .\n\n")
    # direct point typings
    for n, cls in direct_pts:
        lines.append(f"{iri(n)} a brick:{cls} ;\n{label(n)} .\n")
    # parts and their points
    for part_name in sorted(parts):
        part_cls, pts = parts[part_name]
        lines.append(
            f"\nbldg:{_part_local(equip_id, part_name)} a brick:{part_cls} ;\n"
            "    brick:hasPoint " + ",\n        ".join(iri(n) for n, _ in pts) + " .\n"
        )
        for n, cls in pts:
            lines.append(f"{iri(n)} a brick:{cls} ;\n{label(n)} .\n")
    return "".join(lines)
