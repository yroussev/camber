"""The ``camber lab`` HTTP server: a pure :func:`dispatch_lab` and a thin stdlib handler.

``camber serve`` is GET-only by the OT-safety posture (docs/SECURITY.md §2); the lab is the one
CAMBER server that accepts writes, so it is a **separate** server with its own, narrower rules:

- **Loopback only.** It binds ``127.0.0.1`` and refuses any other address -- there is no
  ``--host``. :func:`make_lab_server` raises ``ValueError`` for anything else.
- **Host allowlist** (DNS rebinding): every request's ``Host`` must be ``127.0.0.1:<port>`` or
  ``localhost:<port>``, else 403.
- **Origin allowlist**: a request carrying an ``Origin`` must come from those two origins; a POST
  must carry one. A browser's ``Sec-Fetch-Site`` other than ``same-origin`` / ``none`` is refused.
- **Authentication on every route** (0.102, #127): another account on the same computer can
  reach a loopback port, so every request -- GET or POST, the delegated read routes included --
  needs the session cookie (``camber-lab-<port>``) or ``Authorization: Bearer <access token>``,
  else 401 (a wrong token is 403). A ``GET`` with a valid ``?token=`` (the launch URL ``camber
  lab`` prints) sets the cookie and redirects (303) to the same URL without the token. See
  :mod:`camber.lab._auth`.
- **CSRF token**: every POST also carries the per-run CSRF token (``X-Camber-Lab-Token``),
  compared with :func:`hmac.compare_digest`; it is only in the lab page, which another origin
  cannot read and which itself needs the session.
- **JSON only** (415 otherwise), **16 KiB body cap** (413, checked on the declared length before
  the body is read), unknown body keys refused (400), and catalog ids only.
- **Writes are jobs**: the only POST routes queue a fetch / ingest / remove job or cancel one.
  A remove needs the typed dataset id (``confirm``); an ingest from a local folder takes an
  absolute folder path that is validated server-side and only read (0.103, #123).
- **Strict CSP** on every HTML response (the lab page pins its script and stylesheet by hash);
  ``nosniff``, ``no-referrer``, ``no-store`` and ``frame-ancestors 'none'`` on everything.

Routes (every one needs the session cookie or the bearer token)::

    GET  /<any>?token=<access token>   set the session cookie, 303 to the URL without the token
    GET  /lab                     the catalog page
    GET  /lab/catalog             catalog + fetched / ingested status + free disk (JSON)
    GET  /lab/jobs[/<id>]         job progress (JSON)
    GET  /lab/reports/<fid>       the audit report for an ingested dataset (built on demand)
    GET  /lab/docs/workbook/<page>.md   a workbook exercise page from the local docs (0.97)
    GET  /ui /facilities /points /history    delegated unchanged to camber.api.server.dispatch
    POST /lab/jobs/fetch          {"ids": [...], "subset"?, "ingest"?, "acknowledge"?}
    POST /lab/jobs/ingest         {"ids": [...], "subset"?, "force"?, "acknowledge"?}
    POST /lab/jobs/from-dir       {"id", "dir", "subset"?, "force"?, "acknowledge"?}  (0.103)
    POST /lab/jobs/remove         {"id", "confirm", "purge_store"?}                   (0.103)
    POST /lab/jobs/<id>/cancel    {}
"""

from __future__ import annotations

import errno
import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

from ..api.server import _UI_CSP, dispatch
from . import _auth
from ._app import DEFAULT_PORT, LAB_HOST, LabApp, LabError
from ._docs import DOCS_CSP, DOCS_ROUTE
from ._ui import LAB_CSP, lab_page_html

BODY_LIMIT = 16 * 1024
TOKEN_HEADER = "X-Camber-Lab-Token"
READ_ROUTES = ("/ui", "/ui/", "/facilities", "/points", "/history")
_FETCH_SITES = ("same-origin", "none")
_FETCH_KEYS = {"ids", "subset", "ingest", "acknowledge"}
_INGEST_KEYS = {"ids", "subset", "force", "acknowledge"}
_FROM_DIR_KEYS = {"id", "dir", "subset", "force", "acknowledge"}
_REMOVE_KEYS = {"id", "confirm", "purge_store"}
_JOB_ROUTES = ("/lab/jobs/fetch", "/lab/jobs/ingest", "/lab/jobs/from-dir", "/lab/jobs/remove")

