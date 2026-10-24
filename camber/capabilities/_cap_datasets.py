"""The ``dataset_search`` and ``dataset_entry`` capabilities: the built-in open-dataset catalog,
metadata only (0.110, #278).

Catalog metadata is public, so research-only entries (a non-commercial or no-derivatives
licence, or an open-licence dataset held back for a stated reason) are listed too -- each with
its licence and access fields and a notice that this server refuses their data. Nothing is
fetched or ingested here; that stays ``camber datasets``, run by a person.
"""

from __future__ import annotations

import re

from . import Capability, CapabilityError, CapabilityResult

_KINDS = ("simulated", "real", "lab", "synthetic")

#: the notice a research-only entry carries
REFUSED = (
    "Research-only dataset: its licence or terms forbid commercial use or derivatives, so this "
    "server refuses any facility ingested from it. Use it only under its licence, outside this "
    "assistant."
)

SEARCH_INPUT = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "query": {
            "type": "string",
            "minLength": 1,
            "maxLength": 200,
            "description": "words to match in the id, title, summary, equipment and what the "
            "dataset teaches (all words must match; case-insensitive)",
        },
        "kind": {"type": "string", "enum": list(_KINDS)},
        "labeled": {"type": "boolean", "description": "only datasets with fault labels"},
        "licence": {
            "type": "string",
            "enum": ["all", "commercial"],
            "description": "commercial: only datasets usable commercially (default all)",
        },
    },
}

_ROW: dict = {
    "type": "object",
    "required": [
        "id",
        "title",
        "kind",
        "licence",
        "access",
        "research_only",
        "commercial_ok",
        "labeled_faults",
        "equipment",
        "download_bytes",
        "store_bytes_estimate",
        "good_for",
        "notice",
    ],
    "additionalProperties": False,
    "properties": {
        "id": {"type": "string"},
        "title": {"type": "string"},
        "kind": {"type": "string"},
        "licence": {"type": "string", "description": "SPDX id"},
        "access": {"type": "string", "enum": ["open", "research_only"]},
        "research_only": {"type": "boolean"},
        "commercial_ok": {"type": "boolean"},
        "labeled_faults": {"type": "boolean"},
        "equipment": {"type": "string"},
        "download_bytes": {"type": "integer", "minimum": 0, "description": "0: generated locally"},
        "store_bytes_estimate": {"type": ["integer", "null"]},
        "good_for": {"type": "array", "items": {"type": "string"}},
        "notice": {"type": ["string", "null"]},
    },
}

SEARCH_OUTPUT = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["count", "research_only", "datasets"],
    "additionalProperties": False,
    "properties": {
        "count": {"type": "integer", "minimum": 0},
        "research_only": {"type": "integer", "minimum": 0},
        "datasets": {"type": "array", "items": _ROW},
    },
}


def _size(e) -> int:
    return int(sum(int(f.get("size") or 0) for f in e.subset_files()))


def _row(e) -> dict:
    return {
        "id": e.id,
        "title": e.title,
        "kind": e.kind,
        "licence": e.licence,
        "access": "research_only" if e.research_only else "open",
        "research_only": bool(e.research_only),
        "commercial_ok": bool(e.commercial_ok and not e.research_only),
        "labeled_faults": bool(e.labeled_faults),
        "equipment": str(e.equipment or ""),
        "download_bytes": _size(e),
        "store_bytes_estimate": e.store_bytes_estimate,
        "good_for": [str(t) for t in e.teaches],
        "notice": REFUSED if e.research_only else None,
    }


def _haystack(e) -> str:
    parts = [e.id, e.title, e.summary, e.equipment, e.publisher, e.kind, *e.teaches]
    return " ".join(str(p) for p in parts).lower()


def dataset_search(ctx, arguments: dict) -> CapabilityResult:
    """Catalog entries matching the query and filters, sorted by id."""
    from .. import datasets

    words = re.findall(r"[\w-]+", (arguments.get("query") or "").lower())
    labeled = arguments.get("labeled")
    entries = datasets.catalog(
        licence=arguments.get("licence") or "all",
        kind=arguments.get("kind"),
        labeled=True if labeled else None,
    )
    hits = sorted((e for e in entries if all(w in _haystack(e) for w in words)), key=lambda e: e.id)
    rows = [_row(e) for e in hits]
    n_ro = sum(1 for r in rows if r["research_only"])
    facts = [
        {
            "id": "F1@dataset_search",
            "kind": "dataset_count",
            "value": len(rows),
            "text": f"{len(rows)} catalog dataset(s) match, {n_ro} of them research-only",
        }
    ]
    text = [f"{len(rows)} catalog dataset(s) match, {n_ro} research-only [F1@dataset_search]."]
    for i, r in enumerate(rows, start=2):
        fact_id = f"F{i}@dataset_search"
        facts.append(
            {
                "id": fact_id,
                "kind": "dataset",
                "dataset_id": r["id"],
                "value": r["licence"],
                "text": f"{r['id']} ({r['title']}): {r['kind']}, licence {r['licence']}, "
                f"access {r['access']}",
            }
        )
        if i - 1 <= 10:
            text.append(f"{r['id']} is {r['access']} under {r['licence']} [{fact_id}].")
    banners = (
        [REFUSED.replace("Research-only dataset", f"{n_ro} research-only dataset(s)")]
        if n_ro
        else []
    )
    data = {"count": len(rows), "research_only": n_ro, "datasets": rows}
    return CapabilityResult(data=data, text=" ".join(text), facts=facts, banners=banners)


