"""HTML table of per-AHU air-side drift verdicts, for dropping into a report.

Renders the roll-ups from :func:`camber.ahudrift.diagnose_ahu_drift` as a compact, self-contained
``<table>`` — one row per AHU, ranked worst-first — showing the severity, the ``locus`` (fan vs
air-path vs coil), an AHU-wide flag, and the localized causes. A standalone renderer (like the ones
in :mod:`camber.report.linking`): callers splice the returned HTML into a site or fleet report. Pure
string building; no matplotlib, no new dependency.
"""

from __future__ import annotations

import html as _html

_RANK = {"ok": 0, "info": 0, "warn": 1, "fault": 2}


def ahu_diagnosis_table(diagnoses, *, title: str = "AHU air-side drift diagnosis") -> str:
    """An HTML table of per-AHU air-side drift verdicts, ranked worst-severity first.

    ``diagnoses`` is an iterable of :class:`camber.ahudrift.AhuDriftDiagnosis` (or anything with
    ``equip`` / ``severity`` / ``locus`` / ``ahu_wide`` / ``causes``). An ``ahu_wide`` verdict is
    flagged (more than one AHU subsystem drifting). Returns a heading plus the table; empty input
    returns a short placeholder.
    """
    ds = list(diagnoses)
    if not ds:
        return f"<h2>{_html.escape(title)}</h2><p>No AHU diagnoses.</p>"
    ds = sorted(ds, key=lambda d: -_RANK.get(getattr(d, "severity", "ok"), 0))

    rows = [
        "<tr><th>Equip</th><th>Severity</th><th>Locus</th><th>AHU-wide</th><th>Causes</th></tr>"
    ]
    for d in ds:
        causes = "; ".join(getattr(d, "causes", []) or []) or "steady"
        ahu_wide = "yes" if getattr(d, "ahu_wide", False) else ""
        rows.append(
            "<tr>"
            f"<td>{_html.escape(str(getattr(d, 'equip', '')))}</td>"
            f"<td>{_html.escape(str(getattr(d, 'severity', 'ok')))}</td>"
            f"<td>{_html.escape(str(getattr(d, 'locus', '')))}</td>"
            f"<td>{ahu_wide}</td>"
            f"<td>{_html.escape(causes)}</td>"
            "</tr>"
        )
    table = "<table border='1' cellpadding='5' cellspacing='0'>" + "".join(rows) + "</table>"
    return f"<h2>{_html.escape(title)}</h2>" + table
