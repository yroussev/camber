"""FDD rule framework: Rule protocol, Finding result, and a registry/runner.

A rule is a self-contained diagnostic: it declares which :class:`Role` inputs it
needs, runs over one equipment's role-named frame, and returns a :class:`Finding`
(a structured result with metrics and a severity). Because rules consume
role-frames (not filenames or vendor tokens), one rule runs on any building once
its tags are mapped.

The registry maps rule name -> Rule and the runner applies rules across all
discovered equipment, skipping any equipment missing a rule's required roles.

Declaring what you couldn't evaluate (honesty convention)
---------------------------------------------------------
A rule must **never assert a negative it did not test.** When an absent optional input
makes a sub-check impossible, the analysis layer represents that sub-check as ``None``
(tri-state ``bool | None`` / ``float | None``), never a sentinel (``nan``/``False``/``0``)
that silently collapses into an asserted negative. The rule then:

1. **excludes** a ``None`` sub-check from the severity decision (test ``is False`` / ``is
   None`` -- never ``not x``, which reads ``True`` for ``None``);
2. writes the metric as ``None`` (an honest JSON ``null``), not ``False``;
3. phrases the summary without the untested clause; and
4. appends a **caveat** to :attr:`Finding.caveats` naming what wasn't evaluated and why.

As a backstop, :meth:`Registry.run` records any missing optional roles on every Finding
(``metrics["_missing_optional"]``) so the whole class is visible without reading each rule.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Protocol, runtime_checkable

import pandas as pd

from ..model.entities import missing_inputs, roles_any_of
from ..model.mapping import MappingProvider
from ..resolve import resolve
from ..sensorhealth import untrusted_roles


@dataclass
class Finding:
    """The structured result of running one rule on one equipment.

    ``caveats`` carries human-readable "could not evaluate X" notes. A rule appends one
    whenever a missing optional input made a sub-check impossible, so the rule can *decline*
    that sub-check (an honest ``None`` metric + a caveat) rather than assert a false negative.
    See the "declaring what you couldn't evaluate" convention below.
    """

    rule: str
    equip: str
    severity: str  # "ok" | "info" | "warn" | "fault"
    metrics: dict = field(default_factory=dict)
    summary: str = ""
    evidence: dict | None = None  # optional JSON-friendly evidence descriptor (pattern J)
    caveats: list = field(default_factory=list)  # "could not evaluate X" notes (see convention)

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

    0.98 (#86): a rule may also declare ``roles_any_of`` (optional, provisional), a tuple of role
    groups of which each needs **at least one** role present -- e.g.
    ``((Role.BOILER_STATUS, Role.GAS_INPUT_RATE),)`` for "a run status or a gas input". Every role
    in a group is loaded when present; a group with none present blocks the rule like a missing
    required role
    (:func:`camber.model.entities.missing_inputs` is the one test the runners and
    :func:`camber.model.entities.runnable_rules` share).
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

    def analyze_fleet(self, frames: dict, *, topology=None) -> Finding:
        """Run over {equip: role-frame}; return one aggregate Finding.

        ``topology`` is an optional :class:`camber.model.topology.Topology`; grouping-aware fleet
        rules (e.g. the rogue-zone census) use it to scope per system, others ignore it.
        """
        ...


@runtime_checkable
class PeriodRule(Protocol):
    """A diagnostic that compares a **baseline** window against a **current** window.

    :class:`Rule` collapses one frame to a verdict, so it can see a metric's *level* but never
    its *trajectory*. A PeriodRule receives two explicitly-bounded slices of the same equipment
    and reports how the current one differs from the baseline -- the shape a drift alert needs.

    This is a **separate, optional protocol**: :class:`Rule` is unchanged, and a PeriodRule is
    run by :meth:`Registry.run_periods` rather than :meth:`Registry.run`. A rule may implement
    both, in which case ordinary single-frame pipelines keep working unmodified.
    """

    name: str
    roles_required: tuple
    roles_optional: tuple

    def analyze_periods(
        self, equip: str, baseline: pd.DataFrame, current: pd.DataFrame
    ) -> Finding: ...


def _roles_to_load(rule) -> tuple:
    """Required + optional (+ any-of) roles a rule wants resolved (only required must exist)."""
    load = tuple(rule.roles_required) + tuple(getattr(rule, "roles_optional", ()))
    extra = [r for g in roles_any_of(rule) for r in g if r not in load]
    return load + tuple(dict.fromkeys(extra)) if extra else load


def _gate_roles(rule, frame: pd.DataFrame) -> tuple:
    """The roles the sensor-health gate judges: required, plus the any-of roles present."""
    req = tuple(rule.roles_required)
    extra = [r for g in roles_any_of(rule) for r in g if r in frame.columns and r not in req]
    return req + tuple(dict.fromkeys(extra)) if extra else req


def _cannot_run(rule, frame: pd.DataFrame) -> bool:
    """True when the resolved frame is empty or lacks a required role or an any-of group."""
    if frame.empty:
        return True
    req, groups = missing_inputs(rule, frame.columns)
    return bool(req or groups)


def _missing_optional(rule, frame: pd.DataFrame) -> list:
    """Optional roles the rule declared but that aren't present on the resolved frame."""
    return [r for r in getattr(rule, "roles_optional", ()) if r not in frame.columns]


