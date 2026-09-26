"""Point-mapping confidence: how sure are we each BAS tag resolved to the right role?

Mapping raw BAS point names to roles is the most labor-intensive (and error-prone) part
of onboarding a building, and a single bad mapping silently corrupts every diagnostic
downstream. This scores how much to trust each resolution from three signals:

- **how it matched** -- an explicit alias (a human wrote it down) is far more trustworthy
  than a regex pattern (a heuristic guess); no match means the point is unused,
- **ambiguity** -- if a token also matches other patterns for *different* roles, the
  first-wins choice is shakier,
- **data fit** -- when the point's data is available, does it respect the role's physical
  bounds? A tag mapped to OAT whose values sit at 0-100 (a valve, not a temperature) is
  almost certainly mismapped; this reuses :data:`camber.sensorhealth.PHYSICAL_BOUNDS`
  (unit-aware: a declared degC/K series is converted first),
- **scale** -- a cfm/gpm flow role whose data never leaves 0-100 or 0-1 (or whose declared unit
  is ``%``) is most likely a speed or position point typed as a flow; the wide flow bounds alone
  would accept it (see also :func:`camber.sensorhealth.percent_scale_suspect`).

The output flags the low-confidence and ambiguous mappings (and the unmapped tokens) so
an onboarding reviewer can spend their attention where it's actually needed instead of
eyeballing the whole point list.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .model.mapping import MappingProvider
from .model.roles import Role
from .sensorhealth import percent_scale_suspect, range_violation_frac

__all__ = [
    "MappingConfidence",
    "score_token",
    "score_mapping",
    "review",
]


# PHYSICAL_BOUNDS are in degF. A series declared in degC is converted before the range check --
# otherwise every correctly named degC temperature fails (a 15 degC supply air reads as a
# below-freezing degF "supply") and only the widest-bounded role survives.
_CELSIUS = frozenset({"c", "degc", "celsius", "degreescelsius", "degreesc"})
_KELVIN = frozenset({"k", "degk", "kelvin", "degreeskelvin"})
# temperature *differences*: a degC delta scales by 1.8 with no offset
_DELTA_TEMP_ROLES = frozenset(
    {
        Role.COND_APPROACH_TEMP,
        Role.EVAP_APPROACH_TEMP,
        Role.SUBCOOLING_TEMP,
        Role.SUPERHEAT_TEMP,
    }
)
_TEMP_ROLES = frozenset(r for r in Role if r.value.endswith("_temp") or r is Role.OAT)
# volumetric-flow roles (cfm / gpm) and the units that contradict them
_FLOW_ROLES = frozenset(
    {Role.AIRFLOW, Role.OA_AIRFLOW, Role.AIRFLOW_SP, Role.CHW_FLOW, Role.HW_FLOW}
)
_PERCENT_UNITS = frozenset({"%", "percent", "pct"})


def _unit_token(unit) -> str:
    """Normalize a unit string (``"°C"`` -> ``"c"``, ``"degF"`` -> ``"degf"``, ``"%"``)."""
    import re

    return re.sub(r"[^a-z%]+", "", str(unit).lower()) if unit else ""


def _in_bound_units(series, role, unit):
    """``series`` converted to the units :data:`~camber.sensorhealth.PHYSICAL_BOUNDS` uses
    (degF for temperatures) according to the declared ``unit``; unchanged otherwise."""
    u = _unit_token(unit)
    if series is None or role not in _TEMP_ROLES or not (u in _CELSIUS or u in _KELVIN):
        return series
    if role in _DELTA_TEMP_ROLES:
        return series * 1.8
    c = series - 273.15 if u in _KELVIN else series
    return c * 1.8 + 32.0


def _percent_scale(series) -> bool:
    """True when a series looks like a 0-100 % signal: >= 99 % of samples inside the percent
    bounds (-2..102, tolerating a few sensor glitches) and not trivially small (its 99th
    percentile above 1, so a near-zero flow isn't flagged)."""
    s = pd.to_numeric(series, errors="coerce").dropna()
    if len(s) < 3:
        return False
    inside = float(((s >= -2.0) & (s <= 102.0)).mean())
    return bool(inside >= 0.99 and s.quantile(0.99) > 1.0)


@dataclass
class MappingConfidence:
    """Confidence that one raw token resolved to the right role."""

    token: str
    role: str | None  # role slug, or None if unmapped
    basis: str  # "alias" | "pattern" | "unmapped"
    ambiguous: bool  # token also matches other patterns for other roles
    data_fit: float  # 1 - range-violation frac (NaN if no data / no bounds)
    confidence: float  # 0..1
    verdict: str  # "high" | "medium" | "low" | "unmapped"
    flags: list = field(default_factory=list)

    def as_dict(self) -> dict:
        """Return the confidence result as a plain dict."""
        d = self.__dict__.copy()
        d["flags"] = list(self.flags)
        return d


def score_token(
    token: str,
    mapping: MappingProvider,
    series: pd.Series | None = None,
    *,
    unit: str | None = None,
) -> MappingConfidence:
    """Score the confidence of one token's mapping, optionally cross-checked vs data.

    ``unit`` (optional) is the point's declared unit: a degC/K temperature is converted before the
    physical-range check, and a declared ``%`` under a volumetric-flow role is flagged
    ``unit_mismatch``. Without a unit, a flow-role series bounded to 0-100 is flagged
    ``percent_scale`` -- the signature of a speed/position point typed as a cfm/gpm flow.
    """
    role = mapping.role_of(token)
    if role is None:
        return MappingConfidence(
            token, None, "unmapped", False, float("nan"), 0.0, "unmapped", ["unmapped"]
        )

    alias_hit = mapping.aliases.get(token.lower()) is not None
    basis = "alias" if alias_hit else "pattern"
    distinct = set(mapping.candidates(token))
    ambiguous = (not alias_hit) and len(distinct) > 1

    conf = 0.95 if alias_hit else 0.70
    flags = []
    if ambiguous:
        conf *= 0.6
        flags.append("ambiguous")

    data_fit = float("nan")
    if series is not None:
        rv = range_violation_frac(_in_bound_units(series, role, unit), role)
        if rv == rv:  # role has bounds and data present
            data_fit = round(1.0 - rv, 4)
            if rv > 0.1:  # data doesn't physically fit the role
                flags.append("data_mismatch")
                conf *= 1.0 - min(rv * 2.0, 1.0)

    if role in _FLOW_ROLES:
        if _unit_token(unit) in _PERCENT_UNITS:
            flags.append("unit_mismatch")  # a declared % is not a volumetric flow
            conf *= 0.3
        elif (
            unit is None
            and series is not None
            and (_percent_scale(series) or percent_scale_suspect(series, role) is True)
        ):
            # a cfm/gpm role whose data never leaves 0-100: most likely a speed or position (%)
            # typed as a flow -- plausible data for the role, but not trustworthy without a unit
            flags.append("percent_scale")
            conf *= 0.5

    conf = round(max(0.0, min(1.0, conf)), 4)
    verdict = "high" if conf >= 0.8 else ("medium" if conf >= 0.5 else "low")
    return MappingConfidence(token, role.value, basis, ambiguous, data_fit, conf, verdict, flags)


def score_mapping(
    tokens,
    mapping: MappingProvider,
    series_by_token: dict | None = None,
    *,
    units: dict | None = None,
) -> list:
    """Score a whole set of tokens against a mapping (optionally with per-token data).

    ``series_by_token``: ``{token: pd.Series}`` to enable the data-fit cross-check; ``units``:
    ``{token: unit}`` declared units (see :func:`score_token`).
    Returns a list of :class:`MappingConfidence`, in the order of ``tokens``.
    """
    sbt = series_by_token or {}
    un = units or {}
    return [score_token(t, mapping, sbt.get(t), unit=un.get(t)) for t in tokens]


def review(
    tokens,
    mapping: MappingProvider,
    series_by_token: dict | None = None,
    *,
    min_confidence: float = 0.5,
    units: dict | None = None,
) -> dict:
    """Summarize a mapping review: what's solid, what needs a human look.

    Returns ``{"scored", "needs_review", "unmapped", "n"}`` where ``needs_review`` is the
    mapped tokens below ``min_confidence`` (or ambiguous / data-mismatched / scale-suspect) and
    ``unmapped`` is tokens that didn't resolve at all.
    """
    scored = score_mapping(tokens, mapping, series_by_token, units=units)
    unmapped = [s for s in scored if s.basis == "unmapped"]
    needs = [
        s
        for s in scored
        if s.basis != "unmapped"
        and (
            s.confidence < min_confidence
            or s.ambiguous
            or {"data_mismatch", "unit_mismatch", "percent_scale"} & set(s.flags)
        )
    ]
    return {"scored": scored, "needs_review": needs, "unmapped": unmapped, "n": len(scored)}
