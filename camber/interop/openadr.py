"""OpenADR report-schema mapping for demand-response results.

Maps a :class:`camber.geb.DemandResponseResult` to an **OpenADR-3.0-shaped report** payload, so a
CAMBER-measured DR event can be handed to a demand-response program in a shape it recognizes. This
is a **schema-level mapping for interop**, not a VTN/VEN transport implementation — it produces the
report object; wiring it to a client/exchange is the operator's step. Clean-room from the public
OpenADR 3.0 report structure; stdlib only (dict/JSON), no dependency.

Timestamps are caller-supplied (``created=``) — the mapping fabricates nothing.
"""

from __future__ import annotations

import json

_KWH = "KWH"
_KW = "KW"


def _payload(ptype: str, value, unit: str) -> dict:
    return {"type": ptype, "unit": unit, "values": [round(float(value), 3)]}


def to_openadr_report(
    dr,
    *,
    program_id: str = "",
    event_id: str = "",
    client_name: str = "",
    resource_name: str = "aggregate",
    created: str = "",
    report_name: str = "DR_EVENT_PERFORMANCE",
) -> dict:
    """Map a ``DemandResponseResult`` (or its ``as_dict()``) to an OpenADR-3.0-shaped report dict.

    The event's baseline / actual / shed energies become interval report payloads (KWH); the average
    and peak reduction, percent, rebound, and event hours become a performance summary.
    """
    d = dr.as_dict() if hasattr(dr, "as_dict") else dict(dr)
    interval = {
        "id": 0,
        "payloads": [
            _payload("BASELINE", d["baseline_kwh"], _KWH),
            _payload("USAGE", d["actual_kwh"], _KWH),
            _payload("REDUCTION", d["energy_shed_kwh"], _KWH),
        ],
    }
    return {
        "objectType": "REPORT",
        "reportName": report_name,
        "programID": program_id,
        "eventID": event_id,
        "clientName": client_name,
        "createdDateTime": created,
        "resources": [{"resourceName": resource_name, "intervals": [interval]}],
        "payloadDescriptors": [
            {"payloadType": "BASELINE", "units": _KWH},
            {"payloadType": "USAGE", "units": _KWH},
            {"payloadType": "REDUCTION", "units": _KWH},
        ],
        "performanceSummary": {
            "eventHours": d["event_hours"],
            "avgReductionKW": d["avg_shed_kw"],
            "peakReductionKW": d["peak_shed_kw"],
            "pctReduction": d["pct_shed"],
            "reboundKWH": d["rebound_kwh"],
            "reductionUnit": _KW,
        },
    }


def openadr_report_json(dr, **kwargs) -> str:
    """The OpenADR report as a compact JSON string. Accepts the same options as
    :func:`to_openadr_report`."""
    return json.dumps(to_openadr_report(dr, **kwargs), separators=(",", ":"))
