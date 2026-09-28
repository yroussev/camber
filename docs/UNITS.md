# Energy units: IP and SI reporting (provisional, 0.92)

CAMBER reports energy in the unit system a config chooses: **kBtu** for IP or **kWh** for SI
(issue #69). This covers energy, demand and EUI only. Temperatures, pressures and flows are
unchanged, and the rules and detectors keep their internal IP units, so no detection result moves.

```json
{"site": "Example office", "units": {"system": "ip"}, "...": "..."}
```

| System | Energy | Demand / power | EUI |
|---|---|---|---|
| `ip` | kBtu | kBtu/h | kBtu/ft2/yr |
| `si` | kWh | kW | kWh/m2/yr |

**Without a `units` block nothing changes.** Every output stays in the meter's own unit, byte for
byte as before 0.92. `units.area` (`ft2` or `m2`) states the unit a config's floor areas are in.
It defaults to `ft2` for IP and `m2` for SI. An unknown system or key is an error.

The module is `camber.energy_units` (provisional: names and signatures may change in a minor
release).

## Conversion factors

The canonical internal unit is the **kWh**. Every conversion goes through it, so any pair of
units round-trips to float precision. The factors below are exact by definition. The rounded forms
are the usual printed values.

| Quantity | Factor | Basis |
|---|---|---|
| 1 Btu (International Table) | 1055.05585262 J, exact | NIST SP 811, Appendix B.8 |
| 1 kWh | 3.6 MJ, exact | definition of the watt-hour |
| 1 kWh | 3.41214163 kBtu (printed **3.412142**) | the two lines above |
| 1 kBtu | 1.05505585 MJ (printed **1.055056**) | the IT Btu |
| 1 therm | 100,000 Btu = 100 kBtu | the IT therm |
| 1 MMBtu = 1 dekatherm | 1,000 kBtu = 10 therms | |
| 1 ton-hour | 12,000 Btu = 12 kBtu | refrigeration ton |
| 1 ton | 12,000 Btu/h = 12 kBtu/h | |
| 1 MBH | 1 kBtu/h | |
| 1 GJ | 1,000 MJ = 277.78 kWh | |
| 1 ft | 0.3048 m, exact | so 1 m2 = 10.7639 ft2 and 1 m3 = 35.3147 ft3 |
| 1 lb | 0.45359237 kg, exact | |

The US therm is defined on the 59 F Btu (105.4804 MJ), 0.024% below the IT therm. CAMBER uses the
IT therm throughout. EUI converts as 1 kBtu/ft2/yr = 3.15459 kWh/m2/yr.

### The 3.412 in `bps.EUI_FACTORS_KBTU`

`camber.bps.site_eui` has always converted electricity at **3.412 kBtu/kWh**. That is the exact
3.412142 rounded, and it is 0.004% low. It is **kept**, so existing EUI outputs do not move.
It is also ENERGY STAR Portfolio Manager's standard multiplier, and so is its 100 kBtu per therm
(see [Energy conversion factors](#energy-conversion-factors); a test keeps them equal).
The unit-aware `bps.site_eui_units` uses the exact factors. On a building that is all electric,
the two differ by 0.004% of the EUI (108.24 against 108.2428 kBtu/ft2/yr in the test example). Its
other rows (100 kBtu per therm, 12 kBtu per ton-hour) are already exact.

## Units the parser reads

`parse_unit` ignores case, spaces and `-`, `_` and `·`, and reads the common spellings:

- **Energy:** kWh (`kilowatt-hours`), MWh, GWh, Wh, Btu, kBtu, MMBtu (`dekatherm`, `Dth`), therm
  (`therms`, `thm`), ton-hour (`ton-hr`, `ton·h`), kJ, MJ and GJ.
- **Power:** kW, MW, W, Btu/h, kBtu/h (`MBH`), MMBtu/h and ton (`tons`).
- **Gas volume:** ft3 (`cf`), CCF, Mcf and m3. A bare `Mcf` is a **thousand** cubic feet, the
  US gas-utility reading. `MMcf` is read only through a factor set.
- **Steam mass:** lb, klb and kg.

It **refuses**:

- an unknown unit;
- an ambiguous one. `MBtu` and `Mlb` are ambiguous because M means a thousand in US utility usage
  and a million in SI. Write `kBtu` or `MMBtu`, and `klb`;
- a unit of the wrong kind, such as a bare `ton` where energy is expected (it suggests
  `ton-hour`).

**Gas by volume and steam by mass have no default.** A volume converts only with an explicit
`heat_content`, such as `"10.37 therm/Mcf"`, `"1037 Btu/ft3"` or
`{"value": 1.037, "unit": "MMBtu/Mcf"}`. A steam mass converts only with an explicit `enthalpy`,
such as `"1000 Btu/lb"`. Use the heat content printed on the gas bill, or the supplier's value.

```python
from camber.energy_units import convert, convert_rate, UnitSystem

convert(1.0, "therm", "kWh")  # 29.3071
convert(120.0, "Mcf", "kBtu", heat_content="10.37 therm/Mcf")  # 124,440
convert_rate(1.20, "therm", "kWh")  # $/therm -> $/kWh
UnitSystem.of("si").eui_factor("kBtu/ft2/yr")  # 3.15459
```

## Where the system applies

### M&V savings, bands and SEP results

A config `mv` entry's reported quantities are converted. The fits stay in the meter's own unit,
so R², CV(RMSE), NMBE, the savings percentage and SEnPI are the same in every system, and the
IP and SI savings agree after conversion. What is converted:

- the savings, projections, measured totals and bands;
- the adjusted figures;
- the chain links and their SEP terms (the variance terms by the factor squared);
- the ledger's resolved amounts;
- the waterfall;
- the `auto` sensitivity table.

The model coefficients and an indicator's per-day `rate` stay in the meter's unit, which the
finding names.

Each converted finding carries `energy_unit` (kBtu or kWh), `meter_unit` (the unit the fits are
in) and `unit_system`, and its summary names the unit after every energy number:

```text
Gas meter: avoided energy 554,023 kBtu (16.3%) ± 85,632 kBtu at 90% over 365 reporting days ...
```

- **Trended meters.** An `mv` entry on trended equipment must name its meter's **rate** unit under
  a unit system, for example `"units": "kW"` (or `Btu/h`, `kBtu/h`, `MBH`, `tons`). The daily
  energy is its hourly integral: kWh, Btu, kBtu or ton-hours. An entry without `units` is an
  error once a system is set.
- **Billing entries.** The bills' unit comes from `bills.units` or the file's `units` column, and
  more than one unit in the column is an error. Under a unit system the unit must parse.
  `bills.heat_content` is required for gas billed by volume (Mcf, CCF, m3), and `bills.enthalpy`
  for steam billed by mass. Both are validated whenever they are given. Opt-in, a
  `units.factor_set` supplies them instead (see
  [Energy conversion factors](#energy-conversion-factors)). Without a system, bills in Mcf are
  fitted and reported in Mcf, as before. Bills in a bare `Mcf` carry a caveat under a system
  (see [The "M" problem](#the-m-problem)).
  `BillingSeries.converted(unit, heat_content=...)` converts a series in code.

### SEP primary energy

Annex B multipliers apply to delivered **energy**. `sep.primary_energy` and
`sep.aggregate_energy_types` take `units=` (the delivered unit of every energy type),
`heat_content=` / `enthalpy=` (per type, for volumes and steam) and `energy_unit=` (`"kWh"` by
default, or `"kBtu"`). Each type is converted to that energy unit first, and the multiplier is
applied after. A type without a unit is refused, and so is an ambiguous unit or a volume without a
heat content. The result carries `unit` and `delivered_units`, and a caveat names each heat content
used. Without `units` the amounts must already share one energy unit, as before.

```python
from camber.mandv.sep import primary_energy

primary_energy(
    {"grid_electricity": 1_000_000, "natural_gas": 1_200},
    units={"grid_electricity": "kWh", "natural_gas": "Mcf"},
    heat_content={"natural_gas": "10.37 therm/Mcf"},
    energy_unit="kBtu",
)
```

### EUI

`bps.site_eui_units(energy, units, area, area_unit="ft2" | "m2", system="ip" | "si")` returns
site EUI in kBtu/ft2/yr or kWh/m2/yr from amounts in their own units. The area unit is required.
In a config, `report.benchmark` takes an optional `unit` (the unit its EUIs are stated in,
`kBtu/ft2/yr` by default). Under a unit system both EUIs are converted to the system's EUI unit,
and the audit report labels them with it.

### Cost and carbon

Per-unit prices and emission factors convert with `convert_rate`. For example,
`convert_rate(5.30, "therm", "MMBtu")` gives 53.0 kg CO2e/MMBtu.

A config `price` block also accepts a rate in any unit:

```json
"price": {"electricity": {"rate": 95.0, "per": "MWh"},
          "gas": {"rate": 8.5, "per": "Mcf", "heat_content": "10.37 therm/Mcf"}}
```

This is converted to the `electricity_per_kwh` / `gas_per_therm` the cost estimators use. The
plain `electricity_per_kwh` / `gas_per_therm` keys are read as before.
`fault_economics.annotate_costs(..., units="ip" | "si")` adds `waste_energy` (both fuels' site
energy) and `energy_unit` beside the existing `waste_kwh` / `waste_therms`.

## Energy conversion factors

`camber.energy_factors` (provisional) holds **published conversion factor sets**: the multipliers
that turn a billed quantity (cubic feet of gas, gallons of oil, pounds of steam, tons of coal)
into energy. Each set is a JSON file in the package, transcribed from its source and pinned to it
by URL, edition, retrieval date and the source file's sha256. Every entry is listed in
[the factor tables](ENERGY-FACTORS.md).

### The ENERGY STAR set

`energy_star_thermal_2015` is the ENERGY STAR Portfolio Manager technical reference *Thermal
Energy Conversions* (U.S. EPA, August 2015), retrieved 2026-09-28 from
<https://portfoliomanager.energystar.gov/pdf/reference/Thermal%20Conversions.pdf>. It is a work
of the U.S. Government, and the numbers are transcribed as printed.

- **Figure 2** (quick reference) gives the standard multipliers between kWh, MWh, kBtu, MMBtu and
  GJ, the same for both countries.
- **Figure 3** gives the multiplier to kBtu and the heat content for every unit Portfolio Manager
  accepts, for 17 meter types: electricity, natural gas, fuel oil No. 1, No. 2, No. 4 and
  No. 5 & 6, diesel, kerosene, propane, district steam, hot water and chilled water, anthracite
  and bituminous coal, coke, wood and "other". That is 105 unit rows per region, 210 entries.
- **Regions.** `US` (U.S. property assumptions) and `CA` (Canadian property assumptions). The US
  heat contents come from the EPA Greenhouse Gas Reporting Rule, 40 CFR 98, subpart C,
  Tables C-1 and C-2. The Canadian fossil-fuel heat contents come from Statistics Canada's
  *Report on Energy Supply and Demand* (Text Table 1.1, 2009). District steam uses the
  International District Energy Association's 1,194 Btu/lb for both. For example, natural gas is
  1,026 Btu/cf (US) and 1,031.43 Btu/cf (CA).
- **Source discrepancies.** Five printed multipliers disagree with the table's own heat content
  beyond rounding: US natural gas per cubic metre (36.303, where 1,026 Btu/cf gives 36.233), the
  three Canadian propane liquid rows (they follow 0.090809 MBtu/gallon; the printed heat content
  reads 0.09089), and US wood per tonne (15,857, which is 17,480 divided by 1.10231 instead of
  multiplied). They are kept as printed, because they are what Portfolio Manager applies. Each
  is marked in the file, and using one raises a caveat.

```python
from camber.energy_factors import factor_sets, factor_for, to_kbtu

factor_sets()  # ['energy_star_thermal_2015']
to_kbtu(120, "kcf", "natural_gas", factor_set="energy_star_thermal_2015", region="US")  # 123,120
to_kbtu(500, "gallons", "fuel_oil_2", factor_set="energy_star_thermal_2015", region="CA")
factor_for("klb", "district_steam", factor_set="energy_star_thermal_2015", region="US").describe()
```

`to_kbtu(value, unit, meter_type, *, factor_set, region)` takes a meter type by its key
(`natural_gas`), its printed name (`"Fuel Oil (No. 2)"`) or an alias (`gas`, `steam`,
`fuel_oil`). A unit the set does not list for that meter type is an error that lists the units it
does. `factor_for` returns the entry with its heat content, footnotes and caveats. `to_kbtu`
issues each caveat as an `EnergyFactorWarning`.

### The "M" problem

!!! warning "Mcf: a thousand or a million cubic feet?"
    ENERGY STAR writes **M for million** and k/K for thousand: its `Mcf` is a *million* cubic
    feet (1,026,000 kBtu), its `Kcf` a thousand, and its `MBtu` a million Btu (the source's own
    note 5). Many US gas utilities write **M for thousand**: their `Mcf` is a *thousand* cubic
    feet, and a million is `MMcf`. The two readings differ by 1,000x.

CAMBER keeps one reading for bare strings everywhere. A bare `Mcf` is a **thousand** cubic feet,
as `camber.energy_units` reads it, and `MBtu` and `Mlb` are refused as ambiguous. Each factor set
records its own convention (`conventions.M`), and its entries use unambiguous keys: `kcf` and
`MMcf`, `klb` and `MMlb`, and `MMBtu`. With a factor set these labels are accepted: `kcf`,
`thousand cubic feet`, `MMcf`, `million cf`, `MMBtu`, `klb`, `MMlb` and `million lb`.

**Whenever a bare `Mcf` is converted,** in `to_kbtu` or in a billing entry under a unit system,
with a factor set or with an explicit heat content, a caveat names the conflict. If the bill
follows ENERGY STAR's convention, the energy is 1,000x too low. To remove the caveat, write
`kcf` or `MMcf`.

### In a config (opt-in)

```json
"units": {"system": "ip", "factor_set": "energy_star_thermal_2015", "region": "US"}
```

`factor_set` and `region` go together. Without them nothing changes: a gas volume still needs
`bills.heat_content`, and steam by mass `bills.enthalpy`. With them, a billing entry in a unit
that `camber.energy_units` cannot convert on its own is converted with the set's multiplier to
kBtu, and then to the system's energy unit. Such units are volumes and masses without an explicit
heat content, and gallons, litres, tons, tonnes, `kcf`, `MMcf` and `MMlb`.

- `bills.meter_type` names the fuel, for example `"fuel_oil_2"`, `"propane"` or
  `"coal_bituminous"`. Without it, a gas volume is read as `natural_gas` and a mass as
  `district_steam`, as `camber.energy_units` reads them, and a caveat says so. Gallons and tons
  need it.
- **An explicit `heat_content` or `enthalpy` always wins.** The set is not used then.
- Energy units (kWh, therms, MMBtu, ton-hours) keep the exact factors above. An electric or
  therm meter reports the same with or without a factor set.
- Every finding of the entry records the factor as `energy_factor` (the set, region, meter type,
  printed unit, multiplier, heat content and footnotes), and carries a caveat such as
  `Converted with energy_star_thermal_2015 (..., 2015-08), US, Natural Gas: 1 Kcf (thousand
  cubic feet) = 1,026 kBtu, heat content 1,026 Btu/cf.`

### Exact and rounded factors

`camber.energy_units` converts with exact definitions: 1 kWh = 3.412142 kBtu. A published set
carries its publisher's numbers, which are often rounded: ENERGY STAR converts a kWh at 3.412
kBtu and a GJ at 947.817 kBtu. Which one applies depends on the path. `to_kbtu` with a set uses
the set's numbers. The default path, and energy-unit bills under a factor set, use the exact
factors. `bps.EUI_FACTORS_KBTU` keeps its historical values. Its electricity (3.412), gas (100
per therm) and chilled-water (12 per ton-hour) rows equal ENERGY STAR's. Its propane (91.6 per
gallon) and fuel-oil (138.7 per gallon) rows are other published values, within 0.5% of ENERGY
STAR's 92 and 138, and are unchanged.

### Adding or updating a set

A new set is a new JSON file in `camber/energy_factors/`. It needs no code change, unless it uses
a physical unit outside `camber.energy_factors.UNIT_KEYS`. Examples are an EIA or Statistics
Canada update, an ASHRAE table, site-to-source factors, or emission factors with their own
`kind` and `output_unit`. A new edition of an existing source is a new file, so that results
citing the old one stay reproducible.

`scripts/energy_factors_refresh.py` validates a draft (`--validate file.json`) and checks a
set's source against its pinned sha256 (`--check`). It re-pins the sha256, retrieval date and
edition (`--pin ... --edition ... --write`) and regenerates [the factor tables](ENERGY-FACTORS.md)
(`--docs --write`). Its docstring is the checklist. On load, every set is validated: the schema,
the unit keys, the footnote references, and that each multiplier equals heat content x unit size
within the printed precision. A row the source prints inconsistently must be marked a
`source_discrepancy`.

## Not converted (0.92)

- Factor sets apply only to config billing entries and `camber.energy_factors` itself, not to
  SEP, EUI or price conversions, which still take an explicit heat content.

- Temperatures, pressures, flows, and every rule's own metrics.
- The versioned-baseline chain report (`camber mv report`).
- The fleet report's peer-median EUI and the agent context, which stay in kBtu/ft2/yr.
- A trended gas meter metered by volume flow (cfh). Only power rates are accepted for trended
  meters.
