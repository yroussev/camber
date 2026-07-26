"""camber.agent — grounded, provider-agnostic explanation & Q&A over the deterministic layers.

This package turns CAMBER's deterministic results (findings, costs, recommendations, root-cause
groups, scorecards, completeness, history, mapping review) into a **grounding whitelist** — a set of
citable :class:`~camber.agent.context.Fact` objects — and, on top of it, explanation/Q&A that is
useful with **no LLM wired at all** (deterministic templates) and, when a caller injects a
``complete(prompt, **opts) -> str`` callable, verifies every LLM claim against that whitelist.

By construction: no LLM SDK is imported, no vendor is named, nothing is written back to the BAS. The
caller owns all I/O by wrapping any vendor SDK in the injected callable — mirroring the
``ingest.haystack`` transport seam. Advisory only, always auditable.
"""

from .context import (
    Fact, Context, build_context, facts_from_findings, facts_from_run, facts_from_scorecard,
    facts_from_completeness, facts_from_history, facts_from_mapping, facts_from_fleet,
)
from .verify import Grounded, check
from .client import AgentClient, client_from_callable, stub_client
from .explain import explain
from .ask import ask

__all__ = [
    "Fact", "Context", "build_context", "facts_from_findings", "facts_from_run",
    "facts_from_scorecard", "facts_from_completeness", "facts_from_history", "facts_from_mapping",
    "facts_from_fleet",
    "Grounded", "check",
    "AgentClient", "client_from_callable", "stub_client",
    "explain", "ask",
]
