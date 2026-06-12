"""Ingest: source adapters (per-point/wide CSV, Haystack) and data-quality assessment."""

from .csv_perpoint import PerPointCsvAdapter
from .csv_wide import WideCsvAdapter
from .haystack import (
    HaystackAdapter, client_transport, http_json_transport, parse_his_grid,
)
from .sql import SqlSource, read_points

__all__ = ["PerPointCsvAdapter", "WideCsvAdapter", "HaystackAdapter",
           "parse_his_grid", "http_json_transport", "client_transport",
           "SqlSource", "read_points"]
