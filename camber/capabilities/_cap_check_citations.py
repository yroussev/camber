"""The ``check_citations`` capability: verify a drafted text against fact ids (0.110, #278).

Where the cited facts come from (0.112, #294): with a session ledger (``ctx.ledger``, which
``camber mcp`` always gives), each cited id resolves to the fact this server issued to this
session; an id it never issued is ``unknown_citation`` and one it issued but has since forgotten
(the ledger is bounded) is ``forgotten_citation``. ``facts`` is then optional, and every fact
passed must equal the issued fact with its id, or the call is refused (``invalid_params``): a
caller cannot add, alter or import a fact. With no ledger (direct Python use) ``facts`` is
required and taken as given: the result says ``provenance: "unverified"``.

:func:`camber.agent.verify.check` judges every sentence: it must cite at least one known fact id
(``[F1@rule_run]``) when ``per_sentence`` is on, and every number it states must appear in a fact
it cites. Reads no store and no facility.
"""

from __future__ import annotations

from . import Capability, CapabilityError, CapabilityResult
from ._common import cite
from ._page import OFFSET, limit_arg, page

_VERDICTS = (
    "supported",
    "unknown_citation",
    "forgotten_citation",
    "uncited",
    "unsupported_number",
)
#: sentences (and cited ids) per page by default (#299)
_PAGE = 50

INPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["text"],
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
            "description": "not needed; if given, each must be a fact of this session, unchanged",
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
        "detail": {
            "type": "boolean",
            "description": "list every sentence and return verified_text (default false: only "
            "the sentences that fail, so the result stays small)",
        },
        "limit": limit_arg(_PAGE, "sentences, cited"),
        "offset": OFFSET,
    },
}

_SENTENCE = {
    "type": "object",
    "required": ["index", "text", "verdict", "cited", "unknown", "forgotten"],
    "additionalProperties": False,
    "properties": {
        "index": {"type": "integer", "minimum": 0},
        "text": {"type": "string"},
        "verdict": {"type": "string", "enum": list(_VERDICTS)},
        "cited": {"type": "array", "items": {"type": "string"}},
        "unknown": {"type": "array", "items": {"type": "string"}},
        "forgotten": {"type": "array", "items": {"type": "string"}},
    },
}

OUTPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": [
        "grounded",
        "n_sentences",
        "n_supported",
        "verdicts",
        "sentences",
        "cited",
        "provenance",
    ],
    "additionalProperties": False,
    "properties": {
        "grounded": {"type": "boolean", "description": "every sentence is supported"},
        "n_sentences": {"type": "integer", "minimum": 0},
        "n_supported": {"type": "integer", "minimum": 0},
        "verdicts": {
            "type": "object",
            "required": list(_VERDICTS),
            "additionalProperties": False,
            "properties": {v: {"type": "integer", "minimum": 0} for v in _VERDICTS},
            "description": "sentences per verdict",
        },
        "verified_text": {
            "type": "string",
            "description": "with detail: the text with every unsupported sentence removed",
        },
        "sentences": {
            "type": "array",
            "items": _SENTENCE,
            "description": "the failing sentences (with detail: every sentence); a page",
        },
        "cited": {
            "type": "array",
            "items": {"type": "string"},
            "description": "known fact ids the text cites, each once, in order; a page",
        },
        "provenance": {
            "type": "string",
            "enum": ["issued", "unverified"],
            "description": "issued: every cited fact is one this server issued in this session; "
            "unverified: no session, the caller's facts were taken as given",
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


def _cited_ids(arguments) -> list:
    """The fact ids a call reads from the session's ledger: those its text cites and those of the
    facts it passes (``Capability.cites``; any argument shape)."""
    from ..agent.verify import extract_cites

    if not isinstance(arguments, dict):
        return []
    text = arguments.get("text")
    ids = extract_cites(text) if isinstance(text, str) else []
    facts = arguments.get("facts")
    if isinstance(facts, list):
        ids += [f.get("id") for f in facts if isinstance(f, dict) and isinstance(f.get("id"), str)]
    return ids


def _issued(ledger, arguments: dict) -> tuple[list, set]:
    """``(facts, forgotten ids)``: the issued facts the text cites (and those passed, each
    checked against the issued one), from the session's ledger. A passed fact the session was
    not issued, or one that differs from the issued fact, refuses the call."""
    from ..agent.verify import extract_cites
    from ._ledger import FORGOTTEN, ISSUED, canonical

    facts, forgotten = {}, set()
    bad: dict = {"not_issued": [], "forgotten": [], "changed": []}
    for f in arguments.get("facts") or []:
        state, issued = ledger.lookup(f["id"])
        if state == FORGOTTEN:  # issued, but no longer held to compare with
            bad["forgotten"].append(f["id"])
        elif state != ISSUED:
            bad["not_issued"].append(f["id"])
        elif canonical(f) != issued:
            bad["changed"].append(f["id"])
        else:
            facts[f["id"]] = issued
    if any(bad.values()):
        parts = []
        if bad["not_issued"]:
            parts.append(f"{len(bad['not_issued'])} were not issued by this server in this session")
        if bad["forgotten"]:
            parts.append(f"{len(bad['forgotten'])} are no longer held by the server")
        if bad["changed"]:
            parts.append(f"{len(bad['changed'])} differ from the fact issued with that id")
        raise CapabilityError(
            "invalid_params",
            f"of the facts passed, {' and '.join(parts)}; leave 'facts' out: the server looks "
            "up every id it issued in this session (run the tools again for ids it no longer "
            "holds or from another session)",
            data={k: v[:50] for k, v in bad.items() if v},
        )
    for fid in extract_cites(arguments["text"]):
        if fid in facts:
            continue
        state, issued = ledger.lookup(fid)
        if state == ISSUED:
            facts[fid] = issued
        elif state == FORGOTTEN:
            forgotten.add(fid)
    return list(facts.values()), forgotten


def check_citations(ctx, arguments: dict) -> CapabilityResult:
    """Per-sentence citation verdicts for a drafted text."""
    from ..agent.verify import _sentences, check, extract_cites

    ledger = getattr(ctx, "ledger", None)
    if ledger is not None:
        facts_in, forgotten = _issued(ledger, arguments)
        provenance = "issued"
    elif arguments.get("facts"):
        facts_in, forgotten, provenance = arguments["facts"], set(), "unverified"
    else:
        raise CapabilityError(
            "invalid_params",
            "this call has no session whose facts the server could look up: pass 'facts' (the "
            "facts of earlier results, as returned)",
        )
    context = _context(facts_in)
    known = set(context.ids())
    per = arguments.get("per_sentence")
    per = True if per is None else bool(per)
    whole = check(arguments["text"], context, strict=True, require_cite_each_sentence=per)
    sentences = []
    for i, sent in enumerate(_sentences(arguments["text"])):
        cites = extract_cites(sent)
        unknown = [c for c in cites if c not in known and c not in forgotten]
        lost = [c for c in cites if c in forgotten]
        if per:
            g = check(sent, context, strict=False, require_cite_each_sentence=True)
        else:  # numbers may trace to any fact the whole text cites
            own = [c for c in extract_cites(arguments["text"]) if c in known]
            g = check(f"{sent} " + " ".join(f"[{c}]" for c in own), context, strict=False)
        reasons = {f["reason"] for f in g.flagged}
        if unknown:
            verdict = "unknown_citation"
        elif lost:
            verdict = "forgotten_citation"
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
                "forgotten": lost,
            }
        )
    n_ok = sum(1 for s in sentences if s["verdict"] == "supported")
    bad = {v: sum(1 for s in sentences if s["verdict"] == v) for v in _VERDICTS}
    detail = bool(arguments.get("detail"))
    listed = sentences if detail else [s for s in sentences if s["verdict"] != "supported"]
    cited = list(dict.fromkeys(c for c in extract_cites(arguments["text"]) if c in known))
    data = {
        "grounded": n_ok == len(sentences),
        "n_sentences": len(sentences),
        "n_supported": n_ok,
        "verdicts": bad,
        "sentences": page(listed, arguments, default=_PAGE, what="sentences", order="in order"),
        "cited": page(cited, arguments, default=_PAGE, what="cited", order="in order"),
        "provenance": provenance,
    }
    if detail:
        data["verified_text"] = whole.text
    facts = [
        {
            "id": "F1@check_citations",
            "kind": "citation_check",
            "value": n_ok,
            "text": f"{n_ok} of {len(sentences)} sentence(s) are supported by the facts they "
            f"cite; {bad['unknown_citation']} cite an unknown id, "
            + (
                f"{bad['forgotten_citation']} cite a fact the server has forgotten, "
                if bad["forgotten_citation"]
                else ""
            )
            + f"{bad['uncited']} cite nothing and {bad['unsupported_number']} state a number "
            "no cited fact carries",
        }
    ]
    text = cite(facts[0])
    banners = []
    if bad["forgotten_citation"]:
        banners.append(
            "Some cited facts are no longer held by the server (it keeps a bounded number per "
            "session): run the tools that gave them again and cite the new ids."
        )
    if n_ok < len(sentences):
        banners.append(
            "Some sentences are not supported by the facts they cite: fix or remove them "
            "before sharing the text (with detail, verified_text has them removed)."
        )
    return CapabilityResult(data=data, text=text, facts=facts, banners=banners)


CAPABILITIES = (
    Capability(
        name="check_citations",
        title="Check citations",
        description=(
            "Check your draft before showing it: pass the text only; each [id] is looked up "
            "among the facts this server gave you in this session. A sentence is supported, "
            "unknown_citation (not given in this session), forgotten_citation (rerun that tool), "
            "uncited or unsupported_number (a figure no cited fact carries); failing ones are "
            "listed (detail: all, plus verified_text). Call it last. Cheap."
        ),
        input_schema=INPUT_SCHEMA,
        output_schema=OUTPUT_SCHEMA,
        handler=check_citations,
        own_output=("verified_text", "sentences"),  # the caller's own text, never cut (#320)
        cites=_cited_ids,  # the session's facts it reads (#294)
    ),
)
