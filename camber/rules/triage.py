"""Fault prioritization and lifecycle tracking.

Detection is the easy part; the value is a short, ranked list of what to fix and
knowing which faults are new, still open, or resolved. This module:

- **ranks** findings by impact (severity first, then an optional magnitude metric),
  so an operator sees the worst handful rather than a flat wall of flags; and
- **tracks lifecycle** across runs via a stable (site, equip, rule) fingerprint,
  classifying each fault as new / ongoing / resolved.

Findings are duck-typed (``severity`` / ``equip`` / ``rule`` / ``metrics``), so any
finding-like object works.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..integrate.tickets import _attr, fingerprint

# Higher = worse; drives the primary ranking order.
SEVERITY_ORDER = {"fault": 3, "warn": 2, "info": 1, "ok": 0}
_ACTIONABLE = frozenset({"fault", "warn"})


@dataclass(frozen=True)
class Ranked:
    """A finding with its computed impact score and 1-based rank."""

    finding: object
    severity: str
    magnitude: float
    score: float
    rank: int


def impact_score(finding, *, magnitude_key: str | None = None) -> float:
    """Impact score: severity dominates; an optional metric scales within a tier.

    ``magnitude_key`` names a metric (e.g. a waste/percentage field) whose value
    orders findings of equal severity. The severity term is weighted so a higher
    severity always outranks a lower one regardless of magnitude.
    """
    sev = SEVERITY_ORDER.get(_attr(finding, "severity", "info"), 1)
    mag = 0.0
    if magnitude_key:
        m = (_attr(finding, "metrics", {}) or {}).get(magnitude_key)
        if isinstance(m, (int, float)):
            mag = float(m)
    return sev * 1e9 + mag


def rank_findings(findings, *, magnitude_key: str | None = None,
                  actionable_only: bool = False) -> list:
    """Rank findings worst-first. Returns :class:`Ranked` items with 1-based rank."""
    items = list(findings)
    if actionable_only:
        items = [f for f in items if _attr(f, "severity", "info") in _ACTIONABLE]
    scored = []
    for f in items:
        sev = _attr(f, "severity", "info")
        mag = 0.0
        if magnitude_key:
            m = (_attr(f, "metrics", {}) or {}).get(magnitude_key)
            if isinstance(m, (int, float)):
                mag = float(m)
        scored.append((impact_score(f, magnitude_key=magnitude_key), sev, mag, f))
    scored.sort(key=lambda t: -t[0])
    return [Ranked(finding=f, severity=sev, magnitude=mag, score=round(s, 4),
                   rank=i + 1)
            for i, (s, sev, mag, f) in enumerate(scored)]


@dataclass
class FaultRegister:
    """Tracks open faults across runs and classifies new / ongoing / resolved.

    Call :meth:`update` once per analysis run with that run's findings. A fault is
    keyed by its (site, equip, rule) fingerprint; ``run_id`` is any orderable label
    (timestamp, integer, date string) stamped as first/last seen.
    """

    _open: dict = field(default_factory=dict)   # fingerprint -> {site,equip,rule,first_seen,last_seen}

    def open_faults(self) -> dict:
        """Snapshot of currently open faults keyed by fingerprint."""
        return dict(self._open)

    def update(self, findings, *, site: str = "", run_id=None,
               actionable=_ACTIONABLE) -> dict:
        """Fold in one run; return ``{"new":[...], "ongoing":[...], "resolved":[...]}``
        as lists of fingerprints."""
        now = {}
        for f in findings:
            if _attr(f, "severity", "info") in actionable:
                fp = fingerprint(site, _attr(f, "equip", ""), _attr(f, "rule", ""))
                now[fp] = {"site": site, "equip": _attr(f, "equip", ""),
                           "rule": _attr(f, "rule", "")}
        prev = set(self._open)
        cur = set(now)
        new, ongoing, resolved = cur - prev, cur & prev, prev - cur
        for fp in new:
            self._open[fp] = {**now[fp], "first_seen": run_id, "last_seen": run_id}
        for fp in ongoing:
            self._open[fp]["last_seen"] = run_id
        for fp in resolved:
            del self._open[fp]
        return {"new": sorted(new), "ongoing": sorted(ongoing),
                "resolved": sorted(resolved)}


# --------------------------------------------------------------------------- #
# Root-cause grouping
#
# Detection produces many findings; diagnosis means relating co-occurring ones to
# a likely single cause. Known causal chains order rules from upstream root to
# downstream symptom on the same equipment. The canonical air-side chain: a
# supply-air-temperature reset that's missing/too-low overcools zones, which forces
# terminal reheat, which shows up as simultaneous heating and cooling at the AHU.
# Findings on one equipment that fall in the same chain are grouped, with the most
# upstream as the presumed root cause.
# --------------------------------------------------------------------------- #

CAUSE_CHAINS = [
    ("overcool_reheat", ["supply_air_reset", "overcooling_min_flow",
                         "reheat_minimization", "reheat_penalty",
                         "simultaneous_heat_cool"]),
]
# rule name -> (chain_id, position) where position 0 is the most upstream
_CHAIN_POS = {rule: (cid, i)
              for cid, rules in CAUSE_CHAINS for i, rule in enumerate(rules)}


@dataclass(frozen=True)
class RootCauseGroup:
    """A cluster of related findings on one equipment with a presumed root cause."""

    equip: str
    primary_rule: str        # the most-upstream rule present (presumed root cause)
    severity: str            # worst severity among the grouped findings
    members: list            # findings, ordered root-cause first
    summary: str


def group_findings(findings, *, actionable_only: bool = True) -> list:
    """Cluster co-occurring findings on each equipment into root-cause groups.

    Findings whose rules share a known causal chain (see :data:`CAUSE_CHAINS`) and
    sit on the same equipment are grouped; the most upstream is the presumed root
    cause. Unrelated findings each form their own single-member group. Groups are
    returned worst-severity first.
    """
    items = [f for f in findings
             if (not actionable_only)
             or _attr(f, "severity", "info") in _ACTIONABLE]
    buckets = {}
    for f in items:
        equip = _attr(f, "equip", "")
        rule = _attr(f, "rule", "")
        cid = _CHAIN_POS.get(rule, (None, None))[0]
        key = (equip, cid) if cid else (equip, f"solo:{rule}")
        buckets.setdefault(key, []).append(f)

    def _pos(f):
        return _CHAIN_POS.get(_attr(f, "rule", ""), (None, 99))[1] or 0

    groups = []
    for (equip, _key), fs in buckets.items():
        fs_sorted = sorted(fs, key=_pos)
        primary = fs_sorted[0]
        sev = max((_attr(f, "severity", "info") for f in fs),
                  key=lambda s: SEVERITY_ORDER.get(s, 1))
        others = [_attr(f, "rule", "") for f in fs_sorted[1:]]
        if others:
            summary = (f"{equip}: likely root cause '{_attr(primary, 'rule', '')}' "
                       f"with {len(others)} related symptom(s): {', '.join(others)}")
        else:
            summary = f"{equip}: {_attr(primary, 'rule', '')}"
        groups.append(RootCauseGroup(
            equip=equip, primary_rule=_attr(primary, "rule", ""), severity=sev,
            members=fs_sorted, summary=summary))
    groups.sort(key=lambda g: (-SEVERITY_ORDER.get(g.severity, 1), -len(g.members)))
    return groups
