"""Edge configuration: a small non-secret file, overlaid by environment variables.

The config file describes *what* to forward (facility id, source kind, sink kind + allowlisted host,
mapping, cadence). **Secrets never live in it** -- presigned URLs, tokens, passwords, and cloud keys
come only from environment variables (or a secret manager that populates them). :func:`load_config`
enforces that by rejecting any secret-shaped key or presigned URL found in the file, so "no secrets
in config" is a tested invariant, not documentation. Mirrors the env-var config style of
:mod:`camber.api.server` (``CAMBER_*``).
"""

from __future__ import annotations

import difflib
import logging
import os
import re
import warnings
from dataclasses import dataclass, field

_LOG = logging.getLogger("camber.edge")

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
# Value fragments that mark a string as a presigned/authenticated URL or credential (a secret),
# whatever its scheme (0.108, #249): an S3 / GCS / Azure SAS signature, an access key id, an Azure
# connection string.
_SECRET_VALUE_HINTS = (
    "x-amz-signature",
    "x-amz-credential",
    "x-amz-security-token",
    "awsaccesskeyid",
    "x-goog-signature",
    "x-goog-credential",
    "googleaccessid",
    "sharedaccesssignature",
    "accountkey=",
)
# Query parameters that carry a signature or a token (an Azure SAS is ``...&sig=...``).
_SECRET_QUERY_PARAMS = frozenset(
    {
        "sig",
        "signature",
        "x-amz-signature",
        "x-goog-signature",
        "access_token",
        "token",
        "api_key",
        "apikey",
        "key",
        "password",
    }
)
# ``scheme://user:password@host``: a URL (any scheme, a DSN included) with a password in it.
_USERINFO_RE = re.compile(r"[a-z][a-z0-9+.\-]*://[^/?#@\s]*:[^/?#@\s]*@", re.IGNORECASE)
_QUERY_RE = re.compile(r"[?&;]([a-z0-9_.\-]+)=", re.IGNORECASE)


def _secret_in_value(value: str) -> str | None:
    """Why ``value`` is a secret (a presigned URL, a SAS, a password in a URL), else ``None``."""
    vl = value.lower()
    for hint in _SECRET_VALUE_HINTS:
        if hint in vl:
            return f"it carries {hint.rstrip('=')!r}"
    if _USERINFO_RE.search(value):
        return "it is a URL with a password in its userinfo (user:password@host)"
    for name in _QUERY_RE.findall(value):
        if name.lower() in _SECRET_QUERY_PARAMS:
            return f"it is a URL with a {name.lower()!r} query parameter (a signature or token)"
    return None


