"""Tests for the dataset downloader (camber.datasets._fetch) -- all offline.

Every network path runs through an injected fake opener that returns canned responses, so resume
(206/200/validator change), verification (size/sha256 -> ``.bad``), https-only redirects, the disk
pre-check and progress reporting are all exercised with no network.
"""

import hashlib
import io
import os
import sys
import urllib.error
import urllib.request
from collections import namedtuple

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.datasets import _fetch  # noqa: E402

URL = "https://example.org/data/file.zip"
PAYLOAD = bytes(range(256)) * 40  # 10240 bytes
SHA = hashlib.sha256(PAYLOAD).hexdigest()


class FakeResponse:
    def __init__(self, body, status=200, headers=None, url=URL):
        self._buf = io.BytesIO(body)
        self.status = status
        self.headers = dict(headers or {})
        self._url = url

    def read(self, n=-1):
        return self._buf.read(n)

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """Records each request; serves the queued responses (or raises queued exceptions)."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, req, timeout=None):
        self.requests.append(req)
        r = self.responses.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r


def _hdr(req, name):
    return {k.lower(): v for k, v in req.header_items()}.get(name.lower())


# --------------------------------------------------------------------------- helpers


def test_sha256_file_and_human_bytes(tmp_path):
    p = tmp_path / "x"
    p.write_bytes(PAYLOAD)
    assert _fetch.sha256_file(str(p), chunk_size=100) == SHA
    assert _fetch.human_bytes(0) == "0 B"
    assert _fetch.human_bytes(999) == "999 B"
    assert _fetch.human_bytes(1_500_000_000) == "1.5 GB"
    assert _fetch.human_bytes(2_000) == "2.0 KB"
    assert _fetch.human_bytes(5e15) == "5000.0 TB"


def test_require_https():
    assert _fetch.require_https(URL) == URL
    with pytest.raises(_fetch.InsecureURL):
        _fetch.require_https("http://example.org/x")
    with pytest.raises(_fetch.InsecureURL):
        _fetch.require_https("ftp://example.org/x")


def test_https_opener_has_https_only_redirect():
    op = _fetch.https_opener(5)
    assert any(isinstance(h, _fetch._HttpsOnlyRedirect) for h in op.handlers)


def test_redirect_handler_refuses_downgrade_allows_https():
    h = _fetch._HttpsOnlyRedirect()
    req = urllib.request.Request(URL)
    with pytest.raises(_fetch.InsecureURL):
        h.redirect_request(req, None, 302, "Found", {}, "http://evil.example/x")
    new = h.redirect_request(req, None, 302, "Found", {}, "https://mirror.example/x")
    assert new.full_url == "https://mirror.example/x"


# --------------------------------------------------------------------------- happy paths


def test_happy_path_then_skip_if_verified(tmp_path):
    dest = str(tmp_path / "sub" / "file.zip")
    op = FakeOpener(FakeResponse(PAYLOAD, headers={"ETag": '"abc"'}))
    res = _fetch.download(URL, dest, size=len(PAYLOAD), sha256=SHA, opener=op, chunk_size=1000)
    assert res.sha256 == SHA and res.bytes == len(PAYLOAD) and res.etag == '"abc"'
    assert not res.resumed and not res.skipped
    assert open(dest, "rb").read() == PAYLOAD
    assert not os.path.exists(dest + ".part") and not os.path.exists(dest + ".part.etag")
    assert _hdr(op.requests[0], "User-Agent") == _fetch.USER_AGENT
    assert _hdr(op.requests[0], "Range") is None
    # second call: verified on disk -> no network at all
    again = _fetch.download(URL, dest, size=len(PAYLOAD), sha256=SHA, opener=FakeOpener())
    assert again.skipped and again.sha256 == SHA


def test_skip_unpinned_existing_file_computes_sha(tmp_path):
    dest = tmp_path / "f"
    dest.write_bytes(PAYLOAD)
    res = _fetch.download(URL, str(dest), opener=FakeOpener())
    assert res.skipped and res.sha256 == SHA


def test_unpinned_download_uses_content_length_for_progress(tmp_path):
    calls = []
    dest = str(tmp_path / "f")
    op = FakeOpener(FakeResponse(PAYLOAD, headers={"Content-Length": str(len(PAYLOAD))}))
    res = _fetch.download(URL, dest, opener=op, progress=lambda d, t: calls.append((d, t)))
    assert res.sha256 == SHA and res.etag is None
    assert calls[-1] == (len(PAYLOAD), len(PAYLOAD))


def test_progress_final_call_without_total(tmp_path):
    calls = []
    _fetch.download(
        URL,
        str(tmp_path / "f"),
        opener=FakeOpener(FakeResponse(PAYLOAD)),
        progress=lambda d, t: calls.append((d, t)),
    )
    assert calls[-1] == (len(PAYLOAD), len(PAYLOAD))


def test_progress_throttled_intermediate_calls(tmp_path, monkeypatch):
    ticks = iter(range(0, 1000))
    monkeypatch.setattr(_fetch.time, "monotonic", lambda: float(next(ticks)))
    calls = []
    _fetch.download(
        URL,
        str(tmp_path / "f"),
        size=len(PAYLOAD),
        opener=FakeOpener(FakeResponse(PAYLOAD)),
        progress=lambda d, t: calls.append((d, t)),
        chunk_size=1024,
    )
    assert len(calls) == 11  # 10 chunk callbacks (1 s apart) + the final one
    assert calls[-1] == (len(PAYLOAD), len(PAYLOAD))
    assert all(t == len(PAYLOAD) for _, t in calls)


def test_progress_exception_propagates_and_keeps_part(tmp_path):
    def boom(d, t):
        raise KeyboardInterrupt

    dest = str(tmp_path / "f")
    with pytest.raises(KeyboardInterrupt):
        _fetch.download(
            URL, dest, size=len(PAYLOAD), opener=FakeOpener(FakeResponse(PAYLOAD)), progress=boom
        )
    assert os.path.getsize(dest + ".part") == len(PAYLOAD) and not os.path.exists(dest)


# --------------------------------------------------------------------------- verification


def test_size_mismatch_moves_to_bad(tmp_path):
    dest = str(tmp_path / "f")
    op = FakeOpener(FakeResponse(PAYLOAD[:-10], headers={"ETag": "e"}))
    with pytest.raises(_fetch.ChecksumMismatch) as ei:
        _fetch.download(URL, dest, size=len(PAYLOAD), sha256=SHA, opener=op)
    assert ei.value.kind == "size" and ei.value.expected == len(PAYLOAD)
    assert ei.value.actual == len(PAYLOAD) - 10
    assert os.path.exists(dest + ".bad") and not os.path.exists(dest)
    assert not os.path.exists(dest + ".part") and not os.path.exists(dest + ".part.etag")


def test_sha_mismatch_moves_to_bad(tmp_path):
    dest = str(tmp_path / "f")
    op = FakeOpener(FakeResponse(PAYLOAD))
    with pytest.raises(_fetch.ChecksumMismatch) as ei:
        _fetch.download(URL, dest, size=len(PAYLOAD), sha256="0" * 64, opener=op)
    assert ei.value.kind == "sha256" and ei.value.actual == SHA
    assert "not accepted" in str(ei.value)
    assert open(dest + ".bad", "rb").read() == PAYLOAD and not os.path.exists(dest)


def test_existing_corrupt_dest_moved_aside_and_redownloaded(tmp_path):
    dest = tmp_path / "f"
    dest.write_bytes(b"corrupt")
    res = _fetch.download(
        URL, str(dest), size=len(PAYLOAD), sha256=SHA, opener=FakeOpener(FakeResponse(PAYLOAD))
    )
    assert not res.skipped and dest.read_bytes() == PAYLOAD
    assert (tmp_path / "f.bad").read_bytes() == b"corrupt"


def test_existing_right_size_wrong_sha_redownloaded(tmp_path):
    dest = tmp_path / "f"
    dest.write_bytes(b"\0" * len(PAYLOAD))
    res = _fetch.download(
        URL, str(dest), size=len(PAYLOAD), sha256=SHA, opener=FakeOpener(FakeResponse(PAYLOAD))
    )
    assert res.sha256 == SHA and (tmp_path / "f.bad").exists()


# --------------------------------------------------------------------------- resume


def test_range_resume_206_appends_with_if_range(tmp_path):
    dest = str(tmp_path / "f")
    open(dest + ".part", "wb").write(PAYLOAD[:4000])
    open(dest + ".part.etag", "w").write('"v1"')
    op = FakeOpener(FakeResponse(PAYLOAD[4000:], status=206, headers={"ETag": '"v1"'}))
    res = _fetch.download(URL, dest, size=len(PAYLOAD), sha256=SHA, opener=op)
    assert res.resumed and res.sha256 == SHA
    req = op.requests[0]
    assert _hdr(req, "Range") == "bytes=4000-" and _hdr(req, "If-Range") == '"v1"'
    assert open(dest, "rb").read() == PAYLOAD


def test_resume_without_sidecar_sends_range_only(tmp_path):
    dest = str(tmp_path / "f")
    open(dest + ".part", "wb").write(PAYLOAD[:10])
    op = FakeOpener(FakeResponse(PAYLOAD[10:], status=206))
    res = _fetch.download(URL, dest, sha256=SHA, opener=op)
    assert res.resumed and _hdr(op.requests[0], "If-Range") is None


def test_resume_200_restarts_from_zero(tmp_path):
    dest = str(tmp_path / "f")
    open(dest + ".part", "wb").write(b"garbage-prefix")
    op = FakeOpener(FakeResponse(PAYLOAD, status=200))
    res = _fetch.download(URL, dest, size=len(PAYLOAD), sha256=SHA, opener=op)
    assert not res.resumed and open(dest, "rb").read() == PAYLOAD


def test_etag_change_restarts_and_records_new_validator(tmp_path, monkeypatch):
    dest = str(tmp_path / "f")
    open(dest + ".part", "wb").write(PAYLOAD[:100])
    open(dest + ".part.etag", "w").write('"old"')
    # If-Range mismatch -> server sends the full (new) file with 200 and a new ETag
    op = FakeOpener(FakeResponse(PAYLOAD, status=200, headers={"ETag": '"new"'}))
    res = _fetch.download(URL, dest, sha256=SHA, opener=op)
    assert _hdr(op.requests[0], "If-Range") == '"old"'
    assert not res.resumed and res.etag == '"new"'
    assert open(dest, "rb").read() == PAYLOAD


def test_oversized_part_restarts(tmp_path):
    dest = str(tmp_path / "f")
    open(dest + ".part", "wb").write(PAYLOAD + b"extra")
    open(dest + ".part.etag", "w").write("stale")
    op = FakeOpener(FakeResponse(PAYLOAD, headers={"Last-Modified": "Mon"}))
    res = _fetch.download(URL, dest, size=len(PAYLOAD), opener=op)
    assert _hdr(op.requests[0], "Range") is None and res.etag == "Mon"


def test_network_error_keeps_part(tmp_path):
    dest = str(tmp_path / "f")
    open(dest + ".part", "wb").write(PAYLOAD[:50])
    op = FakeOpener(urllib.error.URLError("unreachable"))
    with pytest.raises(_fetch.FetchError) as ei:
        _fetch.download(URL, dest, opener=op)
    assert URL in str(ei.value) and isinstance(ei.value.__cause__, urllib.error.URLError)
    assert os.path.getsize(dest + ".part") == 50


def test_status_via_getcode(tmp_path):
    class CodeOnly(FakeResponse):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.status = None

        def getcode(self):
            return 200

    res = _fetch.download(URL, str(tmp_path / "f"), opener=FakeOpener(CodeOnly(PAYLOAD)))
    assert res.sha256 == SHA


def test_response_without_geturl(tmp_path):
    class NoUrl(FakeResponse):
        def __getattribute__(self, name):
            if name == "geturl":
                raise AttributeError(name)
            return super().__getattribute__(name)

    res = _fetch.download(URL, str(tmp_path / "f"), opener=FakeOpener(NoUrl(PAYLOAD)))
    assert res.sha256 == SHA


# --------------------------------------------------------------------------- https + disk


def test_http_url_refused_before_network(tmp_path):
    op = FakeOpener()
    with pytest.raises(_fetch.InsecureURL):
        _fetch.download("http://example.org/f", str(tmp_path / "f"), opener=op)
    assert op.requests == []


def test_final_url_downgrade_detected(tmp_path):
    op = FakeOpener(FakeResponse(PAYLOAD, url="http://example.org/f"))
    with pytest.raises(_fetch.InsecureURL):
        _fetch.download(URL, str(tmp_path / "f"), opener=op)
    assert not os.path.exists(str(tmp_path / "f"))


Usage = namedtuple("Usage", "total used free")


def test_disk_refusal(tmp_path, monkeypatch):
    monkeypatch.setattr(_fetch.shutil, "disk_usage", lambda p: Usage(10**9, 0, 1000))
    with pytest.raises(_fetch.InsufficientSpace) as ei:
        _fetch.download(
            URL,
            str(tmp_path / "deep" / "dir" / "f"),
            size=2_000_000,
            opener=FakeOpener(FakeResponse(PAYLOAD)),
        )
    e = ei.value
    assert e.needed == 2_000_000 and e.free == 1000
    assert "2.0 MB" in str(e) and "1.0 KB" in str(e)


def test_check_disk_walks_to_existing_ancestor(tmp_path, monkeypatch):
    seen = []

    def usage(p):
        seen.append(p)
        return Usage(10**12, 0, 10**12)

    monkeypatch.setattr(_fetch.shutil, "disk_usage", usage)
    _fetch.check_disk(str(tmp_path / "a" / "b" / "c.zip"), 100)
    assert seen == [str(tmp_path)]
    _fetch.check_disk(str(tmp_path), None)  # unknown size -> no-op
    _fetch.check_disk(str(tmp_path), 0)
    assert len(seen) == 1


def test_check_disk_margin(tmp_path, monkeypatch):
    monkeypatch.setattr(_fetch.shutil, "disk_usage", lambda p: Usage(0, 0, 1040))
    with pytest.raises(_fetch.InsufficientSpace):
        _fetch.check_disk(str(tmp_path), 1000)  # needs 1050 with the 5% margin
    _fetch.check_disk(str(tmp_path), 1000, margin=0.0)
