"""The ``rule_run`` and ``finding_detail`` capabilities: run the FDD rules on one facility, and
read one finding back with its evidence (0.110, #278).

Both run through :func:`run_config_for` (no-persist, the trust floor, the allowlist and the
licence check) on the caller's run config for the facility when there is one, else on a
store-backed config built from the facility's stored equipment classes.
"""

from __future__ import annotations

from dataclasses import replace

import pandas as pd

from . import Capability, CapabilityError, CapabilityResult
from ._common import (
    COUNTS,
    DATE,
    EQUIPS,
    FACILITY,
    FINDING_ROW,
    FLEET,
    FLEET_SHOWN,
    MASK,
    SEVERITIES,
    SEVERITY,
    by_severity,
    check_run_selection,
    cite,
    conditional_banner,
    facility_config,
    finding_fact,
    finding_ids,
    finding_row,
    is_fleet_id,
    jsonable,
    mask_summary,
    narrow,
    reference_conditions,
    reference_findings,
    reference_notes_banner,
    rule_names,
    run,
    severity_counts,
    trust_banner,
    trust_declines,
    window,
    with_rules,
)
from ._page import OFFSET, limit_arg, page
from ._sanitize import plain

_RULES = {
    "type": "array",
    "items": {
        "type": "string",
        "enum": list(rule_names()),
        "description": "a rule name (rule_params kind 'rule'); a drift detector (kind 'drift') "
        "runs with the drift capability",
    },
    "minItems": 1,
    "maxItems": 100,
    "description": "only these rules (see rule_params); default: the run config's rules, or "
    "every built-in rule. Drift detectors run with the drift capability",
}
_PARAMS = {
    "type": "object",
    "additionalProperties": {"type": "object"},
    "description": "per-rule parameter overrides for this run, {rule: {param: value}}; the "
    "names and ranges are in rule_params",
}

_RUN_ARGS = {
    "facility_id": FACILITY,
    "rules": _RULES,
    "params": _PARAMS,
    "equips": EQUIPS,
    "start": DATE,
    "end": DATE,
}

#: findings (and skipped rules) per page by default (#299): about 60 KB on the wire
_PAGE = 25

RULE_RUN_INPUT = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["facility_id"],
    "additionalProperties": False,
    "properties": {
        **_RUN_ARGS,
        "min_severity": {
            **SEVERITY,
            "description": "list findings at least this severe (default info: ok verdicts are "
            "counted, not listed)",
        },
        "limit": limit_arg(_PAGE, "findings, skipped"),
        "offset": OFFSET,
    },
}

_SKIP = {
    "type": "object",
    "required": ["rule", "equip", "reason", "missing"],
    "additionalProperties": False,
    "properties": {
        "rule": {"type": "string"},
        "equip": {"type": "string"},
        "reason": {"type": "string"},
        "missing": {"type": "array", "items": {"type": "string"}},
    },
}

_WINDOW = {
    "type": "object",
    "required": ["start", "end"],
    "additionalProperties": False,
    "properties": {"start": {"type": ["string", "null"]}, "end": {"type": ["string", "null"]}},
}

RULE_RUN_OUTPUT = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": [
        "facility_id",
        "from_config",
        "window",
        "rules_run",
        "n_equipment",
        "counts",
        "count",
        "findings",
        "skipped",
    ],
    "additionalProperties": False,
    "properties": {
        "facility_id": {"type": "string"},
        "from_config": {
            "type": "boolean",
            "description": "True when a run config for this facility was used: the caller's, "
            "or the config template of the catalog dataset it holds (a banner says which)",
        },
        "window": _WINDOW,
        "rules_run": {"type": "array", "items": {"type": "string"}},
        "n_equipment": {"type": "integer", "minimum": 0},
        "counts": COUNTS,
        "count": {"type": "integer", "minimum": 0, "description": "findings before paging"},
        "findings": {"type": "array", "items": FINDING_ROW, "description": "one page"},
        "skipped": {"type": "array", "items": _SKIP, "description": "one page"},
    },
}

