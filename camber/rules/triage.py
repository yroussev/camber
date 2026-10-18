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


def rank_findings(
    findings, *, magnitude_key: str | None = None, actionable_only: bool = False
) -> list:
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
    return [
        Ranked(finding=f, severity=sev, magnitude=mag, score=round(s, 4), rank=i + 1)
        for i, (s, sev, mag, f) in enumerate(scored)
    ]


@dataclass
class FaultRegister:
    """Tracks open faults across runs and classifies new / ongoing / resolved.

    Call :meth:`update` once per analysis run with that run's findings. A fault is
    keyed by its (site, equip, rule) fingerprint; ``run_id`` is any orderable label
    (timestamp, integer, date string) stamped as first/last seen.
    """

    _open: dict = field(
        default_factory=dict
    )  # fingerprint -> {site,equip,rule,first_seen,last_seen}

    def open_faults(self) -> dict:
        """Snapshot of currently open faults keyed by fingerprint."""
        return dict(self._open)

    def update(
        self, findings, *, site: str = "", run_id=None, actionable=_ACTIONABLE, facility_id=None
    ) -> dict:
        """Fold in one run; return ``{"new":[...], "ongoing":[...], "resolved":[...]}``
        as lists of fingerprints. ``facility_id`` keys the fingerprints by the facility (stable
        across renames) instead of ``site``. Only faults keyed like this run (the same
        ``facility_id``, or without one the same ``site``) can be ``resolved`` by it; a register
        shared by several sites or facilities leaves the others' faults open (0.96, #76)."""
        key = facility_id or site
        now = {}
        for f in findings:
            if _attr(f, "severity", "info") in actionable:
                fp = fingerprint(key, _attr(f, "equip", ""), _attr(f, "rule", ""))
                now[fp] = {
                    "site": site,
                    "equip": _attr(f, "equip", ""),
                    "rule": _attr(f, "rule", ""),
                }
        prev = {
            fp
            for fp, o in self._open.items()
            if fp == fingerprint(key, o.get("equip", ""), o.get("rule", ""))
        }
        cur = set(now)
        new, ongoing, resolved = cur - prev, cur & prev, prev - cur
        for fp in new:
            self._open[fp] = {**now[fp], "first_seen": run_id, "last_seen": run_id}
        for fp in ongoing:
            self._open[fp]["last_seen"] = run_id
        for fp in resolved:
            del self._open[fp]
        return {"new": sorted(new), "ongoing": sorted(ongoing), "resolved": sorted(resolved)}


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
#
# Every member must be a rule name some registry actually emits (a test holds this): before
# 0.88 the SAT chain listed "reheat_minimization", but the rule is "reheat_minimization_g36",
# so that link silently never grouped.
# --------------------------------------------------------------------------- #

CAUSE_CHAINS = [
    (
        # SAT held too cold -> zones overcool at minimum flow -> terminal reheat fights it ->
        # the AHU sees simultaneous heating and cooling.
        "sat",
        [
            "supply_air_reset",
            "supply_air_reset_compliance",
            "overcooling_severity",
            "overcooling_min_flow",
            "reheat_penalty",
            "reheat_minimization_g36",
            "simultaneous_heat_cool",
        ],
    ),
    (
        # A drifting / stuck OA damper -> no high-limit lockout -> excess OA -> the free cooling
        # the unit should have taken is missed.
        "econ",
        [
            "economizer_damper_drift",
            "economizer_high_limit",
            "outdoor_air_fraction",
            "free_cooling_missed",
        ],
    ),
    (
        # 0.103 (#132): a heating plant not locked out in warm weather -- the boiler enabled (or
        # firing) above the lockout keeps its hot-water pump running too, one fix for both.
        "hw_lockout",
        [
            "boiler_summer_lockout",
            "hw_pump_summer_lockout",
        ],
    ),
    (
        # A static reset that does not trim -> rogue zones pin it -> the damper census shows
        # boxes throttled against excess static.
        "static",
        [
            "static_reset_effectiveness",
            "static_rogue_zone_census",
            "damper_census",
        ],
    ),
]
# rule name -> (chain_id, position) where position 0 is the most upstream
_CHAIN_POS = {rule: (cid, i) for cid, rules in CAUSE_CHAINS for i, rule in enumerate(rules)}


@dataclass(frozen=True)
class RootCauseGroup:
    """A cluster of related findings on one equipment with a presumed root cause."""

    equip: str
    primary_rule: str  # the most-upstream rule present (presumed root cause)
    severity: str  # worst severity among the grouped findings
    members: list  # findings, ordered root-cause first
    summary: str


def group_findings(findings, *, actionable_only: bool = True) -> list:
    """Cluster co-occurring findings on each equipment into root-cause groups.

    Findings whose rules share a known causal chain (see :data:`CAUSE_CHAINS`) and
    sit on the same equipment are grouped; the most upstream is the presumed root
    cause. Unrelated findings each form their own single-member group. Groups are
    returned worst-severity first.
    """
    items = [
        f for f in findings if (not actionable_only) or _attr(f, "severity", "info") in _ACTIONABLE
    ]
    buckets: dict = {}
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
        sev = max(
            (_attr(f, "severity", "info") for f in fs), key=lambda s: SEVERITY_ORDER.get(s, 1)
        )
        others = [_attr(f, "rule", "") for f in fs_sorted[1:]]
        if others:
            summary = (
                f"{equip}: likely root cause '{_attr(primary, 'rule', '')}' "
                f"with {len(others)} related symptom(s): {', '.join(others)}"
            )
        else:
            summary = f"{equip}: {_attr(primary, 'rule', '')}"
        groups.append(
            RootCauseGroup(
                equip=equip,
                primary_rule=_attr(primary, "rule", ""),
                severity=sev,
                members=fs_sorted,
                summary=summary,
            )
        )
    groups.sort(key=lambda g: (-SEVERITY_ORDER.get(g.severity, 1), -len(g.members)))
    return groups


# --------------------------------------------------------------------------- #
# Issue linking (provisional)
#
# ``group_findings`` clusters by causal chain; ``link_findings`` turns those clusters into the
# *issues* a retro-commissioning report ranks: one per root cause, with honest arithmetic over its
# members (hours are a union, a chain's cost is its largest member, never a sum), sensor precedence
# (a finding that leans on a sensor we have reason to distrust is conditional on it -- annotated and
# demoted, never deleted), and a confidence grade with the reasons behind it.
# --------------------------------------------------------------------------- #

#: Roles that measure what every unit on a site shares, so a problem with one is a problem for
#: every equipment that uses it (the building OAT sensor, typically merged into every frame).
SHARED_ROLES = frozenset({"oat", "wetbulb_temp", "outdoor_rh", "outdoor_co2"})

_LEVELS = ("L", "M", "H")  # ordered worst -> best; a confidence grade is the minimum component


@dataclass(frozen=True)
class SensorCause:
    """A reason to distrust one sensor: what was seen, on which equipment and role."""

    kind: str  # "sensor_drift" | "trust" | "mixing"
    equip: str
    roles: tuple  # role slugs this cause taints
    detail: str  # one line, e.g. "sensor_drift:oat fault (bias +6.2)"
    shared: bool = False  # True -> taints every equipment using the role (e.g. the building OAT)
    source: object = field(default=None, compare=False, repr=False)  # the finding, if one

    def label(self) -> str:
        return f"{self.detail} on {self.equip}" if self.equip else self.detail


#: Rules whose finding says the chilled-water plant did not make its supply setpoint while it ran:
#: a capacity (or plant control) problem every air handler it serves inherits.
PLANT_CAPACITY_RULES = ("chw_supply_tracking",)

#: Air-handler rules whose finding can mean "supply air too warm" -- the symptom a starved plant
#: produces downstream. ``supply_air_control`` counts when its too-warm share leads; a G36 fault
#: condition 13 ("SAT too high in full cooling") counts under any rule that names it.
SAT_HIGH_RULES = ("supply_air_control",)

