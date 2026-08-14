"""BACnet discovery → CAMBER role mapping (the inverse-of-tags adapter, for BACnet).

Given the objects a discovery run found (:mod:`camber.ingest.bacnet_discovery`), bootstrap a
point→:class:`~camber.model.roles.Role` mapping — the BACnet analogue of
:func:`camber.interop.haystack_semantic.roles_from_haystack`. A BACnet object carries two mapping
signals the Haystack tags don't spell out directly: its **object type** (analog / binary /
multistate) and its **engineering units** enum. Those two axes bound the candidate roles, and the
object *name* then picks within that bounded set via the same feature suggester the assisted-mapping
path uses (:mod:`camber.mapping_assist`), so we reuse that machinery rather than re-deriving it.

Import-light: this module imports only core (`model.mapping`, `model.roles`, `mapping_assist`) and
duck-types the discovered objects (anything with ``object_name`` / ``object_id`` / ``units``), so
it needs neither bacpypes3 nor the discovery module at import time.
"""

from __future__ import annotations

from ..mapping_assist import ROLE_UNIT, FeatureSuggester, review_unmapped
from ..model.mapping import MappingProvider
from ..model.roles import STATUS_ROLES, Role

#: BACnet ``EngineeringUnits`` enum name → the normalized unit token understood by
#: :data:`camber.mapping_assist.ROLE_UNIT` (buckets, not the full ~200-member enum; unknowns fall
#: through to name-only matching).
BACNET_UNIT_TO_TOKEN = {
    "degreesFahrenheit": "degf",
    "degreesCelsius": "degc",
    "degreesKelvin": "degc",
    "percent": "percent",
    "percentObscurationPerFoot": "percent",
    "cubicFeetPerMinute": "cfm",
    "litersPerSecond": "cfm",
    "cubicMetersPerHour": "cfm",
    "usGallonsPerMinute": "gpm",
    "litersPerMinute": "gpm",
    "inchesOfWater": "inwc",
    "pascals": "inwc",
    "kilopascals": "inwc",
    "kilowatts": "kw",
    "watts": "kw",
    "btusPerHour": "kw",
    "tons": "kw",
    "partsPerMillion": "ppm",
    "percentRelativeHumidity": "rh",
}

#: BACnet object type → the coarse role family it constrains to. Analog objects are constrained by
#: their *unit*; binary objects are status points; multistate objects are stage points.
OBJECT_TYPE_ROLE_HINT = {
    "analogInput": "numeric",
    "analogOutput": "numeric",
    "analogValue": "numeric",
    "binaryInput": "status",
    "binaryOutput": "status",
    "binaryValue": "status",
    "multiStateInput": "stage",
    "multiStateOutput": "stage",
    "multiStateValue": "stage",
}

# A conservative subset of standard ASHRAE 135 EngineeringUnits integer codes (some clients report
# the enum as an int). Only high-confidence codes are included; unknown ints degrade gracefully.
_UNIT_CODE_TO_NAME = {
    29: "percentRelativeHumidity",
    47: "watts",
    48: "kilowatts",
    62: "degreesCelsius",
    63: "degreesKelvin",
    64: "degreesFahrenheit",
    84: "cubicFeetPerMinute",
    96: "partsPerMillion",
    98: "percent",
}

_STAGE_ROLES = (Role.COMPRESSOR_STAGE, Role.HEAT_STAGE)


def _unit_key(s) -> str:
    """Separator/case-insensitive key so ``degreesFahrenheit`` == ``degrees-fahrenheit``."""
    return "".join(ch for ch in str(s).lower() if ch.isalnum())


# BACNET_UNIT_TO_TOKEN keyed by the separator-insensitive form, so units rendered as the camelCase
# enum name OR the dashed ASN.1 string (bacpypes3 does the latter) both resolve.
_UNIT_TOKEN_BY_KEY = {_unit_key(k): v for k, v in BACNET_UNIT_TO_TOKEN.items()}


def normalize_bacnet_unit(units) -> str:
    """Reduce a BACnet ``units`` to a :data:`camber.mapping_assist.ROLE_UNIT` token, or ``""``.

    Accepts the ``EngineeringUnits`` enum name (``"degreesFahrenheit"``), the dashed ASN.1 string
    (``"degrees-fahrenheit"``, what bacpypes3 renders), an enum object exposing ``.name``, or the
    standard integer code (``64``). Anything unrecognized returns ``""`` (the caller then falls back
    to name-only matching), never raises.
    """
    if units is None or isinstance(units, bool):
        return ""
    if isinstance(units, int):
        name = _UNIT_CODE_TO_NAME.get(units, "")
    else:
        name = getattr(units, "name", None) or str(units)
        if name.isdigit():
            name = _UNIT_CODE_TO_NAME.get(int(name), "")
    return BACNET_UNIT_TO_TOKEN.get(name) or _UNIT_TOKEN_BY_KEY.get(_unit_key(name), "")


