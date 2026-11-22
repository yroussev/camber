"""The ``camber a2a`` HTTP server: :class:`A2AServer` answers one request (a pure function of the
method, path, headers and body), and a thin stdlib handler serves it.

Routes::

    GET  /.well-known/agent-card.json   the agent card (no token on a loopback bind)
    POST /a2a                           JSON-RPC 2.0, A2A 1.0 (the bearer token always)

Every request passes, in order: the ``Host`` allowlist (DNS rebinding) and the cross-site check
of :mod:`camber._access` (403), then the bearer token (401 without one, 403 for a wrong one);
the card on a loopback bind is the one exception to the token. A POST must be
``application/json`` (415), give its length (411) and be at most :data:`BODY_LIMIT` bytes (413,
refused before it is read). A JSON-RPC request needs ``A2A-Version: 1.0`` (header or query
parameter); none means 0.3, which is ``VersionNotSupportedError``.
"""

from __future__ import annotations

import json
import logging
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .. import _access
from ..capabilities._grounding import error_info
from . import _protocol as p
from ._tasks import Task, TaskStore

log = logging.getLogger("camber.a2a")

#: the largest request body, in bytes
BODY_LIMIT = 64 * 1024
#: how long a blocking ``SendMessage`` waits past the call's own time cap before it answers with
#: the task as it is (the peer then polls ``GetTask``)
BLOCK_GRACE_SECONDS = 30.0
#: ``ListTasks`` page sizes
DEFAULT_PAGE = 50
MAX_PAGE = 100

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Cross-Origin-Resource-Policy": "same-origin",
}

_METHODS_UNSUPPORTED = {
    "SendStreamingMessage": (
        p.UNSUPPORTED_OPERATION,
        "streaming is not supported (the card "
        "says streaming: false); use SendMessage, then GetTask",
    ),
    "SubscribeToTask": (
        p.UNSUPPORTED_OPERATION,
        "streaming is not supported (the card says streaming: false); poll GetTask",
    ),
    "CreateTaskPushNotificationConfig": (
        p.PUSH_NOT_SUPPORTED,
        "push notifications are not supported",
    ),
    "GetTaskPushNotificationConfig": (p.PUSH_NOT_SUPPORTED, "push notifications are not supported"),
    "ListTaskPushNotificationConfigs": (
        p.PUSH_NOT_SUPPORTED,
        "push notifications are not supported",
    ),
    "DeleteTaskPushNotificationConfig": (
        p.PUSH_NOT_SUPPORTED,
        "push notifications are not supported",
    ),
}


def _valid_id(value: Any) -> bool:
    return isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool))


def _error(id_: Any, code: int, message: str, data: Any = None) -> dict:
    err: dict = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": id_, "error": err}


class _DuplicateKey(ValueError):
    pass


def _no_duplicates(pairs: list) -> dict:
    out: dict = {}
    for k, v in pairs:
        if k in out:
            raise _DuplicateKey(k)
        out[k] = v
    return out


def _decode(body: bytes) -> Any:
    """The JSON of a request body: duplicate keys, NaN and infinities refused."""

    def no_constant(name):
        raise ValueError(f"{name} is not JSON")

    return json.loads(
        body.decode("utf-8"), object_pairs_hook=_no_duplicates, parse_constant=no_constant
    )


