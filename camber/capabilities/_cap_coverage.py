"""The ``coverage`` capability: how complete a facility's stored trends are (0.110, #278).

Per point: samples, first and last sample, the sampling interval, coverage over the window and
the gaps in it; per equipment, the mean and the worst point. The interval is the median spacing
of the point's own samples (:func:`camber.ingest.quality.infer_freq`); a gap is a stretch longer
than 1.5 intervals with no valid sample, including a late start or an early end in the window.
"""

from __future__ import annotations

import pandas as pd

from . import Capability, CapabilityError, CapabilityResult
from ._common import DATE, EQUIPS, FACILITY, TEXT, window
from ._page import OFFSET, limit_arg, page
from ._policy import open_store
from ._sanitize import name

#: rows per list and page by default (#299)
_PAGE = 50

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
        "group_by": {
            "type": "string",
            "enum": ["point", "equip"],
            "description": "point (default): one row per point and per equipment; equip: the "
            "equipment rows only",
        },
        "min_gap_hours": {
            "type": "number",
            "minimum": 0,
            "maximum": 8760,
            "description": "list only gaps at least this long (default: any gap longer than "
            "1.5 sampling intervals)",
        },
        "limit": limit_arg(_PAGE, "equipment, points, gaps"),
        "offset": OFFSET,
    },
}

_POINT = {
    "type": "object",
    "required": [
        "equip",
        "equip_class",
        "role",
        "n_samples",
        "first",
        "last",
        "interval_minutes",
        "coverage",
        "n_gaps",
        "longest_gap_hours",
    ],
    "additionalProperties": False,
    "properties": {
        "equip": {"type": "string"},
        "equip_class": {"type": "string"},
        "role": {"type": "string"},
        "n_samples": {"type": "integer", "minimum": 0},
        "first": {"type": ["string", "null"]},
        "last": {"type": ["string", "null"]},
        "interval_minutes": {"type": ["number", "null"]},
        "coverage": {"type": "number", "minimum": 0, "maximum": 1},
        "n_gaps": {"type": "integer", "minimum": 0},
        "longest_gap_hours": {"type": "number", "minimum": 0},
    },
}

_EQUIP = {
    "type": "object",
    "required": ["equip", "equip_class", "n_points", "coverage", "worst_role", "worst_coverage"],
    "additionalProperties": False,
    "properties": {
        "equip": {"type": "string"},
        "equip_class": {"type": "string"},
        "n_points": {"type": "integer", "minimum": 0},
        "coverage": {"type": "number", "minimum": 0, "maximum": 1, "description": "mean"},
        "worst_role": {"type": "string"},
        "worst_coverage": {"type": "number", "minimum": 0, "maximum": 1},
    },
}

_GAP = {
    "type": "object",
    "required": ["equip", "role", "start", "end", "hours"],
    "additionalProperties": False,
    "properties": {
        "equip": {"type": "string"},
        "role": {"type": "string"},
        "start": {
            "type": "string",
            "description": "the last sample before the gap (or the window start)",
        },
        "end": {
            "type": "string",
            "description": "the first sample after it (or the window end)",
        },
        "hours": {"type": "number", "minimum": 0},
    },
}

OUTPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["facility_id", "window", "coverage", "n_points", "equipment", "points", "gaps"],
    "additionalProperties": False,
    "properties": {
        "facility_id": {"type": "string"},
        "window": {
            "type": "object",
            "required": ["start", "end"],
            "additionalProperties": False,
            "properties": {
                "start": {"type": ["string", "null"]},
                "end": {"type": ["string", "null"]},
            },
        },
        "coverage": {"type": "number", "minimum": 0, "maximum": 1, "description": "mean"},
        "n_points": {"type": "integer", "minimum": 0},
        "equipment": {"type": "array", "items": _EQUIP, "description": "worst first"},
        "points": {"type": "array", "items": _POINT, "description": "worst first"},
        "gaps": {"type": "array", "items": _GAP, "description": "longest first"},
    },
}

#: equipment named in the summary and given their own fact
_LISTED, _FACTS = 5, 100


