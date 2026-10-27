"""The ``sensor_trust`` capability: how far each stored point can be trusted (0.110, #278).

Each equipment's role frame is scored by :func:`camber.sensorhealth.frame_sensor_health`
-- coverage, flatlines, stuck runs, outliers, physical range and the frame-level checks -- read
the way the rule runner gates its inputs (``view="rules"``, the default) or the way the RCx
report's sensor-health table reads them (``view="report"``: duct points judged with the fan on,
plant points while their equipment runs, per-mode outliers). A point below the caller's trust
floor (``ctx.min_trust``, or a higher ``min_trust`` argument) is one a rule run declines on.

0.111 (#311): the frame is the one ``rule_run`` reads for the same facility, equipment and window
(the run config's resample and shared points), and the ``rules`` view is the trust floor's own
read (:func:`camber.sensorhealth._input_health`), so a check is declined exactly when an input is
``below_floor`` here. 0.111 (#313): the run config's sensor references (``report.rcx.references``)
are compared as ``rcx_report`` compares them; a disagreeing one makes its point suspect.
"""

from __future__ import annotations

from . import Capability, CapabilityError, CapabilityResult
from ._common import (
    DATE,
    EQUIPS,
    FACILITY,
    TEXT,
    check_selection,
    check_window,
    facility_config,
    jsonable,
    narrow,
    reference_findings,
    window,
)
from ._page import OFFSET, limit_arg, page
from ._policy import open_store
from ._sanitize import name, plain, prose

#: points per page by default (#299)
_PAGE = 100

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
        "limit": limit_arg(_PAGE, "points"),
        "offset": OFFSET,
    },
}

#: one configured sensor-reference comparison (0.111, #313)
_REFERENCE = {
    "type": "object",
    "required": [
        "equip",
        "role",
        "reference",
        "verdict",
        "bias",
        "rmse",
        "correlation",
        "drift_per_month",
        "n_samples",
        "overlap_start",
        "overlap_end",
        "summary",
    ],
    "additionalProperties": False,
    "properties": {
        "equip": {"type": "string"},
        "role": {"type": "string"},
        "reference": {"type": "string", "description": "the reference's label or equipment"},
        "verdict": {
            "type": "string",
            "enum": ["ok", "warn", "fault", "not_compared"],
            "description": "ok: agrees (a passing comparison); warn / fault: disagrees, so the "
            "point is suspect and findings resting on it are conditional on it; not_compared: "
            "too few paired samples",
        },
        "bias": {
            "type": ["number", "null"],
            "description": "median of the point minus the reference over their paired "
            "samples: positive means the point reads high, negative low",
        },
        "rmse": {"type": ["number", "null"]},
        "correlation": {"type": ["number", "null"]},
        "drift_per_month": {
            "type": ["number", "null"],
            "description": "null when the overlap is too short to measure a rate",
        },
        "n_samples": {"type": "integer", "minimum": 0, "description": "paired samples"},
        "overlap_start": {"type": ["string", "null"]},
        "overlap_end": {"type": ["string", "null"]},
        "summary": {"type": "string"},
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
        "coverage": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": "the share of the point's own span (from its first valid sample to "
            "the window end) on the rule run's time grid (the run config's resample) that holds "
            "a value; for a plant point trended only while its equipment runs, of its running "
            "hours. A late start is the late_start flag, not low coverage; a trend logged in "
            "bursts covers only its bursts. The coverage capability measures over the whole "
            "window instead, so the two can differ for one point",
        },
        "flatline_frac": {"type": "number", "minimum": 0, "maximum": 1},
        "outlier_frac": {"type": "number", "minimum": 0, "maximum": 1},
        "range_violation_frac": {"type": ["number", "null"]},
        "longest_flat_hours": {
            "type": ["number", "null"],
            "description": "the longest run of one value, in hours; null when the point is not "
            "judged by duration (no duration limit for its role, a fan-dependent point read "
            "without a fan gate as the rules view reads it, or a plant point that never ran)",
        },
        "n_stuck_intervals": {"type": "integer", "minimum": 0},
        "checks": {"type": "array", "items": {"type": "string"}, "description": "frame checks"},
        "reference": {
            "type": "object",
            "description": "the point's entry of references, when the run config names one",
        },
    },
}

OUTPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": [
        "facility_id",
        "view",
        "min_trust",
        "counts",
        "n_points",
        "points",
        "references",
    ],
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
        "n_points": {"type": "integer", "minimum": 0, "description": "points before paging"},
        "points": {"type": "array", "items": _ROW, "description": "least trusted first; a page"},
        "references": {
            "type": "array",
            "items": _REFERENCE,
            "description": "every sensor-reference comparison the run config names "
            "(report.rcx.references) for these points, disagreeing ones first; empty when none "
            "is configured",
        },
    },
}

#: points named in the summary, and given their own fact
_LISTED, _FACTS = 8, 200


def _health(frame, view: str) -> dict:
    from ..schedules import fan_on_mask
    from ..sensorhealth import _input_health, frame_sensor_health, plant_gates

    if view == "rules":
        return _input_health(frame)  # 0.111 (#311): the trust floor's own read
    num = frame.select_dtypes(include="number")
    if num.empty:
        return {}
    g, _src = fan_on_mask(frame)
    if g is not None or plant_gates(num):
        return frame_sensor_health(num, gate=g, plant_gate="auto", mode="auto")
    return frame_sensor_health(num, mode="auto")


def _run_frames(ctx, fid: str, equips, start, end):
    """The frames (a run-like namespace, see :func:`~._policy.run_frames_for`) of the rule run
    ``rule_run`` makes for the same facility, equipment and window (0.111, #311), so a point is
    scored on the frame the trust floor gates on."""
    from ._policy import run_frames_for

    config, base_dir, _from, _notice = facility_config(ctx, fid)
    config = narrow(config, equips=equips, start=start, end=end)
    try:
        return run_frames_for(ctx, config, base_dir=base_dir)
    except CapabilityError:
        raise
    except (KeyError, ValueError, TypeError) as e:
        msg = e.args[0] if isinstance(e, KeyError) and e.args else e
        raise CapabilityError("invalid_params", f"the run config was refused: {msg}") from None


#: sort rank of a verdict: least trusted first (a point whose reference disagrees is suspect)
_RANK = {"untrusted": 0, "suspect": 1, "trusted": 2}


def _num(x, nd: int):
    v = jsonable(x)
    return round(float(v), nd) if isinstance(v, (int, float)) else None


def _references(ctx, fid: str, run, equips, roles) -> dict:
    """The configured sensor references of ``fid`` (``report.rcx.references``) compared on the
    run's frames as ``rcx_report`` compares them (0.111, #313): ``{"all": [comparison],
    "by_point": {(equip, role): comparison}, "notes": [why one was not compared]}``. A passing
    comparison is listed too."""
    found, notes = reference_findings(ctx, fid, run)
    out: dict = {"all": [], "by_point": {}, "notes": []}
    for f in found:
        equip, role = str(f.equip), str(f.rule).split(":", 1)[1]
        if (equips is not None and equip not in equips) or (
            roles is not None and role not in roles
        ):
            continue
        m = f.metrics or {}
        sev = str(f.severity)
        ref = {
            "equip": equip,
            "role": role,
            "reference": str(m.get("reference") or ""),
            "verdict": sev if sev in ("ok", "warn", "fault") else "not_compared",
            "bias": _num(m.get("bias"), 3),
            "rmse": _num(m.get("rmse"), 3),
            "correlation": _num(m.get("correlation"), 3),
            "drift_per_month": _num(m.get("drift_per_month"), 3),
            "n_samples": int(m.get("n") or 0),
            "overlap_start": m.get("overlap_start"),
            "overlap_end": m.get("overlap_end"),
            "summary": plain(" ".join(str(f.summary or "").split())),
        }
        out["all"].append(ref)
        out["by_point"][(equip, role)] = ref
    for note in notes:
        if equips is None or any(f"for {e} " in note or f"for {e}:" in note for e in equips):
            out["notes"].append(note)
    out["all"].sort(key=lambda r: (r["verdict"] not in ("fault", "warn"), r["equip"], r["role"]))
    return out


