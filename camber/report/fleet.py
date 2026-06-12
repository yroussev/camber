"""Fleet / portfolio rollup across many buildings.

Most analytics value at scale is comparative: which buildings are worst, what
faults recur across the portfolio, and how each site benchmarks against its peers.
This rolls per-building results into a portfolio summary —

- **cross-sectional EUI benchmarking**: rank sites by energy-use intensity and
  place each in a percentile against the peer set (and vs. a peer median if given);
- **fault rollup**: actionable-fault counts per site and the most common findings
  fleet-wide; and
- **dollar rollup** (optional): each site's estimated annual recoverable waste, so the
  portfolio can be ranked by money and a fleet total reported — using
  :mod:`camber.fault_economics` when an :class:`~camber.fault_economics.EnergyPrice` (and
  optional per-equipment sizing) is supplied, or a precomputed ``annual_cost_usd`` per
  building —

rendered to text or HTML. Inputs are plain: a list of per-building dicts
``{"site", "eui", "findings"}`` (optionally ``"annual_cost_usd"``) where findings are any
finding-like objects.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ..integrate.tickets import _attr

_ACTIONABLE = frozenset({"fault", "warn"})


@dataclass(frozen=True)
class BuildingSummary:
    """Per-building rollup: EUI, its fleet rank/percentile, and fault counts."""

    site: str
    eui: float | None
    n_fault: int
    n_warn: int
    top_rules: list                 # most common actionable rules in this building
    eui_percentile: float | None    # 100 = most efficient (lowest EUI) in the fleet
    pct_vs_median: float | None     # +% above / -% below the peer-median EUI
    annual_cost_usd: float | None = None   # estimated recoverable annual waste ($)


@dataclass
class FleetReport:
    """A portfolio rollup over many :class:`BuildingSummary` rows."""

    buildings: list = field(default_factory=list)
    peer_median_eui: float | None = None
    fleet_top_rules: list = field(default_factory=list)   # [(rule, n_buildings)]
    total_annual_cost_usd: float | None = None            # fleet-wide recoverable waste ($)

    def worst_by_faults(self):
        """Buildings ordered by actionable-fault burden (faults, then warnings)."""
        return sorted(self.buildings, key=lambda b: (-b.n_fault, -b.n_warn))

    def worst_by_cost(self):
        """Buildings ordered by estimated recoverable dollars (highest first)."""
        return sorted([b for b in self.buildings if b.annual_cost_usd is not None],
                      key=lambda b: -b.annual_cost_usd)

    def by_efficiency(self):
        """Buildings ordered most- to least-efficient (lowest EUI first)."""
        return sorted([b for b in self.buildings if b.eui is not None],
                      key=lambda b: b.eui)

    def to_text(self) -> str:
        """Render the fleet rollup as plain text."""
        L = [f"Fleet summary -- {len(self.buildings)} buildings"]
        if self.peer_median_eui:
            L.append(f"Peer-median EUI: {self.peer_median_eui:g} kBtu/ft2/yr")
        if self.total_annual_cost_usd is not None:
            L.append(f"Estimated recoverable waste: ${self.total_annual_cost_usd:,.0f}/yr fleet-wide")
        L.append("\nCross-sectional EUI (most efficient first):")
        for b in self.by_efficiency():
            extra = []
            if b.eui_percentile is not None:
                extra.append(f"{b.eui_percentile:.0f}th pctile")
            if b.pct_vs_median is not None:
                extra.append(f"{b.pct_vs_median:+.0f}% vs median")
            L.append(f"  {b.site:16s} {b.eui:6.1f}  ({', '.join(extra)})")
        L.append("\nFault rollup (worst first):")
        for b in self.worst_by_faults():
            tr = f"  top: {', '.join(b.top_rules)}" if b.top_rules else ""
            L.append(f"  {b.site:16s} {b.n_fault} faults, {b.n_warn} warnings{tr}")
        ranked = self.worst_by_cost()
        if ranked:
            L.append("\nRecoverable waste (highest first):")
            for b in ranked:
                L.append(f"  {b.site:16s} ${b.annual_cost_usd:,.0f}/yr")
        if self.fleet_top_rules:
            L.append("\nMost common findings fleet-wide:")
            for rule, n in self.fleet_top_rules:
                L.append(f"  {rule}  ({n} buildings)")
        return "\n".join(L)

    def to_html(self) -> str:
        """Render the fleet rollup as an HTML fragment."""
        import html as _h
        e = _h.escape
        parts = [f"<h1>Fleet summary &mdash; {len(self.buildings)} buildings</h1>"]
        if self.peer_median_eui:
            parts.append(f"<p><b>Peer-median EUI:</b> {self.peer_median_eui:g} "
                         "kBtu/ft&sup2;/yr</p>")
        if self.total_annual_cost_usd is not None:
            parts.append(f"<p><b>Estimated recoverable waste:</b> "
                         f"${self.total_annual_cost_usd:,.0f}/yr fleet-wide</p>")
        parts.append("<h2>Cross-sectional EUI</h2><table border='1' cellpadding='4'>"
                     "<tr><th>Site</th><th>EUI</th><th>Percentile</th>"
                     "<th>vs median</th></tr>")
        for b in self.by_efficiency():
            pc = "" if b.eui_percentile is None else f"{b.eui_percentile:.0f}"
            vm = "" if b.pct_vs_median is None else f"{b.pct_vs_median:+.0f}%"
            parts.append(f"<tr><td>{e(b.site)}</td><td>{b.eui:.1f}</td>"
                         f"<td>{pc}</td><td>{vm}</td></tr>")
        parts.append("</table><h2>Fault rollup</h2><table border='1' cellpadding='4'>"
                     "<tr><th>Site</th><th>Faults</th><th>Warnings</th>"
                     "<th>Top findings</th></tr>")
        for b in self.worst_by_faults():
            parts.append(f"<tr><td>{e(b.site)}</td><td>{b.n_fault}</td>"
                         f"<td>{b.n_warn}</td><td>{e(', '.join(b.top_rules))}</td></tr>")
        parts.append("</table>")
        ranked = self.worst_by_cost()
        if ranked:
            parts.append("<h2>Recoverable waste</h2><table border='1' cellpadding='4'>"
                         "<tr><th>Site</th><th>Estimated $/yr</th></tr>")
            for b in ranked:
                parts.append(f"<tr><td>{e(b.site)}</td>"
                             f"<td>${b.annual_cost_usd:,.0f}</td></tr>")
            parts.append("</table>")
        if self.fleet_top_rules:
            parts.append("<h2>Most common findings fleet-wide</h2><ul>"
                         + "".join(f"<li>{e(r)} ({n} buildings)</li>"
                                   for r, n in self.fleet_top_rules) + "</ul>")
        return "\n".join(parts)


def _building_cost(b, price, loads, cost_params):
    """Per-building estimated annual $ waste: a precomputed value wins, else estimate it."""
    if b.get("annual_cost_usd") is not None:
        return float(b["annual_cost_usd"])
    if price is None:
        return None
    from ..fault_economics import cost_findings, total_cost
    site_loads = (loads or {}).get(b.get("site", ""))
    costs = cost_findings(b.get("findings", []), site_loads, price, params=cost_params)
    return total_cost(costs)["annual_cost_usd"]


def build_fleet_report(buildings, *, peer_median_eui=None, top_n=5,
                       price=None, loads=None, cost_params=None) -> FleetReport:
    """Roll per-building results into a :class:`FleetReport`.

    ``buildings`` is a list of dicts ``{"site", "eui", "findings"}`` (optionally a
    precomputed ``"annual_cost_usd"``). ``eui`` may be None (excluded from benchmarking).
    Cross-sectional percentile is computed within the supplied set (100 = lowest EUI = most
    efficient); ``pct_vs_median`` uses ``peer_median_eui`` if given, else the fleet's own
    median EUI.

    If ``price`` (an :class:`~camber.fault_economics.EnergyPrice`) is supplied, each
    building's recoverable annual waste is estimated from its findings via
    :mod:`camber.fault_economics` (``loads`` is an optional ``{site: EquipmentLoad |
    {equip: EquipmentLoad}}`` of equipment sizing; ``cost_params`` overrides cost
    assumptions), and the fleet total is reported. A building's precomputed
    ``annual_cost_usd`` always wins over an estimate.
    """
    euis = sorted(b["eui"] for b in buildings if b.get("eui") is not None)
    fleet_median = euis[len(euis) // 2] if euis else None
    median = peer_median_eui if peer_median_eui is not None else fleet_median

    rule_buildings = Counter()      # rule -> number of buildings it appears in
    summaries = []
    any_cost = False
    for b in buildings:
        site = b.get("site", "")
        eui = b.get("eui")
        acts = [f for f in b.get("findings", [])
                if _attr(f, "severity", "info") in _ACTIONABLE]
        n_fault = sum(1 for f in acts if _attr(f, "severity") == "fault")
        n_warn = sum(1 for f in acts if _attr(f, "severity") == "warn")
        rule_counts = Counter(_attr(f, "rule", "") for f in acts)
        top_rules = [r for r, _ in rule_counts.most_common(3) if r]
        for r in set(rule_counts):
            if r:
                rule_buildings[r] += 1
        pctile = None
        if eui is not None and euis:
            pctile = round(100.0 * sum(1 for e in euis if e >= eui) / len(euis), 0)
        pct_vs_median = None
        if eui is not None and median:
            pct_vs_median = round(100.0 * (eui - median) / median, 0)
        cost = _building_cost(b, price, loads, cost_params)
        any_cost = any_cost or cost is not None
        summaries.append(BuildingSummary(
            site=site, eui=eui, n_fault=n_fault, n_warn=n_warn, top_rules=top_rules,
            eui_percentile=pctile, pct_vs_median=pct_vs_median, annual_cost_usd=cost))

    total = (round(sum(b.annual_cost_usd for b in summaries
                       if b.annual_cost_usd is not None), 2) if any_cost else None)
    return FleetReport(buildings=summaries, peer_median_eui=median,
                       fleet_top_rules=rule_buildings.most_common(top_n),
                       total_annual_cost_usd=total)
