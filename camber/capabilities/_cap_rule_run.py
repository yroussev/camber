"""The ``rule_run`` and ``finding_detail`` capabilities: run the FDD rules on one facility, and
read one finding back with its evidence (0.110, #278).

Both run through :func:`run_config_for` (no-persist, the trust floor, the allowlist and the
licence check) on the caller's run config for the facility when there is one, else on a
store-backed config built from the facility's stored equipment classes.
"""

from __future__ import annotations

from . import Capability, CapabilityError, CapabilityResult
from ._common import (
    COUNTS,
    DATE,
    EQUIPS,
    FACILITY,
    FINDING_ROW,
    MASK,
    SEVERITIES,
    SEVERITY,
    by_severity,
    facility_config,
    finding_fact,
    finding_ids,
    finding_row,
    jsonable,
    mask_summary,
    narrow,
    rule_names,
    run,
    severity_counts,
    window,
    with_rules,
)

_RULES = {
    "type": "array",
    "items": {"type": "string", "enum": list(rule_names())},
    "minItems": 1,
    "maxItems": 100,
    "description": "only these rules (see rule_params); default: the run config's rules, or "
    "every built-in rule",
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
            "description": "True when the caller's run config for this facility was used",
        },
        "window": _WINDOW,
        "rules_run": {"type": "array", "items": {"type": "string"}},
        "n_equipment": {"type": "integer", "minimum": 0},
        "counts": COUNTS,
        "count": {"type": "integer", "minimum": 0, "description": "listed findings before caps"},
        "findings": {"type": "array", "items": FINDING_ROW},
        "skipped": {"type": "array", "items": _SKIP},
    },
}

#: findings that get their own fact (the rest are in ``data`` only)
_FACTS = 200
#: findings named in the summary
_LISTED = 10


def _run_for(ctx, arguments: dict, rules=None):
    fid = arguments["facility_id"]
    start, end = window(arguments)
    config, base_dir, from_caller = facility_config(ctx, fid)
    config = with_rules(config, rules or arguments.get("rules"), arguments.get("params"))
    config = narrow(config, equips=arguments.get("equips"), start=start, end=end)
    return run(ctx, config, base_dir), from_caller, (start, end)


