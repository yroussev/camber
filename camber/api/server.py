"""HTTP server for the read API (stdlib only -- no web-framework dependency).

Routes are factored into a pure :func:`dispatch` function (method, path, query ->
(status, body)) so the routing is unit-testable without binding a socket; the
:class:`http.server` handler is a thin wrapper that parses the request, calls
``dispatch``, and writes JSON. Read-only: only GET is served (any other method is 405).

Request checks (0.103, #128), in :func:`check_request` before ``dispatch``:

- **Host allowlist** against DNS rebinding: ``127.0.0.1`` / ``localhost`` / ``[::1]`` at the bound
  port when bound to loopback (or every interface), the bound host, and any ``--allow-host`` /
  ``CAMBER_API_ALLOWED_HOSTS`` name; anything else is 403. Binding ``0.0.0.0`` / ``::`` needs an
  explicit allowlist.
- **Optional token auth** (``auth="token"`` / ``camber serve --auth token``): the ``camber lab``
  model (:mod:`camber._access`) -- a printed launch URL with ``?token=``, exchanged for an
  ``HttpOnly; SameSite=Strict`` session cookie, or ``Authorization: Bearer <token>``.

Endpoints (facility_id addresses a facility; the legacy ``site=`` param is still accepted):
  GET /            | /about | /health   -> service info
  GET /facilities   -> {"facilities": [{"facility_id","name","display_name","state"}, ...]}
  GET /sites                            -> {"sites": [...]}   (deprecated alias)
  GET /points?facility_id=&equip=&role=                       -> {"points": [...], "count": n}
  GET /history?facility_id=&equip=&role=&start=&end=&limit=&max_points=
                                   -> {"history": [...], "count": n, "source_count", "first", ...}
"""

from __future__ import annotations

import html
import json
import os
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .read import ReadAPI
from .ui import live_dashboard_html

# Strict same-origin CSP for the inline-JS/CSS live dashboard (the app ships no external asset; the
# only network it does is the same-origin fetch/poll of the read-only JSON endpoints).
_UI_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; "
    "object-src 'none'"
)


def _q(query: dict, *keys):
    """Pick present single-valued query params from a parsed query dict."""
    return {k: query[k][0] for k in keys if query.get(k)}


def dispatch(api: ReadAPI, method: str, path: str, query: dict):
    """Route a request to the read API. Returns ``(status_code, body)``.

    ``body`` is a JSON-serializable dict for every endpoint except the live dashboard route
    ``/ui``, which returns the dashboard HTML as a ``str`` (the handler serves it as ``text/html``).
    """
    if method != "GET":
        return 405, {"error": "method not allowed", "method": method}
    if path in ("/ui", "/ui/"):  # the live web dashboard (HTML; fetches the JSON endpoints below)
        return 200, live_dashboard_html()
    if path in ("/", "/about", "/health"):
        return 200, api.about()
    if path == "/facilities":
        return 200, api.facilities()
    if path == "/sites":  # deprecated alias
        return 200, api.sites()
    if path == "/points":
        return 200, api.points(**_q(query, "facility_id", "site", "equip", "role"))
    if path == "/history":
        kw = _q(
            query, "facility_id", "site", "equip", "role", "start", "end", "limit", "max_points"
        )
        try:
            return 200, api.history(**kw)
        except ValueError as exc:  # a malformed start / end / limit / max_points (0.103, #122)
            return 400, {"error": "bad request", "detail": str(exc)}
    return 404, {"error": "not found", "path": path}


_AUTH_MODES = ("none", "token")
#: the env var with extra allowed ``Host`` names (comma-separated), like ``--allow-host``
ALLOWED_HOSTS_ENV = "CAMBER_API_ALLOWED_HOSTS"
_COOKIE_PREFIX = "camber-serve-"
_LAUNCH_PREFIX = "serve"
_AUTH_CSP = "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
_PAGE_ROUTES = ("/ui", "/ui/")


