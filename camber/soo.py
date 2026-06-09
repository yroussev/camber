"""Sequence-of-Operations (SOO) conformance engine.

The rule library asks "is this a known fault?"; SOO conformance asks the complementary
question "is the equipment doing what its sequence of operations *says* it should?"
You write the sequence down as a short list of declarative **clauses** -- each a
gated expectation over roles -- and the engine measures, per clause, the fraction of
applicable intervals where the equipment actually conformed.

A clause is ``when <gate> then expect <predicate>`` (the gate is optional -> always).
Both gate and expectation are **predicates**: a role compared to a constant, to another
role (e.g. a setpoint), or to a binary on/off state. Examples a sequence might encode:

    when OAT > 65F        then boiler_status is off          # summer lockout
    when supply_fan on    then supply_air_temp within 2F of supply_air_temp_sp
    when occupancy is off  then airflow <= 200               # unoccupied setback

Clauses are plain data (dataclasses or JSON dicts), so a sequence is a config artifact,
not code -- and because they key off roles, the same sequence spec runs on any building
once its points are mapped. Conformance is reported as a percentage with a severity, and
each clause also emits a :class:`~camber.rules.base.Finding` so SOO results flow through
the same prioritization, reporting, and triage as the rule library.

This is intentionally a *measurement* of operated-vs-designed behavior, not a fault
taxonomy: a low conformance score points at the clause and the data behind it, leaving
the diagnosis to the analyst (and to the rule library).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict

import numpy as np
import pandas as pd

from .model.roles import Role
from .rules.base import Finding

# Comparison operators a predicate may use. Numeric compares take ``value`` (a
# constant) or ``ref`` (another role's series); eq/ne/within use ``tol``; off/on
# treat the subject as a 0/1 binary (status/command) point.
_OPS = ("lt", "le", "gt", "ge", "eq", "ne", "within", "off", "on")
_OP_SYMBOL = {"lt": "<", "le": "<=", "gt": ">", "ge": ">=", "eq": "==", "ne": "!=",
              "within": "within", "off": "is off", "on": "is on"}


def _as_role(x) -> Role:
    """Coerce a Role or a role slug string to a Role."""
    return x if isinstance(x, Role) else Role(x)


@dataclass
class Predicate:
    """A boolean test over one role: subject OP (value | ref), with a tolerance.

    ``ref`` (another role) takes precedence over ``value`` (a constant) as the
    right-hand side. ``off``/``on`` ignore both and test the subject as 0/1.
    """

    subject: Role
    op: str
    value: float | None = None
    ref: Role | None = None
    tol: float = 0.5

    def __post_init__(self):
        self.subject = _as_role(self.subject)
        if self.ref is not None:
            self.ref = _as_role(self.ref)
        if self.op not in _OPS:
            raise ValueError(f"unknown op {self.op!r}; expected one of {_OPS}")

    def roles(self) -> set:
        """Roles this predicate reads (subject plus ref, if any)."""
        return {self.subject} | ({self.ref} if self.ref is not None else set())

    def evaluate(self, frame: pd.DataFrame):
        """Return (valid, holds) boolean Series, or (None, None) if a role is absent.

        ``valid`` marks rows where the inputs are present (non-NaN); ``holds`` is True
        only where the predicate is both valid and satisfied (False elsewhere), so both
        Series stay clean ``bool`` dtype.
        """
        if self.subject not in frame.columns:
            return None, None
        left = frame[self.subject]
        if self.op in ("off", "on"):
            valid = left.notna()
            holds = (left < 0.5) if self.op == "off" else (left >= 0.5)
            return valid, (holds & valid)
        if self.ref is not None:
            if self.ref not in frame.columns:
                return None, None
            right = frame[self.ref]
            valid = left.notna() & right.notna()
        else:
            right = self.value
            valid = left.notna()
        if self.op == "lt":
            holds = left < right
        elif self.op == "le":
            holds = left <= right
        elif self.op == "gt":
            holds = left > right
        elif self.op == "ge":
            holds = left >= right
        elif self.op in ("eq", "within"):
            holds = (left - right).abs() <= self.tol
        else:  # ne
            holds = (left - right).abs() > self.tol
        # & valid forces False where inputs were NaN, keeping a clean bool Series
        return valid, (holds.fillna(False).astype(bool) & valid)

    def describe(self) -> str:
        """Human-readable predicate, e.g. ``supply_air_temp within 2.0 of supply_air_temp_sp``."""
        if self.op in ("off", "on"):
            return f"{self.subject.value} {_OP_SYMBOL[self.op]}"
        rhs = self.ref.value if self.ref is not None else self.value
        if self.op == "within":
            return f"{self.subject.value} within {self.tol} of {rhs}"
        return f"{self.subject.value} {_OP_SYMBOL[self.op]} {rhs}"


@dataclass
class Clause:
    """One sequence clause: ``when <gate> then expect <predicate>`` + conformance bands."""

    name: str
    expect: Predicate
    when: Predicate | None = None
    fault_below: float = 80.0     # conformance % below this == fault
    warn_below: float = 95.0      # conformance % below this == warn
    min_samples: int = 10         # fewer applicable intervals -> not assessable

    def roles(self) -> set:
        """All roles this clause reads (gate + expectation)."""
        return self.expect.roles() | (self.when.roles() if self.when else set())


@dataclass
class ClauseResult:
    """Conformance of one clause over a role-frame."""

    name: str
    severity: str                 # "ok" | "warn" | "fault" | "info"
    conformance_pct: float        # % of applicable intervals that conformed (NaN if n/a)
    n_applicable: int             # intervals where the gate held and inputs were present
    summary: str

    def as_dict(self) -> dict:
        """Return the clause result as a plain dict."""
        return asdict(self)


@dataclass
class SOOReport:
    """Conformance across a whole sequence for one equipment."""

    equip: str
    clauses: list = field(default_factory=list)   # list[ClauseResult]
    overall_conformance: float = float("nan")     # mean over assessable clauses
    severity: str = "info"                        # worst clause severity

    def as_dict(self) -> dict:
        """Return the report (with nested clause results) as a plain dict."""
        return {"equip": self.equip,
                "clauses": [c.as_dict() for c in self.clauses],
                "overall_conformance": self.overall_conformance,
                "severity": self.severity}


def evaluate_clause(frame: pd.DataFrame, clause: Clause) -> ClauseResult:
    """Measure one clause's conformance over a role-frame."""
    e_valid, e_holds = clause.expect.evaluate(frame)
    if e_valid is None:
        return ClauseResult(clause.name, "info", float("nan"), 0,
                            f"{clause.name}: expectation role(s) not present")

    if clause.when is None:
        gate_true = pd.Series(True, index=frame.index)
    else:
        w_valid, w_holds = clause.when.evaluate(frame)
        if w_valid is None:
            return ClauseResult(clause.name, "info", float("nan"), 0,
                                f"{clause.name}: gate role(s) not present")
        gate_true = w_holds   # already bool: True only where the gate is valid and met

    applicable = gate_true & e_valid
    n_app = int(applicable.sum())
    if n_app < clause.min_samples:
        return ClauseResult(clause.name, "info", float("nan"), n_app,
                            f"{clause.name}: only {n_app} applicable intervals "
                            f"(< {clause.min_samples})")

    holds = int((e_holds & applicable).sum())
    conf = round(100.0 * holds / n_app, 1)
    if conf < clause.fault_below:
        severity = "fault"
    elif conf < clause.warn_below:
        severity = "warn"
    else:
        severity = "ok"

    gate_txt = f"when {clause.when.describe()}, " if clause.when else ""
    return ClauseResult(
        name=clause.name,
        severity=severity,
        conformance_pct=conf,
        n_applicable=n_app,
        summary=(f"{clause.name}: {conf:.0f}% conformance over {n_app} intervals "
                 f"[{gate_txt}expect {clause.expect.describe()}]"),
    )


