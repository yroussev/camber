"""Time-series storage (capability-map §6): persist points keyed to entities.

Per-file CSV loads are fine for one building analyzed once; they do not scale to a
portfolio re-read every run. This package persists normalized point history to a
columnar (Parquet) store keyed to the semantic entity model, so analytics read
"these roles for these equipment over this range" with predicate pushdown instead
of re-parsing raw exports.
"""

from .parquet_store import ParquetStore, role_frame_to_long

__all__ = ["ParquetStore", "role_frame_to_long"]
