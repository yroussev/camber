"""Grounded natural-language Q&A over a fact context — LLM optional, deterministic by default.

``ask(question, context=...)`` answers strictly from the fact whitelist. With no client it
routes the question through :func:`templates.answer_from_facts` (grounded, no LLM); with a
wired client it prompts the model with the fact block + the question, then verifies with
:func:`verify.check`, falling back to the deterministic answer if strict verification empties
the response. The context may be passed directly or built here from findings.
"""

from __future__ import annotations

from . import templates
from .context import build_context
from .explain import _answer


def ask(
    question,
    context=None,
    *,
    findings=None,
    run=None,
    read_api=None,
    scorecard=None,
    completeness=None,
    mapping_review=None,
    client=None,
    loads=None,
    price=None,
    strict: bool = True,
):
    """Answer ``question`` from ``context`` (or one built from the given sources).

    Grounded answer.
    """
    if context is None:
        context = build_context(
            list(findings) if findings else None,
            run=run,
            read_api=read_api,
            scorecard=scorecard,
            completeness=completeness,
            mapping_review=mapping_review,
            loads=loads,
            price=price,
        )
    task = (
        "Answer the question using only the facts above. Cite every claim with its [id]; if no "
        "fact answers it, say you don't know."
    )
    fallback = templates.answer_from_facts(question, context)
    return _answer(task, context, client, strict, fallback, question=question)