def evaluate_soo(frame: pd.DataFrame, spec, equip: str = "") -> SOOReport:
    """Evaluate a sequence (list of clauses) over one equipment's role-frame."""
    results = [evaluate_clause(frame, c) for c in spec]
    assessable = [r.conformance_pct for r in results
                  if r.conformance_pct == r.conformance_pct]   # drop NaN (info)
    overall = round(float(np.mean(assessable)), 1) if assessable else float("nan")
    order = {"ok": 0, "info": 0, "warn": 1, "fault": 2}
    severity = max((r.severity for r in results), key=lambda s: order[s], default="info")
    return SOOReport(equip=equip, clauses=results, overall_conformance=overall,
                     severity=severity)


def soo_findings(frame: pd.DataFrame, spec, equip: str) -> list:
    """Evaluate a sequence and return one :class:`Finding` per assessed clause.

    Lets SOO conformance flow through the same prioritization / reporting / triage as
    the rule library. Non-assessable clauses (missing roles / too few intervals) are
    emitted as ``info`` findings so coverage gaps are visible, not silent.
    """
    out = []
    for r in evaluate_soo(frame, spec, equip).clauses:
        out.append(Finding(
            rule=f"soo:{r.name}",
            equip=equip,
            severity=r.severity,
            metrics={"conformance_pct": r.conformance_pct, "n_applicable": r.n_applicable},
            summary=r.summary,
        ))
    return out


# --- JSON / config authoring -------------------------------------------------- #

def predicate_from_dict(d: dict) -> Predicate:
    """Build a :class:`Predicate` from a plain dict (JSON config)."""
    return Predicate(subject=d["subject"], op=d["op"], value=d.get("value"),
                     ref=d.get("ref"), tol=d.get("tol", 0.5))


def clause_from_dict(d: dict) -> Clause:
    """Build a :class:`Clause` from a plain dict (JSON config).

    Shape: ``{"name", "expect": {...}, "when": {...}?, "fault_below"?, "warn_below"?,
    "min_samples"?}``.
    """
    return Clause(
        name=d["name"],
        expect=predicate_from_dict(d["expect"]),
        when=predicate_from_dict(d["when"]) if d.get("when") else None,
        fault_below=d.get("fault_below", 80.0),
        warn_below=d.get("warn_below", 95.0),
        min_samples=d.get("min_samples", 10),
    )


def spec_from_dicts(items) -> list:
    """Build a sequence (list of clauses) from a list of dicts."""
    return [clause_from_dict(d) for d in items]