def _scan_secrets(obj, path: str = "") -> None:
    """Raise if the config tree carries a secret-shaped key or a presigned/credential value."""
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
        why = _secret_in_value(obj)
        if why:
            raise ValueError(
                f"a presigned/authenticated URL or credential is a secret ({why}); set it via the "
                f"environment, not the config file (at {path.rstrip('.')!r})"
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
    device_id: str | None = None  # 0.95 (#18): names the device in its retirement record
    part_layout: str = "canonical"  # 0.107 (#142): "legacy" for a pre-0.107 cloud landing
    timezone: str | None = None  # 0.108 (#249): the site's IANA zone, passed to file sources
    dead_letter_after: int = 5  # 0.108 (#245): permanent rejections before a batch is dead


# 0.108 (#249): the keys a config file may hold. Anything else is refused (a typo such as
# ``spool_dri`` would otherwise leave the default silently in force). Keys that start with ``_``
# and ``note`` / ``comment`` are free-form comments at any level.
CONFIG_KEYS = frozenset(EdgeConfig.__dataclass_fields__)
_COMMENT_KEYS = frozenset({"note", "comment", "description"})
_SINK_KEYS = {
    "collect": {"kind"},
    "presigned": {"kind", "host", "ca_file", "timeout"},
    "s3": {"kind", "bucket", "prefix"},
    "gcs": {"kind", "bucket", "prefix"},
    "azure": {"kind", "container", "prefix"},
}
_FILE_SOURCE_KEYS = {
    "csv_wide": {"kind", "path", "profile", "timezone", "strict_timezone"},
    "csv_long": {"kind", "path", "profile", "timezone", "strict_timezone"},
    "csv_perpoint": {"kind", "folder", "timezone", "strict_timezone"},
}
_SINK_REQUIRED = {"s3": ("bucket",), "gcs": ("bucket",), "azure": ("container",)}
_FILE_SOURCE_REQUIRED = {"csv_wide": ("path",), "csv_long": ("path",), "csv_perpoint": ("folder",)}


def _is_comment(key) -> bool:
    return str(key).startswith("_") or str(key) in _COMMENT_KEYS


def _check_keys(obj: dict, allowed, where: str) -> None:
    unknown = sorted(str(k) for k in obj if k not in allowed and not _is_comment(k))
    if not unknown:
        return
    hints = []
    for k in unknown:
        close = difflib.get_close_matches(k, sorted(allowed), n=1)
        hints.append(f"{k!r}" + (f" (did you mean {close[0]!r}?)" if close else ""))
    raise ValueError(
        f"unknown key(s) in the edge config {where}: {', '.join(hints)}; known keys: "
        f"{', '.join(sorted(allowed))} (prefix a key with '_' to keep it as a comment)"
    )


def _num(value, name: str, *, cast, minimum=None, positive=False):
    """``value`` as ``cast`` (a number or a numeric string; never a bool), range-checked."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"edge config {name!r} must be a number, got {value!r}")
    try:
        out = cast(float(value)) if cast is int and isinstance(value, str) else cast(value)
    except (TypeError, ValueError):
        raise ValueError(f"edge config {name!r} must be a number, got {value!r}") from None
    if cast is int and isinstance(value, float) and value != int(value):
        raise ValueError(f"edge config {name!r} must be a whole number, got {value!r}")
    if out != out or out in (float("inf"), float("-inf")):  # NaN / infinity
        raise ValueError(f"edge config {name!r} must be finite, got {value!r}")
    if positive and out <= 0:
        raise ValueError(f"edge config {name!r} must be greater than 0, got {value!r}")
    if minimum is not None and out < minimum:
        raise ValueError(f"edge config {name!r} must be at least {minimum}, got {value!r}")
    return out


def _bool(value, name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"edge config {name!r} must be true or false (a JSON boolean), got {value!r}")


def _text(value, name: str, *, optional: bool = False):
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"edge config {name!r} must be a non-empty string, got {value!r}")
    return value


def _choice(value, name: str, choices) -> str:
    if value not in choices:
        raise ValueError(
            f"edge config {name!r} must be one of {', '.join(map(repr, choices))}, got {value!r}"
        )
    return value


def _check_source(spec: dict, timezone) -> None:
    kind = spec.get("kind")
    if kind in _FILE_SOURCE_KEYS:
        _check_keys(spec, _FILE_SOURCE_KEYS[kind], f"'source' (kind {kind!r})")
        for k in _FILE_SOURCE_REQUIRED[kind]:
            _text(spec.get(k), f"source.{k}")
        if "strict_timezone" in spec:
            _bool(spec["strict_timezone"], "source.strict_timezone")
    if spec.get("timezone") is not None:
        from ..tsparse import check_timezone

        check_timezone(spec["timezone"])


def _check_sink(spec: dict) -> None:
    kind = spec.get("kind", "presigned")
    if kind not in _SINK_KEYS:
        raise ValueError(f"unknown sink kind {kind!r}; one of {', '.join(sorted(_SINK_KEYS))}")
    _check_keys(spec, _SINK_KEYS[kind], f"'sink' (kind {kind!r})")
    for k in _SINK_REQUIRED.get(kind, ()):
        _text(spec.get(k), f"sink.{k}")
    if "timeout" in spec:
        _num(spec["timeout"], "sink.timeout", cast=float, positive=True)


def load_config(path: str | None = None, *, environ=None) -> EdgeConfig:
    """Load the JSON (or YAML) config (rejecting secrets) and overlay ``CAMBER_EDGE_*`` env vars.

    Since 0.108 (#249) the values are checked: ``interval`` and ``spool_max_bytes`` must be
    greater than 0, ``quality`` a boolean, ``wire_format`` / ``part_layout`` one of their choices,
    ``timezone`` an IANA zone, and an unknown key (a typo) is refused rather than ignored.
    """
    from .forwarder import PART_LAYOUTS

    environ = os.environ if environ is None else environ
    path = path or environ.get("CAMBER_EDGE_CONFIG")
    raw: dict = {}
    if path:
        from .._yaml import read_config_file  # 0.98 (#90): .yaml / .yml too (the [yaml] extra)

        raw = read_config_file(path)
        if not isinstance(raw, dict):
            raise ValueError(f"the edge config {path} must be a mapping (a JSON object)")
        _scan_secrets(raw)
        _check_keys(raw, CONFIG_KEYS, "file")

    facility_id = environ.get("CAMBER_EDGE_FACILITY_ID") or raw.get("facility_id")
    if not facility_id:
        raise ValueError(
            "facility_id is required (config 'facility_id' or CAMBER_EDGE_FACILITY_ID)"
        )
    _text(facility_id, "facility_id")

    source = raw.get("source", {})
    sink = raw.get("sink", {})
    for name, spec in (("source", source), ("sink", sink)):
        if not isinstance(spec, dict):
            raise ValueError(f"edge config {name!r} must be a mapping, got {spec!r}")
    mapping = raw.get("mapping")
    if mapping is not None and not isinstance(mapping, dict):
        raise ValueError(f"edge config 'mapping' must be a mapping, got {mapping!r}")

    timezone = environ.get("CAMBER_EDGE_TIMEZONE") or raw.get("timezone")
    if timezone is not None:
        from ..tsparse import check_timezone

        check_timezone(timezone)
    _check_source(source, timezone)
    _check_sink(sink)

    resample = _text(environ.get("CAMBER_EDGE_RESAMPLE") or raw.get("resample", "1h"), "resample")
    try:
        import pandas as pd

        pd.tseries.frequencies.to_offset(resample)
    except ValueError:
        raise ValueError(
            f"edge config 'resample' must be a pandas frequency such as '1h', got {resample!r}"
        ) from None
    interval = _num(
        environ.get("CAMBER_EDGE_INTERVAL") or raw.get("interval", 3600.0),
        "interval",
        cast=float,
        positive=True,
    )
    spool_max_bytes = _num(
        raw.get("spool_max_bytes", _DEFAULT_MAX_BYTES), "spool_max_bytes", cast=int, positive=True
    )
    if spool_max_bytes < 1024**2:
        msg = (
            f"edge config 'spool_max_bytes' is {spool_max_bytes} bytes (under 1 MiB): an offline "
            "device will evict older batches early"
        )
        _LOG.warning("edge.config %s", msg)
        warnings.warn(msg, UserWarning, stacklevel=2)
    return EdgeConfig(
        facility_id=facility_id,
        source=source,
        sink=sink,
        mapping=mapping,
        resample=resample,
        interval=interval,
        spool_dir=_text(
            environ.get("CAMBER_EDGE_SPOOL_DIR") or raw.get("spool_dir", "./camber-edge-spool"),
            "spool_dir",
        ),
        spool_max_bytes=spool_max_bytes,
        quality=_bool(raw.get("quality", True), "quality"),
        wire_format=_choice(
            environ.get("CAMBER_EDGE_WIRE_FORMAT") or raw.get("wire_format", "parquet"),
            "wire_format",
            ("parquet", "ndjson"),
        ),
        device_id=_text(
            environ.get("CAMBER_EDGE_DEVICE_ID") or raw.get("device_id"), "device_id", optional=True
        ),
        part_layout=_choice(
            environ.get("CAMBER_EDGE_PART_LAYOUT") or raw.get("part_layout", "canonical"),
            "part_layout",
            PART_LAYOUTS,
        ),
        timezone=timezone,
        dead_letter_after=_num(
            raw.get("dead_letter_after", 5), "dead_letter_after", cast=int, minimum=1
        ),
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
            url_template=url_template,
            host=spec.get("host"),
            ca_file=spec.get("ca_file"),
            timeout=float(spec.get("timeout", 30.0)),
        )
    if kind == "s3":
        return S3Sink(bucket=spec["bucket"], prefix=spec.get("prefix", ""))
    if kind == "gcs":
        return GcsSink(bucket=spec["bucket"], prefix=spec.get("prefix", ""))
    if kind == "azure":
        return AzureBlobSink(container=spec["container"], prefix=spec.get("prefix", ""))
    raise ValueError(f"unknown sink kind {kind!r}")


def _build_source(spec: dict, timezone: str | None = None):
    """Construct a file-based source; live protocol sources must be injected (deployment-owned).

    ``timezone`` (the config's site zone, 0.108 #249) is passed to the file sources; a
    ``source.timezone`` overrides it.
    """
    kind = spec.get("kind")
    tz: str | None = spec.get("timezone", timezone)
    strict = bool(spec.get("strict_timezone", False))
    if kind == "csv_wide":
        from ..ingest.csv_wide import WideCsvAdapter

        return WideCsvAdapter(
            spec["path"], profile=spec.get("profile"), timezone=tz, strict_timezone=strict
        )
    if kind == "csv_long":
        from ..ingest.csv_long import LongCsvAdapter

        return LongCsvAdapter(
            spec["path"], profile=spec.get("profile"), timezone=tz, strict_timezone=strict
        )
    if kind == "csv_perpoint":
        from ..ingest.csv_perpoint import PerPointCsvAdapter

        return PerPointCsvAdapter(spec["folder"], timezone=tz, strict_timezone=strict)
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
    source = source if source is not None else _build_source(cfg.source, cfg.timezone)
    spool = Spool(
        cfg.spool_dir, max_bytes=cfg.spool_max_bytes, dead_letter_after=cfg.dead_letter_after
    )
    return Forwarder(
        source,
        sink,
        facility_id=cfg.facility_id,
        spool=spool,
        mapping=mapping,
        resample=cfg.resample,
        quality=cfg.quality,
        wire_format=cfg.wire_format,
        part_layout=cfg.part_layout,
    )