#: G36 §5.16.14 fault conditions that read as "supply air too warm with the cooling coil at full
#: output" -- FC13 only: FC12 (SAT above MAT) also fires on a coil that is simply off, and FC1
#: (duct static) is an airflow fault, neither a plant-capacity symptom.
G36_SAT_HIGH_FCS = ("FC13",)


def is_sat_high(finding) -> bool:
    """True when ``finding`` reports an air handler's supply air too warm (SAT-high / G36 FC13).

    ``supply_air_control`` qualifies when its too-warm share is at least its too-cold share; the
    ``g36_afdd`` rule's finding qualifies when FC13 is among its ``flagged_fcs`` (reported on its
    warn share of enough applicable hours; an older finding without that list, when its
    ``fc["FC13"]`` entry was evaluated at or above the rule's default warn share); any other G36
    finding when its rule name carries ``fc13`` or its metrics name fault condition 13
    (``fault_condition`` / ``fc`` of 13, or a positive ``fc13*`` metric).
    """
    rule = str(_attr(finding, "rule", "") or "")
    m = _attr(finding, "metrics", {}) or {}
    if rule in SAT_HIGH_RULES:
        warm, cold = m.get("too_warm_pct"), m.get("too_cold_pct")
        return isinstance(warm, (int, float)) and warm > 0 and warm >= (cold or 0)
    flagged = m.get("flagged_fcs")
    if isinstance(flagged, (list, tuple)):
        return any(str(x).upper() in G36_SAT_HIGH_FCS for x in flagged)
    fcs = m.get("fc")
    if isinstance(fcs, dict):  # the g36_afdd per-FC table without a flagged list
        from .g36_rule import WARN_PCT

        for label in G36_SAT_HIGH_FCS:
            e = fcs.get(label)
            if isinstance(e, dict) and e.get("status") == "evaluated":
                pct = e.get("pct")
                if isinstance(pct, (int, float)) and pct >= WARN_PCT:
                    return True
        return False
    low = rule.lower().replace("-", "_")
    if "fc13" in low or "fc_13" in low:
        return True
    for k in ("fault_condition", "fc"):
        v = m.get(k)
        if v is not None and str(v).lower().lstrip("fc_") == "13":
            return True
    return any(
        str(k).lower().startswith("fc13")
        and isinstance(v, (int, float))
        and not isinstance(v, bool)
        and v > 0
        for k, v in m.items()
    )


@dataclass(frozen=True)
class UpstreamCause:
    """Upstream equipment that may explain an issue: e.g. a chilled-water plant short of setpoint
    behind an air handler's warm supply air. A conditional *explanation*, never a deletion: the
    downstream finding stands, and the plant is named as the place to look first.

    ``overlap_share`` is the share of the issue's violation hours during which the plant was also
    short, and ``plant_share`` the share of the plant's short hours (with the unit running) during
    which the unit was also in violation -- ``None`` when either side exposes no hours.
    ``overlap_hours`` is the coincident time. ``basis`` says how the two were tied:
    ``"topology"`` (a served-by graph lists the plant upstream of the unit) or ``"site"`` (no
    topology covers the unit, so the site's plant is assumed). ``unit_hours`` (0.92, #67) names
    the unit hours the overlap was taken on: ``"FC13"`` when a G36 finding's own FC13 hours were
    available, ``"finding"`` for the finding's whole violation mask (``supply_air_control``, or a
    G36 finding that exposes only its any-FC union), ``None`` when no overlap was assessed.
    """

    kind: str  # "plant_capacity"
    equip: str  # the upstream equipment
    rule: str  # the upstream finding's rule
    detail: str
    overlap_share: float | None = None
    overlap_hours: float | None = None
    basis: str = "site"
    source: object = field(default=None, compare=False, repr=False)
    plant_share: float | None = None
    unit_hours: str | None = None  # 0.92 (#67): "FC13" | "finding" | None

    def label(self) -> str:
        return f"{self.detail} on {self.equip}"


@dataclass
class Confidence:
    """A finding's confidence grade (H/M/L) with one level + one "why" line per component."""

    level: str
    components: dict = field(default_factory=dict)  # component -> "H" | "M" | "L"
    why: list = field(default_factory=list)  # one "why we believe this" line per component


@dataclass
class Issue:
    """One root cause as a report ranks it: a chain of findings on one equipment.

    ``key`` is the root finding's fingerprint (facility-keyed when a facility id is known), so an
    issue -- and any engineer note written against it -- follows the fault across runs.
    ``members`` are ordered root first. ``dependents`` lists the findings (on any equipment) that
    are conditional on *this* issue when it is a sensor problem; ``conditional_on`` lists the
    sensor causes *this* issue leans on. ``upstream_causes`` (0.91) lists the
    :class:`UpstreamCause` s -- upstream equipment that may explain the issue -- and ``downstream``
    the findings an upstream issue may explain. ``hours_union`` is the union of the members'
    violation masks (never a sum; ``None`` when no member exposes a mask) and ``cost`` the largest
    costed member estimate (``None`` when none is costed). Fields after ``why`` are additive detail.
    """

    key: str
    root: object
    members: list
    dependents: list = field(default_factory=list)
    conditional_on: list = field(default_factory=list)
    hours_union: float | None = None
    cost: float | None = None
    cost_basis_note: str = ""
    confidence: str = "M"
    why: list = field(default_factory=list)
    severity: str = "info"
    equip: str = ""
    chain: str | None = None
    rank: int = 0
    fan_on_hours: float | None = None  # the % runtime denominator
    fan_gate: str = ""  # which signal the runtime / hours were gated on
    mask: object = None  # the union violation mask (bool Series) when one exists
    member_costs: list = field(default_factory=list)  # FaultCost per member, members' order
    confidence_components: dict = field(default_factory=dict)
    # 0.91: upstream equipment that may explain this issue (a plant short of setpoint behind warm
    # supply air), and -- on that upstream issue -- the downstream findings it may explain.
    upstream_causes: list = field(default_factory=list)
    downstream: list = field(default_factory=list)
    # 0.107 (#196): AHU -> VAV cause chains (:class:`ZoneLink`). On an air handler's issue, the
    # box symptoms the served-by topology ties to it (members on other equipment); on a fleet
    # rogue-zone census issue, the air handlers whose reset its rogue boxes sit under.
    zone_links: list = field(default_factory=list)

    @property
    def conditional(self) -> bool:
        """True when the issue leans on a sensor we have reason to distrust."""
        return bool(self.conditional_on)

    @property
    def costed(self) -> bool:
        return self.cost is not None

    @property
    def pct_runtime(self) -> float | None:
        """Union violation hours as a share of fan-on hours (``None`` when either is unknown)."""
        if self.hours_union is None or not self.fan_on_hours:
            return None
        return round(100.0 * self.hours_union / self.fan_on_hours, 1)

    @property
    def rules(self) -> list:
        return [_attr(f, "rule", "") for f in self.members]


def _level(x) -> str:
    return x if x in _LEVELS else "M"


