"""Grounded generated prose for the RCx report, and the fact index it cites (0.107, #194).

The report's tables are built from deterministic objects: the ranked issues, their members and
costs, the declines, the caveats, the assumptions. :func:`_build_fact_index` turns those same
objects into a whitelist of citable :class:`~camber.agent.context.Fact` s, one statement each,
with **context-qualified ids**: ``K1@summary`` (a KPI), ``X2@appA`` (a decline), ``A3@appB``
(an assumption), ``C1@<issue key>`` (an issue's cost). The part after ``@`` is where the fact
lives; the issue key is the root finding's fingerprint, so an issue's ids do not move when other
issues come and go. Within a context, ids count per kind in the order the report shows them, so
the same inputs give the same ids.

Prose slots are the executive summary (``exec_summary``) and each issue page
(``issue:<key>``, "what we found and why it matters"), the same slot ids the engineer notes use.
Prose comes from a JSON file keyed by slot (offline, reproducible) or from an
:class:`~camber.agent.client.AgentClient` the caller wires; with neither, nothing here runs and
the report is unchanged. Each slot's text goes through :func:`camber.agent.verify.check` with
``require_cite_each_sentence=True`` against the facts that slot may cite. A slot renders only when
no sentence was dropped; otherwise the page keeps its deterministic text and Appendix A lists
the slot and why. An engineer note on a slot overrides its prose.

Pure apart from the injected client: no network, no vendor, no file I/O beyond
:func:`load_prose`.
"""

from __future__ import annotations

import json
import re

from ..agent.context import Context, Fact
from ..agent.verify import _CITE, check

__all__ = ["load_prose", "prose_prompts"]

#: fact kind -> id prefix, for the RCx fact index (a namespace of its own; the ``@ctx`` suffix
#: keeps these ids apart from camber.agent's run-level ones)
_PREFIX = {
    "issue": "I",
    "cost": "C",
    "hours": "T",
    "finding": "F",
    "action": "R",
    "why": "W",
    "caveat": "V",
    "dependency": "D",
    "link": "U",
    "chain": "Z",
    "kpi": "K",
    "decline": "X",
    "missing": "M",
    "chain_gap": "G",
    "unevaluated": "Q",
    "assumption": "A",
}

#: report-level fact contexts (an issue's context is its key)
_CTX_SUMMARY, _CTX_APPENDIX_A, _CTX_APPENDIX_B = "summary", "appA", "appB"

_SYSTEM = (
    "You write one paragraph of a retro-commissioning report. Use ONLY the numbered facts given. "
    "End every sentence with the [id] of each fact it rests on, before the full stop. Copy every "
    "number exactly as a cited fact states it, and state no number that is not in a fact you cite "
    "in that sentence. Never invent equipment, costs, hours or actions."
)


class _Ids:
    """Per-(context, kind) counters: ``F1@ctx``, ``F2@ctx``, ``C1@ctx`` ... in build order."""

    def __init__(self):
        self._n: dict = {}

    def next(self, kind: str, ctx: str) -> str:
        k = (ctx, kind)
        self._n[k] = self._n.get(k, 0) + 1
        return f"{_PREFIX[kind]}{self._n[k]}@{ctx}"


def _clean(text) -> str:
    return " ".join(str(text or "").split())


def _sentence(text) -> str:
    t = _clean(text)
    return t if not t or t[-1] in ".!?" else t + "."


# ============================================================================ fact index