#: findings that get their own fact (the rest are in ``data`` only)
_FACTS = 200
#: findings named in the summary
_LISTED = 10


def _run_for(ctx, arguments: dict):
    """The rule run ``rule_run`` makes for ``arguments``; ``finding_detail`` makes the same one
    (#298), so a finding whose verdict depends on another rule's (a fighting room's capacity
    finding) reads the same in both."""
    fid = arguments["facility_id"]
    start, end = window(arguments)
    config, base_dir, from_caller, notice = facility_config(ctx, fid)
    check_run_selection(ctx, fid, arguments, config)  # 0.111 (#296)
    config = with_rules(config, arguments.get("rules"), arguments.get("params"))
    config = narrow(config, equips=arguments.get("equips"), start=start, end=end)
    return run(ctx, config, base_dir), from_caller, notice, (start, end)


def _rows(ctx, fid: str, res, notes: list | None = None) -> list:
    """The run's findings as rows, each marked ``conditional_on`` the configured sensor
    references of its inputs that disagree (0.111, #313); what could not be compared is added
    to ``notes`` (#320)."""
    ids = finding_ids(res.findings)
    refs, ref_notes = reference_findings(ctx, fid, res)
    if notes is not None:
        notes.extend(ref_notes)
    conds = reference_conditions(res.findings, refs, getattr(res, "registry", None))
    rows = []
    for i, f, cond in zip(ids, res.findings, conds):
        row = finding_row(i, f)
        if cond:
            row["conditional_on"] = cond
        rows.append(row)
    return rows


def rule_run(ctx, arguments: dict) -> CapabilityResult:
    """Run the FDD rules on one allowed facility and return the findings, worst first."""
    fid = arguments["facility_id"]
    res, from_caller, config_notice, (start, end) = _run_for(ctx, arguments)
    ref_notes: list = []
    rows = by_severity(_rows(ctx, fid, res, ref_notes))
    counts = severity_counts(rows)
    sev_floor = SEVERITIES.index(arguments.get("min_severity") or "info")
    listed = [r for r in rows if SEVERITIES.index(r["severity"]) <= sev_floor]
    every_skip = sorted(
        (
            {
                "rule": str(s.rule),
                "equip": str(s.equip),
                "reason": str(s.reason),
                "missing": [str(m) for m in s.missing],
            }
            for s in getattr(res, "rules_skipped", None) or []
        ),
        key=lambda s: (s["rule"], s["equip"]),
    )
    shown = page(listed, arguments, default=_PAGE, what="findings", order="worst first")
    skipped = page(every_skip, arguments, default=_PAGE, what="skipped")
    n_rules = len(res.rules_run)
    # #304: rules, not rule x equipment pairs: how many returned a result (an ok included, #310),
    # and the skipped pairs with the number of distinct rules among them
    n_found = len({r["rule"] for r in rows})
    n_skip_rules = len({s["rule"] for s in every_skip})
    skips = (
        f"{len(every_skip)} rule-equipment pair(s) ({n_skip_rules} rule(s)) skipped for missing "
        "inputs"
    )
    declines, floor = trust_declines(res.findings)  # 0.111 (#297)
    declined = f"; {len(declines)} check(s) declined on untrusted input(s)" if declines else ""
    facts = [
        {
            "id": "F1@rule_run",
            "kind": "rule_run",
            "facility_id": fid,
            "value": len(rows),
            "counts": counts,
            "text": f"{n_rules} rules were run on {res.equipment} equipment of {fid} and "
            f"{n_found} of them returned a result: {counts['fault']} fault, {counts['warn']} warn, "
            f"{counts['info']} info and {counts['ok']} ok; {skips}{declined}",
        }
    ]
    text = [
        f"{n_rules} rules were run on {res.equipment} equipment of {fid} and {n_found} of them "
        f"returned a result (ok included), with {counts['fault']} fault and {counts['warn']} warn "
        "findings"
        f"{declined} [F1@rule_run]."
    ]
    for i, r in enumerate(shown[:_FACTS], start=2):
        fact_id = f"F{i}@rule_run"
        r["fact"] = fact_id
        facts.append(finding_fact(fact_id, r))
        if i - 1 <= _LISTED and r["severity"] in ("fault", "warn"):
            text.append(cite(facts[-1]))
    banners = [config_notice] if config_notice else []
    notice = trust_banner(declines, ctx.min_trust if floor is None else floor)
    if notice:
        banners.append(notice)
    # #313; 0.111 (#318): count what is listed, and name the conditional results left out
    hidden: dict = {}
    for r in rows:
        if r.get("conditional_on") and SEVERITIES.index(r["severity"]) > sev_floor:
            hidden[r["severity"]] = hidden.get(r["severity"], 0) + 1
    notice = conditional_banner(sum(1 for r in listed if r.get("conditional_on")), hidden)
    if notice:
        banners.append(notice)
    notice = reference_notes_banner(ref_notes, arguments.get("equips"))  # 0.111 (#320)
    if notice:
        banners.append(notice)
    data = {
        "facility_id": fid,
        "from_config": from_caller,
        "window": {"start": start, "end": end},
        "rules_run": sorted(res.rules_run),
        "n_equipment": int(res.equipment),
        "counts": counts,
        "count": len(listed),
        "findings": shown,
        "skipped": skipped,
    }
    return CapabilityResult(data=data, text=" ".join(text), facts=facts, banners=banners)