#: what a comparison's verdict says, for its fact
_REF_WORDS = {
    "fault": "disagrees (fault): the point is suspect, and a finding that rests on it is "
    "conditional on this sensor",
    "warn": "disagrees (warn): the point is suspect, and a finding that rests on it is "
    "conditional on this sensor",
    "ok": "agrees within the comparison's thresholds (passed)",
    "not_compared": "was not compared (too few paired samples)",
}


def _reference_fact(fact_id: str, r: dict) -> dict:
    sign = ""
    if r["bias"] is not None:
        b = round(r["bias"], 1)
        reads = "high" if b > 0 else "low" if b < 0 else "neither high nor low"
        sign = (
            f"; bias {b:+.1f} is the median of the point minus the reference, so the point reads "
            f"{reads} against it"
        )
    span = (
        f" from {r['overlap_start']} to {r['overlap_end']}"
        if r["overlap_start"] and r["overlap_end"]
        else ""
    )
    return {
        "id": fact_id,
        "kind": "reference_comparison",
        "equip": r["equip"],
        "role": r["role"],
        "value": r["verdict"],
        "text": f"{name(r['equip'])} {name(r['role'])} against its configured reference "
        f"{name(r['reference'])} {_REF_WORDS[r['verdict']]}: {prose(r['summary'])}{sign}; "
        f"{r['n_samples']} paired samples{span}",
    }


