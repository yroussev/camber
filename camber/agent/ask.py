"""Grounded natural-language Q&A over a fact context — LLM optional, deterministic by default.

``ask(question, context=...)`` answers strictly from the fact whitelist. With no client it routes the
question through :func:`templates.answer_from_facts` (grounded, no LLM); with a wired client it prompts
the model with the fact block + the question, then verifies with :func:`verify.check`, falling back to
the deterministic answer if strict verification empties the response. The context may be passed
directly or built here from findings.
"""

from __future__ import annotations

from .context import build_context
from . import templates
from .explain import _answer


def ask(question, context=None, *, findings=None, client=None, loads=None, price=None,
        strict: bool = True):
    """Answer ``question`` from ``context`` (or one built from ``findings``). Returns a grounded answer."""
    if context is None:
        context = build_context(list(findings) if findings else None, loads=loads, price=price)
    task = ("Answer the question using only the facts above. Cite every claim with its [id]; if no "
            "fact answers it, say you don't know.")
    fallback = templates.answer_from_facts(question, context)
    return _answer(task, context, client, strict, fallback, question=question)
