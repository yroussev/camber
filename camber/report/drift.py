"""HTML report for a whole drift run — every family's verdicts, plus what could not be evaluated.

The per-family tables (:mod:`camber.report.ahu`, :mod:`camber.report.chiller`, and their four
siblings) each render one family's roll-ups. This composes them into one page for a
:class:`camber.driftrun.DriftResult`, and adds the two things a single-family table cannot carry:

* the **threshold-confidence banner** — the magnitude floors are screening-grade and the CUSUM
  timing parameters are untuned (:mod:`camber.driftthresholds`). It is rendered *above* the first
  table and there is deliberately no flag to suppress it: a drift page without it invites a reader
  to dispatch on a screening-grade number.
* an **"Equipment not evaluated"** table. Equipment whose required roles never resolved produce no
  Findings and are therefore never diagnosed (see :mod:`camber.driftrun`); listing them keeps the
  page from reading as a clean bill of health for a machine nobody tested.

Pure string building by default — ``html.escape``, matching the standalone renderer convention of
the per-family tables. ``charts=True`` additionally embeds each finding's **pattern-J evidence**:
the current period scattered on its frozen baseline's band, which is the comparison the detector
actually made. That path imports matplotlib lazily, so the default page still needs nothing.

Input is duck-typed, so any object with the same shape renders.
"""

from __future__ import annotations

import html as _html

from ..driftthresholds import MAGNITUDE_NOTE, TEMPORAL_NOTE
from .ahu import ahu_diagnosis_table
from .chiller import chiller_diagnosis_table
from .condenser import condenser_diagnosis_table
from .evaporator import evaporator_diagnosis_table
from .pump import pump_diagnosis_table
from .vav import vav_diagnosis_table

__all__ = ["drift_report_html", "threshold_confidence_html"]

# family -> the table renderer(s) it needs. The chiller roll-up nests both side diagnoses, so it
# renders the whole-machine verdict and then each side.
_TABLES = {
    "ahu": (("", ahu_diagnosis_table),),
    "chiller": (
        ("", chiller_diagnosis_table),
        ("condenser", condenser_diagnosis_table),
        ("evaporator", evaporator_diagnosis_table),
    ),
    "condenser": (("", condenser_diagnosis_table),),
    "evaporator": (("", evaporator_diagnosis_table),),
    "pump": (("", pump_diagnosis_table),),
    "vav": (("", vav_diagnosis_table),),
}


def threshold_confidence_html() -> str:
    """The two-line banner stating what a drift severity is, and is not, evidence of."""
    return (
        "<p><strong>How to read these severities.</strong><br>"
        f"{_html.escape(MAGNITUDE_NOTE)}.<br>"
        f"{_html.escape(TEMPORAL_NOTE)}.</p>"
    )


def _unevaluated_table(rows) -> str:
    """Equipment no detector in the family could evaluate; empty string when there are none."""
    rows = list(rows)
    if not rows:
        return ""
    out = ["<tr><th>Equip</th><th>Family</th><th>Roles the family needed</th></tr>"]
    for r in rows:
        out.append(
            "<tr>"
            f"<td>{_html.escape(str(r.get('equip', '')))}</td>"
            f"<td>{_html.escape(str(r.get('family', '')))}</td>"
            f"<td>{_html.escape(', '.join(r.get('roles_required', []) or []))}</td>"
            "</tr>"
        )
    return (
        "<h2>Equipment not evaluated</h2>"
        "<p>No detector in the family could run on these — their required points did not resolve. "
        "They were <em>not</em> diagnosed, and their absence from the tables above is not a "
        "verdict of steady.</p>"
        "<table border='1' cellpadding='5' cellspacing='0'>" + "".join(out) + "</table>"
    )


