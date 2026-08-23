"""One-shot site report — scorecard + charts + action plan + evidence in one HTML page.

The owner-facing deliverable that answers, top to bottom: *how healthy is this building* (the
scorecard), *what does the data look like* (readiness / carpet / data-quality charts), *what should
we do* (the ranked action plan — finding + $ + recommendation), and *how do we know* (each finding's
evidence chart). It composes the existing report fragments (`scorecard`, `actionplan`, the dashboard
sections and evidence engine) into a single self-contained HTML string — matplotlib inlined, no web
framework, read-only toward the BAS.
"""

from __future__ import annotations

import html as _html

from ..actionplan import action_plan_html, build_action_plan
from ..rules.triage import rank_findings
from ..scorecard import build_scorecard
from .ahu import ahu_diagnosis_table
from .chiller import chiller_diagnosis_table
from .condenser import condenser_diagnosis_table
from .dashboard import (
    _SECTION_TITLES,
    _STYLE,
    _findings_table,
    _rules_map,
    _section_image,
    render_evidence_blocks,
)
from .evaporator import evaporator_diagnosis_table
from .pump import pump_diagnosis_table
from .vav import vav_diagnosis_table

_SC_STYLE = (
    ".sc{font-size:15px;margin:10px 0}.grade{font-size:30px;font-weight:700}"
    ".score{color:#555}.A{color:#2a7}.B{color:#5a5}.C{color:#c90}.D{color:#e70}.F{color:#c33}"
)


def _scorecard_html(sc) -> str:
    rows = ["<tr><th>Category</th><th>Score</th><th>Grade</th><th>Faults</th><th>Warn</th></tr>"]
    for c in sc.categories:
        rows.append(
            f"<tr><td>{_html.escape(c.category)}</td><td>{c.score:.0f}</td>"
            f"<td>{c.grade}</td><td>{c.n_faults}</td><td>{c.n_warnings}</td></tr>"
        )
    return (
        f"<p class='sc'><span class='grade {sc.overall_grade}'>{sc.overall_grade}</span> "
        f"<span class='score'>{sc.overall_score:.0f}/100</span> &middot; "
        f"{sc.n_actionable} actionable finding(s)</p>"
        "<table border='1' cellpadding='5' cellspacing='0'>" + "".join(rows) + "</table>"
    )


def build_site_report(
    df,
    *,
    findings=None,
    diagnoses=None,
    condenser_diagnoses=None,
    evaporator_diagnoses=None,
    pump_diagnoses=None,
    ahu_diagnoses=None,
    vav_diagnoses=None,
    rules=None,
    frames=None,
    loads=None,
    price=None,
    title: str = "CAMBER site report",
    sections=("A", "E", "I"),
    rank_by: str = "cost",
    top_n: int = 20,
    normalize: bool = True,
) -> str:
    """Assemble a self-contained site-report HTML string.

    ``df`` is the wide role/point frame for the charts. ``findings`` drives the scorecard, ranked
    action plan (``loads``/``price`` cost the plan), and — with ``rules`` (and optional
    per-equipment ``frames``) — the pattern-J evidence. ``diagnoses`` (chiller drift roll-ups from
    :func:`camber.chillerdiag.diagnose_chiller_drift`) add a whole-machine verdict table just under
    the scorecard; ``condenser_diagnoses`` (per-loop heat-rejection roll-ups from
    :func:`camber.condenserdrift.diagnose_condenser_drift`) add a condenser-loop table by it;
    ``evaporator_diagnoses`` (from :func:`camber.evaporatordrift.diagnose_evaporator_drift`) add a
    per-loop evaporator / CHW verdict table too; ``pump_diagnoses`` (from
    :func:`camber.pumpdrift.diagnose_pump_drift`) add a per-loop pump
    verdict table alongside it; ``ahu_diagnoses`` (from :func:`camber.ahudrift.diagnose_ahu_drift`)
    add a per-AHU air-side verdict table too; ``vav_diagnoses`` (from
    :func:`camber.vavdrift.diagnose_vav_drift`) add a per-box VAV zone-terminal verdict table as
    well. ``sections`` chooses which dashboard chart sections to include (A/B/E/I).
    """
    style = _STYLE + _SC_STYLE
    parts = [
        f"<!doctype html><html><head><meta charset='utf-8'><style>{style}</style>"
        f"<title>{_html.escape(title)}</title></head><body>",
        f"<h1>{_html.escape(title)}</h1>",
    ]

    if findings:
        parts.append("<h2>Health scorecard</h2>" + _scorecard_html(build_scorecard(findings)))

    if diagnoses:
        parts.append(chiller_diagnosis_table(diagnoses))

    if condenser_diagnoses:
        parts.append(condenser_diagnosis_table(condenser_diagnoses))

    if evaporator_diagnoses:
        parts.append(evaporator_diagnosis_table(evaporator_diagnoses))

    if pump_diagnoses:
        parts.append(pump_diagnosis_table(pump_diagnoses))

    if ahu_diagnoses:
        parts.append(ahu_diagnosis_table(ahu_diagnoses))

    if vav_diagnoses:
        parts.append(vav_diagnosis_table(vav_diagnoses))

    for letter in sections:
        img = _section_image(
            letter, df, spans=None, carpet_col=None, multitrend_cols=None, normalize=normalize
        )
        parts.append(
            f"<h2>{letter}. {_SECTION_TITLES.get(letter, letter)}</h2>"
            f"<img src='{img}' alt='{_SECTION_TITLES.get(letter, letter)}'>"
        )

    if findings:
        key = "annual_cost_usd" if rank_by == "cost" else None
        ranked = rank_findings(findings, magnitude_key=key, actionable_only=True)[:top_n]
        parts.append("<h2>Findings</h2>" + _findings_table(ranked))
        items = build_action_plan(findings, loads=loads, price=price)
        if items:
            ranked_by = "$/yr" if any(getattr(i, "costed", False) for i in items) else "severity"
            parts.append(
                f"<h2>Recommended actions (ranked by {ranked_by})</h2>" + action_plan_html(items)
            )
        if rules is not None:
            frame_for = frames.get if frames else (lambda _equip: df)
            imgs = render_evidence_blocks(ranked, _rules_map(rules), frame_for)
            if imgs:
                parts.append("<h2>Evidence</h2>" + imgs)

    parts.append("</body></html>")
    return "\n".join(parts)