# --------------------------------------------------------------------------- finding detail

DETAIL_INPUT = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["facility_id", "finding_id"],
    "additionalProperties": False,
    "properties": {
        **_RUN_ARGS,
        "finding_id": {
            "type": "string",
            "minLength": 3,
            "maxLength": 300,
            "pattern": r"^[a-z0-9_]+@[\s\S]+$",
            "description": "a finding_id from rule_run (pass the same params / equips / window)",
        },
    },
}

_PARAM_USED = {
    "type": "object",
    "required": ["name", "value", "default", "overridden"],
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string"},
        "value": {},
        "default": {},
        "overridden": {"type": "boolean"},
        "fallback": {
            "type": "boolean",
            "description": "true when the parameter was not set (its documented default is "
            "null) and value is the rule's built-in fallback it ran with",
        },
        "unit": {"type": "string"},
        "basis": {"type": "string"},
    },
}

_CHART = {
    "type": ["object", "null"],
    "required": ["renderer", "roles", "title", "description"],
    "additionalProperties": False,
    "properties": {
        "renderer": {
            "type": "string",
            "enum": ["multitrend", "oat_scatter", "diagnostic", "carpet"],
        },
        "roles": {"type": "array", "items": {"type": "string"}},
        "title": {"type": "string"},
        "label": {"type": "string"},
        "template": {"type": "string"},
        "description": {"type": "string"},
    },
}

DETAIL_OUTPUT = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": [
        "facility_id",
        "finding",
        "metrics",
        "parameters",
        "chart",
        "violations",
        "violations_note",
        "conditions",
    ],
    "additionalProperties": False,
    "properties": {
        "facility_id": {"type": "string"},
        "finding": FINDING_ROW,
        "metrics": {"type": "object", "description": "every metric, nested ones included"},
        "parameters": {"type": "array", "items": _PARAM_USED},
        "chart": _CHART,
        "violations": {
            "type": ["object", "null"],
            "description": "the hours the rule flagged, as contiguous spans (null: see "
            "violations_note)",
            **{k: v for k, v in MASK.items() if k != "type"},
        },
        "violations_note": {
            "type": ["string", "null"],
            "description": "why violations is null (a fleet finding, a rule that judges a "
            "trend rather than flagging hours, no evidence for this equipment); null when spans "
            "are given",
        },
        "conditions": {
            "type": "array",
            "description": "named sub-conditions (e.g. one per G36 fault condition)",
            "items": {
                "type": "object",
                "required": [
                    "name",
                    "hours",
                    "samples",
                    "judged_samples",
                    "judged_basis",
                    "share",
                    "n_spans",
                    "spans",
                ],
                "additionalProperties": False,
                "properties": {"name": {"type": "string"}, **MASK["properties"]},
            },
        },
    },
}

