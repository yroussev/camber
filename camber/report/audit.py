"""Audit-deliverable wrapper (ASHRAE/ACCA Standard 211 framing).

Std 211 defines commercial energy audit Levels 1/2/3 and the report content each
requires. Our analytics (FDD findings, change-point M&V, comfort) already produce
the substance; this module *packages* that substance into the Std-211 deliverable
shape -- benchmarking, an energy-conservation-measure (ECM) table, and a structured
report object that renders to text or HTML.

This is a packaging layer: no new analytics. It cites the Std-211 structure
(§5.2.3 benchmarking, §5.4 Level 2 ECM tables / end-use, §5.5 Level 3) for framing;
no standard text is reproduced.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
import html as _html


@dataclass
class Benchmark:
    """EUI benchmark vs a peer median (Std 211 §5.2.3 / §6.1.3)."""
    site_eui: float                 # kBtu/ft2/yr
    peer_median_eui: float
    metric_name: str = "ENERGY STAR property-type median"

    @property
    def pct_over(self) -> float:
        """Percent the site EUI exceeds the peer median (NaN if no valid median)."""
        if self.peer_median_eui <= 0:
            return float("nan")
        return round(100.0 * (self.site_eui - self.peer_median_eui) / self.peer_median_eui, 1)


@dataclass
class ECM:
    """One energy-conservation measure row (Std 211 §5.4 ECM table)."""
    name: str
    finding: str                    # the evidence (a diagnostic/M&V result)
    affected_system: str
    comfort_iaq_impact: str = ""
    est_savings: str = ""           # band, e.g. "low/medium/high" or a number+unit
    est_cost: str = ""
    priority: str = "medium"        # low | medium | high

    def as_dict(self):
        """Return the ECM row as a plain dict."""
        return asdict(self)


@dataclass
class AuditReport:
    """A Std-211-shaped audit report assembled from analytics outputs."""
    building: str
    level: int                      # 1, 2, or 3
    climate_zone: str = ""
    benchmark: Benchmark | None = None
    ecms: list = field(default_factory=list)
    end_use_notes: list = field(default_factory=list)
    comfort_notes: list = field(default_factory=list)
    caveats: list = field(default_factory=list)
    findings: list = field(default_factory=list)         # raw FDD Finding objects
    finding_magnitude_key: str | None = None             # metric to rank ties by

    def add_ecm(self, ecm: ECM):
        """Append an ECM row to the report; return self for chaining."""
        self.ecms.append(ecm)
        return self

    def add_findings(self, findings, *, magnitude_key: str | None = None):
        """Attach FDD findings; they are impact-ranked when the report renders."""
        self.findings.extend(findings)
        if magnitude_key:
            self.finding_magnitude_key = magnitude_key
        return self

    def ranked_findings(self):
        """Actionable findings, worst-first (impact prioritization)."""
        from ..rules.triage import rank_findings
        return rank_findings(self.findings,
                             magnitude_key=self.finding_magnitude_key,
                             actionable_only=True)

    # ECMs sorted high->medium->low for presentation
    def ranked_ecms(self):
        """ECMs sorted high -> medium -> low priority for presentation."""
        order = {"high": 0, "medium": 1, "low": 2}
        return sorted(self.ecms, key=lambda e: order.get(e.priority, 1))

    def to_text(self) -> str:
        """Render the audit report as plain text."""
        L = [f"ASHRAE Std-211 Level {self.level} Audit -- {self.building}"]
        if self.climate_zone:
            L.append(f"Climate zone: {self.climate_zone}")
        if self.benchmark:
            b = self.benchmark
            L.append(f"\nBenchmark: site EUI {b.site_eui} kBtu/ft2/yr vs "
                     f"{b.peer_median_eui} ({b.metric_name}) = {b.pct_over:+.0f}%")
        if self.end_use_notes:
            L.append("\nEnd-use / system notes:")
            L += [f"  - {n}" for n in self.end_use_notes]
        if self.comfort_notes:
            L.append("\nComfort (Std 55):")
            L += [f"  - {n}" for n in self.comfort_notes]
        rf = self.ranked_findings() if self.findings else []
        if rf:
            L.append(f"\nPrioritized FDD findings ({len(rf)}, worst first):")
            for r in rf:
                eq = getattr(r.finding, "equip", "")
                rule = getattr(r.finding, "rule", "")
                L.append(f"  {r.rank}. [{r.severity.upper()}] {rule} @ {eq}")
                summ = getattr(r.finding, "summary", "") or ""
                if summ:
                    L.append(f"     {summ}")
        L.append(f"\nEnergy Conservation Measures ({len(self.ecms)}):")
        for i, e in enumerate(self.ranked_ecms(), 1):
            L.append(f"  {i}. [{e.priority.upper()}] {e.name} ({e.affected_system})")
            L.append(f"     finding: {e.finding}")
            if e.comfort_iaq_impact:
                L.append(f"     comfort/IAQ: {e.comfort_iaq_impact}")
            if e.est_savings or e.est_cost:
                L.append(f"     savings: {e.est_savings or 'TBD'}   cost: {e.est_cost or 'TBD'}")
        if self.caveats:
            L.append("\nCaveats:")
            L += [f"  - {c}" for c in self.caveats]
        return "\n".join(L)

    def to_html(self) -> str:
        """Render the audit report as an HTML fragment."""
        e = _html.escape
        parts = [f"<h1>ASHRAE Std-211 Level {self.level} Audit &mdash; {e(self.building)}</h1>"]
        if self.climate_zone:
            parts.append(f"<p><b>Climate zone:</b> {e(self.climate_zone)}</p>")
        if self.benchmark:
            b = self.benchmark
            parts.append(f"<p><b>Benchmark:</b> site EUI {b.site_eui} kBtu/ft&sup2;/yr "
                         f"vs {b.peer_median_eui} ({e(b.metric_name)}) = "
                         f"<b>{b.pct_over:+.0f}%</b></p>")
        if self.end_use_notes:
            parts.append("<h2>End-use / system notes</h2><ul>"
                         + "".join(f"<li>{e(n)}</li>" for n in self.end_use_notes) + "</ul>")
        if self.comfort_notes:
            parts.append("<h2>Comfort (Std 55)</h2><ul>"
                         + "".join(f"<li>{e(n)}</li>" for n in self.comfort_notes) + "</ul>")
        rf = self.ranked_findings() if self.findings else []
        if rf:
            parts.append("<h2>Prioritized FDD findings</h2>")
            parts.append("<table border='1' cellpadding='4'><tr><th>#</th>"
                         "<th>Severity</th><th>Rule</th><th>Equipment</th>"
                         "<th>Summary</th></tr>")
            for r in rf:
                eq = e(str(getattr(r.finding, "equip", "")))
                rule = e(str(getattr(r.finding, "rule", "")))
                summ = e(str(getattr(r.finding, "summary", "") or ""))
                parts.append(f"<tr><td>{r.rank}</td><td>{e(r.severity)}</td>"
                             f"<td>{rule}</td><td>{eq}</td><td>{summ}</td></tr>")
            parts.append("</table>")
        parts.append("<h2>Energy Conservation Measures</h2>")
        parts.append("<table border='1' cellpadding='4'><tr><th>#</th><th>Priority</th>"
                     "<th>Measure</th><th>System</th><th>Finding</th>"
                     "<th>Comfort/IAQ</th><th>Savings</th><th>Cost</th></tr>")
        for i, m in enumerate(self.ranked_ecms(), 1):
            parts.append(
                f"<tr><td>{i}</td><td>{e(m.priority)}</td><td>{e(m.name)}</td>"
                f"<td>{e(m.affected_system)}</td><td>{e(m.finding)}</td>"
                f"<td>{e(m.comfort_iaq_impact)}</td><td>{e(m.est_savings)}</td>"
                f"<td>{e(m.est_cost)}</td></tr>")
        parts.append("</table>")
        if self.caveats:
            parts.append("<h2>Caveats</h2><ul>"
                         + "".join(f"<li>{e(c)}</li>" for c in self.caveats) + "</ul>")
        return "\n".join(parts)