def _build_fact_index(S) -> Context:
    """The report's fact index, from the builder's state ``S`` (what the sections render).

    Report-level facts first (``summary``, then ``appA``, then ``appB``), then each issue's in
    rank order. Every fact's text is a sentence the report itself shows, or assembles from the
    same values; ``data`` holds the displayed values, so a figure in generated prose traces to
    one of them.
    """
    from . import rcx as R

    ids = _Ids()
    facts: list = []

    def add(kind, ctx, text, equip="", **data):
        facts.append(Fact(ids.next(kind, ctx), kind, equip or "", _sentence(text), dict(data)))

    k = S["kpis"]
    c = _CTX_SUMMARY
    n = k["n_issues"]
    add("kpi", c, f"The report ranks {n} issue{'s' if n != 1 else ''}.", n_issues=n)
    if k["n_costed"]:
        add(
            "kpi",
            c,
            f"The {k['n_costed']} costed, non-conditional issue(s) total "
            f"{R._fmt_usd(k['annual_cost_usd'])}/yr; issues add, and a chain counts once.",
            annual_cost_usd=R._fmt_usd(k["annual_cost_usd"]),
            n_costed=k["n_costed"],
        )
    else:
        add("kpi", c, "No issue is costed, so the report states no dollar total.", n_costed=0)
    if k["at_risk_usd"]:
        add(
            "kpi",
            c,
            f"A further {R._fmt_usd(k['at_risk_usd'])}/yr is at risk pending a sensor fix.",
            at_risk_usd=R._fmt_usd(k["at_risk_usd"]),
        )
    add(
        "kpi",
        c,
        f"{k['n_uncosted']} issue(s) are uncosted and {k['n_conditional']} are conditional on a "
        "sensor fix.",
        n_uncosted=k["n_uncosted"],
        n_conditional=k["n_conditional"],
    )
    if k["coverage_pct"] is not None:
        add(
            "kpi",
            c,
            f"Data coverage of the air-side points is {k['coverage_pct']:.0f}% (the mean share of "
            "samples present).",
            coverage_pct=f"{k['coverage_pct']:.0f}",
        )
    add(
        "kpi",
        c,
        f"{k['n_declined']} check(s) declined to reach a verdict.",
        n_declined=k["n_declined"],
    )

    c = _CTX_APPENDIX_A
    for f in S["declined"]:
        m = f.metrics or {}
        why = m.get("reason") or (
            "untrusted input(s): " + ", ".join(m.get("untrusted_roles", []))
            if m.get("untrusted_roles")
            else f.summary
        )
        add("decline", c, f"{f.rule} on {f.equip} declined: {_clean(why)}", f.equip, rule=f.rule)
    for rule, missing, where in R._skip_rows(S["ctx"].run):
        add("missing", c, f"{rule} was not evaluated (missing {missing}) on {where}", rule=rule)
    for f in S["findings"]:
        mo = (f.metrics or {}).get("_missing_optional")
        if mo:
            add(
                "missing",
                c,
                f"{f.rule} on {f.equip} ran without optional input(s) {', '.join(mo)}",
                f.equip,
                rule=f.rule,
            )
    seen: list = []
    for f in S["findings"]:
        for cv in getattr(f, "caveats", None) or []:
            if cv not in seen:
                seen.append(cv)
                add("caveat", c, f"Caveat: {cv}")
    gaps = S.get("chain_gaps") or {}
    if gaps:
        add(
            "chain_gap",
            c,
            f"{len(gaps['boxes'])} box(es) ({R._name_boxes(gaps['boxes'])}) report symptoms an air "
            "handler may cause, but no declared served-by topology links them, so they are their "
            "own issues",
            n_boxes=len(gaps["boxes"]),
        )
    ctx = S["ctx"]
    done = {getattr(f, "equip", "") for f in S["findings"]}
    not_eval = [e for e in ctx.equips if e not in done]
    if not_eval:
        add("unevaluated", c, "Equipment not evaluated by any rule: " + ", ".join(not_eval))

    c = _CTX_APPENDIX_B
    for key, val in sorted(S["cost_defaults"].items()):
        add("assumption", c, f"Cost assumption {key} = {val}", key=key, value=val)
    price = S["o"].price
    if price is not None:
        add(
            "assumption",
            c,
            f"Energy price ${price.electricity_per_kwh}/kWh and ${price.gas_per_therm}/therm "
            "(configured)",
        )
    else:
        add(
            "assumption",
            c,
            "Energy price: package placeholders ($0.15/kWh, $1.20/therm), not the site's",
        )
    if S["o"].loads:
        add("assumption", c, "Equipment sizing supplied for: " + ", ".join(sorted(S["o"].loads)))
    else:
        add(
            "assumption",
            c,
            "No equipment sizing supplied, so sizing-dependent issues stay uncosted",
        )

    for iss in S["issues"]:
        _issue_facts(S, iss, add)
    return Context(facts=facts, site=getattr(ctx.run, "site", "") or None)


