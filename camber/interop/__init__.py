"""Interop with building-ontology models (Brick / Haystack).

Derive CAMBER role mappings from a semantic model so an already-tagged building
needs no hand-written mapping. See :mod:`camber.interop.brick`.
"""

from .brick import mapping_from_brick, roles_from_brick
from .export import equip_haystack_tags, haystack_tags, to_brick
from .site_model import site_from_ttl, site_to_ttl

__all__ = ["mapping_from_brick", "roles_from_brick",
           "haystack_tags", "equip_haystack_tags", "to_brick",
           "site_to_ttl", "site_from_ttl"]
