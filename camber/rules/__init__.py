"""Rules: fault-detection rules (reheat, simultaneous H/C, setback, resets, plant) and triage.

``__all__`` is the curated framework surface — the ``Registry`` and its entry points, the
``Finding``/``Rule`` types, and triage. The individual ``*_rule`` diagnostics are discovered
through :func:`builtin_registry`, not imported by name, so they are not re-exported here
(they remain importable by path, e.g. ``camber.rules.reheat_rule``).
"""

from .base import Finding, FleetRule, PeriodRule, Registry, Rule
from .builtin import builtin_registry, is_fleet, rule_names
from .triage import (
    SEVERITY_ORDER,
    FaultRegister,
    Ranked,
    RootCauseGroup,
    group_findings,
    impact_score,
    rank_findings,
)

__all__ = [
    "Finding",
    "Rule",
    "FleetRule",
    "PeriodRule",
    "Registry",
    "builtin_registry",
    "rule_names",
    "is_fleet",
    "rank_findings",
    "Ranked",
    "impact_score",
    "group_findings",
    "RootCauseGroup",
    "FaultRegister",
    "SEVERITY_ORDER",
]