def _issue_facts(S, iss, add) -> None:
    from ..aso import recommend
    from . import rcx as R

    ctx = S["ctx"]
    c = iss.key
    eq = iss.equip
    rec = recommend(iss.root, frame=ctx.frame(eq))
    title, action, suggested = R._advice(S, iss, rec)
    lead = R._cause_lead(iss, frame=ctx.frame(eq))
    heading = R._heading(rec, title, lead)
    add(
        "issue",
        c,
        f"Issue {iss.rank}: {heading}, on {eq}{R._chain_boxes_note(iss)}; severity "
        f"{iss.severity}, confidence {iss.confidence}",
        eq,
        rank=iss.rank,
    )
    if iss.cost is not None:
        tail = " at risk pending a sensor fix" if iss.conditional else ""
        add(
            "cost",
            c,
            f"Issue {iss.rank} is estimated at {R._fmt_usd(iss.cost)}/yr{tail} "
            f"({_clean(iss.cost_basis_note) or 'the largest member estimate'})",
            eq,
            annual_cost_usd=R._fmt_usd(iss.cost),
        )
    else:
        add(
            "cost",
            c,
            f"Issue {iss.rank} has no dollar figure: {_clean(iss.cost_basis_note) or 'uncosted'}",
            eq,
        )
    excl = S["exclude_cost"]
    for f, fc in zip(iss.members, iss.member_costs or [None] * len(iss.members)):
        why = excl(f)
        on = getattr(f, "equip", eq)
        rule = getattr(f, "rule", "")
        if why:
            add("cost", c, f"The {rule} finding on {on} is excluded from the cost: {why}", on)
        elif fc is not None and getattr(fc, "costed", False):
            add(
                "cost",
                c,
                f"The {rule} finding on {on} is estimated at {R._fmt_usd(fc.annual_cost_usd)}/yr",
                on,
                annual_cost_usd=R._fmt_usd(fc.annual_cost_usd),
            )
    if iss.hours_union is not None:
        pct = iss.pct_runtime
        add(
            "hours",
            c,
            f"Issue {iss.rank} ran {iss.hours_union:,.0f} violation hours (union of members)"
            + (f", {pct:.1f}% of {iss.fan_on_hours:,.0f} fan-on hours" if pct is not None else "")
            + f"; gate: {iss.fan_gate or R.FAN_GATE_NONE}",
            eq,
            hours=f"{iss.hours_union:,.0f}",
        )
    else:
        add("hours", c, f"Issue {iss.rank}'s violation hours were not measured", eq)
    for i, f in enumerate(iss.members):
        on = getattr(f, "equip", eq)
        role = "root" if i == 0 else "member"
        add(
            "finding",
            c,
            f"{role} finding {getattr(f, 'rule', '')} on {on} ({getattr(f, 'severity', '')}): "
            f"{_clean(getattr(f, 'summary', ''))}",
            on,
            rule=getattr(f, "rule", ""),
        )
    if action:
        add(
            "action",
            c,
            f"Recommended action, {title}: {_clean(action)}"
            + (f" Suggested: {_clean(suggested)}" if suggested else ""),
            eq,
        )
    else:
        add("action", c, "No packaged action: the engineer is to specify the fix", eq)
    for w in iss.why:
        add("why", c, f"Confidence {iss.confidence}: {_clean(w)}", eq)
    for cond in iss.conditional_on:
        add("dependency", c, f"Conditional pending a sensor fix: {cond.label()}", eq)
    for f in iss.dependents:
        add(
            "dependency",
            c,
            f"{getattr(f, 'rule', '')} on {getattr(f, 'equip', '')} is conditional on this "
            "sensor issue",
            getattr(f, "equip", ""),
        )
    for u in getattr(iss, "upstream_causes", None) or ():
        add("link", c, f"Upstream cause: {u.label()}", eq)
    for f in getattr(iss, "downstream", None) or ():
        add(
            "link",
            c,
            f"This shortfall may explain {getattr(f, 'rule', '')} on {getattr(f, 'equip', '')}",
            getattr(f, "equip", ""),
        )
    for z in getattr(iss, "zone_links", None) or ():
        if not z.boxes:
            continue
        add(
            "chain",
            c,
            f"Cause chain {z.chain}: {z.cause} at {z.ahu}, {z.symptom} on {z.count()} "
            f"({R._name_boxes(z.boxes)})",
            z.ahu,
            n_boxes=len(z.boxes),
            n_served=len(z.served),
        )
    seen: list = []
    for f in iss.members:
        for cv in getattr(f, "caveats", None) or []:
            if cv not in seen:
                seen.append(cv)
                add("caveat", c, f"Caveat: {cv}", eq)


# ============================================================================ slots


def load_prose(path) -> dict:
    """Load a pre-generated prose file: a JSON object ``{slot: text}`` (``{}`` for ``None``).

    Slots are ``exec_summary`` and ``issue:<key>``. A value is a string or ``{"text": ...}``.
    """
    if not path:
        return {}
    with open(path) as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("a prose file must be a JSON object keyed by slot id")
    out = {}
    for k, v in data.items():
        text = v.get("text") if isinstance(v, dict) else v
        if isinstance(text, str):
            out[str(k)] = text
    return out


