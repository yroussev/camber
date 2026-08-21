"""HTML table of per-loop evaporator / chilled-water drift verdicts, for dropping into a report.

Renders the roll-ups from :func:`camber.evaporatordrift.diagnose_evaporator_drift` as a compact,
self-contained ``<table>`` — one row per evaporator / CHW loop, ranked worst-first — showing the
severity, whether two or more evaporator-side signals **corroborate** (evaporator tube fouling ·
superheat feed · suction pressure), and the localized causes. A standalone renderer (like those in
:mod:`camber.report.linking`): callers splice the returned HTML into a site or fleet report. Pure
string building; no matplotlib, no new dependency.
"""

from __future__ import annotations

import html as _html

_RANK = {"ok": 0, "info": 0, "warn": 1, "fault": 2}


def evaporator_diagnosis_table(
    diagnoses, *, title: str = "Evaporator / chilled-water drift diagnosis"
) -> str:
    """An HTML table of per-loop evaporator drift verdicts, ranked worst-severity first.

    ``diagnoses`` is an iterable of :class:`camber.evaporatordrift.EvaporatorDriftDiagnosis` (or
    anything with ``equip`` / ``severity`` / ``corroborated`` / ``causes``). A ``corroborated``
    verdict is flagged (two or more evaporator-side signals drifting together). Returns a heading
    plus the table; empty input returns a short placeholder.
    """
    ds = list(diagnoses)
    if not ds:
        return f"<h2>{_html.escape(title)}</h2><p>No evaporator diagnoses.</p>"
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
