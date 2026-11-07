"""Mutation operators: change one field of a true claim so that it is false.

Each operator returns zero or more mutants of a :class:`~.facttable.Claim`; a mutant keeps the
original's ``fact_id`` (the sentence still cites the fact the true claim came from). A mutant
whose :meth:`~.facttable.Claim.key` is another true claim's is dropped: it is true, not false
(``AHU-2 passes X`` is no mutant of ``AHU-1 passes X`` when AHU-2 passes X too); so is one a
unit's own fact implies (a point's coverage when the unit's mean and worst point are that figure,
0112-cite10: the judge calibration found these labelled false).

The operators (plan section 3): swap the unit, point, rule or site; perturb a figure; °F for
°C; % for percentage points; flip a sign or a direction; flip a verdict; negate; quantify
("Every AHU ..."); stretch the period; promote a unit's claim to the site; inject a claim of
checking. Pair forms add ``swap_pair`` (the two units' claims exchanged) and ``overgeneralise``
(one unit's claim said of both): see :mod:`.forms`.
"""

from __future__ import annotations

from dataclasses import replace

OPERATORS = (
    "swap_unit",
    "swap_point",
    "swap_rule",
    "swap_site",
    "perturb_figure",
    "temp_unit",
    "pct_pp",
    "sign",
    "verdict",
    "negation",
    "quantifier",
    "stretch_period",
    "promote_site",
    "inject_check",
)
PAIR_OPERATORS = ("swap_pair", "overgeneralise")

_PERIODS = (
    "throughout 2026",
    "all year",
    "every day since January",
    "for the whole of 2025",
)
#: how a claim of checking is written around a clause: (subject prefix, predicate suffix)
_CHECKS = (
    ("Verified: ", ""),
    ("", " (checked)"),
    ("", ", as the data confirm"),
    ("", "; this was verified"),
)
#: what a site can be said to have or do as a unit does (a span or a rank cannot)
_SITE_ATTRS = ("verdict", "trust_class", "flat", "reference", "coverage", "trust", "bias", "share")
_FLIP = {"fault": "ok", "warn": "ok", "ok": "fault"}


class World:
    """What the operators need to know of the fact table: the families of units, the points,
    rules and sites, and every true claim's key."""

    def __init__(self, claims: list):
        from .facttable import METERS, UNITS

        self.claims = list(claims)
        self.keys = {c.key() for c in claims}
        self.units = list(UNITS)
        self.meters = list(METERS)
        self.datasets = sorted({c.unit for c in claims if c.attribute == "licence"})
        self.points = sorted({c.point for c in claims if c.point})
        self.rules = sorted({c.rule for c in claims if c.rule and c.attribute == "verdict"})
        self.sites = sorted({c.site for c in claims if c.site})
        # (0112-cite10) each unit's mean and worst point coverage, from its equipment fact
        self.coverage = {
            c.unit: (float(c.value), float(c.extra["worst"]))
            for c in claims
            if c.attribute == "coverage" and c.unit and not c.point and "worst" in (c.extra or {})
        }

    def family(self, unit: str) -> list:
        for fam in (self.units, self.meters, self.datasets):
            if unit in fam:
                return [u for u in fam if u != unit]
        return []

    def true(self, claim) -> bool:
        return claim.key() in self.keys or self.implied(claim)

    def implied(self, claim) -> bool:
        """(0112-cite10) A point's coverage the unit's own fact implies: when its mean and its
        worst point are both the claim's figure, every point has it ("every point on AHU-2 has
        100.0% coverage" of "mean coverage 100.0% ... worst at 100.0%")."""
        if claim.attribute != "coverage" or not claim.point or not claim.unit:
            return False
        cov = self.coverage.get(claim.unit)
        if cov is None or claim.value is None:
            return False
        v = round(float(claim.value), 3)
        return round(cov[0], 3) == v == round(cov[1], 3) and not (claim.extra or {}).get("period")


