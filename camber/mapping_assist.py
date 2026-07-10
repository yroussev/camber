"""Assisted point mapping — suggest a Role for an unmapped BAS tag.

The deterministic mapper (`camber.model.mapping.MappingProvider`) resolves a tag to a role by alias
or regex, and `camber.mapping_confidence` scores how sure that resolution is. This adds the missing
piece: when a tag **doesn't** resolve, propose the most likely roles from the vendor-neutral `Role`
vocabulary — from the tag string, its unit, and (if given) whether the data physically fits the role.

Advisory only, by construction: it returns a ranked, human-confirmed **review list** and never mutates
a `MappingProvider` — a confirmed suggestion is applied by the operator editing the mapping JSON (the
same boundary `camber.aso` keeps toward the BAS). Three suggesters share one interface —
`suggest(token, *, series=None, unit=None, k=3) -> list[RoleSuggestion]`:

- :class:`FeatureSuggester` — dependency-light (numpy/stdlib), always available (this module);
- ``MLSuggester`` — an optional learned backend behind the ``[ml]`` extra (added later);
- ``LLMSuggester`` — an optional suggester over the provider-agnostic agent seam (added later).

numpy/pandas + stdlib.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, asdict

from .model.mapping import MappingProvider
from .model.roles import Role
from .sensorhealth import PHYSICAL_BOUNDS, range_violation_frac

# --------------------------------------------------------------------------- unit → candidate roles

_TEMP_ROLES = frozenset(r for r in Role if r.value.endswith("_temp") or r in (Role.OAT,))
_PERCENT_ROLES = frozenset({Role.HEAT_VALVE, Role.COOL_VALVE, Role.OA_DAMPER, Role.DAMPER,
                            Role.SUPPLY_FAN_SPEED, Role.CHW_PUMP_SPEED, Role.HW_PUMP_SPEED,
                            Role.TOWER_FAN_SPEED})
#: normalized unit token -> the roles that unit is physically compatible with
ROLE_UNIT: dict[str, frozenset] = {
    "degf": _TEMP_ROLES, "degc": _TEMP_ROLES, "f": _TEMP_ROLES, "c": _TEMP_ROLES,
    "percent": _PERCENT_ROLES, "pct": _PERCENT_ROLES, "%": _PERCENT_ROLES,
    "cfm": frozenset({Role.AIRFLOW, Role.OA_AIRFLOW}), "gpm": frozenset({Role.CHW_FLOW}),
    "kw": frozenset({Role.POWER}), "kwh": frozenset({Role.POWER}),
    "inwc": frozenset({Role.DUCT_STATIC}), "inh2o": frozenset({Role.DUCT_STATIC}),
    "ppm": frozenset({Role.CO2}), "rh": frozenset({Role.OUTDOOR_RH}),
}


@dataclass(frozen=True)
class RoleSuggestion:
    """One advisory role suggestion for a token."""

    token: str
    role: str                 # a Role enum value (always validated via Role(value))
    confidence: float         # 0..1
    basis: str                # ngram | edit_distance | initials | unit | range_fit | ml | llm | combined
    rationale: str            # deterministic, human-readable why

    def as_dict(self) -> dict:
        return asdict(self)


def _norm(s: str) -> list[str]:
    """Lowercase a tag and split into word tokens (on non-alphanumerics and digit runs)."""
    return [w for w in re.split(r"[^a-z0-9]+", s.lower()) if w and not w.isdigit()]


def _initials(role: Role) -> str:
    return "".join(w[0] for w in role.value.split("_"))


def _norm_unit(unit) -> str:
    return re.sub(r"[^a-z%]+", "", str(unit).lower()) if unit else ""


def _string_score(words: list[str], role: Role) -> tuple[float, str]:
    """Best string-similarity of a tag's words to a role's slug: initials or per-word edit distance."""
    slug = role.value
    joined = "".join(words)
    initials = _initials(role)
    # initials hit (SAT -> supply_air_temp) is the strongest lexical signal
    if len(initials) >= 2 and (initials in joined or initials in words):
        return 0.85, "initials"
    slug_words = slug.split("_")
    best = 0.0
    for w in words:
        for sw in slug_words:
            best = max(best, difflib.SequenceMatcher(None, w, sw).ratio())
        best = max(best, difflib.SequenceMatcher(None, w, slug).ratio())
    return round(best, 4), "ngram" if best >= 0.6 else "edit_distance"


class FeatureSuggester:
    """Dependency-light role suggester from a tag's string, unit, and data range-fit."""

    def __init__(self, mapping: MappingProvider | None = None, *, vocab=tuple(Role)):
        self.mapping = mapping
        self.vocab = tuple(vocab)

    def suggest(self, token: str, *, series=None, unit=None, k: int = 3) -> list:
        words = _norm(token)
        u = _norm_unit(unit)
        unit_roles = ROLE_UNIT.get(u, frozenset())
        scored = []
        for role in self.vocab:
            s, basis = _string_score(words, role)   # the dominant lexical signal (0..0.85)
            bases = [basis]
            # unit + range are GATES + small tie-breakers -- they must not erase the lexical order
            if unit_roles:
                if role in unit_roles:
                    s += 0.05; bases.append("unit")
                else:
                    s *= 0.4                        # a known-incompatible unit strongly demotes
            if series is not None and role in PHYSICAL_BOUNDS:
                rv = range_violation_frac(series, role)
                if rv == rv:                        # role has bounds AND data present
                    if rv > 0.1:
                        s *= (1.0 - min(rv, 1.0))    # data doesn't fit -> demote
                    else:
                        s += 0.03; bases.append("range_fit")
            s = min(1.0, s)
            if s <= 0.0:
                continue
            rationale = self._rationale(token, role, u, bases)
            scored.append(RoleSuggestion(token=token, role=role.value, confidence=round(s, 4),
                                         basis="combined" if len(bases) > 1 else bases[0],
                                         rationale=rationale))
        scored.sort(key=lambda x: -x.confidence)
        return scored[:k]

    @staticmethod
    def _rationale(token, role, unit, bases) -> str:
        bits = []
        if "initials" in bases:
            bits.append(f"'{token}' matches the initials of {role.value}")
        elif "ngram" in bases or "edit_distance" in bases:
            bits.append(f"'{token}' is string-similar to {role.value}")
        if "unit" in bases:
            bits.append(f"unit '{unit}' fits {role.value}")
        if "range_fit" in bases:
            bits.append("the data stays within this role's physical bounds")
        return "; ".join(bits) or f"weak match to {role.value}"


