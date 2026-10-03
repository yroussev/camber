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

import html as _html
from dataclasses import asdict, dataclass, field

# Banner carried by every report built from research-only (NC / ND licensed) data. The catalog lets
# a learner analyse such data after an explicit acknowledgement; the licence still forbids
# commercial use and redistribution, so the report says so where nobody can miss it.
RESEARCH_ONLY_BANNER = (
    "NON-COMMERCIAL / RESEARCH USE ONLY: this report is built from data whose licence forbids "
    "commercial use and redistribution. Do not sell it, and do not redistribute it or the "
    "underlying data."
)
SHARE_ALIKE_NOTE = (
    "Share-alike licence: analysing the data is fine (including commercially), but a "
    "redistributed adaptation of the dataset must carry the same licence."
)


def _research_only_banner(srcs: list) -> str:
    """The banner for ``srcs`` ('' when none is research-only).

    An NC / ND licence gets :data:`RESEARCH_ONLY_BANNER`. A dataset CAMBER holds research-only for
    a stated reason although its licence is open (``access_reason``) gets the same heading with
    that reason instead of the licence claim.
    """
    ro = [s for s in srcs if s.get("access") == "research_only"]
    if not ro:
        return ""
    reasons = [
        f"{s.get('dataset_id') or s.get('title') or s.get('facility_id', '')}: {s['access_reason']}"
        for s in ro
        if s.get("access_reason")
    ]
    if len(reasons) == len(ro):
        return (
            "NON-COMMERCIAL / RESEARCH USE ONLY: this report is built from data CAMBER holds "
            "research-only although its licence is open (" + "; ".join(reasons) + "). Do not sell "
            "it, and do not redistribute it or the underlying data."
        )
    if reasons:
        return (
            RESEARCH_ONLY_BANNER + " Also held research-only by CAMBER: " + "; ".join(reasons) + "."
        )
    return RESEARCH_ONLY_BANNER


def _is_share_alike(licence: str) -> bool:
    return "-SA" in str(licence or "").upper()


def _extra_dois(s: dict) -> list:
    """The source's DOIs that its citation does not already print (no "doi: X" twice)."""
    cite = str(s.get("citation") or "").lower()
    return [str(d) for d in (s.get("dois") or []) if str(d).lower() not in cite]


def _sources(sources) -> list:
    return [s for s in (sources or []) if isinstance(s, dict) and s]


def data_sources_text(sources) -> str:
    """Plain-text "Data source & licence" block for ``sources`` (empty string when none).

    Each source is a provenance dict as recorded at ingest (``title``, ``publisher``, ``licence``,
    ``access``, ``citation``, ``dois``, ``landing_url``, ...). A research-only source adds the
    :data:`RESEARCH_ONLY_BANNER`; a share-alike licence adds :data:`SHARE_ALIKE_NOTE`.
    """
    srcs = _sources(sources)
    if not srcs:
        return ""
    L = []
    banner = _research_only_banner(srcs)
    if banner:
        L.append(f"*** {banner} ***")
    L.append("Data source & licence:")
    for s in srcs:
        name = s.get("title") or s.get("dataset_id") or s.get("facility_id", "")
        ident = f" [{s['dataset_id']}]" if s.get("dataset_id") and s.get("title") else ""
        L.append(f"  - {name}{ident}")
        if s.get("publisher"):
            L.append(f"    publisher: {s['publisher']}")
        if s.get("licence"):
            L.append(f"    licence: {s['licence']} ({s.get('access', 'open')})")
        if s.get("access_reason"):
            L.append(f"    held research-only: {s['access_reason']}")
        if s.get("citation"):
            L.append(f"    cite: {s['citation']}")
        if _extra_dois(s):
            L.append("    doi: " + ", ".join(_extra_dois(s)))
        if s.get("landing_url"):
            L.append(f"    source: {s['landing_url']}")
        if _is_share_alike(s.get("licence", "")):
            L.append(f"    note: {SHARE_ALIKE_NOTE}")
    return "\n".join(L)


