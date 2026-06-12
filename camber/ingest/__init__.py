"""Ingest: source adapters (per-point/wide CSV, Haystack, SQL/historian, and read-only
network protocols — Modbus, MQTT, BACnet) plus data-quality assessment.

The CSV / Haystack / SQL adapters need no extra dependencies. The network-protocol adapters
(Modbus, MQTT, BACnet) are read-only by construction and import their protocol library lazily
behind optional extras — see ``docs/INGEST-PROTOCOLS.md`` and ``docs/SECURITY.md``. The
recommended ingest posture is historian/SQL/Haystack, not live OT polling.
"""

from .bacnet import (
    BacnetPoint, BacnetSource, BacnetTarget, READ_SERVICES, trendlog_to_series,
)
from .csv_perpoint import PerPointCsvAdapter
from .csv_wide import WideCsvAdapter
from .haystack import (
    HaystackAdapter, client_transport, http_json_transport, parse_his_grid,
)
from .modbus import ModbusPoint, ModbusSource, decode_registers
from .mqtt_stream import MqttPoint, MqttStreamSource, parse_payload
from .sql import SqlSource, read_points

__all__ = ["PerPointCsvAdapter", "WideCsvAdapter", "HaystackAdapter",
           "parse_his_grid", "http_json_transport", "client_transport",
           "SqlSource", "read_points",
           "ModbusSource", "ModbusPoint", "decode_registers",
           "MqttStreamSource", "MqttPoint", "parse_payload",
           "BacnetSource", "BacnetPoint", "BacnetTarget", "trendlog_to_series",
           "READ_SERVICES"]
