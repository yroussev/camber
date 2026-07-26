"""Deterministic (no-LLM) explanation & Q&A over the fact whitelist.

This is the layer that makes ``camber.agent`` useful with **zero LLM wired**: it composes an answer
purely from :class:`~camber.agent.context.Fact` text, and — because every sentence cites the fact it
came from and asserts nothing not in a fact — it is *trivially 100 % grounded*. It is therefore also
the regression oracle the LLM path is checked against (:func:`camber.agent.verify.check`).

Pure: string composition over the fact set, no LLM, no I/O.
"""

from __future__ import annotations

_KIND_ORDER = {"finding": 0, "rootcause": 1, "cost": 2, "recommendation": 3}


def _cite(fact) -> str:
    return f"[{fact.id}] {fact.text}"


def explain_from_facts(context) -> str:
    """A grounded narrative of the findings: each finding, then its cost, then its recommendation.

    Facts are grouped by equipment (in first-seen order) and, within an equipment, ordered
    finding → root-cause → cost → recommendation. Every sentence carries its ``[id]``.
    """
    if not context.facts:
        return "No findings to explain."
    order = []
    for f in context.facts:
        if f.equip not in order:
            order.append(f.equip)
    blocks = []
    for equip in order:
        facts = sorted(context.by_equip(equip), key=lambda f: (_KIND_ORDER.get(f.kind, 9), f.id))
        head = f"{equip}: " if equip else ""
        blocks.append(head + " ".join(_cite(f) for f in facts))
    return "\n".join(blocks)


# --------------------------------------------------------------------------- Q&A routing

_COST_WORDS = ("cost", "$", "dollar", "save", "saving", "waste", "energy", "kwh", "expensive")
_FIX_WORDS = ("recommend", "fix", "do", "action", "repair", "correct", "resolve", "should")
_CAUSE_WORDS = ("cause", "root", "why", "reason", "because", "driver")
_FLEET_WORDS = ("fleet", "portfolio", "building", "buildings", "worst", "best", "eui",
                "efficient", "efficiency", "rank", "across")


def _mentioned_equips(question, context) -> list:
    q = question.lower()
    equips = []
    for f in context.facts:
        if f.equip and f.equip.lower() in q and f.equip not in equips:
            equips.append(f.equip)
    return equips


def _render(facts) -> str:
    return " ".join(_cite(f) for f in facts) if facts else ""


def answer_from_facts(question: str, context) -> str:
    """Answer ``question`` from the fact set by keyword/equipment routing. Grounded by construction.

    Routes on mentioned equipment first, then on cost / recommendation / root-cause keywords; falls
    back to the finding facts. Returns an honest "I don't know" (no fabricated number) when no fact
    addresses the question.
    """
    if not context.facts:
        return "I don't know — there are no facts in the current context."
    q = (question or "").lower()

    equips = _mentioned_equips(question, context)
    scope = [f for f in context.facts if f.equip in equips] if equips else context.facts

    def _kind(kind):
        return [f for f in scope if f.kind == kind]

    fleet_facts = _kind("fleet")
    if fleet_facts and (any(w in q for w in _FLEET_WORDS) or not equips):
        # a portfolio question (or any question when the context is fleet-level) -> fleet facts
        facts = fleet_facts
    elif any(w in q for w in _COST_WORDS):
        facts = _kind("cost") or fleet_facts
    elif any(w in q for w in _FIX_WORDS):
        facts = _kind("recommendation")
    elif any(w in q for w in _CAUSE_WORDS):
        facts = _kind("rootcause") or _kind("finding")
    elif equips:
        facts = scope                       # "tell me about AHU-1" -> everything on it
    else:
        facts = _kind("finding")

    rendered = _render(facts)
    if not rendered:
        subject = f" for {', '.join(equips)}" if equips else ""
        return f"I don't know — no matching fact{subject} is in the current context."
    return rendered
