"""ASHRAE 223P interop — a minimal, clean-room profile of the standard-223 semantic model.

ASHRAE Standard 223P is an RDF/SHACL semantic data model for building systems (equipment,
connections, media, and the physical *properties* they observe). The full standard is large,
SHACL-validated, and still maturing; this module exports/round-trips a deliberately **minimal
profile** of a CAMBER :class:`~camber.model.entities.Site` — equipment, their observable
properties, and each property's quantity-kind + medium derived from the vendor-neutral
:class:`~camber.model.roles.Role`. It is *not* a full-223P-conformance claim (that needs
validation against the published SHACL shapes); it makes CAMBER's model shareable with 223P
tooling at the equipment/property level.

No new dependency: serialization is plain Turtle and the reader is a minimal string parser, so
this works without rdflib (the existing ``brick`` extra still applies for heavier RDF work).
"""

from __future__ import annotations

from ..model.entities import Equip, Point, Site
from ..model.roles import Role

# Role -> (QUDT quantity-kind local name, 223P medium individual). A clean-room mapping using
# public QUDT / 223P vocabulary terms.
ROLE_TO_223 = {
    Role.OAT: ("Temperature", "Air"),
    Role.MIXED_AIR_TEMP: ("Temperature", "Air"),
    Role.RETURN_AIR_TEMP: ("Temperature", "Air"),
    Role.SUPPLY_AIR_TEMP: ("Temperature", "Air"),
    Role.SUPPLY_AIR_TEMP_SP: ("Temperature", "Air"),
    Role.SPACE_TEMP: ("Temperature", "Air"),
    Role.AIRFLOW: ("VolumeFlowRate", "Air"),
    Role.OA_AIRFLOW: ("VolumeFlowRate", "Air"),
    Role.AIRFLOW_SP: ("VolumeFlowRate", "Air"),
    Role.DUCT_STATIC: ("Pressure", "Air"),
    Role.DUCT_STATIC_SP: ("Pressure", "Air"),
    Role.CO2: ("MoleFraction", "Air"),
    Role.OUTDOOR_CO2: ("MoleFraction", "Air"),
    Role.OUTDOOR_RH: ("RelativeHumidity", "Air"),
    Role.CHW_FLOW: ("VolumeFlowRate", "Water"),
    Role.COOL_VALVE: ("PositionRatio", "Water"),
    Role.HEAT_VALVE: ("PositionRatio", "Water"),
    Role.OA_DAMPER: ("PositionRatio", "Air"),
    Role.DAMPER: ("PositionRatio", "Air"),
    Role.SUPPLY_FAN_SPEED: ("DimensionlessRatio", "Air"),
    Role.OCCUPANCY: ("Dimensionless", "Air"),
    # --- 0.6: plant / hydronic ---
    Role.CHW_SUPPLY_TEMP: ("Temperature", "Water"),
    Role.CHW_RETURN_TEMP: ("Temperature", "Water"),
    Role.CHW_SUPPLY_TEMP_SP: ("Temperature", "Water"),
    Role.CHW_DIFF_PRESS: ("Pressure", "Water"),
    Role.CHW_DIFF_PRESS_SP: ("Pressure", "Water"),
    Role.CHW_PUMP_SPEED: ("DimensionlessRatio", "Water"),
    Role.HW_SUPPLY_TEMP: ("Temperature", "Water"),
    Role.HW_RETURN_TEMP: ("Temperature", "Water"),
    Role.HW_DIFF_PRESS: ("Pressure", "Water"),
    Role.HW_PUMP_SPEED: ("DimensionlessRatio", "Water"),
    Role.CW_SUPPLY_TEMP: ("Temperature", "Water"),
    Role.CW_RETURN_TEMP: ("Temperature", "Water"),
    Role.TOWER_FAN_SPEED: ("DimensionlessRatio", "Air"),
    # --- 0.6: ambient / setpoints / humidity / filtration ---
    Role.WETBULB_TEMP: ("Temperature", "Air"),
    Role.COOL_SP: ("Temperature", "Air"),
    Role.HEAT_SP: ("Temperature", "Air"),
    Role.SUPPLY_AIR_HUMIDITY: ("RelativeHumidity", "Air"),
    Role.RETURN_AIR_HUMIDITY: ("RelativeHumidity", "Air"),
    Role.FILTER_DIFF_PRESS: ("Pressure", "Air"),
    # --- 0.6: energy / refrigerant-side ---
    Role.POWER: ("Power", "Electricity"),
    Role.ENERGY_RATE: ("Power", "Water"),
    Role.COND_APPROACH_TEMP: ("Temperature", "Refrigerant"),
    Role.EVAP_APPROACH_TEMP: ("Temperature", "Refrigerant"),
    Role.SUBCOOLING_TEMP: ("Temperature", "Refrigerant"),
    Role.SUPERHEAT_TEMP: ("Temperature", "Refrigerant"),
}

