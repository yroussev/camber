# Energy conversion factor tables (provisional, 0.92)

This page lists every entry of the factor sets bundled in `camber.energy_factors`. It is
generated from the JSON files by `python scripts/energy_factors_refresh.py --docs --write`, and a
test fails when it is out of date. How the sets are used, the "M" note and how to add a set are
in [Energy units: Energy conversion factors](UNITS.md#energy-conversion-factors).

Values are as printed in the source, with its thousands separators. A key such as `kcf` or
`MMcf` is CAMBER's unambiguous name for the printed unit. **(!)** marks a multiplier the source
prints inconsistently with its own heat content. It is kept as printed, the note under the table
explains it, and using it raises a caveat. The Notes column gives the source's footnote numbers.

<!-- BEGIN GENERATED FACTOR TABLES -->

### ENERGY STAR Portfolio Manager Technical Reference: Thermal Energy Conversions

`energy_star_thermal_2015`: U.S. Environmental Protection Agency, ENERGY STAR, edition 2015-08, retrieved 2026-09-28. Source: <https://portfoliomanager.energystar.gov/pdf/reference/Thermal%20Conversions.pdf> (sha256 `52fb681b4c3c626a7132a7546ba86b236921a304c4e990c309598e47f1c9ece1`). Multipliers to kBtu, as printed.

#### Electricity (Grid Purchase and Onsite Renewable) (`electricity`)

| Input unit (as printed) | Key | US kBtu per unit | US heat content | CA kBtu per unit | CA heat content | Notes |
|---|---|---|---|---|---|---|
| kBtu (thousand Btu) | `kBtu` | 1 |  | 1 |  |  |
| MBtu/MMBtu (million Btu) | `MMBtu` | 1,000 |  | 1,000 |  | 5 |
| kWh (thousand Watt-hours) | `kWh` | 3.412 |  | 3.412 |  |  |
| MWh (million Watt-hours) | `MWh` | 3,412 |  | 3,412 |  |  |
| GJ (billion joules) | `GJ` | 947.817 |  | 947.817 |  |  |

Heat content: US Not Applicable; CA Not Applicable.

#### Natural Gas (`natural_gas`)

| Input unit (as printed) | Key | US kBtu per unit | US heat content | CA kBtu per unit | CA heat content | Notes |
|---|---|---|---|---|---|---|
| kBtu (thousand Btu) | `kBtu` | 1 |  | 1 |  |  |
| MBtu/MMBtu (million Btu) | `MMBtu` | 1,000 |  | 1,000 |  | 5 |
| cf (cubic feet) | `ft3` | 1.026 | 1,026 Btu/cf | 1.031 | 1,031.43 Btu/cf | 1, 2a |
| Ccf (hundred cubic feet) | `CCF` | 102.6 | 1,026 Btu/cf | 103.143 | 1,031.43 Btu/cf | 1, 2a |
| Kcf (thousand cubic feet) | `kcf` | 1,026 | 1,026 Btu/cf | 1,031 | 1,031.43 Btu/cf | 1, 2a |
| Mcf (million cubic feet) | `MMcf` | 1,026,000 | 1,026 Btu/cf | 1,031,430 | 1,031.43 Btu/cf | 1, 2a, 5 |
| Therms | `therm` | 100 |  | 100 |  |  |
| cubic meters | `m3` | 36.303 (!) | 1,026 Btu/cf | 36.425 | 1,031.43 Btu/cf | 1, 2a |
| GJ (billion joules) | `GJ` | 947.817 |  | 947.817 |  |  |

Heat content: US 1,026 Btu/cf; CA 1,031.43 Btu/cf.

- US `m3`: The printed 36.303 kBtu per cubic metre is 0.19% above 1,026 Btu/cf x 35.3147 cf/m3 = 36.233 kBtu. Transcribed as printed; Portfolio Manager applies the printed multiplier.

#### Fuel Oil (No. 1) (`fuel_oil_1`)

| Input unit (as printed) | Key | US kBtu per unit | US heat content | CA kBtu per unit | CA heat content | Notes |
|---|---|---|---|---|---|---|
| kBtu (thousand Btu) | `kBtu` | 1 |  | 1 |  |  |
| MBtu/MMBtu (million Btu) | `MMBtu` | 1,000 |  | 1,000 |  | 5 |
| Gallons (US) | `gal_US` | 139 | 0.139 MBtu/gallon | 139.210 | 0.139210 MBtu/gallon | 1, 2a |
| Gallons (UK) | `gal_UK` | 166.927 | 0.139 MBtu/gallon | 167.184 | 0.139210 MBtu/gallon | 1, 2a |
| liters | `L` | 36.720 | 0.139 MBtu/gallon | 36.775 | 0.139210 MBtu/gallon | 1, 2a |
| GJ (billion joules) | `GJ` | 947.817 |  | 947.817 |  |  |

Heat content: US 0.139 MBtu/gallon; CA 0.139210 MBtu/gallon.

#### Fuel Oil (No. 2) (`fuel_oil_2`)

| Input unit (as printed) | Key | US kBtu per unit | US heat content | CA kBtu per unit | CA heat content | Notes |
|---|---|---|---|---|---|---|
| kBtu (thousand Btu) | `kBtu` | 1 |  | 1 |  |  |
| MBtu/MMBtu (million Btu) | `MMBtu` | 1,000 |  | 1,000 |  | 5 |
| Gallons (US) | `gal_US` | 138 | 0.138 MBtu/gallon | 139.210 | 0.139210 MBtu/gallon | 1, 2a |
| Gallons (UK) | `gal_UK` | 165.726 | 0.138 MBtu/gallon | 167.184 | 0.139210 MBtu/gallon | 1, 2a |
| liters | `L` | 36.456 | 0.138 MBtu/gallon | 36.775 | 0.139210 MBtu/gallon | 1, 2a |
| GJ (billion joules) | `GJ` | 947.817 |  | 947.817 |  |  |

Heat content: US 0.138 MBtu/gallon; CA 0.139210 MBtu/gallon.

#### Fuel Oil (No. 4) (`fuel_oil_4`)

| Input unit (as printed) | Key | US kBtu per unit | US heat content | CA kBtu per unit | CA heat content | Notes |
|---|---|---|---|---|---|---|
| kBtu (thousand Btu) | `kBtu` | 1 |  | 1 |  |  |
| MBtu/MMBtu (million Btu) | `MMBtu` | 1,000 |  | 1,000 |  | 5 |
| Gallons (US) | `gal_US` | 146 | 0.146 MBtu/gallon | 139.210 | 0.139210 MBtu/gallon | 1, 2a |
| Gallons (UK) | `gal_UK` | 175.333 | 0.146 MBtu/gallon | 167.184 | 0.139210 MBtu/gallon | 1, 2a |
| liters | `L` | 38.569 | 0.146 MBtu/gallon | 36.775 | 0.139210 MBtu/gallon | 1, 2a |
| GJ (billion joules) | `GJ` | 947.817 |  | 947.817 |  |  |

Heat content: US 0.146 MBtu/gallon; CA 0.139210 MBtu/gallon.

#### Fuel Oil (No. 5 & No. 6) (`fuel_oil_5_6`)

| Input unit (as printed) | Key | US kBtu per unit | US heat content | CA kBtu per unit | CA heat content | Notes |
|---|---|---|---|---|---|---|
| kBtu (thousand Btu) | `kBtu` | 1 |  | 1 |  | 3 |
| MBtu/MMBtu (million Btu) | `MMBtu` | 1,000 |  | 1,000 |  | 3, 5 |
| Gallons (US) | `gal_US` | 150 | 0.150 MBtu/gallon | 152.485 | 0.152485 MBtu/gallon | 1, 2a, 3 |
| Gallons (UK) | `gal_UK` | 180.137 | 0.150 MBtu/gallon | 183.127 | 0.152485 MBtu/gallon | 1, 2a, 3 |
| liters | `L` | 39.626 | 0.150 MBtu/gallon | 40.282 | 0.152485 MBtu/gallon | 1, 2a, 3 |
| GJ (billion joules) | `GJ` | 947.817 |  | 947.817 |  | 3 |

Heat content: US 0.150 MBtu/gallon; CA 0.152485 MBtu/gallon.

#### Diesel (`diesel`)

| Input unit (as printed) | Key | US kBtu per unit | US heat content | CA kBtu per unit | CA heat content | Notes |
|---|---|---|---|---|---|---|
| kBtu (thousand Btu) | `kBtu` | 1 |  | 1 |  |  |
| MBtu/MMBtu (million Btu) | `MMBtu` | 1,000 |  | 1,000 |  | 5 |
| Gallons (US) | `gal_US` | 138 | 0.138 MBtu/gallon | 137.416 | 0.137416 MBtu/gallon | 1, 2a |
| Gallons (UK) | `gal_UK` | 165.726 | 0.138 MBtu/gallon | 165.029 | 0.137416 MBtu/gallon | 1, 2a |
| liters | `L` | 36.456 | 0.138 MBtu/gallon | 36.301 | 0.137416 MBtu/gallon | 1, 2a |
| GJ (billion joules) | `GJ` | 947.817 |  | 947.817 |  |  |

Heat content: US 0.138 MBtu/gallon; CA 0.137416 MBtu/gallon.

#### Kerosene (`kerosene`)

| Input unit (as printed) | Key | US kBtu per unit | US heat content | CA kBtu per unit | CA heat content | Notes |
|---|---|---|---|---|---|---|
| kBtu (thousand Btu) | `kBtu` | 1 |  | 1 |  |  |
| MBtu/MMBtu (million Btu) | `MMBtu` | 1,000 |  | 1,000 |  | 5 |
| Gallons (US) | `gal_US` | 135 | 0.135 MBtu/gallon | 135.191 | 0.135191 MBtu/gallon | 1, 2a |
| Gallons (UK) | `gal_UK` | 162.123 | 0.135 MBtu/gallon | 162.358 | 0.135191 MBtu/gallon | 1, 2a |
| liters | `L` | 35.663 | 0.135 MBtu/gallon | 35.714 | 0.135191 MBtu/gallon | 1, 2a |
| GJ (billion joules) | `GJ` | 947.817 |  | 947.817 |  |  |

Heat content: US 0.135 MBtu/gallon; CA 0.135191 MBtu/gallon.

#### Propane (`propane`)

| Input unit (as printed) | Key | US kBtu per unit | US heat content | CA kBtu per unit | CA heat content | Notes |
|---|---|---|---|---|---|---|
| kBtu (thousand Btu) | `kBtu` | 1 |  | 1 |  | 4 |
| MBtu/MMBtu (million Btu) | `MMBtu` | 1,000 |  | 1,000 |  | 4, 5 |
| Cf (cubic feet) | `ft3` | 2.516 |  | 2.516 |  | 4 |
| Ccf (hundred cubic feet) | `CCF` | 251.6 |  | 251.6 |  | 4 |
| Kcf (thousand cubic feet) | `kcf` | 2,516 |  | 2,516 |  | 4 |
| Gallons (US) | `gal_US` | 92 | 0.092 MBtu/gallon | 90.809 (!) | 0.09089 MBtu/gallon | 1, 2a, 4 |
| Gallons (UK) | `gal_UK` | 110.484 | 0.092 MBtu/gallon | 109.057 (!) | 0.09089 MBtu/gallon | 1, 2a, 4 |
| liters | `L` | 24.304 | 0.092 MBtu/gallon | 23.989 (!) | 0.09089 MBtu/gallon | 1, 2a, 4 |
| GJ (billion joules) | `GJ` | 947.817 |  | 947.817 |  | 4 |

Heat content: US 0.092 MBtu/gallon; CA 0.09089 MBtu/gallon.

- CA `CCF`: Gaseous propane (note 4). The table states no gas-phase heat content; 2.516 kBtu/cf is implied by the multiplier.
- CA `L`: Consistent with 0.090809 MBtu/gallon, not the printed 0.09089 (see the US-gallon entry).
- CA `ft3`: Gaseous propane (note 4). The table states no gas-phase heat content; 2.516 kBtu/cf is implied by the multiplier.
- CA `gal_UK`: Consistent with 0.090809 MBtu/gallon, not the printed 0.09089 (see the US-gallon entry).
- CA `gal_US`: The printed heat content reads 0.09089 MBtu/gallon, but the gallon, UK-gallon and litre multipliers (90.809, 109.057, 23.989) all follow 0.090809 MBtu/gallon; the heat-content cell appears to drop a digit. Transcribed as printed.
- CA `kcf`: Gaseous propane (note 4). The table states no gas-phase heat content; 2.516 kBtu/cf is implied by the multiplier.
- US `CCF`: Gaseous propane (note 4). The table states no gas-phase heat content; 2.516 kBtu/cf is implied by the multiplier.
- US `ft3`: Gaseous propane (note 4). The table states no gas-phase heat content; 2.516 kBtu/cf is implied by the multiplier.
- US `kcf`: Gaseous propane (note 4). The table states no gas-phase heat content; 2.516 kBtu/cf is implied by the multiplier.

#### District Steam (`district_steam`)

| Input unit (as printed) | Key | US kBtu per unit | US heat content | CA kBtu per unit | CA heat content | Notes |
|---|---|---|---|---|---|---|
| kBtu (thousand Btu) | `kBtu` | 1 |  | 1 |  |  |
| MBtu/MMBtu (million Btu) | `MMBtu` | 1,000 |  | 1,000 |  | 5 |
| Lbs | `lb` | 1.194 | 1,194 Btu/Lb | 1.194 | 1,194 Btu/Lb | 1c, 2b |
| kLbs (thousand pounds) | `klb` | 1,194 | 1,194 Btu/Lb | 1,194 | 1,194 Btu/Lb | 1c, 2b |
| MLbs (million pounds) | `MMlb` | 1,194,000 | 1,194 Btu/Lb | 1,194,000 | 1,194 Btu/Lb | 1c, 2b, 5 |
| therms | `therm` | 100.0 |  | 100.000 |  |  |
| GJ (billion joules) | `GJ` | 947.817 |  | 947.817 |  |  |
| kg | `kg` | 2.632 | 1,194 Btu/Lb | 2.632 | 1,194 Btu/Lb | 1c, 2b |

Heat content: US 1,194 Btu/Lb; CA 1,194 Btu/Lb.

#### District Hot Water (`district_hot_water`)

| Input unit (as printed) | Key | US kBtu per unit | US heat content | CA kBtu per unit | CA heat content | Notes |
|---|---|---|---|---|---|---|
| kBtu (thousand Btu) | `kBtu` | 1 |  | 1 |  |  |
| MBtu/MMBtu (million Btu) | `MMBtu` | 1,000 |  | 1,000 |  | 5 |
| Therms | `therm` | 100 |  | 100 |  |  |
| GJ (billion joules) | `GJ` | 947.817 |  | 947.817 |  |  |

Heat content: US Not Needed - No Volume Entry Units; CA Not Needed - No Volume Entry Units.

#### District Chilled Water (All Types) (`district_chilled_water`)

| Input unit (as printed) | Key | US kBtu per unit | US heat content | CA kBtu per unit | CA heat content | Notes |
|---|---|---|---|---|---|---|
| kBtu (thousand Btu) | `kBtu` | 1 |  | 1 |  |  |
| MBtu/MMBtu (million Btu) | `MMBtu` | 1,000 |  | 1,000 |  | 5 |
| Ton Hours | `ton-hour` | 12.0 |  | 12.0 |  |  |
| GJ (billion joules) | `GJ` | 947.817 |  | 947.817 |  |  |

Heat content: US Not Needed - No Volume Entry Units; CA Not Needed - No Volume Entry Units.

#### Coal (anthracite) (`coal_anthracite`)

| Input unit (as printed) | Key | US kBtu per unit | US heat content | CA kBtu per unit | CA heat content | Notes |
|---|---|---|---|---|---|---|
| kBtu (thousand Btu) | `kBtu` | 1 |  | 1 |  |  |
| MBtu/MMBtu (million Btu) | `MMBtu` | 1,000 |  | 1,000 |  | 5 |
| Tons | `short_ton` | 25,090 | 25.09 MBtu/ton | 23,818 | 23.818 MBtu/ton | 1, 2a |
| Lbs | `lb` | 12.545 | 25.09 MBtu/ton | 11.909 | 23.818 MBtu/ton | 1, 2a |
| kLbs (thousand pounds) | `klb` | 12,545 | 25.09 MBtu/ton | 11,909 | 23.818 MBtu/ton | 1, 2a |
| MLbs (million pounds) | `MMlb` | 12,545,000 | 25.09 MBtu/ton | 11,909,055 | 23.818 MBtu/ton | 1, 2a, 5 |
| Tonnes (metric) | `tonne` | 27,658.355 | 25.09 MBtu/ton | 26,255 | 23.818 MBtu/ton | 1, 2a |
| GJ (billion joules) | `GJ` | 947.817 |  | 947.817 |  |  |

Heat content: US 25.09 MBtu/ton; CA 23.818 MBtu/ton.

- CA `short_ton`: Tons are short tons (2,000 lb), as the Lbs multiplier confirms.
- US `short_ton`: Tons are short tons (2,000 lb), as the Lbs multiplier confirms.

#### Coal (bituminous) (`coal_bituminous`)

| Input unit (as printed) | Key | US kBtu per unit | US heat content | CA kBtu per unit | CA heat content | Notes |
|---|---|---|---|---|---|---|
| kBtu (thousand Btu) | `kBtu` | 1 |  | 1 |  |  |
| MBtu/MMBtu (million Btu) | `MMBtu` | 1,000 |  | 1,000 |  | 5 |
| Tons | `short_ton` | 24,930 | 24.93 MBtu/ton | 21,496 | 21.496 MBtu/ton | 1, 2a |
| Lbs | `lb` | 12.465 | 24.93 MBtu/ton | 10.748 | 21.496 MBtu/ton | 1, 2a |
| kLbs (thousand pounds) | `klb` | 12,465 | 24.93 MBtu/ton | 10,748 | 21.496 MBtu/ton | 1, 2a |
| MLbs (million pounds) | `MMlb` | 12,465,000 | 24.93 MBtu/ton | 10,748,245 | 21.496 MBtu/ton | 1, 2a, 5 |
| Tonnes (metric) | `tonne` | 27,482 | 24.93 MBtu/ton | 23,695 | 21.496 MBtu/ton | 1, 2a |
| GJ (billion joules) | `GJ` | 947.817 |  | 947.817 |  |  |

Heat content: US 24.93 MBtu/ton; CA 21.496 MBtu/ton.

- CA `short_ton`: Tons are short tons (2,000 lb), as the Lbs multiplier confirms.
- US `short_ton`: Tons are short tons (2,000 lb), as the Lbs multiplier confirms.

#### Coke (`coke`)

| Input unit (as printed) | Key | US kBtu per unit | US heat content | CA kBtu per unit | CA heat content | Notes |
|---|---|---|---|---|---|---|
| kBtu (thousand Btu) | `kBtu` | 1 |  | 1 |  |  |
| MBtu/MMBtu (million Btu) | `MMBtu` | 1,000 |  | 1,000 |  | 5 |
| Tons | `short_ton` | 24,800 | 24.80 MBtu/ton | 24,790 | 24.79 MBtu/ton | 1, 2a |
| Lbs | `lb` | 12.4 | 24.80 MBtu/ton | 12.395 | 24.79 MBtu/ton | 1, 2a |
| kLbs (thousand pounds) | `klb` | 12,400 | 24.80 MBtu/ton | 12,395 | 24.79 MBtu/ton | 1, 2a |
| MLbs (million pounds) | `MMlb` | 12,400,000 | 24.80 MBtu/ton | 12,394,876 | 24.79 MBtu/ton | 1, 2a, 5 |
| Tonnes (metric) | `tonne` | 27,339 | 24.80 MBtu/ton | 27,326 | 24.79 MBtu/ton | 1, 2a |
| GJ (billion joules) | `GJ` | 947.817 |  | 947.817 |  |  |

Heat content: US 24.80 MBtu/ton; CA 24.79 MBtu/ton.

- CA `short_ton`: Tons are short tons (2,000 lb), as the Lbs multiplier confirms.
- US `short_ton`: Tons are short tons (2,000 lb), as the Lbs multiplier confirms.

#### Wood (`wood`)

| Input unit (as printed) | Key | US kBtu per unit | US heat content | CA kBtu per unit | CA heat content | Notes |
|---|---|---|---|---|---|---|
| kBtu (thousand Btu) | `kBtu` | 1 |  | 1 |  |  |
| MBtu/MMBtu (million Btu) | `MMBtu` | 1,000 |  | 1,000 |  | 5 |
| Tons | `short_ton` | 17,480 | 17.48 MBtu/Ton | 15,477 | 15.48 MBtu/Ton | 1, 2a |
| Tonnes (metric) | `tonne` | 15,857 (!) | 17.48 MBtu/Ton | 17,061 | 15.48 MBtu/Ton | 1, 2a |
| GJ (billion joules) | `GJ` | 947.817 |  | 947.817 |  |  |

Heat content: US 17.48 MBtu/Ton; CA 15.48 MBtu/Ton.

- CA `short_ton`: Tons are short tons (2,000 lb), as the Lbs multiplier confirms.
- US `short_ton`: Tons are short tons (2,000 lb), as the Lbs multiplier confirms.
- US `tonne`: The printed 15,857 kBtu per metric tonne is 17,480 / 1.10231, i.e. the ton multiplier divided rather than multiplied by the 1.10231 short tons in a tonne; 17.48 MBtu/ton gives 19,268 kBtu per tonne. Transcribed as printed; Portfolio Manager applies the printed multiplier.

#### Other (`other`)

| Input unit (as printed) | Key | US kBtu per unit | US heat content | CA kBtu per unit | CA heat content | Notes |
|---|---|---|---|---|---|---|
| kBtu (thousand Btu) | `kBtu` | 1.0 |  | 1.0 |  |  |

Heat content: US Not Needed - No Volume Entry Units; CA Not Needed - No Volume Entry Units.

<!-- END GENERATED FACTOR TABLES -->
