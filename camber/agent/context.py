"""The grounding surface — deterministic Facts the explanation/Q&A layer is allowed to cite.

A :class:`Fact` is one atomic, citable statement built from an existing deterministic object
(``Finding``, ``FaultCost``, ``Recommendation``, ``RootCauseGroup``, …). Its ``text`` comes straight
from that object's pre-written template surface (``Finding.summary``, ``Recommendation.title`` +
action, ``FaultCost.basis``/dollars) and its ``data`` is the object's ``as_dict()`` — so a citation
is always traceable back to a deterministic result. :class:`Context` is the whitelist the LLM sees:
it can only cite facts in the set, and ``to_prompt_block()`` is the *only* thing ever put in front
of a model — it cannot invent equipment or metrics that aren't here.

Ids are **order-stable and deterministic** (``F1``, ``C1``, ``R1``, ``G1`` incrementing per kind in
input order) so citations are reproducible and testable. This module is pure (no LLM, no I/O,
no vendor).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from ..aso import recommend
from ..fault_economics import estimate_cost
from ..rules.triage import group_findings

#: fact kind -> id prefix (order-stable, human-readable citations)
_KIND_PREFIX = {
    "finding": "F",
    "cost": "C",
    "recommendation": "R",
    "rootcause": "G",
    "run": "N",
    "scorecard": "S",
    "completeness": "M",
    "history": "H",
    "mapping": "P",
    "fleet": "L",
}


@dataclass(frozen=True)
class Fact:
    """One citable, deterministic statement the explanation layer may reference by ``id``."""

    id: str
    kind: str  # finding | cost | recommendation | rootcause | run | scorecard | ...
    equip: str
    text: str  # from the deterministic template surface (never model-authored)
    data: dict = field(default_factory=dict)  # the source object's as_dict()

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Context:
    """An ordered whitelist of :class:`Fact` — the only ground truth the LLM may cite."""

    facts: list = field(default_factory=list)
    site: str | None = None

    def ids(self) -> list:
        return [f.id for f in self.facts]

    def by_id(self, fid: str):
        for f in self.facts:
            if f.id == fid:
                return f
        return None

    def by_kind(self, kind: str) -> list:
        return [f for f in self.facts if f.kind == kind]

    def by_equip(self, equip: str) -> list:
        return [f for f in self.facts if f.equip == equip]

    def as_dict(self) -> dict:
        return {"site": self.site, "facts": [f.as_dict() for f in self.facts]}

    def to_prompt_block(self) -> str:
        """The exact text put in front of a model: one ``[id] (kind, equip) text`` line per fact."""
        lines = []
        for f in self.facts:
            head = f"[{f.id}] ({f.kind}{', ' + f.equip if f.equip else ''})"
            lines.append(f"{head} {f.text}")
        return "\n".join(lines)


class _IdGen:
    """Per-kind incrementing id generator (F1, F2, C1, …) — order-stable and deterministic."""

    def __init__(self):
        self._n: dict = {}

    def next(self, kind: str) -> str:
        prefix = _KIND_PREFIX.get(kind, kind[:1].upper() or "X")
        self._n[kind] = self._n.get(kind, 0) + 1
        return f"{prefix}{self._n[kind]}"


def _cost_text(fc) -> str:
    """A cost fact's text: the dollar figure when costed, otherwise the basis — never a fake
    number."""
    if fc.costed:
        split = []
        if fc.electricity_kwh:
            split.append(f"{fc.electricity_kwh:,.0f} kWh")
        if fc.gas_therms:
            split.append(f"{fc.gas_therms:,.0f} therms")
        tail = f" ({', '.join(split)}; {fc.basis})" if split else f" ({fc.basis})"
        return f"Estimated annual cost ≈ ${fc.annual_cost_usd:,.0f}{tail}."
    return f"No dollar figure — {fc.basis}."


def _rec_text(rec) -> str:
    bits = [rec.title.rstrip(".") + "."]
    if rec.action:
        bits.append(rec.action)
    if rec.suggested:
        bits.append(f"Suggested: {rec.suggested}.")
    if rec.standard:
        bits.append(f"Basis: {rec.standard}.")
    return " ".join(bits)


def facts_from_findings(
    findings, *, loads=None, price=None, ids: _IdGen | None = None, actionable_only: bool = True
) -> list:
    """Facts for a list of findings: the finding itself, its cost, its recommendation, root causes.

    For each finding: a ``finding`` fact (``Finding.summary``); a ``cost`` fact via
    :func:`fault_economics.estimate_cost` (dollar figure only when ``costed``, else the basis); and
    a ``recommendation`` fact via :func:`aso.recommend` when one exists. Then one ``rootcause`` fact
    per :func:`triage.group_findings` cluster. All text is deterministic template output.
    """
    gen = ids or _IdGen()
    findings = list(findings)
    out: list = []
    for f in findings:
        equip = getattr(f, "equip", "")
        summary = getattr(f, "summary", "") or f"{getattr(f, 'rule', 'finding')} on {equip}"
        out.append(Fact(gen.next("finding"), "finding", equip, summary, _as_dict(f)))
        fc = estimate_cost(f, _load_for(loads, equip), price)
        out.append(Fact(gen.next("cost"), "cost", equip, _cost_text(fc), fc.as_dict()))
        rec = recommend(f)
        if rec is not None:
            out.append(
                Fact(
                    gen.next("recommendation"),
                    "recommendation",
                    equip,
                    _rec_text(rec),
                    rec.as_dict(),
                )
            )
    for grp in group_findings(findings, actionable_only=actionable_only):
        if len(grp.members) < 2:
            continue  # a solo group adds nothing over its finding fact
        out.append(
            Fact(gen.next("rootcause"), "rootcause", grp.equip, grp.summary, _group_as_dict(grp))
        )
    return out


def facts_from_run(run, *, loads=None, price=None, ids: _IdGen | None = None) -> list:
    """Facts for a whole :class:`config.RunResult`: a run-summary fact, then all finding facts."""
    gen = ids or _IdGen()
    site = getattr(run, "site", "") or ""
    n_equip = getattr(run, "equipment", 0)
    rules_run = getattr(run, "rules_run", []) or []
    findings = getattr(run, "findings", []) or []
    text = (
        f"Run over {n_equip} equipment at '{site}': {len(rules_run)} rules executed, "
        f"{len(findings)} findings."
    )
    out = [
        Fact(
            gen.next("run"),
            "run",
            "",
            text,
            {
                "site": site,
                "equipment": n_equip,
                "n_rules": len(rules_run),
                "n_findings": len(findings),
            },
        )
    ]
    out.extend(facts_from_findings(findings, loads=loads, price=price, ids=gen))
    return out


def facts_from_scorecard(scorecard, *, ids: _IdGen | None = None) -> list:
    """One overall health fact plus a fact for each category carrying a fault or warning."""
    gen = ids or _IdGen()
    text = (
        f"Overall building health: grade {scorecard.overall_grade} "
        f"({scorecard.overall_score:.0f}/100), {scorecard.n_actionable} actionable findings."
    )
    out = [Fact(gen.next("scorecard"), "scorecard", "", text, scorecard.as_dict())]
    for cat in getattr(scorecard, "categories", []):
        if getattr(cat, "n_faults", 0) or getattr(cat, "n_warnings", 0):
            ctext = (
                f"{cat.category}: grade {cat.grade} ({cat.score:.0f}/100) — "
                f"{cat.n_faults} faults, {cat.n_warnings} warnings."
            )
            out.append(Fact(gen.next("scorecard"), "scorecard", "", ctext, cat.as_dict()))
    return out


def facts_from_completeness(items, *, ids: _IdGen | None = None) -> list:
    """A fact per equipment that is missing template-required roles — i.e. *why* a rule couldn't
    run."""
    gen = ids or _IdGen()
    out: list = []
    for c in items:
        missing = sorted(_role_str(r) for r in getattr(c, "missing_required", ()) or ())
        if not missing:
            continue
        equip = getattr(c, "equip", "") or ""
        text = (
            f"{equip or c.equip_class}: some rules cannot run — missing required "
            f"role(s) {', '.join(missing)} for class '{c.equip_class}'."
        )
        out.append(
            Fact(gen.next("completeness"), "completeness", equip, text, _completeness_dict(c))
        )
    return out


def facts_from_history(
    read_api, *, site=None, equip=None, role=None, limit=None, ids: _IdGen | None = None
) -> list:
    """Bounded per-point stats (count/min/max/mean/span) from a :class:`api.read.ReadAPI`.

    Never emits raw series — only summary statistics — so the context stays small and no unbounded
    data leaks into a prompt.
    """
    gen = ids or _IdGen()
    hist = read_api.history(site=site, equip=equip, role=role, limit=limit)
    buckets: dict = {}
    for row in hist.get("history", []):
        v = row.get("value")
        if v is None:
            continue
        buckets.setdefault((row["equip"], row["role"]), []).append((row["ts"], v))
    out: list = []
    for (eq, rl), pts in buckets.items():
        vals = [v for _, v in pts]
        ts = [t for t, _ in pts]
        lo, hi, mean = min(vals), max(vals), sum(vals) / len(vals)
        text = (
            f"{eq}/{rl}: {len(vals)} samples, min {lo:.2f}, max {hi:.2f}, mean {mean:.2f} "
            f"({min(ts)} to {max(ts)})."
        )
        out.append(
            Fact(
                gen.next("history"),
                "history",
                eq,
                text,
                {
                    "equip": eq,
                    "role": rl,
                    "count": len(vals),
                    "min": lo,
                    "max": hi,
                    "mean": mean,
                    "start": min(ts),
                    "end": max(ts),
                },
            )
        )
    return out


def facts_from_mapping(review_result, *, ids: _IdGen | None = None) -> list:
    """Facts from a :func:`mapping_confidence.review` result: unmapped and low-confidence tokens."""
    gen = ids or _IdGen()
    out: list = []
    for s in review_result.get("unmapped", []):
        out.append(
            Fact(
                gen.next("mapping"),
                "mapping",
                "",
                f"Point '{s.token}' is unmapped — no role resolved.",
                _as_dict(s),
            )
        )
    for s in review_result.get("needs_review", []):
        out.append(
            Fact(
                gen.next("mapping"),
                "mapping",
                "",
                f"Point '{s.token}' maps to {getattr(s, 'role', '?')} but needs review "
                f"(confidence {getattr(s, 'confidence', 0.0):.2f}).",
                _as_dict(s),
            )
        )
    return out


def facts_from_fleet(fleet_report, *, ids: _IdGen | None = None) -> list:
    """Portfolio facts from a :class:`report.FleetReport`: a fleet-summary fact + one per building.

    Enables grounded portfolio-wide triage ("which building is worst / wastes the most?"). Every
    figure (EUI, fault counts, $/yr) is in the fact text so :func:`verify.check` keeps portfolio
    answers grounded. Per-building facts carry the site name in ``equip`` so equipment/site routing
    resolves.
    """
    gen = ids or _IdGen()
    fr = fleet_report
    buildings = getattr(fr, "buildings", []) or []
    parts = [f"Fleet of {len(buildings)} buildings"]
    if getattr(fr, "peer_median_eui", None):
        parts.append(f"peer-median EUI {fr.peer_median_eui:g} kBtu/ft2/yr")
    if getattr(fr, "total_annual_cost_usd", None) is not None:
        parts.append(f"estimated recoverable waste ${fr.total_annual_cost_usd:,.0f}/yr fleet-wide")
    out = [
        Fact(
            gen.next("fleet"),
            "fleet",
            "",
            "; ".join(parts) + ".",
            {
                "n_buildings": len(buildings),
                "peer_median_eui": getattr(fr, "peer_median_eui", None),
                "total_annual_cost_usd": getattr(fr, "total_annual_cost_usd", None),
            },
        )
    ]
    for b in buildings:
        bits = [f"{b.site}:"]
        if b.eui is not None:
            bits.append(f"EUI {b.eui:g} kBtu/ft2/yr")
        if b.pct_vs_median is not None:
            bits.append(f"{b.pct_vs_median:+.0f}% vs peer median")
        bits.append(f"{b.n_fault} faults, {b.n_warn} warnings")
        if b.annual_cost_usd is not None:
            bits.append(f"${b.annual_cost_usd:,.0f}/yr recoverable")
        out.append(Fact(gen.next("fleet"), "fleet", b.site, " ".join(bits) + ".", _as_dict(b)))
    return out


def build_context(
    findings=None,
    *,
    loads=None,
    price=None,
    site=None,
    run=None,
    runs=None,
    fleet=None,
    scorecard=None,
    completeness=None,
    read_api=None,
    mapping_review=None,
    history_query: dict | None = None,
) -> Context:
    """The single front door. Assemble a :class:`Context` with deterministic, order-stable ids.

    Any subset of sources may be supplied; a shared id generator keeps ids unique and stable across
    them. When ``run`` is given, ``site`` defaults to the run's site.
    """
    gen = _IdGen()
    facts: list = []
    if fleet is not None:
        facts.extend(facts_from_fleet(fleet, ids=gen))
        site = site or [b.site for b in getattr(fleet, "buildings", []) or []]
    if run is not None:
        facts.extend(facts_from_run(run, loads=loads, price=price, ids=gen))
        site = site or getattr(run, "site", None)
    for r in runs or []:
        facts.extend(facts_from_run(r, loads=loads, price=price, ids=gen))
    if runs and site is None:
        site = [getattr(r, "site", None) for r in runs]
    if findings:
        facts.extend(facts_from_findings(findings, loads=loads, price=price, ids=gen))
    if scorecard is not None:
        facts.extend(facts_from_scorecard(scorecard, ids=gen))
    if completeness:
        facts.extend(facts_from_completeness(completeness, ids=gen))
    if read_api is not None:
        facts.extend(facts_from_history(read_api, ids=gen, **(history_query or {})))
    if mapping_review is not None:
        facts.extend(facts_from_mapping(mapping_review, ids=gen))
    return Context(facts=facts, site=site)


# --------------------------------------------------------------------------- helpers


def _as_dict(obj) -> dict:
    fn = getattr(obj, "as_dict", None)
    if callable(fn):
        return fn()
    try:
        return asdict(obj)
    except TypeError:
        return dict(getattr(obj, "__dict__", {}))


def _group_as_dict(grp) -> dict:
    return {
        "equip": grp.equip,
        "primary_rule": grp.primary_rule,
        "severity": grp.severity,
        "summary": grp.summary,
        "members": [_as_dict(m) for m in grp.members],
    }


def _load_for(loads, equip):
    if not loads:
        return None
    if isinstance(loads, dict):
        return loads.get(equip)
    return loads


def _role_str(role) -> str:
    return getattr(role, "value", str(role))


def _completeness_dict(c) -> dict:
    fn = getattr(c, "as_dict", None)
    if callable(fn):
        return fn()
    return {
        "equip": getattr(c, "equip", ""),
        "equip_class": getattr(c, "equip_class", ""),
        "present": sorted(_role_str(r) for r in getattr(c, "present", ()) or ()),
        "missing_required": sorted(_role_str(r) for r in getattr(c, "missing_required", ()) or ()),
        "missing_optional": sorted(_role_str(r) for r in getattr(c, "missing_optional", ()) or ()),
        "has_template": getattr(c, "has_template", False),
    }
