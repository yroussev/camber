"""Equipment-class families: which kind of equipment a free-form class name denotes.

An equipment's class is whatever the source or config called it -- ``"AHU"``, ``"HEAT_PUMP"``,
``"HotWaterPlant"``, ``"CH"`` -- and folder sources use it as a file-name prefix, so the vocabulary
is open. Rules and report sections that only make sense for one *kind* of equipment (an air
handler's supply-air reset, a terminal box's reheat, a chilled-water plant's supply temperature)
need to ask "is this an air handler?" without enumerating every spelling. :func:`equip_family`
answers that for the common spellings and returns ``None`` for a name it does not recognise, so a
caller can keep its roles-only behaviour for unknown classes instead of guessing (provisional,
0.91).

Families: ``air_handler`` (AHU, RTU, DOAS, MAU), ``terminal`` (VAV, CAV, FCAV boxes),
``fan_coil`` (FCU), ``heat_pump`` (packaged / water-source / ground-source heat pumps, VRF),
``chw_plant`` (chillers and chilled-water plants), ``hw_plant`` (boilers and hot-water plants),
``cooling_tower`` (towers and condenser-water plants), ``pump``, ``meter``, ``weather`` and
``refrigeration``.
"""

from __future__ import annotations

import re

__all__ = ["EQUIP_FAMILIES", "equip_family", "family_matches"]

#: family -> the normalised class spellings (upper case, letters and digits only) it covers.
#: A family name itself (``"air_handler"`` -> ``AIRHANDLER``) is also accepted as a spelling.
EQUIP_FAMILIES: dict = {
    "air_handler": (
        "AHU",
        "RTU",
        "DOAS",
        "MAU",
        "MUA",
        "ACU",
        "SAHU",
        "DDAHU",
        "AIRHANDLER",
        "AIRHANDLINGUNIT",
        "ROOFTOPUNIT",
    ),
    "terminal": ("VAV", "CAV", "FCAV", "TERMINAL", "TERMINALUNIT", "VAVBOX", "FPB", "FPVAV"),
    "fan_coil": ("FCU", "FANCOIL", "FANCOILUNIT"),
    "heat_pump": ("HEATPUMP", "HP", "WSHP", "GSHP", "ASHP", "WAHP", "VRF", "VRV"),
    "chw_plant": (
        "CHILLER",
        "CH",
        "CHW",
        "CHWPLANT",
        "CHILLERPLANT",
        "CHILLEDWATERPLANT",
        "CHILLEDWATER",
    ),
    "hw_plant": ("BOILER", "BOILERPLANT", "HWPLANT", "HOTWATERPLANT", "HW", "HWP", "HOTWATER"),
    "cooling_tower": ("COOLINGTOWER", "CT", "TOWER", "CONDENSERWATER", "CWPLANT", "CW"),
    "pump": ("PUMP", "CHWPUMP", "HWPUMP", "CWPUMP", "PUMPS"),
    "meter": ("METER", "ELECMETER", "ELECTRICMETER", "UTILITYMETER", "BTUMETER"),
    "weather": ("WEATHER", "WX", "WEATHERSTATION"),
    "refrigeration": ("REFRIGCIRCUIT", "REFRIGERATION", "REFRIG"),
}

_ALIAS = {}
for _fam, _names in EQUIP_FAMILIES.items():
    _ALIAS[re.sub(r"[^A-Z0-9]", "", _fam.upper())] = _fam
    for _n in _names:
        _ALIAS[_n] = _fam
# a token naming one of these wins over any other token: a "CHILLEDWATER_METER" is a meter
_DOMINANT = ("meter", "weather")


def _norm(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(text).upper())


def equip_family(equip_class) -> str | None:
    """The family a class name denotes (see :data:`EQUIP_FAMILIES`), or ``None`` if unrecognised.

    Matching ignores case and separators (``"heat_pump"``, ``"HeatPump"`` and ``"HEAT-PUMP"`` are
    one spelling) and trailing digits (``"AHU1"``). A compound name is read token by token
    (``"AHU_DOAS"`` is an air handler); a meter or weather token decides the family wherever it
    sits, so ``"CHILLEDWATER_METER"`` is a meter, not a plant.
    """
    if equip_class is None:
        return None
    raw = str(equip_class).strip()
    if not raw:
        return None
    norm = _norm(raw)
    for cand in (norm, norm.rstrip("0123456789")):
        if cand in _ALIAS:
            return _ALIAS[cand]
    # split on separators and CamelCase boundaries
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", raw)
    tokens = [t for t in re.split(r"[^A-Za-z0-9]+", spaced) if t]
    fams = [_ALIAS.get(_norm(t).rstrip("0123456789") or _norm(t)) for t in tokens]
    fams = [f for f in fams if f]
    for dom in _DOMINANT:
        if dom in fams:
            return dom
    return fams[0] if fams else None


def family_matches(equip_class, wanted) -> bool | None:
    """Whether ``equip_class`` belongs to one of the ``wanted`` classes or families.

    ``wanted`` holds class spellings or family names (``("AHU", "RTU")`` or ``("air_handler",)``).
    Returns ``True`` / ``False`` for a recognised class and ``None`` when the class is not
    recognised (an unknown class is neither in nor out -- the caller decides).
    """
    fam = equip_family(equip_class)
    if fam is None:
        return None
    want = {equip_family(w) or str(w) for w in wanted}
    return fam in want