def _point(ts: pd.DatetimeIndex, w0, w1) -> tuple:
    """``(row fields, gaps)`` of one point's valid sample times over the window ``w0..w1``."""
    from ..ingest.quality import infer_freq

    n = len(ts)
    if n == 0:
        return {
            "n_samples": 0,
            "first": None,
            "last": None,
            "interval_minutes": None,
            "coverage": 0.0,
        }, ([(w0, w1)] if w0 is not None and w1 is not None else [])
    step = infer_freq(ts) if n > 1 else None
    step = step if step is not None and step > pd.Timedelta(0) else pd.Timedelta(hours=1)
    expected = int((w1 - w0) / step) + 1
    gaps = []
    edges = [w0 - step, *ts, w1 + step]
    for a, b in zip(edges[:-1], edges[1:]):
        if b - a > 1.5 * step:
            gaps.append((a, b))
    return {
        "n_samples": n,
        "first": ts[0].isoformat(),
        "last": ts[-1].isoformat(),
        "interval_minutes": round(step / pd.Timedelta(minutes=1), 3),
        "coverage": round(min(1.0, n / max(expected, 1)), 4),
        "_step": step,
    }, gaps


def coverage(ctx, arguments: dict) -> CapabilityResult:
    """Coverage and gaps per point and per equipment of one allowed facility."""
    fid = arguments["facility_id"]
    start, end = window(arguments)
    store = open_store(ctx)
    if fid not in store.facilities():
        raise CapabilityError("not_found", f"facility {fid!r} has no stored data")
    classes = store.equipment(facility_id=fid).get(fid) or {}
    want_eq, want_roles = arguments.get("equips"), arguments.get("roles")
    keys = sorted(
        (k.equip, k.role)
        for k in store.points(facility_id=fid)
        if (want_eq is None or k.equip in want_eq) and (want_roles is None or k.role in want_roles)
    )
    long = store.read_long(
        facility_id=fid,
        equips=sorted({e for e, _r in keys}) or None,
        roles=sorted({r for _e, r in keys}) or None,
        start=start,
        end=end,
        columns=["ts", "equip", "role", "value"],
    )
    long = long[long["value"].notna()] if not long.empty else long
    ts_all = pd.to_datetime(long["ts"]) if not long.empty else pd.Series(dtype="datetime64[ns]")
    w0 = pd.Timestamp(start) if start else (ts_all.min() if len(ts_all) else None)
    w1 = pd.Timestamp(end) if end else (ts_all.max() if len(ts_all) else None)
    groups: dict = {}
    for (equip, role), g in long.groupby(["equip", "role"], sort=True) if not long.empty else ():
        groups[(equip, role)] = pd.DatetimeIndex(pd.to_datetime(g["ts"])).unique().sort_values()
    floor_h = float(arguments.get("min_gap_hours") or 0.0)
    points, gaps = [], []
    for equip, role in keys:
        ts = groups.get((equip, role), pd.DatetimeIndex([]))
        fields, pgaps = _point(ts, w0, w1)  # w0 / w1 is None only when no point has data
        step = fields.pop("_step", pd.Timedelta(0))
        hours = [((b - a - step) / pd.Timedelta(hours=1), a, b) for a, b in pgaps]
        hours = [(max(h, 0.0), a, b) for h, a, b in hours]
        points.append(
            {
                "equip": equip,
                "equip_class": str(classes.get(equip, "")),
                "role": role,
                **fields,
                "n_gaps": len(hours),
                "longest_gap_hours": round(max((h for h, _a, _b in hours), default=0.0), 2),
            }
        )
        for h, a, b in hours:
            if h >= floor_h:
                gaps.append(
                    {
                        "equip": equip,
                        "role": role,
                        "start": max(a, w0).isoformat(),
                        "end": min(b, w1).isoformat(),
                        "hours": round(h, 2),
                    }
                )
    gaps.sort(key=lambda g: (-g["hours"], g["equip"], g["role"], g["start"]))
    by_eq: dict = {}
    for p in points:
        by_eq.setdefault(p["equip"], []).append(p)
    equipment = []
    for equip, ps in by_eq.items():
        worst = min(ps, key=lambda p: (p["coverage"], p["role"]))
        equipment.append(
            {
                "equip": equip,
                "equip_class": ps[0]["equip_class"],
                "n_points": len(ps),
                "coverage": round(sum(p["coverage"] for p in ps) / len(ps), 4),
                "worst_role": worst["role"],
                "worst_coverage": worst["coverage"],
            }
        )
    equipment.sort(key=lambda e: (e["coverage"], e["equip"]))
    points.sort(key=lambda p: (p["coverage"], p["equip"], p["role"]))
    mean = round(sum(p["coverage"] for p in points) / len(points), 4) if points else 0.0
    low = sum(1 for p in points if p["coverage"] < 0.9)
    win = {
        "start": w0.isoformat() if w0 is not None else None,
        "end": w1.isoformat() if w1 is not None else None,
    }
    span = (
        f" from {win['start'] or 'the first sample'} to {win['end'] or 'the last sample'}"
        if w0 is not None or w1 is not None
        else ""
    )
    facts = [
        {
            "id": "F1@coverage",
            "kind": "coverage",
            "facility_id": fid,
            "value": round(mean * 100, 1),
            "text": f"{fid}: {len(points)} points on {len(equipment)} equipment have a mean "
            f"coverage of {mean * 100:.1f}%{span}; {low} point(s) are below 90% and "
            f"{len(gaps)} gap(s) are listed",
        }
    ]
    text = [
        f"{fid} has a mean coverage of {mean * 100:.1f}% over {len(points)} points, with "
        f"{low} point(s) below 90% [F1@coverage]."
    ]
    by_point = (arguments.get("group_by") or "point") == "point"
    equipment_page = page(
        equipment, arguments, default=_PAGE, what="equipment", order="worst first"
    )
    for i, e in enumerate(equipment_page[:_FACTS], start=2):
        facts.append(
            {
                "id": f"F{i}@coverage",
                "kind": "equip_coverage",
                "equip": e["equip"],
                "value": round(e["coverage"] * 100, 1),
                "text": f"{name(e['equip'])}: mean coverage {e['coverage'] * 100:.1f}% over "
                f"{e['n_points']} points; worst {name(e['worst_role'])} at "
                f"{e['worst_coverage'] * 100:.1f}%",
            }
        )
        if i - 1 <= _LISTED and e["coverage"] < 0.9:
            text.append(
                f"{name(e['equip'])} averages {e['coverage'] * 100:.1f}% "
                f"(worst {name(e['worst_role'])}) "
                f"[F{i}@coverage]."
            )
    data = {
        "facility_id": fid,
        "window": win,
        "coverage": mean,
        "n_points": len(points),
        "equipment": equipment_page,
        "points": (
            page(points, arguments, default=_PAGE, what="points", order="worst first")
            if by_point
            else []
        ),
        "gaps": page(
            gaps,
            arguments,
            default=_PAGE,
            what="gaps",
            order="longest first",
            more="or raise min_gap_hours",
        ),
    }
    banners = []
    if ts_all.empty and keys:
        banners.append(
            "No valid stored sample of these points falls in the requested window, so every "
            "point shows 0% coverage; the facilities capability lists the first and last stored "
            "sample of each facility."
        )
    return CapabilityResult(data=data, text=" ".join(text), facts=facts, banners=banners)


CAPABILITIES = (
    Capability(
        name="coverage",
        title="Data coverage and gaps",
        description=(
            "How complete a facility's stored trends are: per point the samples, first and last "
            "sample, interval, coverage (0-1) and gaps; per equipment the mean and the worst "
            "point; the longest gaps. Worst first. Use it before trusting an analysis. Filter by "
            "equips, roles and start / end; group_by='equip' drops the point rows. Cost: one scan"
            " of the timestamps. Needs facility_id."
        ),
        input_schema=INPUT_SCHEMA,
        output_schema=OUTPUT_SCHEMA,
        handler=coverage,
    ),
)
