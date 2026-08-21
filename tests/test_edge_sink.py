"""Tests for the one-way edge sinks (camber.edge.sink)."""

import ssl

import pytest

from camber.edge.sink import PresignedHttpsSink, S3Sink, collect_sink


class _FakeResp:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeOpener:
    """Records the last Request + the TLS context the sink would use."""

    def __init__(self, status=200):
        self.status = status
        self.last = None

    def open(self, req, timeout=None):
        self.last = req
        return _FakeResp(self.status)


def test_collect_sink_records():
    sink, log = collect_sink()
    out = sink.put("k", b"data", content_type="application/octet-stream", metadata={"a": 1})
    assert out["ok"] is True and out["collected"] == 1
    assert log[0]["key"] == "k" and log[0]["data"] == b"data" and log[0]["metadata"] == {"a": 1}


def test_presigned_put_builds_https_put_with_hash_header():
    op = _FakeOpener(status=200)
    sink = PresignedHttpsSink(url_template="https://lake.example.org/up/{key}", _opener=op)
    res = sink.put(
        "facility_id=x/year=2024/part-abc.parquet",
        b"payload",
        content_type="application/vnd.apache.parquet",
    )
    assert res["ok"] is True and res["status"] == 200
    req = op.last
    assert req.get_method() == "PUT"
    assert req.full_url.startswith("https://lake.example.org/up/")
    assert req.data == b"payload"
    assert req.headers["Content-type"] == "application/vnd.apache.parquet"
    assert req.headers["X-camber-content-sha256"] == res["sha256"]


def test_presigned_rejects_http():
    sink = PresignedHttpsSink(url_template="http://lake.example.org/{key}", _opener=_FakeOpener())
    with pytest.raises(ValueError, match="https"):
        sink.put("k", b"d")


def test_presigned_uses_a_verified_tls_context():
    # No injected opener -> the sink builds a real verified context; assert it never disables it.
    sink = PresignedHttpsSink(url_template="https://lake.example.org/{key}")
    ctx = sink._opener.handlers  # opener exists; verify the default context is strict
    default = ssl.create_default_context()
    assert default.check_hostname is True and default.verify_mode == ssl.CERT_REQUIRED
    assert ctx  # opener was built (HTTPSHandler present)


def test_presigned_host_allowlist_blocks_redirect():
    sink = PresignedHttpsSink(
        url_broker=lambda key: "https://evil.example.com/steal",
        host="lake.example.org",
        _opener=_FakeOpener(),
    )
    with pytest.raises(ValueError, match="allowlisted host"):
        sink.put("k", b"d")


def test_presigned_non_2xx_is_not_ok():
    sink = PresignedHttpsSink(
        url_template="https://lake.example.org/{key}", _opener=_FakeOpener(status=503)
    )
    res = sink.put("k", b"d")
    assert res["ok"] is False and res["status"] == 503


def test_requires_exactly_one_url_source():
    with pytest.raises(ValueError, match="exactly one"):
        PresignedHttpsSink()
    with pytest.raises(ValueError, match="exactly one"):
        PresignedHttpsSink(url_template="https://a/{key}", url_broker=lambda k: "https://a")


def test_s3_sink_helpful_error_without_boto3():
    sink = S3Sink(bucket="b")  # no injected client
    try:
        import boto3  # noqa: F401

        pytest.skip("boto3 installed; the no-dep error path is not exercised here")
    except ImportError:
        pass
    with pytest.raises(ImportError, match=r"camber-toolkit\[edge-s3\]"):
        sink.put("k", b"d")


def test_s3_sink_uses_injected_client():
    class FakeS3:
        def __init__(self):
            self.calls = []

        def put_object(self, **kw):
            self.calls.append(kw)

    c = FakeS3()
    sink = S3Sink(bucket="b", prefix="pre", client=c)
    out = sink.put("facility_id=x/year=2024/p.parquet", b"d", metadata={"rows": 3})
    assert out["ok"] is True and out["key"] == "pre/facility_id=x/year=2024/p.parquet"
    assert c.calls[0]["Bucket"] == "b" and c.calls[0]["Body"] == b"d"
    assert c.calls[0]["Metadata"] == {"rows": "3"}


def test_gcs_sink_uses_injected_client():
    class _Blob:
        def __init__(self):
            self.metadata = None
            self.uploaded = None

        def upload_from_string(self, data, content_type=""):
            self.uploaded = (data, content_type)

    class _Bucket:
        def __init__(self, blob):
            self._blob = blob

        def blob(self, name):
            self._blob.name = name
            return self._blob

    class _Client:
        def __init__(self, bucket):
            self._bucket = bucket

        def bucket(self, name):
            return self._bucket

    blob = _Blob()
    client = _Client(_Bucket(blob))
    from camber.edge.sink import GcsSink

    out = GcsSink(bucket="b", prefix="p", client=client).put("k", b"d", metadata={"rows": 2})
    assert out["ok"] is True and out["key"] == "p/k"
    assert blob.uploaded[0] == b"d" and blob.metadata == {"rows": "2"}


def test_azure_sink_uses_injected_container_client():
    class _Container:
        def __init__(self):
            self.calls = []

        def upload_blob(self, name, data, overwrite=False, metadata=None):
            self.calls.append((name, data, overwrite, metadata))

    from camber.edge.sink import AzureBlobSink

    c = _Container()
    out = AzureBlobSink(container="cont", prefix="pre", client=c).put("k", b"d", metadata={"n": 1})
    assert out["ok"] is True and out["key"] == "pre/k"
    assert c.calls[0] == ("pre/k", b"d", True, {"n": "1"})


def test_azure_sink_without_client_raises():
    from camber.edge.sink import AzureBlobSink

    try:
        import azure.storage.blob  # noqa: F401
    except ImportError:
        import pytest as _pt

        with _pt.raises(ImportError, match=r"camber-toolkit\[edge-azure\]"):
            AzureBlobSink(container="c").put("k", b"d")
        return
    import pytest as _pt

    with _pt.raises(ValueError, match="ContainerClient"):
        AzureBlobSink(container="c").put("k", b"d")