def rule_run(ctx, arguments: dict) -> CapabilityResult:
    """Run the FDD rules on one allowed facility and return the findings, worst first."""
    fid = arguments["facility_id"]
    res, from_caller, (start, end) = _run_for(ctx, arguments)
    rows = by_severity([finding_row(i, f) for i, f in zip(finding_ids(res.findings), res.findings)])
    counts = severity_counts(rows)
    floor = SEVERITIES.index(arguments.get("min_severity") or "info")
    listed = [r for r in rows if SEVERITIES.index(r["severity"]) <= floor]
    skipped = sorted(
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
    n_rules = len(res.rules_run)
    facts = [
        {
            "id": "F1@rule_run",
            "kind": "rule_run",
            "facility_id": fid,
            "value": len(rows),
            "counts": counts,
            "text": f"{n_rules} rules ran on {res.equipment} equipment of {fid}: "
            f"{counts['fault']} fault, {counts['warn']} warn, {counts['info']} info and "
            f"{counts['ok']} ok findings, {len(skipped)} rule(s) skipped for missing inputs",
        }
    ]
    text = [
        f"{n_rules} rules ran on {res.equipment} equipment of {fid}, with {counts['fault']} "
        f"fault and {counts['warn']} warn findings [F1@rule_run]."
    ]
    for i, r in enumerate(listed[:_FACTS], start=2):
        fact_id = f"F{i}@rule_run"
        r["fact"] = fact_id
        facts.append(finding_fact(fact_id, r))
        if i - 1 <= _LISTED and r["severity"] in ("fault", "warn"):
            text.append(f"{r['equip']}: {r['rule']} is {r['severity']} [{fact_id}].")
    banners = []
    if not from_caller:
        banners.append(
            "No run config names this facility, so every equipment class it stores ran with "
            "every built-in rule (or the rules asked for) at their default parameters."
        )
    data = {
        "facility_id": fid,
        "from_config": from_caller,
        "window": {"start": start, "end": end},
        "rules_run": sorted(res.rules_run),
        "n_equipment": int(res.equipment),
        "counts": counts,
        "count": len(listed),
        "findings": listed,
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
            "pattern": r"^[a-z0-9_]+@.+$",
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
            "description": "the hours the rule flagged, as contiguous spans",
            **{k: v for k, v in MASK.items() if k != "type"},
        },
        "conditions": {
            "type": "array",
            "description": "named sub-conditions (e.g. one per G36 fault condition)",
            "items": {
                "type": "object",
                "required": ["name", "hours", "samples", "share", "n_spans", "spans"],
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
    roles = [str(jsonable(r)) for r in ev.roles or []]
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
    mask = ev.mask
    if mask is None and ev.template is not None:
        from ..charts.diagnostic import template_violations

        mask = template_violations(ev.frame if ev.frame is not None else frame, ev.template)
    return None if mask is None else mask_summary(mask)


def _parameters(rule: str, entry_params: dict) -> list:
    from ..rules.param_docs import describe

    try:
        params = describe(rule)["params"]
    except KeyError:
        return []
    out = []
    for p in params:
        name = p["name"]
        over = name in entry_params
        row = {
            "name": name,
            "value": jsonable(entry_params[name] if over else p.get("default")),
            "default": jsonable(p.get("default")),
            "overridden": over,
        }
        if p.get("unit"):
            row["unit"] = str(p["unit"])
        if p.get("basis"):
            row["basis"] = str(p["basis"])
        out.append(row)
    return out


def finding_detail(ctx, arguments: dict) -> CapabilityResult:
    """One finding of a rule run, with its metrics, parameters and evidence hours."""
    from ..charts.evidence import finding_evidence

    fid, want = arguments["facility_id"], arguments["finding_id"]
    rule = want.split("@", 1)[0]
    if rule not in rule_names():
        raise CapabilityError("not_found", f"no finding {want!r}: {rule!r} is not a rule")
    res, _from_caller, _w = _run_for(ctx, arguments, rules=[rule])
    found = dict(zip(finding_ids(res.findings), res.findings))
    f = found.get(want)
    if f is None:
        raise CapabilityError(
            "not_found",
            f"no finding {want!r} in this run (call rule_run with the same arguments to list "
            "the finding ids)",
        )
    row = finding_row(want, f)
    entry = next(
        (e for e in res.config.get("rules") or [] if isinstance(e, dict) and e.get("name") == rule),
        {},
    )
    ev, frame = None, None
    try:
        rule_obj = res.registry.get(rule)
        frame = res.frame_for(f.equip)
        if frame is not None and not frame.empty:
            ev = finding_evidence(rule_obj, f.equip, frame)
    except Exception:  # noqa: BLE001 - a rule that cannot draw simply has no evidence
        ev = None
    chart = _chart(ev)
    try:
        viol = _violations(ev, frame)
    except Exception:  # noqa: BLE001 - as above
        viol = None
    conditions = []
    masks = getattr(ev, "masks", None) if ev is not None else None
    if isinstance(masks, dict):
        for name in sorted(masks, key=str):
            if masks[name] is not None:
                conditions.append({"name": str(name), **mask_summary(masks[name])})
    params = _parameters(rule, dict(entry.get("params") or {}))

    row["fact"] = "F1@finding_detail"
    facts = [finding_fact("F1@finding_detail", row)]
    text = [f"{row['equip']}: {row['rule']} is {row['severity']} [F1@finding_detail]."]
    if viol is not None:
        facts.append(
            {
                "id": "F2@finding_detail",
                "kind": "evidence_hours",
                "value": viol["hours"],
                "text": f"The rule flagged {viol['hours']:g} hours ({viol['samples']} samples, "
                f"{viol['share']:.1%} of the window) in {viol['n_spans']} span(s)",
                "share": viol["share"],
            }
        )
        text.append(
            f"The rule flagged {viol['hours']:g} hours in {viol['n_spans']} span(s) "
            "[F2@finding_detail]."
        )
    if params:
        listed = ", ".join(
            f"{p['name']} = {p['value']}" + (" (set for this run)" if p["overridden"] else "")
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
        text.append(f"The parameters it ran with are listed [F{n}@finding_detail].")
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
    data = {
        "facility_id": fid,
        "finding": row,
        "metrics": jsonable(f.metrics or {}),
        "parameters": params,
        "chart": chart,
        "violations": viol,
        "conditions": conditions,
    }
    return CapabilityResult(data=data, text=" ".join(text), facts=facts)


CAPABILITIES = (
    Capability(
        name="rule_run",
        title="Run the FDD rules",
        description=(
            "Run CAMBER's fault-detection rules on one facility and return the findings, worst "
            "first: each with a finding_id, rule, equipment, severity (fault / warn / info / ok), "
            "a one-line summary, caveats and its scalar metrics, plus counts per severity and the "
            "rules skipped for missing inputs. Uses the caller's run config for the facility when "
            "there is one (its rules, parameters and mapping), else every built-in rule on every "
            "stored equipment class. Narrow it with rules, equips, a start / end window and "
            "per-rule params (see rule_params). Nothing is saved. Cost: reads the facility's "
            "trends and runs every rule -- seconds on a small site, minutes on a large one; "
            "narrow it when you can. Needs facility_id (see facilities)."
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
            "Explain one finding from rule_run: its full metrics, the parameter values the rule "
            "ran with (default or overridden), the hours it flagged as contiguous time spans "
            "(and per named sub-condition, e.g. G36 fault conditions), and which evidence chart "
            "applies (renderer, the roles plotted, what it shows). Returns no image. Pass the "
            "finding_id and the same facility_id, params, equips and window as the rule_run "
            "call. Cost: re-runs that one rule on the facility."
        ),
        input_schema=DETAIL_INPUT,
        output_schema=DETAIL_OUTPUT,
        handler=finding_detail,
    ),
)