_CHART_WORDS = {
    "multitrend": "a multi-trend of {roles} over the analysis window, with the {label} spans "
    "shaded",
    "oat_scatter": "a scatter of {roles} against outdoor-air temperature",
    "diagnostic": "a diagnostic scatter ({template}) with the violating samples marked",
    "carpet": "a carpet plot (hour of day by date) of {roles}",
}


def _chart(ev) -> dict | None:
    if ev is None:
        return None
    plotted = list(ev.roles or [])
    if not plotted and ev.template is not None:  # #310: a diagnostic scatter plots its two axes
        plotted = [ev.template.x, ev.template.y]
    roles = [str(jsonable(r)) for r in plotted]
    renderer = str(ev.renderer)
    template = getattr(ev.template, "name", None) if ev.template is not None else None
    words = _CHART_WORDS.get(renderer, "a {renderer} chart of {roles}").format(
        roles=", ".join(roles) or "the rule's inputs",
        label=ev.label or "violation",
        template=template or "the rule's template",
        renderer=renderer,
    )
    out = {
        "renderer": renderer if renderer in _CHART_WORDS else "multitrend",
        "roles": roles,
        "title": str(ev.title or ""),
        "label": str(ev.label or ""),
        "description": words[0].upper() + words[1:],
    }
    if template:
        out["template"] = str(template)
    return out


def _violations(ev, frame):
    if ev is None:
        return None
    mask, basis = ev.mask, getattr(ev, "judged_basis", None) or None
    if mask is None and ev.template is not None:
        from ..charts.diagnostic import template_violations

        mask = template_violations(ev.frame if ev.frame is not None else frame, ev.template)
        basis = basis or "samples on the evidence chart"  # the rows with both template inputs
    if mask is None:
        return None
    return mask_summary(
        mask,
        events=getattr(ev, "events", None),
        judged=getattr(ev, "judged", None),
        basis=basis,
    )


def _reported_only(ev, finding):
    """``ev`` narrowed to the conditions the finding reports (0.111, #318): a rule that names
    the conditions it reported (``flagged_fcs``: G36 fault conditions at or above ``warn_pct``)
    keeps only those as named conditions and as the union of flagged hours, so a condition
    below the reporting threshold is neither stated as flagged nor counted in the total."""
    flagged = (getattr(finding, "metrics", None) or {}).get("flagged_fcs")
    if ev is None or not isinstance(ev.masks, dict) or not isinstance(flagged, list):
        return ev
    keep = {str(k) for k in flagged}
    masks = {k: v for k, v in ev.masks.items() if str(k) in keep}
    mask = None
    for m in masks.values():
        mask = m if mask is None else (mask | m)
    if mask is None and isinstance(ev.mask, pd.Series):
        mask = pd.Series(False, index=ev.mask.index)
    judged = ev.masks_judged
    if isinstance(judged, dict):
        judged = {k: v for k, v in judged.items() if str(k) in keep}
    return replace(ev, mask=mask, masks=masks, masks_judged=judged)


def _no_spans_note(rule_obj, ev, failed: bool) -> str:
    """Why a finding has no flagged-hour spans (#298)."""
    if rule_obj is not None and hasattr(rule_obj, "analyze_fleet"):
        return (
            "a fleet finding judges many units together, so it has no one unit's flagged hours; "
            "its metrics give the figures per unit (or per room or cohort) -- call finding_detail "
            "on a member unit's own findings for their hours"
        )
    reason = getattr(rule_obj, "no_spans_reason", None)
    if reason:
        return str(reason)
    if failed:
        return "the rule's evidence could not be computed on this equipment's trend"
    if ev is None:
        return "none of the rule's inputs is in this equipment's trend, so no evidence is drawn"
    return (
        "this rule does not mark the individual hours behind its verdict; its metrics carry "
        "the figures it judged"
    )


