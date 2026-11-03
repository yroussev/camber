"""The ``check_citations`` capability: verify a drafted text against fact ids (0.110, #278).

Where the cited facts come from (0.112, #294): with a session ledger (``ctx.ledger``, which
``camber mcp`` always gives), each cited id resolves to the fact this server issued to this
session; an id it never issued is ``unknown_citation`` and one it issued but has since forgotten
(the ledger is bounded) is ``forgotten_citation``. ``facts`` is then optional, and every fact
passed must equal the issued fact with its id, or the call is refused (``invalid_params``): a
caller cannot add, alter or import a fact. With no ledger (direct Python use) ``facts`` is
required and taken as given: the result says ``provenance: "unverified"``.

:func:`camber.agent.verify._judge` judges every sentence (#294, #328): it must cite at least
one known fact id (``[F1@rule_run]``, or several: ``[F1@a, F2@b]``) when ``per_sentence`` is on,
every figure it states must be one a fact it cites states (sign, unit, counted thing and role
included), and the names, point roles, verdicts, directions, negations, quantifiers and ranks it
states must be ones those facts make, what it says of a counted thing must agree with their
count of it, and closed-set labels (heating or cooling side, trust class, fault condition) bind
their own figures (#336; that module's doc says exactly what is read). The caller's
facility ids and their registry names are names too, so another facility named in a sentence
must be one a cited fact names; rule, tool and field names are not equipment. A ``NOTICE:`` line
repeating the text of a ``notice`` fact among the facts is kept and not judged; a fact's whole
text quoted word for word with its own id (a notice's ``N`` id too) is one supported sentence.
Every result's fact and summary say that it covers only the text passed (:data:`SCOPE`, #338):
a text reworded after a passing check is not checked until it is checked again.
``verified_text`` keeps the draft's lines, headings, list markers and blank lines (#322); when
it drops a sentence that gave a verdict, a negation or a caveat, it opens with :data:`REMOVED`
and the result says so (#328), so a stripped answer never reads as more certain than the draft.
Reads the store's facility registry for those names, and no facility data.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache

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
#: the line ``verified_text`` opens with when it dropped a verdict or caveat (#328); checking
#: that text again keeps the line and does not judge it
REMOVED = (
    "NOTICE: check_citations removed sentences that gave a verdict, a negation or a caveat; "
    "this text says less than the draft and is not the complete answer."
)

#: what every result says of its own reach (#338): a passing check is for the text it was given
SCOPE = (
    "This check covers only the text passed in this call (with detail, verified_text): give the "
    "user only text a check marked supported, and if you reword, shorten or add to it "
    "afterwards, even one sentence, check the whole text again before you give it or say that "
    "it was checked."
)

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


@lru_cache(maxsize=1)
def _terms() -> tuple:
    """``(terms, rules)``: the rule, tool and field names a sentence may use that name no
    equipment (#328) -- every built-in rule, every capability and every argument or result
    field with an underscore -- and the rule names among them."""
    import importlib
    import pkgutil

    from .. import capabilities as pkg
    from ._common import rule_names

    rules = set(rule_names())
    terms = set(rules)

    def fields(schema, depth=0):
        if depth > 8 or not isinstance(schema, dict):
            return
        for key, sub in (schema.get("properties") or {}).items():
            if "_" in key:
                terms.add(key)
            fields(sub, depth + 1)
        fields(schema.get("items"), depth + 1)

    for m in pkgutil.iter_modules(pkg.__path__):
        if m.name.startswith("_cap_"):
            mod = importlib.import_module(f"{pkg.__name__}.{m.name}")
            for cap in getattr(mod, "CAPABILITIES", ()):
                if "_" in cap.name:  # "points", "coverage": ordinary words too
                    terms.add(cap.name)
                fields(cap.input_schema)
                fields(cap.output_schema)
    return frozenset(terms), frozenset(rules)


def _facility_names(ctx) -> dict:
    """Every facility id of the caller's allowlist and of the store's registry, and each one's
    registry name (and that name before a parenthesis), mapped to its id (#328): a sentence
    naming one must cite a fact about that facility. A one-word id or a one-word name is left
    out (it reads as an ordinary word), and the matcher reads a name only where it is a plain
    name."""

    def usable(fid: str) -> bool:
        return not fid.isalpha() or len(fid) >= 6

    out = {f: f for f in getattr(ctx, "allowed_facilities", None) or () if usable(f)}
    store = getattr(ctx, "store", None)
    if not isinstance(store, str) or not os.path.isdir(store):
        return out
    try:
        from .._servable import read_registry

        entries = read_registry(store)[0]
    except Exception:  # noqa: BLE001 - names help the check, they are never a reason to fail it
        return out
    for fid, meta in entries.items():
        if usable(fid):
            out.setdefault(fid, fid)
        for key in ("display_name", "name"):
            name = (meta or {}).get(key)
            if not isinstance(name, str):
                continue
            name = " ".join(name.split())
            for n in (name, re.sub(r"\s*\(.*$", "", name).strip()):
                if len(n.split()) >= 2 and len(n) >= 8:
                    out.setdefault(n, fid)
    return out


def check_citations(ctx, arguments: dict) -> CapabilityResult:
    """Per-sentence citation verdicts for a drafted text."""
    from ..agent.verify import _judge, _rebuild, _states_verdict_or_caveat, _visible, extract_cites

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
    notices.append(REMOVED[len("NOTICE: ") :])
    terms, rules = _terms()
    judged = _judge(
        draft,
        context,
        per_sentence=per,
        notices=notices,
        aliases=_facility_names(ctx),
        terms=terms,
        rules=rules,
    )
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
    dropped = [j for j, s in zip(judged, sentences) if s["verdict"] != "supported"]
    # 0.112 (#328): a removed verdict or caveat leaves an answer more certain than its draft
    caveats = sum(1 for j in dropped if _states_verdict_or_caveat(j.text))
    if detail:  # 0.112 (#322): the draft's lines, headings and blank lines kept
        kept = _rebuild(draft, dropped)
        data["verified_text"] = f"{REMOVED}\n\n{kept}" if caveats and kept.strip() else kept
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
            f"cited fact carries and {bad['unsupported_claim']} make a claim no cited fact makes. "
            + SCOPE,
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
    if caveats:
        banners.append(
            f"{caveats} of the unsupported sentence(s) gave a verdict, a negation or a caveat: "
            "verified_text without them says less than the draft and can read as more certain "
            "than it is. Do not show it as the answer; fix those sentences and check again."
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
            "uncited, unsupported_number (figure, sign, unit or role) or unsupported_claim "
            "(name, verdict, negation or direction); failures come with why. Call it last. "
            "Cheap."
        ),
        input_schema=INPUT_SCHEMA,
        output_schema=OUTPUT_SCHEMA,
        handler=check_citations,
        own_output=("verified_text", "sentences"),  # the caller's own text, never cut (#320)
        cites=_cited_ids,  # the session's facts it reads (#294)
    ),
)