# A report is standalone HTML built by CAMBER's report code; it is served sandboxed (an opaque
# origin: it cannot call the lab API) and may not load or send anything.
REPORT_CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'; "
    "sandbox allow-scripts allow-popups"
)
JSON_CSP = "default-src 'none'; frame-ancestors 'none'"
# the 401 / 403 page: no script, no style, nothing loaded
AUTH_CSP = "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
# the routes a browser opens as a page: a refused request gets a short HTML page, not JSON
_PAGE_ROUTES = ("/", "/lab", "/lab/", "/ui", "/ui/")
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


def _json(status: int, body: dict):
    return status, body, {"Content-Type": "application/json", "Content-Security-Policy": JSON_CSP}


def _html(status: int, body: str, csp: str):
    return (
        status,
        body,
        {"Content-Type": "text/html; charset=utf-8", "Content-Security-Policy": csp},
    )


def _err(status: int, message: str):
    return _json(status, {"error": message})


def _check_origin(app: LabApp, method: str, h: dict):
    """Host / Sec-Fetch-Site / Origin, for every route; ``None`` when the request may proceed."""
    if h.get("host") not in app.allowed_hosts:
        return _err(403, "host not allowed: the lab answers only 127.0.0.1 / localhost")
    site = h.get("sec-fetch-site")
    if site is not None and site not in _FETCH_SITES:
        return _err(403, f"cross-site request refused (Sec-Fetch-Site: {site})")
    origin = h.get("origin")
    if origin is not None and origin not in app.allowed_origins:
        return _err(403, "origin not allowed")
    if method == "POST" and origin is None:
        return _err(403, "a POST must carry an Origin header")
    return None


def _wants_page(path: str) -> bool:
    return path in _PAGE_ROUTES or path.startswith(("/lab/reports/", "/lab/docs/"))


def _refuse_auth(status: int, path: str, why: str):
    """401 / 403 for a request without valid credentials: a short page or a JSON error."""
    msg = (
        f"{why} Open the URL that `camber lab` printed in its terminal (it ends in ?token=...); "
        "it is also saved in the lab's launch file (see docs/LAB.md, Troubleshooting)."
    )
    hdrs = {"WWW-Authenticate": 'Bearer realm="camber lab"'} if status == 401 else {}
    if not _wants_page(path):
        st, body, h = _err(status, msg)
        return st, body, {**h, **hdrs}
    page = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<title>camber lab: open the URL from the terminal</title></head><body>"
        "<h1>Open the lab from its terminal</h1>"
        f"<p>{html.escape(why)}</p>"
        "<p>Open the URL that <code>camber lab</code> printed in the terminal where it is "
        "running. It looks like <code>http://127.0.0.1:&lt;port&gt;/lab?token=...</code>. "
        "If that terminal is gone, the URL is also saved in the launch file "
        "<code>lab-&lt;port&gt;.url</code> (see the Troubleshooting section of docs/LAB.md). "
        "A lab that was restarted prints a new URL; the old one no longer works.</p>"
        "</body></html>"
    )
    st, body, h = _html(status, page, AUTH_CSP)
    return st, body, {**h, **hdrs}


def _safe_path(path: str) -> str:
    """``path`` for a same-origin ``Location``: never ``//host`` or a backslash trick."""
    if not path.startswith("/") or path.startswith("//") or any(c in path for c in "\\\r\n"):
        return "/lab"
    return path


