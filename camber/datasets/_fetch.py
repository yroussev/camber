"""Low-level HTTPS downloader for the dataset catalog: resumable, verified, atomic.

Modelled on :mod:`camber.weather_source` -- stdlib ``urllib`` only, a descriptive User-Agent, an
explicit timeout, and an **injectable opener** (anything with ``.open(request, timeout=...)``) so
every path is tested offline on canned responses.

Guarantees of :func:`download`:

* **HTTPS only**, including after redirects (the default opener refuses a downgrade, and the final
  URL is re-checked).
* **Resume**: bytes stream to ``<dest>.part``; the response validator (ETag, else Last-Modified)
  that started the ``.part`` is kept in a ``<dest>.part.etag`` sidecar. A later call sends
  ``Range`` + ``If-Range``; a ``206`` appends, a ``200`` (range ignored or the file changed)
  restarts from byte 0.
* **Verification**: the declared size and sha256 are checked before the file is published. A
  mismatch moves the bytes to ``<dest>.bad`` and raises :class:`ChecksumMismatch` -- a changed
  upstream file is never silently accepted.
* **Atomic publish**: ``fsync`` then ``os.replace(<dest>.part, <dest>)``.
* **Disk pre-check**: :class:`InsufficientSpace` (with the numbers) before streaming starts.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

USER_AGENT = "camber-toolkit dataset fetcher (https://github.com/yroussev/camber)"
_PROGRESS_INTERVAL = 0.5  # seconds between progress callbacks (plus one final call)
#: Headroom every disk pre-check adds to the bytes it needs (a fetch, an archive extraction and
#: ``camber lab``'s pre-fetch check all use it, so the page and the fetch agree).
DISK_MARGIN = 0.05


class FetchError(RuntimeError):
    """A download failed (network error, bad response, or a verification failure)."""


class ChecksumMismatch(FetchError):
    """The downloaded bytes do not match the catalog's pinned size or sha256."""

    def __init__(self, path: str, expected, actual, kind: str):
        self.path, self.expected, self.actual, self.kind = path, expected, actual, kind
        super().__init__(
            f"{kind} mismatch for {path}: expected {expected}, got {actual} "
            f"(kept as {path}.bad; the upstream file may have changed -- not accepted)"
        )


class LocalFileMismatch(ChecksumMismatch):
    """A local copy (``ingest --from-dir``) does not match the catalog's pinned size or sha256."""

    def __init__(self, path: str, expected, actual, kind: str):
        self.path, self.expected, self.actual, self.kind = path, expected, actual, kind
        FetchError.__init__(
            self,
            f"{kind} mismatch for local file {path}: expected {expected}, got {actual} -- not the "
            "file the catalog pins (a different release, or a damaged download); not accepted",
        )


class ManualDownload(FetchError):
    """The entry is a manual download (``manual: true``): CAMBER never fetches its files."""


class InsufficientSpace(FetchError):
    """Not enough free disk for the download (checked before streaming starts)."""

    def __init__(self, path: str, needed: int, free: int):
        self.path, self.needed, self.free = path, needed, free
        super().__init__(
            f"not enough disk space at {path}: need {human_bytes(needed)} "
            f"({needed} bytes), only {human_bytes(free)} ({free} bytes) free"
        )


class InsecureURL(FetchError):
    """A non-https URL, or a redirect that would downgrade to one."""


def human_bytes(n) -> str:
    """Format a byte count with decimal units, e.g. ``1.5 GB``."""
    v = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(v) < 1000 or unit == "TB":
            return f"{int(v)} B" if unit == "B" else f"{v:.1f} {unit}"
        v /= 1000.0
    return f"{v:.1f} TB"  # pragma: no cover - loop always returns


