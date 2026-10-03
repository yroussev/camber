# References: PNNL Building Re-tuning

CAMBER's rules and advisory actions follow the free **Building Re-tuning** material that the
Pacific Northwest National Laboratory (PNNL) publishes for large commercial buildings. This page
lists every resource CAMBER links to, and which rules each one backs. The same list is the
registry `camber.references` (provisional, 0.96), which the reports use:

- the audit report's findings table and its **Recommended actions** table carry
  **Learn more** links to the guide behind each finding (plain text: `learn more: <ids>`);
- the [RCx report](RCX-REPORT.md) adds the links to each issue page and ends with a short
  **Further reading** section listing only the guides relevant to its issues;
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
| [AHU Static Pressure Control](https://www.pnnl.gov/sites/default/files/media/file/pnnl_sa_84187.pdf) | PNNL-SA-84187 | `static_pressure_reset` | Is there a reset-schedule for the duct static pressure? | 2026-09-29 |
| [AHU Discharge-Air Temperature Control](https://www.pnnl.gov/sites/default/files/media/file/pnnl_sa_84186.pdf) | PNNL-SA-84186 | `supply_air_reset`, `supply_air_reset_compliance`, `supply_air_control` | Is reset being used to control the discharge-air set point?; Is the discharge-air temperature meeting set point, or do deviations occur? | 2026-09-29 |
| [Occupancy Scheduling: Night and Weekend Temperature Set back and Supply Fan Cycling during Unoccupied Hours](https://www.pnnl.gov/sites/default/files/media/file/pnnl_sa_85194.pdf) | PNNL-SA-85194 | `night_weekend_setback` | Is there night set back for unoccupied hours? | 2026-09-29 |
| [Zone Heating and Cooling Control](https://www.pnnl.gov/sites/default/files/media/file/pnnl_sa_85200.pdf) | PNNL-SA-85200 | `reheat_penalty`, `reheat_minimization_g36` | Is there significant reheat occurring at the interior zones? | 2026-09-29 |
| [Central Utility Plant Cooling Control](https://www.pnnl.gov/sites/default/files/media/file/pnnl_sa_89198.pdf) | PNNL-SA-89198 | `chw_plant_reset`, `chw_pump_dp_reset` | Is reset utilized on the chilled water supply temperature?; Is the loop delta-T (ChWRT-ChWST) low?; Is the loop differential pressure set point constant and if so, can it be reset at partial load conditions? | 2026-09-29 |
| [Central Utility Plant Heating Control](https://www.pnnl.gov/sites/default/files/media/file/pnnl_sa_89222.pdf) | PNNL-SA-89222 | `hw_plant_deltat`, `hw_pump_dp_reset` | Is the loop delta-T (HWST-HWRT) low?; Is the hot water loop differential pressure constant and if so, can it be reset at partial load conditions? | 2026-09-29 |
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
| [Large Commercial Buildings: Re-tuning for Efficiency, chapter 5: Air Handling Units: Pre-Re-Tuning and Trending and Re-Tuning](https://www.pnnl.gov/sites/default/files/media/file/ch5_air_handling.pdf) | PNNL-SA-85063 | `dcv_verification`, `dcv_system_verification`, `co2_ventilation`, `co2_ventilation_system`, `static_pressure_reset`, `supply_air_reset`, `supply_air_reset_compliance`, `supply_air_control`, `simultaneous_heat_cool`, `leaking_valve`, `control_hunting`, `night_weekend_setback` | 2026-09-29 |
| [Large Commercial Buildings: Re-tuning for Efficiency, chapter 6: Economizer Operations: Pre-Re-Tuning and Re-Tuning](https://www.pnnl.gov/sites/default/files/media/file/ch6_economizer.pdf) | PNNL-SA-85063 | `outdoor_air_fraction`, `economizer_high_limit`, `free_cooling_missed` | 2026-09-29 |
| [Large Commercial Buildings: Re-tuning for Efficiency, chapter 7: Terminal Units in Air Distribution System: Pre-Re-Tuning and Re-Tuning](https://www.pnnl.gov/sites/default/files/media/file/ch7_terminal_units.pdf) | PNNL-SA-85063 | `reheat_penalty`, `reheat_minimization_g36`, `overcooling_min_flow`, `overcooling_severity`, `unmet_setpoint_hours`, `airflow_tracking`, `zones_heat_cool_census` | 2026-09-29 |
| [Large Commercial Buildings: Re-tuning for Efficiency, chapter 8: Central Utility Plant: Pre-Re-Tuning and Re-Tuning](https://www.pnnl.gov/sites/default/files/media/file/ch8_central_plant.pdf) | PNNL-SA-85063 | `chw_plant_reset`, `chw_pump_dp_reset`, `chw_supply_tracking`, `chiller_efficiency`, `chiller_staging`, `condenser_water_reset`, `cooling_tower_approach`, `hw_plant_deltat`, `hw_pump_dp_reset`, `boiler_short_cycle`, `boiler_summer_lockout` | 2026-09-29 |
| [Large Commercial Buildings: Re-tuning for Efficiency, chapter 9: Building Walk Down](https://www.pnnl.gov/sites/default/files/media/file/ch9_building_walkdown.pdf) | PNNL-SA-85063 | — | 2026-09-29 |
| [Large Commercial Buildings: Re-tuning for Efficiency, chapter 10: Re-Tuning Building Controls and Systems](https://www.pnnl.gov/sites/default/files/media/file/ch10_retuning_building.pdf) | PNNL-SA-85063 | — | 2026-09-29 |

Chapters 1–4 and 9–10 cover the re-tuning process (building personality, initial information, trend collection, the walk-down, re-tuning the building) rather than one fault, so no rule maps to them.

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

## Rules without a reference

Drift detectors, DX and heat-pump rules, the G36 fault conditions (`g36_afdd`, which cite
ASHRAE Guideline 36 itself) and the damper-census and cohort rules have no Re-tuning guide that clearly
covers them, so they carry no link.

## Using the registry

```python
from camber.references import references_for, reference

for ref in references_for("dcv_verification"):
    print(ref.id, ref.url)
reference("pnnl-guide-min-oa").label()
```
