"""Axis and colour-bar labels for a role: its display name and unit (0.102, #115).

The unit comes from the same source the live trend viewer uses
(:func:`camber.api.ui.role_units`), so an evidence chart and the viewer name a point alike:
``cond approach temp (°F)``, ``duct static sp (inH₂O)``. A role with no stated unit (a status,
a command, a mode) reads by its name alone, and a derived column (a string such as ``"tons"``)
reads as given.
"""

from __future__ import annotations

import re
from functools import lru_cache


@lru_cache(maxsize=1)
def _units() -> dict:
    from ..api.ui import role_units

    return role_units()


def _slug(key) -> str | None:
    """The role slug of ``key`` (a Role, its value, or its name), or None for a non-role."""
    from ..model.roles import Role

    if isinstance(key, Role):
        return key.value
    s = str(key)
    for cand in (s, s.lower()):
        try:
            return Role(cand).value
        except ValueError:
            continue
    member = Role.__members__.get(s.upper())
    return member.value if member is not None else None


def role_unit(key) -> str:
    """The display unit of ``key``'s role (``"°F"``, ``"%"``, ``"cfm"`` ...), or ``""``."""
    slug = _slug(key)
    return _units().get(slug, "") if slug else ""


def role_label(key) -> str:
    """``"<display name> (<unit>)"`` for a role, its name alone when it has no unit; a non-role
    key (a derived column) is returned as written."""
    slug = _slug(key)
    if slug is None:
        return str(getattr(key, "value", key))
    unit = _units().get(slug, "")
    name = slug.replace("_", " ")
    return f"{name} ({unit})" if unit else name


_UNESCAPED_DOLLAR = re.compile(r"(?<!\\)\$")


def mpl_text(text: object) -> str:
    """``text`` made safe for a matplotlib label or title (0.108, #264).

    Matplotlib reads a ``$`` as the start of math text, so a point or equipment name carrying one
    (a Niagara-style ``$20`` escape, ``VAV $5 to $10``) renders as garbled math or makes the
    figure fail to draw. Every data-derived label, legend entry, tick label and title passes
    through here: each unescaped ``$`` becomes ``\\$``, which matplotlib draws as a plain dollar
    sign. Idempotent, so a label escaped upstream is not escaped twice. ``None`` reads as ``""``.
    """
    if text is None:
        return ""
    return _UNESCAPED_DOLLAR.sub(r"\\$", str(text))
