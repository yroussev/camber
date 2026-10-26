"""The ``facilities`` capability: the facilities this caller may read (0.110, #275)."""

from __future__ import annotations

from ..store.parquet_store import StoreReadError
from . import Capability, CapabilityResult
from ._page import OFFSET, limit_arg, page
from ._policy import open_store
from ._sanitize import name

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
        "unavailable": {
            "type": "object",
            "required": ["retryable"],
            "additionalProperties": False,
            "properties": {"retryable": {"type": "boolean"}},
            "description": "present when the facility's stored data could not be read for this "
            "listing (start and end are then null); retryable: a retry will likely succeed",
        },
    },
}

#: facilities per page by default (#299)
_PAGE = 100

INPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {"limit": limit_arg(_PAGE, "facilities"), "offset": OFFSET},
    "additionalProperties": False,
}

OUTPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["facilities", "count", "withheld_research_only", "withheld_unverified"],
    "additionalProperties": False,
    "properties": {
        "facilities": {"type": "array", "items": _ROW, "description": "by id; a page"},
        "count": {"type": "integer", "minimum": 0, "description": "facilities before paging"},
        "withheld_research_only": {"type": "integer", "minimum": 0},
        "withheld_unverified": {
            "type": "integer",
            "minimum": 0,
            "description": "allowed facilities whose licence or lifecycle cannot be established",
        },
    },
}

#: per-facility sentences in the summary; the rest are in ``data``
_LISTED = 10


def _state(state: str) -> str:
    """A lifecycle state for the summary: as it is when it is one of the states, else quoted."""
    from ..portfolio._states import STATES

    return state if state in STATES else name(state)


def _span(store, fid: str) -> tuple:
    """The first and last stored sample of ``fid`` (ISO), from file statistics (#293)."""
    start, end = store._ts_span(fid)
    if start is None:
        return None, None
    return start.isoformat(), end.isoformat()


def facilities(ctx, arguments: dict) -> CapabilityResult:
    """The allowed facilities in the store, with name, state, date range and point counts.

    A facility is listed only when :func:`camber._servable.refusal` clears it (0.111, #290): the
    registry read here is the fail-closed one, so a damaged registry lists nothing.
    """
    from .._provenance import facility_timezone
    from .._servable import read_registry, refusal

    store = open_store(ctx)
    registry = read_registry(store.root)
    meta = registry[0]
    known = set(store.facilities()) | set(meta)
    rows, withheld, unverified = [], 0, 0
    for fid in sorted(f for f in ctx.allowed_facilities if f in known):
        m = meta.get(fid) or {}
        why = refusal(fid, store.root, registry)
        if why is not None:
            if why[0] == "research_only":
                withheld += 1
            else:
                unverified += 1
            continue
        # this facility's catalog entry and file statistics only, never its rows (#293); one
        # facility's damaged file must not hide the others (#301)
        broken, n_points, n_equipment, start, end = None, 0, 0, None, None
        try:
            n_points = len(store.points(facility_id=fid))
            n_equipment = len(store.equipment(facility_id=fid).get(fid) or {})
            start, end = _span(store, fid)
        except StoreReadError as e:
            start, end, broken = None, None, {"retryable": bool(e.retryable)}
        rows.append(
            {
                "facility_id": fid,
                "name": str(m.get("display_name") or m.get("name") or fid),
                "state": str(m.get("state") or "active"),
                "timezone": facility_timezone(m),
                "start": start,
                "end": end,
                "n_points": n_points,
                "n_equipment": n_equipment,
                **({"unavailable": broken} if broken else {}),
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
    shown = page(rows, arguments, default=_PAGE, what="facilities", order="by id")
    for i, r in enumerate(shown, start=2):
        if r.get("unavailable"):
            span = "stored data that cannot be read right now"
        else:
            span = f"data from {r['start']} to {r['end']}" if r["start"] else "no stored data"
        facts.append(
            {
                "id": f"F{i}@facilities",
                "kind": "facility",
                "facility_id": r["facility_id"],
                "value": r["n_points"],
                "text": f"{r['facility_id']} ({name(r['name'])}): {_state(r['state'])}, "
                f"{r['n_points']} points on {r['n_equipment']} equipment, {span}",
            }
        )
        if i - 1 <= _LISTED:
            text.append(
                f"{r['facility_id']} is {_state(r['state'])} with {r['n_points']} points "
                f"[F{i}@facilities]."
            )
    banners = []
    unreadable = [r["facility_id"] for r in rows if r.get("unavailable")]
    if unreadable:
        banners.append(
            f"The stored data of {', '.join(unreadable)} cannot be read right now (a damaged "
            "file or a concurrent ingest); calls on it return the error code unavailable."
        )
    if withheld:
        banners.append(
            f"{withheld} allowed facility(ies) hold research-only (non-commercial / "
            "no-derivatives) data and are not listed: such data is never served here."
        )
    if unverified:
        banners.append(
            f"{unverified} allowed facility(ies) are not listed because their licence or "
            "lifecycle cannot be established from the store's facility registry; they are not "
            "served until it is repaired."
        )
    data = {
        "facilities": shown,
        "count": len(rows),
        "withheld_research_only": withheld,
        "withheld_unverified": unverified,
    }
    return CapabilityResult(data=data, text=" ".join(text), facts=facts, banners=banners)


CAPABILITIES = (
    Capability(
        name="facilities",
        title="Facilities",
        description=(
            "List the facilities (buildings) this caller may read: id, name, lifecycle state, "
            "time zone, first and last stored sample, and the point and equipment counts. Call it"
            " first: it gives the facility_id values the other tools accept."
        ),
        input_schema=INPUT_SCHEMA,
        output_schema=OUTPUT_SCHEMA,
        handler=facilities,
    ),
)