class _ServeAccess:
    """Per-run request checks of one ``camber serve``: the Host allowlist and, with
    ``auth="token"``, the access token and session id (the ``camber lab`` model, #127/#128)."""

    def __init__(self, bind_host: str, port: int, *, allowed_hosts=(), auth=None, token=None):
        from .._access import MIN_TOKEN_LEN, HostAllowlist, new_secret

        self.bind_host = bind_host
        self.port = int(port)
        self.hosts = HostAllowlist(bind_host, self.port, allowed_hosts)
        self.auth = auth or "none"
        self.access_token: str | None = None
        self.session_id: str | None = None
        if self.auth == "token":
            if token is not None and len(str(token)) < MIN_TOKEN_LEN:
                raise ValueError(f"access_token must be at least {MIN_TOKEN_LEN} characters")
            self.access_token = str(token) if token else new_secret()
            self.session_id = new_secret()

    @property
    def cookie_name(self) -> str:
        return f"{_COOKIE_PREFIX}{self.port}"

    def launch_url(self, path: str = "/ui") -> str:
        """The URL that opens the viewer (with ``?token=`` when token auth is on)."""
        from urllib.parse import quote

        from .._access import TOKEN_PARAM, host_name, is_wildcard

        host = "127.0.0.1" if is_wildcard(self.bind_host) else host_name(self.bind_host)
        url = f"http://{host}:{self.port}{path}"
        if self.access_token:
            url += f"?{TOKEN_PARAM}={quote(self.access_token, safe='')}"
        return url


def _refusal(status: int, path: str, why: str, *, auth: bool):
    """A 401 / 403: a short HTML page for ``/ui``, else a JSON error."""
    hint = (
        " Open the URL that `camber serve --auth token` printed in its terminal (it ends in "
        "?token=...); it is also saved in its launch file serve-<port>.url (docs/CLI.md)."
        if auth
        else ""
    )
    hdrs = {"Cache-Control": "no-store"}
    if status == 401:
        hdrs["WWW-Authenticate"] = 'Bearer realm="camber serve"'
    if path not in _PAGE_ROUTES:
        return status, {"error": why + hint}, hdrs
    page = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<title>camber serve: access refused</title></head><body>"
        f"<h1>Access refused</h1><p>{html.escape(why + hint)}</p></body></html>"
    )
    return status, page, {**hdrs, "Content-Security-Policy": _AUTH_CSP}


def check_request(access, method: str, path: str, query: dict, headers: dict):
    """The request checks before :func:`dispatch`. ``None`` when the request may proceed, else
    ``(status, body, extra headers)`` to send instead.

    1. **Host allowlist** (every request, every method): a ``Host`` not in ``access.hosts`` is 403
       -- a DNS-rebinding page sends its own name.
    2. **Token auth** (``auth="token"`` only): a ``GET`` with ``?token=`` exchanges a valid token
       for the session cookie and a 303 to the same URL without it (a wrong one is 403); else the
       request needs the session cookie or ``Authorization: Bearer <access token>`` (401 without,
       403 for a wrong bearer token). ``GET /health`` without credentials answers ``{"ok": true}``
       only, so a container health check needs no secret.
    """
    from urllib.parse import urlencode

    from .. import _access

    h = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    if not access.hosts.allows(h.get("host")):
        return _refusal(
            403,
            path,
            "Host not allowed. camber serve answers only the names it is bound to; add "
            "--allow-host NAME (or CAMBER_API_ALLOWED_HOSTS) for a proxy or DNS name.",
            auth=False,
        )
    if access.auth != "token":
        return None
    if method == "GET" and _access.TOKEN_PARAM in (query or {}):
        given = (query.get(_access.TOKEN_PARAM) or [""])[-1]
        if not _access.same_secret(given, access.access_token):
            return _refusal(403, path, "That access token is not valid for this run.", auth=True)
        rest = {k: v for k, v in query.items() if k != _access.TOKEN_PARAM}
        target = _access.safe_path(path, "/ui") + (
            "?" + urlencode(rest, doseq=True) if rest else ""
        )
        hdrs = {
            "Location": target,
            "Set-Cookie": _access.session_cookie(access.cookie_name, access.session_id),
            "Cache-Control": "no-store",
        }
        return 303, {"location": target}, hdrs
    bearer = _access.bearer_token(h.get("authorization"))
    if bearer is not None:
        if _access.same_secret(bearer, access.access_token):
            return None
        return _refusal(403, path, "That access token is not valid for this run.", auth=True)
    cookies = _access.cookie_value(h.get("cookie"), access.cookie_name)
    if any(_access.same_secret(c, access.session_id) for c in cookies):
        return None
    if method == "GET" and path == "/health":
        return 200, {"ok": True}, {}
    if cookies:
        return _refusal(
            401, path, "This browser's session has expired (a restarted server?).", auth=True
        )
    return _refusal(401, path, "This server needs its access token.", auth=True)


