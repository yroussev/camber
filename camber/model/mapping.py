"""Map a building's raw measure tokens to vendor-neutral :class:`Role` values.

Each BAS names points differently, so meaning lives in a per-source **mapping**:
a table from raw measure token (the part of a point name that says *what* it
measures, e.g. ``HWValve``, ``HHW_Valve``, ``SupplyAir``) to a :class:`Role`.

A :class:`MappingProvider` resolves a token to a role by, in order:
1. an exact alias in the table (case-insensitive),
2. a regex pattern rule (for families like ``*Temp`` -> SPACE_TEMP),
falling back to ``None`` (unmapped) so callers can skip or report it.

Mappings are plain data and can be loaded from a dict or a config file, so adding
a new building/BAS is a config task, not a code change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .roles import Role


@dataclass
class MappingProvider:
    """Resolve raw measure tokens to roles for one data source.

    ``aliases``: exact token -> Role (matched case-insensitively).
    ``patterns``: list of (regex, Role); first match wins, tried after aliases.
    """

    aliases: dict[str, Role] = field(default_factory=dict)
    patterns: list[tuple[str, Role]] = field(default_factory=list)
    _compiled: list[tuple[re.Pattern, Role]] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self):
        # normalize alias keys to lowercase for case-insensitive lookup
        self.aliases = {k.lower(): v for k, v in self.aliases.items()}
        self._compiled = [(re.compile(p, re.IGNORECASE), r) for p, r in self.patterns]

    def role_of(self, token: str) -> Role | None:
        """Return the Role for a raw measure ``token``, or None if unmapped."""
        if token is None:
            return None
        hit = self.aliases.get(token.lower())
        if hit is not None:
            return hit
        for rx, role in self._compiled:
            if rx.search(token):
                return role
        return None

    def candidates(self, token: str) -> list:
        """Every role ``token`` could resolve to: the alias hit (if any) first, then
        each matching pattern's role -- so callers can detect ambiguity (the same token
        competing for more than one role) that ``role_of`` hides behind first-match.
        """
        out = []
        if token is None:
            return out
        a = self.aliases.get(token.lower())
        if a is not None:
            out.append(a)
        for rx, role in self._compiled:
            if rx.search(token):
                out.append(role)
        return out

    def roles_present(self, tokens) -> dict[Role, str]:
        """Given raw tokens, return {Role: token} for those that map (first wins)."""
        out: dict[Role, str] = {}
        for t in tokens:
            r = self.role_of(t)
            if r is not None and r not in out:
                out[r] = t
        return out

    @classmethod
    def from_dict(cls, spec: dict) -> "MappingProvider":
        """Build from a plain dict: {'aliases': {token: role_slug},
        'patterns': [[regex, role_slug], ...]}. Role slugs are Role values."""
        aliases = {k: Role(v) for k, v in spec.get("aliases", {}).items()}
        patterns = [(p, Role(v)) for p, v in spec.get("patterns", [])]
        return cls(aliases=aliases, patterns=patterns)
