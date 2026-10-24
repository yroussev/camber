"""The ``facilities`` capability: the facilities this caller may read (0.110, #275)."""

from __future__ import annotations

import pandas as pd

from . import Capability, CapabilityResult
from ._policy import open_store, research_only

_ROW = {
    "type": "object",
    "required": ["facility_id", "name", "state", "start", "end", "n_points", "n_equipment"],
    "additionalProperties": False,
    "properties": {
        "facility_id": {"type": "string"},
        "name": {"type": "string"},
        "state": {"type": "string"},
        "timezone": {"type": ["string", "null"]},
        "start": {"type": ["string", "null"], "description": "first stored sample (ISO)"},
        "end": {"type": ["string", "null"], "description": "last stored sample (ISO)"},
        "n_points": {"type": "integer", "minimum": 0},
        "n_equipment": {"type": "integer", "minimum": 0},
    },
}

INPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

OUTPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["facilities", "count", "withheld_research_only"],
    "additionalProperties": False,
    "properties": {
        "facilities": {"type": "array", "items": _ROW},
        "count": {"type": "integer", "minimum": 0},
        "withheld_research_only": {"type": "integer", "minimum": 0},
    },
}

#: per-facility sentences in the summary; the rest are in ``data``
_LISTED = 10


def _span(store, fid: str) -> tuple:
    ts = store.read_long(facility_id=fid, columns=["ts"])
    if ts.empty:
        return None, None
    return pd.Timestamp(ts["ts"].min()).isoformat(), pd.Timestamp(ts["ts"].max()).isoformat()


def facilities(ctx, arguments: dict) -> CapabilityResult:
    """The allowed facilities in the store, with name, state, date range and point counts."""
    from .._provenance import facility_timezone

    store = open_store(ctx)
    meta = store.facilities_meta()
    known = set(store.facilities()) | set(meta)
    points = store.points()
    equipment = store.equipment()
    rows, withheld = [], 0
    for fid in sorted(f for f in ctx.allowed_facilities if f in known):
        m = meta.get(fid) or {}
        if research_only(m):
            withheld += 1
            continue
        start, end = _span(store, fid)
        rows.append(
            {
                "facility_id": fid,
                "name": str(m.get("display_name") or m.get("name") or fid),
                "state": str(m.get("state") or "active"),
                "timezone": facility_timezone(m),
                "start": start,
                "end": end,
                "n_points": sum(1 for k in points if k.facility_id == fid),
                "n_equipment": len(equipment.get(fid) or {}),
            }
        )
    facts = [
        {
            "id": "F1@facilities",
            "kind": "facility_count",
            "value": len(rows),
            "text": f"{len(rows)} facilities are visible to this caller",
        }
    ]
    text = [f"{len(rows)} facilities are visible [F1@facilities]."]
    for i, r in enumerate(rows, start=2):
        span = f"data from {r['start']} to {r['end']}" if r["start"] else "no stored data"
        facts.append(
            {
                "id": f"F{i}@facilities",
                "kind": "facility",
                "facility_id": r["facility_id"],
                "value": r["n_points"],
                "text": f"{r['facility_id']} ({r['name']}): {r['state']}, {r['n_points']} points "
                f"on {r['n_equipment']} equipment, {span}",
            }
        )
        if i - 1 <= _LISTED:
            text.append(
                f"{r['facility_id']} is {r['state']} with {r['n_points']} points [F{i}@facilities]."
            )
    banners = []
    if withheld:
        banners.append(
            f"{withheld} allowed facility(ies) hold research-only (non-commercial / "
            "no-derivatives) data and are not listed: such data is never served here."
        )
    data = {"facilities": rows, "count": len(rows), "withheld_research_only": withheld}
    return CapabilityResult(data=data, text=" ".join(text), facts=facts, banners=banners)


CAPABILITIES = (
    Capability(
        name="facilities",
        title="Facilities",
        description=(
            "List the facilities (buildings) this caller may read: each one's id, name, "
            "lifecycle state, time zone, the first and last stored sample, and how many points "
            "and equipment are stored. Call it first to learn which facility_id values the "
            "other capabilities accept. Takes no arguments."
        ),
        input_schema=INPUT_SCHEMA,
        output_schema=OUTPUT_SCHEMA,
        handler=facilities,
    ),
)