class ReadAPIHandler(BaseHTTPRequestHandler):
    """BaseHTTPRequestHandler bound to a ReadAPI via ``server.api`` (and its request checks via
    ``server.access``). GET only: every other method is 405 after the Host check."""

    def _handle(self, method: str) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        extra: dict = {}
        try:
            access = getattr(self.server, "access", None)
            refused = (
                check_request(access, method, parsed.path, query, dict(self.headers.items()))
                if access is not None
                else None
            )
            if refused is not None:
                status, body, extra = refused
            else:
                status, body = dispatch(self.server.api, method, parsed.path, query)  # type: ignore[attr-defined]
        except Exception as exc:  # never leak a stack trace over the wire
            status, body = 500, {"error": "internal error", "detail": str(exc)}
        self.send_response(status)
        if isinstance(body, str):  # the /ui live-dashboard HTML (or a refusal page)
            payload = body.encode("utf-8")
            self.send_header("Content-Type", "text/html; charset=utf-8")
            if "Content-Security-Policy" not in extra:
                self.send_header("Content-Security-Policy", _UI_CSP)
        else:
            payload = json.dumps(body).encode("utf-8")
            self.send_header("Content-Type", "application/json")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in extra.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if method != "HEAD":
            self.wfile.write(payload)

    def do_GET(self):  # noqa: N802 (stdlib naming)
        """Parse the request, check it, dispatch, and write the response (JSON, or HTML for
        ``/ui``)."""
        self._handle("GET")

    def do_POST(self):  # noqa: N802
        """Refused (405): the read API is GET-only."""
        self._handle("POST")

    def do_PUT(self):  # noqa: N802
        """Refused (405)."""
        self._handle("PUT")

    def do_DELETE(self):  # noqa: N802
        """Refused (405)."""
        self._handle("DELETE")

    def do_PATCH(self):  # noqa: N802
        """Refused (405)."""
        self._handle("PATCH")

    def do_HEAD(self):  # noqa: N802
        """Refused (405), with no body."""
        self._handle("HEAD")

    def do_OPTIONS(self):  # noqa: N802
        """Refused (405): no CORS preflight is answered."""
        self._handle("OPTIONS")

    def log_message(self, *args):  # keep the test/CLI output quiet
        """Suppress the default per-request stderr logging."""
        pass


class _ReadHTTPServer(ThreadingHTTPServer):
    daemon_threads = True


class _ReadHTTPServer6(_ReadHTTPServer):
    address_family = socket.AF_INET6


def _resolve_allowed_hosts(allowed_hosts) -> list:
    """``allowed_hosts`` (a list, or a comma-separated string); ``None`` reads the env var."""
    from .._access import parse_host_list

    if allowed_hosts is None:
        return parse_host_list(os.environ.get(ALLOWED_HOSTS_ENV))
    if isinstance(allowed_hosts, str):
        return parse_host_list(allowed_hosts)
    return [str(h).strip() for h in allowed_hosts if str(h).strip()]


