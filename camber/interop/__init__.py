"""Interop with building-ontology models (Brick / Haystack).

Derive CAMBER role mappings from a semantic model so an already-tagged building
needs no hand-written mapping. See :mod:`camber.interop.brick`.
"""

from .bacnet import mapping_from_bacnet, review_bacnet, roles_from_bacnet
from .brick import mapping_from_brick, roles_from_brick
from .export import equip_haystack_tags, haystack_tags, to_brick
from .haystack_semantic import mapping_from_haystack, role_from_tags, roles_from_haystack
from .semantic223 import (
    ROLE_TO_223,
    equip_to_223,
    role_223_quantity,
    site_from_223,
    site_to_223,
)
from .site_model import site_from_ttl, site_to_ttl

__all__ = [
    "mapping_from_brick",
    "roles_from_brick",
    "roles_from_bacnet",
    "mapping_from_bacnet",
    "review_bacnet",
    "haystack_tags",
    "equip_haystack_tags",
    "to_brick",
    "role_from_tags",
    "roles_from_haystack",
    "mapping_from_haystack",
    "site_to_ttl",
    "site_from_ttl",
    "site_to_223",
    "site_from_223",
    "equip_to_223",
    "role_223_quantity",
    "ROLE_TO_223",
]
