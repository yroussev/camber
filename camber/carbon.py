"""Carbon (greenhouse-gas) accounting from building energy consumption.

Converts energy use by fuel to CO2-equivalent emissions using published emission
factors (EPA eGRID for grid electricity, EIA for fuels). Electricity factors vary
widely by grid region and over time, so the electricity default here is a neutral
placeholder -- pass the region's current eGRID factor for a real number. Fuel
combustion factors are more stable.

Factors are kg CO2e per unit; include CH4/N2O via their global-warming potentials
in the factor where a full CO2e is wanted.
"""

from __future__ import annotations

from dataclasses import dataclass

# kg CO2e per unit. Electricity is grid- and year-specific -- override it.
DEFAULT_FACTORS: dict = {
    "electricity_kwh": 0.40,     # placeholder; supply your eGRID subregion value (~0.4-0.9)
    "natural_gas_therm": 5.30,   # EIA combustion factor, kg CO2e/therm
    "natural_gas_kwh": 0.181,    # if gas is metered in kWh
    "fuel_oil_gal": 10.21,       # kg CO2e/gal (No. 2)
    "propane_gal": 5.72,         # kg CO2e/gal
    "district_steam_kbtu": 0.066,
}


@dataclass(frozen=True)
class Emissions:
    """Emissions result: per-fuel and total CO2e."""

    by_fuel: dict           # fuel -> kg CO2e
    total_kg: float         # total kg CO2e
    intensity_kg_sf: float  # kg CO2e / ft2 (NaN if area not given)

    @property
    def total_tonnes(self) -> float:
        """Total CO2e in metric tonnes."""
        return round(self.total_kg / 1000.0, 4)


def emissions(consumption_by_fuel: dict, *, factors: dict | None = None,
              gross_sf: float | None = None) -> Emissions:
    """Compute CO2e from ``{fuel_key: amount}`` using ``factors`` (kg CO2e/unit).

    Fuel keys must match the factor keys (see :data:`DEFAULT_FACTORS`). Unknown
    keys raise, so a typo can't silently drop a fuel from the footprint.
    """
    f = {**DEFAULT_FACTORS, **(factors or {})}
    by_fuel = {}
    for fuel, amount in consumption_by_fuel.items():
        if fuel not in f:
            raise KeyError(f"no emission factor for '{fuel}'; supply via factors=")
        by_fuel[fuel] = round(float(amount) * f[fuel], 4)
    total = round(sum(by_fuel.values()), 4)
    intensity = round(total / gross_sf, 6) if gross_sf else float("nan")
    return Emissions(by_fuel=by_fuel, total_kg=total, intensity_kg_sf=intensity)
