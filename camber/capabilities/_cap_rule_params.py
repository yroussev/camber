"""The ``rule_params`` capability: the rules, what they apply to and their tunable parameters
(0.110, #278), from :mod:`camber.rules.param_docs` -- defaults read from the constructors, with
each parameter's unit, basis, calibration advice and sensible range.
"""

from __future__ import annotations

import re
from functools import lru_cache

from . import Capability, CapabilityError, CapabilityResult
from ._common import cite, jsonable
from ._page import OFFSET, page
from ._sanitize import plain

#: rules per page by default (#299): without and with detail
_PAGE, _PAGE_DETAIL = 25, 10
#: the parameter fields a rule's fact carries (the figures a sentence may cite)
_FACT_FIELDS = ("name", "default", "unit", "range")


@lru_cache(maxsize=1)
def _names() -> tuple:
    from ..rules.param_docs import documented_drift_rules, documented_rules

    return tuple(sorted({*documented_rules(), *documented_drift_rules()}))


INPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "rules": {
            "type": "array",
            "items": {"type": "string", "enum": list(_names())},
            "minItems": 1,
            "maxItems": 100,
            "description": "these rules / drift detectors only (default: all)",
        },
        "query": {
            "type": "string",
            "minLength": 1,
            "maxLength": 200,
            "description": "words to match in a rule's name, its equipment families and its "
            "parameter names (all words must match)",
        },
        "detail": {
            "type": "boolean",
            "description": "include each parameter's basis, calibration advice and note "
            "(default true when rules are named, else false)",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 1000,
            "description": f"rules per page (default {_PAGE}, or {_PAGE_DETAIL} with detail)",
        },
        "offset": OFFSET,
    },
}

_PARAM = {
    "type": "object",
    "required": ["name", "default"],
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string"},
        "default": {},
        "unit": {"type": "string"},
        "range": {"type": "array"},
        "basis": {"type": "string"},
        "calibrate": {"type": "string"},
        "note": {"type": "string"},
        "passed_to": {"type": "string"},
    },
}

_RULE = {
    "type": "object",
    "required": ["rule", "kind", "run_with", "equip_families", "params"],
    "additionalProperties": False,
    "properties": {
        "rule": {"type": "string"},
        "kind": {"type": "string", "enum": ["rule", "drift"]},
        "run_with": {
            "type": "string",
            "enum": ["rule_run", "drift"],
            "description": "the capability that runs it: a rule with rule_run (and "
            "finding_detail), a drift detector with drift (rule_run refuses it)",
        },
        "equip_families": {
            "type": "array",
            "items": {"type": "string"},
            "description": "equipment families the rule is written for ([] = any with its inputs)",
        },
        "params": {"type": "array", "items": _PARAM},
        "fixed_in_code": {
            "type": "string",
            "description": "thresholds fixed in code, not tunable",
        },
    },
}

OUTPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["count", "rules"],
    "additionalProperties": False,
    "properties": {
        "count": {"type": "integer", "minimum": 0, "description": "matches before paging"},
        "rules": {"type": "array", "items": _RULE, "description": "one page, by name"},
    },
}


def _describe(name: str) -> dict:
    from ..rules.applicability import RULE_EQUIP_CLASSES
    from ..rules.param_docs import describe, documented_rules, drift_rule_params

    if name in documented_rules():
        d = describe(name)
        kind = "rule"
    else:
        d = {"rule": name, "params": []}
        for rp in drift_rule_params(name):
            p = {"name": rp.name, "default": jsonable(rp.default)}
            if rp.doc is not None:
                p.update(
                    unit=rp.doc.unit,
                    basis=rp.doc.basis,
                    calibrate=rp.doc.calibrate,
                    range=list(jsonable(rp.doc.range)),
                )
                if rp.doc.note:
                    p["note"] = rp.doc.note
            d["params"].append(p)
        kind = "drift"
    out = {
        "rule": name,
        "kind": kind,
        "run_with": "rule_run" if kind == "rule" else "drift",
        "equip_families": sorted(str(f) for f in RULE_EQUIP_CLASSES.get(name, ())),
        # #304: no module paths or issue tags in what a model reads
        "params": [
            {k: plain(v) if isinstance(v, str) else v for k, v in jsonable(p).items()}
            for p in d["params"]
        ],
    }
    if d.get("fixed_in_code"):
        out["fixed_in_code"] = plain(str(d["fixed_in_code"]))
    return out


def _default_text(p: dict) -> str:
    """``name = default unit`` for a fact's text; a parameter with no default reads ``not set``
    (not "None %", #314)."""
    if p["default"] is None:
        return f"{p['name']} not set by default"
    return f"{p['name']} = {p['default']!r}" + (f" {p['unit']}" if p.get("unit") else "")


def rule_params(ctx, arguments: dict) -> CapabilityResult:
    """The named (or matching) rules with their tunable parameters and defaults."""
    named = arguments.get("rules")
    names = sorted(set(named)) if named else list(_names())
    words = re.findall(r"[\w-]+", (arguments.get("query") or "").lower())
    detail = arguments.get("detail")
    detail = bool(named) if detail is None else bool(detail)
    rows = []
    for name in names:
        row = _describe(name)
        hay = " ".join([name, *row["equip_families"], *(p["name"] for p in row["params"])])
        if not all(w in hay.lower() for w in words):
            continue
        if not detail:
            row["params"] = [
                {k: v for k, v in p.items() if k in _FACT_FIELDS} for p in row["params"]
            ]
        rows.append(row)
    if named and not rows:
        raise CapabilityError("not_found", "no named rule matches the query")
    n_params = sum(len(r["params"]) for r in rows)
    matched = len(rows)
    rows = page(
        rows,
        arguments,
        default=_PAGE_DETAIL if detail else _PAGE,
        what="rules",
        order="by name",
        more="or filter with rules or query",
    )
    facts = [
        {
            "id": "F1@rule_params",
            "kind": "rule_count",
            "value": matched,
            "text": f"{matched} rule(s) match, with {n_params} tunable parameter(s) between them",
        }
    ]
    text = [f"{matched} rule(s) match, with {n_params} tunable parameter(s) [F1@rule_params]."]
    for i, r in enumerate(rows[:200], start=2):
        listed = ", ".join(_default_text(p) for p in r["params"])
        facts.append(
            {
                "id": f"F{i}@rule_params",
                "kind": "rule_params",
                "rule": r["rule"],
                "value": len(r["params"]),
                "text": f"{r['rule']} ({r['kind']}, run with {r['run_with']}) defaults: "
                f"{listed or 'no tunable parameters'}",
                "params": [{k: v for k, v in p.items() if k in _FACT_FIELDS} for p in r["params"]],
            }
        )
        if named and i - 1 <= 10:
            text.append(cite(facts[-1]))
    data = {"count": matched, "rules": rows}
    return CapabilityResult(data=data, text=" ".join(text), facts=facts)


CAPABILITIES = (
    Capability(
        name="rule_params",
        title="Rule parameters",
        description=(
            "CAMBER's fault-detection rules and opt-in drift detectors: the equipment families "
            "each is written for and its tunable parameters (default, unit, range); with detail, "
            "where each default comes from and how to calibrate it. Use it to choose rules and "
            "params for rule_run, or to explain a threshold. Filter by rules or query words. "
            "Cheap: no data read."
        ),
        input_schema=INPUT_SCHEMA,
        output_schema=OUTPUT_SCHEMA,
        handler=rule_params,
    ),
)