def _object_type_family(object_type) -> str | None:
    if object_type in OBJECT_TYPE_ROLE_HINT:
        return OBJECT_TYPE_ROLE_HINT[object_type]
    t = str(object_type or "")
    if t.startswith("analog"):
        return "numeric"
    if t.startswith("binary"):
        return "status"
    if t.lower().startswith("multistate"):
        return "stage"
    return None


def _candidate_roles(object_type, unit_token) -> tuple:
    """The candidate Role vocabulary for one object = object-type family ∩ unit-implied roles."""
    fam = _object_type_family(object_type)
    if fam == "status":
        return tuple(STATUS_ROLES)
    if fam == "stage":
        return _STAGE_ROLES
    unit_roles = ROLE_UNIT.get(unit_token, frozenset())
    if unit_roles:  # analog/container with a known unit → constrain to that unit's roles
        return tuple(unit_roles)
    return tuple(Role)  # unknown unit → let the object name decide across the whole vocabulary


def _fields(obj) -> tuple:
    name = getattr(obj, "object_name", "") or ""
    oid = getattr(obj, "object_id", None)
    otype = oid[0] if oid else ""
    return name, otype, normalize_bacnet_unit(getattr(obj, "units", ""))


def roles_from_bacnet(objects, *, mapping=None, vendor_aliases=None, min_confidence=0.6) -> dict:
    """Return ``{object_name → Role}`` for the discovered ``objects`` that resolve confidently.

    Per object, in order: (1) an operator ``mapping`` override on the object name wins; (2) a
    ``vendor_aliases`` entry (proprietary property name → role slug, e.g. from
    :mod:`camber.interop.bacnet_vendor`) wins next; (3) otherwise the object name is ranked against
    the candidate roles implied by its object type ∩ units (via
    :class:`camber.mapping_assist.FeatureSuggester`), keeping the top role if its confidence clears
    ``min_confidence``. Only resolved objects are returned (like ``roles_from_haystack``).
    """
    va = {str(k).lower(): Role(v) for k, v in (vendor_aliases or {}).items()}
    out: dict = {}
    for obj in objects:
        name, otype, unit_token = _fields(obj)
        if not name:
            continue
        role = mapping.role_of(name) if mapping is not None else None
        if role is None:
            role = va.get(name.lower())
        if role is None:
            vocab = _candidate_roles(otype, unit_token)
            ranked = FeatureSuggester(mapping, vocab=vocab).suggest(
                name, unit=unit_token or None, k=1
            )
            if ranked and ranked[0].confidence >= min_confidence:
                role = Role(ranked[0].role)
        if role is not None:
            out[name] = role
    return out


def mapping_from_bacnet(objects, *, mapping=None, vendor_aliases=None) -> MappingProvider:
    """Build a :class:`~camber.model.mapping.MappingProvider` from discovered objects."""
    roles = roles_from_bacnet(objects, mapping=mapping, vendor_aliases=vendor_aliases)
    return MappingProvider.from_dict({"aliases": {n: r.value for n, r in roles.items()}})


def review_bacnet(objects, mapping, *, series_by_name=None, k=3, min_confidence=0.5) -> dict:
    """Ranked role suggestions for the discovered objects an existing ``mapping`` doesn't resolve.

    Shapes discovery into :func:`camber.mapping_assist.review_unmapped` inputs (object names as
    tokens, normalized BACnet units as the unit hint). ``series_by_name`` (name → trend series) is
    optional — pass it only if you've pulled snapshots, since range-fit needs a series discovery
    alone doesn't provide; unit + name signals carry the suggestion otherwise. Advisory only.
    """
    objs = list(objects)
    names = [getattr(o, "object_name", "") or "" for o in objs]
    tokens = [n for n in names if n]
    units = {n: normalize_bacnet_unit(getattr(o, "units", "")) for o, n in zip(objs, names) if n}
    return review_unmapped(
        tokens,
        mapping,
        series_by_token=series_by_name,
        units=units,
        k=k,
        min_confidence=min_confidence,
    )


__all__ = [
    "BACNET_UNIT_TO_TOKEN",
    "OBJECT_TYPE_ROLE_HINT",
    "normalize_bacnet_unit",
    "roles_from_bacnet",
    "mapping_from_bacnet",
    "review_bacnet",
]
