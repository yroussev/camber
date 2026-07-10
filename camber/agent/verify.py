"""Citation extraction + grounding verification for generated answers.

An answer is **grounded** when (a) every ``[id]`` it cites resolves to a real fact in the
:class:`~camber.agent.context.Context`, and (b) every number it states is traceable to a cited fact —
it appears in that fact's text or data. Requiring number-*traceability* (rather than a citation token
in the very same sentence) is what lets a multi-sentence fact — e.g. a recommendation whose ``[id]``
leads a paragraph of specifics — stay grounded, while a fabricated figure the model invented is caught.

:func:`check` enforces both. In ``strict`` mode it *repairs* the text — dropping sentences that state
an untraceable number and stripping unknown ``[id]`` tokens — so a partly-hallucinated LLM answer
degrades to its grounded subset instead of misleading. In non-strict mode the same problems are
recorded in ``flagged`` but the text is left intact.

Pure: regex + the fact whitelist, no LLM, no I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

_CITE = re.compile(r"\[([A-Za-z]+\d+)\]")
#: a numeric figure: number with optional $, thousands commas, decimals, trailing %. The lookbehind
#: excludes digits glued to letters/hyphens so equip tokens (AHU-1, Z-2) and refs (G36) aren't numbers.
_NUMBER = re.compile(r"(?<![A-Za-z0-9\-])\$?(\d[\d,]*(?:\.\d+)?)\s*%?")
#: split into sentences on end-punctuation *followed by whitespace* -> never inside "5.16" or "Ch.5".
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


@dataclass
class Grounded:
    """A verified answer: the (possibly repaired) text plus what it cites and whether it's grounded."""

    text: str
    cited: list = field(default_factory=list)     # ids cited AND present in context (order-stable)
    facts: list = field(default_factory=list)     # the resolved Fact objects (may be empty)
    grounded: bool = True                         # no unknown cite AND every number traceable
    flagged: list = field(default_factory=list)   # [{"reason", "text"}] problems found
    source: str = "llm"                           # "llm" | "template"

    def as_dict(self) -> dict:
        d = asdict(self)
        d["facts"] = [f.as_dict() if hasattr(f, "as_dict") else f for f in self.facts]
        return d


def extract_cites(text: str) -> list:
    """The ``[id]`` tokens cited in ``text``, in order, de-duplicated."""
    seen, out = set(), []
    for m in _CITE.finditer(text or ""):
        cid = m.group(1)
        if cid not in seen:
            seen.add(cid)
            out.append(cid)
    return out


def _numbers(text) -> set:
    """Normalized numeric figures in ``text`` (commas/$/%/units stripped): '$2,897'/'2897.0' -> '2897'."""
    out = set()
    for m in _NUMBER.finditer(_CITE.sub("", str(text))):   # numbers inside [F1] don't count
        core = m.group(1).replace(",", "")
        if "." in core:
            core = core.rstrip("0").rstrip(".")             # 2897.0 -> 2897, 12.50 -> 12.5
        out.add(core)
    return out


def _fact_numbers(fact) -> set:
    nums = _numbers(fact.text)
    nums |= _numbers(_stringify(getattr(fact, "data", None)))
    return nums


def _stringify(data) -> str:
    if isinstance(data, dict):
        return " ".join(_stringify(v) for v in data.values())
    if isinstance(data, (list, tuple)):
        return " ".join(_stringify(v) for v in data)
    return str(data)


def _sentences(text: str) -> list:
    return [s for s in (p.strip() for p in _SENT_SPLIT.split(text or "")) if s]


def check(text: str, context, *, strict: bool = True, source: str = "llm") -> Grounded:
    """Verify (and in ``strict`` mode repair) ``text`` against ``context``'s fact whitelist."""
    all_ids = set(context.ids())
    cited_all = extract_cites(text)
    unknown = [c for c in cited_all if c not in all_ids]
    flagged: list = [{"reason": "unknown-citation", "text": c} for c in unknown]

    # every number reachable through a *validly cited* fact is grounded
    grounded_nums: set = set()
    for c in cited_all:
        f = context.by_id(c)
        if f is not None:
            grounded_nums |= _fact_numbers(f)

    kept: list = []
    for sent in _sentences(text):
        untraceable = _numbers(sent) - grounded_nums
        if untraceable:
            flagged.append({"reason": "uncited-number", "text": sent})
            if strict:
                continue                     # drop the unsupported sentence
        kept.append(sent)

    out_text = " ".join(kept) if strict else (text or "")
    if strict and unknown:                   # strip dangling unknown [id] tokens from the repaired text
        for c in unknown:
            out_text = out_text.replace(f"[{c}]", "")
        out_text = re.sub(r"\s{2,}", " ", out_text).strip()

    cited = [c for c in extract_cites(out_text if strict else text) if c in all_ids]
    facts = [context.by_id(c) for c in cited]
    grounded = not unknown and not any(f["reason"] == "uncited-number" for f in flagged)
    return Grounded(text=out_text.strip(), cited=cited, facts=[f for f in facts if f is not None],
                    grounded=grounded, flagged=flagged, source=source)
