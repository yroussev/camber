"""The ``sensor_trust`` capability: how far each stored point can be trusted (0.110, #278).

Each equipment's stored role frame is scored by :func:`camber.sensorhealth.frame_sensor_health`
-- coverage, flatlines, stuck runs, outliers, physical range and the frame-level checks -- read
the way the rule runner gates its inputs (``view="rules"``, the default) or the way the RCx
report's sensor-health table reads them (``view="report"``: duct points judged with the fan on,
plant points while their equipment runs, per-mode outliers). A point below the caller's trust
floor (``ctx.min_trust``, or a higher ``min_trust`` argument) is one a rule run declines on.
"""

from __future__ import annotations

from . import Capability, CapabilityError, CapabilityResult
from ._common import DATE, EQUIPS, FACILITY, TEXT, jsonable, window
from ._policy import open_store
from ._sanitize import name

INPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["facility_id"],
    "additionalProperties": False,
    "properties": {
        "facility_id": FACILITY,
        "equips": EQUIPS,
        "roles": {
            "type": "array",
            "items": TEXT,
            "minItems": 1,
            "maxItems": 100,
            "description": "only these point roles (e.g. supply_air_temp)",
        },
        "start": DATE,
        "end": DATE,
        "view": {
            "type": "string",
            "enum": ["rules", "report"],
            "description": "rules (default): the read the rule runner gates on; report: the RCx "
            "report's operating-state-aware read",
        },
        "min_trust": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": "flag points below this trust; never lower than the caller's floor",
        },
    },
}

_ROW = {
    "type": "object",
    "required": [
        "equip",
        "equip_class",
        "role",
        "trust",
        "verdict",
        "below_floor",
        "flags",
        "coverage",
        "flatline_frac",
        "outlier_frac",
        "range_violation_frac",
        "longest_flat_hours",
        "n_stuck_intervals",
        "checks",
    ],
    "additionalProperties": False,
    "properties": {
        "equip": {"type": "string"},
        "equip_class": {"type": "string"},
        "role": {"type": "string"},
        "trust": {"type": "number", "minimum": 0, "maximum": 1},
        "verdict": {"type": "string", "enum": ["trusted", "suspect", "untrusted"]},
        "below_floor": {"type": "boolean"},
        "flags": {"type": "array", "items": {"type": "string"}},
        "coverage": {"type": "number", "minimum": 0, "maximum": 1},
        "flatline_frac": {"type": "number", "minimum": 0, "maximum": 1},
        "outlier_frac": {"type": "number", "minimum": 0, "maximum": 1},
        "range_violation_frac": {"type": ["number", "null"]},
        "longest_flat_hours": {"type": ["number", "null"]},
        "n_stuck_intervals": {"type": "integer", "minimum": 0},
        "checks": {"type": "array", "items": {"type": "string"}, "description": "frame checks"},
    },
}

OUTPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["facility_id", "view", "min_trust", "counts", "n_points", "points"],
    "additionalProperties": False,
    "properties": {
        "facility_id": {"type": "string"},
        "view": {"type": "string", "enum": ["rules", "report"]},
        "min_trust": {"type": "number", "minimum": 0, "maximum": 1},
        "counts": {
            "type": "object",
            "required": ["trusted", "suspect", "untrusted", "below_floor"],
            "additionalProperties": False,
            "properties": {
                k: {"type": "integer", "minimum": 0}
                for k in ("trusted", "suspect", "untrusted", "below_floor")
            },
        },
        "n_points": {"type": "integer", "minimum": 0},
        "points": {"type": "array", "items": _ROW, "description": "least trusted first"},
    },
}

#: points named in the summary, and given their own fact
_LISTED, _FACTS = 8, 200


def _health(frame, view: str) -> dict:
    from ..schedules import fan_on_mask
    from ..sensorhealth import frame_sensor_health, plant_gates

    num = frame.select_dtypes(include="number")
    if num.empty:
        return {}
    if view == "rules":
        return frame_sensor_health(num, plant_gate="auto")
    g, _src = fan_on_mask(frame)
    if g is not None or plant_gates(num):
        return frame_sensor_health(num, gate=g, plant_gate="auto", mode="auto")
    return frame_sensor_health(num, mode="auto")


def _frac(x) -> float:
    v = jsonable(x)
    return min(max(float(v), 0.0), 1.0) if isinstance(v, (int, float)) else 0.0


