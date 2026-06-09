"""Semantic entity model: sites, equipment, points, and model-completeness.

``model.roles`` says what a single point *means*. This module says how points
roll up into equipment, equipment into a site, and -- the load-bearing part --
whether the roles actually resolvable for a piece of equipment are *enough* to
run a given analytic.

That last capability is **model-completeness validation**. Without it, every rule
has to defend itself against a missing input, and an under-instrumented building
either crashes a run or silently produces nonsense. With it, the engine can ask
"can this rule run on this equipment, and if not, what's missing?" before it
tries -- the precondition for "one rule, all equipment, any building."

The entities are deliberately minimal and ontology-compatible (they map onto a
Haystack ``site``/``equip``/``point`` shape later) but carry no Haystack
dependency. This module imports only :mod:`camber.model.roles`; the
completeness/runnable helpers duck-type on rule objects (``.name`` /
``.roles_required``) so there is no import cycle with the rules layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .roles import Role


@dataclass(frozen=True)
class Point:
    """One measured/commanded stream, tagged with its vendor-neutral role."""

    name: str            # raw source token, e.g. "AHU_1_HHW_Valve"
    role: Role
    unit: str = ""


@dataclass(frozen=True)
class Equip:
    """A piece of equipment and the points resolved onto it."""

    id: str              # equip token incl. id, e.g. "AHU_1" / "VAV_117"
    equip_class: str     # "AHU", "VAV", "HotWaterPlant", ...
    points: tuple = ()   # tuple[Point]
    site: str = ""
    space: str = ""

    def roles(self) -> frozenset:
        """The set of roles present on this equipment."""
        return frozenset(p.role for p in self.points)

    @classmethod
    def from_roles(cls, id: str, equip_class: str, roles, **kw) -> "Equip":
        """Build an Equip when only the present roles are known (no point names).

        Convenience for the common case where ``resolve`` has produced a
        role-named frame and the caller wants an entity to validate against a
        template. Point names are synthesized as ``<id>:<role>``.
        """
        pts = tuple(Point(name=f"{id}:{r.value}", role=r) for r in roles)
        return cls(id=id, equip_class=equip_class, points=pts, **kw)


@dataclass(frozen=True)
class Space:
    """A space/zone within a site (floor, room, thermal zone)."""

    id: str
    site: str = ""


@dataclass(frozen=True)
class Site:
    """A building: its equipment and a bit of context."""

    id: str
    climate_zone: str = ""           # e.g. "CA CZ15"
    equips: tuple = ()               # tuple[Equip]
    spaces: tuple = ()               # tuple[Space]

    def of_class(self, equip_class: str) -> tuple:
        """All equipment of a given class."""
        return tuple(e for e in self.equips if e.equip_class == equip_class)

    def equip(self, id: str):
        """Look up one equipment by id, or None."""
        for e in self.equips:
            if e.id == id:
                return e
        return None


# --------------------------------------------------------------------------- #
# Equipment templates: the roles a fully-instrumented instance of a class is
# expected to expose. "required" are the roles the class is *defined* by (an AHU
# without a supply-air temp is barely an AHU); "optional" enrich analytics but
# their absence is normal on a leanly-instrumented building. Templates document
# the expected point list independently of any one rule's needs -- a rule may
# require a subset, and a building may exceed a template.
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class EquipTemplate:
    """The expected role inventory for an equipment class."""

    equip_class: str
    required: frozenset       # frozenset[Role]
    optional: frozenset = frozenset()

    def expected(self) -> frozenset:
        """All roles the class may have (required plus optional)."""
        return self.required | self.optional


_TEMPLATE_LIST = (
    EquipTemplate(
        "AHU",
        required=frozenset({Role.SUPPLY_AIR_TEMP, Role.HEAT_VALVE, Role.COOL_VALVE}),
        optional=frozenset({
            Role.MIXED_AIR_TEMP, Role.RETURN_AIR_TEMP, Role.OA_DAMPER,
            Role.SUPPLY_AIR_TEMP_SP, Role.DUCT_STATIC, Role.DUCT_STATIC_SP,
            Role.SUPPLY_FAN_STATUS, Role.SUPPLY_FAN_SPEED, Role.OAT, Role.OCCUPANCY,
            Role.ECON_CMD,
        }),
    ),
    EquipTemplate(
        "VAV",
        required=frozenset({Role.SPACE_TEMP, Role.DAMPER}),
        optional=frozenset({
            Role.HEAT_VALVE, Role.AIRFLOW, Role.AIRFLOW_SP, Role.COOL_SP,
            Role.HEAT_SP, Role.OCCUPANCY,
        }),
    ),
    EquipTemplate(
        "HotWaterPlant",
        required=frozenset({Role.HW_SUPPLY_TEMP, Role.BOILER_STATUS}),
        optional=frozenset({
            Role.HW_RETURN_TEMP, Role.HW_DIFF_PRESS, Role.OAT, Role.ENERGY_RATE,
        }),
    ),
    EquipTemplate(
        "ChilledWaterPlant",
        required=frozenset({Role.CHW_SUPPLY_TEMP}),
        optional=frozenset({
            Role.CHW_RETURN_TEMP, Role.CHW_SUPPLY_TEMP_SP, Role.CHW_DIFF_PRESS,
            Role.CHW_DIFF_PRESS_SP, Role.CHW_PUMP_SPEED, Role.OAT, Role.ENERGY_RATE,
        }),
    ),
    EquipTemplate(
        "Chiller",
        required=frozenset({Role.POWER, Role.CHW_SUPPLY_TEMP, Role.CHW_RETURN_TEMP,
                            Role.CHW_FLOW}),
        optional=frozenset({Role.CHW_SUPPLY_TEMP_SP, Role.OAT, Role.ENERGY_RATE}),
    ),
    EquipTemplate(
        "CoolingTower",
        required=frozenset({Role.CW_SUPPLY_TEMP}),
        optional=frozenset({Role.CW_RETURN_TEMP, Role.WETBULB_TEMP, Role.OAT,
                            Role.OUTDOOR_RH, Role.TOWER_FAN_SPEED, Role.POWER}),
    ),
    EquipTemplate(
        "Meter",
        required=frozenset({Role.POWER}),
        optional=frozenset({Role.ENERGY_RATE}),
    ),
)

# CAV / FCAV are terminal boxes that share the VAV template (same role shape).
TEMPLATES: dict = {t.equip_class: t for t in _TEMPLATE_LIST}
TEMPLATES["CAV"] = EquipTemplate("CAV", TEMPLATES["VAV"].required,
                                 TEMPLATES["VAV"].optional)
TEMPLATES["FCAV"] = EquipTemplate("FCAV", TEMPLATES["VAV"].required,
                                  TEMPLATES["VAV"].optional)


def template_for(equip_class: str):
    """The template for a class, or None if the class is not modeled."""
    return TEMPLATES.get(equip_class)


# --------------------------------------------------------------------------- #
# Model-completeness validation
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Completeness:
    """How well a piece of equipment matches its template."""

    equip: str
    equip_class: str
    present: frozenset                 # roles resolvable on the equip
    missing_required: frozenset        # template-required roles not present
    missing_optional: frozenset        # template-optional roles not present
    unexpected: frozenset              # present roles the template didn't list
    has_template: bool

    @property
    def ready(self) -> bool:
        """True if every template-required role is present."""
        return self.has_template and not self.missing_required

    @property
    def score(self) -> float:
        """Fraction of expected (required+optional) roles present, 0..1.

        1.0 when the template is fully instrumented; 0.0 when none of the
        expected roles are present (or there is no template to score against).
        """
        if not self.has_template:
            return 0.0
        expected = TEMPLATES[self.equip_class].expected()
        if not expected:
            return 0.0
        return len(self.present & expected) / len(expected)


def completeness(equip_class: str, present_roles) -> Completeness:
    """Compare the roles present on an equipment to its template.

    ``present_roles`` is any iterable of :class:`Role`. Works whether the roles
    came from a resolved frame, an :class:`Equip`, or a discovery scan.
    """
    present = frozenset(present_roles)
    tmpl = TEMPLATES.get(equip_class)
    if tmpl is None:
        return Completeness(equip="", equip_class=equip_class, present=present,
                            missing_required=frozenset(), missing_optional=frozenset(),
                            unexpected=present, has_template=False)
    return Completeness(
        equip="", equip_class=equip_class, present=present,
        missing_required=tmpl.required - present,
        missing_optional=tmpl.optional - present,
        unexpected=present - tmpl.expected(),
        has_template=True,
    )


@dataclass(frozen=True)
class Runnable:
    """Whether one rule can run on an equipment, and what blocks it."""

    rule: str
    can_run: bool
    missing_required: frozenset        # required roles not present (blocks the run)
    missing_optional: frozenset        # optional roles not present (degrades only)


def runnable_rules(present_roles, rules) -> list:
    """For each rule, decide whether the present roles satisfy its requirements.

    ``rules`` is any iterable of rule objects exposing ``name`` /
    ``roles_required`` (and optionally ``roles_optional``) -- duck-typed so this
    module needs no dependency on the rules package. Returns one
    :class:`Runnable` per rule, in input order. This is the engine-side answer to
    "which analytics can this building's instrumentation support?"
    """
    present = frozenset(present_roles)
    out = []
    for r in rules:
        required = frozenset(getattr(r, "roles_required", ()))
        optional = frozenset(getattr(r, "roles_optional", ()))
        miss_req = required - present
        out.append(Runnable(
            rule=getattr(r, "name", r.__class__.__name__),
            can_run=not miss_req,
            missing_required=miss_req,
            missing_optional=optional - present,
        ))
    return out
