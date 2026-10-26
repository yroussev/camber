"""The ``drift`` capability: drift of a facility's equipment against a baseline (0.110, #278).

Two ways to name the baseline, both read-only (:mod:`camber.driftrun`):

- a known-good ``baseline`` window of the same equipment: each family declares it as its
  reference (``{"period": [start, end]}``), fitted in a scratch store on every call and never
  saved;
- otherwise the ``drift`` section of the caller's run config for the facility, scored against
  its frozen baseline store, which is read and never written.

The run goes through :func:`run_config_for` (no-persist, allowlist, licence, trust floor).
"""

from __future__ import annotations

import os

from . import Capability, CapabilityError, CapabilityResult
from ._common import (
    COUNTS,
    DATE,
    EQUIPS,
    FACILITY,
    FINDING_ROW,
    TEXT,
    by_severity,
    caller_config,
    cite,
    finding_fact,
    finding_ids,
    finding_row,
    jsonable,
    narrow,
    run,
    severity_counts,
    store_config,
    trust_banner,
    trust_declines,
)


def _families() -> list:
    from ..driftrun import family_names

    return list(family_names())


#: equipment family (camber.model.equipclass) -> the drift family that scores it by default
_DEFAULT_FAMILY = {
    "air_handler": "ahu",
    "terminal": "vav",
    "chw_plant": "chiller",
    "hw_plant": "boiler",
    "cooling_tower": "tower",
    "pump": "pump",
    "dx": "dx",
}

_PAIR = {
    "type": "array",
    "items": {**DATE, "type": ["string", "null"]},
    "minItems": 2,
    "maxItems": 2,
}

INPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["facility_id"],
    "additionalProperties": False,
    "properties": {
        "facility_id": FACILITY,
        "baseline": {
            **_PAIR,
            "description": "[start, end] of a known-good period to score against; omit to use "
            "the run config's drift section and its frozen baselines",
        },
        "current": {
            **_PAIR,
            "description": "[start, end] to score (default: everything after the baseline, or "
            "the run config's current window); either end may be null",
        },
        "families": {
            "type": "array",
            "minItems": 1,
            "maxItems": 20,
            "description": "which detector family scores which equipment class (default: one "
            "per stored class: AHU -> ahu, VAV -> vav, chiller -> chiller, ...)",
            "items": {
                "type": "object",
                "required": ["equip_class", "family"],
                "additionalProperties": False,
                "properties": {
                    "equip_class": TEXT,
                    "family": {"type": "string", "enum": _families()},
                },
            },
        },
        "equips": EQUIPS,
    },
}

_FAMILY = {
    "type": "object",
    "required": ["equip_class", "family", "label", "baseline", "current", "reference"],
    "additionalProperties": False,
    "properties": {
        "equip_class": {"type": "string"},
        "family": {"type": "string"},
        "label": {"type": "string"},
        "baseline": {"type": "array", "items": {"type": ["string", "null"]}},
        "current": {"type": "array", "items": {"type": ["string", "null"]}},
        "reference": {"type": ["string", "null"], "description": "what was scored against"},
    },
}

_UNEVALUATED = {
    "type": "object",
    "required": ["family", "equip", "reason"],
    "additionalProperties": False,
    "properties": {
        "family": {"type": "string"},
        "equip": {"type": "string"},
        "reason": {"type": "string"},
    },
}

_DIAGNOSIS = {
    "type": "object",
    "required": ["family", "equip", "severity", "summary", "detail"],
    "additionalProperties": False,
    "properties": {
        "family": {"type": "string"},
        "equip": {"type": "string"},
        "severity": {"type": "string"},
        "summary": {"type": "string"},
        "detail": {"type": "object", "description": "the family's scalar verdict fields"},
    },
}

OUTPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": [
        "facility_id",
        "source",
        "families",
        "counts",
        "diagnoses",
        "findings",
        "unevaluated",
    ],
    "additionalProperties": False,
    "properties": {
        "facility_id": {"type": "string"},
        "source": {
            "type": "string",
            "enum": ["baseline_period", "frozen_baselines"],
            "description": "what the drift was measured against",
        },
        "families": {"type": "array", "items": _FAMILY},
        "counts": COUNTS,
        "diagnoses": {"type": "array", "items": _DIAGNOSIS},
        "findings": {"type": "array", "items": FINDING_ROW, "description": "worst first"},
        "unevaluated": {"type": "array", "items": _UNEVALUATED},
    },
}

_LISTED = 10
_FACTS = 200


def _families_for(config: dict, families) -> list:
    """``[(class, family), ...]``: as given, else one default family per configured class."""
    from ..model.equipclass import equip_family

    if families:
        return [(f["equip_class"], f["family"]) for f in families]
    out = []
    for e in config.get("equipment") or []:
        fam = _DEFAULT_FAMILY.get(equip_family(e["class"]) or "")
        if fam:
            out.append((e["class"], fam))
    return out


def _config(ctx, arguments: dict) -> tuple:
    """``(config, base_dir, source)`` for the drift run."""
    fid = arguments["facility_id"]
    base, base_dir = caller_config(ctx, fid)
    baseline, current = arguments.get("baseline"), arguments.get("current")
    if baseline is not None:
        if not baseline[0] or not baseline[1]:
            raise CapabilityError("invalid_params", "baseline needs both a start and an end")
        if base is None:
            base, base_dir = store_config(ctx, fid), os.path.dirname(os.path.abspath(ctx.store))
        config = {k: v for k, v in base.items() if k not in ("rules", "report", "faults", "mv")}
        pairs = _families_for(config, arguments.get("families"))
        if not pairs:
            raise CapabilityError(
                "invalid_params",
                f"no stored equipment class of {fid!r} has a drift family; pass families",
            )
        classes = {e["class"] for e in config.get("equipment") or []}
        config["equipment"] = [*(config.get("equipment") or [])] + [
            {"class": c} for c, _f in pairs if c not in classes
        ]
        drift = {
            "run_id": "capability",
            "families": [
                {"class": c, "family": f, "reference": {"period": list(baseline)}} for c, f in pairs
            ],
        }
        if current is not None:
            drift["current"] = list(current)
        config["drift"] = drift
        source = "baseline_period"
    else:
        if base is None or not base.get("drift"):
            raise CapabilityError(
                "invalid_params",
                "give a baseline window (a known-good period), or configure a drift section for "
                f"{fid!r} in the run config",
            )
        config = {k: v for k, v in base.items() if k not in ("rules", "report", "faults", "mv")}
        drift = dict(config["drift"])
        if arguments.get("families"):
            drift["families"] = [
                {"class": c, "family": f} for c, f in _families_for(config, arguments["families"])
            ]
        if current is not None:
            drift["current"] = list(current)
        config["drift"] = drift
        source = "frozen_baselines"
    config["rules"] = []
    return narrow(config, equips=arguments.get("equips")), base_dir, source


def _reference(fam) -> str | None:
    ref = fam.reference
    if not ref:
        return None
    if ref.get("equip"):
        return f"equipment {ref['equip']}"
    per = ref.get("period") or (None, None)
    return f"period {per[0]} .. {per[1]}"


