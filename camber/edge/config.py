"""Edge configuration: a small non-secret file, overlaid by environment variables.

The config file describes *what* to forward (facility id, source kind, sink kind + allowlisted host,
mapping, cadence). **Secrets never live in it** -- presigned URLs, tokens, passwords, and cloud keys
come only from environment variables (or a secret manager that populates them). :func:`load_config`
enforces that by rejecting any secret-shaped key or presigned URL found in the file, so "no secrets
in config" is a tested invariant, not documentation. Mirrors the env-var config style of
:mod:`camber.api.server` (``CAMBER_*``).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

_DEFAULT_MAX_BYTES = 2 * 1024**3

# Key substrings that must never appear in the config file -- these are secrets, env-only.
_SECRET_KEY_HINTS = (
    "token",
    "secret",
    "password",
    "passwd",
    "credential",
    "private_key",
    "access_key",
    "apikey",
    "api_key",
    "connection_string",
    "sas",
)
# Value fragments that mark a string as a presigned/authenticated URL (a secret).
_SECRET_VALUE_HINTS = (
    "x-amz-signature",
    "awsaccesskeyid",
    "signature=",
    "sig=",
    "sharedaccesssignature",
    "googleaccessid",
    "se=",  # Azure SAS expiry marker in combination below
)


def _scan_secrets(obj, path: str = "") -> None:
    """Raise if the config tree carries a secret-shaped key or a presigned-URL value."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            if any(h in kl for h in _SECRET_KEY_HINTS):
                raise ValueError(
                    f"secrets must come from environment variables, not the config file: "
                    f"{path + str(k)!r}"
                )
            _scan_secrets(v, f"{path}{k}.")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _scan_secrets(v, f"{path}{i}.")
    elif isinstance(obj, str):
        vl = obj.lower()
        if (
            ("x-amz-signature" in vl)
            or ("sharedaccesssignature" in vl)
            or ("signature=" in vl and "https://" in vl)
        ):
            raise ValueError(
                f"a presigned/authenticated URL is a secret; set it via the environment, not "
                f"the config file (at {path.rstrip('.')!r})"
            )


@dataclass(frozen=True)
class EdgeConfig:
    """A validated edge forwarder configuration (no secrets)."""

    facility_id: str
    source: dict = field(default_factory=dict)
    sink: dict = field(default_factory=dict)
    mapping: dict | None = None
    resample: str = "1h"
    interval: float = 3600.0
    spool_dir: str = "./camber-edge-spool"
    spool_max_bytes: int = _DEFAULT_MAX_BYTES
    quality: bool = True
    wire_format: str = "parquet"


def load_config(path: str | None = None, *, environ=None) -> EdgeConfig:
    """Load the JSON config (rejecting secrets) and overlay ``CAMBER_EDGE_*`` env vars."""
    environ = os.environ if environ is None else environ
    path = path or environ.get("CAMBER_EDGE_CONFIG")
    raw: dict = {}
    if path:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        _scan_secrets(raw)

    facility_id = environ.get("CAMBER_EDGE_FACILITY_ID") or raw.get("facility_id")
    if not facility_id:
        raise ValueError(
            "facility_id is required (config 'facility_id' or CAMBER_EDGE_FACILITY_ID)"
        )

    return EdgeConfig(
        facility_id=facility_id,
        source=raw.get("source", {}),
        sink=raw.get("sink", {}),
        mapping=raw.get("mapping"),
        resample=environ.get("CAMBER_EDGE_RESAMPLE") or raw.get("resample", "1h"),
        interval=float(environ.get("CAMBER_EDGE_INTERVAL") or raw.get("interval", 3600.0)),
        spool_dir=environ.get("CAMBER_EDGE_SPOOL_DIR")
        or raw.get("spool_dir", "./camber-edge-spool"),
        spool_max_bytes=int(raw.get("spool_max_bytes", _DEFAULT_MAX_BYTES)),
        quality=bool(raw.get("quality", True)),
        wire_format=environ.get("CAMBER_EDGE_WIRE_FORMAT") or raw.get("wire_format", "parquet"),
    )


def _build_sink(spec: dict, environ):
    """Construct a sink from ``spec['kind']``; presigned URLs come from the environment."""
    from .sink import AzureBlobSink, GcsSink, PresignedHttpsSink, S3Sink, collect_sink

    kind = spec.get("kind", "presigned")
    if kind == "collect":
        return collect_sink()[0]
    if kind == "presigned":
        url_template = environ.get("CAMBER_EDGE_SINK_URL_TEMPLATE")
        if not url_template:
            raise ValueError(
                "presigned sink needs CAMBER_EDGE_SINK_URL_TEMPLATE in the environment "
                "(a str.format(key=...) https template); it is a secret, never in the config file"
            )
        return PresignedHttpsSink(
            url_template=url_template, host=spec.get("host"), ca_file=spec.get("ca_file")
        )
    if kind == "s3":
        return S3Sink(bucket=spec["bucket"], prefix=spec.get("prefix", ""))
    if kind == "gcs":
        return GcsSink(bucket=spec["bucket"], prefix=spec.get("prefix", ""))
    if kind == "azure":
        return AzureBlobSink(container=spec["container"], prefix=spec.get("prefix", ""))
    raise ValueError(f"unknown sink kind {kind!r}")


def _build_source(spec: dict):
    """Construct a file-based source; live protocol sources must be injected (deployment-owned)."""
    kind = spec.get("kind")
    if kind == "csv_wide":
        from ..ingest.csv_wide import WideCsvAdapter

        return WideCsvAdapter(spec["path"], profile=spec.get("profile"))
    if kind == "csv_long":
        from ..ingest.csv_long import LongCsvAdapter

        return LongCsvAdapter(spec["path"], profile=spec.get("profile"))
    if kind == "csv_perpoint":
        from ..ingest.csv_perpoint import PerPointCsvAdapter

        return PerPointCsvAdapter(spec["folder"])
    raise NotImplementedError(
        f"source kind {kind!r} must be injected: live protocol clients/connections "
        "(sql/haystack/bacnet/opcua/modbus/mqtt) are deployment-specific — pass source= to "
        "build_forwarder (see docs/EDGE-DEPLOY.md)"
    )


def build_forwarder(cfg: EdgeConfig, *, source=None, sink=None, environ=None):
    """Assemble a :class:`~camber.edge.forwarder.Forwarder` from config (source/sink injectable).

    Live protocol sources need a client/connection the deployer owns, so pass ``source=``; file
    sources build from config directly.
    """
    from ..model.mapping import MappingProvider
    from .forwarder import Forwarder
    from .spool import Spool

    environ = os.environ if environ is None else environ
    mapping = MappingProvider.from_dict(cfg.mapping) if cfg.mapping else None
    sink = sink if sink is not None else _build_sink(cfg.sink, environ)
    source = source if source is not None else _build_source(cfg.source)
    spool = Spool(cfg.spool_dir, max_bytes=cfg.spool_max_bytes)
    return Forwarder(
        source,
        sink,
        facility_id=cfg.facility_id,
        spool=spool,
        mapping=mapping,
        resample=cfg.resample,
        quality=cfg.quality,
        wire_format=cfg.wire_format,
    )