def finding_confidence(
    finding,
    *,
    trust=None,
    mapping=None,
    assumptions=None,
    sample_n=None,
    coverage=None,
    corroboration=None,
    conditional_on=(),
) -> Confidence:
    """Grade how far a finding can be believed: the **minimum** over five components.

    Each component is ``(level, why)`` or omitted (``None`` = not assessed; it then neither raises
    nor lowers the grade, but still says so in ``why``):

    * **input trust** -- ``trust`` is ``{role slug: SensorTrust-like}`` for the rule's inputs
      (gated where a fan gate exists); the worst verdict sets it (trusted H, suspect M, untrusted
      L). Any ``conditional_on`` sensor cause forces it to L.
    * **mapping** -- ``mapping`` is ``(level, why)`` for how the points were mapped to roles.
    * **assumptions** -- ``assumptions`` is ``(level, why)``: site-configured parameters (H) vs
      defaults (M) vs a reference the site never declared (L).
    * **sample size / coverage** -- ``sample_n`` samples judged (>= 168 H, >= 48 M, else L; read
      from the finding's metrics when not given) and ``coverage`` (0-1) of those inputs.
    * **corroboration** -- ``corroboration`` is ``{"tpr", "fpr", "track"}`` for the rule from a
      benchmark track (TPR >= 0.9 and FPR <= 0.05 H, else M); absent = not assessed.
    """
    comps: dict = {}
    why: list = []
    rule = _attr(finding, "rule", "")

    # input trust
    if conditional_on:
        comps["input_trust"] = "L"
        why.append(
            "Input trust L: conditional on "
            + "; ".join(c.label() if hasattr(c, "label") else str(c) for c in conditional_on)
        )
    elif trust:
        order = {"trusted": "H", "suspect": "M", "untrusted": "L"}
        worst = min(
            ((order.get(getattr(t, "verdict", ""), "M"), r) for r, t in trust.items()),
            key=lambda t: _LEVELS.index(t[0]),
        )
        comps["input_trust"] = worst[0]
        detail = ", ".join(
            f"{r} {getattr(t, 'verdict', '?')} ({getattr(t, 'trust', float('nan')):.2f})"
            for r, t in sorted(trust.items())
        )
        why.append(f"Input trust {worst[0]}: {detail}")
    else:
        why.append("Input trust not assessed (no sensor-trust scores for this finding's inputs)")

    # mapping
    if mapping is not None:
        lvl, text = mapping
        comps["mapping"] = _level(lvl)
        why.append(f"Mapping {comps['mapping']}: {text}")
    else:
        why.append("Mapping confidence not assessed")

    # site-vs-default assumptions
    if assumptions is not None:
        lvl, text = assumptions
        comps["assumptions"] = _level(lvl)
        why.append(f"Assumptions {comps['assumptions']}: {text}")

    # sample size / coverage
    n = sample_n
    if n is None:
        m = _attr(finding, "metrics", {}) or {}
        for k in ("n", "n_valid", "n_considered", "n_above_limit", "n_cooling", "n_checked"):
            v = m.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                n = int(v)
                break
    if n is not None:
        lvl = "H" if n >= 168 else "M" if n >= 48 else "L"
        text = f"{n} samples judged"
        if coverage is not None:
            text += f", {coverage:.0%} input coverage"
            if coverage < 0.5:
                lvl = "L"
            elif coverage < 0.8 and lvl == "H":
                lvl = "M"
        comps["sample"] = lvl
        why.append(f"Sample {lvl}: {text}")
    else:
        why.append("Sample size not reported by the rule")

    # corroboration
    if corroboration:
        tpr, fpr = corroboration.get("tpr"), corroboration.get("fpr")
        track = corroboration.get("track", "benchmark")
        if isinstance(tpr, (int, float)) and isinstance(fpr, (int, float)):
            lvl = "H" if tpr >= 0.9 and fpr <= 0.05 else "M"
            comps["corroboration"] = lvl
            why.append(
                f"Corroboration {lvl}: {rule} scores TPR {tpr:.0%} / FPR {fpr:.0%} on {track}"
            )
    else:
        why.append(f"Corroboration not assessed: no benchmark track scores {rule!r}")

    level = min(comps.values(), key=_LEVELS.index) if comps else "M"
    return Confidence(level=level, components=comps, why=why)


def _slug(role) -> str:
    return getattr(role, "value", str(role))


def _rule_roles(rules_map: dict, rule_name: str) -> set:
    rule = rules_map.get(rule_name)
    if rule is None:
        return set()
    from ..model.entities import roles_any_of  # 0.98 (#86 item 4a): the any-of inputs count too

    return {
        _slug(r)
        for r in tuple(getattr(rule, "roles_required", ()))
        + tuple(getattr(rule, "roles_optional", ()))
        + tuple(r for g in roles_any_of(rule) for r in g)
    }


def sensor_causes(findings, *, trust=None, mixing=None, shared_scope=None) -> list:
    """The sensor problems that make other findings conditional, derived from the data.

    * a ``sensor_drift:<role>`` finding at ``warn``/``fault``;
    * an ``untrusted`` verdict or a ``stuck`` flag in ``trust`` (``{equip: {role: SensorTrust}}``,
      scored on gated samples where a fan gate exists);
    * (0.92, #16) a ``copied_signal`` or ``mixing_balance`` flag in ``trust`` -- a point carrying
      another point's data, or a mixed-air temperature failing the flow-weighted OA/RA balance
      (:func:`camber.sensorhealth.frame_checks`); unit-local, even on OAT;
    * (0.105, #151) a ``mixing_order`` flag in ``trust`` -- the mixed air outside [OAT, RAT] on a
      unit without airflow stations; unit-local like the two above. A unit whose ``mixing`` result
      below is actionable too gets that one cause only, not a second one per flagged point;
    * a ``warn``/``fault`` mixing-consistency result in ``mixing`` (``{equip: ConsistencyResult}``)
      -- one of MAT / OAT / RAT on that unit is wrong, so all three are tainted *on that unit*.

    A cause on a role in :data:`SHARED_ROLES` (other than a unit-local mixing check) taints every
    equipment that uses the role -- unless the drift finding names the units that read that
    sensor in ``metrics["scope_equips"]`` (0.91: one AHU's own OAT), which then scopes it to them.

    ``shared_scope`` (0.92, #66; ``{equip: [equips reading the same sensor]}``) scopes a *trust*
    cause on a shared role the same way: one AHU's own stuck OAT then taints the units that read
    that sensor, not a chiller or boiler reading the building's weather station.
    """
    out: list = []
    for f in findings:
        rule = _attr(f, "rule", "")
        if rule.startswith("sensor_drift:") and _attr(f, "severity", "") in _ACTIONABLE:
            slug = rule.split(":", 1)[1]
            summ = str(_attr(f, "summary", "") or "")
            detail = f"{rule} {_attr(f, 'severity', '')}" + (f" ({summ})" if summ else "")
            # a unit's own sensor (e.g. one AHU's OAT among several) taints only the units that
            # read it: the finding names them in metrics["scope_equips"]
            scope = (_attr(f, "metrics", {}) or {}).get("scope_equips")
            if isinstance(scope, (list, tuple)) and scope:
                for eq in scope:
                    out.append(SensorCause("sensor_drift", str(eq), (slug,), detail, source=f))
                continue
            out.append(
                SensorCause(
                    "sensor_drift",
                    _attr(f, "equip", ""),
                    (slug,),
                    detail,
                    shared=slug in SHARED_ROLES,
                    source=f,
                )
            )
    # 0.105 (#151): the trust score's mixed-air order flag and the RCx mixing result are the same
    # check; where both fire, the mixing cause below stands for both
    mixed = {e for e, res in (mixing or {}).items() if getattr(res, "severity", "") in _ACTIONABLE}
    for equip, per_role in sorted((trust or {}).items()):
        for role, t in per_role.items():
            slug = _slug(role)
            flags = list(getattr(t, "flags", []) or [])
            if getattr(t, "verdict", "") == "untrusted" or "stuck" in flags:
                why = "stuck" if "stuck" in flags else "untrusted"
                detail = f"{slug} {why} (trust {getattr(t, 'trust', float('nan')):.2f})"
                scope = (shared_scope or {}).get(equip) if slug in SHARED_ROLES else None
                if scope:  # 0.92 (#66): the units that read this sensor, not the site
                    out.extend(
                        SensorCause("trust", str(eq), (slug,), detail) for eq in sorted(set(scope))
                    )
                    continue
                out.append(
                    SensorCause(
                        "trust",
                        equip,
                        (slug,),
                        f"{slug} {why} (trust {getattr(t, 'trust', float('nan')):.2f})",
                        shared=slug in SHARED_ROLES,
                    )
                )
                continue
            # 0.92 (#16): a copied point, or a failing mixed-air flow balance, makes the
            # unit's findings on that point conditional -- on this unit only (both are checks of
            # this unit's own points, even when one of them is the site OAT)
            if equip in mixed and "mixing_order" in flags and "copied_signal" not in flags:
                continue
            cross = _cross_sensor_detail(slug, t, flags)
            if cross:
                out.append(SensorCause("trust", equip, (slug,), cross))
    for equip, res in sorted((mixing or {}).items()):
        if getattr(res, "severity", "") in _ACTIONABLE:
            out.append(
                SensorCause(
                    "mixing",
                    equip,
                    ("mixed_air_temp", "oat", "return_air_temp"),
                    "mixed-air temperature outside [OAT, RAT] "
                    f"{100 * float(getattr(res, 'violation_frac', 0.0) or 0.0):.0f}% of samples",
                )
            )
    return out