def _numeric(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _perturbed(c):
    v = c.value
    a = c.attribute
    if a == "trust":
        return round(v + 0.1 if v < 0.85 else v - 0.15, 2)
    if a == "coverage":
        return 90.0 if v >= 95 else round(v + 10, 1)
    if a == "rank":
        return int(v) + 1
    if a == "count":
        return int(v) + 2
    if a == "bias":
        return round(v + 1.5, 1)
    if c.munit == "h" or a in ("hours", "span", "issue_hours"):
        return float(v) + 5
    if c.munit == "%" or a in ("share", "saving"):
        return round(float(v) + 10, 1)
    if a == "reporting_days":
        return float(v) + 30
    if a == "param":
        return round(float(v) * 2 + 1, 2)
    return None


def mutants(claim, op: str, world: World) -> list:  # noqa: C901 - one branch per operator
    """The mutants of ``claim`` under ``op`` that are false in ``world``."""
    out = []
    ex = dict(claim.extra or {})
    if op == "swap_unit" and claim.unit:
        out = [replace(claim, unit=u) for u in world.family(claim.unit)][:2]
    elif op == "swap_point" and claim.point:
        out = [replace(claim, point=p) for p in world.points if p != claim.point][:3]
    elif op == "swap_rule" and claim.rule and claim.attribute in ("verdict", "hours", "share"):
        out = [replace(claim, rule=r) for r in world.rules if r != claim.rule][:3]
    elif op == "swap_site" and claim.site and not claim.unit and claim.attribute != "licence":
        out = [replace(claim, site=s) for s in world.sites if s != claim.site]
    elif op == "perturb_figure" and _numeric(claim.value):
        nv = _perturbed(claim)
        if nv is not None and nv != claim.value:
            out = [replace(claim, value=nv, sign="-" if nv < 0 else claim.sign)]
    elif op == "temp_unit" and claim.munit == "°F" and _numeric(claim.value) and claim.value:
        out = [replace(claim, munit="°C")]
    elif op == "pct_pp" and claim.munit == "%" and claim.attribute in ("share", "saving", "param"):
        out = [replace(claim, munit="percentage points")]
    elif op == "sign":
        if claim.attribute in ("bias", "saving") and _numeric(claim.value) and claim.value:
            nv = -claim.value
            out = [replace(claim, value=nv, sign="-" if nv < 0 else "+")]
        elif claim.attribute == "reference" and claim.label in ("reads high", "reads low"):
            other = "reads low" if claim.label == "reads high" else "reads high"
            out = [
                replace(
                    claim,
                    label=other,
                    extra={**ex, "pred": f"{other} against its reference"},
                )
            ]
    elif op == "verdict":
        if claim.attribute == "verdict" and claim.verdict in _FLIP:
            out = [replace(claim, verdict=_FLIP[claim.verdict])]
            if claim.verdict == "fault":
                out.append(replace(claim, verdict="warn"))
        elif claim.attribute == "trust_class":
            other = "trusted" if claim.label != "trusted" else "suspect"
            out = [replace(claim, label=other)]
        elif claim.attribute == "reference" and claim.verdict in ("warn", "ok"):
            other = "ok" if claim.verdict == "warn" else "warn"
            out = [replace(claim, verdict=other, label="")]
    elif op == "negation" and claim.attribute in (
        "verdict",
        "trust_class",
        "flat",
        "reference",
    ):
        if claim.attribute != "verdict" or claim.verdict in _FLIP:
            out = [replace(claim, extra={**ex, "neg": True})]
    elif op == "quantifier" and claim.unit in world.units:
        if claim.point:
            subject = f"Every point on {claim.unit}"
            out = [replace(claim, point="*", extra={**ex, "quant": subject})]
        elif claim.attribute in ("verdict", "rank"):
            q = replace(claim, unit="*", extra={**ex, "quant": "Every AHU"})
            if all(replace(claim, unit=u).key() in world.keys for u in world.units):
                q = None
            out = [q] if q else []
    elif (
        op == "stretch_period"
        and claim.unit
        and claim.attribute
        in (
            "verdict",
            "flat",
            "trust_class",
            "coverage",
            "saving",
            "reference",
        )
    ):
        out = [replace(claim, extra={**ex, "period": p}) for p in _PERIODS[:2]]
    elif op == "promote_site" and claim.unit and claim.site and claim.attribute in _SITE_ATTRS:
        subject = claim.site if not claim.point else f"{claim.site}'s {claim.point}"
        site_claim = replace(claim, unit="", extra={**ex, "site_subject": subject})
        # true when the site's own fact says it, or every unit of the site says the same
        same = [replace(claim, unit=u).key() in world.keys for u in world.family(claim.unit)]
        if not world.true(replace(site_claim, extra=ex)) and not all(same):
            out = [site_claim]
    elif op == "inject_check":
        out = [replace(claim, extra={**ex, "check": chk}) for chk in _CHECKS[:2]]
    return [m for m in out if op == "inject_check" or not world.true(m)]
