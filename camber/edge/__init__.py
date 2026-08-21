"""One-way cybersecure edge→cloud BAS forwarder.

Runs on a small edge device (Raspberry Pi) or a Windows BAS front-end: it reads BAS trend data
**read-only** (historian-first; live BACnet/BACnet-SC/Modbus/OPC-UA only when no historian exists),
maps point→role, quality-gates locally, serializes Parquet directly into the
:class:`~camber.store.ParquetStore` Hive layout, and **store-and-forwards it one-way** to an org
cloud data lake -- outbound HTTPS only, no inbound listener, no long-lived cloud credentials on the
edge (a presigned-URL sink is the default). All FDD/M&V analysis runs in the cloud on the landed
store. See ``docs/EDGE-DEPLOY.md`` for the IT/network-security approval dossier.
"""

from __future__ import annotations

from .config import EdgeConfig, build_forwarder, load_config
from .forwarder import BatchResult, Forwarder
from .sink import (
    AzureBlobSink,
    GcsSink,
    PresignedHttpsSink,
    S3Sink,
    Sink,
    collect_sink,
)
from .spool import DrainResult, Spool, SpoolEntry

__all__ = [
    "Sink",
    "PresignedHttpsSink",
    "S3Sink",
    "AzureBlobSink",
    "GcsSink",
    "collect_sink",
    "Spool",
    "SpoolEntry",
    "DrainResult",
    "Forwarder",
    "BatchResult",
    "EdgeConfig",
    "load_config",
    "build_forwarder",
]