def _slot_ids(rep) -> list:
    """The prose slots of ``rep``, in page order: the executive summary, then each issue page."""
    out = []
    for s in rep.sections:
        if s["id"] == "summary":
            out.append("exec_summary")
        elif s.get("kind") == "issue" and s.get("slot"):
            out.append(s["slot"])
    return out


def _scope(rep, slot: str) -> Context:
    """The facts a slot may cite: the summary's are the report-level facts plus each summarised
    issue's headline, cost, hours and action; an issue page's are its own plus the assumptions."""
    facts = rep.facts.facts if rep.facts is not None else []
    if slot == "exec_summary":
        top = {i.key for i in rep.issues[: max(int(rep.options.top_n), 0)]}
        report_ctx = (_CTX_SUMMARY, _CTX_APPENDIX_A, _CTX_APPENDIX_B)
        keep = [
            f
            for f in facts
            if _ctx(f.id) in report_ctx
            or (_ctx(f.id) in top and f.kind in ("issue", "hours", "action"))
            or (_ctx(f.id) in top and f.kind == "cost" and f.id.startswith("C1@"))
        ]
    else:
        key = slot.split(":", 1)[1] if ":" in slot else slot
        keep = [f for f in facts if _ctx(f.id) in (key, _CTX_APPENDIX_B)]
    return Context(facts=keep, site=rep.facts.site if rep.facts is not None else None)


def _ctx(fid: str) -> str:
    return fid.split("@", 1)[1] if "@" in fid else ""


def _task(slot: str) -> str:
    if slot == "exec_summary":
        return (
            "Write the executive summary paragraph: 2 to 4 sentences on the most important "
            "issues, their costs or why they are uncosted, and how far the data can be trusted."
        )
    return (
        "Write the issue page's 'what we found / why it matters' paragraph: 2 to 4 sentences on "
        "what the evidence shows, what it costs or why it is uncosted, and the recommended action."
    )


def prose_prompts(rep) -> dict:
    """``{slot: prompt}``: the exact prompt each prose slot gives a model, fact block included.

    A caller generating prose offline sends these to its own model and writes the answers to a
    prose file keyed by the same slots."""
    return {slot: _prompt(rep, slot) for slot in _slot_ids(rep)}


def _prompt(rep, slot: str) -> str:
    return "\n\n".join([_SYSTEM, "Facts:", _scope(rep, slot).to_prompt_block(), _task(slot)])


def _why_failed(g) -> str:
    counts: dict = {}
    for fl in g.flagged:
        counts[fl["reason"]] = counts.get(fl["reason"], 0) + 1
    words = {
        "uncited-sentence": "sentence(s) without a fact citation",
        "uncited-number": "sentence(s) stating a figure no cited fact holds",
        "unknown-citation": "citation(s) of an id not in the fact index",
    }
    bits = [f"{counts[r]} {words[r]}" for r in words if counts.get(r)]
    return "; ".join(bits) or "no text"


def _apply_prose(rep, *, prose=None, client=None) -> None:
    """Verify each slot's prose and place it on the page (mutates ``rep``).

    ``prose`` is ``{slot: text}`` (see :func:`load_prose`); ``client`` an
    :class:`~camber.agent.client.AgentClient`, asked only for the slots ``prose`` does not cover.
    With neither (or an unwired client) this does nothing, so the report stays byte-identical.
    """
    prose = dict(prose or {})
    wired = client is not None and getattr(client, "wired", False)
    if not prose and not wired:
        return
    rep.prose_enabled = True
    by_slot = {}
    for s in rep.sections:
        if s["id"] == "summary":
            by_slot["exec_summary"] = s
        elif s.get("kind") == "issue" and s.get("slot"):
            by_slot[s["slot"]] = s
    for slot in _slot_ids(rep):
        if slot not in prose and not wired:
            continue
        source = "file" if slot in prose else "llm"
        if slot in rep.notes:  # the engineer's note overrides generated prose (and is not sent)
            rep.prose[slot] = {
                "status": "overridden",
                "source": source,
                "reason": "engineer note",
                "text": "",
                "cited": [],
            }
            continue
        if source == "file":
            raw = prose[slot]
        else:
            try:
                raw = client.generate(_prompt(rep, slot))
            except Exception as exc:  # noqa: BLE001 - a failed call falls back, never aborts
                rep.prose[slot] = {
                    "status": "fallback",
                    "source": source,
                    "reason": f"generation failed ({type(exc).__name__})",
                    "text": "",
                    "cited": [],
                }
                continue
        scope = _scope(rep, slot)
        g = check(raw, scope, strict=True, source=source, require_cite_each_sentence=True)
        dropped = any(f["reason"] in ("uncited-sentence", "uncited-number") for f in g.flagged)
        if dropped or not g.text.strip():
            rep.prose[slot] = {
                "status": "fallback",
                "source": source,
                "reason": _why_failed(g),
                "text": "",
                "cited": [],
            }
            continue
        rep.prose[slot] = {
            "status": "rendered",
            "source": source,
            "reason": _why_failed(g) if g.flagged else "",
            "text": g.text,
            "cited": list(g.cited),
        }
        block = {
            "kind": "prose",
            "slot": slot,
            "source": source,
            "text": g.text,
            "facts": [[f.id, f.text] for f in g.facts],
        }
        sec = by_slot[slot]
        sec["blocks"].insert(_insert_at(sec), block)
    _appendix_rows(rep)