ENTRY_INPUT = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["dataset_id"],
    "additionalProperties": False,
    "properties": {
        "dataset_id": {
            "type": "string",
            "pattern": r"^[a-z0-9][a-z0-9-]{0,99}$",
            "description": "a dataset id from dataset_search",
        }
    },
}

_ISSUE = {
    "type": "object",
    "required": ["id", "title", "handling"],
    "additionalProperties": False,
    "properties": {
        "id": {"type": "string"},
        "title": {"type": "string"},
        "handling": {"type": "string"},
        "note": {"type": "string"},
    },
}

ENTRY_OUTPUT = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": [
        *_ROW["required"],
        "summary",
        "publisher",
        "citation",
        "landing_url",
        "dois",
        "access_reason",
        "attribution_required",
        "timezone",
        "verified_on",
        "subsets",
        "suggested_rules",
        "known_issues",
        "data_issues",
    ],
    "additionalProperties": False,
    "properties": {
        **_ROW["properties"],
        "summary": {"type": "string"},
        "publisher": {"type": "string"},
        "citation": {"type": "string"},
        "landing_url": {"type": "string"},
        "dois": {"type": "array", "items": {"type": "string"}},
        "access_reason": {"type": "string"},
        "attribution_required": {"type": "boolean"},
        "timezone": {"type": "string"},
        "verified_on": {"type": "string"},
        "subsets": {"type": "array", "items": {"type": "string"}},
        "suggested_rules": {"type": "array", "items": {"type": "string"}},
        "known_issues": {"type": "array", "items": {"type": "string"}},
        "data_issues": {"type": "array", "items": _ISSUE},
    },
}


def dataset_entry(ctx, arguments: dict) -> CapabilityResult:
    """One catalog entry's metadata."""
    from .. import datasets

    did = arguments["dataset_id"]
    try:
        e = datasets.get(did)
    except KeyError:
        raise CapabilityError(
            "not_found", f"no catalog dataset {did!r} (see dataset_search)"
        ) from None
    rules = (e.suggested_analyses or {}).get("rules") or []
    issues = []
    for iss in e.data_issues:
        row = {
            "id": str(iss.get("id", "")),
            "title": str(iss.get("title", "")),
            "handling": str(iss.get("handling", "")),
        }
        if iss.get("handling_note"):
            row["note"] = str(iss["handling_note"])
        issues.append(row)
    data = {
        **_row(e),
        "summary": e.summary,
        "publisher": e.publisher,
        "citation": e.citation,
        "landing_url": e.landing_url,
        "dois": [str(d) for d in e.dois],
        "access_reason": str(e.access_reason or ""),
        "attribution_required": bool(e.attribution_required),
        "timezone": str(e.timezone or ""),
        "verified_on": str(e.verified_on),
        "subsets": sorted(e.subsets),
        "suggested_rules": sorted(str(r) for r in rules if isinstance(r, str)),
        "known_issues": [str(k) for k in e.known_issues],
        "data_issues": issues,
    }
    facts = [
        {
            "id": "F1@dataset_entry",
            "kind": "dataset",
            "dataset_id": e.id,
            "value": e.licence,
            "text": f"{e.id} ({e.title}) is a {e.kind} dataset from {e.publisher} under "
            f"{e.licence}, access {data['access']}",
        },
        {
            "id": "F2@dataset_entry",
            "kind": "dataset_size",
            "dataset_id": e.id,
            "value": data["download_bytes"],
            "text": f"{e.id}: default subset download {data['download_bytes']:,} bytes"
            + ("" if data["download_bytes"] else " (generated locally)"),
        },
    ]
    text = (
        f"{e.id} is a {e.kind} dataset under {e.licence}, access {data['access']} "
        "[F1@dataset_entry]."
    )
    banners = [REFUSED] if e.research_only else []
    return CapabilityResult(data=data, text=text, facts=facts, banners=banners)


CAPABILITIES = (
    Capability(
        name="dataset_search",
        title="Search the dataset catalog",
        description=(
            "Search CAMBER's catalog of open building datasets (fault-labelled HVAC "
            "simulations, real building trends, lab rigs, CAMBER's own synthetic sets) by words "
            "and filters. Returns metadata only: id, title, kind, SPDX licence, access (open or "
            "research_only), whether commercial use is allowed, fault labels, equipment, "
            "download size and what each dataset is good for. Research-only entries are listed "
            "with a notice: this server refuses their data. Use it to suggest data to practise "
            "or validate a rule on. Cheap (reads the packaged catalog)."
        ),
        input_schema=SEARCH_INPUT,
        output_schema=SEARCH_OUTPUT,
        handler=dataset_search,
    ),
    Capability(
        name="dataset_entry",
        title="Dataset catalog entry",
        description=(
            "One catalog dataset's full metadata: summary, publisher, citation, DOIs, landing "
            "page, licence and access (with the reason a dataset is held research-only), "
            "subsets, download size, the rules it suits, known issues and the publisher data "
            "issues CAMBER corrects at ingest. Metadata only; nothing is downloaded. Cheap. "
            "Needs dataset_id (see dataset_search)."
        ),
        input_schema=ENTRY_INPUT,
        output_schema=ENTRY_OUTPUT,
        handler=dataset_entry,
    ),
)