def make_server(
    store,
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    allowed_hosts=None,
    auth: str | None = None,
    access_token: str | None = None,
):
    """Create (but don't start) a threading HTTP server bound to ``store``.

    ``port=0`` binds an ephemeral port (read ``server.server_address[1]``). Call
    ``serve_forever()`` to run, or use this in a thread for tests.

    0.103 (#128): every request's ``Host`` must be on the allowlist (403 otherwise): the loopback
    names when bound to loopback or every interface, the bound host, and ``allowed_hosts`` (a list
    or comma-separated string of ``name`` / ``name:port``; ``None`` reads
    ``CAMBER_API_ALLOWED_HOSTS``; ``"*"`` allows any Host, which is unsafe). Binding every
    interface (``0.0.0.0`` / ``::``) without an explicit allowlist raises ``ValueError``.
    ``auth="token"`` requires the per-run access token (``server.access.launch_url()``) or its
    session cookie on every route; ``access_token`` fixes the token (16+ characters).
    """
    from .._access import is_wildcard

    if auth not in (None, *_AUTH_MODES):
        raise ValueError(f"auth must be one of {', '.join(_AUTH_MODES)} (got {auth!r})")
    extra = _resolve_allowed_hosts(allowed_hosts)
    if is_wildcard(host) and not extra:
        raise ValueError(
            f"refusing to bind every interface ({host or '0.0.0.0'!s}) without a Host allowlist: "
            "pass --allow-host NAME (repeatable) or set CAMBER_API_ALLOWED_HOSTS to the names "
            "clients use to reach this server (--allow-host '*' turns the DNS-rebinding check "
            "off; unsafe)"
        )
    cls = _ReadHTTPServer6 if ":" in str(host) else _ReadHTTPServer
    bind = str(host)[1:-1] if str(host).startswith("[") else host
    httpd = cls((bind, port), ReadAPIHandler)
    httpd.api = ReadAPI(store)  # type: ignore[attr-defined]  # stash API on server for the handler
    httpd.access = _ServeAccess(  # type: ignore[attr-defined]
        host, httpd.server_address[1], allowed_hosts=extra, auth=auth, token=access_token
    )
    return httpd


def _announce(httpd, *, out=None, launch_dir=None):
    """Print the URL, the allowlist and any warning; with token auth, write the launch file.
    Returns the launch file's path (or ``None``)."""
    from .. import _access

    out = out or sys.stdout
    access = httpd.access
    url = access.launch_url()
    path = None
    if access.auth == "token":
        print(
            "camber read-api: open this URL in your browser (it carries this run's access "
            "token; keep it private):",
            file=out,
        )
        print(f"    {url}", file=out)
        try:
            path = _access.write_launch_file(url, access.port, launch_dir, prefix=_LAUNCH_PREFIX)
        except OSError as exc:
            print(f"warning: launch file not written: {exc}", file=sys.stderr)
        else:
            print(f"the URL is also saved, readable by you only, in {path}", file=out)
    else:
        print(f"camber read-api serving on {url}  (read-only, GET-only)", file=out)
    print(f"allowed Host values: {access.hosts.describe()}", file=out)
    if not _access.is_loopback(access.bind_host) and access.auth != "token":
        print(
            f"warning: bound to {access.bind_host or '0.0.0.0'} with no authentication: anyone "
            "who can reach this address can read every facility, point and history. Use "
            "--auth token, or put an authenticating reverse proxy in front (docs/SECURITY.md).",
            file=sys.stderr,
        )
    if access.hosts.any:
        print(
            "warning: --allow-host '*' turns the Host check off (DNS rebinding is possible).",
            file=sys.stderr,
        )
    print("Ctrl-C to stop", file=out, flush=True)
    return path


def serve(
    store,
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    allowed_hosts=None,
    auth: str | None = None,
    access_token: str | None = None,
):  # pragma: no cover
    """Run the read API until interrupted (blocking). Arguments as :func:`make_server`."""
    from .._access import remove_launch_file

    httpd = make_server(
        store,
        host=host,
        port=port,
        allowed_hosts=allowed_hosts,
        auth=auth,
        access_token=access_token,
    )
    path = _announce(httpd)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        remove_launch_file(path, httpd.access.launch_url())


if __name__ == "__main__":  # pragma: no cover
    from ..store import ParquetStore

    # argv wins; otherwise env (CAMBER_STORE / _API_HOST / _API_PORT / _API_ALLOWED_HOSTS /
    # _API_AUTH / _API_TOKEN) -- the container sets HOST=0.0.0.0 to be reachable, plus an
    # allowlist; a bare `python -m` stays on localhost.
    root = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("CAMBER_STORE", "tsdb")
    host = os.environ.get("CAMBER_API_HOST", "127.0.0.1")
    port = int(sys.argv[2]) if len(sys.argv) > 2 else int(os.environ.get("CAMBER_API_PORT", "8080"))
    try:
        serve(
            ParquetStore(root),
            host=host,
            port=port,
            auth=os.environ.get("CAMBER_API_AUTH") or None,
            access_token=os.environ.get("CAMBER_API_TOKEN") or None,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
