"""Render the catalog's data issues as the "Data issues and how CAMBER handles them" docs section.

``docs/DATASETS.md`` carries the rendered section between :data:`BEGIN` and :data:`END` markers;
``scripts/datasets_issues_doc.py`` rewrites it from ``catalog.json`` and the test-suite asserts the
two match, so the documentation can never drift from what the ingester actually does.
"""

from __future__ import annotations

BEGIN = (
    "<!-- BEGIN data-issues: generated from camber/datasets/catalog.json by "
    "scripts/datasets_issues_doc.py; do not edit by hand -->"
)
END = "<!-- END data-issues -->"

_HANDLING = {
    "fix": "corrected at ingest (skipped by `--no-corrections`)",
    "annotate": "left as published and recorded in the provenance",
    "exclude": "kept out of scoring / analysis",
    "none": "described only",
}

__all__ = ["BEGIN", "END", "render_markdown", "splice_markdown"]


def _code(items) -> str:
    return ", ".join(f"`{c}`" for c in items)


def _issue_md(iss: dict) -> list:
    doc = iss.get("contradicts") or {}
    lines = [
        f"#### {iss['title']}",
        "",
        f"- **Issue:** `{iss['id']}`",
        f"- **Columns:** {_code(iss.get('columns') or [])}",
    ]
    if iss.get("runs"):
        lines.append(f"- **Runs:** {_code(iss['runs'])}")
    lines += [
        f"- **Evidence:** {iss['evidence']}",
        f"- **Contradicts:** {doc.get('document', '')} ({doc.get('citation', '')})",
        f"- **Handling: {iss['handling']}** -- {_HANDLING.get(iss['handling'], '')}. "
        f"{iss['handling_note']}",
        "",
    ]
    return lines


def render_markdown(entries) -> str:
    """The generated section (markers included) for ``entries`` (catalog order)."""
    out = [
        BEGIN,
        "",
        "The catalog links each dataset exactly as its publisher provides it. Every problem",
        "CAMBER knows of in the *published* data is described below with its evidence, the",
        "publisher documentation it contradicts, and how CAMBER handles it: **fix** (corrected",
        "at ingest by a declared quirk; `camber datasets ingest --no-corrections` ingests the",
        "published data as-is), **annotate** (left in place and recorded in the facility's",
        "provenance), **exclude** (kept out of scoring or of an analysis) or **none** (described",
        "only). Nothing is corrected silently. `camber datasets info <id>` prints the same list.",
        "",
    ]
    for e in entries:
        issues = list(getattr(e, "data_issues", ()) or ())
        out += [f"### `{e.id}`: {e.title}", ""]
        if getattr(e, "synthetic", False) and not issues:  # 0.103 (#133)
            out += [
                "Synthetic: CAMBER generates this dataset (see "
                "[synthetic datasets](#synthetic-datasets)), so there is no published data to "
                "have issues.",
                "",
            ]
        elif not issues:
            out += ["No published-data issues are recorded for this dataset.", ""]
            continue
        for iss in issues:
            out += _issue_md(iss)
    out.append(END)
    return "\n".join(out) + "\n"


def splice_markdown(text: str, section: str) -> str:
    """``text`` with the block between the markers replaced by ``section``.

    Raises ``ValueError`` when the markers are missing or out of order.
    """
    i, j = text.find(BEGIN), text.find(END)
    if i < 0 or j < i:
        raise ValueError("the data-issues markers are missing from the document")
    rest = text[j + len(END) :].lstrip("\n")
    return text[:i] + section + ("\n" + rest if rest else "")