def _cross_sensor_detail(slug: str, t, flags: list) -> str | None:
    """0.92 (#16): the cause line for a ``copied_signal`` / ``mixing_balance`` trust flag (and,
    since 0.105 (#151), a ``mixing_order`` one)."""
    checks = list(getattr(t, "frame_checks", []) or [])
    score = f"trust {getattr(t, 'trust', float('nan')):.2f}"
    if "copied_signal" in flags:
        c: dict = next((c for c in checks if c.get("check") == "copied_signal"), {})
        who = "is a copy of" if c.get("blame") == "level_shift" else "carries the same data as"
        return (
            f"{slug} {who} {c.get('copy_of', 'another point')} "
            f"({c.get('start', '?')} .. {c.get('end', '?')}; {score})"
        )
    if "mixing_balance" in flags:
        c2: dict = next((c for c in checks if c.get("check") == "mixing_flow_balance"), {})
        bias = c2.get("bias_f")
        b = f"MAT {bias:+.1f}F" if isinstance(bias, (int, float)) else "MAT off"
        return f"{slug}: mixed-air flow balance fails ({b} vs the OA/RA blend; {score})"
    if "mixing_order" in flags:  # 0.105 (#151)
        c3: dict = next((c for c in checks if c.get("check") == "mixing_temperature_order"), {})
        frac = c3.get("violation_frac")
        share = f"{100 * frac:.0f}% of fan-on samples" if isinstance(frac, (int, float)) else ""
        return (
            f"{slug}: mixed-air temperature outside [OAT, RAT]"
            + (f" {share}" if share else "")
            + f" ({score})"
        )
    return None


def _taints(cause: SensorCause, equip: str, roles: set) -> bool:
    if not roles.intersection(cause.roles):
        return False
    return cause.shared or cause.equip == equip


def _uncosted_note(basis: str) -> str:
    b = (basis or "").strip()
    if b.startswith("needs "):
        return "uncosted — needs " + b[len("needs ") :].replace("EquipmentLoad.", "")
    if b.startswith("no cost model"):
        return "uncosted — no cost model for this rule"
    return f"uncosted — {b}" if b else "uncosted"


