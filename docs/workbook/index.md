# Re-tuning workbook

A hands-on course in building re-tuning, run on open data with CAMBER. It follows the free
**Building Re-tuning** training from the Pacific Northwest National Laboratory (PNNL): read a
chapter or a guide there, then open a real or simulated building in [`camber lab`](../DATASETS.md#the-lab-camber-lab)
and find the problems it teaches you to look for.

> **Status (0.97, provisional).** This is the workbook's framework and its first worked example,
> [Economizer: a stuck outdoor-air damper and missed free cooling](air-economizer.md). The other
> exercises in the map below are planned for the same release.

## How it works

- **Read PNNL, then practise here.** Each exercise names the PNNL chapter or guide to read
  first, in its *Learn more* section. The workbook links to PNNL's documents and never copies
  them; the links come from CAMBER's [reference registry](../REFERENCES.md).
- **Open data only.** Core exercises use open-licence datasets from the
  [catalog](../DATASETS.md). A research-only dataset appears only as a marked optional extra.
- **Two ways in.** Every exercise gives the steps both in the lab (a local web page:
  `camber lab`, then **Fetch & ingest**, **trends**, **report**) and on the command line.
- **Tuned configs.** Where the dataset's default config doesn't fit an exercise, the exercise
  ships its own: `camber datasets config <dataset> --exercise <exercise-id> --store lab_store
  --out cfg.json`.
- **Answers are checked.** The instructor's key is on a separate
  [instructor page](instructor.md). Every answer on it is pinned by CAMBER's test suite, on a
  small synthetic stand-in for each dataset and, optionally, on the real data, so the key cannot
  silently drift from what CAMBER shows.

**Getting started.** Install CAMBER, then:

```
camber lab
```

Open `http://127.0.0.1:8765/lab`, tick a dataset and press **Fetch & ingest**. A dataset used by
an exercise has an **exercise** link in its row. From a source checkout, the lab serves the page
itself, so it opens offline; otherwise the link goes to this site.

## Curriculum map

The tables map each PNNL chapter or guide to its exercises and datasets. Exercise ids are stable:
they name the page (`<id>.md`) and the tuned config (`--exercise <id>`).

[pnnl-retuning-ch3]: https://www.pnnl.gov/sites/default/files/media/file/ch3_collect_initial.pdf "Large Commercial Buildings: Re-tuning for Efficiency, chapter 3: Collect Initial Building Information (PNNL-SA-85063)"
[pnnl-retuning-ch4]: https://www.pnnl.gov/sites/default/files/media/file/ch4_pre-re-tuning.pdf "Large Commercial Buildings: Re-tuning for Efficiency, chapter 4: Pre-Re-Tuning Phase: Trend Data Collection and Analysis (PNNL-SA-85063)"
[pnnl-retuning-ch5]: https://www.pnnl.gov/sites/default/files/media/file/ch5_air_handling.pdf "Large Commercial Buildings: Re-tuning for Efficiency, chapter 5: Air Handling Units: Pre-Re-Tuning and Trending and Re-Tuning (PNNL-SA-85063)"
[pnnl-retuning-ch6]: https://www.pnnl.gov/sites/default/files/media/file/ch6_economizer.pdf "Large Commercial Buildings: Re-tuning for Efficiency, chapter 6: Economizer Operations: Pre-Re-Tuning and Re-Tuning (PNNL-SA-85063)"
[pnnl-retuning-ch7]: https://www.pnnl.gov/sites/default/files/media/file/ch7_terminal_units.pdf "Large Commercial Buildings: Re-tuning for Efficiency, chapter 7: Terminal Units in Air Distribution System: Pre-Re-Tuning and Re-Tuning (PNNL-SA-85063)"
[pnnl-retuning-ch8]: https://www.pnnl.gov/sites/default/files/media/file/ch8_central_plant.pdf "Large Commercial Buildings: Re-tuning for Efficiency, chapter 8: Central Utility Plant: Pre-Re-Tuning and Re-Tuning (PNNL-SA-85063)"
[pnnl-retuning-ch9]: https://www.pnnl.gov/sites/default/files/media/file/ch9_building_walkdown.pdf "Large Commercial Buildings: Re-tuning for Efficiency, chapter 9: Building Walk Down (PNNL-SA-85063)"
[pnnl-retuning-ch10]: https://www.pnnl.gov/sites/default/files/media/file/ch10_retuning_building.pdf "Large Commercial Buildings: Re-tuning for Efficiency, chapter 10: Re-Tuning Building Controls and Systems (PNNL-SA-85063)"
[pnnl-guide-discharge-air-temp]: https://www.pnnl.gov/sites/default/files/media/file/pnnl_sa_84186.pdf "Building Re-Tuning Training Guide: AHU Discharge-Air Temperature Control (PNNL-SA-84186)"
[pnnl-guide-static-pressure]: https://www.pnnl.gov/sites/default/files/media/file/pnnl_sa_84187.pdf "Building Re-Tuning Training Guide: AHU Static Pressure Control (PNNL-SA-84187)"
[pnnl-guide-ahu-heat-cool]: https://www.pnnl.gov/sites/default/files/media/file/pnnl_sa_88359.pdf "Building Re-Tuning Training Guide: AHU Heating and Cooling Control (PNNL-SA-88359)"
[pnnl-guide-economizer]: https://www.pnnl.gov/sites/default/files/media/file/pnnl_sa_86706.pdf "Building Re-Tuning Training Guide: Air-Side Economizer Operation (PNNL-SA-86706)"
[pnnl-guide-occupancy-scheduling]: https://www.pnnl.gov/sites/default/files/media/file/pnnl_sa_85194.pdf "Building Re-Tuning Training Guide: Occupancy Scheduling: Night and Weekend Temperature Set back and Supply Fan Cycling during Unoccupied Hours (PNNL-SA-85194)"
[pnnl-guide-zone-heat-cool]: https://www.pnnl.gov/sites/default/files/media/file/pnnl_sa_85200.pdf "Building Re-Tuning Training Guide: Zone Heating and Cooling Control (PNNL-SA-85200)"
[pnnl-guide-min-oa]: https://www.pnnl.gov/sites/default/files/media/file/pnnl_sa_88958.pdf "Building Re-Tuning Training Guide: AHU Minimum Outdoor-Air Operation (PNNL-SA-88958)"
[pnnl-guide-plant-cooling]: https://www.pnnl.gov/sites/default/files/media/file/pnnl_sa_89198.pdf "Building Re-Tuning Training Guide: Central Utility Plant Cooling Control (PNNL-SA-89198)"
[pnnl-guide-plant-heating]: https://www.pnnl.gov/sites/default/files/media/file/pnnl_sa_89222.pdf "Building Re-Tuning Training Guide: Central Utility Plant Heating Control (PNNL-SA-89222)"
[pnnl-trending-requirements]: https://www.pnnl.gov/sites/default/files/media/file/trending_requirements_retuning.pdf "Re-Tuning Training Guide: Trending Requirements for Re-Tuning"

<!-- BEGIN workbook-air (#80) -->
### Air side

| PNNL reading | Exercise | Datasets |
|---|---|---|
| [Ch. 5][pnnl-retuning-ch5], [AHU Discharge-Air Temperature Control][pnnl-guide-discharge-air-temp] | `air-sat-reset`: supply-air temperature reset (planned) | `lbnl-sdahu`, `lbnl-ddahu` |
| [Ch. 5][pnnl-retuning-ch5], [AHU Static Pressure Control][pnnl-guide-static-pressure] | `air-static-pressure`: static pressure reset and the damper census (planned) | `lbnl-sdahu`, `lbnl-ddahu` |
| [Ch. 5][pnnl-retuning-ch5], [AHU Heating and Cooling Control][pnnl-guide-ahu-heat-cool] | `air-heat-cool`: simultaneous heating and cooling, a leaking valve (planned) | `lbnl-sdahu` |
| [Ch. 6][pnnl-retuning-ch6], [Air-Side Economizer Operation][pnnl-guide-economizer] | [`air-economizer`: a stuck outdoor-air damper and missed free cooling](air-economizer.md) | `lbnl-sdahu`; `irish-ahu` (planned) |
| [Ch. 5][pnnl-retuning-ch5], [Occupancy Scheduling][pnnl-guide-occupancy-scheduling] | `air-scheduling`: 24/7 operation, setback and after-hours load (planned) | `ornl-frp-ops`, `bdg2` |
<!-- END workbook-air (#80) -->

<!-- BEGIN workbook-zone (#81) -->
### Terminal units and ventilation

| PNNL reading | Exercise | Datasets |
|---|---|---|
| [Ch. 7][pnnl-retuning-ch7], [Zone Heating and Cooling Control][pnnl-guide-zone-heat-cool] | `zone-reheat-overcooling`: reheat penalty and overcooling (planned) | `lbnl-fpu` |
| [Ch. 7][pnnl-retuning-ch7] | `zone-bad-box`: one bad box in a fleet (planned) | `ornl-frp-vav` |
| [Ch. 7][pnnl-retuning-ch7], [Zone Heating and Cooling Control][pnnl-guide-zone-heat-cool] | `zone-reheat-saturated`: a zone below setpoint with its reheat maxed out (planned) | `lbnl-fpu`, `ornl-frp-vav` |
| [Ch. 5][pnnl-retuning-ch5], [AHU Minimum Outdoor-Air Operation][pnnl-guide-min-oa] | `zone-dcv`: is outdoor air following occupancy? (planned) | `finnish-dcv`, `b4b-windesheim`, `sdu-ou44` |
| [AHU Minimum Outdoor-Air Operation][pnnl-guide-min-oa] | `zone-min-oa`: minimum outdoor air and 62.1 system ventilation (planned) | `lbnl-b59` |
<!-- END workbook-zone (#81) -->

<!-- BEGIN workbook-plant (#82) -->
### Central plant

| PNNL reading | Exercise | Datasets |
|---|---|---|
| [Ch. 8][pnnl-retuning-ch8], [Central Utility Plant Cooling Control][pnnl-guide-plant-cooling] | `plant-chiller-efficiency`: chiller efficiency and fouling (planned) | `lbnl-chiller` |
| [Ch. 8][pnnl-retuning-ch8] | `plant-cooling-tower`: approach and fan effort, tower fouling (planned) | `lbnl-chiller` |
| [Ch. 8][pnnl-retuning-ch8], [Central Utility Plant Cooling Control][pnnl-guide-plant-cooling] | `plant-chw-reset-pumping`: chilled-water reset and pumping (planned) | `lbnl-chiller` |
| [Ch. 8][pnnl-retuning-ch8], [Central Utility Plant Heating Control][pnnl-guide-plant-heating] | `plant-boiler`: hot-water reset, short cycling, summer lockout (planned) | `lbnl-boiler` |
| [Ch. 8][pnnl-retuning-ch8] | `plant-sensor-vs-equipment`: sensor faults vs equipment faults (planned) | `lbnl-chiller` |
<!-- END workbook-plant (#82) -->

<!-- BEGIN workbook-practice (#83) -->
### Data quality, energy charting, M&V and the capstone

| PNNL reading | Exercise | Datasets |
|---|---|---|
| [Ch. 3][pnnl-retuning-ch3], [Ch. 4][pnnl-retuning-ch4], [Trending Requirements][pnnl-trending-requirements] | [`data-trend-quality`: are the trends good enough?](data-trend-quality.md) | `lbnl-b59`, `irish-ahu`, `nuig-ahu101` |
| [Ch. 4][pnnl-retuning-ch4], [Occupancy Scheduling][pnnl-guide-occupancy-scheduling] | [`data-energy-charting`: load profiles and weather dependence](data-energy-charting.md) | `bdg2`, `valladolid-uva` |
| [Ch. 4][pnnl-retuning-ch4], [Ch. 10][pnnl-retuning-ch10] | [`mv-baselines`: change-point baselines and M&V](mv-baselines.md) | `valladolid-uva`, `cofactor-drammen`, `bdg2` (synthetic bills) |
| [Economizer][pnnl-guide-economizer], [Ch. 9][pnnl-retuning-ch9], [Ch. 10][pnnl-retuning-ch10] | [`capstone`: the RCx report, a walk-down list, a re-tuning plan, verification](capstone.md) | `lbnl-sdahu`, `ornl-frp-ops` |
<!-- END workbook-practice (#83) -->

## For instructors

The [instructor page](instructor.md) holds the answer keys, discussion points and common
mistakes. Keep it away from learners until they have tried the exercise.