def data_sources_html(sources) -> str:
    """HTML "Data source & licence" block for ``sources`` (empty string when none).

    Same content as :func:`data_sources_text`; the research-only banner renders as a prominent
    ``role="alert"`` box ahead of the block.
    """
    srcs = _sources(sources)
    if not srcs:
        return ""
    e = _html.escape
    parts = []
    banner = _research_only_banner(srcs)
    if banner:
        parts.append(
            "<div class='camber-nc-banner' role='alert' style='border:3px solid #b00020;"
            "padding:8px;margin:8px 0;font-weight:bold'>" + e(banner) + "</div>"
        )
    parts.append("<h2>Data source &amp; licence</h2><ul class='camber-data-sources'>")
    for s in srcs:
        name = s.get("title") or s.get("dataset_id") or s.get("facility_id", "")
        bits = [f"<b>{e(str(name))}</b>"]
        if s.get("dataset_id") and s.get("title"):
            bits.append(f" <code>{e(str(s['dataset_id']))}</code>")
        rows = []
        if s.get("publisher"):
            rows.append(f"publisher: {e(str(s['publisher']))}")
        if s.get("licence"):
            rows.append(f"licence: {e(str(s['licence']))} ({e(str(s.get('access', 'open')))})")
        if s.get("access_reason"):
            rows.append(f"held research-only: {e(str(s['access_reason']))}")
        if s.get("citation"):
            rows.append(f"cite: {e(str(s['citation']))}")
        if _extra_dois(s):
            rows.append("doi: " + ", ".join(e(d) for d in _extra_dois(s)))
        if s.get("landing_url"):
            url = e(str(s["landing_url"]))
            rows.append(f"source: <a href='{url}'>{url}</a>")
        if _is_share_alike(s.get("licence", "")):
            rows.append(e(SHARE_ALIKE_NOTE))
        parts.append(
            "<li>" + "".join(bits) + "<br>" + "<br>".join(rows) + "</li>"
            if rows
            else "<li>" + "".join(bits) + "</li>"
        )
    parts.append("</ul>")
    return "\n".join(parts)


# 0.96 (#78): minimal readable styling carried inside the report fragment -- no external asset,
# scoped to .camber-report so a host page's own styles are untouched; light and dark schemes
REPORT_CSS = (
    ".camber-report{--fg:#1d1d1b;--muted:#5f5f5a;--line:#d6d6d0;--head:#f1f1ee;--acc:#2f5fb3;"
    "color:var(--fg);font:15px/1.5 system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;"
    "max-width:1100px;margin:0 auto;padding:16px}"
    "@media (prefers-color-scheme:dark){.camber-report{--fg:#ececea;--muted:#a3a39c;"
    "--line:#3a3a36;--head:#26262a;--acc:#8fb0ee}}"
    ".camber-report h1{font-size:1.5em;line-height:1.25;margin:0 0 .4em}"
    ".camber-report h2{font-size:1.15em;margin:1.6em 0 .5em;padding-bottom:.2em;"
    "border-bottom:1px solid var(--line)}"
    ".camber-report a{color:var(--acc)}"
    ".camber-report .camber-tw{overflow-x:auto;max-width:100%}"
    ".camber-report table{border-collapse:collapse;font-size:13.5px;margin:.4em 0}"
    ".camber-report th,.camber-report td{border:1px solid var(--line);padding:5px 8px;"
    "text-align:left;vertical-align:top}"
    ".camber-report th{background:var(--head);font-weight:600}"
    ".camber-report .camber-refs{font-size:12.5px;color:var(--muted);margin-top:3px}"
    ".camber-report .camber-scope{color:var(--muted);font-size:13px;margin:0 0 1em}"
    ".camber-report ul{padding-left:1.3em}.camber-report img{max-width:100%;height:auto}"
    # at phone width a wide table keeps readable columns and scrolls inside its box
    "@media (max-width:640px){.camber-report{padding:12px 0}"
    ".camber-report .camber-tw table{min-width:880px}}"
)


