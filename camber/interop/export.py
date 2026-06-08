"""Export the CAMBER entity model to building ontologies (Haystack, Brick).

The companion to :mod:`camber.interop.brick` (import). Given an equipment's roles,
emit Project Haystack tag sets (from the role tag hints) or a Brick (Turtle) model.
Brick export reproduces the equipment relationships an importer needs to recover the
roles -- valves are attached to a chilled-/hot-water coil, dampers to an outside
damper, fan signals to a supply fan -- so a model exported here re-imports to the
same roles (verified by a round-trip test).
"""

from __future__ import annotations

from ..model.roles import HAYSTACK_HINT, Role

# Direct role -> Brick point class (the inverse of brick.DIRECT_CLASS_TO_ROLE).
ROLE_TO_BRICK_POINT_CLASS = {
    Role.MIXED_AIR_TEMP: "Mixed_Air_Temperature_Sensor",
    Role.OAT: "Outside_Air_Temperature_Sensor",
    Role.RETURN_AIR_TEMP: "Return_Air_Temperature_Sensor",
    Role.SUPPLY_AIR_TEMP: "Supply_Air_Temperature_Sensor",
    Role.SUPPLY_AIR_TEMP_SP: "Supply_Air_Temperature_Setpoint",
    Role.DUCT_STATIC: "Supply_Air_Static_Pressure_Sensor",
    Role.DUCT_STATIC_SP: "Supply_Air_Static_Pressure_Setpoint",
    Role.AIRFLOW: "Supply_Air_Flow_Sensor",
    Role.SPACE_TEMP: "Zone_Air_Temperature_Sensor",
    Role.OCCUPANCY: "Occupancy_Status",
}

# Context roles -> (part name, part Brick class, point Brick class). The part is
# what lets an importer disambiguate the point's role.
ROLE_TO_BRICK_PART = {
    Role.COOL_VALVE: ("Cooling_Coil", "Chilled_Water_Coil", "Valve_Position_Sensor"),
    Role.HEAT_VALVE: ("Heating_Coil", "Hot_Water_Coil", "Valve_Position_Sensor"),
    Role.OA_DAMPER: ("Outdoor_Air_Damper", "Outside_Damper", "Damper_Position_Sensor"),
    Role.SUPPLY_FAN_SPEED: ("Supply_Air_Fan", "Fan", "Speed_status"),
    Role.SUPPLY_FAN_STATUS: ("Supply_Air_Fan", "Fan", "Fan_On_Off_Status"),
}

_PREFIX = ('@prefix bldg: <bldg#> .\n'
           '@prefix brick: <https://brickschema.org/schema/Brick#> .\n\n')


def haystack_tags(role: Role) -> frozenset:
    """Project-Haystack marker tags for a role (from its tag hint).

    e.g. ``Role.OAT`` -> {"outside", "air", "temp", "sensor"}. Empty if the role
    has no hint.
    """
    return frozenset(HAYSTACK_HINT.get(role, "").split())


def equip_haystack_tags(roles) -> dict:
    """Map each role to its Haystack tag set, for an equipment's role list."""
    return {r: haystack_tags(r) for r in roles}


def to_brick(equip_id: str, equip_class: str, roles, *,
             point_names: dict | None = None) -> str:
    """Emit a Brick (Turtle) model for one equipment and its roles.

    ``point_names`` optionally maps a role to the point's local name (default is
    the role slug). The output re-imports via :func:`camber.interop.brick` to the
    same set of roles.
    """
    point_names = point_names or {}
    direct_pts = []          # (point_name, point_class) attached straight to equip
    parts = {}               # part_name -> (part_class, [(point_name, point_class)])
    for r in roles:
        pname = point_names.get(r, r.value)
        if r in ROLE_TO_BRICK_POINT_CLASS:
            direct_pts.append((pname, ROLE_TO_BRICK_POINT_CLASS[r]))
        elif r in ROLE_TO_BRICK_PART:
            part_name, part_cls, pt_cls = ROLE_TO_BRICK_PART[r]
            parts.setdefault(part_name, (part_cls, []))[1].append((pname, pt_cls))

    lines = [_PREFIX]
    # equipment node with its parts and directly-attached points
    eq = [f"bldg:{equip_id} a brick:{equip_class}"]
    if parts:
        eq.append("    brick:hasPart " + ",\n        ".join(
            f"bldg:{p}" for p in sorted(parts)))
    if direct_pts:
        eq.append("    brick:hasPoint " + ",\n        ".join(
            f"bldg:{n}" for n, _ in direct_pts))
    lines.append(" ;\n".join(eq) + " .\n\n")
    # direct point typings
    for n, cls in direct_pts:
        lines.append(f"bldg:{n} a brick:{cls} .\n")
    # parts and their points
    for part_name in sorted(parts):
        part_cls, pts = parts[part_name]
        lines.append(f"\nbldg:{part_name} a brick:{part_cls} ;\n"
                     "    brick:hasPoint " + ",\n        ".join(
                         f"bldg:{n}" for n, _ in pts) + " .\n")
        for n, cls in pts:
            lines.append(f"bldg:{n} a brick:{cls} .\n")
    return "".join(lines)
