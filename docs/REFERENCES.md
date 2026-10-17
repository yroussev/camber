# References: PNNL Building Re-tuning

CAMBER's rules and advisory actions follow the free **Building Re-tuning** material that the
Pacific Northwest National Laboratory (PNNL) publishes for large commercial buildings. This page
lists every resource CAMBER links to, and which rules each one backs. The same list is the
registry `camber.references` (provisional, 0.96), which the reports use:

- the audit report's findings table and its **Recommended actions** table carry
  **Learn more** links to the guide behind each finding (plain text: `learn more: <ids>`);
- the [RCx report](RCX-REPORT.md) adds the links to each issue page, links chapter 9 from its
  **Verify on site** walk-down checklist, and ends with a short **Further reading** section
  listing only the guides relevant to its issues;
- JSON outputs carry the reference ids (`Recommendation.references`, the action-plan rows'
  `references`, and each RCx issue's `references`).

**Link policy.** CAMBER links to these documents and never copies them: no text, figures or PDFs
from them are in the repository or in a report. Where a guide backs a rule, the mapping was
checked against the guide's own section headings (listed below, heading only). A rule that no
guide clearly covers is mapped to the relevant training chapter, or left unmapped; nothing is
forced. Every URL is checked weekly by `scripts/datasets_linkcheck.py`, and `verified_on` records
when each one last answered.

## Guides to the re-tuning measures

| Guide | Number | Rules | Checked against (headings) | Verified |
|---|---|---|---|---|
| [Air-Side Economizer Operation](https://www.pnnl.gov/sites/default/files/media/file/pnnl_sa_86706.pdf) | PNNL-SA-86706 | `outdoor_air_fraction`, `economizer_high_limit`, `free_cooling_missed` | Is the outdoor-air damper open when outdoor conditions are not favorable (outdoor-air temperature > return-air temperature)?; Does the cooling coil operate during economizer mode? | 2026-09-29 |
| [AHU Static Pressure Control](https://www.pnnl.gov/sites/default/files/media/file/pnnl_sa_84187.pdf) | PNNL-SA-84187 | `static_pressure_reset`, `damper_census`, `static_rogue_zone_census`, `static_cohort_starvation` | Is there a reset-schedule for the duct static pressure?; Determine whether the static pressure set point is too high or too low | 2026-09-29 |
| [AHU Discharge-Air Temperature Control](https://www.pnnl.gov/sites/default/files/media/file/pnnl_sa_84186.pdf) | PNNL-SA-84186 | `supply_air_reset`, `supply_air_reset_compliance`, `supply_air_control`, `sat_rogue_zone_census` | Is reset being used to control the discharge-air set point?; Is the discharge-air temperature meeting set point, or do deviations occur? | 2026-09-29 |
| [Occupancy Scheduling: Night and Weekend Temperature Set back and Supply Fan Cycling during Unoccupied Hours](https://www.pnnl.gov/sites/default/files/media/file/pnnl_sa_85194.pdf) | PNNL-SA-85194 | `night_weekend_setback` | Is there night set back for unoccupied hours? | 2026-09-29 |
| [Zone Heating and Cooling Control](https://www.pnnl.gov/sites/default/files/media/file/pnnl_sa_85200.pdf) | PNNL-SA-85200 | `reheat_penalty`, `reheat_minimization_g36` | Is there significant reheat occurring at the interior zones? | 2026-09-29 |
| [Central Utility Plant Cooling Control](https://www.pnnl.gov/sites/default/files/media/file/pnnl_sa_89198.pdf) | PNNL-SA-89198 | `chw_plant_reset`, `chw_pump_dp_reset` | Is reset utilized on the chilled water supply temperature?; Is the loop delta-T (ChWRT-ChWST) low?; Is the loop differential pressure set point constant and if so, can it be reset at partial load conditions? | 2026-09-29 |
| [Central Utility Plant Heating Control](https://www.pnnl.gov/sites/default/files/media/file/pnnl_sa_89222.pdf) | PNNL-SA-89222 | `hw_plant_deltat`, `hw_pump_dp_reset`, `hw_pump_summer_lockout` | Is the loop delta-T (HWST-HWRT) low?; Is the hot water loop differential pressure constant and if so, can it be reset at partial load conditions? | 2026-09-29 |
| [AHU Minimum Outdoor-Air Operation](https://www.pnnl.gov/sites/default/files/media/file/pnnl_sa_88958.pdf) | PNNL-SA-88958 | `outdoor_air_fraction`, `dcv_verification`, `dcv_system_verification`, `co2_ventilation`, `co2_ventilation_system`, `ventilation_rate_62_1`, `ventilation_system_62_1` | Is outdoor air sufficient for ventilation or is over-ventilation occurring? | 2026-09-29 |
| [AHU Heating and Cooling Control](https://www.pnnl.gov/sites/default/files/media/file/pnnl_sa_88359.pdf) | PNNL-SA-88359 | `simultaneous_heat_cool`, `leaking_valve` | Is there simultaneous heating and cooling occurring? | 2026-09-29 |
| [Trending Requirements for Re-Tuning](https://www.pnnl.gov/sites/default/files/media/file/trending_requirements_retuning.pdf) | — | — | — | 2026-09-29 |

The `pnnl-trending-requirements` guide lists the points to trend before re-tuning; it backs no single rule, and pairs with chapter 4 and CAMBER's [data readiness](SENSOR-HEALTH.md) checks.

## Training chapters (Large Commercial Buildings: Re-tuning for Efficiency)

| Resource | Number | Rules | Verified |
|---|---|---|---|
| [Large Commercial Buildings: Re-tuning for Efficiency, chapter 1: Introduction](https://www.pnnl.gov/sites/default/files/media/file/ch1_introduction.pdf) | PNNL-SA-85063 | — | 2026-09-29 |
| [Large Commercial Buildings: Re-tuning for Efficiency, chapter 2: Building Personality](https://www.pnnl.gov/sites/default/files/media/file/ch2_building_personality.pdf) | PNNL-SA-85063 | — | 2026-09-29 |
| [Large Commercial Buildings: Re-tuning for Efficiency, chapter 3: Collect Initial Building Information](https://www.pnnl.gov/sites/default/files/media/file/ch3_collect_initial.pdf) | PNNL-SA-85063 | — | 2026-09-29 |
| [Large Commercial Buildings: Re-tuning for Efficiency, chapter 4: Pre-Re-Tuning Phase: Trend Data Collection and Analysis](https://www.pnnl.gov/sites/default/files/media/file/ch4_pre-re-tuning.pdf) | PNNL-SA-85063 | — | 2026-09-29 |
| [Large Commercial Buildings: Re-tuning for Efficiency, chapter 5: Air Handling Units: Pre-Re-Tuning and Trending and Re-Tuning](https://www.pnnl.gov/sites/default/files/media/file/ch5_air_handling.pdf) | PNNL-SA-85063 | `dcv_verification`, `dcv_system_verification`, `co2_ventilation`, `co2_ventilation_system`, `static_pressure_reset`, `damper_census`, `supply_air_reset`, `supply_air_reset_compliance`, `supply_air_control`, `simultaneous_heat_cool`, `leaking_valve`, `control_hunting`, `night_weekend_setback`, `sat_cohort_starvation`, `static_cohort_starvation` | 2026-09-29 |
| [Large Commercial Buildings: Re-tuning for Efficiency, chapter 6: Economizer Operations: Pre-Re-Tuning and Re-Tuning](https://www.pnnl.gov/sites/default/files/media/file/ch6_economizer.pdf) | PNNL-SA-85063 | `outdoor_air_fraction`, `economizer_high_limit`, `free_cooling_missed` | 2026-09-29 |
| [Large Commercial Buildings: Re-tuning for Efficiency, chapter 7: Terminal Units in Air Distribution System: Pre-Re-Tuning and Re-Tuning](https://www.pnnl.gov/sites/default/files/media/file/ch7_terminal_units.pdf) | PNNL-SA-85063 | `damper_census`, `reheat_penalty`, `reheat_minimization_g36`, `overcooling_min_flow`, `overcooling_severity`, `unmet_setpoint_hours`, `airflow_tracking`, `zones_heat_cool_census`, `reheat_capacity_shortfall`, `actuator_stuck`, `cohort_airflow`, `cohort_space_temp`, `sat_rogue_zone_census`, `static_rogue_zone_census`, `sat_cohort_starvation`, `static_cohort_starvation` | 2026-09-29 |
| [Large Commercial Buildings: Re-tuning for Efficiency, chapter 8: Central Utility Plant: Pre-Re-Tuning and Re-Tuning](https://www.pnnl.gov/sites/default/files/media/file/ch8_central_plant.pdf) | PNNL-SA-85063 | `chw_plant_reset`, `chw_pump_dp_reset`, `chw_supply_tracking`, `chiller_efficiency`, `chiller_staging`, `condenser_water_reset`, `cooling_tower_approach`, `condenser_bypass_leak`, `chiller_approach_fouling`, `chiller_staging_fleet`, `cooling_tower_fan_effort_drift`, `hw_plant_deltat`, `hw_pump_dp_reset`, `boiler_short_cycle`, `boiler_summer_lockout`, `hw_pump_summer_lockout`, `boiler_efficiency_drift` | 2026-09-29 |
| [Large Commercial Buildings: Re-tuning for Efficiency, chapter 9: Building Walk Down](https://www.pnnl.gov/sites/default/files/media/file/ch9_building_walkdown.pdf) | PNNL-SA-85063 | `filter_fouling` | 2026-09-29 |
| [Large Commercial Buildings: Re-tuning for Efficiency, chapter 10: Re-Tuning Building Controls and Systems](https://www.pnnl.gov/sites/default/files/media/file/ch10_retuning_building.pdf) | PNNL-SA-85063 | — | 2026-09-29 |

Chapters 1–4 and 9–10 cover the re-tuning process (building personality, initial information, trend collection, the walk-down, re-tuning the building) rather than one fault, so no rule maps to them, with one exception: chapter 9's air-handler walk-down lists "missing, dirty, plugged or collapsed filters" among the things to inspect, so `filter_fouling` links to it (0.106). The chapter gives no pressure threshold; the rule's 1.0 in. w.c. change-out default is CAMBER's own. Chapter 9 (Building Walk Down) is also linked from the RCx report's **Verify on site** section (0.98): every walk-down item carries it (`camber.references.WALKDOWN_REFERENCES`), and the report's Further reading list adds it when that section is present.

## Small/Medium-Sized Commercial Building Re-tuning

PNNL's second course covers small buildings that have packaged rooftop units and thermostats and
often no BAS. Its measures chapter is the closest match for a packaged unit's compressor.

| Resource | Number | Rules | Checked against (headings) | Verified |
|---|---|---|---|---|
| [Small/Medium-Sized Commercial Building Re-tuning Training, chapter 3: Identifying and Implementing Re-tuning Measures](https://www.pnnl.gov/sites/default/files/media/file/ch3_sm_identifying_and_implementing_retuning_measures.pdf) | PNNL-SA-92685 | `compressor_short_cycle`, `compressor_staging` | Building HVAC Implementation: Different HVAC System General Guidance Examples (packaged units); Building HVAC Implementation: Thermostats | 2026-10-10 |

`compressor_short_cycle` maps here since 0.103 (#125). The chapter has no heading on compressor
starts. Its packaged-unit and thermostat sections cover the settings that make a unit cycle: the
thermostat's set points, its location and its fan mode. The large-building course's chapter 8
covers chillers, but not their compressor starts. `compressor_staging` maps here since 0.105
(#147) for the same reason: the thermostat and its stage settings decide how often a stage
changes.

## Heat pumps

No PNNL re-tuning guide or chapter covers heat pumps, so the heat-pump rules (0.105, #147) link
to public guides from a government program, an efficiency nonprofit and a research institute.
Each mapping was checked against the headings listed. None of them gives a threshold the rules
use: the reversals-per-day, wrong-mode and room-imbalance limits stay CAMBER's screening
judgment (`camber rules params <rule>`), and so does `source_loop_deltat`'s 10 °F design
delta-T.

| Resource | Publisher | Rules | Checked against (headings) | Verified |
|---|---|---|---|---|
| [Heating and cooling with a heat pump](https://natural-resources.canada.ca/energy-efficiency/energy-star/heating-cooling-heat-pump) | Natural Resources Canada | `heatpump_defrost`, `hp_capacity_shortfall`, `hp_mode_vs_need` | Air-source heat pumps: The defrost cycle; Supplementary heat sources; Related equipment: Supplementary heating systems; Thermostats | 2026-10-13 |
| [Guide to Installing Air-Source Heat Pumps in Cold Climates](https://neep.org/sites/default/files/Installing%20Air-Source%20Heat%20Pumps%20in%20Cold%20Climates.pdf) | Northeast Energy Efficiency Partnerships (NEEP), 2018 | `hp_mode_vs_need`, `hp_room_imbalance` | System Setup: Thermostat - Installer Setup | 2026-10-13 |
| [Variable Flow Pumping: The Key to Energy Efficiency in Water Loop and Geothermal Heat Pump System Applications](https://restservice.epri.com/publicdownload/TB-114673/0/Product) | Electric Power Research Institute (EPRI), TB-114673, 1999 | `source_loop_deltat` | Making Water Source Heat Pumps Efficient: Improved Loop Pump Control; Variable Flow System Design and Control Details | 2026-10-13 |

The NRCan guide describes the defrost cycle (the reversing valve switches to cooling and the
outdoor fan stops), demand defrost against a time-temperature control on a 30-, 60- or 90-minute
interval, the balance point below which supplementary heat is needed, and moderate setbacks for
heat pumps. The NEEP guide is written for residential installers; its thermostat section (a
control representative of the space, on an interior wall away from sun, appliances and drafts)
is the point `hp_room_imbalance` and `hp_mode_vs_need` check. EPRI's brief explains why loop
pumps at constant speed, through heat pumps without shutoff valves, move water the loop does not
need.

## Other resources

| Resource | Kind | Number | Verified |
|---|---|---|---|
| [Interval Data Analysis with the Energy Charting and Metrics Tool (ECAM)](https://www.pnnl.gov/sites/default/files/media/file/pnnl_20495.pdf) | tool guide | PNNL-20495 | 2026-09-29 |
| [Energy Savings Modeling of Standard Commercial Building Retuning Measures: Large Office Buildings](https://www.pnnl.gov/sites/default/files/media/file/pnnl_21569.pdf) | report | PNNL-21569 | 2026-09-29 |
| [Building Re-tuning (project page)](https://www.pnnl.gov/projects/building-re-tuning) | project page | — | 2026-09-29 |
| [Building Re-tuning: downloads](https://www.pnnl.gov/projects/building-re-tuning/downloads) | project page | — | 2026-09-29 |
| [ECAM (Energy Charting and Metrics), an Excel-based M&V and trend-charting tool](https://latticeenergyworks.com/services/technology-market-assessment/) | related tool | — | 2026-09-29 |

ECAM, the Energy Charting and Metrics tool the PNNL interval-data guide uses, is a related tool
listed in the [ecosystem](ECOSYSTEM.md); CAMBER neither ships nor wraps it.

## Measurement and verification

The standards, protocols and papers behind the names of CAMBER's M&V models and methods (0.105,
#163). The [names registry](MANDV.md#method-correspondence) (`camber.mandv.names`) cites them for
each model and method; `Reference.citation()` gives each in full, with its venue and DOI. Several
are paywalled: the link is then a library or publisher record, never a copy. The same link policy
applies.

| Id | Title | Authors / publisher | Year | Number | Verified |
|---|---|---|---|---|---|
| `ashrae-g14-2014` | [ASHRAE Guideline 14-2014, Measurement of Energy, Demand, and Water Savings](https://www.ashrae.org/technical-resources/standards-and-guidelines/titles-purposes-and-scopes) | ASHRAE | 2014 | Guideline 14-2014 | 2026-10-12 |
| `ashrae-rp1050-imt` | [Inverse Modeling Toolkit: Numerical Algorithms (ASHRAE RP-1050)](https://iifiir.org/en/fridoc/inverse-modelling-toolkit-imt-numerical-algorithms-22451) | Kissock, J.K., Haberl, J.S., Claridge, D.E. | 2003 | KC-03-02-1 | 2026-10-12 |
| `fels-1986-prism` | [PRISM: An introduction](https://doi.org/10.1016/0378-7788(86)90003-4) | Fels, M.F. | 1986 | — | 2026-10-12 |
| `mathieu-2011-towt` | [Quantifying Changes in Building Electricity Use, With Application to Demand Response](https://www.osti.gov/biblio/1048308) | Mathieu, J.L., Price, P.N., Kiliccote, S., Piette, M.A. | 2011 | LBNL-4944E | 2026-10-12 |
| `granderson-2015-baselines` | [Automated measurement and verification: Performance of public domain whole-building electric baseline models](https://www.osti.gov/pages/biblio/1247594) | Granderson, J., Price, P.N., Jump, D., Addy, N., Sohn, M.D. | 2015 | LBNL-187596 | 2026-10-12 |
| `lbnl-187225` | [Assessment of Automated Measurement and Verification (M&V) Methods](https://www.osti.gov/biblio/1236174) | Granderson, J., Touzani, S., Custodio, C., Sohn, M.D., Fernandes, S., Jump, D. | 2015 | LBNL-187225 | 2026-10-12 |
| `lbnl-6963e` | [Performance Metrics and Objective Testing Methods for Energy Baseline Modeling Software](https://www.aceee.org/wp-content/uploads/proceedings-1980-2020/2014/11-734.pdf) | Granderson, J., Jump, D., Price, P.N., Sohn, M.D. | 2014 | LBNL-6963E | 2026-10-12 |
| `caltrack-2` | [CalTRACK Technical Documentation: Methods (CalTRACK 2.0)](https://docs.caltrack.org/en/latest/methods.html) | CalTRACK (LF Energy OpenEEmeter) | 2018 | — | 2026-10-12 |
| `evo-ipmvp-core-2022` | [International Performance Measurement and Verification Protocol (IPMVP) Core Concepts 2022](https://evo-world.org/en/products-services-mainmenu-en/protocols/ipmvp) | Efficiency Valuation Organization (EVO) | 2022 | EVO 10000-1:2022 | 2026-10-12 |
| `doe-sep-mv-2019` | [Superior Energy Performance 50001 Program Measurement & Verification Protocol: 2019 (Edition 2)](https://betterbuildingssolutioncenter.energy.gov/iso-50001/sep-50001/resources/sep-50001-measurement-and-verification-protocol) | U.S. Department of Energy | 2023 | — | 2026-10-12 |

Two links need care: the SEP 50001 M&V Protocol's host (`betterbuildingssolutioncenter.energy.gov`)
serves an incomplete certificate chain, so the weekly link check may report it as an error while a
browser opens it; and ASHRAE has no stable public page for Guideline 14-2014 (superseded by
Guideline 14-2023), so the link is ASHRAE's titles-and-scopes list.

## Rules without a reference

These rules have no Re-tuning guide or chapter that clearly covers them, so they carry no link:

- the G36 reset-effectiveness rules (`sat_reset_effectiveness`, `static_reset_effectiveness`),
  which check a trim-and-respond sequence the guides predate;
- the G36 fault conditions (`g36_afdd`), which cite ASHRAE Guideline 36 itself;
- the DX rules other than `compressor_short_cycle` and `compressor_staging` (the course covers
  built-up air handlers and central plants; the heat-pump rules link to the
  [heat-pump guides](#heat-pumps));
- the drift detectors, except `cooling_tower_fan_effort_drift` and `boiler_efficiency_drift`,
  which map to chapter 8.

The zone-request census rules map to the guide whose heading covers them: the discharge-air
guide's reset section bases a zone-driven reset on the zones the unit serves, setting aside the
warmest and coolest, and the static-pressure guide's "too high or too low" section reads the box
damper positions, sets the static by the most demanding boxes (leaving out failed or outlier
ones) and reads boxes held wide open as starved.

## Using the registry

```python
from camber.references import references_for, reference

for ref in references_for("dcv_verification"):
    print(ref.id, ref.url)
reference("pnnl-guide-min-oa").label()
```