def suggest_roles(token: str, mapping: MappingProvider | None = None, *, series=None, unit=None,
                  suggester=None, k: int = 3) -> list:
    """Ranked role suggestions for one ``token`` (default :class:`FeatureSuggester`)."""
    s = suggester if suggester is not None else FeatureSuggester(mapping)
    return s.suggest(token, series=series, unit=unit, k=k)


def review_unmapped(tokens, mapping: MappingProvider, *, series_by_token: dict | None = None,
                    units: dict | None = None, suggester=None, k: int = 3,
                    min_confidence: float = 0.5) -> dict:
    """Find the tokens that don't map and attach ranked role suggestions to each.

    Reuses `mapping_confidence.review` to identify the unmapped tokens, then runs ``suggester`` on
    each. Returns ``{"suggestions": {token: [RoleSuggestion,...]}, "review_list": [...],
    "n_unmapped": int}`` — a human-confirm artifact. **Never mutates ``mapping``**; a confirmed
    suggestion is applied by editing the mapping spec (`MappingProvider.from_dict`).
    """
    from .mapping_confidence import review as _review

    sbt = series_by_token or {}
    un = units or {}
    unmapped = [s.token for s in _review(tokens, mapping, series_by_token,
                                         min_confidence=min_confidence)["unmapped"]]
    suggestions = {t: suggest_roles(t, mapping, series=sbt.get(t), unit=un.get(t),
                                    suggester=suggester, k=k) for t in unmapped}
    review_list = [{"token": t, "suggestions": [s.as_dict() for s in suggestions[t]]}
                   for t in unmapped]
    return {"suggestions": suggestions, "review_list": review_list, "n_unmapped": len(unmapped)}
