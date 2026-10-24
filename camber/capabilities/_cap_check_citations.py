"""The ``check_citations`` capability: verify a drafted text against fact ids (0.110, #278).

The caller passes the text and the facts it may cite -- the ``facts`` of earlier results, passed
back in. :func:`camber.agent.verify.check` judges every sentence: it must cite at least one
known fact id (``[F1@rule_run]``) when ``per_sentence`` is on, and every number it states must
appear in a fact it cites. Reads no store and no facility.
"""

from __future__ import annotations

from . import Capability, CapabilityError, CapabilityResult
from ._common import cite

_VERDICTS = ("supported", "unknown_citation", "uncited", "unsupported_number")

INPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["text", "facts"],
    "additionalProperties": False,
    "properties": {
        "text": {
            "type": "string",
            "minLength": 1,
            "maxLength": 50000,
            "description": "the drafted text, citing fact ids in square brackets",
        },
        "facts": {
            "type": "array",
            "minItems": 1,
            "maxItems": 5000,
            "description": "the facts it may cite: the 'facts' of earlier results, as returned",
            "items": {
                "type": "object",
                "required": ["id", "text"],
                "properties": {
                    "id": {
                        "type": "string",
                        "pattern": r"^[A-Za-z]+\d+(@[A-Za-z0-9_\-]+)?$",
                        "maxLength": 200,
                    },
                    "text": {"type": "string", "maxLength": 20000},
                },
            },
        },
        "per_sentence": {
            "type": "boolean",
            "description": "every sentence must cite a fact, and its numbers must trace to the "
            "facts that sentence cites (default true); false: numbers may trace to any fact the "
            "text cites, and an uncited sentence without numbers passes",
        },
    },
}

_SENTENCE = {
    "type": "object",
    "required": ["index", "text", "verdict", "cited", "unknown"],
    "additionalProperties": False,
    "properties": {
        "index": {"type": "integer", "minimum": 0},
        "text": {"type": "string"},
        "verdict": {"type": "string", "enum": list(_VERDICTS)},
        "cited": {"type": "array", "items": {"type": "string"}},
        "unknown": {"type": "array", "items": {"type": "string"}},
    },
}

OUTPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["grounded", "n_sentences", "n_supported", "verified_text", "sentences", "cited"],
    "additionalProperties": False,
    "properties": {
        "grounded": {"type": "boolean", "description": "every sentence is supported"},
        "n_sentences": {"type": "integer", "minimum": 0},
        "n_supported": {"type": "integer", "minimum": 0},
        "verified_text": {
            "type": "string",
            "description": "the text with every unsupported sentence removed",
        },
        "sentences": {"type": "array", "items": _SENTENCE},
        "cited": {
            "type": "array",
            "items": {"type": "string"},
            "description": "known fact ids the text cites, in order",
        },
    },
}


def _context(facts: list):
    """The facts as the whitelist :func:`check` reads; a repeated id must repeat its fact."""
    from ..agent.context import Context, Fact

    by_id: dict = {}
    for f in facts:
        fid = f["id"]
        if fid in by_id and by_id[fid] != f:
            raise CapabilityError(
                "invalid_params",
                f"fact id {fid!r} is given twice with different content (pass each result's "
                "facts as returned)",
            )
        by_id[fid] = f
    return Context(
        facts=[
            Fact(
                fid,
                str(f.get("kind", "")),
                str(f.get("equip", "")),
                str(f["text"]),
                {k: v for k, v in f.items() if k not in ("id", "text")},
            )
            for fid, f in by_id.items()
        ]
    )


def check_citations(ctx, arguments: dict) -> CapabilityResult:
    """Per-sentence citation verdicts for a drafted text."""
    from ..agent.verify import _sentences, check, extract_cites

    context = _context(arguments["facts"])
    known = set(context.ids())
    per = arguments.get("per_sentence")
    per = True if per is None else bool(per)
    whole = check(arguments["text"], context, strict=True, require_cite_each_sentence=per)
    sentences = []
    for i, sent in enumerate(_sentences(arguments["text"])):
        cites = extract_cites(sent)
        unknown = [c for c in cites if c not in known]
        if per:
            g = check(sent, context, strict=False, require_cite_each_sentence=True)
        else:  # numbers may trace to any fact the whole text cites
            own = [c for c in extract_cites(arguments["text"]) if c in known]
            g = check(f"{sent} " + " ".join(f"[{c}]" for c in own), context, strict=False)
        reasons = {f["reason"] for f in g.flagged}
        if unknown:
            verdict = "unknown_citation"
        elif "uncited-sentence" in reasons:
            verdict = "uncited"
        elif "uncited-number" in reasons:
            verdict = "unsupported_number"
        else:
            verdict = "supported"
        sentences.append(
            {
                "index": i,
                "text": sent,
                "verdict": verdict,
                "cited": [c for c in cites if c in known],
                "unknown": unknown,
            }
        )
    n_ok = sum(1 for s in sentences if s["verdict"] == "supported")
    bad = {v: sum(1 for s in sentences if s["verdict"] == v) for v in _VERDICTS[1:]}
    data = {
        "grounded": n_ok == len(sentences),
        "n_sentences": len(sentences),
        "n_supported": n_ok,
        "verified_text": whole.text,
        "sentences": sentences,
        "cited": [c for c in extract_cites(arguments["text"]) if c in known],
    }
    facts = [
        {
            "id": "F1@check_citations",
            "kind": "citation_check",
            "value": n_ok,
            "text": f"{n_ok} of {len(sentences)} sentence(s) are supported by the facts they "
            f"cite; {bad['unknown_citation']} cite an unknown id, {bad['uncited']} cite "
            f"nothing and {bad['unsupported_number']} state a number no cited fact carries",
        }
    ]
    text = cite(facts[0])
    banners = []
    if n_ok < len(sentences):
        banners.append(
            "Some sentences are not supported by the facts they cite: fix or remove them "
            "(verified_text has them removed) before sharing the text."
        )
    return CapabilityResult(data=data, text=text, facts=facts, banners=banners)


CAPABILITIES = (
    Capability(
        name="check_citations",
        title="Check citations",
        description=(
            "Verify a drafted text (a summary, an email, a report paragraph) against the facts "
            "of earlier results before showing it: pass the text and the 'facts' arrays those "
            "results returned. Each sentence gets a verdict -- supported, unknown_citation (an "
            "id that is not among the facts), uncited (no [id] at all) or unsupported_number (a "
            "figure no cited fact carries) -- and verified_text keeps only the supported ones. "
            "Call it last, on your own draft, and fix what it flags. Reads no building data. "
            "Cheap."
        ),
        input_schema=INPUT_SCHEMA,
        output_schema=OUTPUT_SCHEMA,
        handler=check_citations,
    ),
)