class A2AServer:
    """The A2A agent over a capability registry, under one call context.

    ``registry`` is :func:`camber.capabilities.default_registry` (or any registry of its shape),
    ``context`` the :class:`camber.capabilities.CallContext` every task runs under (store,
    allowlist, configs, trust floor), ``runner`` a :class:`camber.mcp.WorkerPool` or a
    :class:`~camber.a2a.LocalRunner`. ``token`` is the bearer token; ``bind_host`` and ``port``
    decide the Host allowlist and whether the card needs the token (any non-loopback bind).
    ``served`` are the facilities the extended card names (default: the allowlist).
    """

    def __init__(
        self,
        registry: Any,
        context: Any,
        runner: Any,
        *,
        token: str,
        bind_host: str = "127.0.0.1",
        port: int = 0,
        allowed_hosts=(),
        version: str | None = None,
        served: Any = None,
    ):
        if not token or len(str(token)) < _access.MIN_TOKEN_LEN:
            raise ValueError(
                f"the access token must be at least {_access.MIN_TOKEN_LEN} characters"
            )
        if version is None:
            from .. import __version__ as version
        self.registry = registry
        self.context = context
        self.token = str(token)
        self.version = str(version)
        self.bind_host = str(bind_host)
        self.loopback = _access.is_loopback(bind_host)
        self.hosts = _access.HostAllowlist(bind_host, int(port), allowed_hosts)
        self.served = sorted(served if served is not None else context.allowed_facilities)
        self.tasks = TaskStore(runner, self.version)
        for skill in p.SKILLS:
            registry.get(skill.capability)  # a registry without the capability: fail at start

    # ------------------------------------------------------------------ HTTP

    def handle(self, method: str, target: str, headers: dict, body: bytes = b"") -> tuple:
        """``(status, headers, body bytes)`` for one HTTP request (``headers``: any case)."""
        h = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
        url = urlparse(target)
        query = parse_qs(url.query)
        path = url.path
        if not self.hosts.allows(h.get("host")):
            return self._http(403, {"error": "Host not allowed"})
        crossed = _access.cross_site_refusal(self.hosts, h, fetch_sites=_access.STRICT_FETCH_SITES)
        if crossed is not None:
            return self._http(403, {"error": f"cross-site request refused ({crossed[0]})"})
        card = path == p.CARD_PATH
        if not (card and self.loopback and method in ("GET", "HEAD")):
            refused = self._auth(h)
            if refused is not None:
                return refused
        if card:
            if method not in ("GET", "HEAD"):
                return self._http(405, {"error": "the agent card is GET only"}, allow="GET")
            return self._http(200, self.card(h.get("host")), cache="no-cache")
        if path != p.RPC_PATH:
            return self._http(404, {"error": "not found"})
        if method != "POST":
            return self._http(405, {"error": "JSON-RPC requests are POSTs"}, allow="POST")
        ctype = h.get("content-type", "").split(";")[0].strip().lower()
        if ctype not in ("application/json", "application/a2a+json"):
            return self._http(415, {"error": "send application/json"})
        version = (h.get(p.VERSION_HEADER.lower()) or "").strip()
        if not version:
            version = ((query.get(p.VERSION_HEADER) or [""])[-1]).strip()
        reply = self.rpc(body, version or p.DEFAULT_VERSION)
        extra = {}
        wanted = [e.strip() for e in h.get(p.EXTENSIONS_HEADER.lower(), "").split(",")]
        if p.GROUNDED_FACTS_URI in wanted:
            extra[p.EXTENSIONS_HEADER] = p.GROUNDED_FACTS_URI
        return self._http(200, reply, extra=extra)

    def _auth(self, h: dict):
        bearer = _access.bearer_token(h.get("authorization"))
        if bearer is None:
            return self._http(401, {"error": "this agent needs its bearer token"}, challenge=True)
        if not _access.same_secret(bearer, self.token):
            return self._http(403, {"error": "that token is not valid for this run"})
        return None

    @staticmethod
    def _http(status: int, body: Any, *, allow=None, challenge=False, cache=None, extra=None):
        data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        hdrs = {"Content-Type": "application/json", **SECURITY_HEADERS}
        if cache:
            hdrs["Cache-Control"] = cache
        if allow:
            hdrs["Allow"] = allow
        if challenge:
            hdrs["WWW-Authenticate"] = 'Bearer realm="camber a2a"'
        hdrs.update(extra or {})
        return status, hdrs, data

    def card(self, host: str | None = None, *, extended: bool = False) -> dict:
        """The agent card; its interface URL uses the request's (allowed) ``Host``."""
        if host:
            base = f"http://{host}"
        else:
            name = (
                "127.0.0.1"
                if _access.is_wildcard(self.bind_host)
                else _access.host_name(self.bind_host)
            )
            base = f"http://{name}:{self.hosts.port}"
        return p.agent_card(
            base + p.RPC_PATH, self.version, facilities=self.served, extended=extended
        )

    # ------------------------------------------------------------------ JSON-RPC

    def rpc(self, body: bytes, version: str = p.PROTOCOL_VERSION) -> dict:
        """The JSON-RPC response to one request body."""
        try:
            msg = _decode(body)
        except (UnicodeDecodeError, ValueError):
            return _error(None, p.PARSE_ERROR, "Parse error")
        if not isinstance(msg, dict):
            return _error(None, p.INVALID_REQUEST, "Invalid Request: one JSON-RPC object, no batch")
        id_ = msg.get("id")
        if not _valid_id(id_):
            return _error(None, p.INVALID_REQUEST, "Invalid Request: id must be a string or number")
        method = msg.get("method")
        if msg.get("jsonrpc") != "2.0" or not isinstance(method, str):
            return _error(id_, p.INVALID_REQUEST, "Invalid Request")
        if version not in p.SUPPORTED_VERSIONS:
            shown = version if len(version) <= 16 and version.isprintable() else "?"
            return _error(
                id_,
                p.VERSION_NOT_SUPPORTED,
                f"A2A version {shown} is not supported; this agent speaks "
                + ", ".join(p.SUPPORTED_VERSIONS)
                + f" (send the {p.VERSION_HEADER} header)",
                [
                    p.error_detail(
                        p.REASONS[p.VERSION_NOT_SUPPORTED],
                        {"requested": shown, "supported": ",".join(p.SUPPORTED_VERSIONS)},
                    )
                ],
            )
        params = msg.get("params", {})
        if params is None:
            params = {}
        try:
            result = self._dispatch(method, params)
        except p.RpcError as e:
            return _error(id_, e.code, e.message, e.data)
        except Exception:  # noqa: BLE001 - a bug here must not end the server
            log.exception("internal error answering %s", method[:40])
            return _error(id_, p.INTERNAL_ERROR, "Internal error")
        return {"jsonrpc": "2.0", "id": id_, "result": result}

    def _dispatch(self, method: str, params: Any) -> Any:
        if method in _METHODS_UNSUPPORTED:
            code, why = _METHODS_UNSUPPORTED[method]
            raise p.RpcError(code, why)
        handler = {
            "SendMessage": self._send,
            "GetTask": self._get_task,
            "CancelTask": self._cancel_task,
            "ListTasks": self._list_tasks,
            "GetExtendedAgentCard": self._extended_card,
        }.get(method)
        if handler is None:
            raise p.RpcError(p.METHOD_NOT_FOUND, "Method not found")
        if not isinstance(params, dict):
            raise p.RpcError(p.INVALID_PARAMS, "Invalid params: params must be an object")
        return handler(params)

    def _extended_card(self, params: dict) -> dict:
        p._check_keys(params, frozenset({"tenant"}), "params")
        p.check_tenant(params)
        return self.card(extended=True)

    def _send(self, params: dict) -> dict:
        req = p.parse_send(params)
        if req.task_id is not None:
            self.tasks.get(req.task_id)  # TaskNotFoundError for an unknown one
            raise p.RpcError(
                p.UNSUPPORTED_OPERATION,
                "the tasks of this agent take no follow-up messages; send a new message "
                "without taskId",
            )
        cap = self.registry.get(req.skill.capability)
        task = Task(req.skill, req.arguments, req.message, req.context_id or p.new_id())
        refused = self._precheck(cap, req.arguments)
        if refused is not None:
            self.tasks.reject(task, refused)
        else:
            self.tasks.start(task, self._context())
            if not req.return_immediately:
                caps = getattr(self.context, "caps", None)
                cap_s = float(getattr(caps, "max_seconds", 120.0))
                task.done.wait(cap_s + BLOCK_GRACE_SECONDS)
        return {"task": self.tasks.snapshot(task, history_length=req.history_length)}

    def _precheck(self, cap: Any, arguments: dict) -> dict | None:
        """The input schema and the facility policy, before a worker is used: an error (the task
        is rejected) or ``None``. Invalid input is a JSON-RPC ``-32602``; the policy check is
        repeated by the registry in the worker, which decides when this one cannot."""
        from ..capabilities._schema import validate

        errors = validate(arguments, cap.input_schema)
        if errors:
            raise p.RpcError(
                p.INVALID_PARAMS,
                ("Invalid params: the skill input: " + "; ".join(errors[:3]))[:600],
                [p.bad_request(errors[:20])],
            )
        try:
            from ..capabilities._policy import check_arguments

            check_arguments(self.context, arguments)
        except Exception as exc:  # noqa: BLE001 - only a refusal decides here
            code = getattr(exc, "code", None)
            if code in ("not_allowed", "refused_licence"):
                from ..capabilities._policy import known_roots, redact
                from ..capabilities._sanitize import one_line

                message = redact(
                    one_line(str(getattr(exc, "message", ""))), *known_roots(self.context)
                )
                return error_info(code, message, getattr(exc, "data", None))
        return None

    def _context(self) -> Any:
        import dataclasses

        ctx = self.context
        if dataclasses.is_dataclass(ctx) and not isinstance(ctx, type):
            return dataclasses.replace(ctx, caller="a2a")
        return ctx

    def _get_task(self, params: dict) -> dict:
        p._check_keys(params, frozenset({"tenant", "id", "historyLength"}), "params")
        p.check_tenant(params)
        task = self.tasks.get(params.get("id"))
        return self.tasks.snapshot(task, history_length=p.history_length(params))

    def _cancel_task(self, params: dict) -> dict:
        p._check_keys(params, frozenset({"tenant", "id", "metadata"}), "params")
        p.check_tenant(params)
        task = self.tasks.get(params.get("id"))
        self.tasks.cancel(task)
        return self.tasks.snapshot(task)

    def _list_tasks(self, params: dict) -> dict:
        keys = {
            "tenant",
            "contextId",
            "status",
            "pageSize",
            "pageToken",
            "historyLength",
            "statusTimestampAfter",
            "includeArtifacts",
        }
        p._check_keys(params, frozenset(keys), "params")
        p.check_tenant(params)
        size = params.get("pageSize")
        if size is None:
            size = DEFAULT_PAGE
        if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= MAX_PAGE:
            raise p.RpcError(p.INVALID_PARAMS, f"Invalid params: pageSize must be 1 to {MAX_PAGE}")
        token = params.get("pageToken") or ""
        if not isinstance(token, str) or (token and not token.isdigit()) or len(token) > 9:
            raise p.RpcError(
                p.INVALID_PARAMS, "Invalid params: pageToken is not one this agent gave"
            )
        start = int(token) if token else 0
        context_id = params.get("contextId")
        status = params.get("status")
        if status in (None, "", "TASK_STATE_UNSPECIFIED"):
            status = None
        elif status not in p.TASK_STATES:
            raise p.RpcError(p.INVALID_PARAMS, "Invalid params: status is not a task state")
        after = params.get("statusTimestampAfter")
        if after is not None:
            try:
                after = p.parse_time(after)
            except ValueError:
                raise p.RpcError(
                    p.INVALID_PARAMS, "Invalid params: statusTimestampAfter is no timestamp"
                ) from None
        with_artifacts = params.get("includeArtifacts", False)
        if not isinstance(with_artifacts, bool):
            raise p.RpcError(p.INVALID_PARAMS, "Invalid params: includeArtifacts: a boolean")
        hist = p.history_length(params)
        rows = []
        for task in reversed(self.tasks.all()):  # the newest first
            snap = self.tasks.snapshot(task, history_length=hist, artifacts=with_artifacts)
            if context_id not in (None, "") and snap["contextId"] != context_id:
                continue
            if status is not None and snap["status"]["state"] != status:
                continue
            if after is not None and p.parse_time(snap["status"]["timestamp"]) <= after:
                continue
            rows.append(snap)
        page = rows[start : start + size]
        nxt = start + size
        return {
            "tasks": page,
            "nextPageToken": str(nxt) if nxt < len(rows) else "",
            "pageSize": size,
            "totalSize": len(rows),
        }


