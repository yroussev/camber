"""The grounding surface — deterministic Facts the explanation/Q&A layer is allowed to cite.

A :class:`Fact` is one atomic, citable statement built from an existing deterministic object
(``Finding``, ``FaultCost``, ``Recommendation``, ``RootCauseGroup``, …). Its ``text`` comes straight
from that object's pre-written template surface (``Finding.summary``, ``Recommendation.title`` + action,
``FaultCost.basis``/dollars) and its ``data`` is the object's ``as_dict()`` — so a citation is always
traceable back to a deterministic result. :class:`Context` is the whitelist the LLM sees: it can only
cite facts in the set, and ``to_prompt_block()`` is the *only* thing ever put in front of a model — it
cannot invent equipment or metrics that aren't here.

Ids are **order-stable and deterministic** (``F1``, ``C1``, ``R1``, ``G1`` incrementing per kind in
input order) so citations are reproducible and testable. This module is pure (no LLM, no I/O, no vendor).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict

from ..fault_economics import estimate_cost
from ..aso import recommend
from ..rules.triage import group_findings

#: fact kind -> id prefix (order-stable, human-readable citations)
_KIND_PREFIX = {
    "finding": "F", "cost": "C", "recommendation": "R", "rootcause": "G",
    "run": "N", "scorecard": "S", "completeness": "M", "history": "H", "mapping": "P",
}


@dataclass(frozen=True)
class Fact:
    """One citable, deterministic statement the explanation layer may reference by ``id``."""

    id: str
    kind: str                    # finding | cost | recommendation | rootcause | run | scorecard | ...
    equip: str
    text: str                    # from the deterministic template surface (never model-authored)
    data: dict = field(default_factory=dict)   # the source object's as_dict()

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
    """A cost fact's text: the dollar figure when costed, otherwise the basis — never a fake number."""
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


def facts_from_findings(findings, *, loads=None, price=None, ids: _IdGen | None = None,
                        actionable_only: bool = True) -> list:
    """Facts for a list of findings: the finding itself, its cost, its recommendation, root causes.

    For each finding: a ``finding`` fact (``Finding.summary``); a ``cost`` fact via
    :func:`fault_economics.estimate_cost` (dollar figure only when ``costed``, else the basis); and a
    ``recommendation`` fact via :func:`aso.recommend` when one exists. Then one ``rootcause`` fact per
    :func:`triage.group_findings` cluster. All text is deterministic template output.
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
            out.append(Fact(gen.next("recommendation"), "recommendation", equip,
                            _rec_text(rec), rec.as_dict()))
    for grp in group_findings(findings, actionable_only=actionable_only):
        if len(grp.members) < 2:
            continue                       # a solo group adds nothing over its finding fact
        out.append(Fact(gen.next("rootcause"), "rootcause", grp.equip, grp.summary,
                        _group_as_dict(grp)))
    return out


def build_context(findings=None, *, loads=None, price=None, site: str | None = None) -> Context:
    """The single front door. Assemble a :class:`Context` with deterministic, order-stable ids."""
    gen = _IdGen()
    facts: list = []
    if findings:
        facts.extend(facts_from_findings(findings, loads=loads, price=price, ids=gen))
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
    return {"equip": grp.equip, "primary_rule": grp.primary_rule, "severity": grp.severity,
            "summary": grp.summary, "members": [_as_dict(m) for m in grp.members]}


def _load_for(loads, equip):
    if not loads:
        return None
    if isinstance(loads, dict):
        return loads.get(equip)
    return loads