def _note_missing_optional(finding, missing) -> None:
    """Record absent optional roles on a Finding (backstop for the honesty convention)."""
    if finding is not None and missing:
        finding.metrics.setdefault("_missing_optional", [getattr(r, "value", r) for r in missing])


def _heuristic_topology(equip_refs):
    """A naming-heuristic served-by graph from the fleet's equip refs (provenance="heuristic").

    The refs carry ``equip`` (id) and ``equip_class`` but not the ``id``/``space`` shape
    :func:`camber.topology_infer.topology_from_naming` reads, so each is shimmed into a light shim.
    """
    from types import SimpleNamespace

    from ..topology_infer import topology_from_naming

    shims = [
        SimpleNamespace(id=r.equip, equip_class=r.equip_class, space=getattr(r, "space", ""))
        for r in equip_refs
    ]
    return topology_from_naming(shims)


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


def _name_match(classes, cls: str) -> bool:
    """The 0.90.1 spelling match: equal ignoring case, or a longer class *containing* one of the
    names (``"AHU_DOAS"`` matches ``"AHU"``). Names shorter than three letters must match whole."""
    up = cls.upper()
    for c in classes:
        cu = str(c).upper()
        if cu == up or (len(cu) >= 3 and cu in up):
            return True
    return False


def _class_verdict(rule, ref):
    """``(applies, classes)`` for a rule's equipment-class gate on ``ref``.

    ``applies`` is ``True`` (the rule is written for this class, or declares no classes, or the
    equipment records none), ``False`` (a recognised class outside the rule's families) or
    ``None`` (a class :func:`camber.model.equipclass.equip_family` does not recognise -- the rule
    then runs on its roles alone). ``classes`` is what the rule declares (``None`` when nothing).
    """
    from ..model.equipclass import family_matches
    from .applicability import rule_equip_classes

    classes = rule_equip_classes(rule)
    cls = str(getattr(ref, "equip_class", "") or "")
    if not classes or not cls or _name_match(classes, cls):
        return True, classes
    return family_matches(cls, classes), classes


def _class_declined(rule, ref):
    """A declined Finding when ``rule`` applies to other classes than ``ref``'s; else None.

    Rules are gated by roles, so a rule written for air handlers would otherwise run on any
    equipment that happens to carry the same roles (a heat pump's or a VAV box's discharge air
    read as supply air). What a rule applies to is its optional, provisional ``equip_classes``
    attribute, else its entry in :data:`camber.rules.applicability.RULE_EQUIP_CLASSES`. A class
    matches by spelling (case-insensitive; a class *containing* one of the names also matches,
    ``"AHU_DOAS"``) or by family (:mod:`camber.model.equipclass`: ``"RTU"`` and ``"AHU"`` are both
    air handlers). An equipment with no recorded class, or a class no family recognises, is not
    declined.
    """
    applies, classes = _class_verdict(rule, ref)
    if applies is not False:
        return None
    from ..model.equipclass import equip_family

    cls = str(ref.equip_class)
    names = ", ".join(str(c) for c in classes)
    return Finding(
        rule=rule.name,
        equip=ref.equip,
        severity="info",
        metrics={
            "declined": True,
            "reason": f"not applicable to equipment class {cls!r}",
            "equip_class": cls,
            "equip_family": equip_family(cls),
            "applies_to_classes": [str(c) for c in classes],
        },
        summary=f"{ref.equip}: declined -- {rule.name} applies to {names}, not {cls}",
        caveats=[
            f"{rule.name} not evaluated: it is written for {names} equipment and this is a {cls}"
        ],
    )


