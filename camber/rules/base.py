"""FDD rule framework: Rule protocol, Finding result, and a registry/runner.

A rule is a self-contained diagnostic: it declares which :class:`Role` inputs it
needs, runs over one equipment's role-named frame, and returns a :class:`Finding`
(a structured result with metrics and a severity). Because rules consume
role-frames (not filenames or vendor tokens), one rule runs on any building once
its tags are mapped.

The registry maps rule name -> Rule and the runner applies rules across all
discovered equipment, skipping any equipment missing a rule's required roles.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Protocol, runtime_checkable

import pandas as pd

from ..model.roles import Role
from ..resolve import EquipRef, resolve, occupied
from ..model.mapping import MappingProvider
from ..sensorhealth import untrusted_roles


@dataclass
class Finding:
    """The structured result of running one rule on one equipment."""

    rule: str
    equip: str
    severity: str                 # "ok" | "info" | "warn" | "fault"
    metrics: dict = field(default_factory=dict)
    summary: str = ""

    def as_dict(self):
        """Return the finding as a plain dict (JSON/report friendly)."""
        return asdict(self)


@runtime_checkable
class Rule(Protocol):
    """A diagnostic that maps required roles -> a Finding for one equipment.

    ``roles_required`` gate whether the rule can run at all; ``roles_optional``
    enrich it (e.g. OAT enables the high-OAT reheat indicator) and are loaded when
    present but never block the rule. Rules may omit ``roles_optional`` (treated
    as empty) -- the runner reads it via ``getattr``.
    """

    name: str
    roles_required: tuple  # tuple[Role, ...]
    roles_optional: tuple  # tuple[Role, ...]  (optional attribute; default ())

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on a role-named ``frame``; return a Finding."""
        ...


@runtime_checkable
class FleetRule(Protocol):
    """A diagnostic over *many* equipment at once (e.g. a fleet census).

    Unlike :class:`Rule` (one equipment in, one Finding out), a FleetRule takes a
    mapping of equip -> role-frame and returns a single aggregate Finding.
    """

    name: str
    roles_required: tuple
    roles_optional: tuple

    def analyze_fleet(self, frames: dict) -> Finding:
        """Run over {equip: role-frame}; return one aggregate Finding."""
        ...


def _roles_to_load(rule) -> tuple:
    """Required + optional roles a rule wants resolved (optional may be absent)."""
    return tuple(rule.roles_required) + tuple(getattr(rule, "roles_optional", ()))


def _merge_shared(frame: pd.DataFrame, shared) -> pd.DataFrame:
    """Add building-level {Role: Series} columns to a per-equipment role frame.

    A shared series (e.g. one OAT sensor for the whole building) is reindexed onto
    the frame's time grid. Per-equipment columns win: a role already present on the
    frame is left untouched.
    """
    if not shared or frame is None or frame.empty:
        return frame
    out = frame.copy()
    for role, series in shared.items():
        if role not in out.columns:
            out[role] = series.reindex(out.index).ffill(limit=4)
    return out


class Registry:
    """Name -> Rule, plus a runner that applies rules across discovered equipment."""

    def __init__(self):
        self._rules: dict[str, Rule] = {}

    def register(self, rule: Rule) -> Rule:
        """Register a rule under its ``name``; return it (usable as a decorator)."""
        self._rules[rule.name] = rule
        return rule

    def get(self, name: str) -> Rule:
        """Look up a registered rule by name."""
        return self._rules[name]

    def names(self) -> list[str]:
        """Sorted list of registered rule names."""
        return sorted(self._rules)

    def run(self, rule_name: str, equip_refs, mapping: MappingProvider, *,
            resample: str = "1h", shared=None, min_trust=None) -> list[Finding]:
        """Run one rule across equipment, resolving each to a role-frame first.

        Equipment whose resolved frame lacks any required role is skipped (the
        rule simply can't apply there). ``shared`` is an optional {Role: Series}
        of building-level points (e.g. a single OAT sensor) merged into every
        equipment frame -- the role layer's answer to points that aren't carried
        per-equipment.

        ``min_trust`` (0..1) enables the sensor-health gate: if any of the rule's
        required roles scores below it on the resolved frame, the rule **declines to
        fire** and instead records an ``info`` finding naming the untrusted input -- so
        a fault that is really a sensor problem isn't reported as an equipment fault.
        """
        rule = self.get(rule_name)
        load = _roles_to_load(rule)
        out: list[Finding] = []
        for ref in equip_refs:
            frame = resolve(ref, mapping, load, resample=resample)
            frame = _merge_shared(frame, shared)
            if frame.empty or any(r not in frame.columns for r in rule.roles_required):
                continue
            if min_trust is not None:
                bad = untrusted_roles(frame, rule.roles_required, min_trust=min_trust)
                if bad:
                    out.append(Finding(
                        rule=rule.name, equip=ref.equip, severity="info",
                        metrics={"declined": True, "min_trust": min_trust,
                                 "untrusted_roles": [r.value for r in bad]},
                        summary=(f"{ref.equip}: declined -- untrusted input(s): "
                                 + ", ".join(r.value for r in bad))))
                    continue
            f = rule.analyze(ref.equip, frame)
            if f is not None:
                out.append(f)
        return out

    def run_fleet(self, rule_name: str, equip_refs, mapping: MappingProvider, *,
                  resample: str = "1h", shared=None, min_trust=None) -> Finding:
        """Run a FleetRule: resolve every equipment, pass the set as one batch.

        Equipment missing the required roles are skipped; the rule sees only those
        with usable data. ``shared`` (building-level {Role: Series}) is merged into
        each frame as in :meth:`run`. ``min_trust`` applies the same sensor-health gate
        per equipment: a unit whose required inputs aren't trusted is left out of the
        fleet batch rather than corrupting the aggregate.
        """
        rule = self.get(rule_name)
        load = _roles_to_load(rule)
        frames = {}
        for ref in equip_refs:
            frame = resolve(ref, mapping, load, resample=resample)
            frame = _merge_shared(frame, shared)
            if frame.empty or any(r not in frame.columns for r in rule.roles_required):
                continue
            if min_trust is not None and untrusted_roles(frame, rule.roles_required,
                                                         min_trust=min_trust):
                continue
            frames[ref.equip] = frame
        return rule.analyze_fleet(frames)