def link_findings(
    findings,
    *,
    rules=None,
    costs=None,
    loads=None,
    price=None,
    cost_params=None,
    exclude_cost=None,
    mask_for=None,
    runtime=None,
    trust=None,
    mixing=None,
    confidence_for=None,
    facility_id: str = "",
    site: str = "",
    actionable_only: bool = True,
    topology=None,
    plant_overlap_min: float = 0.25,
    shared_scope=None,
    part_mask_for=None,
    zone_overlap_min: float = 0.25,
) -> list:
    """Link findings into ranked :class:`Issue` objects (provisional API).

    Grouping follows :data:`CAUSE_CHAINS` per equipment, then (0.107, #196) the AHU -> VAV
    chains of :data:`ZONE_CHAINS` across equipment. Then:

    * **AHU -> VAV chains** (0.107): an air handler's finding that :data:`ZONE_CHAINS` names as a
      root (supply air too warm or too cold, duct static too low) takes the matching symptom
      findings of the boxes ``topology`` says it serves as members of its issue; the boxes are
      counted against every box it serves (:class:`ZoneLink`, ``Issue.zone_links``). Nothing is
      linked without an explicit topology (a ``"heuristic"`` naming guess is not used), for a box
      the topology does not cover, or for a box served by another air handler. A symptom that
      leans on a distrusted sensor, or whose hours do not coincide with the root's (each side's
      mask covering less than ``zone_overlap_min`` of the other; no mask on a side -> linked,
      "not assessed"), stays its own issue and is counted on the link. A fleet rogue-zone census
      (:data:`ZONE_ROGUE_LINKS`) is listed on the air handler whose reset its rogue boxes sit
      under, and kept as its own issue. :func:`zone_chain_gaps` reports what a missing topology
      left unlinked.

    * **Sensor precedence** (:func:`sensor_causes`): a finding whose rule lists a tainted role in
      ``roles_required``/``roles_optional`` (``rules`` is a Registry / ``{name: rule}``) on the
      same equipment -- or on any equipment for a shared role like OAT -- makes its issue
      *conditional*: annotated in ``conditional_on`` and listed in the ``sensor_drift`` issue's
      ``dependents``, never deleted. Its cost counts as "at risk pending sensor fix".
      ``shared_scope`` (0.92) scopes a trust cause on a shared role (see :func:`sensor_causes`).
    * **Hours** are the union of the members' violation masks (``mask_for(finding) -> bool Series |
      None``), gated to fan-on by ``runtime(equip) -> (fan_on_mask | None, gate_label)`` when given;
      ``fan_on_hours`` is the % runtime denominator.
    * **Cost** of an issue is the **max** of its costed members ("member estimates overlap; largest
      shown"); ``costs`` are :class:`camber.fault_economics.FaultCost` aligned with ``findings``
      (computed from ``loads``/``price``/``cost_params`` when omitted). ``exclude_cost(finding) ->
      str | None`` names a reason to leave a member out of the dollars (e.g. a reference target the
      site never declared).
    * **Rank**: severity tier, then non-conditional before conditional, costed before uncosted, $
      descending; uncosted issues of one tier then rank by confidence, H before M before L
      (0.102, #112); ties break on the key, so the order is deterministic.
    * **Confidence**: ``confidence_for(issue) -> Confidence`` (default: :func:`finding_confidence`
      of the root with the trust scores of its inputs and the conditional causes).
    * **Plant capacity** (0.91): an issue carrying a SAT-high finding (:func:`is_sat_high`) gets
      each chilled-water plant issue (:data:`PLANT_CAPACITY_RULES`) that serves it as an
      :class:`UpstreamCause` -- served per ``topology`` (a :class:`camber.model.topology.Topology`
      whose ancestors of the unit include the plant equipment), or, when no topology covers the
      unit, the site's plant. When both sides expose violation masks the two must coincide: the
      plant short during at least ``plant_overlap_min`` of the unit's violation hours, or the
      unit in violation during at least ``plant_overlap_min`` of the plant's short hours (a plant
      that runs part of the time explains only part of a unit's hours, but a unit that runs warm
      whenever the plant is short is still its symptom). When either side has no mask the link
      is made and says the overlap was not assessed. The plant issue lists the linked
      findings in ``downstream``. Nothing is removed, demoted or re-costed; a "why" line on each
      side names the link. A G36 finding's unit hours are its **FC13** hours only (0.92, #67):
      ``part_mask_for(finding, "FC13") -> bool Series | None`` supplies them (the RCx report reads
      the rule's per-FC evidence masks); without it, or when it returns ``None``, the finding's
      whole violation mask is used, as before, and ``UpstreamCause.unit_hours`` says which.
    """
    from ..fault_economics import cost_findings

    findings = list(findings)
    rmap: dict = {}
    if rules is not None:
        if hasattr(rules, "names") and hasattr(rules, "get"):
            rmap = {n: rules.get(n) for n in rules.names()}
        elif isinstance(rules, dict):
            rmap = dict(rules)
        else:
            rmap = {getattr(r, "name", str(i)): r for i, r in enumerate(rules)}
    if costs is None:
        costs = cost_findings(findings, loads, price, params=cost_params)
    cost_of = {id(f): c for f, c in zip(findings, costs)}

    causes = sensor_causes(findings, trust=trust, mixing=mixing, shared_scope=shared_scope)
    items = [
        f for f in findings if (not actionable_only) or _attr(f, "severity", "") in _ACTIONABLE
    ]

    buckets: dict = {}
    bucket_of: dict = {}
    for f in items:
        equip, rule = _attr(f, "equip", ""), _attr(f, "rule", "")
        cid = _CHAIN_POS.get(rule, (None, None))[0]
        bk = (equip, cid or f"solo:{rule}")
        buckets.setdefault(bk, []).append(f)
        bucket_of[id(f)] = bk

    # 0.107 (#196): AHU -> VAV chains move each linked box symptom into its air handler's bucket
    plan = _plan_zone_chains(
        items,
        topology,
        causes=causes,
        rmap=rmap,
        mask_for=mask_for,
        part_mask_for=part_mask_for,
        runtime=runtime,
        overlap_min=zone_overlap_min,
    )
    for f, root in plan.moves:
        src, dst = bucket_of[id(f)], bucket_of[id(root)]
        buckets[src] = [g for g in buckets[src] if g is not f]
        buckets[dst].append(f)
        if not buckets[src]:
            del buckets[src]

    def _pos(f):
        return _CHAIN_POS.get(_attr(f, "rule", ""), (None, 99))[1] or 0

    issues: list = []
    by_rule_equip: dict = {}
    issue_of_root: dict = {}
    for (equip, bkey), fs in buckets.items():
        # the bucket's own equipment first (root-first chain order), then linked boxes by id
        own = sorted((f for f in fs if _attr(f, "equip", "") == equip), key=_pos)
        boxes = sorted(
            (f for f in fs if _attr(f, "equip", "") != equip),
            key=lambda f: (str(_attr(f, "equip", "")), _pos(f), str(_attr(f, "rule", ""))),
        )
        members = own + boxes
        root = members[0]
        sev = max(
            (_attr(f, "severity", "info") for f in fs), key=lambda s: SEVERITY_ORDER.get(s, 1)
        )
        key = fingerprint(facility_id or site, equip, _attr(root, "rule", ""))
        ids = {id(f) for f in members}
        # an issue is never conditional on a cause it is itself the evidence for; each member is
        # judged on its own equipment (a linked box member on the box)
        cond = [
            c
            for c in causes
            if id(c.source) not in ids
            and any(
                _taints(c, _attr(f, "equip", ""), _rule_roles(rmap, _attr(f, "rule", "")))
                for f in members
            )
        ]

        # hours: union of member masks, gated to fan-on when a gate is known
        gate, gate_label, fan_hours = None, "", None
        if runtime is not None:
            gate, gate_label = runtime(equip) or (None, "")
        union = None
        for f in members:
            m = mask_for(f) if mask_for is not None else None
            if m is None:
                continue
            m = m.fillna(False).astype(bool)
            union = m if union is None else _or(union, m)
        hours = None
        if union is not None:
            if gate is not None:
                union = union & gate.reindex(union.index).fillna(False).astype(bool)
            hours = round(float(union.sum()) * _interval_hours(union.index), 2)
        if gate is not None:
            on = gate.fillna(False).astype(bool)
            fan_hours = round(float(on.sum()) * _interval_hours(gate.index), 2)

        # cost: max of costed members, never the sum
        mcosts = [cost_of.get(id(f)) for f in members]
        costed = []
        excluded = []
        for f, c in zip(members, mcosts):
            if c is None or not getattr(c, "costed", False):
                continue
            reason = exclude_cost(f) if exclude_cost is not None else None
            if reason:
                excluded.append(f"{_attr(f, 'rule', '')}: {reason}")
                continue
            costed.append((float(c.annual_cost_usd), f, c))
        if costed:
            best = max(costed, key=lambda t: t[0])
            cost = round(best[0], 2)
            note = f"{best[2].basis} ({_attr(best[1], 'rule', '')})"
            if len(costed) > 1:
                note = "member estimates overlap; largest shown — " + note
        else:
            cost = None
            rc = cost_of.get(id(root))
            note = _uncosted_note(getattr(rc, "basis", "") if rc is not None else "")
        if excluded:
            note += " · excluded from $: " + "; ".join(excluded)

        issue = Issue(
            key=key,
            root=root,
            members=members,
            conditional_on=cond,
            hours_union=hours,
            cost=cost,
            cost_basis_note=note,
            severity=sev,
            equip=equip,
            chain=None if bkey.startswith("solo:") else bkey,
            fan_on_hours=fan_hours,
            fan_gate=gate_label,
            mask=union,
            member_costs=mcosts,
        )
        issues.append(issue)
        for f in members:
            by_rule_equip.setdefault((_attr(f, "rule", ""), _attr(f, "equip", "")), issue)
            issue_of_root[id(f)] = issue

    for link in plan.links:  # 0.107 (#196): each link on its AHU issue (and its census issue)
        iss = issue_of_root.get(id(link.root))
        if iss is not None:
            iss.zone_links.append(link)
        if link.source is not None:
            census = issue_of_root.get(id(link.source))
            if census is not None and census is not iss:
                census.zone_links.append(link)

    # a sensor-drift issue lists every finding that became conditional on it
    for iss in issues:
        ids = {id(f) for f in iss.members}
        for other in issues:
            if other is not iss and any(id(c.source) in ids for c in other.conditional_on):
                iss.dependents.extend(other.members)

    _link_plant_capacity(issues, mask_for, runtime, topology, plant_overlap_min, part_mask_for)

    # confidence
    for iss in issues:
        if confidence_for is not None:
            conf = confidence_for(iss)
        else:
            conf = finding_confidence(iss.root, conditional_on=iss.conditional_on)
        iss.confidence = conf.level
        iss.why = list(conf.why) + _upstream_why(iss) + _zone_why(iss)
        iss.confidence_components = dict(conf.components)

    issues.sort(
        key=lambda i: (
            -SEVERITY_ORDER.get(i.severity, 1),
            i.conditional,
            i.cost is None,
            -(i.cost or 0.0),
            _confidence_rank(i) if i.cost is None else 0,
            i.key,
        )
    )
    for n, iss in enumerate(issues, 1):
        iss.rank = n
    return issues


def _confidence_rank(issue) -> int:
    """Sort position of an issue's confidence grade: H, then M, then L, then ungraded (#112)."""
    lvl = getattr(issue, "confidence", None)
    return _LEVELS[::-1].index(lvl) if lvl in _LEVELS else len(_LEVELS)


def _mask_union(findings, mask_for, gate):
    """OR of the findings' violation masks, gated to ``gate`` (``None`` when no mask exists)."""
    union = None
    for f in findings:
        m = mask_for(f) if mask_for is not None else None
        if m is None:
            continue
        m = m.fillna(False).astype(bool)
        union = m if union is None else _or(union, m)
    if union is not None and gate is not None:
        union = union & gate.reindex(union.index).fillna(False).astype(bool)
    return union


def _sat_high_mask(findings, mask_for, part_mask_for, gate):
    """``(mask | None, basis)``: the SAT-high hours of a unit's SAT-high findings (#67).

    A G36 finding contributes its FC13 hours (:data:`G36_SAT_HIGH_FCS`) when ``part_mask_for``
    supplies them -- its violation mask is the union of every fault condition, which would count
    duct-static or economizer hours as plant symptoms. Anything else (``supply_air_control``, or a
    G36 finding without per-FC masks) contributes its whole violation mask. ``basis`` is ``"FC13"``
    when every G36 member was read on its FC13 hours, else ``"finding"``.
    """
    union = None
    basis = "FC13"
    for f in findings:
        m = None
        if _attr(f, "rule", "") not in SAT_HIGH_RULES and part_mask_for is not None:
            for label in G36_SAT_HIGH_FCS:
                pm = part_mask_for(f, label)
                if pm is not None:
                    pm = pm.fillna(False).astype(bool)
                    m = pm if m is None else _or(m, pm)
        if m is None:
            basis = "finding"
            m = mask_for(f) if mask_for is not None else None
            if m is None:
                continue
            m = m.fillna(False).astype(bool)
        union = m if union is None else _or(union, m)
    if union is not None and gate is not None:
        union = union & gate.reindex(union.index).fillna(False).astype(bool)
    return union, basis