def _authenticate(app: LabApp, method: str, path: str, query: dict, h: dict):
    """``None`` when the request is authenticated, else the response to send instead.

    A GET with ``?token=`` is the launch URL: a valid token buys the session cookie and a 303 to
    the same URL without the token; a wrong one is 403. Otherwise the request needs the session
    cookie or ``Authorization: Bearer <access token>``.
    """
    if method == "GET" and _auth.TOKEN_PARAM in (query or {}):
        given = (query.get(_auth.TOKEN_PARAM) or [""])[-1]
        if not _auth.same_secret(given, app.access_token):
            return _refuse_auth(403, path, "That lab token is not valid for this run.")
        rest = {k: v for k, v in query.items() if k != _auth.TOKEN_PARAM}
        target = _safe_path(path) + ("?" + urlencode(rest, doseq=True) if rest else "")
        hdrs = {
            "Location": target,
            "Set-Cookie": _auth.session_cookie(app.port, app.session_id),
        }
        status, body, base = _json(303, {"location": target})
        return status, body, {**base, **hdrs}
    bearer = _auth.bearer_token(h.get("authorization"))
    if bearer is not None:
        if _auth.same_secret(bearer, app.access_token):
            return None
        return _refuse_auth(403, path, "That lab token is not valid for this run.")
    cookies = _auth.cookie_value(h.get("cookie"), _auth.cookie_name(app.port))
    if any(_auth.same_secret(c, app.session_id) for c in cookies):
        return None
    if cookies:
        return _refuse_auth(401, path, "This browser's lab session has expired (a restarted lab?).")
    return _refuse_auth(401, path, "This lab needs its access token.")


def _check_post(app: LabApp, h: dict, body):
    """The POST-only checks (after authentication): CSRF token, JSON, body size."""
    token = h.get(TOKEN_HEADER.lower(), "")
    if not _auth.same_secret(token, app.token):
        return _err(403, "missing or invalid CSRF token")
    ctype = h.get("content-type", "").split(";")[0].strip().lower()
    if ctype != "application/json":
        return _err(415, "the lab accepts application/json only")
    try:
        declared = int(h.get("content-length") or 0)
    except ValueError:
        return _err(400, "bad Content-Length")
    if declared > BODY_LIMIT or (body is not None and len(body) > BODY_LIMIT):
        return _err(413, f"request body over {BODY_LIMIT} bytes")
    return None


