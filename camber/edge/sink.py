"""The one-way write seam: a ``Sink`` pushes an object outbound and never listens.

An edge forwarder must be *provably* one-way — it reads BAS trends read-only and writes only to a
cloud landing, over an outbound connection it initiates, with no inbound listener anywhere. This
module defines the :class:`Sink` protocol (a single ``put``) and the shipped sinks:

* :class:`PresignedHttpsSink` -- the **default**. stdlib ``urllib`` PUT to a short-lived presigned
  ``https://`` URL (or one fetched per-object from a customer HTTPS broker). No cloud SDK, **no
  long-lived credentials on the edge**, TLS verification never disabled, and every resolved URL is
  checked against a single allowlisted host so a rogue broker response cannot redirect egress.
* :class:`S3Sink` / :class:`AzureBlobSink` / :class:`GcsSink` -- thin, lazily-imported adapters for
  sites whose IT mandates direct scoped-credential auth (each behind a ``camber-toolkit[edge-*]``
  extra; a ``client`` is injectable so CI needs no SDK).
* :func:`collect_sink` -- an in-memory double for tests / dry-run (mirror of
  :func:`camber.integrate.tickets.collect_transport`).

The style mirrors the callable-transport convention in :mod:`camber.integrate.tickets`: a sink is a
small injectable object, so the forwarder and tests never branch on the concrete cloud.
"""

from __future__ import annotations

import hashlib
import logging
import ssl
from collections.abc import Callable
from typing import Protocol, runtime_checkable
from urllib import parse as _parse
from urllib import request as _request

_LOG = logging.getLogger("camber.edge")


@runtime_checkable
class Sink(Protocol):
    """A one-way object writer: push ``data`` to ``key``, return a small status dict."""

    def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
        metadata=None,
    ) -> dict:
        """Write ``data`` at ``key``; return ``{"ok": bool, ...}``. Never listens."""
        ...


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _CollectSink:
    """A no-network sink that records every object; the double behind :func:`collect_sink`."""

    def __init__(self, log: list):
        self._log = log

    def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
        metadata=None,
    ) -> dict:
        """Record the object instead of sending it anywhere."""
        self._log.append(
            {"key": key, "data": data, "content_type": content_type, "metadata": metadata or {}}
        )
        return {"ok": True, "collected": len(self._log), "key": key}


def collect_sink():
    """A no-network sink that records objects; returns ``(sink, log)``.

    The default for tests and dry runs: ``log`` is the list of every object the sink was asked to
    ``put`` (each ``{"key", "data", "content_type", "metadata"}``).
    """
    log: list = []
    return _CollectSink(log), log


class PresignedHttpsSink:
    """Push objects to a cloud landing by HTTPS PUT to a presigned URL. Outbound-only, no creds.

    Exactly one of ``url_template`` (a ``str.format(key=...)`` template that yields a full presigned
    ``https://`` PUT URL) or ``url_broker`` (a callable ``key -> url`` that fetches a short-lived
    presigned URL from a customer HTTPS endpoint) must be given. ``host`` is the single allowlisted
    egress hostname: every resolved URL's host must equal it, so a compromised broker cannot
    redirect data elsewhere. TLS is always verified (``ssl.create_default_context``); ``http://`` is
    rejected. ``_opener`` is injectable so tests exercise the path with no network.
    """

    def __init__(
        self,
        *,
        url_template: str | None = None,
        url_broker: Callable[[str], str] | None = None,
        host: str | None = None,
        timeout: float = 30.0,
        ca_file: str | None = None,
        extra_headers: dict | None = None,
        _opener=None,
    ):
        if (url_template is None) == (url_broker is None):
            raise ValueError("give exactly one of url_template or url_broker")
        self.url_template = url_template
        self.url_broker = url_broker
        self.host = host
        self.timeout = timeout
        self.extra_headers = dict(extra_headers or {})
        # A verified TLS context -- there is deliberately no code path that disables verification.
        context = ssl.create_default_context(cafile=ca_file)
        self._opener = _opener or _request.build_opener(_request.HTTPSHandler(context=context))

    def _resolve_url(self, key: str) -> str:
        if self.url_template is not None:
            url = self.url_template.format(key=key)
        else:
            assert self.url_broker is not None  # guaranteed by __init__ (exactly one is set)
            url = self.url_broker(key)
        parts = _parse.urlsplit(url)
        if parts.scheme != "https":
            raise ValueError(f"sink URL must be https, got {parts.scheme!r}")
        if self.host is not None and parts.hostname != self.host:
            raise ValueError(
                f"resolved egress host {parts.hostname!r} is not the allowlisted host {self.host!r}"
            )
        return url

    def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
        metadata=None,
    ) -> dict:
        """PUT ``data`` to the (presigned) URL for ``key`` over verified TLS; return status."""
        url = self._resolve_url(key)
        digest = _sha256(data)
        headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(data)),
            "x-camber-content-sha256": digest,
            **self.extra_headers,
        }
        req = _request.Request(url, data=data, method="PUT", headers=headers)
        with self._opener.open(req, timeout=self.timeout) as resp:  # noqa: S310
            status = int(getattr(resp, "status", 0) or 0)
        ok = 200 <= status < 300
        _LOG.info(
            "edge.sink.put host=%s key=%s bytes=%d sha256=%s status=%d ok=%s",
            _parse.urlsplit(url).hostname,
            key,
            len(data),
            digest,
            status,
            ok,
        )
        return {"ok": ok, "status": status, "key": key, "sha256": digest}


