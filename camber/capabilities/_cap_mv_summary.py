"""The ``mv_summary`` capability: the M&V results of a configured project (0.110, #278).

Runs only the ``mv`` section of the caller's run config for the facility (the same code path
as ``camber mv run``: :func:`camber.config.run_config` reads any frozen baseline versions and
never writes them) through :func:`run_config_for`, so nothing is saved and a billing entry
fetches no weather.
"""

from __future__ import annotations

from . import Capability, CapabilityError, CapabilityResult
from ._common import (
    COUNTS,
    EQUIPS,
    FACILITY,
    FINDING_ROW,
    by_severity,
    caller_config,
    cite,
    finding_fact,
    finding_ids,
    finding_row,
    narrow,
    run,
    severity_counts,
)

INPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["facility_id"],
    "additionalProperties": False,
    "properties": {"facility_id": FACILITY, "equips": EQUIPS},
}

_KINDS = ("mv_baseline", "mv_savings", "mv_trigger", "mv_method_proposal", "other")

OUTPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["facility_id", "entries", "meters", "kinds", "counts", "declined", "findings"],
    "additionalProperties": False,
    "properties": {
        "facility_id": {"type": "string"},
        "entries": {"type": "integer", "minimum": 0, "description": "mv entries configured"},
        "meters": {"type": "array", "items": {"type": "string"}},
        "kinds": {
            "type": "object",
            "required": list(_KINDS),
            "additionalProperties": False,
            "properties": {k: {"type": "integer", "minimum": 0} for k in _KINDS},
        },
        "counts": COUNTS,
        "declined": {"type": "integer", "minimum": 0},
        "findings": {
            "type": "array",
            "items": FINDING_ROW,
            "description": "mv_baseline (model fit and G14 statistics), mv_savings (avoided "
            "energy, savings %, uncertainty), mv_trigger and mv_method_proposal findings",
        },
    },
}

#: config sections an M&V run does not need
_DROP = ("rules", "drift", "report", "faults", "soo", "ventilation", "topology")
_LISTED = 10


def mv_summary(ctx, arguments: dict) -> CapabilityResult:
    """The M&V findings of the caller's configured project for one allowed facility."""
    fid = arguments["facility_id"]
    config, base_dir = caller_config(ctx, fid)
    if config is None:
        raise CapabilityError(
            "invalid_params",
            f"no M&V project is configured for {fid!r}: the caller's run config does not name it",
        )
    if not config.get("mv"):
        raise CapabilityError(
            "invalid_params", f"the run config for {fid!r} has no 'mv' section (no M&V project)"
        )
    config = {k: v for k, v in config.items() if k not in _DROP}
    config["rules"] = []
    config = narrow(config, equips=arguments.get("equips"))
    res = run(ctx, config, base_dir)
    rows = by_severity([finding_row(i, f) for i, f in zip(finding_ids(res.findings), res.findings)])
    kinds = dict.fromkeys(_KINDS, 0)
    for r in rows:
        kinds[r["rule"] if r["rule"] in kinds else "other"] += 1
    declined = sum(1 for r in rows if r["metrics"].get("declined") is True)
    meters = sorted({r["equip"] for r in rows})
    facts = [
        {
            "id": "F1@mv_summary",
            "kind": "mv_summary",
            "facility_id": fid,
            "value": len(rows),
            "text": f"M&V for {fid}: {len(meters)} meter(s), {kinds['mv_baseline']} baseline "
            f"fit(s), {kinds['mv_savings']} savings result(s), {kinds['mv_trigger']} "
            f"trigger(s); {declined} result(s) declined",
        }
    ]
    text = [
        f"M&V for {fid} covers {len(meters)} meter(s) with {kinds['mv_savings']} savings "
        f"result(s), {declined} declined [F1@mv_summary]."
    ]
    for i, r in enumerate(rows, start=2):
        fact_id = f"F{i}@mv_summary"
        r["fact"] = fact_id
        facts.append(finding_fact(fact_id, r))
        if i - 1 <= _LISTED and r["rule"] in ("mv_savings", "mv_trigger"):
            text.append(cite(facts[-1]))
    data = {
        "facility_id": fid,
        "entries": len(config.get("mv") or []),
        "meters": meters,
        "kinds": kinds,
        "counts": severity_counts(rows),
        "declined": declined,
        "findings": rows,
    }
    banners = []
    if declined:
        banners.append(
            f"{declined} M&V result(s) declined to state a figure; read their declined_reason "
            "before quoting any saving."
        )
    return CapabilityResult(data=data, text=" ".join(text), facts=facts, banners=banners)


CAPABILITIES = (
    Capability(
        name="mv_summary",
        title="M&V summary",
        description=(
            "Measurement and verification (IPMVP / ASHRAE Guideline 14) results of the facility's"
            " configured M&V project: per meter the baseline model and its fit (R2, CV(RMSE), "
            "NMBE), avoided energy and savings % with uncertainty, rebaseline triggers, and what "
            "declined and why. Use it to check a savings claim. Needs a run config with an mv "
            "section; nothing is saved or fetched. Cost: seconds. Needs facility_id."
        ),
        input_schema=INPUT_SCHEMA,
        output_schema=OUTPUT_SCHEMA,
        handler=mv_summary,
    ),
)