# --------------------------------------------------------------------------- the stdlib server


class A2AHandler(BaseHTTPRequestHandler):
    """Hands each request to ``server.agent`` (:class:`A2AServer`)."""

    server_version = "camber-a2a"
    sys_version = ""

    def _handle(self, method: str) -> None:
        agent: A2AServer = self.server.agent  # type: ignore[attr-defined]
        body = b""
        t0 = time.monotonic()
        if method == "POST":
            refused = self._body_problem()
            if refused is not None:
                status, payload = refused
                self._send(*agent._http(status, {"error": payload}), method)
                return
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
        try:
            status, hdrs, data = agent.handle(method, self.path, dict(self.headers.items()), body)
        except Exception:  # noqa: BLE001 - last resort; handle answers its own errors
            log.exception("internal error answering a request")
            status, hdrs, data = agent._http(500, {"error": "internal error"})
        self._send(status, hdrs, data, method)
        if status >= 400:
            log.info("%s refused with %d in %.2fs", method, status, time.monotonic() - t0)

    def _body_problem(self):
        if self.headers.get("Transfer-Encoding"):
            return 411, "send Content-Length, not a chunked body"
        raw = self.headers.get("Content-Length")
        if raw is None or not raw.strip().isdigit():
            return 411, "send Content-Length"
        if int(raw) > BODY_LIMIT:
            self.close_connection = True
            return 413, f"the body is over {BODY_LIMIT} bytes"
        return None

    def _send(self, status: int, hdrs: dict, data: bytes, method: str) -> None:
        self.send_response(status)
        for k, v in hdrs.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if method != "HEAD":
            self.wfile.write(data)

    def do_GET(self):  # noqa: N802 (stdlib naming)
        self._handle("GET")

    def do_HEAD(self):  # noqa: N802
        self._handle("HEAD")

    def do_POST(self):  # noqa: N802
        self._handle("POST")

    def do_PUT(self):  # noqa: N802
        self._handle("PUT")

    def do_DELETE(self):  # noqa: N802
        self._handle("DELETE")

    def do_PATCH(self):  # noqa: N802
        self._handle("PATCH")

    def do_OPTIONS(self):  # noqa: N802
        self._handle("OPTIONS")

    def log_message(self, *args):  # requests are logged by the agent, not to stderr raw
        pass


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class _Server6(_Server):
    import socket as _socket

    address_family = _socket.AF_INET6


def make_server(host: str, port: int, build_agent) -> ThreadingHTTPServer:
    """Bind ``host:port`` (``port`` 0: any free one) and attach the agent ``build_agent(port)``
    returns (it needs the bound port for its Host allowlist). Not started: call
    ``serve_forever()``."""
    cls = _Server6 if ":" in str(host) else _Server
    bind = str(host)[1:-1] if str(host).startswith("[") else str(host)
    httpd = cls((bind, int(port)), A2AHandler)
    try:
        httpd.agent = build_agent(httpd.server_address[1])  # type: ignore[attr-defined]
    except BaseException:
        httpd.server_close()
        raise
    return httpd