def _require_boto3():
    try:
        import boto3  # noqa: F401
    except ImportError as exc:  # pragma: no cover - exercised via message assertion
        raise ImportError("S3Sink needs boto3; install camber-toolkit[edge-s3]") from exc
    return boto3


def _require_azure():
    try:
        from azure.storage.blob import BlobServiceClient  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "AzureBlobSink needs azure-storage-blob; install camber-toolkit[edge-azure]"
        ) from exc
    from azure.storage.blob import BlobServiceClient

    return BlobServiceClient


def _require_gcs():
    try:
        from google.cloud import storage  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "GcsSink needs google-cloud-storage; install camber-toolkit[edge-gcs]"
        ) from exc
    from google.cloud import storage

    return storage


class S3Sink:
    """Push objects to an S3 bucket (``camber-toolkit[edge-s3]``). Inject ``client`` for tests."""

    def __init__(self, *, bucket: str, prefix: str = "", client=None):
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self._client = client

    def _c(self):
        if self._client is None:
            self._client = _require_boto3().client("s3")
        return self._client

    def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
        metadata=None,
    ) -> dict:
        """``put_object`` the bytes; a write-only path, never a read/list/actuate."""
        full = f"{self.prefix}/{key}" if self.prefix else key
        meta = {k: str(v) for k, v in (metadata or {}).items()}
        self._c().put_object(
            Bucket=self.bucket, Key=full, Body=data, ContentType=content_type, Metadata=meta
        )
        _LOG.info("edge.sink.put s3=%s key=%s bytes=%d", self.bucket, full, len(data))
        return {"ok": True, "key": full}


class AzureBlobSink:
    """Push objects to an Azure Blob container (``camber-toolkit[edge-azure]``)."""

    def __init__(self, *, container: str, prefix: str = "", client=None):
        self.container = container
        self.prefix = prefix.strip("/")
        self._client = client  # a ContainerClient

    def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
        metadata=None,
    ) -> dict:
        """``upload_blob`` the bytes (overwrite = idempotent land-by-key)."""
        if self._client is None:
            _require_azure()
            raise ValueError("inject a ContainerClient as client= for AzureBlobSink")
        full = f"{self.prefix}/{key}" if self.prefix else key
        meta = {k: str(v) for k, v in (metadata or {}).items()}
        self._client.upload_blob(name=full, data=data, overwrite=True, metadata=meta)
        _LOG.info("edge.sink.put azure=%s key=%s bytes=%d", self.container, full, len(data))
        return {"ok": True, "key": full}


class GcsSink:
    """Push objects to a GCS bucket (``camber-toolkit[edge-gcs]``). Inject ``client`` for tests."""

    def __init__(self, *, bucket: str, prefix: str = "", client=None):
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self._client = client

    def _c(self):
        if self._client is None:
            self._client = _require_gcs().Client()
        return self._client

    def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
        metadata=None,
    ) -> dict:
        """Upload the bytes to the object (overwrite = idempotent land-by-key)."""
        full = f"{self.prefix}/{key}" if self.prefix else key
        blob = self._c().bucket(self.bucket).blob(full)
        if metadata:
            blob.metadata = {k: str(v) for k, v in metadata.items()}
        blob.upload_from_string(data, content_type=content_type)
        _LOG.info("edge.sink.put gcs=%s key=%s bytes=%d", self.bucket, full, len(data))
        return {"ok": True, "key": full}
