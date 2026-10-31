"""The ``check_citations`` capability: verify a drafted text against fact ids (0.110, #278).

Where the cited facts come from (0.112, #294): with a session ledger (``ctx.ledger``, which
``camber mcp`` always gives), each cited id resolves to the fact this server issued to this
session; an id it never issued is ``unknown_citation`` and one it issued but has since forgotten
(the ledger is bounded) is ``forgotten_citation``. ``facts`` is then optional, and every fact
passed must equal the issued fact with its id, or the call is refused (``invalid_params``): a
caller cannot add, alter or import a fact. With no ledger (direct Python use) ``facts`` is
required and taken as given: the result says ``provenance: "unverified"``.

:func:`camber.agent.verify._judge` judges every sentence (#294): it must cite at least one known
fact id (``[F1@rule_run]``, or several: ``[F1@a, F2@b]``) when ``per_sentence`` is on, every
figure it states must be one a fact it cites states (sign, unit, counted thing and role
included), and the names, verdicts, directions and negations it states must be ones those facts
make. A ``NOTICE:`` line repeating the text of a ``notice`` fact among the facts is kept and not
judged. ``verified_text`` keeps the draft's lines, headings and blank lines (#322). Reads no
store and no facility.
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
    "unsupported_claim",
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
            "description": "default true: each sentence must cite facts and match them; "
            "false: figures may match any cited fact, uncited sentences pass",
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
        "why": {"type": "string"},
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


#: a sentence's verdict, from :func:`camber.agent.verify._judge`'s reason
_VERDICT_OF = {
    "": "supported",
    "uncited-sentence": "uncited",
    "uncited-number": "unsupported_number",
    "unsupported-claim": "unsupported_claim",
}


def check_citations(ctx, arguments: dict) -> CapabilityResult:
    """Per-sentence citation verdicts for a drafted text."""
    from ..agent.verify import _judge, _rebuild, _visible, extract_cites

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
    draft = arguments["text"]
    # a NOTICE line that repeats a notice fact's text is the server's own words (#294)
    notices = [f.text for f in context.facts if f.kind == "notice"]
    judged = _judge(draft, context, per_sentence=per, notices=notices)
    sentences = []
    for i, j in enumerate(judged):
        unknown = [c for c in j.unknown if c not in forgotten]
        lost = [c for c in j.unknown if c in forgotten]
        if unknown:
            verdict, why = (
                "unknown_citation",
                [f"{c} is not a fact of this session" for c in unknown],
            )
        elif lost:
            verdict, why = "forgotten_citation", [f"{c} is no longer held" for c in lost]
        else:
            verdict, why = _VERDICT_OF[j.reason], j.why
        row = {
            "index": i,
            "text": j.text,
            "verdict": verdict,
            "cited": j.cited,
            "unknown": unknown,
            "forgotten": lost,
        }
        if verdict != "supported":
            row["why"] = "; ".join(why)
        sentences.append(row)
    n_ok = sum(1 for s in sentences if s["verdict"] == "supported")
    bad = {v: sum(1 for s in sentences if s["verdict"] == v) for v in _VERDICTS}
    detail = bool(arguments.get("detail"))
    listed = sentences if detail else [s for s in sentences if s["verdict"] != "supported"]
    cited = list(dict.fromkeys(c for c in extract_cites(_visible(draft)) if c in known))
    data = {
        "grounded": n_ok == len(sentences),
        "n_sentences": len(sentences),
        "n_supported": n_ok,
        "verdicts": bad,
        "sentences": page(listed, arguments, default=_PAGE, what="sentences", order="in order"),
        "cited": page(cited, arguments, default=_PAGE, what="cited", order="in order"),
        "provenance": provenance,
    }
    if detail:  # 0.112 (#322): the draft's lines, headings and blank lines kept
        data["verified_text"] = _rebuild(
            draft, [j for j, s in zip(judged, sentences) if s["verdict"] != "supported"]
        )
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
            + f"{bad['uncited']} cite nothing, {bad['unsupported_number']} state a figure no "
            f"cited fact carries and {bad['unsupported_claim']} make a claim no cited fact makes",
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
            "before sharing the text (each one's 'why' says what did not match; with detail, "
            "verified_text has them removed)."
        )
    return CapabilityResult(data=data, text=text, facts=facts, banners=banners)


CAPABILITIES = (
    Capability(
        name="check_citations",
        title="Check citations",
        description=(
            "Check your draft before showing it: pass the text only; each [id] is looked up "
            "in this session's facts. A sentence is supported, unknown_citation (not of "
            "this session), forgotten_citation (rerun that tool), "
            "uncited, unsupported_number (figure, sign or unit) or unsupported_claim (name, "
            "verdict or direction); failures come with why. Call it last. Cheap."
        ),
        input_schema=INPUT_SCHEMA,
        output_schema=OUTPUT_SCHEMA,
        handler=check_citations,
        own_output=("verified_text", "sentences"),  # the caller's own text, never cut (#320)
        cites=_cited_ids,  # the session's facts it reads (#294)
    ),
)