def _parameters(rule: str, entry_params: dict, rule_obj=None) -> list:
    from ..rules.param_docs import describe

    try:
        params = describe(rule)["params"]
    except KeyError:
        return []
    out = []
    for p in params:
        name = p["name"]
        over = name in entry_params
        value = jsonable(entry_params[name] if over else p.get("default"))
        row = {
            "name": name,
            "value": value,
            "default": jsonable(p.get("default")),
            "overridden": over,
        }
        # #298: a parameter left unset (documented default null) runs on the rule's built-in
        # fallback; show the value the rule used, read from the rule as it ran
        if value is None and rule_obj is not None:
            used = jsonable(getattr(rule_obj, name, None))
            if isinstance(used, (bool, int, float, str)):
                row["value"], row["fallback"] = used, True
        if p.get("unit"):
            row["unit"] = str(p["unit"])
        if p.get("basis"):  # 0.111 (#318): no issue tags in what a model reads, as rule_params
            row["basis"] = plain(str(p["basis"]))
        out.append(row)
    return out


def finding_detail(ctx, arguments: dict) -> CapabilityResult:
    """One finding of a rule run, with its metrics, parameters and evidence hours."""
    from ..charts.evidence import finding_evidence

    fid, want = arguments["facility_id"], arguments["finding_id"]
    rule = want.split("@", 1)[0]
    # 0.111 (#318): the fleet id as it was once shown; only a fleet rule's (#320: a unit stored
    # with that id is a unit, whatever its name)
    if want == f"{rule}@{FLEET_SHOWN}" and is_fleet_id(rule, FLEET):
        want = f"{rule}@{FLEET}"
    if rule not in rule_names():
        raise CapabilityError("not_found", f"no finding {want!r}: {rule!r} is not a rule")
    res, _from_caller, _notice, _w = _run_for(ctx, arguments)
    found = dict(zip(finding_ids(res.findings), res.findings))
    f = found.get(want)
    if f is None:
        raise CapabilityError(
            "not_found",
            f"no finding {want!r} in this run (call rule_run with the same arguments to list "
            "the finding ids)",
        )
    row = next(r for r in _rows(ctx, fid, res) if r["finding_id"] == want)
    entry = next(
        (e for e in res.config.get("rules") or [] if isinstance(e, dict) and e.get("name") == rule),
        {},
    )
    ev, frame, rule_obj, failed = None, None, None, False
    try:
        rule_obj = res.registry.get(rule)
        frame = res.frame_for(f.equip)
        if frame is not None and not frame.empty:
            ev = _reported_only(finding_evidence(rule_obj, f.equip, frame), f)
    except Exception:  # noqa: BLE001 - a rule that cannot draw simply has no evidence
        ev, failed = None, True
    chart = _chart(ev)
    try:
        viol = _violations(ev, frame)
    except Exception:  # noqa: BLE001 - as above
        viol, failed = None, True
    note = None if viol is not None else _no_spans_note(rule_obj, ev, failed)
    conditions = []
    masks = getattr(ev, "masks", None) if ev is not None else None
    if isinstance(masks, dict):
        own = getattr(ev, "masks_judged", None) or {}
        for name in sorted(masks, key=str):
            if masks[name] is not None:
                judged = own.get(name, getattr(ev, "judged", None))
                basis = getattr(ev, "judged_basis", None) or None
                if isinstance(judged, tuple):  # (mask, basis) for a condition's own denominator
                    judged, basis = judged
                conditions.append(
                    {"name": str(name), **mask_summary(masks[name], judged=judged, basis=basis)}
                )
    params = _parameters(rule, dict(entry.get("params") or {}), rule_obj)

    row["fact"] = "F1@finding_detail"
    facts = [finding_fact("F1@finding_detail", row)]
    text = [cite(facts[0])]
    if viol is not None:
        events = ""
        if viol.get("events") is not None:
            events = f" covering {viol['events']} counted event(s)"
        facts.append(
            {
                "id": "F2@finding_detail",
                "kind": "evidence_hours",
                "value": viol["hours"],
                "text": f"The rule flagged {viol['hours']:g} hours{events} ({viol['samples']} "
                f"of {viol['judged_samples']} judged {viol['judged_basis']}, "
                f"{viol['share']:.1%}) in {viol['n_spans']} contiguous span(s)",
                "share": viol["share"],
            }
        )
        text.append(cite(facts[-1]))
        # 0.111 (#310): a finding that judges named conditions (one per coil, per G36 fault
        # condition) states each condition's share of its own judged samples -- the shares the
        # rule_run summary states
        for c in conditions:
            if not c["samples"]:
                continue
            n = len(facts) + 1
            facts.append(
                {
                    "id": f"F{n}@finding_detail",
                    "kind": "condition_hours",
                    "value": c["hours"],
                    "text": f"{c['name']}: the rule flagged {c['hours']:g} hours ({c['samples']} "
                    f"of {c['judged_samples']} judged {c['judged_basis']}, {c['share']:.1%})",
                    "share": c["share"],
                }
            )
            text.append(cite(facts[-1]))
    if params:
        listed = ", ".join(
            (f"{p['name']} = {p['value']}" if p["value"] is not None else f"{p['name']} not set")
            + (" (set for this run)" if p["overridden"] else "")
            + (" (not set: the rule's built-in fallback)" if p.get("fallback") else "")
            for p in params
        )
        n = len(facts) + 1
        facts.append(
            {
                "id": f"F{n}@finding_detail",
                "kind": "parameters",
                "value": len(params),
                "text": f"{rule} ran with {listed}",
            }
        )
        text.append(cite(facts[-1]))
    if chart is not None:
        n = len(facts) + 1
        facts.append(
            {
                "id": f"F{n}@finding_detail",
                "kind": "chart",
                "value": chart["renderer"],
                "text": f"Evidence chart: {chart['description']}",
            }
        )
        text.append(cite(facts[-1]))
    data = {
        "facility_id": fid,
        "finding": row,
        "metrics": jsonable(f.metrics or {}),
        "parameters": params,
        "chart": chart,
        "violations": viol,
        "violations_note": note,
        "conditions": conditions,
    }
    banner = conditional_banner(1 if row.get("conditional_on") else 0)  # 0.111 (#313)
    return CapabilityResult(
        data=data, text=" ".join(text), facts=facts, banners=[banner] if banner else []
    )