def _reference_sentence(r: dict, fact_id: str) -> str:
    what = {
        "fault": "disagrees with its configured reference, so it is suspect",
        "warn": "disagrees with its configured reference, so it is suspect",
        "ok": "agrees with its configured reference",
        "not_compared": "could not be compared with its configured reference",
    }[r["verdict"]]
    bias = f" (bias {r['bias']:+.1f})" if r["bias"] is not None else ""
    return f"{name(r['equip'])} {name(r['role'])} {what}{bias} [{fact_id}]."


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
    check_selection(store, fid, arguments)  # 0.111 (#296): an unknown id is not_found
    check_window(store, fid, start, end)
    classes = store.equipment(facility_id=fid).get(fid) or {}
    want_eq, want_roles = arguments.get("equips"), arguments.get("roles")
    run = _run_frames(ctx, fid, want_eq, start, end)
    frame_for, resample = run.frame_for, run.resample
    refs = _references(ctx, fid, run, want_eq, want_roles)  # 0.111 (#313)
    rows, read_any = [], False
    for equip in sorted(classes):
        if want_eq is not None and equip not in want_eq:
            continue
        # 0.111 (#311): the frame a rule run reads (the run's resample and window); equipment
        # the run does not discover is read from the store on the same time grid
        frame = frame_for(equip)
        if frame is None:
            frame = store.read_role_frame(
                facility_id=fid, equip=equip, start=start, end=end, resample=resample
            )
        if frame is None or frame.empty:
            continue
        read_any = True
        for role, t in _health(frame, view).items():
            slug = str(jsonable(role))
            if want_roles is not None and slug not in want_roles:
                continue
            trust = round(_frac(t.trust), 4)
            rng = jsonable(t.range_violation_frac)
            flat_h = jsonable(t.longest_flat_hours)
            verdict = t.verdict if t.verdict in ("trusted", "suspect") else "untrusted"
            flags = [str(f) for f in t.flags]
            ref = refs["by_point"].get((equip, slug))
            if ref is not None and ref["verdict"] in ("warn", "fault"):
                # a reference that disagrees makes the point suspect whatever its own trend
                # looks like: an offset has no shape (#313)
                flags.append(f"reference_{ref['verdict']}")
                if verdict == "trusted":
                    verdict = "suspect"
            row = {
                "equip": equip,
                "equip_class": str(classes.get(equip, "")),
                "role": slug,
                "trust": trust,
                "verdict": verdict,
                "below_floor": trust < floor,
                "flags": sorted(flags),
                "coverage": round(_frac(t.coverage), 4),
                "flatline_frac": round(_frac(t.flatline_frac), 4),
                "outlier_frac": round(_frac(t.outlier_frac), 4),
                "range_violation_frac": None if rng is None else round(float(rng), 4),
                "longest_flat_hours": None if flat_h is None else round(float(flat_h), 2),
                "n_stuck_intervals": len(t.stuck_intervals or []),
                "checks": sorted({str(c.get("check", "")) for c in t.frame_checks or []}),
            }
            if ref is not None:
                row["reference"] = ref
            rows.append(row)
    rows.sort(key=lambda r: (_RANK[r["verdict"]], r["trust"], r["equip"], r["role"]))
    counts = {k: sum(1 for r in rows if r["verdict"] == k) for k in ("trusted", "suspect")}
    counts["untrusted"] = sum(1 for r in rows if r["verdict"] == "untrusted")
    counts["below_floor"] = sum(1 for r in rows if r["below_floor"])
    # 0.111 (#320): a selection's count is not the facility's
    points = "matching points" if want_eq or want_roles else "points"
    facts = [
        {
            "id": "F1@sensor_trust",
            "kind": "trust_summary",
            "facility_id": fid,
            "value": counts["below_floor"],
            "text": f"{fid}: {len(rows)} {points} judged ({view} view): {counts['trusted']} "
            f"trusted, {counts['suspect']} suspect, {counts['untrusted']} untrusted; "
            f"{counts['below_floor']} below the trust floor {floor:g}",
            "min_trust": floor,
        }
    ]
    text = [
        f"Of {len(rows)} {points} on {fid}, {counts['below_floor']} are below the trust floor "
        f"{floor:g} [F1@sensor_trust]."
    ]
    shown = page(rows, arguments, default=_PAGE, what="points", order="least trusted first")
    for i, r in enumerate(shown[:_FACTS], start=2):
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
                f"({r['verdict']}; {why}); coverage {r['coverage']:.0%} of its own span",
            }
        )
        if i - 1 <= _LISTED:
            text.append(
                f"{name(r['equip'])} {name(r['role'])} scores {r['trust']:.2f} ({r['verdict']}"
                + ("; its configured reference disagrees" if "reference" in why else "")
                + f") [F{i}@sensor_trust]."
            )
    for j, ref in enumerate(refs["all"], start=1):
        fact_id = f"R{j}@sensor_trust"
        facts.append(_reference_fact(fact_id, ref))
        if j <= _LISTED:
            text.append(_reference_sentence(ref, fact_id))
    banners = list(refs["notes"])
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
        "points": shown,
        "references": refs["all"],
    }
    return CapabilityResult(data=data, text=" ".join(text), facts=facts, banners=banners)


CAPABILITIES = (
    Capability(
        name="sensor_trust",
        title="Sensor trust",
        description=(
            "Score each point of a facility for trust (0-1: trusted / suspect / untrusted) from "
            "coverage, flatlines, stuck runs, outliers, range and cross-point checks, with the "
            "flags behind it. Least trusted first; below_floor marks exactly the inputs rule_run "
            "declines on. Also compares the run config's sensor references (bias, RMSE, samples, "
            "verdict; passing ones too): a disagreeing one makes the point suspect. Use it before "
            "believing a fault. view='report' judges duct points with the fan on. Cost: reads "
            "every trend (narrow with equips, roles or a window). Needs facility_id."
        ),
        input_schema=INPUT_SCHEMA,
        output_schema=OUTPUT_SCHEMA,
        handler=sensor_trust,
    ),
)
