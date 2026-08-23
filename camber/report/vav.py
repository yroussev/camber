"""HTML table of per-box VAV drift verdicts, for dropping into a report.

Renders the roll-ups from :func:`camber.vavdrift.diagnose_vav_drift` as a compact, self-contained
``<table>`` — one row per VAV box, ranked worst-first — showing the severity, the ``locus`` (airflow
vs reheat vs upstream), a box-wide flag, and the localized causes. A standalone renderer (like the
ones in :mod:`camber.report.linking`): callers splice the returned HTML into a site or fleet report.
Pure string building; no matplotlib, no new dependency.
"""

from __future__ import annotations

import html as _html

_RANK = {"ok": 0, "info": 0, "warn": 1, "fault": 2}


def vav_diagnosis_table(diagnoses, *, title: str = "VAV zone-terminal drift diagnosis") -> str:
    """An HTML table of per-box VAV drift verdicts, ranked worst-severity first.

    ``diagnoses`` is an iterable of :class:`camber.vavdrift.VavDriftDiagnosis` (or anything with
    ``equip`` / ``severity`` / ``locus`` / ``box_wide`` / ``causes``). A ``box_wide`` verdict is
    flagged (more than one box subsystem drifting). Returns a heading plus the table; empty input
    returns a short placeholder.
    """
    ds = list(diagnoses)
    if not ds:
        return f"<h2>{_html.escape(title)}</h2><p>No VAV diagnoses.</p>"
    ds = sorted(ds, key=lambda d: -_RANK.get(getattr(d, "severity", "ok"), 0))

    rows = [
        "<tr><th>Equip</th><th>Severity</th><th>Locus</th><th>Box-wide</th><th>Causes</th></tr>"
    ]
    for d in ds:
        causes = "; ".join(getattr(d, "causes", []) or []) or "steady"
        box_wide = "yes" if getattr(d, "box_wide", False) else ""
        rows.append(
            "<tr>"
            f"<td>{_html.escape(str(getattr(d, 'equip', '')))}</td>"
            f"<td>{_html.escape(str(getattr(d, 'severity', 'ok')))}</td>"
            f"<td>{_html.escape(str(getattr(d, 'locus', '')))}</td>"
            f"<td>{box_wide}</td>"
            f"<td>{_html.escape(causes)}</td>"
            "</tr>"
        )
    table = "<table border='1' cellpadding='5' cellspacing='0'>" + "".join(rows) + "</table>"
    return f"<h2>{_html.escape(title)}</h2>" + table