# Roles intentionally NOT in ROLE_TO_223: binary/enumerated status & command signals carry no QUDT
# quantity-kind — 223P models them as enumerated states, out of scope for this quantity/medium
# profile.
# (Kept explicit so the coverage is honest and a newly-added role can't be silently forgotten.)
_NO_223_QUANTITY = frozenset(
    {
        Role.BOILER_STATUS,
        Role.SUPPLY_FAN_STATUS,
        Role.WARMUP,
        Role.COOLDOWN,
        Role.ECON_CMD,
        Role.COMPRESSOR_STATUS,
        Role.COMPRESSOR_STAGE,
        Role.CONDENSER_FAN_STATUS,
        Role.HEAT_STAGE,
        Role.REVERSING_VALVE_CMD,
    }
)

S223_PREFIX = (
    "@prefix s223: <http://data.ashrae.org/standard223#> .\n"
    "@prefix qk: <http://qudt.org/vocab/quantitykind/> .\n"
    "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
    "@prefix bldg: <bldg#> .\n\n"
)


def role_223_quantity(role: Role):
    """``(quantity_kind, medium)`` for a role under this profile, or None if unmapped."""
    return ROLE_TO_223.get(role)


def equip_to_223(equip: Equip, *, profile: str = "minimal", include_relations: bool = True) -> str:
    """Turtle for one equipment + its observable properties (no prefix block)."""
    props, decls = [], []
    for p in equip.points:
        q = ROLE_TO_223.get(p.role)
        if q is None and profile == "minimal":
            continue
        qk, medium = q if q is not None else ("Dimensionless", "Air")
        pid = f"bldg:{equip.id}.{p.role.value}"
        props.append(pid)
        decls.append(
            f"{pid} a s223:Property ;\n"
            f"    s223:hasQuantityKind qk:{qk} ;\n"
            f"    s223:ofMedium s223:{medium} ."
        )
    head = [f"bldg:{equip.id} a s223:Equipment ;"]
    if equip.equip_class:
        head.append(f'    rdfs:label "{equip.equip_class}" ;')
    if include_relations and props:
        head.append("    s223:hasProperty " + ",\n        ".join(props) + " .")
    else:
        head[-1] = head[-1].rstrip(" ;") + " ."
    return "\n".join(["\n".join(head)] + decls)


def site_to_223(site: Site, *, profile: str = "minimal", include_relations: bool = True) -> str:
    """Serialize a whole site to a minimal-223P Turtle document.

    ``profile`` "minimal" emits only role-mapped properties; "full" also emits unmapped roles as
    a generic dimensionless property. ``include_relations`` toggles the equip→property edges.
    """
    body = [
        equip_to_223(e, profile=profile, include_relations=include_relations) for e in site.equips
    ]
    return S223_PREFIX + "\n\n".join(body) + "\n"


def _strip_comments(ttl: str):
    for ln in ttl.splitlines():
        s = ln.strip()
        if s and not s.startswith(("@prefix", "@base", "#")):
            yield s


def site_from_223(ttl: str, *, site_id: str = "") -> Site:
    """Parse a minimal-223P document (as emitted here) back into a :class:`Site`.

    Reconstructs equipment (with their ``equip_class`` from ``rdfs:label``) and the points whose
    roles are encoded in the property IRIs (``bldg:<equip>.<role>``). String-based; no rdflib.
    """
    slug_to_role = {r.value: r for r in Role}
    equips: dict = {}  # id -> {"class": str, "roles": set}
    current = None
    for s in _strip_comments(ttl):
        if s.startswith("bldg:") and " a s223:Equipment" in s:
            current = s.split()[0].split(":", 1)[1]
            equips.setdefault(current, {"class": "", "roles": set()})
        elif current and s.startswith("rdfs:label"):
            equips[current]["class"] = s.split('"')[1] if '"' in s else ""
        if s.startswith("bldg:") and " a s223:Property" in s:
            ref = s.split()[0].split(":", 1)[1]  # "<equip>.<role>"
            if "." in ref:
                eid, role_slug = ref.rsplit(".", 1)
                if role_slug in slug_to_role:
                    equips.setdefault(eid, {"class": "", "roles": set()})
                    equips[eid]["roles"].add(slug_to_role[role_slug])

    out = []
    for eid, info in equips.items():
        pts = tuple(
            Point(name=f"{eid}.{r.value}", role=r)
            for r in sorted(info["roles"], key=lambda x: x.value)
        )
        out.append(Equip(id=eid, equip_class=info["class"], points=pts, site=site_id))
    return Site(id=site_id, equips=tuple(out))
