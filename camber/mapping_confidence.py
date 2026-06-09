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
  almost certainly mismapped; this reuses :data:`camber.sensorhealth.PHYSICAL_BOUNDS`.

The output flags the low-confidence and ambiguous mappings (and the unmapped tokens) so
an onboarding reviewer can spend their attention where it's actually needed instead of
eyeballing the whole point list.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .model.mapping import MappingProvider
from .model.roles import Role
from .sensorhealth import range_violation_frac


@dataclass
class MappingConfidence:
    """Confidence that one raw token resolved to the right role."""

    token: str
    role: str | None              # role slug, or None if unmapped
    basis: str                    # "alias" | "pattern" | "unmapped"
    ambiguous: bool               # token also matches other patterns for other roles
    data_fit: float               # 1 - range-violation frac (NaN if no data / no bounds)
    confidence: float             # 0..1
    verdict: str                  # "high" | "medium" | "low" | "unmapped"
    flags: list = field(default_factory=list)

    def as_dict(self) -> dict:
        """Return the confidence result as a plain dict."""
        d = self.__dict__.copy()
        d["flags"] = list(self.flags)
        return d


def score_token(token: str, mapping: MappingProvider,
                series: pd.Series | None = None) -> MappingConfidence:
    """Score the confidence of one token's mapping, optionally cross-checked vs data."""
    role = mapping.role_of(token)
    if role is None:
        return MappingConfidence(token, None, "unmapped", False, float("nan"), 0.0,
                                 "unmapped", ["unmapped"])

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
        rv = range_violation_frac(series, role)
        if rv == rv:                       # role has bounds and data present
            data_fit = round(1.0 - rv, 4)
            if rv > 0.1:                   # data doesn't physically fit the role
                flags.append("data_mismatch")
                conf *= (1.0 - min(rv * 2.0, 1.0))

    conf = round(max(0.0, min(1.0, conf)), 4)
    verdict = "high" if conf >= 0.8 else ("medium" if conf >= 0.5 else "low")
    return MappingConfidence(token, role.value, basis, ambiguous, data_fit, conf,
                             verdict, flags)


def score_mapping(tokens, mapping: MappingProvider, series_by_token: dict | None = None) -> list:
    """Score a whole set of tokens against a mapping (optionally with per-token data).

    ``series_by_token``: ``{token: pd.Series}`` to enable the data-fit cross-check.
    Returns a list of :class:`MappingConfidence`, in the order of ``tokens``.
    """
    sbt = series_by_token or {}
    return [score_token(t, mapping, sbt.get(t)) for t in tokens]


def review(tokens, mapping: MappingProvider, series_by_token: dict | None = None,
           *, min_confidence: float = 0.5) -> dict:
    """Summarize a mapping review: what's solid, what needs a human look.

    Returns ``{"scored", "needs_review", "unmapped", "n"}`` where ``needs_review`` is the
    mapped tokens below ``min_confidence`` (or ambiguous / data-mismatched) and
    ``unmapped`` is tokens that didn't resolve at all.
    """
    scored = score_mapping(tokens, mapping, series_by_token)
    unmapped = [s for s in scored if s.basis == "unmapped"]
    needs = [s for s in scored if s.basis != "unmapped"
             and (s.confidence < min_confidence or s.ambiguous or "data_mismatch" in s.flags)]
    return {"scored": scored, "needs_review": needs, "unmapped": unmapped, "n": len(scored)}
