"""The ``rcx_report`` capability: the RCx report as structured data and its fact index (0.110,
#278).

The report is built by :func:`camber.report.rcx.build_rcx_report` from a no-persist rule run
(:func:`run_config_for`) and returned as data -- KPIs, ranked issues, the section list and the
report's own fact index -- never as a file. A configured OAT reference that would fetch weather
over the network is left out (a CSV reference is kept), and no LLM writes prose.
"""

from __future__ import annotations

from . import Capability, CapabilityResult
from ._common import (
    DATE,
    EQUIPS,
    FACILITY,
    RULE_SECTIONS,
    check_run_selection,
    cite,
    facility_config,
    finding_ids,
    jsonable,
    narrow,
    rule_names,
    run,
    trust_banner,
    trust_declines,
    window,
    with_rules,
)
from ._page import MAX_LIMIT, OFFSET, limit_arg, page
from ._sanitize import name, plain

#: appendix A facts (declines and data gaps) returned by default (#299)
_APPENDIX = 20

INPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["facility_id"],
    "additionalProperties": False,
    "properties": {
        "facility_id": FACILITY,
        "equips": EQUIPS,
        "start": DATE,
        "end": DATE,
        "top_n": {
            "type": "integer",
            "minimum": 1,
            "maximum": 200,
            "description": "issues to return, best ranked first (default: the config's, else 8)",
        },
        "offset": {**OFFSET, "description": "ranked issues to skip, for the next page"},
        "limit": limit_arg(_APPENDIX, "the fact index's appendix A (declines, data gaps)"),
    },
}

_ISSUE = {
    "type": "object",
    "required": [
        "key",
        "rank",
        "title",
        "cause",
        "equip",
        "severity",
        "rules",
        "finding_ids",
        "annual_cost_usd",
        "cost_basis_note",
        "hours",
        "pct_runtime",
        "confidence",
        "conditional_on",
        "why",
        "fact",
    ],
    "additionalProperties": False,
    "properties": {
        "key": {"type": "string", "description": "the issue key the report's fact ids use"},
        "rank": {"type": "integer", "minimum": 1},
        "title": {"type": "string"},
        "cause": {"type": "string"},
        "equip": {"type": "string"},
        "severity": {"type": "string"},
        "rules": {"type": "array", "items": {"type": "string"}},
        "finding_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "the rule findings behind the issue, for finding_detail (the same "
            "ids rule_run gives); [] for an issue of drift or report checks only",
        },
        "annual_cost_usd": {"type": ["number", "null"]},
        "cost_basis_note": {"type": "string"},
        "hours": {"type": ["number", "null"]},
        "pct_runtime": {"type": ["number", "null"]},
        "confidence": {"type": "string"},
        "conditional_on": {"type": "array", "items": {"type": "string"}},
        "why": {"type": "array", "items": {"type": "string"}},
        "fact": {"type": "string", "description": "the fact id that cites this issue"},
    },
}

_FACT = {
    "type": "object",
    "required": ["id", "kind", "equip", "text"],
    "additionalProperties": False,
    "properties": {
        "id": {"type": "string"},
        "kind": {"type": "string"},
        "equip": {"type": "string"},
        "text": {"type": "string"},
    },
}

OUTPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": [
        "facility_id",
        "title",
        "site",
        "period",
        "from_config",
        "kpis",
        "issues",
        "sections",
        "fact_index",
    ],
    "additionalProperties": False,
    "properties": {
        "facility_id": {"type": "string"},
        "title": {"type": "string"},
        "site": {"type": "string"},
        "period": {"type": "array", "items": {"type": "string"}},
        "from_config": {"type": "boolean"},
        "kpis": {"type": "object"},
        "issues": {"type": "array", "items": _ISSUE, "description": "ranked; top_n of them"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "title"],
                "additionalProperties": False,
                "properties": {"id": {"type": "string"}, "title": {"type": "string"}},
            },
        },
        "fact_index": {
            "type": "array",
            "items": _FACT,
            "description": "the report's fact index for the KPIs, the appendices and the "
            "issues returned (ids such as K1@summary-<token>); also in the result's facts",
        },
    },
}

