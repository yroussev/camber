"""Grounded, plain-language explanation of findings — LLM optional, deterministic by default.

``explain(findings)`` builds a :class:`~camber.agent.context.Context`, and:
- with no client (or an unwired one), returns the deterministic :func:`templates.explain_from_facts`
  answer — fully grounded, no LLM;
- with a wired :class:`~camber.agent.client.AgentClient`, prompts the model with the fact block and a
  cite-everything instruction, then runs :func:`verify.check`; if strict verification guts the answer
  to nothing, it falls back to the deterministic template.

Either way the return is a :class:`~camber.agent.verify.Grounded` whose ``source`` says which path ran.
"""

from __future__ import annotations

from .context import build_context
from . import templates
from .verify import check

_SYSTEM = ("You are a buildings-analytics assistant. Answer ONLY from the numbered facts provided. "
           "Cite every claim with its [id]. If a fact isn't present, say you don't know — never "
           "invent equipment, numbers, or recommendations.")


def _prompt(task: str, context, question: str | None = None) -> str:
    parts = ["Facts:", context.to_prompt_block(), task]
    if question:
        parts.append(f"Question: {question}")
    return "\n\n".join(parts)


def _answer(task: str, context, client, strict: bool, fallback: str, question=None):
    """Shared path: template fallback when no LLM or when strict verification empties the answer."""
    if client is None or not getattr(client, "wired", False):
        return check(fallback, context, strict=strict, source="template")
    raw = client.generate(_prompt(task, context, question))
    g = check(raw, context, strict=strict, source="llm")
    if not g.text.strip():                   # verification gutted it -> deterministic fallback
        return check(fallback, context, strict=strict, source="template")
    return g


def explain(findings, *, client=None, loads=None, price=None, strict: bool = True):
    """Explain one finding or a list of findings. Returns a grounded answer (template or LLM)."""
    if findings is None:
        findings = []
    elif not isinstance(findings, (list, tuple)):
        findings = [findings]
    context = build_context(list(findings), loads=loads, price=price)
    task = ("Explain what is wrong, what it costs, and what to do, grouped by equipment. "
            "Cite every statement with its [id].")
    return _answer(task, context, client, strict, templates.explain_from_facts(context))