def _evidence_figures(fam) -> str:
    """Embed the family's pattern-J evidence charts; empty string when there are none."""
    evidence = dict(getattr(fam, "evidence", {}) or {})
    if not evidence:
        return ""

    import matplotlib

    matplotlib.use("Agg")  # a report is never rendered interactively
    import matplotlib.pyplot as plt

    from ..charts.evidence import render_evidence
    from .dashboard import fig_to_base64

    blocks = []
    for (equip, rule_name), ev in sorted(evidence.items()):
        fig = None
        try:
            fig, ax = plt.subplots(figsize=(7, 4))
            render_evidence(ev, getattr(ev, "frame", None), ax=ax)
            img = fig_to_base64(fig)  # closes fig on success
        except Exception:  # noqa: BLE001 - one unrenderable chart must not lose the whole report
            if fig is not None:
                plt.close(fig)
            continue
        caption = _html.escape(f"{equip} — {rule_name}")
        blocks.append(
            f"<figure><img src='{img}' alt='{caption}'><figcaption>{caption}</figcaption></figure>"
        )
    if not blocks:
        return ""
    return "<h3>Evidence — the current period on each frozen baseline</h3>" + "".join(blocks)


def drift_report_html(
    result, *, title: str = "CAMBER drift report", standalone: bool = True, charts: bool = False
) -> str:
    """Render a whole :class:`camber.driftrun.DriftResult` as HTML.

    ``standalone`` wraps the body in a minimal ``<html>`` document; pass ``False`` to splice the
    body into a larger report (as the config-driven run's audit HTML does). An empty result renders
    an explicit placeholder rather than a blank page — and the threshold banner is emitted either
    way.

    ``charts`` embeds each finding's evidence chart (needs ``run_drift(..., evidence=True)``, whose
    Evidence objects the result carries); without it the page stays dependency-free text and tables.
    """
    fams = list(getattr(result, "families", []) or [])
    site = str(getattr(result, "site", "") or "")
    run_id = str(getattr(result, "run_id", "") or "")

    head = [f"<h1>{_html.escape(title)}</h1>"]
    meta = " · ".join(x for x in (site, f"run {run_id}" if run_id else "") if x)
    if meta:
        head.append(f"<p>{_html.escape(meta)}</p>")
    head.append(threshold_confidence_html())

    body: list = []
    unevaluated: list = []
    for fam in fams:
        name = str(getattr(fam, "family", ""))
        label = str(getattr(fam, "label", name))
        cls = str(getattr(fam, "equip_class", ""))
        base, cur = getattr(fam, "baseline", ("", "")), getattr(fam, "current", ("", ""))
        diagnoses = list(getattr(fam, "diagnoses", []) or [])

        window = f"baseline {tuple(base)} vs current {tuple(cur)}"
        body.append(f"<h2>{_html.escape(label)} — {_html.escape(cls)}</h2>")
        body.append(f"<p>{_html.escape(window)}</p>")

        for side, render in _TABLES.get(name, (("", None),)):
            if render is None:
                continue
            if side == "condenser":
                rows = [d.condenser for d in diagnoses if getattr(d, "condenser", None) is not None]
            elif side == "evaporator":
                rows = [
                    d.evaporator for d in diagnoses if getattr(d, "evaporator", None) is not None
                ]
            else:
                rows = diagnoses
            body.append(render(rows))

        plant = getattr(fam, "plant", None)
        if plant is not None:
            body.append(
                "<p><strong>Plant roll-up</strong> — "
                f"{_html.escape(str(getattr(plant, 'summary', '')))}"
                + (
                    f" <em>{_html.escape(str(plant.recommendation))}</em>"
                    if getattr(plant, "recommendation", "")
                    else ""
                )
                + "</p>"
            )

        if charts:
            body.append(_evidence_figures(fam))

        for row in getattr(fam, "unevaluated", []) or []:
            unevaluated.append({**row, "family": name})

    if not fams:
        body.append("<p>No drift families were run.</p>")
    body.append(_unevaluated_table(unevaluated))

    html = "\n".join(head + body)
    if not standalone:
        return html
    return (
        "<html><head><meta charset='utf-8'>"
        f"<title>{_html.escape(title)}</title></head><body>\n{html}\n</body></html>\n"
    )