def _link_plant_capacity(
    issues, mask_for, runtime, topology, overlap_min, part_mask_for=None
) -> None:
    """Attach plant-capacity issues as upstream causes of the SAT-high issues they may explain."""
    plants = []
    for iss in issues:
        cap = [f for f in iss.members if _attr(f, "rule", "") in PLANT_CAPACITY_RULES]
        if cap:
            plants.append((iss, cap))
    if not plants:
        return
    for iss in issues:
        if any(iss is p for p, _cap in plants):
            continue
        sat = [f for f in iss.members if is_sat_high(f)]
        if not sat:
            continue
        serving = frozenset(topology.ancestors(iss.equip)) if topology is not None else frozenset()
        gate = None
        if runtime is not None:
            gate = (runtime(iss.equip) or (None, ""))[0]
        unit_mask, unit_basis = _sat_high_mask(sat, mask_for, part_mask_for, gate)
        for p, cap in plants:
            if serving:
                if p.equip not in serving:
                    continue
                basis = "topology"
            else:
                basis = "site"
            plant_mask = _mask_union(cap, mask_for, gate)  # plant short while the unit runs
            share = pshare = hours = None
            if unit_mask is not None and plant_mask is not None:
                both = unit_mask & plant_mask.reindex(unit_mask.index).fillna(False)
                n_unit, n_plant = int(unit_mask.sum()), int(plant_mask.sum())
                share = float(both.sum()) / n_unit if n_unit else None
                pshare = float(both.sum()) / n_plant if n_plant else None
                hours = round(float(both.sum()) * _interval_hours(unit_mask.index), 2)
                if max(share or 0.0, pshare or 0.0) < overlap_min:
                    continue
            rules = ", ".join(sorted({_attr(f, "rule", "") for f in cap}))
            detail = f"chilled-water plant short of setpoint ({rules} {p.severity})"
            cause = UpstreamCause(
                "plant_capacity",
                p.equip,
                rules,
                detail,
                overlap_share=None if share is None else round(share, 3),
                overlap_hours=hours,
                basis=basis,
                source=cap[0],
                plant_share=None if pshare is None else round(pshare, 3),
                unit_hours=None if hours is None else unit_basis,
            )
            iss.upstream_causes.append(cause)
            for f in sat:
                if not any(f is d for d in p.downstream):
                    p.downstream.append(f)


def _upstream_why(iss) -> list:
    """The "why" lines naming an issue's upstream causes / the downstream findings it explains."""
    out = []
    for c in iss.upstream_causes:
        if c.overlap_hours is not None:
            unit = "FC13 hours" if c.unit_hours == "FC13" else "violation hours"
            when = (
                f"for {c.overlap_hours:,.0f} h of the same hours ("
                f"{100 * (c.overlap_share or 0):.0f}% of this unit's {unit}; the unit was "
                f"in violation {100 * (c.plant_share or 0):.0f}% of the plant's short hours)"
            )
        else:
            when = "(same-hours overlap not assessed: no violation mask on one side)"
        tie = (
            "served per the configured topology"
            if c.basis == "topology"
            else "assumed to serve this unit (no served-by topology covers it)"
        )
        out.append(
            f"Likely upstream cause (conditional): {c.label()} {when}, {tie}. Check the plant's "
            "capacity and staging before the coil valve; this finding is kept, not removed."
        )
    if iss.downstream:
        names = sorted(
            {f"{_attr(f, 'rule', '')} on {_attr(f, 'equip', '')}" for f in iss.downstream}
        )
        out.append(
            "May explain downstream: " + "; ".join(names) + " (linked as a conditional cause)"
        )
    return out


# --------------------------------------------------------------------------- #
# AHU -> VAV cause chains (0.107, #196)
#
# ``CAUSE_CHAINS`` group findings on one equipment. An air handler's supply air or duct static,
# though, shows up downstream on the boxes it serves: supply air too warm leaves zones unable to
# cool, supply air too cold overcools them at minimum flow and forces reheat, duct static too low
# starves the boxes. ``ZONE_CHAINS`` names those pairs -- an AHU-side root test and a box-side
# symptom test, each read from the rules' own metrics -- and ``link_findings`` makes the AHU
# finding the root of an issue whose members include the matching findings of the boxes a
# served-by topology says it serves. Nothing is guessed: no explicit topology, or a box the
# topology does not cover, links nothing (:func:`zone_chain_gaps` says what was left unlinked).
# --------------------------------------------------------------------------- #


def _metrics(f) -> dict:
    m = _attr(f, "metrics", {}) or {}
    return m if isinstance(m, dict) else {}


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _leads(f, a: str, b: str, *, ties: bool = True) -> bool:
    """True when ``f``'s ``a`` share is positive and at least (``ties``) / above its ``b`` share."""
    m = _metrics(f)
    xa, ya = m.get(a), m.get(b)
    x = float(xa) if isinstance(xa, (int, float)) and not isinstance(xa, bool) else 0.0
    y = float(ya) if isinstance(ya, (int, float)) and not isinstance(ya, bool) else 0.0
    if x <= 0:
        return False
    return x >= y if ties else x > y


def _g36_flags(f, label: str) -> bool:
    """True when a ``g36_afdd`` finding reports fault condition ``label`` (its ``flagged_fcs``)."""
    if _attr(f, "rule", "") != "g36_afdd":
        return False
    flagged = _metrics(f).get("flagged_fcs")
    return isinstance(flagged, (list, tuple)) and label in {str(x).upper() for x in flagged}


def _reset_modes(f, reset: str) -> set:
    """The failure modes of a ``<reset>_reset_effectiveness`` finding (empty for other rules)."""
    if _attr(f, "rule", "") != f"{reset}_reset_effectiveness":
        return set()
    m = _metrics(f)
    modes = m.get("failure_modes")
    if not isinstance(modes, (list, tuple)) or not modes:
        modes = [m.get("reason")] if m.get("reason") else []
    return {str(x) for x in modes}


def _sat_warm(f) -> bool:
    """AHU supply air too warm: ``supply_air_control`` with its too-warm share leading, a G36
    FC13 (:func:`is_sat_high`), or a SAT reset parked at its warm (trim) end under demand."""
    return is_sat_high(f) or "not_responding" in _reset_modes(f, "sat")


def _sat_cold(f) -> bool:
    """AHU supply air too cold: ``supply_air_control`` with its too-cold share leading, a SAT
    held cold with no upward reset (``supply_air_reset``, which warns only when supply air is below
    58 F on most cooling hours), SAT colder than the G36 map (``supply_air_reset_compliance`` on its
    below-target side, 0.108; it also warns when supply air runs above the target), or
    a SAT reset that does not trim back while zones are idle (not when it is also parked warm)."""
    rule = _attr(f, "rule", "")
    if rule == "supply_air_control":
        return _leads(f, "too_cold_pct", "too_warm_pct", ties=False)
    if rule == "supply_air_reset":
        return True
    if rule == "supply_air_reset_compliance":
        # 0.108: the compliance rule also warns when supply air runs ABOVE the target (a cooling
        # shortfall); only its below-target side is supply air too cold
        return _below_target(f)
    modes = _reset_modes(f, "sat")
    return "not_trimming" in modes and "not_responding" not in modes


