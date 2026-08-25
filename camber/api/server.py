"""HTTP server for the read API (stdlib only -- no web-framework dependency).

Routes are factored into a pure :func:`dispatch` function (method, path, query ->
(status, body)) so the routing is unit-testable without binding a socket; the
:class:`http.server` handler is a thin wrapper that parses the request, calls
``dispatch``, and writes JSON. Read-only: only GET is served.

Endpoints (facility_id addresses a facility; the legacy ``site=`` param is still accepted):
  GET /            | /about | /health   -> service info
  GET /facilities                       -> {"facilities": [{"facility_id","name"}, ...]}
  GET /sites                            -> {"sites": [...]}   (deprecated alias)
  GET /points?facility_id=&equip=&role=                       -> {"points": [...], "count": n}
  GET /history?facility_id=&equip=&role=&start=&end=&limit=   -> {"history": [...], "count": n}
"""

from __future__ import annotations

import json
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
        kw = _q(query, "facility_id", "site", "equip", "role", "start", "end", "limit")
        return 200, api.history(**kw)
    return 404, {"error": "not found", "path": path}


class ReadAPIHandler(BaseHTTPRequestHandler):
    """BaseHTTPRequestHandler bound to a ReadAPI via ``server.api``."""

    def do_GET(self):  # noqa: N802 (stdlib naming)
        """Parse the request, dispatch, and write the response (JSON, or HTML for ``/ui``)."""
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            status, body = dispatch(self.server.api, "GET", parsed.path, query)
        except Exception as exc:  # never leak a stack trace over the wire
            status, body = 500, {"error": "internal error", "detail": str(exc)}
        self.send_response(status)
        if isinstance(body, str):  # the /ui live-dashboard HTML
            payload = body.encode("utf-8")
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Security-Policy", _UI_CSP)
        else:
            payload = json.dumps(body).encode("utf-8")
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # keep the test/CLI output quiet
        """Suppress the default per-request stderr logging."""
        pass


def make_server(store, *, host: str = "127.0.0.1", port: int = 8080):
    """Create (but don't start) a threading HTTP server bound to ``store``.

    ``port=0`` binds an ephemeral port (read ``server.server_address[1]``). Call
    ``serve_forever()`` to run, or use this in a thread for tests.
    """
    httpd = ThreadingHTTPServer((host, port), ReadAPIHandler)
    httpd.api = ReadAPI(store)  # type: ignore[attr-defined]  # stash API on server for the handler
    return httpd


def serve(store, *, host: str = "127.0.0.1", port: int = 8080):  # pragma: no cover
    """Run the read API until interrupted (blocking)."""
    httpd = make_server(store, host=host, port=port)
    addr = httpd.server_address
    print(f"camber read-api serving on http://{addr[0]}:{addr[1]}  (Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":  # pragma: no cover
    import os
    import sys

    from ..store import ParquetStore

    # argv wins; otherwise env (CAMBER_STORE / _API_HOST / _API_PORT) — the container
    # sets HOST=0.0.0.0 to be reachable, while a bare `python -m` stays on localhost.
    root = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("CAMBER_STORE", "tsdb")
    host = os.environ.get("CAMBER_API_HOST", "127.0.0.1")
    port = int(sys.argv[2]) if len(sys.argv) > 2 else int(os.environ.get("CAMBER_API_PORT", "8080"))
    serve(ParquetStore(root), host=host, port=port)
