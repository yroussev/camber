"""Group a wide table's columns into equipment from a dataset's Brick model (``group: "brick"``).

Some datasets publish one wide table per *quantity* (every air handler's supply temperature in one
file, every damper in another) and ship a Brick model saying which point belongs to which
equipment. A run with ``"group": "brick"`` is split by that model instead of becoming one
equipment:

* each column is a Brick point (matched by local name, case-insensitively); its role comes from
  :func:`camber.interop.brick.brick_mapping_report` (mapped and alias points; ambiguous and
  unmapped points are left out);
* its equipment is the point's :attr:`~camber.interop.brick.BrickPointMapping.owner`, or the first
  entity up the ``hasPart`` / ``isPartOf`` containment chain, whose Brick class
  ``ingest.brick.equip_classes`` maps to a CAMBER class (``{"Rooftop_Unit": "AHU"}``); a point
  with no such owner goes to the run's own ``equip`` / ``class`` (e.g. building-level weather);
* **the dataset's mapping file wins**: its ``aliases`` (and ``patterns``) override the Brick role
  of a column, its ``"equipment": {column: equip}`` overrides the owner, and its
  ``"equipment_classes": {equip: class}`` overrides the class -- the curated mapping is where a
  wrong or missing Brick statement is corrected, and each override is reported.

Equipment ids are the owner's local name, plus ``__<scenario>`` only for a labelled run, so the
columns of several unlabelled per-quantity files merge into the same equipment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..interop.brick import brick_mapping_report, part_parents_from_brick
from ..model.mapping import MappingProvider
from ..model.roles import Role

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def safe_equip_id(name: str) -> str:
    """A store-safe equipment id from a Brick local name (``RTU 01/a`` -> ``RTU_01_a``)."""
    return _SAFE.sub("_", str(name)).strip("_") or "equip"


@dataclass
class PointGroup:
    """Where one column goes: equipment, CAMBER class and role (and why, for the notes)."""

    column: str
    equip: str
    equip_class: str
    role: Role
    source: str = "brick"  # "brick" | "mapping" (an override won) | "fallback" (no owner)


@dataclass
class BrickGrouping:
    """The column -> equipment/role plan for a Brick model plus mapping overrides."""

    points: dict = field(default_factory=dict)  # lower-cased point name -> PointGroup
    notes: list = field(default_factory=list)

    def plan(self, columns, *, default_equip: str, default_class: str, mapping, overrides: dict):
        """``{column: PointGroup}`` for the given table columns (unmapped columns left out)."""
        equip_over = {str(k).lower(): v for k, v in (overrides.get("equipment") or {}).items()}
        class_over = dict(overrides.get("equipment_classes") or {})
        out: dict = {}
        for col in columns:
            key = str(col).lower()
            hit = self.points.get(key)
            role = mapping.role_of(col) if mapping is not None else None
            src = "mapping" if role is not None else ""
            if role is None and hit is not None:
                role, src = hit.role, hit.source
            if role is None:
                continue
            if key in equip_over:
                equip, src = safe_equip_id(equip_over[key]), "mapping"
                cls = class_over.get(equip) or (hit.equip_class if hit else default_class)
            elif hit is not None and hit.source != "fallback":
                equip, cls = hit.equip, class_over.get(hit.equip, hit.equip_class)
            else:
                equip, cls = default_equip, class_over.get(default_equip, default_class)
                src = src if src == "mapping" else "fallback"
            out[col] = PointGroup(col, equip, cls, role, src)
        return out


def grouping_from_brick(ttl: str, equip_classes: dict, *, backend: str = "auto") -> BrickGrouping:
    """Resolve every Brick point's equipment (owner or containing part) and role."""
    report = brick_mapping_report(ttl, backend=backend)
    parents = part_parents_from_brick(ttl, backend=backend)
    classes = {p.owner: p.owner_class for p in report.points if p.owner}
    types = _types(ttl, backend)
    g = BrickGrouping()
    for p in report.points:
        if p.role is None:
            continue
        equip, cls = _equipment_of(p.owner, equip_classes, parents, {**types, **classes})
        if equip is None:
            g.points[p.point.lower()] = PointGroup(p.point, "", "", p.role, "fallback")
        else:
            g.points[p.point.lower()] = PointGroup(p.point, safe_equip_id(equip), cls, p.role)
    return g


def _types(ttl: str, backend: str) -> dict:
    from ..interop.brick import _parse

    return _parse(ttl, backend)[0]


def _equipment_of(owner: str, equip_classes: dict, parents: dict, types: dict):
    """``(entity, CAMBER class)`` of the first mapped class from ``owner`` up the containment."""
    seen: set = set()
    frontier = [owner] if owner else []
    while frontier:
        node = frontier.pop(0)
        if node in seen:
            continue
        seen.add(node)
        cls = equip_classes.get(types.get(node, ""))
        if cls:
            return node, cls
        frontier.extend(parents.get(node, []))
    return None, None


def mapping_overrides(mapping_spec: dict) -> tuple:
    """``(MappingProvider or None, overrides dict)`` from a dataset mapping file's JSON."""
    if not mapping_spec:
        return None, {}
    has_roles = mapping_spec.get("aliases") or mapping_spec.get("patterns")
    mp = MappingProvider.from_dict(mapping_spec) if has_roles else None
    over = {k: mapping_spec[k] for k in ("equipment", "equipment_classes") if k in mapping_spec}
    return mp, over
