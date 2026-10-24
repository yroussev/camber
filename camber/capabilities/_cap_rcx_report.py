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
    cite,
    facility_config,
    jsonable,
    narrow,
    run,
    window,
    with_rules,
)

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
            "maximum": 50,
            "description": "issues to rank (default: the config's, else 8)",
        },
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
        "issues": {"type": "array", "items": _ISSUE, "description": "ranked"},
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
            "description": "the report's fact index (ids such as K1@summary-<token>); "
            "every entry is also in the result's facts",
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
    config, base_dir, from_caller = facility_config(ctx, fid, keep=(*RULE_SECTIONS, "report"))
    config = with_rules(config, None, None)
    config = narrow(config, equips=arguments.get("equips"), start=start, end=end)
    config["report"] = {**dict(config.get("report") or {}), "layout": "rcx"}
    for key in ("out_text", "out_html"):
        config["report"].pop(key, None)
    res = run(ctx, config, base_dir)
    opts, banners = _options(config, base_dir, arguments.get("top_n"))
    rep = build_rcx_report(res, options=opts)
    d = rep.to_dict()

    index = []
    facts = []
    for f in rep.facts.facts if rep.facts is not None else []:
        index.append({"id": f.id, "kind": f.kind, "equip": f.equip or "", "text": f.text})
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
    n = int(k.get("n_issues") or 0)
    cost = jsonable(k.get("annual_cost_usd"))
    cost_txt = f"${cost:,.0f}/yr across {k.get('n_costed')} costed issue(s)" if cost else "no cost"
    head = {
        "id": "F1@rcx_report",
        "kind": "rcx_report",
        "facility_id": fid,
        "value": n,
        "text": f"The RCx report for {fid} ranks {n} issue(s) with {cost_txt}",
        "kpis": jsonable(k),
    }
    issues = []
    text = [cite(head)]
    for i, iss in enumerate(d["issues"], start=2):
        fact_id = f"F{i}@rcx_report"
        cost_v = jsonable(iss.get("cost"))
        cost_v = cost_v if isinstance(cost_v, (int, float)) else None
        row = {
            "key": str(iss["key"]),
            "rank": int(iss["rank"]),
            "title": str(iss.get("title") or ""),
            "cause": str(iss.get("cause") or ""),
            "equip": str(iss.get("equip") or ""),
            "severity": str(iss.get("severity") or ""),
            "rules": [str(r) for r in iss.get("rules") or []],
            "annual_cost_usd": cost_v,
            "cost_basis_note": str(iss.get("cost_basis_note") or ""),
            "hours": jsonable(iss.get("hours_union")),
            "pct_runtime": jsonable(iss.get("pct_runtime")),
            "confidence": str(iss.get("confidence") or ""),
            "conditional_on": [str(c) for c in iss.get("conditional_on") or []],
            "why": [str(w) for w in iss.get("why") or []],
            "fact": fact_id,
        }
        issues.append(row)
        money = f"${cost_v:,.0f}/yr" if cost_v else "uncosted"
        facts.append(
            {
                "id": fact_id,
                "kind": "issue",
                "equip": row["equip"],
                "value": row["rank"],
                "text": f"Issue {row['rank']} on {row['equip']}: {row['cause'] or row['title']} "
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
        "sections": [{"id": str(s["id"]), "title": str(s["title"])} for s in d["sections"]],
        "fact_index": index,
    }
    return CapabilityResult(data=data, text=" ".join(text), facts=facts, banners=banners)


CAPABILITIES = (
    Capability(
        name="rcx_report",
        title="RCx report (structured)",
        description=(
            "Build the retro-commissioning (RCx) report for one facility and return it as data: "
            "the KPIs (issue counts, annual cost, data coverage), the ranked issues (cause, "
            "equipment, severity, rules, annual cost or why uncosted, hours, confidence and its "
            "reasons), the section list, and the report's fact index (ids such as "
            "K1@summary-<token> and C1@<issue key>-<token>, also returned as facts) for "
            "writing a cited executive summary. "
            "No file and no charts are returned. Uses the caller's run config for the facility "
            "when there is one. Cost: a full rule run plus the report's analysis -- the slowest "
            "capability; prefer rule_run for a quick look. Needs facility_id."
        ),
        input_schema=INPUT_SCHEMA,
        output_schema=OUTPUT_SCHEMA,
        handler=rcx_report,
        long_running=True,
    ),
)
