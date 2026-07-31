"""Assisted point mapping — suggest a Role for an unmapped BAS tag.

The deterministic mapper (`camber.model.mapping.MappingProvider`) resolves a tag to a role by alias
or regex, and `camber.mapping_confidence` scores how sure that resolution is. This adds the missing
piece: when a tag **doesn't** resolve, propose the most likely roles from the vendor-neutral `Role`
vocabulary — from the tag string, its unit, and (if given) whether the data physically fits the
role.

Advisory only, by construction: it returns a ranked, human-confirmed **review list** and never
mutates a `MappingProvider` — a confirmed suggestion is applied by the operator editing the mapping
JSON (the same boundary `camber.aso` keeps toward the BAS). Three suggesters share one interface —
`suggest(token, *, series=None, unit=None, k=3) -> list[RoleSuggestion]`:

- :class:`FeatureSuggester` — dependency-light (numpy/stdlib), always available (this module);
- ``MLSuggester`` — an optional learned backend behind the ``[ml]`` extra (added later);
- ``LLMSuggester`` — an optional suggester over the provider-agnostic agent seam (added later).

numpy/pandas + stdlib.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import asdict, dataclass

from .model.mapping import MappingProvider
from .model.roles import Role
from .sensorhealth import PHYSICAL_BOUNDS, range_violation_frac

__all__ = [
    "ROLE_UNIT",
    "RoleSuggestion",
    "FeatureSuggester",
    "MLSuggester",
    "LLMSuggester",
    "suggest_roles",
    "review_unmapped",
]

# --------------------------------------------------------------------------- unit → candidate roles

_TEMP_ROLES = frozenset(r for r in Role if r.value.endswith("_temp") or r in (Role.OAT,))
_PERCENT_ROLES = frozenset(
    {
        Role.HEAT_VALVE,
        Role.COOL_VALVE,
        Role.OA_DAMPER,
        Role.DAMPER,
        Role.SUPPLY_FAN_SPEED,
        Role.CHW_PUMP_SPEED,
        Role.HW_PUMP_SPEED,
        Role.TOWER_FAN_SPEED,
    }
)
#: normalized unit token -> the roles that unit is physically compatible with
ROLE_UNIT: dict[str, frozenset] = {
    "degf": _TEMP_ROLES,
    "degc": _TEMP_ROLES,
    "f": _TEMP_ROLES,
    "c": _TEMP_ROLES,
    "percent": _PERCENT_ROLES,
    "pct": _PERCENT_ROLES,
    "%": _PERCENT_ROLES,
    "cfm": frozenset({Role.AIRFLOW, Role.OA_AIRFLOW}),
    "gpm": frozenset({Role.CHW_FLOW}),
    "kw": frozenset({Role.POWER}),
    "kwh": frozenset({Role.POWER}),
    "inwc": frozenset({Role.DUCT_STATIC}),
    "inh2o": frozenset({Role.DUCT_STATIC}),
    "ppm": frozenset({Role.CO2}),
    "rh": frozenset({Role.OUTDOOR_RH}),
}


@dataclass(frozen=True)
class RoleSuggestion:
    """One advisory role suggestion for a token."""

    token: str
    role: str  # a Role enum value (always validated via Role(value))
    confidence: float  # 0..1
    basis: str  # ngram | edit_distance | initials | unit | range_fit | ml | llm | combined
    rationale: str  # deterministic, human-readable why

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
    """Best string-similarity of a tag's words to a role's slug: initials or per-word edit
    distance."""
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
            s, basis = _string_score(words, role)  # the dominant lexical signal (0..0.85)
            bases = [basis]
            # unit + range are GATES + small tie-breakers -- they must not erase the lexical order
            if unit_roles:
                if role in unit_roles:
                    s += 0.05
                    bases.append("unit")
                else:
                    s *= 0.4  # a known-incompatible unit strongly demotes
            if series is not None and role in PHYSICAL_BOUNDS:
                rv = range_violation_frac(series, role)
                if rv == rv:  # role has bounds AND data present
                    if rv > 0.1:
                        s *= 1.0 - min(rv, 1.0)  # data doesn't fit -> demote
                    else:
                        s += 0.03
                        bases.append("range_fit")
            s = min(1.0, s)
            if s <= 0.0:
                continue
            rationale = self._rationale(token, role, u, bases)
            scored.append(
                RoleSuggestion(
                    token=token,
                    role=role.value,
                    confidence=round(s, 4),
                    basis="combined" if len(bases) > 1 else bases[0],
                    rationale=rationale,
                )
            )
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


def _range_fit(role: Role, series) -> tuple:
    """Physical-range consistency of ``series`` with ``role``: ``(multiplier, fit)``.

    ``fit`` is ``True`` (data inside bounds), ``False`` (data violates bounds ->
    ``multiplier`` < 1), or ``None`` (role has no bounds or no data -> ``multiplier`` == 1). Reused
    by the ML/LLM tiers so a physically-impossible role can't win regardless of how it was proposed.
    """
    if series is None or role not in PHYSICAL_BOUNDS:
        return 1.0, None
    rv = range_violation_frac(series, role)
    if rv != rv:  # NaN -> no bounds / no data
        return 1.0, None
    if rv > 0.1:
        return 1.0 - min(rv, 1.0), False
    return 1.0, True


def _unpack_label(entry) -> tuple:
    """Normalize a training label to ``(token, role_value, unit, series)`` (unit/series
    optional)."""
    token, role = entry[0], entry[1]
    unit = entry[2] if len(entry) > 2 else None
    series = entry[3] if len(entry) > 3 else None
    return token, Role(role).value, unit, series


class MLSuggester:
    """Optional learned suggester (scikit-learn, behind the ``[ml]`` extra; imported lazily).

    A character-n-gram classifier over tag strings, trained on the caller's / synthetic labels — it
    ships **no pretrained weights** (clean-room). Predictions are gated by the same physical-range
    check as the baseline, so a learned guess that the data contradicts is demoted. Call :meth:`fit`
    (or :meth:`from_mapping`) before :meth:`suggest`.
    """

    def __init__(self):
        self._vec = None
        self._clf = None
        self._classes = None

    @staticmethod
    def _require():
        try:
            import sklearn  # noqa: F401
        except ImportError as e:  # pragma: no cover - exercised only without the extra
            raise ImportError(
                "MLSuggester needs scikit-learn. Install the optional extra: "
                "`pip install camber-toolkit[ml]`. The numpy FeatureSuggester baseline needs none "
                "of it."
            ) from e

    def fit(self, labeled) -> MLSuggester:
        """Train on ``labeled = [(token, role[, unit[, series]]), ...]``. Roles validated via
        ``Role``."""
        self._require()
        from sklearn.feature_extraction.text import CountVectorizer
        from sklearn.linear_model import LogisticRegression

        rows = [_unpack_label(e) for e in labeled]
        tokens = [" ".join(_norm(t)) or t.lower() for t, _, _, _ in rows]
        roles = [r for _, r, _, _ in rows]
        self._vec = CountVectorizer(analyzer="char_wb", ngram_range=(2, 4))
        X = self._vec.fit_transform(tokens)
        self._clf = LogisticRegression(max_iter=1000)
        self._clf.fit(X, roles)
        self._classes = list(self._clf.classes_)
        return self

    @classmethod
    def from_mapping(cls, mapping: MappingProvider, *, extra=None) -> MLSuggester:
        """Bootstrap labels from a mapping's aliases (token->role) plus optional ``extra``
        labels."""
        labeled = [(tok, role.value) for tok, role in getattr(mapping, "aliases", {}).items()]
        labeled += list(extra or [])
        return cls().fit(labeled)

    def suggest(self, token: str, *, series=None, unit=None, k: int = 3) -> list:
        if self._clf is None:
            raise RuntimeError("MLSuggester.suggest called before fit(); train it first.")
        X = self._vec.transform([" ".join(_norm(token)) or token.lower()])
        proba = self._clf.predict_proba(X)[0]
        out = []
        for role_val, p in zip(self._classes, proba):
            role = Role(role_val)
            mult, fit = _range_fit(role, series)
            s = min(1.0, float(p) * mult + (0.02 if fit else 0.0))
            if s <= 0.0:
                continue
            note = f"learned model (p={p:.2f})" + (
                "; data fits bounds" if fit else "; data violates bounds" if fit is False else ""
            )
            out.append(RoleSuggestion(token, role_val, round(s, 4), "ml", note))
        out.sort(key=lambda x: -x.confidence)
        return out[:k]


class LLMSuggester:
    """Optional suggester over the provider-agnostic agent seam — the LLM proposes, the
    deterministic layer disposes.

    Reuses a :class:`camber.agent.client.AgentClient` (no new dependency, no vendor). The model is
    shown the tag, its unit + bounded sample stats, and the whole :class:`Role` vocabulary, and
    asked to propose roles. Every proposal is validated ``Role(value)`` (out-of-vocab dropped)
    **and** re-scored through :func:`mapping_confidence.score_token`, so a physically-inconsistent
    suggestion cannot outrank a good one.
    """

    def __init__(self, client, mapping: MappingProvider | None = None):
        self.client = client
        self.mapping = mapping

    def suggest(self, token: str, *, series=None, unit=None, k: int = 3) -> list:
        raw = self.client.generate(_llm_prompt(token, series, unit))
        proposed = _parse_roles(raw)
        out, seen = [], set()
        for role in proposed:
            if role.value in seen:
                continue
            seen.add(role.value)
            conf = _rescore(token, role, series)
            out.append(
                RoleSuggestion(
                    token,
                    role.value,
                    round(conf, 4),
                    "llm",
                    f"LLM proposed; re-scored to {conf:.2f} for physical consistency",
                )
            )
        out.sort(key=lambda x: -x.confidence)
        return out[:k]


def _llm_prompt(token, series, unit) -> str:
    vocab = ", ".join(r.value for r in Role)
    stats = ""
    if series is not None and len(series):
        try:
            import numpy as _np

            arr = _np.asarray(series, dtype=float)
            arr = arr[~_np.isnan(arr)]
            if arr.size:
                stats = (
                    f" Sample stats: min={arr.min():.2f}, max={arr.max():.2f}, "
                    f"mean={arr.mean():.2f}."
                )
        except Exception:  # pragma: no cover - stats are best-effort
            stats = ""
    u = f" unit='{unit}'" if unit else ""
    return (
        f"A building-automation point is tagged '{token}'.{u}{stats}\n"
        f"Choose the most likely role(s) ONLY from this list: {vocab}.\n"
        f"Reply with role slugs, most likely first."
    )


def _parse_roles(raw: str) -> list:
    """Roles whose slug appears in the model's reply, in order of first appearance (out-of-vocab
    ignored)."""
    low = (raw or "").lower()
    hits = [(low.index(r.value), r) for r in Role if r.value in low]
    return [r for _, r in sorted(hits)]


def _rescore(token, role: Role, series) -> float:
    """Physical-consistency confidence for proposing ``role`` for ``token`` (via score_token)."""
    from .mapping_confidence import score_token

    mp = MappingProvider.from_dict({"aliases": {token: role.value}, "patterns": []})
    return float(score_token(token, mp, series).confidence)


def suggest_roles(
    token: str,
    mapping: MappingProvider | None = None,
    *,
    series=None,
    unit=None,
    suggester=None,
    k: int = 3,
) -> list:
    """Ranked role suggestions for one ``token`` (default :class:`FeatureSuggester`)."""
    s = suggester if suggester is not None else FeatureSuggester(mapping)
    return s.suggest(token, series=series, unit=unit, k=k)


def review_unmapped(
    tokens,
    mapping: MappingProvider,
    *,
    series_by_token: dict | None = None,
    units: dict | None = None,
    suggester=None,
    k: int = 3,
    min_confidence: float = 0.5,
) -> dict:
    """Find the tokens that don't map and attach ranked role suggestions to each.

    Reuses `mapping_confidence.review` to identify the unmapped tokens, then runs ``suggester`` on
    each. Returns ``{"suggestions": {token: [RoleSuggestion,...]}, "review_list": [...],
    "n_unmapped": int}`` — a human-confirm artifact. **Never mutates ``mapping``**; a confirmed
    suggestion is applied by editing the mapping spec (`MappingProvider.from_dict`).
    """
    from .mapping_confidence import review as _review

    sbt = series_by_token or {}
    un = units or {}
    unmapped = [
        s.token
        for s in _review(tokens, mapping, series_by_token, min_confidence=min_confidence)[
            "unmapped"
        ]
    ]
    suggestions = {
        t: suggest_roles(t, mapping, series=sbt.get(t), unit=un.get(t), suggester=suggester, k=k)
        for t in unmapped
    }
    review_list = [
        {"token": t, "suggestions": [s.as_dict() for s in suggestions[t]]} for t in unmapped
    ]
    return {"suggestions": suggestions, "review_list": review_list, "n_unmapped": len(unmapped)}
