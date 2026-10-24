"""``camber mcp``: a Model Context Protocol server over stdio (provisional API, 0.110, #277).

A thin adapter that serves the capability registry (``camber.capabilities``) to an MCP client
(Claude Desktop, the ``claude`` command line, any MCP host) as tools and prompts. Standard
library only: the ``mcp`` extra is an empty marker.

- Speaks **2026-07-28** (stateless: version and client capabilities in each request's ``_meta``,
  ``server/discover``, ``resultType`` on every result, ``ttlMs`` / ``cacheScope`` on list results)
  and **2025-11-25** (the ``initialize`` handshake; 2025-06-18 is accepted too), so newer and
  older clients both connect.
- Read-only: tools, prompts, nothing else (no resources, subscriptions, roots, sampling or
  logging). The store, the facility allowlist and the trust floor come from the launch
  configuration, never from the client.
- Logs go to stderr; stdout carries MCP messages only.

::

    from camber.capabilities import CallContext, default_registry
    from camber.mcp import McpServer, serve_stdio

    ctx = CallContext(store="store", allowed_facilities=frozenset({"hq"}))
    serve_stdio(McpServer(default_registry(), ctx))

See docs/MCP.md. This API is **provisional** (docs/API-STABILITY.md).
"""

from __future__ import annotations

from ._launch import LaunchError, build_context, launch
from ._server import (
    CACHE_SCOPE,
    LEGACY_VERSIONS,
    LIST_TTL_MS,
    MODERN_VERSIONS,
    SUPPORTED_VERSIONS,
    TOOL_ERROR_CODES,
    McpServer,
)
from ._stdio import MAX_MESSAGE_BYTES, serve, serve_stdio

__all__ = [
    "McpServer",
    "serve",
    "serve_stdio",
    "launch",
    "build_context",
    "LaunchError",
    "SUPPORTED_VERSIONS",
    "MODERN_VERSIONS",
    "LEGACY_VERSIONS",
    "TOOL_ERROR_CODES",
    "LIST_TTL_MS",
    "CACHE_SCOPE",
    "MAX_MESSAGE_BYTES",
]
