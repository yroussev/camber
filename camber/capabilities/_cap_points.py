"""The ``points`` capability: the stored points of one facility (0.110, #275)."""

from __future__ import annotations

from . import Capability, CapabilityError, CapabilityResult
from ._page import OFFSET, limit_arg, page
from ._policy import open_store
from ._sanitize import name

_TEXT = {"type": "string", "minLength": 1, "maxLength": 200}
#: points per page by default (#299)
_PAGE = 250

INPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["facility_id"],
    "additionalProperties": False,
    "properties": {
        "facility_id": {**_TEXT, "description": "a facility_id from the facilities capability"},
        "equip": {**_TEXT, "description": "only this equipment id (exact match)"},
        "role": {**_TEXT, "description": "only this point role (e.g. supply_air_temp)"},
        "equip_class": {**_TEXT, "description": "only equipment of this class (e.g. AHU)"},
        "limit": limit_arg(_PAGE, "points"),
        "offset": OFFSET,
    },
}

_ROW = {
    "type": "object",
    "required": ["equip", "equip_class", "role"],
    "additionalProperties": False,
    "properties": {
        "equip": {"type": "string"},
        "equip_class": {"type": "string"},
        "role": {"type": "string"},
    },
}

OUTPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["facility_id", "points", "count", "n_equipment"],
    "additionalProperties": False,
    "properties": {
        "facility_id": {"type": "string"},
        "points": {"type": "array", "items": _ROW, "description": "by equipment and role; a page"},
        "count": {"type": "integer", "minimum": 0, "description": "matches before paging"},
        "n_equipment": {"type": "integer", "minimum": 0},
    },
}


def points(ctx, arguments: dict) -> CapabilityResult:
    """The stored points of one allowed facility, optionally filtered by equip, role or class."""
    fid = arguments["facility_id"]
    store = open_store(ctx)
    if fid not in store.facilities():
        raise CapabilityError("not_found", f"facility {fid!r} has no stored data")
    classes = store.equipment(facility_id=fid).get(fid) or {}
    want_eq, want_role = arguments.get("equip"), arguments.get("role")
    want_cls = arguments.get("equip_class")
    rows = sorted(
        (
            {"equip": k.equip, "equip_class": classes.get(k.equip, ""), "role": k.role}
            for k in store.points(facility_id=fid)
            if (want_eq is None or k.equip == want_eq)
            and (want_role is None or k.role == want_role)
            and (want_cls is None or classes.get(k.equip, "") == want_cls)
        ),
        key=lambda r: (r["equip"], r["role"]),
    )
    n_eq = len({r["equip"] for r in rows})
    filt = ", ".join(
        f"{k} {name(v)}"
        for k, v in (("equip", want_eq), ("role", want_role), ("class", want_cls))
        if v is not None
    )
    facts = [
        {
            "id": "F1@points",
            "kind": "point_count",
            "facility_id": fid,
            "value": len(rows),
            "text": f"{fid} stores {len(rows)} points on {n_eq} equipment"
            + (f" matching {filt}" if filt else ""),
        }
    ]
    text = f"{fid} stores {len(rows)} matching points on {n_eq} equipment [F1@points]."
    shown = page(rows, arguments, default=_PAGE, what="points", more="or filter by equip or role")
    data = {"facility_id": fid, "points": shown, "count": len(rows), "n_equipment": n_eq}
    return CapabilityResult(data=data, text=text, facts=facts)


CAPABILITIES = (
    Capability(
        name="points",
        title="Points of a facility",
        description=(
            "List one facility's stored points (trend series): equipment id, equipment class and "
            "role (what the point measures, e.g. supply_air_temp). Filter by equip, role or "
            "equip_class. Use it to see what data a facility has before asking for an analysis. "
            "Needs facility_id."
        ),
        input_schema=INPUT_SCHEMA,
        output_schema=OUTPUT_SCHEMA,
        handler=points,
    ),
)