#: ranked issues named in the summary
_LISTED = 5


def _options(config: dict, base_dir: str, top_n):
    from ..report.rcx import RcxOptions

    opts = RcxOptions.from_config(config.get("report"), base_dir=base_dir)
    banners = []
    if opts.oat_reference and opts.oat_reference.get("fetch"):
        opts.oat_reference = None  # a network weather fetch: never from an assistant call
        banners.append(
            "The run config's OAT reference fetches weather over the network; it was left out "
            "here, so the report compares the OAT sources with each other instead."
        )
    if top_n:
        opts.top_n = int(top_n)
    return opts, banners


def rcx_report(ctx, arguments: dict) -> CapabilityResult:
    """The RCx report of one allowed facility, as data plus its fact index."""
    from ..report.rcx import build_rcx_report

    fid = arguments["facility_id"]
    start, end = window(arguments)
    config, base_dir, from_caller, notice = facility_config(
        ctx, fid, keep=(*RULE_SECTIONS, "drift", "report")
    )
    check_run_selection(ctx, fid, arguments, config)  # 0.111 (#296)
    config = with_rules(config, None, None)
    config = narrow(config, equips=arguments.get("equips"), start=start, end=end)
    config["report"] = {**dict(config.get("report") or {}), "layout": "rcx"}
    for key in ("out_text", "out_html"):
        config["report"].pop(key, None)
    res = run(ctx, config, base_dir)
    opts, banners = _options(config, base_dir, arguments.get("top_n"))
    if notice:
        banners.insert(0, notice)
    rep = build_rcx_report(res, options=opts)
    d = rep.to_dict()
    # 0.111 (#299): top_n is the page of ranked issues returned, with their facts and sections
    every = {str(i["key"]) for i in d["issues"]}
    offset = arguments.get("offset") or 0
    shown = page(
        d["issues"],
        {"limit": max(int(opts.top_n), 1), "offset": offset},
        default=8,
        what="issues",
        order="best ranked first",
        how=f"call again with offset={offset + max(int(opts.top_n), 1)} or a larger top_n "
        "(up to 200)",
        more="kpis count every issue",
    )
    left_out = every - {str(i["key"]) for i in shown}
    # appendix A (declines and data gaps) has an entry per rule and equipment: a page of it
    every_fact = rep.facts.facts if rep.facts is not None else []
    appendix = [f.id for f in every_fact if f.id.partition("@")[2] == "appA"]
    kept = set(
        page(
            appendix,
            {"limit": arguments.get("limit")},
            default=_APPENDIX,
            what="fact_index appendix A",
            order="as in the report",
            how=f"call again with a larger limit (up to {MAX_LIMIT})",
        )
    )
    left_out |= {"appA"} if len(kept) < len(appendix) else set()

    index = []
    facts = []
    for f in every_fact:
        if f.id.partition("@")[2] in left_out and f.id not in kept:
            continue
        index.append({"id": f.id, "kind": f.kind, "equip": f.equip or "", "text": plain(f.text)})
        facts.append(
            {
                "id": f.id,
                "kind": f.kind,
                "equip": f.equip or "",
                "text": f.text,
                "data": jsonable(f.data),
            }
        )
    k = d["kpis"]
    declines, floor = trust_declines(res.findings)  # 0.111 (#297)
    declined = f"; {len(declines)} check(s) declined on untrusted input(s)" if declines else ""
    notice = trust_banner(declines, ctx.min_trust if floor is None else floor)
    if notice:
        banners.append(notice)
    n = int(k.get("n_issues") or 0)
    cost = jsonable(k.get("annual_cost_usd"))
    cost_txt = f"${cost:,.0f}/yr across {k.get('n_costed')} costed issue(s)" if cost else "no cost"
    head = {
        "id": "F1@rcx_report",
        "kind": "rcx_report",
        "facility_id": fid,
        "value": n,
        "text": f"The RCx report for {fid} ranks {n} issue(s) with {cost_txt}{declined}",
        "kpis": jsonable(k),
    }
    # #304: each issue names its findings' ids, so finding_detail needs no extra rule_run
    id_of = {id(f): i for f, i in zip(res.findings, finding_ids(res.findings))}
    rule_set = set(rule_names())
    by_key = {str(i.key): i for i in rep.issues}

    def _ids(key: str) -> list:
        iss = by_key.get(key)
        found = [getattr(iss, "root", None), *(getattr(iss, "members", None) or ())]
        ids = [id_of[id(f)] for f in found if id(f) in id_of and f.rule in rule_set]
        return list(dict.fromkeys(ids))[:20]

    issues = []
    text = [cite(head)]
    for i, iss in enumerate(shown, start=2):
        fact_id = f"F{i}@rcx_report"
        cost_v = jsonable(iss.get("cost"))
        cost_v = cost_v if isinstance(cost_v, (int, float)) else None
        row = {
            "key": str(iss["key"]),
            "rank": int(iss["rank"]),
            "title": str(iss.get("title") or ""),
            "cause": plain(str(iss.get("cause") or "")),
            "equip": str(iss.get("equip") or ""),
            "severity": str(iss.get("severity") or ""),
            "rules": list(dict.fromkeys(str(r) for r in iss.get("rules") or [])),
            "finding_ids": _ids(str(iss["key"])),
            "annual_cost_usd": cost_v,
            "cost_basis_note": plain(str(iss.get("cost_basis_note") or "")),
            "hours": jsonable(iss.get("hours_union")),
            "pct_runtime": jsonable(iss.get("pct_runtime")),
            "confidence": str(iss.get("confidence") or ""),
            "conditional_on": [str(c) for c in iss.get("conditional_on") or []],
            "why": [plain(str(w)) for w in iss.get("why") or []],
            "fact": fact_id,
        }
        issues.append(row)
        money = f"${cost_v:,.0f}/yr" if cost_v else "uncosted"
        # 0.111 (#297): an issue that rests on a sensor a reference found wrong says so
        cond = sorted({c.split(" (")[0] for c in row["conditional_on"]})
        if cond:
            money += f"; conditional on {', '.join(cond)}"
        facts.append(
            {
                "id": fact_id,
                "kind": "issue",
                "equip": row["equip"],
                "value": row["rank"],
                "text": f"Issue {row['rank']} on {name(row['equip'])}: "
                f"{row['cause'] or row['title']} "
                f"({row['severity']}, confidence {row['confidence']}, {money})",
                "issue_key": row["key"],
            }
        )
        if i - 1 <= _LISTED:
            text.append(cite(facts[-1]))
    facts.insert(0, head)
    data = {
        "facility_id": fid,
        "title": str(d["title"]),
        "site": str(d["site"] or ""),
        "period": [str(p) for p in d["period"]],
        "from_config": from_caller,
        "kpis": jsonable(k),
        "issues": issues,
        "sections": [
            {"id": str(s["id"]), "title": str(s["title"])}
            for s in d["sections"]
            if str(s["id"]).removeprefix("issue-") not in left_out
        ],
        "fact_index": index,
    }
    return CapabilityResult(data=data, text=" ".join(text), facts=facts, banners=banners)


CAPABILITIES = (
    Capability(
        name="rcx_report",
        title="RCx report (structured)",
        description=(
            "The retro-commissioning (RCx) report of one facility as data: the KPIs (issue "
            "counts, annual cost, coverage), the top_n ranked issues (cause, equipment, severity,"
            " rules and the finding ids for finding_detail, cost or why uncosted, hours, "
            "confidence and its reasons), the section list "
            "and the report's fact index (K1@summary-<token>, C1@<issue key>-<token>; also in "
            "facts) for a cited executive summary. No file or charts. Uses the caller's run "
            "config when there is one. Cost: a full rule run plus the analysis, the slowest tool;"
            " prefer rule_run for a quick look. Needs facility_id."
        ),
        input_schema=INPUT_SCHEMA,
        output_schema=OUTPUT_SCHEMA,
        handler=rcx_report,
        long_running=True,
    ),
)