def _below_target(f) -> bool:
    """True when a ``supply_air_reset_compliance`` finding reports its below-target (reheat)
    side: supply air colder than the G36 target on at least its warn share of hours, by at least
    its gap floor. An older finding without the thresholds counts on a positive gap."""
    m = _metrics(f)

    def num(key, default=None):
        v = m.get(key)
        return float(v) if _num(v) else default

    pct, gap = num("pct_below_g36_target"), num("mean_gap_f")
    if pct is None or gap is None:
        return False
    return pct >= num("warn_pct_threshold", 0.0) and gap >= num("warn_gap_floor_f", 1e-9)


def _static_low(f) -> bool:
    """AHU duct static too low: G36 FC1 (duct static too low with the fan at full speed), or a
    static reset parked at its low (trim) end under demand."""
    return _g36_flags(f, "FC1") or "not_responding" in _reset_modes(f, "static")


def _zone_warm(f) -> bool:
    """A zone that can't cool: ``unmet_setpoint_hours`` with its too-hot share leading."""
    return _attr(f, "rule", "") == "unmet_setpoint_hours" and _leads(
        f, "too_hot_pct", "too_cold_pct"
    )


#: The box rules that read overcooling at minimum flow or the reheat it forces.
OVERCOOL_REHEAT_RULES = (
    "overcooling_min_flow",
    "overcooling_severity",
    "reheat_penalty",
    "reheat_minimization_g36",
)


def _overcool_reheat(f) -> bool:
    return _attr(f, "rule", "") in OVERCOOL_REHEAT_RULES


def _starved(f) -> bool:
    """A starved box: ``airflow_tracking`` with its under-setpoint share leading, or a zone that
    can't cool (``airflow_tracking``: a box starved by low duct static cascades into unmet zone
    temperatures)."""
    if _attr(f, "rule", "") == "airflow_tracking":
        return _leads(f, "undershoot_pct", "overshoot_pct")
    return _zone_warm(f)


#: AHU -> VAV cause chains: ``(chain id, AHU-side cause, root test, box-side symptom, symptom
#: test, G36 fault condition whose own hours the root is read on)``. A box finding joins the first
#: chain, in this order, whose root is found on an air handler the topology says serves the box.
ZONE_CHAINS = (
    ("sat_high", "supply air too warm", _sat_warm, "zones can't cool", _zone_warm, "FC13"),
    (
        "sat_low",
        "supply air too cold",
        _sat_cold,
        "overcooling and reheat at the boxes",
        _overcool_reheat,
        None,
    ),
    ("static_low", "duct static too low", _static_low, "starved boxes", _starved, "FC1"),
)

#: SAT / duct-static reset not resetting -> the rogue zones a fleet census names (``(reset, AHU
#: root rules, census rule)``). The census is one ``<fleet>`` finding over many air handlers, so it
#: is listed on each AHU whose reset its rogue boxes sit under, never absorbed into one.
ZONE_ROGUE_LINKS = (
    ("sat", ("sat_reset_effectiveness", "supply_air_reset"), "sat_rogue_zone_census"),
    ("static", ("static_reset_effectiveness", "static_pressure_reset"), "static_rogue_zone_census"),
)


@dataclass
class ZoneLink:
    """One AHU -> VAV cause chain: an air handler's root finding and the boxes it explains.

    ``boxes`` are the served boxes whose matching findings joined the AHU issue (members on other
    equipment); ``served`` every box the topology lists under the air handler, so a page reads
    "14 of 22 boxes". ``rules`` are the symptom rules that joined. ``not_assessed`` lists boxes
    joined without a same-hours check (no violation mask on one side); ``out_of_hours`` boxes
    whose symptom did not coincide with the root and ``conditional`` boxes whose symptom leans on
    a distrusted sensor -- both kept as their own issues. A rogue-zone census link
    (``source`` the fleet finding, ``absorbed`` False) lists the census's rogue boxes served by the
    air handler; the census stays its own issue.
    """

    chain: str
    ahu: str
    cause: str
    symptom: str
    root: object = field(default=None, compare=False, repr=False)
    boxes: tuple = ()
    served: tuple = ()
    rules: tuple = ()
    not_assessed: tuple = ()
    out_of_hours: tuple = ()
    conditional: tuple = ()
    absorbed: bool = True
    source: object = field(default=None, compare=False, repr=False)

    def count(self) -> str:
        """``"14 of 22 boxes"`` (``"3 boxes"`` when the topology lists none under the AHU)."""
        n, of = len(self.boxes), len(self.served)
        noun = "box" if (of or n) == 1 else "boxes"
        return f"{n} of {of} {noun}" if of else f"{n} {noun}"

    def label(self) -> str:
        return f"{self.cause} at {self.ahu} -> {self.symptom} on {self.count()}"


@dataclass
class _ZonePlan:
    moves: list = field(default_factory=list)  # (box finding, AHU root finding)
    links: list = field(default_factory=list)  # ZoneLink
    uncovered: dict = field(default_factory=dict)  # box -> {chain cause}
    topology: str = "explicit"  # "explicit" | "semantic" | "none" | "heuristic"


def _is_conditional(f, causes, rmap) -> bool:
    roles = _rule_roles(rmap, _attr(f, "rule", ""))
    equip = _attr(f, "equip", "")
    return any(c.source is not f and _taints(c, equip, roles) for c in causes or ())


def _pick(cands) -> object:
    """The root among an AHU's candidates: worst severity, then rule name (deterministic)."""
    return sorted(
        cands,
        key=lambda f: (-SEVERITY_ORDER.get(_attr(f, "severity", "info"), 1), _attr(f, "rule", "")),
    )[0]


def _coincide(root, f, fc, mask_for, part_mask_for, gate, overlap_min):
    """``True`` / ``False`` when the two findings' hours do / don't coincide, ``None`` when a side
    exposes no mask. The root is read on its G36 fault condition ``fc``'s own hours when the
    evidence carries them (a ``g36_afdd`` mask is the union of every condition)."""
    rm = None
    if fc and _attr(root, "rule", "") == "g36_afdd" and part_mask_for is not None:
        rm = part_mask_for(root, fc)
    if rm is None and mask_for is not None:
        rm = mask_for(root)
    bm = mask_for(f) if mask_for is not None else None
    if rm is None or bm is None:
        return None
    rm = rm.fillna(False).astype(bool)
    bm = bm.fillna(False).astype(bool)
    if gate is not None:
        rm = rm & gate.reindex(rm.index).fillna(False).astype(bool)
        bm = bm & gate.reindex(bm.index).fillna(False).astype(bool)
    both = bm & rm.reindex(bm.index).fillna(False)
    n_box, n_root, n_both = int(bm.sum()), int(rm.sum()), int(both.sum())
    if not n_box or not n_root:
        return False
    return max(n_both / n_box, n_both / n_root) >= overlap_min


