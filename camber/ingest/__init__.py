"""Ingest: source adapters (per-point/wide CSV, Haystack, SQL/historian, and read-only
network protocols — Modbus, MQTT, BACnet) plus data-quality assessment.

The CSV / Haystack / SQL adapters need no extra dependencies. The network-protocol adapters
(Modbus, MQTT, BACnet) are read-only by construction and import their protocol library lazily
behind optional extras — see ``docs/INGEST-PROTOCOLS.md`` and ``docs/SECURITY.md``. The
recommended ingest posture is historian/SQL/Haystack, not live OT polling.
"""

from .bacnet import (
    READ_SERVICES,
    BacnetPoint,
    BacnetSource,
    BacnetTarget,
    trendlog_to_series,
)
from .bacnet_client import (
    BacnetClientConfig,
    Bacpypes3Client,
    bacnet_discovery_client,
    bacnet_read_client,
    discover_default,
)
from .bacnet_discovery import (
    DISCOVERY_SERVICES,
    BacnetPointRecord,
    DiscoveredDevice,
    DiscoveredObject,
    DiscoveryClient,
    discover,
    discover_addresses,
    discovery_to_inventory,
    discovery_to_points,
)
from .csv_long import LongCsvAdapter
from .csv_perpoint import PerPointCsvAdapter
from .csv_wide import WideCsvAdapter
from .haystack import (
    HaystackAdapter,
    client_transport,
    http_json_transport,
    parse_his_grid,
    phable_transport,
)
from .modbus import ModbusPoint, ModbusSource, decode_registers
from .mqtt_stream import MqttPoint, MqttStreamSource, parse_payload
from .opcua import OpcUaPoint, OpcUaSecurity, OpcUaSource, history_to_series
from .profiles import PROFILES, IngestProfile, get_profile
from .quality import (
    CleaningLog,
    QualityReport,
    assess,
    clean,
    gap_count,
    infer_freq,
    longest_flatline,
    outlier_mask,
)
from .sql import SqlSource, read_points

__all__ = [
    "QualityReport",
    "assess",
    "CleaningLog",
    "clean",
    "infer_freq",
    "gap_count",
    "longest_flatline",
    "outlier_mask",
    "PerPointCsvAdapter",
    "WideCsvAdapter",
    "LongCsvAdapter",
    "IngestProfile",
    "PROFILES",
    "get_profile",
    "HaystackAdapter",
    "parse_his_grid",
    "http_json_transport",
    "client_transport",
    "phable_transport",
    "SqlSource",
    "read_points",
    "ModbusSource",
    "ModbusPoint",
    "decode_registers",
    "MqttStreamSource",
    "MqttPoint",
    "parse_payload",
    "BacnetSource",
    "BacnetPoint",
    "BacnetTarget",
    "trendlog_to_series",
    "discover",
    "discover_addresses",
    "discovery_to_points",
    "discovery_to_inventory",
    "DiscoveredDevice",
    "DiscoveredObject",
    "DiscoveryClient",
    "BacnetPointRecord",
    "DISCOVERY_SERVICES",
    "Bacpypes3Client",
    "BacnetClientConfig",
    "bacnet_read_client",
    "bacnet_discovery_client",
    "discover_default",
    "OpcUaSource",
    "OpcUaPoint",
    "OpcUaSecurity",
    "history_to_series",
    "READ_SERVICES",
]
