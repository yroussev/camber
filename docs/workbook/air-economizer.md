# Economizer: a stuck outdoor-air damper and missed free cooling

*Workbook exercise `air-economizer` · PNNL re-tuning chapter 6 · about 45 minutes*

## Goal

Find the air handlers whose outdoor-air damper is stuck, tell a damper stuck **open** from one
stuck **closed**, and see what each costs: too much outdoor air in hot weather, or free cooling
lost in cool weather. Then score CAMBER against the dataset's published fault labels.

## Learn more

Read these first (PNNL, free):

- [Air-Side Economizer Operation][pnnl-guide-economizer], the re-tuning guide: its two
  questions, whether the outdoor-air damper is open when outdoor conditions are not favorable
  and whether the cooling coil runs during economizer mode, are the two halves of this exercise.
- [Chapter 6: Economizer Operations][pnnl-retuning-ch6] of the re-tuning training.
- [AHU Minimum Outdoor-Air Operation][pnnl-guide-min-oa], for why the unit's own design minimum
  matters.

[pnnl-guide-economizer]: https://www.pnnl.gov/sites/default/files/media/file/pnnl_sa_86706.pdf "Building Re-Tuning Training Guide: Air-Side Economizer Operation (PNNL-SA-86706)"
[pnnl-retuning-ch6]: https://www.pnnl.gov/sites/default/files/media/file/ch6_economizer.pdf "Large Commercial Buildings: Re-tuning for Efficiency, chapter 6: Economizer Operations: Pre-Re-Tuning and Re-Tuning (PNNL-SA-85063)"
[pnnl-guide-min-oa]: https://www.pnnl.gov/sites/default/files/media/file/pnnl_sa_88958.pdf "Building Re-Tuning Training Guide: AHU Minimum Outdoor-Air Operation (PNNL-SA-88958)"

## Datasets and licence

- **`lbnl-sdahu`**: LBNL's simulated single-duct VAV air handler (cooling coil, dry-bulb
  economizer), a year at one-minute resolution, as a fault-free run plus labelled faulted runs.
  Licence **CC-BY-4.0** (open; cite it). Its default subset is about 600 MB to download.
  The catalog lists the problems found in the published data and how CAMBER handles each:
  see [its data issues](../DATASETS.md#lbnl-sdahu-lbnl-simulated-single-duct-ahu-labelled-faults).

Each run becomes one piece of equipment, `AHU__<run>`, in the facility `ds-lbnl-sdahu`:
`AHU__fault_free`, the damper stuck at 10, 25, 75 and 100 % (`AHU__damper_stuck_010` ...
`AHU__damper_stuck_100_short`), and a cooling-coil valve leak (`AHU__coi_leakage_010`).

## Setup

### In the lab

1. Run `camber lab` and open `http://127.0.0.1:8765/lab`.
2. Tick `lbnl-sdahu` and press **Fetch & ingest**.
3. When the job is done, the row links to **trends** (the trend viewer) and **report** (the
   dataset's default config). This exercise uses its own config: use the command line for step 2
   of the steps below, and the lab's trend viewer to look at the data.

### On the command line

```
camber datasets fetch lbnl-sdahu
camber datasets ingest lbnl-sdahu --store lab_store
camber datasets config lbnl-sdahu --exercise air-economizer --store lab_store --out econ.json
camber run econ.json --out econ_out
camber datasets score lbnl-sdahu --store lab_store --findings econ_out/findings.json
```

The exercise's config (`--exercise air-economizer`) runs three rules: `outdoor_air_fraction`,
`economizer_high_limit` and `free_cooling_missed`, with this unit's own design minimum (a 1.6 %
outdoor-air fraction) and its fixed 60 °F dry-bulb high limit. `camber report econ.json --out
econ.html` gives the same findings as a report with evidence charts.

## Steps

1. **Look before you judge.** In the trend viewer, open `AHU__fault_free` and
   `AHU__damper_stuck_075`. Compare the outdoor-air damper command with the mixed-air
   temperature on a hot afternoon and on a cold morning.
2. **Run the exercise's config** (the commands above) and read the findings `camber run` prints.
3. **Stuck open.** For each run, find the `outdoor_air_fraction` finding: the median
   outdoor-air fraction in cooling weather, and the share of cooling hours above the minimum.
   Then find `economizer_high_limit`: is the unit locked out to minimum above 60 °F?
4. **Stuck closed.** Find `free_cooling_missed` for each run: the share of free-cooling hours
   (outdoor air below 60 °F) in which the cooling coil ran anyway.
5. **Score.** Run `camber datasets score` on the findings and read the per-detector rates.

## Questions

1. Which runs does CAMBER flag for too much outdoor air, and how much outdoor air do they bring
   in during cooling weather?
2. What outdoor-air fraction does the fault-free unit run at in cooling weather? Its annual
   median is much higher. Why is that not a fault?
3. Which stuck dampers do the outdoor-air-fraction rules miss, and why?
4. Where do those dampers show up instead? Compare them with the fault-free unit.
5. What true- and false-positive rates does the label score give `outdoor_air_fraction`, and
   what does the overall score count as missed?

## What CAMBER shows

- **Findings.** `camber run` prints one line per finding with its severity (`fault`, `warn`,
  `ok`); `econ_out/findings.json` holds every metric, e.g. `median_oaf_cooling` and
  `excess_oa_pct` for `outdoor_air_fraction`, `not_locked_out_pct` for `economizer_high_limit`,
  `missed_pct` for `free_cooling_missed`.
- **Evidence.** In the report, the `outdoor_air_fraction` finding carries a chart of the
  outdoor-air fraction against the outdoor temperature, drawn from the samples the rule judged,
  with the band it judges them against.
- **Score.** `camber datasets score` prints the true- and false-positive rates, with 95 %
  intervals, for the dataset's declared detectors, and which rules fired on each labelled run.

## Caveats

- The data are simulated: one unit, one climate, one control sequence. Real dampers stick part
  way, drift, or stick only in some weather.
- The outdoor-air fraction here comes from a temperature balance (mixed, return and outdoor
  air); it is unreliable when the outdoor and return temperatures are close, and CAMBER leaves
  those samples out.
- The 100 % damper run is shorter than the others (`_short`), so it has fewer free-cooling
  hours.
- The simulated schedule runs one weekday late; see the dataset's data issues.

## Going further

- Re-run with the dataset's default config (`camber datasets config lbnl-sdahu --store
  lab_store --out sdahu.json`) and compare what it reports.
- Try a generic 20 % design minimum in `econ.json` (`min_oa_pct`) and see which verdicts change.
- The spliced run `AHU__onset_damper_stuck_025` switches from fault-free to a stuck damper part
  way through the year: can you see when, in the trend viewer?