def _class_unrecognised_note(rule, ref) -> str | None:
    """A caveat when a class-gated rule ran on equipment whose class no family recognises."""
    applies, classes = _class_verdict(rule, ref)
    if applies is not None:
        return None
    names = ", ".join(str(c) for c in classes)
    return (
        f"equipment class {ref.equip_class!r} is not a recognised class; {rule.name} (written for "
        f"{names}) ran on its roles alone"
    )


def _note_unrecognised(finding, rule, ref) -> None:
    note = _class_unrecognised_note(rule, ref)
    if finding is not None and note and note not in finding.caveats:
        finding.caveats.append(note)


def _as_bound(value):
    """Coerce one period endpoint to a Timestamp; ``None`` stays open-ended."""
    return None if value is None else pd.Timestamp(value)


def _slice_period(frame: pd.DataFrame, period, *, label: str) -> pd.DataFrame:
    """Slice a time-indexed frame to an explicit ``(start, end)`` pair (both bounds inclusive).

    Periods are **explicit** by design: a rolling "last N days vs the N before" convention would
    make the reference window implicit and unauditable, and would silently move every time the
    analysis re-runs. Either endpoint may be ``None`` for an open-ended side.
    """
    try:
        start, end = period
    except (TypeError, ValueError):
        raise ValueError(f"{label} period must be a (start, end) pair, got {period!r}") from None
    lo, hi = _as_bound(start), _as_bound(end)
    if lo is not None and hi is not None and lo > hi:
        raise ValueError(f"{label} period start {lo} is after its end {hi}")
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise TypeError(f"run_periods needs a time-indexed frame, got {type(frame.index).__name__}")
    return frame.loc[lo:hi]


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

    def run(
        self,
        rule_name: str,
        equip_refs,
        mapping: MappingProvider,
        *,
        resample: str = "1h",
        shared=None,
        min_trust=None,
    ) -> list[Finding]:
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

        ``roles_any_of`` groups (0.98) gate like required roles: each needs one role present, and
        the present ones join the sensor-health gate.
        """
        rule = self.get(rule_name)
        load = _roles_to_load(rule)
        out: list[Finding] = []
        for ref in equip_refs:
            frame = resolve(ref, mapping, load, resample=resample)
            frame = _merge_shared(frame, shared)
            if _cannot_run(rule, frame):
                continue
            declined = _class_declined(rule, ref)
            if declined is not None:
                out.append(declined)
                continue
            if min_trust is not None:
                bad = untrusted_roles(frame, _gate_roles(rule, frame), min_trust=min_trust)
                if bad:
                    out.append(
                        Finding(
                            rule=rule.name,
                            equip=ref.equip,
                            severity="info",
                            metrics={
                                "declined": True,
                                "min_trust": min_trust,
                                "untrusted_roles": [r.value for r in bad],
                            },
                            summary=(
                                f"{ref.equip}: declined -- untrusted input(s): "
                                + ", ".join(r.value for r in bad)
                            ),
                        )
                    )
                    continue
            f = rule.analyze(ref.equip, frame)
            _note_missing_optional(f, _missing_optional(rule, frame))
            _note_unrecognised(f, rule, ref)
            if f is not None:
                out.append(f)
        return out

    def run_periods(
        self,
        rule_name: str,
        equip_refs,
        mapping: MappingProvider,
        *,
        baseline,
        current,
        resample: str = "1h",
        shared=None,
        min_trust=None,
    ) -> list[Finding]:
        """Run a :class:`PeriodRule` across equipment, comparing two explicit time windows.

        ``baseline`` and ``current`` are each an explicit ``(start, end)`` pair -- timestamps,
        ISO strings, or ``None`` for an open-ended side. Each equipment is resolved **once** over
        its full history and then sliced twice, so the two windows always come from one consistent
        load and resample.

        Equipment whose resolved frame lacks any required role is skipped, exactly as in
        :meth:`run`. ``shared`` and ``min_trust`` behave identically too -- an untrusted required
        input makes the rule decline with an ``info`` finding rather than report an equipment
        fault. When a window resolves to no rows the rule also **declines**, with a caveat naming
        the empty window: a chiller silently dropped from a drift report reads as "no drift",
        which is precisely the false negative the honesty convention forbids.
        """
        rule = self.get(rule_name)
        load = _roles_to_load(rule)
        out: list[Finding] = []
        for ref in equip_refs:
            frame = resolve(ref, mapping, load, resample=resample)
            frame = _merge_shared(frame, shared)
            if _cannot_run(rule, frame):
                continue
            declined = _class_declined(rule, ref)
            if declined is not None:
                out.append(declined)
                continue
            if min_trust is not None:
                bad = untrusted_roles(frame, _gate_roles(rule, frame), min_trust=min_trust)
                if bad:
                    out.append(
                        Finding(
                            rule=rule.name,
                            equip=ref.equip,
                            severity="info",
                            metrics={
                                "declined": True,
                                "min_trust": min_trust,
                                "untrusted_roles": [r.value for r in bad],
                            },
                            summary=(
                                f"{ref.equip}: declined -- untrusted input(s): "
                                + ", ".join(r.value for r in bad)
                            ),
                        )
                    )
                    continue
            base_frame = _slice_period(frame, baseline, label="baseline")
            cur_frame = _slice_period(frame, current, label="current")
            empty = [n for n, fr in (("baseline", base_frame), ("current", cur_frame)) if fr.empty]
            if empty:
                out.append(
                    Finding(
                        rule=rule.name,
                        equip=ref.equip,
                        severity="info",
                        metrics={"declined": True, "empty_periods": empty},
                        summary=f"{ref.equip}: declined -- no data in the {'/'.join(empty)} period",
                        caveats=[f"could not evaluate drift: {'/'.join(empty)} window has no rows"],
                    )
                )
                continue
            f = rule.analyze_periods(ref.equip, base_frame, cur_frame)  # type: ignore[attr-defined]
            _note_missing_optional(f, _missing_optional(rule, frame))
            _note_unrecognised(f, rule, ref)
            if f is not None:
                out.append(f)
        return out

    def run_fleet(
        self,
        rule_name: str,
        equip_refs,
        mapping: MappingProvider,
        *,
        resample: str = "1h",
        shared=None,
        min_trust=None,
        topology=None,
    ) -> Finding:
        """Run a FleetRule: resolve every equipment, pass the set as one batch.

        Equipment missing the required roles are skipped; the rule sees only those
        with usable data. ``shared`` (building-level {Role: Series}) is merged into
        each frame as in :meth:`run`. ``min_trust`` applies the same sensor-health gate
        per equipment: a unit whose required inputs aren't trusted is left out of the
        fleet batch rather than corrupting the aggregate.

        ``topology`` is an optional :class:`camber.model.topology.Topology` handed to the rule
        so a grouping-aware analytic can scope per system (e.g. the rogue-zone census per air
        handler). When it is ``None`` and the rule opts in via ``wants_topology``, a naming
        served-by graph is auto-built from the ``equip_refs`` -- so the census auto-scopes even
        with no semantic model (that heuristic grouping carries a screening caveat downstream).
        """
        rule = self.get(rule_name)
        load = _roles_to_load(rule)
        frames = {}
        other_class: dict = {}  # equip -> class, for equipment the rule is not written for
        unrecognised: list = []
        for ref in equip_refs:
            applies, _classes = _class_verdict(rule, ref)
            if applies is False:  # decided before resolving: no data is read for it
                other_class[ref.equip] = str(ref.equip_class)
                continue
            frame = resolve(ref, mapping, load, resample=resample)
            frame = _merge_shared(frame, shared)
            if _cannot_run(rule, frame):
                continue
            if min_trust is not None and untrusted_roles(
                frame, _gate_roles(rule, frame), min_trust=min_trust
            ):
                continue
            frames[ref.equip] = frame
            if applies is None:
                unrecognised.append(ref)
        if topology is None and getattr(rule, "wants_topology", False):
            topology = _heuristic_topology(equip_refs)
        f = rule.analyze_fleet(frames, topology=topology)  # type: ignore[attr-defined]  # fleet only
        if f is not None and other_class:
            f.metrics.setdefault(
                "_class_excluded",
                {"n": len(other_class), "classes": sorted(set(other_class.values()))},
            )
        if f is not None and unrecognised:
            classes = sorted({str(r.equip_class) for r in unrecognised})
            note = (
                f"equipment of unrecognised class(es) {', '.join(classes)} entered {rule.name} on "
                "roles alone"
            )
            if note not in f.caveats:
                f.caveats.append(note)
        # backstop: optional roles that were present on no equipment at all
        if frames:
            never = [
                r
                for r in getattr(rule, "roles_optional", ())
                if all(r not in fr.columns for fr in frames.values())
            ]
            _note_missing_optional(f, never)
        return f
