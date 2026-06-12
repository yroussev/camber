"""HTTP server for the read API (stdlib only -- no web-framework dependency).

Routes are factored into a pure :func:`dispatch` function (method, path, query ->
(status, body)) so the routing is unit-testable without binding a socket; the
:class:`http.server` handler is a thin wrapper that parses the request, calls
``dispatch``, and writes JSON. Read-only: only GET is served.

Endpoints:
  GET /            | /about | /health   -> service info
  GET /sites                            -> {"sites": [...]}
  GET /points?site=&equip=&role=        -> {"points": [...], "count": n}
  GET /history?site=&equip=&role=&start=&end=&limit=  -> {"history": [...], "count": n}
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .read import ReadAPI


def _q(query: dict, *keys):
    """Pick present single-valued query params from a parsed query dict."""
    return {k: query[k][0] for k in keys if query.get(k)}


def dispatch(api: ReadAPI, method: str, path: str, query: dict):
    """Route a request to the read API. Returns ``(status_code, body_dict)``."""
    if method != "GET":
        return 405, {"error": "method not allowed", "method": method}
    if path in ("/", "/about", "/health"):
        return 200, api.about()
    if path == "/sites":
        return 200, api.sites()
    if path == "/points":
        return 200, api.points(**_q(query, "site", "equip", "role"))
    if path == "/history":
        kw = _q(query, "site", "equip", "role", "start", "end", "limit")
        return 200, api.history(**kw)
    return 404, {"error": "not found", "path": path}


class ReadAPIHandler(BaseHTTPRequestHandler):
    """BaseHTTPRequestHandler bound to a ReadAPI via ``server.api``."""

    def do_GET(self):  # noqa: N802 (stdlib naming)
        """Parse the request, dispatch to the read API, and write the JSON response."""
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            status, body = dispatch(self.server.api, "GET", parsed.path, query)
        except Exception as exc:  # never leak a stack trace over the wire
            status, body = 500, {"error": "internal error", "detail": str(exc)}
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
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
    httpd.api = ReadAPI(store)
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