def drift(ctx, arguments: dict) -> CapabilityResult:
    """Drift findings and per-equipment verdicts for one allowed facility."""
    fid = arguments["facility_id"]
    config, base_dir, source = _config(ctx, arguments)
    res = run(ctx, config, base_dir)
    dr = res.drift
    fams_raw = list(dr.families) if dr is not None else []
    fams, diagnoses, unevaluated, rows = [], [], [], []
    for fam in dr.families if dr is not None else []:
        fams.append(
            {
                "equip_class": str(fam.equip_class),
                "family": str(fam.family),
                "label": str(fam.label),
                "baseline": [None if x is None else str(x) for x in fam.baseline],
                "current": [None if x is None else str(x) for x in fam.current],
                "reference": _reference(fam),
            }
        )
        for d in fam.diagnoses:
            dd = jsonable(d.as_dict()) if hasattr(d, "as_dict") else {}
            diagnoses.append(
                {
                    "family": str(fam.family),
                    "equip": str(dd.get("equip") or getattr(d, "equip", "")),
                    "severity": str(dd.get("severity") or ""),
                    "summary": str(dd.get("summary") or ""),
                    "detail": {
                        k: v
                        for k, v in sorted(dd.items())
                        if isinstance(v, (str, int, float, bool)) or v is None
                    },
                }
            )
        for u in fam.unevaluated:
            u = u if isinstance(u, dict) else {"equip": str(u)}
            unevaluated.append(
                {
                    "family": str(fam.family),
                    "equip": str(u.get("equip", "")),
                    "reason": str(u.get("reason", "")),
                }
            )
        ids = finding_ids(fam.findings)
        rows += [finding_row(f"{fam.family}:{i}", f) for i, f in zip(ids, fam.findings)]
    rows = by_severity(rows)
    diagnoses.sort(key=lambda d: (d["family"], d["equip"]))
    unevaluated.sort(key=lambda u: (u["family"], u["equip"]))
    counts = severity_counts(rows)
    declined = sum(1 for r in rows if "declined" in r["summary"])
    facts = [
        {
            "id": "F1@drift",
            "kind": "drift",
            "facility_id": fid,
            "value": len(rows),
            "text": f"Drift on {fid} ({source.replace('_', ' ')}): {len(fams)} family run(s), "
            f"{counts['fault']} fault and {counts['warn']} warn findings, {declined} "
            f"declined, {len(unevaluated)} equipment not evaluated",
        }
    ]
    declines, floor = trust_declines(f for fam in fams_raw for f in fam.findings)  # 0.111 (#297)
    untrusted = f" ({len(declines)} on untrusted input(s))" if declines else ""
    if declines:
        facts[0]["text"] += f" ({len(declines)} declined on untrusted input(s))"
    text = [
        f"Drift on {fid} gave {counts['fault']} fault and {counts['warn']} warn findings; "
        f"{declined} check(s) declined{untrusted}; {len(unevaluated)} equipment were not "
        "evaluated [F1@drift]."
        if declines
        else f"Drift on {fid} gave {counts['fault']} fault and {counts['warn']} warn findings; "
        f"{len(unevaluated)} equipment were not evaluated [F1@drift]."
    ]
    for i, r in enumerate(rows[:_FACTS], start=2):
        fact_id = f"F{i}@drift"
        r["fact"] = fact_id
        facts.append(finding_fact(fact_id, r))
        if i - 1 <= _LISTED and r["severity"] in ("fault", "warn"):
            text.append(cite(facts[-1]))
    banners = []
    notice = trust_banner(declines, ctx.min_trust if floor is None else floor)
    if notice:
        banners.append(notice)
    if source == "baseline_period":
        banners.append(
            "Drift is measured against the baseline window given, fitted for this call only: it "
            "is a known-good period only if the equipment was healthy then."
        )
    data = {
        "facility_id": fid,
        "source": source,
        "families": fams,
        "counts": counts,
        "diagnoses": diagnoses,
        "findings": rows,
        "unevaluated": unevaluated,
    }
    return CapabilityResult(data=data, text=" ".join(text), facts=facts, banners=banners)


CAPABILITIES = (
    Capability(
        name="drift",
        title="Performance drift",
        description=(
            "Detect slow performance drift (fouling coils, loading filters, a leaking valve, a "
            "chiller losing efficiency) by scoring a current window against a baseline: either "
            "a known-good baseline [start, end] you give (fitted for this call, never saved) or "
            "the frozen baselines of the run config's drift section. Returns per-equipment "
            "verdicts, the drift findings worst first, and the equipment that could not be "
            "evaluated and why (a decline is not a clean bill of health). Families default to "
            "one per stored equipment class. Nothing is saved. Cost: reads both windows and "
            "fits each detector (seconds to minutes). Needs facility_id."
        ),
        input_schema=INPUT_SCHEMA,
        output_schema=OUTPUT_SCHEMA,
        handler=drift,
        long_running=True,
    ),
)