def _plan_zone_chains(
    items,
    topology,
    *,
    causes=(),
    rmap=None,
    mask_for=None,
    part_mask_for=None,
    runtime=None,
    overlap_min: float = 0.25,
) -> _ZonePlan:
    """Which box findings join which AHU root (see :data:`ZONE_CHAINS`), and what was left
    unlinked for want of a topology. ``items`` are the findings already filtered to the ones
    linked into issues."""
    rmap = rmap or {}
    plan = _ZonePlan()
    usable = topology is not None and getattr(topology, "provenance", "") != "heuristic"
    if topology is None:
        plan.topology = "none"
    elif not usable:
        plan.topology = "heuristic"
    else:
        plan.topology = str(getattr(topology, "provenance", "explicit") or "explicit")

    def actionable(f):
        return _attr(f, "severity", "") in _ACTIONABLE

    # roots per chain: {ahu: finding}; a root leaning on a distrusted sensor roots nothing
    roots: dict = {}
    for cid, _cause, is_root, _sym, _is_sym, _fc in ZONE_CHAINS:
        per: dict = {}
        for f in items:
            if actionable(f) and is_root(f) and not _is_conditional(f, causes, rmap):
                per.setdefault(_attr(f, "equip", ""), []).append(f)
        roots[cid] = {a: _pick(fs) for a, fs in per.items()}
    root_ids = {id(f) for per in roots.values() for f in per.values()}

    links: dict = {}  # (id(root), chain) -> working dict

    def _link(cid, cause, symptom, ahu, root, *, source=None):
        k = (id(root), cid, id(source) if source is not None else None)
        if k not in links:
            links[k] = {
                "chain": cid,
                "ahu": ahu,
                "cause": cause,
                "symptom": symptom,
                "root": root,
                "source": source,
                "boxes": set(),
                "rules": set(),
                "not_assessed": set(),
                "out_of_hours": set(),
                "conditional": set(),
            }
        return links[k]

    def _gate(ahu):
        if runtime is None:
            return None
        return (runtime(ahu) or (None, ""))[0]

    for f in items:
        box = _attr(f, "equip", "")
        if not actionable(f) or id(f) in root_ids or box == "<fleet>":
            continue
        joined = False
        pending: list = []  # (link dict, kind) misses, recorded only when nothing joined
        for cid, cause, _is_root, symptom, is_sym, fc in ZONE_CHAINS:
            per = roots[cid]
            if not per or not is_sym(f) or box in per:
                continue  # a symptom on the root's own unit is a same-equipment chain
            if not usable or not topology.parents_of(box):
                plan.uncovered.setdefault(box, set()).add(cause)
                continue
            ahu = topology.nearest_ancestor(box, lambda n, per=per: n in per)
            if ahu is None:
                continue  # served by another air handler, or by none with this root
            root = per[ahu]
            if _is_conditional(f, causes, rmap):
                pending.append((_link(cid, cause, symptom, ahu, root), "conditional"))
                continue
            ok = _coincide(root, f, fc, mask_for, part_mask_for, _gate(ahu), overlap_min)
            if ok is False:
                pending.append((_link(cid, cause, symptom, ahu, root), "out_of_hours"))
                continue
            w = _link(cid, cause, symptom, ahu, root)
            w["boxes"].add(box)
            w["rules"].add(_attr(f, "rule", ""))
            if ok is None:
                w["not_assessed"].add(box)
            plan.moves.append((f, root))
            joined = True
            break
        if not joined:
            for w, kind in pending:
                w[kind].add(box)

    # rogue-zone censuses: listed on the AHU whose reset the rogue boxes sit under
    for reset, root_rules, census_rule in ZONE_ROGUE_LINKS:
        cands: dict = {}
        for f in items:
            if (
                actionable(f)
                and _attr(f, "rule", "") in root_rules
                and not _is_conditional(f, causes, rmap)
            ):
                cands.setdefault(_attr(f, "equip", ""), []).append(f)
        per = {a: _pick(fs) for a, fs in cands.items()}
        if not per:
            continue
        cause = "SAT reset not resetting" if reset == "sat" else "duct-static reset not resetting"
        for c in items:
            if not actionable(c) or _attr(c, "rule", "") != census_rule:
                continue
            for z in _metrics(c).get("rogues") or ():
                z = str(z)
                if not usable or not topology.parents_of(z):
                    plan.uncovered.setdefault(z, set()).add(cause)
                    continue
                ahu = topology.nearest_ancestor(z, lambda n, per=per: n in per)
                if ahu is None:
                    continue
                w = _link(f"{reset}_rogue", cause, "rogue zone", ahu, per[ahu], source=c)
                w["boxes"].add(z)
                w["rules"].add(census_rule)

    for w in links.values():
        if not w["boxes"] and not (w["conditional"] or w["out_of_hours"]):
            continue
        served = tuple(topology.zones_of(w["ahu"])) if usable else ()
        plan.links.append(
            ZoneLink(
                chain=w["chain"],
                ahu=w["ahu"],
                cause=w["cause"],
                symptom=w["symptom"],
                root=w["root"],
                boxes=tuple(sorted(w["boxes"])),
                served=served,
                rules=tuple(sorted(w["rules"])),
                not_assessed=tuple(sorted(w["not_assessed"])),
                out_of_hours=tuple(sorted(w["out_of_hours"] - w["boxes"])),
                conditional=tuple(sorted(w["conditional"] - w["boxes"])),
                absorbed=w["source"] is None,
                source=w["source"],
            )
        )
    plan.links.sort(key=lambda z: (z.ahu, z.chain))
    return plan


def zone_chain_gaps(findings, topology=None, *, actionable_only: bool = True) -> dict:
    """What a missing served-by topology left out of the AHU -> VAV chains (0.107, #196).

    ``{}`` when nothing was left out; else ``{"topology": "none" | "heuristic" | <provenance>,
    "boxes": [...], "causes": [...]}``: the boxes whose findings match a chain whose root an air
    handler on the site reports, but which no usable topology covers (``"none"``: no topology;
    ``"heuristic"``: a naming guess, not used; otherwise the topology lists no parent for them).
    """
    items = [
        f for f in findings if (not actionable_only) or _attr(f, "severity", "") in _ACTIONABLE
    ]
    plan = _plan_zone_chains(items, topology)
    if not plan.uncovered:
        return {}
    return {
        "topology": plan.topology,
        "boxes": sorted(plan.uncovered),
        "causes": sorted({c for cs in plan.uncovered.values() for c in cs}),
    }


def _zone_why(iss) -> list:
    """The "why" lines naming an issue's AHU -> VAV chains (0.107, #196)."""
    out = []
    ids = {id(f) for f in iss.members}
    for z in getattr(iss, "zone_links", None) or ():
        if id(z.root) in ids:  # this issue is the air handler's
            if z.absorbed and not z.boxes:
                line = (
                    f"Served-by topology: {z.cause} at {z.ahu} may explain {z.symptom}, but no "
                    "box finding joined this issue"
                )
            elif z.absorbed:
                line = (
                    f"Cause chain (served-by topology): {z.cause} at {z.ahu} is the likely cause "
                    f"of {z.symptom} on {z.count()} it serves ({', '.join(z.rules)}); those box "
                    "findings are members of this issue (hours are their union, the cost the "
                    "largest member)"
                )
                if z.not_assessed:
                    line += f"; same-hours overlap not assessed for {len(z.not_assessed)}"
            else:
                line = (
                    f"Rogue zone(s) {', '.join(z.boxes)} served by {z.ahu} ({z.rules[0]}, kept as "
                    "its own issue): the reset this issue reports does not answer their requests"
                )
            kept = []
            if z.out_of_hours:
                kept.append(f"{len(z.out_of_hours)} not in the same hours")
            if z.conditional:
                kept.append(f"{len(z.conditional)} conditional on a sensor")
            if kept:
                line += (
                    "; matching findings on served boxes that stay their own issues: "
                    + ", ".join(kept)
                )
            out.append(line + ".")
        else:  # a fleet census issue listing the air handlers its rogues sit under
            out.append(
                f"Rogue zone(s) {', '.join(z.boxes)} sit under {z.ahu}, whose "
                f"{_attr(z.root, 'rule', '')} finding reports the reset not resetting (linked)."
            )
    return out


def _interval_hours(index) -> float:
    from ..timegrid import interval_hours

    return float(interval_hours(index))


def _or(a, b):
    """Element-wise OR of two boolean Series over the union of their indexes."""
    idx = a.index.union(b.index)
    return a.reindex(idx, fill_value=False) | b.reindex(idx, fill_value=False)


def issue_totals(issues) -> dict:
    """Roll issues up: the costed, non-conditional $/yr (issues add; a chain counts once), the
    conditional "at risk pending sensor fix" $/yr, and the uncosted / conditional counts."""
    firm = sum(i.cost for i in issues if i.cost is not None and not i.conditional)
    at_risk = sum(i.cost for i in issues if i.cost is not None and i.conditional)
    return {
        "annual_cost_usd": round(firm, 2),
        "at_risk_usd": round(at_risk, 2),
        "n_issues": len(issues),
        "n_costed": sum(1 for i in issues if i.cost is not None and not i.conditional),
        "n_uncosted": sum(1 for i in issues if i.cost is None),
        "n_conditional": sum(1 for i in issues if i.conditional),
    }