CAPABILITIES = (
    Capability(
        name="rule_run",
        title="Run the FDD rules",
        description=(
            "Run the fault-detection rules on one facility: the findings worst first (finding_id,"
            " rule, equipment, severity fault / warn / info / ok, summary, caveats, scalar "
            "metrics), the counts per severity and the rules skipped for missing inputs. Uses the"
            " caller's run config when there is one, else every built-in rule on every stored "
            "equipment class. Narrow with rules, equips, start / end and per-rule params (see "
            "rule_params). Nothing is saved. Cost: seconds to minutes. Needs facility_id."
        ),
        input_schema=RULE_RUN_INPUT,
        output_schema=RULE_RUN_OUTPUT,
        handler=rule_run,
        long_running=True,
    ),
    Capability(
        name="finding_detail",
        title="Finding detail and evidence",
        description=(
            "Explain one rule_run finding: all its metrics, the parameter values the rule ran "
            "with (default, overridden or built-in fallback), the hours it flagged as time spans "
            "(also per named sub-condition such as a G36 fault condition, and any events counted)"
            " or why there are none, and which evidence chart applies. No image. Pass the "
            "finding_id with the same facility_id, rules, params, equips and window as the "
            "rule_run call. Cost: the same rule run."
        ),
        input_schema=DETAIL_INPUT,
        output_schema=DETAIL_OUTPUT,
        handler=finding_detail,
    ),
)
