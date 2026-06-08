"""Point-name parsing and matching for the synthetic / wide-CSV column scheme.

This handles column names of the form ``<prefix><id>_<measure>`` used by the
synthetic generator (`synth.py`) and the demo CLI -- e.g. ``AHU1_HeC``,
``AHU12_CC``, ``Z5_Temp``. (The per-point real-data exports use a different
``<TYPE>_<ID>_<MEASURE>`` scheme handled independently in ``inventory.parse_name``.)

Naming convention (a factual data format, restated in our own terms):

* The **measure** is the token after the final underscore.
* The **equipment id** is the trailing run of 1-2 digits immediately before that
  final underscore; the **prefix** is whatever precedes the id. Only the last one
  or two digits are taken as the id, so ``AHU12_CC`` -> (AHU, 12) but a longer
  digit run is truncated to its final two -- a deliberate cap, not an accident.
* A column with no trailing digit before the measure (e.g. ``Bldg_TempOa``) has
  no equipment id; callers filter those out.

Matching a *generic* measure name (e.g. ``AHU_HeC``, which carries no id) to a
concrete column for a given equipment id follows two rules: a building-level
point (``Bldg*`` sharing the measure suffix), or an exact
``<prefix><id><suffix>`` match.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Trailing 1-2 digit equipment id at the end of the pre-measure segment. Greedy
# so a two-digit id wins over one; capped at two digits by the quantifier.
_TRAILING_ID = re.compile(r"(\d{1,2})$")


@dataclass(frozen=True)
class Point:
    """A parsed data-column name ``<prefix><id>_<measure>``."""

    column: str          # original column name, e.g. "AHU1_HeC"
    prefix: str          # equipment prefix, e.g. "AHU"
    equip_id: int | None # integer id, e.g. 1  (None if no trailing digit)
    measure: str         # measure suffix WITHOUT the underscore, e.g. "HeC"

    @property
    def equip(self) -> str:
        """Full equipment name incl. id, e.g. ``AHU1``."""
        return f"{self.prefix}{self.equip_id}" if self.equip_id is not None else self.prefix


def equip_id_len(name: str) -> int:
    """Number of digits (1 or 2) in the trailing equipment id of ``name``.

    Returns 1 when there is no trailing digit run before the measure.
    """
    head = name.rpartition("_")[0]
    m = _TRAILING_ID.search(head)
    return len(m.group(1)) if m else 1


def parse_point(name: str) -> Point:
    """Parse a column name into :class:`Point`.

    ``equip_id`` is ``None`` when no trailing digit precedes the measure (e.g. a
    building-level point), so callers can skip non-equipment columns.
    """
    if "_" not in name:
        return Point(column=name, prefix=name, equip_id=None, measure="")
    head, _, measure = name.rpartition("_")
    m = _TRAILING_ID.search(head)
    if m:
        equip_id: int | None = int(m.group(1))
        prefix = head[: m.start()]
    else:
        equip_id = None
        prefix = head
    return Point(column=name, prefix=prefix, equip_id=equip_id, measure=measure)


def measure_suffix(generic_name: str) -> str:
    """The ``_<measure>`` portion (incl. leading underscore) of a generic name.

    For a generic measure name like ``AHU_HeC`` this returns ``_HeC``.
    """
    i = generic_name.rfind("_")
    return generic_name if i == -1 else generic_name[i:]


def matches(header: str, generic_name: str, equip_id: int) -> bool:
    """Does column ``header`` correspond to ``generic_name`` for ``equip_id``?

    True when ``header`` is a building-level point sharing the measure suffix
    (``Bldg*<suffix>``) or an exact ``<prefix><id><suffix>`` match.
    """
    suffix = measure_suffix(generic_name)              # "_HeC"
    last_us = generic_name.rfind("_")
    prefix = generic_name[:last_us] if last_us != -1 else generic_name  # "AHU"
    if header.startswith("Bldg") and header.endswith(suffix):
        return True
    return header == f"{prefix}{equip_id}{suffix}"


def find_column(headers, generic_name: str, equip_id: int):
    """Return the first header matching ``generic_name`` for ``equip_id``, else None."""
    for h in headers:
        if matches(h, generic_name, equip_id):
            return h
    return None


def count_equipment(headers, prefix: str) -> int:
    """Largest equipment id present for ``prefix`` across ``headers`` (0 if none)."""
    best = 0
    for h in headers:
        p = parse_point(h)
        if p.equip_id is not None and p.prefix == prefix:
            best = max(best, p.equip_id)
    return best