def _insert_at(sec) -> int:
    """After the KPI strip on the summary; after the opening banners on an issue page."""
    blocks = sec["blocks"]
    if sec["id"] == "summary":
        return 1 if blocks and blocks[0].get("kind") == "kpis" else 0
    i = 0
    while i < len(blocks) and blocks[i].get("kind") == "banner":
        i += 1
    return i


def _appendix_rows(rep) -> None:
    from . import rcx as R

    secs = {s["id"]: s for s in rep.sections}
    fell = [(k, v) for k, v in rep.prose.items() if v["status"] == "fallback"]
    a = secs.get("appendix-a")
    if a is not None and fell:
        if len(a["blocks"]) == 1 and a["blocks"][0].get("text") == "Nothing was declined.":
            a["blocks"] = []
        a["blocks"].append(
            R._p(
                "Generated prose not shown (the page keeps its standard text): a slot is shown "
                "only when every sentence cites the fact index (Appendix D) and every figure is "
                "in a fact it cites:"
            )
        )
        a["blocks"].append(
            R._table(["Slot", "Source", "Why"], [[k, v["source"], v["reason"]] for k, v in fell])
        )
    d = secs.get("appendix-d")
    if d is not None and rep.facts is not None:
        rows = [[f.id, f.kind, f.text] for f in rep.facts.facts]
        d["blocks"] = [
            R._p(
                "Every fact generated prose may cite, built from the same objects as the tables. "
                "An id reads kind, number and context: @summary is the executive summary, @appA "
                "and @appB the appendices, and an issue's facts carry its key."
            ),
            R._table(["Fact", "Kind", "Statement"], rows),
        ]


def _prose_html(b: dict, esc) -> str:
    """A verified prose block: the marker, the text with its citations, and the cited facts."""
    src = "an offline prose file" if b.get("source") == "file" else "a language model"
    tag = (
        f"Generated text from {src}, checked against the fact index: every sentence cites the "
        "facts listed below."
    )
    parts = []
    last = 0
    text = b["text"]
    for m in _CITE.finditer(text):
        parts.append(esc(text[last : m.start()]))
        parts.append(f"<sup class='cite'>[{esc(m.group(1))}]</sup>")
        last = m.end()
    parts.append(esc(text[last:]))
    body = re.sub(r"\s+(<sup)", r" \1", "".join(parts))
    items = "".join(f"<li><b>{esc(i)}</b> {esc(t)}</li>" for i, t in b.get("facts", []))
    return (
        f"<div class='prose' role='note'><div class='prose-tag'>{esc(tag)}</div>"
        f"<p>{body}</p><ol class='prose-facts'>{items}</ol></div>"
    )


#: print and screen rules for prose blocks, added to the page only when one is shown
_PROSE_CSS = (
    ".prose{border-left:4px solid var(--accent);background:#f2f8f9;padding:6px 10px;margin:8px 0}"
    ".prose .prose-tag{font-size:11px;color:var(--muted);font-weight:600}"
    ".prose p{margin:4px 0}.prose sup.cite{font-size:9px;color:var(--accent)}"
    ".prose ol.prose-facts{font-size:11px;color:var(--muted);margin:4px 0;padding-left:18px;"
    "list-style:none}"
    "@media print{.prose{break-inside:avoid}}"
)

#: added when the executive summary shows prose: lift its one-page print cap
#: (``max-height`` + ``overflow:hidden``) so the paragraph is never clipped in print
_SUMMARY_UNCAP_CSS = "@media print{section#summary{max-height:none;overflow:visible}}"
