"""The facts and notices of one outcome as one JSON object, for any transport (0.113, #280).

``camber a2a`` attaches :func:`grounding` to every artifact and to every failed, rejected or
cancelled task, as the data part of the ``grounded-facts`` extension (docs/a2a/grounded-facts.md):
a successful result carries its facts and notices, a refusal or failure its error code too, so a
peer reads one shape either way. ``facts`` and ``notices`` are the same as the ``camber`` key of
an MCP tool's ``structuredContent`` (``camber.mcp.RESULT_KEY``, 0.112, #331). Private module.
"""

from __future__ import annotations

import json
from typing import Any


def fact_index(facts: Any) -> dict:
    """``{fact id: fact text}`` of ``facts``, in order; entries without a string id are left out."""
    return {
        f["id"]: str(f.get("text", ""))
        for f in facts or ()
        if isinstance(f, dict) and isinstance(f.get("id"), str) and f["id"]
    }


def error_info(code: str, message: str, data: Any = None) -> dict:
    """An error as data: ``{"code", "message"}`` and ``data`` when it is JSON (else left out)."""
    out: dict = {"code": str(code), "message": str(message)}
    if data is not None:
        try:
            json.dumps(data, allow_nan=False)
        except (TypeError, ValueError):
            return out
        out["data"] = data
    return out


def grounding(facts: Any = (), notices: Any = (), *, error: dict | None = None) -> dict:
    """``{"facts": {id: text}, "notices": [...], "error": {...} | None}`` for one outcome.

    ``facts`` are the outcome's fact records (each a dict with an ``id`` and a ``text``),
    ``notices`` its banners (licence, trust or truncation notices, always shown to the user), and
    ``error`` an :func:`error_info` when the outcome is a refusal or a failure."""
    return {
        "facts": fact_index(facts),
        "notices": [str(n) for n in notices or ()],
        "error": error,
    }
