"""Prioritized action plan — fuse *what's wrong*, *what it costs*, and *what to do*.

FDD produces findings, `camber.fault_economics` prices them, and `camber.aso` suggests the fix. This
joins the three into one **ranked action plan**: each actionable finding with its estimated annual
dollar impact and its advisory recommendation, ordered worst-dollars-first (severity breaks ties).
It's the operator's punch list — what to fix, why it's worth it, and the suggested correction, all
grounded and read-only. Dependency-light (stdlib).
"""

from __future__ import annotations

from dataclasses import dataclass

from .aso import _SEV_ORDER, recommend
from .fault_economics import cost_findings


@dataclass
class ActionItem:
    """One row of the action plan: a finding + its cost + its recommendation."""

    equip: str
    rule: str
    severity: str
    annual_cost_usd: float
    costed: bool
    recommendation: object = None  # camber.aso.Recommendation | None
    finding: object = None

    def as_dict(self) -> dict:
        d = {
            "equip": self.equip,
            "rule": self.rule,
            "severity": self.severity,
            "annual_cost_usd": self.annual_cost_usd,
            "costed": self.costed,
            "recommendation": self.recommendation.as_dict() if self.recommendation else None,
        }
        return d


def build_action_plan(
    findings,
    *,
    loads=None,
    price=None,
    params: dict | None = None,
    aso_params: dict | None = None,
    min_severity: str = "warn",
    costed_only: bool = False,
) -> list:
    """Rank actionable findings by estimated annual dollars (worst first), each with its ASO
    recommendation. ``params`` tunes the cost models; ``aso_params`` tunes the recommendations;
    ``loads``/``price`` feed `fault_economics`. Severity breaks dollar ties."""
    floor = _SEV_ORDER.get(min_severity, 2)
    costs = cost_findings(findings, loads, price, params=params)
    items = []
    for f, fc in zip(findings, costs):
        sev = getattr(f, "severity", "")
        if _SEV_ORDER.get(sev, 0) < floor:
            continue
        if costed_only and not fc.costed:
            continue
        items.append(
            ActionItem(
                equip=fc.equip,
                rule=fc.rule,
                severity=sev,
                annual_cost_usd=fc.annual_cost_usd,
                costed=fc.costed,
                recommendation=recommend(f, params=aso_params),
                finding=f,
            )
        )
    items.sort(key=lambda a: (-a.annual_cost_usd, -_SEV_ORDER.get(a.severity, 0)))
    return items


def action_plan_rows(items) -> list:
    """Flatten action items to JSON/table-friendly dict rows."""
    rows = []
    for a in items:
        rec = a.recommendation
        rows.append(
            {
                "equip": a.equip,
                "rule": a.rule,
                "severity": a.severity,
                "annual_cost_usd": a.annual_cost_usd if a.costed else None,
                "action": rec.title if rec else "",
                "suggested": rec.suggested if rec else "",
                "standard": rec.standard if rec else "",
            }
        )
    return rows


def action_plan_html(items) -> str:
    """A self-contained HTML table of the action plan (no external assets)."""
    import html as _html

    if not items:
        return "<p>No actionable findings.</p>"
    head = (
        "<tr><th>#</th><th>Severity</th><th>Equip</th><th>Rule</th><th>$/yr</th>"
        "<th>Recommended action</th><th>Target</th><th>Cite</th></tr>"
    )
    rows = [head]
    for i, a in enumerate(items, 1):
        rec = a.recommendation
        cost = f"${a.annual_cost_usd:,.0f}" if a.costed else "&mdash;"
        rows.append(
            f"<tr><td>{i}</td><td>{_html.escape(a.severity)}</td>"
            f"<td>{_html.escape(str(a.equip))}</td><td>{_html.escape(str(a.rule))}</td>"
            f"<td>{cost}</td>"
            f"<td>{_html.escape(rec.title if rec else '')}</td>"
            f"<td>{_html.escape(rec.suggested if rec else '')}</td>"
            f"<td>{_html.escape(rec.standard if rec else '')}</td></tr>"
        )
    return "<table border='1' cellpadding='5' cellspacing='0'>" + "".join(rows) + "</table>"