def _parse_body(body, allowed: set) -> dict:
    try:
        data = json.loads((body or b"{}").decode("utf-8") or "{}")
    except (UnicodeDecodeError, ValueError) as exc:
        raise LabError(400, f"body is not valid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise LabError(400, "body must be a JSON object")
    extra = sorted(set(data) - allowed)
    if extra:
        raise LabError(400, f"unknown field(s): {', '.join(extra)}")
    ack = data.get("acknowledge")
    if ack is not None and not (
        isinstance(ack, dict) and all(isinstance(v, str) for v in ack.values())
    ):
        raise LabError(400, "acknowledge must map dataset ids to the typed ids")
    for flag in ("ingest", "force", "purge_store"):
        if flag in data and not isinstance(data[flag], bool):
            raise LabError(400, f"{flag} must be true or false")
    return data


def dispatch_lab(app: LabApp, method: str, path: str, query: dict, headers: dict, body=b""):
    """Route one request. Returns ``(status, body, headers)``.

    ``body`` is a JSON-serializable dict, or a ``str`` of HTML; ``headers`` carries its
    ``Content-Type`` and ``Content-Security-Policy`` (and ``Location`` for a redirect,
    ``Set-Cookie`` for the token exchange, ``WWW-Authenticate`` for a 401).
    ``headers`` in is the request's headers (any case); ``body`` the raw request body, or ``None``
    when the handler refused to read an oversized one. Pure apart from queueing jobs on ``app``.
    """
    h = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    refused = _check_origin(app, method, h) or _authenticate(app, method, path, query, h)
    if refused is None and method == "POST":
        refused = _check_post(app, h, body)
    if refused is not None:
        return refused
    try:
        if method == "GET":
            return _get(app, path, query)
        if method == "POST":
            return _post(app, path, body)
    except LabError as exc:
        return _err(exc.status, str(exc))
    return _err(405, f"method {method} not allowed")


def _get(app: LabApp, path: str, query: dict):
    if path == "/":
        status, body, hdrs = _json(303, {"location": "/lab"})
        return status, body, {**hdrs, "Location": "/lab"}
    if path in ("/lab", "/lab/"):
        return _html(200, lab_page_html(app.token), LAB_CSP)
    if path == "/lab/catalog":
        return _json(200, app.catalog_view())
    if path == "/lab/jobs":
        return _json(200, {"jobs": [j.view() for j in app.jobs.jobs()]})
    if path in _JOB_ROUTES or (path.startswith("/lab/jobs/") and path.endswith("/cancel")):
        return _err(405, "use POST")
    if path.startswith("/lab/jobs/") and path.count("/") == 3:
        return _json(200, {"job": app.job(path.rsplit("/", 1)[1])})
    if path.startswith("/lab/reports/") and path.count("/") == 3:
        return _html(200, app.report_html(path.rsplit("/", 1)[1]), REPORT_CSP)
    if path.startswith(DOCS_ROUTE):
        return _html(200, app.docs_page(path[len(DOCS_ROUTE) :]), DOCS_CSP)
    if path in READ_ROUTES:
        status, body = dispatch(app.read_api, "GET", path, query)
        if isinstance(body, str):
            return _html(status, body, _UI_CSP + "; frame-ancestors 'none'")
        return _json(status, body)
    return _err(404, f"not found: {path}")


def _post(app: LabApp, path: str, body):
    if path == "/lab/jobs/fetch":
        data = _parse_body(body, _FETCH_KEYS)
        job = app.submit_fetch(
            data.get("ids"),
            subset=data.get("subset"),
            ingest=bool(data.get("ingest", False)),
            acknowledge=data.get("acknowledge"),
        )
        return _json(202, {"job": job.view()})
    if path == "/lab/jobs/ingest":
        data = _parse_body(body, _INGEST_KEYS)
        job = app.submit_ingest(
            data.get("ids"),
            subset=data.get("subset"),
            force=bool(data.get("force", False)),
            acknowledge=data.get("acknowledge"),
        )
        return _json(202, {"job": job.view()})
    if path == "/lab/jobs/from-dir":
        data = _parse_body(body, _FROM_DIR_KEYS)
        job = app.submit_from_dir(
            data.get("id"),
            data.get("dir"),
            subset=data.get("subset"),
            force=bool(data.get("force", False)),
            acknowledge=data.get("acknowledge"),
        )
        return _json(202, {"job": job.view()})
    if path == "/lab/jobs/remove":
        data = _parse_body(body, _REMOVE_KEYS)
        job = app.submit_remove(
            data.get("id"),
            purge_store=bool(data.get("purge_store", False)),
            confirm=data.get("confirm"),
        )
        return _json(202, {"job": job.view()})
    parts = path.split("/")
    if len(parts) == 5 and parts[:3] == ["", "lab", "jobs"] and parts[4] == "cancel":
        _parse_body(body, set())
        return _json(200, {"job": app.cancel(parts[3])})
    if path == "/lab" or path.startswith("/lab/") or path in READ_ROUTES or path == "/":
        return _err(405, "method POST not allowed here")
    return _err(404, f"not found: {path}")


class LabHandler(BaseHTTPRequestHandler):
    """Parses a request, calls :func:`dispatch_lab`, writes the response (``server.app``)."""

    server_version = "camber-lab"
    sys_version = ""

    def _handle(self, method: str) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        body: bytes | None = b""
        if method == "POST":
            if self.headers.get("Transfer-Encoding"):
                self._send(*_err(411, "chunked bodies are not accepted; send Content-Length"))
                self.close_connection = True
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if 0 <= length <= BODY_LIMIT:
                body = self.rfile.read(length) if length else b""
            else:  # never read an oversized (or unparseable) body
                body = None
                self.close_connection = True
        try:
            out = dispatch_lab(
                self.server.app,  # type: ignore[attr-defined]
                method,
                parsed.path,
                query,
                dict(self.headers.items()),
                body,
            )
        except Exception as exc:  # never leak a stack trace over the wire
            out = _err(500, f"internal error: {type(exc).__name__}")
        self._send(*out)

    def _send(self, status: int, body, headers: dict) -> None:
        payload = (body if isinstance(body, str) else json.dumps(body, default=str)).encode("utf-8")
        self.send_response(status)
        for k, v in {**SECURITY_HEADERS, **headers}.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def do_GET(self):  # noqa: N802 (stdlib naming)
        """GET: pages, catalog, jobs, reports and the delegated read routes."""
        self._handle("GET")

    def do_POST(self):  # noqa: N802
        """POST: queue a fetch / ingest / remove job or cancel one (token + JSON + allowlists)."""
        self._handle("POST")

    def do_PUT(self):  # noqa: N802
        """Refused (405) after the request checks."""
        self._handle("PUT")

    def do_DELETE(self):  # noqa: N802
        """Refused (405) after the request checks."""
        self._handle("DELETE")

    def do_PATCH(self):  # noqa: N802
        """Refused (405) after the request checks."""
        self._handle("PATCH")

    def do_HEAD(self):  # noqa: N802
        """Refused (405) after the request checks."""
        self._handle("HEAD")

    def do_OPTIONS(self):  # noqa: N802
        """Refused (405): the lab answers no CORS preflight, so no other origin may POST."""
        self._handle("OPTIONS")

    def log_message(self, *args):
        """Quiet: the lab prints its own one-line status, not a line per request."""
        pass


class _LabHTTPServer(ThreadingHTTPServer):
    daemon_threads = True


class PortInUse(OSError):
    """The lab's port is taken (another ``camber lab`` or another program); the message says
    how to pick another one."""

    def __init__(self, port: int):
        self.port = int(port)
        super().__init__(
            errno.EADDRINUSE,
            f"port {self.port} on {LAB_HOST} is already in use (another `camber lab`, or another "
            f"program, is listening there). Stop it, or pick a free port: "
            f"`camber lab --port {self.port + 1}` (or any free port number)",
        )

    def __str__(self) -> str:
        return str(self.args[1])


def make_lab_server(app: LabApp, *, port: int = DEFAULT_PORT, host: str = LAB_HOST):
    """Create (but don't start) the lab server for ``app`` on ``127.0.0.1:port``.

    ``host`` exists only to be refused: anything but ``127.0.0.1`` raises ``ValueError`` -- the
    lab downloads and writes data, so it is never reachable from another machine. ``port=0``
    binds an ephemeral port; the Host / Origin allowlist follows the bound port. A port that is
    already in use raises :class:`PortInUse` (an ``OSError``) with a hint to use ``--port``.
    """
    if host != LAB_HOST:
        raise ValueError(
            f"camber lab binds {LAB_HOST} only (refused {host!r}): it can download and write "
            "data, so it is never exposed beyond this machine. Use `camber serve` for a "
            "read-only API on another interface."
        )
    try:
        httpd = _LabHTTPServer((LAB_HOST, int(port)), LabHandler)
    except OSError as exc:
        if exc.errno in (errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", -1)):
            raise PortInUse(int(port)) from None
        raise
    httpd.app = app  # type: ignore[attr-defined]
    app.bind(httpd.server_address[1])
    return httpd


def announce(app: LabApp, *, launch_dir=None, out=None) -> str | None:
    """Print the launch URL to the terminal and save it in the private launch file.

    Returns the launch file's path, or ``None`` when it could not be written safely (the reason
    is printed; the lab still runs and the URL is still printed). The URL is a secret: it goes
    to ``out`` (stdout) and a 0600 file only -- never to argv, a log or a browser command line.
    """
    import sys

    out = out or sys.stdout
    url = app.launch_url()
    print(
        "open this URL in your browser (it carries this run's access token; keep it private):",
        file=out,
    )
    print(f"    {url}", file=out)
    try:
        path = _auth.write_launch_file(url, app.port, launch_dir)
    except OSError as exc:
        print(f"warning: launch file not written: {exc}", file=sys.stderr)
        path = None
    else:
        print(f"the URL is also saved, readable by you only, in {path}", file=out)
    print("loopback only; Ctrl-C to stop", file=out, flush=True)
    return path


def serve_lab(app: LabApp, *, port: int = DEFAULT_PORT) -> None:  # pragma: no cover - blocking
    """Run the lab until interrupted (blocking); prints the launch URL, writes the launch file."""
    httpd = make_lab_server(app, port=port)
    path = announce(app)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        app.close()
        _auth.remove_launch_file(path, app.launch_url())
