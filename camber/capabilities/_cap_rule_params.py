"""The ``rule_params`` capability: the rules, what they apply to and their tunable parameters
(0.110, #278), from :mod:`camber.rules.param_docs` -- defaults read from the constructors, with
each parameter's unit, basis, calibration advice and sensible range.
"""

from __future__ import annotations

import re
from functools import lru_cache

from . import Capability, CapabilityError, CapabilityResult
from ._common import jsonable


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
    "required": ["rule", "kind", "equip_families", "params"],
    "additionalProperties": False,
    "properties": {
        "rule": {"type": "string"},
        "kind": {"type": "string", "enum": ["rule", "drift"]},
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
        "count": {"type": "integer", "minimum": 0},
        "rules": {"type": "array", "items": _RULE},
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
        "equip_families": sorted(str(f) for f in RULE_EQUIP_CLASSES.get(name, ())),
        "params": [jsonable(p) for p in d["params"]],
    }
    if d.get("fixed_in_code"):
        out["fixed_in_code"] = str(d["fixed_in_code"])
    return out


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
                {k: v for k, v in p.items() if k in ("name", "default", "unit", "range")}
                for p in row["params"]
            ]
        rows.append(row)
    if named and not rows:
        raise CapabilityError("not_found", "no named rule matches the query")
    n_params = sum(len(r["params"]) for r in rows)
    facts = [
        {
            "id": "F1@rule_params",
            "kind": "rule_count",
            "value": len(rows),
            "text": f"{len(rows)} rule(s) match, with {n_params} tunable parameter(s) between them",
        }
    ]
    text = [f"{len(rows)} rule(s) match, with {n_params} tunable parameter(s) [F1@rule_params]."]
    for i, r in enumerate(rows[:200], start=2):
        listed = ", ".join(
            f"{p['name']} = {p['default']!r}" + (f" {p['unit']}" if p.get("unit") else "")
            for p in r["params"]
        )
        facts.append(
            {
                "id": f"F{i}@rule_params",
                "kind": "rule_params",
                "rule": r["rule"],
                "value": len(r["params"]),
                "text": f"{r['rule']} ({r['kind']}) defaults: {listed or 'no tunable parameters'}",
                "params": r["params"],
            }
        )
        if named and i - 1 <= 10:
            text.append(f"The defaults of {r['rule']} are listed [F{i}@rule_params].")
    data = {"count": len(rows), "rules": rows}
    return CapabilityResult(data=data, text=" ".join(text), facts=facts)


CAPABILITIES = (
    Capability(
        name="rule_params",
        title="Rule parameters",
        description=(
            "List CAMBER's fault-detection rules (and opt-in drift detectors), the equipment "
            "families each is written for, and their tunable parameters with defaults, units "
            "and sensible ranges; with detail, also where each default comes from (a standard, "
            "a public source, judgment or a calibration) and how to calibrate it. Use it to "
            "choose rules and params for rule_run, or to explain a threshold. Filter by rule "
            "names or query words. Cheap (no data read)."
        ),
        input_schema=INPUT_SCHEMA,
        output_schema=OUTPUT_SCHEMA,
        handler=rule_params,
    ),
)