def sensor_trust(ctx, arguments: dict) -> CapabilityResult:
    """Trust scores per point of one allowed facility, least trusted first."""
    fid = arguments["facility_id"]
    start, end = window(arguments)
    view = arguments.get("view") or "rules"
    floor = max(float(ctx.min_trust), float(arguments.get("min_trust") or 0.0))
    store = open_store(ctx)
    if fid not in store.facilities():
        raise CapabilityError("not_found", f"facility {fid!r} has no stored data")
    classes = store.equipment(facility_id=fid).get(fid) or {}
    want_eq, want_roles = arguments.get("equips"), arguments.get("roles")
    rows, read_any = [], False
    for equip in sorted(classes):
        if want_eq is not None and equip not in want_eq:
            continue
        frame = store.read_role_frame(facility_id=fid, equip=equip, start=start, end=end)
        if frame.empty:
            continue
        read_any = True
        for role, t in _health(frame, view).items():
            slug = str(jsonable(role))
            if want_roles is not None and slug not in want_roles:
                continue
            trust = round(_frac(t.trust), 4)
            rng = jsonable(t.range_violation_frac)
            flat_h = jsonable(t.longest_flat_hours)
            rows.append(
                {
                    "equip": equip,
                    "equip_class": str(classes.get(equip, "")),
                    "role": slug,
                    "trust": trust,
                    "verdict": t.verdict if t.verdict in ("trusted", "suspect") else "untrusted",
                    "below_floor": trust < floor,
                    "flags": sorted(str(f) for f in t.flags),
                    "coverage": round(_frac(t.coverage), 4),
                    "flatline_frac": round(_frac(t.flatline_frac), 4),
                    "outlier_frac": round(_frac(t.outlier_frac), 4),
                    "range_violation_frac": None if rng is None else round(float(rng), 4),
                    "longest_flat_hours": None if flat_h is None else round(float(flat_h), 2),
                    "n_stuck_intervals": len(t.stuck_intervals or []),
                    "checks": sorted({str(c.get("check", "")) for c in t.frame_checks or []}),
                }
            )
    rows.sort(key=lambda r: (r["trust"], r["equip"], r["role"]))
    counts = {k: sum(1 for r in rows if r["verdict"] == k) for k in ("trusted", "suspect")}
    counts["untrusted"] = sum(1 for r in rows if r["verdict"] == "untrusted")
    counts["below_floor"] = sum(1 for r in rows if r["below_floor"])
    facts = [
        {
            "id": "F1@sensor_trust",
            "kind": "trust_summary",
            "facility_id": fid,
            "value": counts["below_floor"],
            "text": f"{fid}: {len(rows)} points judged ({view} view): {counts['trusted']} "
            f"trusted, {counts['suspect']} suspect, {counts['untrusted']} untrusted; "
            f"{counts['below_floor']} below the trust floor {floor:g}",
            "min_trust": floor,
        }
    ]
    text = [
        f"Of {len(rows)} points on {fid}, {counts['below_floor']} are below the trust floor "
        f"{floor:g} [F1@sensor_trust]."
    ]
    for i, r in enumerate(rows[:_FACTS], start=2):
        if r["verdict"] == "trusted" and not r["below_floor"]:
            break
        why = ", ".join(r["flags"]) or "no single flag"
        facts.append(
            {
                "id": f"F{i}@sensor_trust",
                "kind": "point_trust",
                "equip": r["equip"],
                "role": r["role"],
                "value": r["trust"],
                "text": f"{name(r['equip'])} {name(r['role'])}: trust {r['trust']:.2f} "
                f"({r['verdict']}; {why}); coverage {r['coverage']:.0%}",
            }
        )
        if i - 1 <= _LISTED:
            text.append(
                f"{name(r['equip'])} {name(r['role'])} scores {r['trust']:.2f} ({r['verdict']}) "
                f"[F{i}@sensor_trust]."
            )
    banners = []
    if not read_any and (start or end):
        banners.append(
            "No stored sample of these equipment falls in the requested window, so no point was "
            "judged; the facilities capability lists the first and last stored sample of each "
            "facility."
        )
    if counts["below_floor"]:
        banners.append(
            f"{counts['below_floor']} point(s) score below the trust floor {floor:g}: a rule that "
            "needs one of them declines rather than report a fault that may be a sensor problem."
        )
    data = {
        "facility_id": fid,
        "view": view,
        "min_trust": floor,
        "counts": counts,
        "n_points": len(rows),
        "points": rows,
    }
    return CapabilityResult(data=data, text=" ".join(text), facts=facts, banners=banners)


CAPABILITIES = (
    Capability(
        name="sensor_trust",
        title="Sensor trust",
        description=(
            "Score how far each stored point of a facility can be trusted (0-1, with a verdict "
            "trusted / suspect / untrusted) from coverage, flatlines and stuck runs, outliers, "
            "physical range and cross-point checks, with the flags that explain the score. "
            "Least trusted first; below_floor marks points under the caller's trust floor, the "
            "inputs a rule run declines on. Use it before believing a fault, or to list sensors "
            "to calibrate. view='report' reads duct points with the fan on and plant points "
            "while running, as the RCx report does. Cost: reads every trend of the facility "
            "(narrow with equips, roles or a window). Needs facility_id."
        ),
        input_schema=INPUT_SCHEMA,
        output_schema=OUTPUT_SCHEMA,
        handler=sensor_trust,
    ),
)
