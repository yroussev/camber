"""``camber a2a``: an Agent2Agent (A2A) protocol server over HTTP (provisional API, 0.113, #280).

A thin adapter, like ``camber mcp``, over the capability registry (``camber.capabilities``):
another agent sends a message naming a skill and its input as a data part, and gets an A2A task
whose artifact is the capability's result. Standard library only: the ``a2a`` extra is an empty
marker.

- Speaks **A2A 1.0** over the **JSON-RPC** binding (``POST /a2a``): ``SendMessage``,
  ``GetTask``, ``ListTasks``, ``CancelTask`` and ``GetExtendedAgentCard``. Streaming and push
  notifications are not offered (the card says so); any other ``A2A-Version`` is
  ``VersionNotSupportedError``.
- Two skills: ``rcx-report`` (capability ``rcx_report``) and ``findings`` (``rule_run``). Every
  artifact, and every failed, rejected or cancelled task, carries its facts and notices in a data
  part: the ``grounded-facts`` extension (:data:`GROUNDED_FACTS_URI`).
- Read-only and no-persist; the store, the facility allowlist, the run configs and the trust floor
  come from the launch, never from a peer; research-only data is always refused. A peer's text is
  never read: no model sees it and it is never a config.
- Bound to ``127.0.0.1`` by default, with a bearer token on every request; the agent card at
  ``/.well-known/agent-card.json`` needs no token on a loopback bind, and does on any other.

::

    from camber.a2a import A2AServer, LocalRunner
    from camber.capabilities import CallContext, default_registry

    reg = default_registry()
    ctx = CallContext(store="store", allowed_facilities=frozenset({"hq"}))
    agent = A2AServer(reg, ctx, LocalRunner(reg), token="a-token-of-16-or-more")
    status, headers, body = agent.handle("GET", "/.well-known/agent-card.json",
                                         {"Host": "127.0.0.1:0"})

See docs/A2A.md. This API is **provisional** (docs/API-STABILITY.md).
"""

from __future__ import annotations

from ._launch import DEFAULT_PORT, TOKEN_ENV, launch
from ._protocol import (
    CARD_PATH,
    GROUNDED_FACTS_URI,
    PROTOCOL_VERSION,
    RPC_PATH,
    SKILLS,
    SUPPORTED_VERSIONS,
    Skill,
    agent_card,
)
from ._server import BODY_LIMIT, A2AServer, make_server
from ._tasks import MAX_TASKS, LocalRunner

__all__ = [
    "A2AServer",
    "BODY_LIMIT",
    "CARD_PATH",
    "DEFAULT_PORT",
    "GROUNDED_FACTS_URI",
    "LocalRunner",
    "MAX_TASKS",
    "PROTOCOL_VERSION",
    "RPC_PATH",
    "SKILLS",
    "SUPPORTED_VERSIONS",
    "Skill",
    "TOKEN_ENV",
    "agent_card",
    "launch",
    "make_server",
]
