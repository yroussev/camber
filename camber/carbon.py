"""Carbon (greenhouse-gas) accounting from building energy consumption.

Converts energy use by fuel to CO2-equivalent emissions using published emission
factors (EPA eGRID for grid electricity, EIA for fuels). Electricity factors vary
widely by grid region and over time, so the electricity default here is a neutral
placeholder -- pass the region's current eGRID factor for a real number. Fuel
combustion factors are more stable.

Factors are kg CO2e per unit; include CH4/N2O via their global-warming potentials
in the factor where a full CO2e is wanted.

**Per-unit factors** (0.93, #70; provisional). A factor may also be given in any unit, as
``{"rate": 53.06, "per": "MMBtu"}`` (or with ``"heat_content"`` for a factor per gas volume,
``"enthalpy"`` per steam mass); :func:`factor_per` converts it exactly
(:func:`camber.energy_units.convert_rate`) to the unit the fuel key names by its suffix
(``natural_gas_therm`` -> therm, ``electricity_kwh`` -> kWh, ``district_steam_kbtu`` -> kBtu). A
key whose unit is not energy (``fuel_oil_gal``) takes a factor only in its own unit. Published
greenhouse-gas factor sets (EPA eGRID, EIA) are not bundled; see docs/CARBON.md.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

__all__ = [
    "DEFAULT_FACTORS",
    "Emissions",
    "emissions",
    "factor_per",
]

# kg CO2e per unit. Electricity is grid- and year-specific -- override it.
DEFAULT_FACTORS: dict = {
    "electricity_kwh": 0.40,  # placeholder; supply your eGRID subregion value (~0.4-0.9)
    "natural_gas_therm": 5.30,  # EIA combustion factor, kg CO2e/therm
    "natural_gas_kwh": 0.181,  # if gas is metered in kWh
    "fuel_oil_gal": 10.21,  # kg CO2e/gal (No. 2)
    "propane_gal": 5.72,  # kg CO2e/gal
    "district_steam_kbtu": 0.066,
}


@dataclass(frozen=True)
class Emissions:
    """Emissions result: per-fuel and total CO2e."""

    by_fuel: dict  # fuel -> kg CO2e
    total_kg: float  # total kg CO2e
    intensity_kg_sf: float  # kg CO2e / ft2 (NaN if area not given)

    @property
    def total_tonnes(self) -> float:
        """Total CO2e in metric tonnes."""
        return round(self.total_kg / 1000.0, 4)


def emissions(
    consumption_by_fuel: dict, *, factors: dict | None = None, gross_sf: float | None = None
) -> Emissions:
    """Compute CO2e from ``{fuel_key: amount}`` using ``factors`` (kg CO2e/unit).

    Fuel keys must match the factor keys (see :data:`DEFAULT_FACTORS`). Unknown
    keys raise, so a typo can't silently drop a fuel from the footprint. A factor may be a number
    (kg CO2e per the key's unit) or a per-unit spec such as ``{"rate": 53.06, "per": "MMBtu"}``,
    converted by :func:`factor_per` (0.93).
    """
    f = {
        k: (factor_per(v, k) if isinstance(v, Mapping) else v)
        for k, v in {**DEFAULT_FACTORS, **(factors or {})}.items()
    }
    by_fuel = {}
    for fuel, amount in consumption_by_fuel.items():
        if fuel not in f:
            raise KeyError(f"no emission factor for '{fuel}'; supply via factors=")
        by_fuel[fuel] = round(float(amount) * f[fuel], 4)
    total = round(sum(by_fuel.values()), 4)
    intensity = round(total / gross_sf, 6) if gross_sf else float("nan")
    return Emissions(by_fuel=by_fuel, total_kg=total, intensity_kg_sf=intensity)


def _key_unit(key: str) -> str:
    """The unit a fuel key names by its last ``_`` part (``natural_gas_therm`` -> ``therm``)."""
    return str(key).rsplit("_", 1)[-1]


def factor_per(spec, key: str) -> float:
    """kg CO2e per the unit ``key`` names, from ``spec`` (0.93, #70; provisional).

    ``spec`` is ``{"rate": <kg CO2e>, "per": "<unit>"}``, optionally with ``"heat_content"`` (a
    factor per gas volume, e.g. per Mcf) or ``"enthalpy"`` (per steam mass). An energy key
    (``..._kwh``, ``..._therm``, ``..._kbtu``, ``..._mmbtu``) converts exactly through
    :func:`camber.energy_units.convert_rate`: ``{"rate": 53.06, "per": "MMBtu"}`` for
    ``natural_gas_therm`` is 5.306 kg CO2e/therm. Any other key (``fuel_oil_gal``) takes a spec
    only in its own unit. ``ValueError`` on a malformed spec or a unit that cannot convert.
    """
    from .energy_units import convert_rate, parse_unit

    if not isinstance(spec, Mapping) or "rate" not in spec or "per" not in spec:
        raise ValueError(
            f'factor for {key!r} must be {{"rate": ..., "per": "<unit>"}}, got {spec!r}'
        )
    rate = float(spec["rate"])
    if not rate >= 0:
        raise ValueError(f"factor for {key!r}: rate must be a non-negative number, got {rate!r}")
    unit = _key_unit(key)
    try:
        target = parse_unit(unit, kind="energy").name
    except ValueError:
        if str(spec["per"]).strip().lower() != unit.lower():
            raise ValueError(
                f"{key!r} is per {unit}, not an energy unit: give its factor per {unit}"
            ) from None
        return rate
    try:
        return convert_rate(
            rate,
            spec["per"],
            target,
            heat_content=spec.get("heat_content"),
            enthalpy=spec.get("enthalpy"),
        )
    except ValueError as e:
        raise ValueError(f"factor for {key!r}: {e}") from None
