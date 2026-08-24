"""Model: equipment/point entities, roles, and point-to-role mapping.

This is the semantic core. ``__all__`` is the curated package-level API; the submodules
(``camber.model.roles`` / ``.entities`` / ``.mapping``) remain importable directly.
"""

from .entities import (
    TEMPLATES,
    Completeness,
    Equip,
    EquipTemplate,
    Point,
    Runnable,
    Site,
    Space,
    completeness,
    runnable_rules,
    template_for,
)
from .mapping import MappingProvider
from .roles import HAYSTACK_HINT, STATUS_ROLES, Role
from .topology import Topology

__all__ = [
    "Role",
    "STATUS_ROLES",
    "HAYSTACK_HINT",
    "Point",
    "Equip",
    "Space",
    "Site",
    "Topology",
    "EquipTemplate",
    "TEMPLATES",
    "template_for",
    "Completeness",
    "completeness",
    "Runnable",
    "runnable_rules",
    "MappingProvider",
]
