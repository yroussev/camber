"""The MCP message layer: JSON-RPC 2.0 messages in, JSON-RPC 2.0 messages out (no I/O here).

:class:`McpServer` serves the capability registry (``camber.capabilities``) to an MCP client in
both protocol eras:

- **2026-07-28 (modern, stateless).** Every request carries its protocol version and client
  capabilities in ``params._meta``; the server answers each request on its own, puts
  ``resultType: "complete"`` and ``_meta["io.modelcontextprotocol/serverInfo"]`` on every result
  and ``ttlMs`` / ``cacheScope`` on the cacheable ones, implements ``server/discover`` and refuses
  an unknown version with ``UnsupportedProtocolVersion`` (-32022).
- **2025-11-25 (legacy, handshake).** ``initialize`` negotiates a version for the rest of the
  process (stdio is one session), ``notifications/initialized`` follows, ``ping`` answers ``{}``.
  2025-06-18 is accepted too: the subset served here is the same in both.

The era is chosen per message as the 2026-07-28 versioning page describes for a dual-era server:
a request carrying ``io.modelcontextprotocol/protocolVersion`` in ``_meta`` is served statelessly;
``initialize`` selects legacy semantics for the process; a request with neither is malformed
(-32602) unless a legacy session is open. Nothing here writes: tools are the registry's read-only
capabilities, and resources, subscriptions, roots, sampling and logging are not offered.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import math
import time
from typing import Any

log = logging.getLogger("camber.mcp")

#: The protocol revision served statelessly (per-request ``_meta``).
MODERN_VERSIONS: tuple[str, ...] = ("2026-07-28",)
#: Revisions negotiated by the ``initialize`` handshake; the first is offered when the client asks
#: for one this server does not know.
LEGACY_VERSIONS: tuple[str, ...] = ("2025-11-25", "2025-06-18")
#: Every revision this server speaks, newest first (``server/discover``'s ``supportedVersions`` and
#: the ``supported`` list of an ``UnsupportedProtocolVersion`` error).
SUPPORTED_VERSIONS: tuple[str, ...] = MODERN_VERSIONS + LEGACY_VERSIONS

#: Freshness hint (``ttlMs``) on the cacheable results: the tool and prompt lists are fixed for the
#: life of the process, so five minutes only bounds how long a client trusts a list across restarts.
LIST_TTL_MS = 300_000
#: ``cacheScope`` of the cacheable results. The lists hold no facility data, but a launch is one
#: user's configuration (store, allowlist), so shared caches are not invited.
CACHE_SCOPE = "private"

# JSON-RPC 2.0 and MCP error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
UNSUPPORTED_PROTOCOL_VERSION = -32022

_PV = "io.modelcontextprotocol/protocolVersion"
_CLIENT_CAPS = "io.modelcontextprotocol/clientCapabilities"
_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"
_SERVER_INFO = "io.modelcontextprotocol/serverInfo"

#: what this server offers: tools, prompts and completion of the prompts' arguments (#304)
_CAPABILITIES: dict[str, dict] = {"tools": {}, "prompts": {}, "completions": {}}

#: CapabilityError codes reported to the model as a tool result with ``isError: true`` (a tool
#: execution error it can act on); ``internal`` is a server error and a JSON-RPC -32603.
TOOL_ERROR_CODES = frozenset(
    {
        "invalid_params",
        "not_found",
        "not_allowed",
        "refused_licence",
        "too_large",
        "timeout",
        "unavailable",
        "busy",
    }
)

_INSTRUCTIONS = (
    "CAMBER is a read-only building-automation analytics toolkit. These tools read one local "
    "time-series store, and only the facilities this server was launched with; a facility "
    "outside that list is refused, and so is research-only (non-commercial / no-derivatives) "
    "data. Nothing a tool does changes the store. Summaries cite fact ids such as [F1@ctx]; quote "
    "them when you repeat a number. Long lists come a page at a time (limit and offset "
    "arguments). Lines starting with NOTICE are licence, trust or truncation notices (a cut "
    "list says how to get the rest): always show them to the user."
)


@dataclasses.dataclass
class ToolCall:
    """A ``tools/call`` request that passed every check of the message layer, ready to run
    elsewhere (a worker process, #292): :meth:`McpServer.handle` with ``defer=True`` returns it
    instead of calling the registry, and :meth:`McpServer.finish` turns its outcome into the
    response, the same bytes the in-process call would have given."""

    id: Any
    name: str
    arguments: dict
    context: Any
    caller: str
    modern: bool


class _Deferred(Exception):
    def __init__(self, call: ToolCall):
        super().__init__(call.name)
        self.call = call


class _RpcError(Exception):
    """A JSON-RPC error response for the current request."""

    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def _error(id_: Any, code: int, message: str, data: Any = None) -> dict:
    err: dict = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    # JSON-RPC 2.0: "id" is always present, null when the request's id could not be read (the
    # official MCP SDK drops an error response without one, and the caller waits forever)
    return {"jsonrpc": "2.0", "id": id_, "error": err}


class _DuplicateKey(Exception):
    """An object in the message names the same key twice."""


class _IntTooLarge(Exception):
    """An integer in the message has more digits than Python converts."""


def _pairs_no_duplicates(pairs: list) -> dict:
    # last-wins would let a proxy that keeps the first key see another method, id or version
    out = dict(pairs)
    if len(out) != len(pairs):
        seen: set = set()
        for k, _ in pairs:
            if k in seen:
                raise _DuplicateKey(k)
            seen.add(k)
    return out


def _parse_int(digits: str) -> int:
    try:
        return int(digits)
    except ValueError:  # over sys.get_int_max_str_digits(): valid JSON, just not convertible
        raise _IntTooLarge from None


def decode_message(line: str) -> Any:
    """``json.loads`` for one message line, refusing duplicate keys at any depth.

    Raises ``_DuplicateKey``, ``_IntTooLarge``, or ``ValueError`` / ``RecursionError`` for text
    that is not JSON.
    """
    return json.loads(line, object_pairs_hook=_pairs_no_duplicates, parse_int=_parse_int)


def _is_lone_surrogate_free(text: str) -> bool:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _has_lone_surrogate(value: Any) -> bool:
    """Whether any string (key or value) in ``value`` holds a lone UTF-16 surrogate.

    ``"\\ud800"`` is valid JSON syntax, but not a Unicode character: such text cannot be written
    back as UTF-8, so it is refused on the way in. Iterative, so a deep value cannot overflow.
    """
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            if not _is_lone_surrogate_free(item):
                return True
        elif isinstance(item, dict):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return False


def _valid_id(value: Any) -> bool:
    # MCP: a string or an integer, never null (bool is an int subclass in Python; not an id)
    return isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool))


def _capability_error(exc: BaseException) -> tuple[str, str, Any] | None:
    """``(code, message, data)`` of a ``camber.capabilities.CapabilityError``, recognised by shape
    (string ``code`` and ``message``) so the server imports without the registry and works with any
    registry of the same shape; ``None`` for any other exception."""
    code, message = getattr(exc, "code", None), getattr(exc, "message", None)
    if isinstance(code, str) and isinstance(message, str):
        return code, message, getattr(exc, "data", None)
    return None


def _printable(key: str) -> str:
    """A key for an error message: JSON-quoted, ASCII-only and short."""
    text = json.dumps(key)
    return text if len(text) <= 66 else text[:62] + '..."'


def _client_label(info: Any) -> str:
    """``mcp:<client name>`` for the call context's audit label (self-reported, display only)."""
    name = info.get("name") if isinstance(info, dict) else None
    if not isinstance(name, str):
        return "mcp"
    clean = "".join(c for c in name if c.isalnum() or c in "-_. ")[:64].strip()
    return f"mcp:{clean}" if clean else "mcp"


def json_safe(value: Any) -> Any:
    """``value`` with NaN and infinities replaced by ``None`` (JSON has no such numbers)."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


def encode_message(message: dict) -> bytes:
    """One newline-terminated UTF-8 JSON line (no embedded newline: strings are escaped).

    A lone surrogate (which UTF-8 cannot carry) is written as its ``\\uXXXX`` JSON escape: outside
    strings ``json.dumps`` emits ASCII only, so the replacement always lands inside a string.
    """
    try:
        text = json.dumps(message, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except ValueError:  # NaN / inf somewhere in a result
        text = json.dumps(
            json_safe(message), ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
    return text.encode("utf-8", "backslashreplace") + b"\n"


def encode_reply(message: dict) -> bytes:
    """:func:`encode_message` that never raises: a reply that cannot be serialised is logged to
    stderr and replaced by an Internal error for the same request id (``null`` as a last resort)."""
    try:
        return encode_message(message)
    except Exception:
        log.exception("could not serialise the reply; answered as an internal error")
    id_ = message.get("id") if isinstance(message, dict) else None
    for candidate in (id_, None):
        try:
            return encode_message(_error(candidate, INTERNAL_ERROR, "Internal error"))
        except Exception:  # an id that cannot be written (it was validated, so not expected)
            continue
    return b'{"jsonrpc":"2.0","id":null,"error":{"code":-32603,"message":"Internal error"}}\n'


def _published(schema: Any, *, notes: bool = True) -> Any:
    """``schema`` as ``tools/list`` sends it (#299: a shorter tool list): without a ``$schema`` of
    JSON Schema 2020-12, the dialect both revisions assume when there is none, and with
    ``notes=False`` without its ``description`` annotations (an output schema validates the same
    without them, and a model picks a tool from its description and input schema)."""
    if isinstance(schema, dict) and schema.get("$schema") == _DIALECT:
        schema = {k: v for k, v in schema.items() if k != "$schema"}
    return schema if notes else _without_notes(schema)


def _without_notes(schema: Any, depth: int = 0) -> Any:
    if depth > 30:
        return schema
    if isinstance(schema, list):
        return [_without_notes(v, depth + 1) for v in schema]
    if not isinstance(schema, dict):
        return schema
    out = {}
    for k, v in schema.items():
        if k == "description" and isinstance(v, str):
            continue
        if k in ("properties", "patternProperties", "$defs"):  # keys here are names, not keywords
            out[k] = {n: _without_notes(s, depth + 1) for n, s in v.items()}
        elif k in ("enum", "const", "required"):
            out[k] = v
        else:
            out[k] = _without_notes(v, depth + 1)
    return out


_DIALECT = "https://json-schema.org/draft/2020-12/schema"


class McpServer:
    """Answer MCP JSON-RPC messages from a capability registry, under a fixed call context.

    ``registry`` follows the ``camber.capabilities.Registry`` contract (``capabilities()``,
    ``prompts()``, ``call(name, arguments, ctx)``); ``context`` is the ``CallContext`` built from
    the launch configuration -- the client never chooses the store, the facility allowlist or the
    trust floor. Only ``caller`` is set per request, from the client's self-reported name.

    :meth:`handle` takes one decoded JSON value and returns the response (``None`` for a
    notification); :meth:`handle_line` does the same for one raw line. The transport loop is
    :func:`camber.mcp.serve` / :func:`camber.mcp.serve_stdio`. With ``defer=True`` a tool call is
    returned as a :class:`ToolCall` for the transport to run (:meth:`finish` answers it), and
    ``notifications/cancelled`` calls :attr:`on_cancel` with the request id.
    """

    def __init__(
        self,
        registry: Any,
        context: Any,
        *,
        server_name: str = "camber",
        server_title: str = "CAMBER",
        server_version: str | None = None,
        instructions: str | None = None,
    ):
        if server_version is None:
            from .. import __version__ as server_version
        self.registry = registry
        self.context = context
        self.server_info = {"name": server_name, "title": server_title, "version": server_version}
        self.instructions = _INSTRUCTIONS if instructions is None else instructions
        # the lists are fixed for the life of the process: computed once, in registry order
        # (the contract sorts by name), so tools/list is deterministic
        self._tools = list(registry.capabilities())
        self._prompts = list(registry.prompts())
        self._tool_names = {c.name for c in self._tools}
        self._prompt_by_name = {p.name: p for p in self._prompts}
        #: the legacy session (stdio = one process = one session): the negotiated version
        self.legacy_version: str | None = None
        self.legacy_client: Any = None
        self.legacy_initialized = False
        #: ``on_cancel(request_id)`` for ``notifications/cancelled`` (set by the transport)
        self.on_cancel: Any = None
        self._defer = False

    # ------------------------------------------------------------------ entry points

    def handle_line(self, line: bytes | str, *, defer: bool = False) -> dict | ToolCall | None:
        """The response to one raw message line, or ``None`` (a notification or a blank line)."""
        if isinstance(line, bytes):
            try:
                line = line.decode("utf-8")
            except UnicodeDecodeError:
                return _error(None, PARSE_ERROR, "Parse error: message is not UTF-8")
        line = line.strip()
        if not line:
            return None
        try:
            msg = decode_message(line)
        except _DuplicateKey as e:
            return _error(
                None, INVALID_REQUEST, f"Invalid Request: duplicate key {_printable(e.args[0])}"
            )
        except _IntTooLarge:
            return _error(
                None, INVALID_REQUEST, "Invalid Request: an integer (id or value) is too large"
            )
        except (ValueError, RecursionError):
            return _error(None, PARSE_ERROR, "Parse error: Invalid JSON")
        return self.handle(msg, defer=defer)

    def handle(self, msg: Any, *, defer: bool = False) -> dict | ToolCall | None:
        """The response to one decoded JSON-RPC message, or ``None`` for a notification; with
        ``defer``, a valid tool call is returned as a :class:`ToolCall` instead of being run."""
        self._defer = defer
        try:
            return self._handle(msg)
        finally:
            self._defer = False

    def _handle(self, msg: Any) -> dict | ToolCall | None:
        if isinstance(msg, list):
            # JSON-RPC batching was removed in 2025-06-18 and is absent from both revisions served
            return _error(None, INVALID_REQUEST, "Invalid Request: batches are not supported")
        if not isinstance(msg, dict):
            return _error(None, INVALID_REQUEST, "Invalid Request: not a JSON-RPC object")
        has_id = "id" in msg
        id_ = msg.get("id") if has_id and _valid_id(msg.get("id")) else None
        if _has_lone_surrogate(msg):
            if isinstance(id_, str) and not _is_lone_surrogate_free(id_):
                id_ = None
            if not has_id and isinstance(msg.get("method"), str):
                log.warning("dropped a notification holding a lone UTF-16 surrogate")
                return None
            return _error(
                id_, INVALID_REQUEST, "Invalid Request: a string holds a lone UTF-16 surrogate"
            )
        if msg.get("jsonrpc") != "2.0":
            return _error(id_, INVALID_REQUEST, 'Invalid Request: "jsonrpc" must be "2.0"')
        method = msg.get("method")
        if method is None and ("result" in msg or "error" in msg):
            # a response: this server never sends requests, so there is nothing to correlate
            log.debug("ignoring a response message from the client")
            return None
        if not isinstance(method, str) or not method:
            return _error(id_, INVALID_REQUEST, 'Invalid Request: "method" must be a string')
        if not has_id:
            self._notification(method, msg.get("params"))
            return None
        if id_ is None:
            return _error(
                None, INVALID_REQUEST, 'Invalid Request: "id" must be a string or integer'
            )
        try:
            return {"jsonrpc": "2.0", "id": id_, "result": self._request(method, msg)}
        except _Deferred as d:
            d.call.id = id_
            return d.call
        except _RpcError as e:
            return _error(id_, e.code, e.message, e.data)
        except Exception:  # a bug here must not end the session
            log.exception("internal error answering %s", method)
            return _error(id_, INTERNAL_ERROR, "Internal error")

    # ------------------------------------------------------------------ dispatch

    def _notification(self, method: str, params: Any) -> None:
        if method == "notifications/initialized":
            self.legacy_initialized = True
        elif method == "notifications/cancelled" and self.on_cancel is not None:
            # the transport stops the call (#292); answered one at a time (no on_cancel), a
            # cancelled request has already been answered or has not been read yet
            rid = params.get("requestId") if isinstance(params, dict) else None
            if _valid_id(rid):
                self.on_cancel(rid)
        # anything else is ignored (no reply)
        log.debug("notification %s", method)

    def _request(self, method: str, msg: dict) -> dict:
        params = msg.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise _RpcError(INVALID_PARAMS, 'Invalid params: "params" must be an object')
        meta = params.get("_meta")
        if meta is not None and not isinstance(meta, dict):
            raise _RpcError(INVALID_PARAMS, 'Invalid params: "_meta" must be an object')
        meta = meta or {}

        if method == "initialize":  # selects legacy semantics for this process
            return self._initialize(params)

        version = meta.get(_PV)
        if version is not None:
            if not isinstance(version, str):
                raise _RpcError(INVALID_PARAMS, f"Invalid params: {_PV} must be a string")
            if version in MODERN_VERSIONS:
                caps = meta.get(_CLIENT_CAPS)
                if not isinstance(caps, dict):
                    raise _RpcError(
                        INVALID_PARAMS, f"Invalid params: _meta is missing {_CLIENT_CAPS}"
                    )
                if not isinstance(meta.get(_CLIENT_INFO, {}), dict):
                    raise _RpcError(
                        INVALID_PARAMS, f"Invalid params: {_CLIENT_INFO} must be an object"
                    )
                return self._modern(method, params, meta)
            if version in LEGACY_VERSIONS:
                if self.legacy_version is None:
                    raise _RpcError(
                        INVALID_PARAMS,
                        f"Invalid params: protocol version {version} needs the initialize "
                        "handshake first",
                    )
                return self._legacy(method, params)
            raise _RpcError(
                UNSUPPORTED_PROTOCOL_VERSION,
                "Unsupported protocol version",
                {"supported": list(SUPPORTED_VERSIONS), "requested": version},
            )
        if self.legacy_version is not None:
            return self._legacy(method, params)
        if method == "ping":  # 2025-11-25: a ping may come before initialize
            return {}
        raise _RpcError(
            INVALID_PARAMS,
            f"Invalid params: _meta is missing {_PV} (2026-07-28), and no initialize handshake "
            "was made (2025-11-25)",
        )

    def _modern(self, method: str, params: dict, meta: dict) -> dict:
        caller = _client_label(meta.get(_CLIENT_INFO))
        if method == "server/discover":
            result = self._cacheable(
                {
                    "supportedVersions": list(SUPPORTED_VERSIONS),
                    "capabilities": dict(_CAPABILITIES),
                    "instructions": self.instructions,
                }
            )
        elif method == "tools/list":
            self._no_cursor(params)
            result = self._cacheable(
                {"tools": [self._tool_def(c, modern=True) for c in self._tools]}
            )
        elif method == "tools/call":
            result = self._call_tool(params, caller, modern=True)
        elif method == "prompts/list":
            self._no_cursor(params)
            result = self._cacheable({"prompts": [self._prompt_def(p) for p in self._prompts]})
        elif method == "prompts/get":
            result = self._get_prompt(params)
        elif method == "completion/complete":
            result = self._complete_argument(params)
        else:
            raise _RpcError(METHOD_NOT_FOUND, f"Method not found: {method}")
        return self._complete(result)

    def _complete(self, result: dict) -> dict:
        """A 2026-07-28 result: ``resultType`` and the server info in ``_meta``."""
        result["resultType"] = "complete"
        result["_meta"] = {**result.get("_meta", {}), _SERVER_INFO: dict(self.server_info)}
        return result

    def _legacy(self, method: str, params: dict) -> dict:
        info = (
            self.legacy_client.get("clientInfo") if isinstance(self.legacy_client, dict) else None
        )
        if method == "ping":
            return {}
        if method == "tools/list":
            self._no_cursor(params)
            return {"tools": [self._tool_def(c, modern=False) for c in self._tools]}
        if method == "tools/call":
            return self._call_tool(params, _client_label(info), modern=False)
        if method == "prompts/list":
            self._no_cursor(params)
            return {"prompts": [self._prompt_def(p) for p in self._prompts]}
        if method == "prompts/get":
            return self._get_prompt(params)
        if method == "completion/complete":
            return self._complete_argument(params)
        raise _RpcError(METHOD_NOT_FOUND, f"Method not found: {method}")

    # ------------------------------------------------------------------ lifecycle (legacy)

    def _initialize(self, params: dict) -> dict:
        if self.legacy_version is not None:
            # one stdio process is one session: the version is not renegotiated mid-session
            raise _RpcError(INVALID_REQUEST, "Invalid Request: the session is already initialized")
        requested = params.get("protocolVersion")
        if not isinstance(requested, str):
            raise _RpcError(INVALID_PARAMS, 'Invalid params: "protocolVersion" must be a string')
        # the same rules as the stateless path's clientCapabilities / clientInfo
        if not isinstance(params.get("capabilities"), dict):
            raise _RpcError(INVALID_PARAMS, 'Invalid params: "capabilities" must be an object')
        if not isinstance(params.get("clientInfo", {}), dict):
            raise _RpcError(INVALID_PARAMS, 'Invalid params: "clientInfo" must be an object')
        # 2025-11-25 lifecycle: echo a supported version, else offer the latest this server speaks
        version = requested if requested in LEGACY_VERSIONS else LEGACY_VERSIONS[0]
        self.legacy_version = version
        self.legacy_client = {
            "clientInfo": params.get("clientInfo"),
            "capabilities": params.get("capabilities"),
        }
        self.legacy_initialized = False
        log.info(
            "initialize: %s, protocol %s (asked %s)",
            _client_label(params.get("clientInfo")),
            version,
            requested,
        )
        return {
            "protocolVersion": version,
            "capabilities": dict(_CAPABILITIES),
            "serverInfo": dict(self.server_info),
            "instructions": self.instructions,
        }

    # ------------------------------------------------------------------ features

    @staticmethod
    def _cacheable(result: dict) -> dict:
        result["ttlMs"] = LIST_TTL_MS
        result["cacheScope"] = CACHE_SCOPE
        return result

    @staticmethod
    def _no_cursor(params: dict) -> None:
        # every list fits one page, so no cursor is ever issued; any cursor is not one of ours
        if params.get("cursor") is not None:
            raise _RpcError(INVALID_PARAMS, "Invalid params: unknown cursor")

    @staticmethod
    def _tool_def(cap: Any, *, modern: bool) -> dict:
        out: dict = {
            "name": cap.name,
            "title": cap.title,
            "description": cap.description,
            "inputSchema": _published(cap.input_schema),
        }
        schema = cap.output_schema
        # 2025-11-25 requires an object-rooted outputSchema; 2026-07-28 allows any JSON Schema
        if schema and (modern or (isinstance(schema, dict) and schema.get("type") == "object")):
            out["outputSchema"] = _published(schema, notes=False)
        # the tool's own title is the display name, so annotations do not repeat it (#299)
        out["annotations"] = {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
        return out

    @staticmethod
    def _prompt_def(prompt: Any) -> dict:
        return {
            "name": prompt.name,
            "title": prompt.title,
            "description": prompt.description,
            "arguments": [
                {"name": a.name, "description": a.description, "required": bool(a.required)}
                for a in prompt.arguments
            ],
        }

    def _context_for(self, caller: str) -> Any:
        if dataclasses.is_dataclass(self.context) and not isinstance(self.context, type):
            return dataclasses.replace(self.context, caller=caller)
        return self.context

    def _call_tool(self, params: dict, caller: str, *, modern: bool) -> dict:
        name = params.get("name")
        if not isinstance(name, str):
            raise _RpcError(INVALID_PARAMS, 'Invalid params: "name" must be a string')
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise _RpcError(INVALID_PARAMS, 'Invalid params: "arguments" must be an object')
        if name not in self._tool_names:
            raise _RpcError(INVALID_PARAMS, f"Unknown tool: {name}")
        call = ToolCall(None, name, arguments, self._context_for(caller), caller, modern)
        if self._defer:
            raise _Deferred(call)
        t0 = time.monotonic()
        try:
            res = self.registry.call(name, arguments, call.context)
        except Exception as exc:
            return self._call_outcome(call, exc, t0)
        return self._call_outcome(call, res, t0)

    def _call_outcome(self, call: ToolCall, outcome: Any, t0: float) -> dict:
        """The tool result for a registry call's outcome (a result, or the exception it raised)."""
        name, caller, took = call.name, call.caller, time.monotonic() - t0
        if isinstance(outcome, BaseException):
            cap_err = _capability_error(outcome)
            if cap_err is None:  # the contract says this never happens
                log.error(
                    "tool %s (%s) raised; answered as an internal error",
                    name,
                    caller,
                    exc_info=outcome,
                )
                raise _RpcError(INTERNAL_ERROR, "Internal error") from None
            code, message, data = cap_err
            log.info("tool %s (%s): %s after %.2fs", name, caller, code, took)
            if code not in TOOL_ERROR_CODES:
                raise _RpcError(INTERNAL_ERROR, f"Internal error: {message}") from None
            return self._tool_error(code, message, data)
        result = self._tool_result(outcome, modern=call.modern)
        # 0.111 (#299): the cap bounds the reply as sent -- the summary, the JSON text block,
        # structuredContent and _meta together -- not only the result's data
        cap = getattr(getattr(call.context, "caps", None), "max_bytes", None)
        sent = self._complete(dict(result)) if call.modern else result
        try:
            size = len(encode_message({"jsonrpc": "2.0", "id": call.id, "result": sent}))
        except (TypeError, ValueError):  # unserialisable: encode_reply answers Internal error
            size = 0
        if isinstance(cap, int) and size > cap:
            log.info("tool %s (%s): too_large (%d bytes) after %.2fs", name, caller, size, took)
            return self._tool_error(
                "too_large",
                f"the reply is {size:,} bytes, over this server's limit of {cap:,}; ask for a "
                "smaller page (limit) or narrow the query",
                {"bytes": size, "max_bytes": cap},
            )
        log.info("tool %s (%s): ok in %.2fs", name, caller, took)
        return result

    def finish(self, call: ToolCall, outcome: Any, started: float | None = None) -> dict:
        """The response to a deferred ``call`` given its outcome: what ``Registry.call`` returned,
        or the exception it raised (``started`` is the ``time.monotonic()`` for the log line)."""
        try:
            result = self._call_outcome(
                call, outcome, time.monotonic() if started is None else started
            )
            if call.modern:
                result = self._complete(result)
            return {"jsonrpc": "2.0", "id": call.id, "result": result}
        except _RpcError as e:
            return _error(call.id, e.code, e.message, e.data)
        except Exception:  # a bug here must not end the session
            log.exception("internal error answering tools/call")
            return _error(call.id, INTERNAL_ERROR, "Internal error")

    @staticmethod
    def _tool_error(code: str, message: str, data: Any) -> dict:
        err: dict = {"code": code}
        if data is not None:
            try:
                json.dumps(data, allow_nan=False)
                err["data"] = data
            except (TypeError, ValueError):
                pass
        return {
            "content": [{"type": "text", "text": f"{code}: {message}"}],
            "isError": True,
            "_meta": {"camber/error": err},
        }

    @staticmethod
    def _tool_result(res: Any, *, modern: bool) -> dict:
        banners = [str(b) for b in (res.banners or ())]
        summary = str(res.text or "")
        if banners:
            notices = "\n".join(f"NOTICE: {b}" for b in banners)
            summary = f"{notices}\n\n{summary}" if summary else notices
        data = json_safe(res.data)
        content = [{"type": "text", "text": summary}]
        # both revisions ask a tool with structured content to repeat it as JSON text (SHOULD),
        # for clients that pass only `content` to the model: once, compact (#299)
        content.append(
            {
                "type": "text",
                "text": json.dumps(data, ensure_ascii=False, separators=(",", ":")),
            }
        )
        out: dict = {"content": content, "isError": False}
        # 2025-11-25 structuredContent is an object; 2026-07-28 takes any JSON value
        if modern or isinstance(data, dict):
            out["structuredContent"] = data
        out["_meta"] = {
            "camber/facts": json_safe(list(res.facts or [])),
            "camber/banners": banners,
            "camber/truncated": bool(res.truncated),
        }
        return out

    def _complete_argument(self, params: dict) -> dict:
        """``completion/complete`` for a prompt argument (0.111, #304): facility ids from the
        allowlist, equipment ids from the store for an allowed facility in
        ``context.arguments`` (see ``camber.capabilities.Registry.complete``)."""
        ref = params.get("ref")
        if not isinstance(ref, dict) or ref.get("type") != "ref/prompt":
            raise _RpcError(
                INVALID_PARAMS, 'Invalid params: "ref" must be a ref/prompt (no resources here)'
            )
        name = ref.get("name")
        if not isinstance(name, str) or name not in self._prompt_by_name:
            raise _RpcError(INVALID_PARAMS, f"Unknown prompt: {_printable(str(name))}")
        arg = params.get("argument")
        if not (
            isinstance(arg, dict)
            and isinstance(arg.get("name"), str)
            and isinstance(arg.get("value", ""), str)
        ):
            raise _RpcError(
                INVALID_PARAMS, 'Invalid params: "argument" needs a string name and value'
            )
        context = params.get("context") or {}
        given = context.get("arguments") if isinstance(context, dict) else None
        given = given if isinstance(given, dict) else {}
        given = {k: v for k, v in given.items() if isinstance(k, str) and isinstance(v, str)}
        complete = getattr(self.registry, "complete", None)
        if complete is None:
            values, total = [], 0
        else:
            try:
                values, total = complete(
                    name, arg["name"], arg.get("value", ""), given, self.context
                )
            except Exception as exc:
                cap_err = _capability_error(exc)
                if cap_err is not None and cap_err[0] == "invalid_params":
                    raise _RpcError(INVALID_PARAMS, f"Invalid params: {cap_err[1]}") from None
                log.exception("completion for prompt %s failed", name)
                raise _RpcError(INTERNAL_ERROR, "Internal error") from None
        return {
            "completion": {
                "values": list(values),
                "total": int(total),
                "hasMore": total > len(values),
            }
        }

    def _get_prompt(self, params: dict) -> dict:
        name = params.get("name")
        if not isinstance(name, str):
            raise _RpcError(INVALID_PARAMS, 'Invalid params: "name" must be a string')
        prompt = self._prompt_by_name.get(name)
        if prompt is None:
            raise _RpcError(INVALID_PARAMS, f"Unknown prompt: {name}")
        args = params.get("arguments")
        if args is None:
            args = {}
        if not isinstance(args, dict) or not all(isinstance(v, str) for v in args.values()):
            raise _RpcError(INVALID_PARAMS, 'Invalid params: "arguments" must map names to strings')
        known = {a.name for a in prompt.arguments}
        unknown = sorted(set(args) - known)
        if unknown:
            raise _RpcError(
                INVALID_PARAMS, f"Unknown argument(s) for prompt {name}: {', '.join(unknown)}"
            )
        missing = [a.name for a in prompt.arguments if a.required and a.name not in args]
        if missing:
            raise _RpcError(
                INVALID_PARAMS,
                f"Missing required argument(s) for prompt {name}: {', '.join(missing)}",
            )
        try:
            rendered = prompt.render(dict(args))
        except Exception as exc:
            cap_err = _capability_error(exc)
            if cap_err is not None and cap_err[0] == "invalid_params":
                raise _RpcError(INVALID_PARAMS, f"Invalid params: {cap_err[1]}") from None
            log.exception("prompt %s failed to render", name)
            raise _RpcError(INTERNAL_ERROR, "Internal error") from None
        messages = []
        for m in rendered:
            role = m.get("role")
            if role not in ("user", "assistant"):
                log.error("prompt %s rendered a message with role %r", name, role)
                raise _RpcError(INTERNAL_ERROR, "Internal error")
            messages.append({"role": role, "content": {"type": "text", "text": str(m["text"])}})
        return {"description": prompt.description, "messages": messages}
