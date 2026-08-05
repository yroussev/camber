"""Time-series storage (capability-map §6): persist points keyed to entities.

Per-file CSV loads are fine for one building analyzed once; they do not scale to a
portfolio re-read every run. This package persists normalized point history to a
columnar (Parquet) store keyed to the semantic entity model, so analytics read
"these roles for these equipment over this range" with predicate pushdown instead
of re-parsing raw exports. Each facility is identified by a stable, path-safe
``facility_id`` (see :mod:`camber.store.facilities`), decoupled from its display name.
"""

from .facilities import (
    FacilityRegistry,
    make_facility_id,
    migrate_site_to_facility,
    require_facility_id,
    valid_facility_id,
)
from .parquet_store import ParquetStore, PointKey, role_frame_to_long

__all__ = [
    "ParquetStore",
    "PointKey",
    "role_frame_to_long",
    "make_facility_id",
    "valid_facility_id",
    "require_facility_id",
    "FacilityRegistry",
    "migrate_site_to_facility",
]