def sha256_file(path: str, chunk_size: int = 1 << 20) -> str:
    """Hex sha256 of a file, read in chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk_size), b""):
            h.update(block)
    return h.hexdigest()


def require_https(url: str) -> str:
    """Return ``url`` if its scheme is https, else raise :class:`InsecureURL`."""
    if urlparse(str(url)).scheme.lower() != "https":
        raise InsecureURL(f"refusing non-https URL: {url}")
    return url


class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect handler that refuses any redirect to a non-https URL."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        require_https(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def https_opener(timeout: float = 60.0) -> urllib.request.OpenerDirector:
    """The default opener: stdlib ``urllib`` with the https-only redirect handler.

    ``timeout`` is accepted for symmetry with the transports in :mod:`camber.weather_source`; it
    is applied per request by :func:`download`.
    """
    return urllib.request.build_opener(_HttpsOnlyRedirect())


def check_disk(path: str, needed_bytes, *, margin: float = DISK_MARGIN) -> None:
    """Raise :class:`InsufficientSpace` unless ``needed_bytes`` (+ ``margin``) fit at ``path``.

    Uses the nearest existing ancestor directory of ``path``. ``needed_bytes`` of ``None``/0 is a
    no-op (unknown size: nothing to check).
    """
    if not needed_bytes or needed_bytes <= 0:
        return
    probe = os.path.abspath(path)
    while not os.path.isdir(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    free = shutil.disk_usage(probe).free
    if free < needed_bytes * (1.0 + margin):
        raise InsufficientSpace(path, int(needed_bytes), int(free))


@dataclass
class DownloadResult:
    """Outcome of :func:`download`."""

    path: str
    bytes: int
    sha256: str
    etag: str | None
    resumed: bool
    skipped: bool


def _verified_existing(dest: str, size, sha256) -> DownloadResult | None:
    """If ``dest`` already holds the right bytes return a skipped result; else move it aside."""
    if not os.path.exists(dest):
        return None
    n = os.path.getsize(dest)
    if size is None or n == size:
        digest = sha256_file(dest)
        if sha256 is None or digest == sha256:
            return DownloadResult(dest, n, digest, None, resumed=False, skipped=True)
    os.replace(dest, dest + ".bad")  # never trust a mismatched file; keep it for inspection
    return None


def _read_text(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _status(resp) -> int:
    return int(getattr(resp, "status", None) or resp.getcode())


def _stream(resp, fh, done: int, total, chunk_size: int, progress) -> int:
    """Copy ``resp`` into ``fh`` in chunks, reporting throttled progress; return bytes done."""
    last = time.monotonic()
    while True:
        block = resp.read(chunk_size)
        if not block:
            break
        fh.write(block)
        done += len(block)
        if progress is not None:
            now = time.monotonic()
            if now - last >= _PROGRESS_INTERVAL:
                last = now
                progress(done, total)
    fh.flush()
    os.fsync(fh.fileno())
    return done


def download(
    url: str,
    dest: str,
    *,
    size: int | None = None,
    sha256: str | None = None,
    opener=None,
    progress: Callable[[int, int | None], None] | None = None,
    timeout: float = 60.0,
    chunk_size: int = 1 << 20,
) -> DownloadResult:
    """Download ``url`` to ``dest`` (resumable, verified, atomic); see the module docstring.

    ``progress(done_bytes, total_bytes_or_None)`` is called at most every ~0.5 s and once at the
    end. An exception raised by ``progress`` propagates (a caller may use it to cancel); the
    ``.part`` file is kept so the next call resumes.
    """
    require_https(url)
    have = _verified_existing(dest, size, sha256)
    if have is not None:
        return have
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    part, sidecar = dest + ".part", dest + ".part.etag"
    offset = os.path.getsize(part) if os.path.exists(part) else 0
    if offset and size is not None and offset >= size:
        offset = 0  # a .part at/over the declared size cannot be a valid prefix
    validator = _read_text(sidecar) if offset else None

    headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
        if validator:
            headers["If-Range"] = validator
    opener = opener if opener is not None else https_opener(timeout)
    request = urllib.request.Request(url, headers=headers)
    try:
        with opener.open(request, timeout=timeout) as resp:
            final = resp.geturl() if hasattr(resp, "geturl") else None
            if final:
                require_https(final)
            status = _status(resp)
            resumed = bool(offset) and status == 206
            if not resumed:
                offset = 0  # 200: range ignored or the validator changed -> start over
            new_validator = resp.headers.get("ETag") or resp.headers.get("Last-Modified")
            if not resumed:
                if new_validator:
                    with open(sidecar, "w", encoding="utf-8") as fh:
                        fh.write(new_validator)
                else:
                    _remove(sidecar)
            total = size
            if total is None and not resumed and resp.headers.get("Content-Length"):
                total = int(resp.headers.get("Content-Length"))
            check_disk(dest, (total - offset) if total else None)
            with open(part, "ab" if resumed else "wb") as fh:
                done = _stream(resp, fh, offset, total, chunk_size, progress)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise FetchError(f"download failed for {url}: {exc}") from exc

    if progress is not None:
        progress(done, total if total is not None else done)
    if size is not None and done != size:
        _reject(part, sidecar, dest)
        raise ChecksumMismatch(dest, size, done, "size")
    digest = sha256_file(part)
    if sha256 is not None and digest != sha256:
        _reject(part, sidecar, dest)
        raise ChecksumMismatch(dest, sha256, digest, "sha256")
    os.replace(part, dest)
    _remove(sidecar)
    return DownloadResult(dest, done, digest, new_validator, resumed=resumed, skipped=False)


def _reject(part: str, sidecar: str, dest: str) -> None:
    """Move rejected bytes to ``<dest>.bad`` and drop the resume sidecar."""
    os.replace(part, dest + ".bad")
    _remove(sidecar)