def html_document(body: str, *, title: str = "CAMBER report") -> str:
    """Wrap a report fragment in a standalone HTML document (charset, viewport, title)."""
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{_html.escape(title)}</title></head><body>\n{body}\n</body></html>\n"
    )


def _tw(table_html: str) -> str:
    """A table inside a horizontal scroller, so a wide table never widens a phone-width page."""
    return f"<div class='camber-tw'>{table_html}</div>"


@dataclass
class Benchmark:
    """EUI benchmark vs a peer median (Std 211 §5.2.3 / §6.1.3)."""

    site_eui: float  # in ``unit``
    peer_median_eui: float
    metric_name: str = "ENERGY STAR property-type median"
    unit: str = "kBtu/ft2/yr"  # 0.92 (#69): kWh/m2/yr under a config "units": {"system": "si"}

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
    finding: str  # the evidence (a diagnostic/M&V result)
    affected_system: str
    comfort_iaq_impact: str = ""
    est_savings: str = ""  # band, e.g. "low/medium/high" or a number+unit
    est_cost: str = ""
    priority: str = "medium"  # low | medium | high

    def as_dict(self):
        """Return the ECM row as a plain dict."""
        return asdict(self)


@dataclass
class AuditReport:
    """A Std-211-shaped audit report assembled from analytics outputs."""

    building: str
    level: int  # 1, 2, or 3
    climate_zone: str = ""
    benchmark: Benchmark | None = None
    ecms: list = field(default_factory=list)
    end_use_notes: list = field(default_factory=list)
    comfort_notes: list = field(default_factory=list)
    caveats: list = field(default_factory=list)
    findings: list = field(default_factory=list)  # raw FDD Finding objects
    finding_magnitude_key: str | None = None  # metric to rank ties by
    # provenance of the data the report was built from (dataset, licence, citation); rendered as a
    # "Data source & licence" block, with a do-not-redistribute banner for research-only data
    data_sources: list = field(default_factory=list)
    # 0.96 (#78): an explicit title; "" builds one -- the Std-211 title only when the report carries
    # the Std-211 inputs (see :meth:`is_std211`), else a neutral one
    title: str = ""

    def is_std211(self) -> bool:
        """Whether the report carries the inputs a Std-211 audit needs: an EUI benchmark, plus an
        ECM table at Level 2 and above. A report built from trend data alone (a dataset, a single
        room) is an analytics report, not a Std-211 audit, and is titled neutrally."""
        return self.benchmark is not None and (self.level <= 1 or bool(self.ecms))

    def display_title(self) -> str:
        """The report's title: :attr:`title` when set, else the Std-211 title when
        :meth:`is_std211`, else a neutral "Building analytics report" title."""
        if self.title:
            return self.title
        if self.is_std211():
            return f"ASHRAE Std-211 Level {self.level} Audit -- {self.building}"
        return f"Building analytics report -- {self.building}"

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

        return rank_findings(
            self.findings, magnitude_key=self.finding_magnitude_key, actionable_only=True
        )

    def all_caveats(self) -> list:
        """Report-level caveats plus distinct finding-level "could not evaluate" notes.

        Surfaces the honesty convention (see ``camber.rules.base``): where a rule declined
        a sub-check for want of an input, that reaches the reader instead of hiding.
        """
        out = list(self.caveats)
        seen = set(out)
        for f in self.findings:
            for c in getattr(f, "caveats", None) or []:
                if c not in seen:
                    seen.add(c)
                    out.append(c)
        return out

    # ECMs sorted high->medium->low for presentation
    def ranked_ecms(self):
        """ECMs sorted high -> medium -> low priority for presentation."""
        order = {"high": 0, "medium": 1, "low": 2}
        return sorted(self.ecms, key=lambda e: order.get(e.priority, 1))

    def to_text(self) -> str:
        """Render the audit report as plain text."""
        from ..references import links_text, reference_ids_for

        L = [self.display_title()]
        if self.climate_zone:
            L.append(f"Climate zone: {self.climate_zone}")
        src = data_sources_text(self.data_sources)
        if src:
            L.append("\n" + src)
        if self.benchmark:
            b = self.benchmark
            L.append(
                f"\nBenchmark: site EUI {b.site_eui} {b.unit} vs "
                f"{b.peer_median_eui} ({b.metric_name}) = {b.pct_over:+.0f}%"
            )
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
                refs = links_text(reference_ids_for(rule))
                if refs:
                    L.append(f"     learn more: {refs}")
        if self.ecms:  # 0.96 (#78): an empty section is left out
            L.append(f"\nEnergy Conservation Measures ({len(self.ecms)}):")
        for i, e in enumerate(self.ranked_ecms(), 1):
            L.append(f"  {i}. [{e.priority.upper()}] {e.name} ({e.affected_system})")
            L.append(f"     finding: {e.finding}")
            if e.comfort_iaq_impact:
                L.append(f"     comfort/IAQ: {e.comfort_iaq_impact}")
            if e.est_savings or e.est_cost:
                L.append(f"     savings: {e.est_savings or 'TBD'}   cost: {e.est_cost or 'TBD'}")
        cav = self.all_caveats()
        if cav:
            L.append("\nCaveats:")
            L += [f"  - {c}" for c in cav]
        return "\n".join(L)

    def _evidence_html(self, ranked, rules, frames) -> str:
        """Pattern J: render each actionable finding's evidence chart, using per-equipment frames.

        ``frames`` is ``{equip: role-frame}`` and ``rules`` a Registry / {name: rule} / iterable;
        a finding renders evidence only when its rule exposes an ``evidence()`` hook and its
        equipment has a frame. Shares the render loop with the dashboard.
        """
        from .dashboard import _rules_map, render_evidence_blocks

        rmap = _rules_map(rules)
        if not rmap or not frames:
            return ""
        return render_evidence_blocks(ranked, rmap, frames.get)

    def action_plan(
        self, *, loads=None, price=None, params=None, aso_params=None, min_severity: str = "warn"
    ):
        """Ranked action plan (finding + estimated $/yr + advisory recommendation) for the report's
        findings, worst-dollars-first. See :func:`camber.actionplan.build_action_plan`."""
        from ..actionplan import build_action_plan

        return build_action_plan(
            self.findings,
            loads=loads,
            price=price,
            params=params,
            aso_params=aso_params,
            min_severity=min_severity,
        )

    def to_html(
        self, *, rules=None, frames=None, recommend: bool = False, loads=None, price=None
    ) -> str:
        """Render the audit report as an HTML fragment.

        Pattern J — pass ``rules`` (a Registry / {name: rule} / iterable) and ``frames``
        (``{equip: role-frame}``) to embed each actionable finding's evidence chart beneath the
        findings table. ``recommend=True`` appends a ranked **action plan** ($/yr + advisory
        recommendation per finding; ``loads``/``price`` feed the cost estimate).
        """
        from ..references import links_html, reference_ids_for

        e = _html.escape
        parts = [
            f"<style>{REPORT_CSS}</style><div class='camber-report'>",
            f"<h1>{e(self.display_title()).replace(' -- ', ' &mdash; ')}</h1>",
        ]
        if self.climate_zone:
            parts.append(f"<p><b>Climate zone:</b> {e(self.climate_zone)}</p>")
        src = data_sources_html(self.data_sources)
        if src:
            parts.append(src)
        if self.benchmark:
            b = self.benchmark
            parts.append(
                f"<p><b>Benchmark:</b> site EUI {b.site_eui} {e(b.unit).replace('2/', '&sup2;/')} "
                f"vs {b.peer_median_eui} ({e(b.metric_name)}) = "
                f"<b>{b.pct_over:+.0f}%</b></p>"
            )
        if self.end_use_notes:
            parts.append(
                "<h2>End-use / system notes</h2><ul>"
                + "".join(f"<li>{e(n)}</li>" for n in self.end_use_notes)
                + "</ul>"
            )
        if self.comfort_notes:
            parts.append(
                "<h2>Comfort (Std 55)</h2><ul>"
                + "".join(f"<li>{e(n)}</li>" for n in self.comfort_notes)
                + "</ul>"
            )
        rf = self.ranked_findings() if self.findings else []
        if rf:
            parts.append("<h2>Prioritized FDD findings</h2>")
            refs = [links_html(reference_ids_for(getattr(r.finding, "rule", ""))) for r in rf]
            more = "<th>Learn more</th>" if any(refs) else ""
            rows = [
                "<table border='1' cellpadding='4'><tr><th>#</th>"
                "<th>Severity</th><th>Rule</th><th>Equipment</th>"
                f"<th>Summary</th>{more}</tr>"
            ]
            for r, ref in zip(rf, refs):
                eq = e(str(getattr(r.finding, "equip", "")))
                rule = e(str(getattr(r.finding, "rule", "")))
                summ = e(str(getattr(r.finding, "summary", "") or ""))
                rows.append(
                    f"<tr><td>{r.rank}</td><td>{e(r.severity)}</td>"
                    f"<td>{rule}</td><td>{eq}</td><td>{summ}</td>"
                    + (f"<td class='camber-refs'>{ref}</td>" if more else "")
                    + "</tr>"
                )
            rows.append("</table>")
            parts.append(_tw("".join(rows)))
            if rules is not None and frames is not None:
                imgs = self._evidence_html(rf, rules, frames)
                if imgs:
                    parts.append("<h2>Finding evidence</h2>" + imgs)
        if recommend and self.findings:
            from ..actionplan import action_plan_html

            items = self.action_plan(loads=loads, price=price)
            if items:
                # only claim dollar-ranking when at least one item is actually costed
                ranked_by = (
                    "$/yr" if any(getattr(i, "costed", False) for i in items) else "severity"
                )
                parts.append(
                    f"<h2>Recommended actions (ranked by {ranked_by})</h2>"
                    + _tw(action_plan_html(items))
                )
        if self.ecms:  # 0.96 (#78): an empty ECM table is left out
            parts.append("<h2>Energy Conservation Measures</h2>")
            rows = [
                "<table border='1' cellpadding='4'><tr><th>#</th><th>Priority</th>"
                "<th>Measure</th><th>System</th><th>Finding</th>"
                "<th>Comfort/IAQ</th><th>Savings</th><th>Cost</th></tr>"
            ]
            for i, m in enumerate(self.ranked_ecms(), 1):
                rows.append(
                    f"<tr><td>{i}</td><td>{e(m.priority)}</td><td>{e(m.name)}</td>"
                    f"<td>{e(m.affected_system)}</td><td>{e(m.finding)}</td>"
                    f"<td>{e(m.comfort_iaq_impact)}</td><td>{e(m.est_savings)}</td>"
                    f"<td>{e(m.est_cost)}</td></tr>"
                )
            rows.append("</table>")
            parts.append(_tw("".join(rows)))
        cav = self.all_caveats()
        if cav:
            parts.append(
                "<h2>Caveats</h2><ul>" + "".join(f"<li>{e(c)}</li>" for c in cav) + "</ul>"
            )
        parts.append("</div>")
        return "\n".join(parts)

    def to_html_document(self, **kw) -> str:
        """:meth:`to_html` as a standalone HTML document (see :func:`html_document`)."""
        return html_document(self.to_html(**kw), title=self.display_title())
