"""HTML table of per-loop condenser heat-rejection drift verdicts, for dropping into a report.

Renders the roll-ups from :func:`camber.condenserdrift.diagnose_condenser_drift` as a compact,
self-contained ``<table>`` — one row per condenser loop, ranked worst-first — showing the severity,
whether two or more condenser-side signals **corroborate** (tower approach · tube fouling · CW-flow
range · high-side pressure), and the localized causes. A standalone renderer (like the ones
in :mod:`camber.report.linking`): callers splice the returned HTML into a site or fleet report. Pure
string building; no matplotlib, no new dependency.
"""

from __future__ import annotations

import html as _html

_RANK = {"ok": 0, "info": 0, "warn": 1, "fault": 2}


def condenser_diagnosis_table(
    diagnoses, *, title: str = "Condenser heat-rejection drift diagnosis"
) -> str:
    """An HTML table of per-loop condenser drift verdicts, ranked worst-severity first.

    ``diagnoses`` is an iterable of :class:`camber.condenserdrift.CondenserDriftDiagnosis` (or
    anything with ``equip`` / ``severity`` / ``corroborated`` / ``causes``). A ``corroborated``
    verdict is flagged (two or more condenser-side signals drifting together). Returns a heading
    plus the table; empty input returns a short placeholder.
    """
    ds = list(diagnoses)
    if not ds:
        return f"<h2>{_html.escape(title)}</h2><p>No condenser diagnoses.</p>"
    ds = sorted(ds, key=lambda d: -_RANK.get(getattr(d, "severity", "ok"), 0))

    rows = ["<tr><th>Equip</th><th>Severity</th><th>Corroborated</th><th>Causes</th></tr>"]
    for d in ds:
        causes = "; ".join(getattr(d, "causes", []) or []) or "steady"
        corroborated = "yes" if getattr(d, "corroborated", False) else ""
        rows.append(
            "<tr>"
            f"<td>{_html.escape(str(getattr(d, 'equip', '')))}</td>"
            f"<td>{_html.escape(str(getattr(d, 'severity', 'ok')))}</td>"
            f"<td>{corroborated}</td>"
            f"<td>{_html.escape(causes)}</td>"
            "</tr>"
        )
    table = "<table border='1' cellpadding='5' cellspacing='0'>" + "".join(rows) + "</table>"
    return f"<h2>{_html.escape(title)}</h2>" + table
