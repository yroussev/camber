# Ventilation: minimum outdoor air and ASHRAE 62.1 system ventilation

*Workbook exercise `zone-min-oa` · PNNL re-tuning chapter 5 · about 50 minutes*

## Goal

How much outdoor air does an air handler need, and how much is it actually bringing in? Work
out a system-level ventilation requirement for the four rooftop units of a real office, compare
it with their measured outdoor airflow, and read the building's zone CO₂ as a second opinion.
Along the way, state every assumption you had to make. The building publishes no design
ventilation schedule, and the answer is only as good as your inputs.

## Learn more

Read these first (PNNL, free):

- [AHU Minimum Outdoor-Air Operation][pnnl-guide-min-oa], the re-tuning guide on minimum
  outdoor air: why the minimum setting deserves a check, and what too much outdoor air costs.
- [Chapter 5: Air Handling Units][pnnl-retuning-ch5] of the re-tuning training, for where the
  minimum outdoor air sits in the air-handler sequence.

[pnnl-guide-min-oa]: https://www.pnnl.gov/sites/default/files/media/file/pnnl_sa_88958.pdf "Building Re-Tuning Training Guide: AHU Minimum Outdoor-Air Operation (PNNL-SA-88958)"
[pnnl-retuning-ch5]: https://www.pnnl.gov/sites/default/files/media/file/ch5_air_handling.pdf "Large Commercial Buildings: Re-tuning for Efficiency, chapter 5: Air Handling Units: Pre-Re-Tuning and Trending and Re-Tuning (PNNL-SA-85063)"

**About ASHRAE 62.1.** The requirement comes from the Ventilation Rate Procedure of ASHRAE
Standard 62.1. CAMBER's [ventilation page](../VENTILATION.md#system-level-vrp-multiple-zone-systems)
describes how it computes the system requirement and where each piece comes from. It follows the
free 62.1-2016 Addendum f and public secondary sources, not a licensed copy of the current
edition. Neither that page nor this one quotes the standard. For a design or compliance
question, use the edition your jurisdiction adopts.

## Datasets and licence

- **`lbnl-b59`**: LBNL Building 59, a real two-floor office in California, 2018–2020 at
  one-minute resolution. It has four 20,000 cfm rooftop units with measured outdoor-air flow
  (from April 2020) and damper position, 11 zone CO₂ sensors, 51 underfloor terminals and a
  Brick model. The building has no DCV. Licence **CC-BY-4.0** (open; cite it). The default
  subset needs `Building_59.zip` (263 MB) and a README. See
  [its data issues](../DATASETS.md#lbnl-b59-lbnl-building-59-three-years-of-a-real-offices-rooftop-units-zone-co2-and-underfloor-terminals).

The exercise uses the four rooftop units (`RTU01`–`RTU04`, class AHU) and the 11 CO₂ zones
(`RTU0N_zone_<z>`, grouped under the unit the Brick model says serves them).

## Setup

### In the lab

1. Run `camber lab` and open `http://127.0.0.1:8765/lab`.
2. `lbnl-b59` is a **manual** download: Dryad serves its files only to a browser. Its row in the
   lab (and `camber datasets info lbnl-b59`) says which files to download and from where. Put
   them in one directory, `b59_download`.
3. Ingest from that directory with the command below, then use the row's **trends** link to
   look at the units. This exercise uses its own config: run it from the command line.

### On the command line

```
camber datasets info lbnl-b59
camber datasets ingest lbnl-b59 --from-dir b59_download --store lab_store
camber datasets config lbnl-b59 --exercise zone-min-oa --store lab_store --out b59.json
camber run b59.json --out b59_out
```

The exercise's config (`--exercise zone-min-oa`) runs three rules:

- `ventilation_system_62_1`, from its `ventilation` section: each unit's measured outdoor air
  against its system requirement, Vot.
- `dcv_system_verification`: does outdoor air follow the zones' CO₂?
- `co2_ventilation_system`: is the zones' CO₂ near outdoor, outside economizer hours?

**The inputs are assumptions, and the config says so.** The data publish no zone areas or
design populations. So each unit gets one lumped zone standing for a quarter of the two
2,325 m² floors: 12,513 ft² and 63 people, from the office default of 5 people per 1,000 ft².
Both inputs are marked `area_assumed` and `population_assumed`. With no system population
given, occupant diversity D is 1. Read the config's `_comment` before you read the results.

## Steps

1. **Look first.** In the trend viewer, open `RTU01` for a week in summer 2020 and plot the
   outdoor-air flow, the supply airflow and the outdoor-air damper. Then open a CO₂ zone and
   compare its CO₂ with 420 ppm.
2. **Work the requirement by hand.** For one unit's lumped zone, compute the breathing-zone
   outdoor air (people part plus area part, with the office rates from the ventilation page),
   then the system requirement with the simplified system efficiency for D = 1.
3. **Run the config** (the commands above) and read `ventilation_system_62_1` (equipment
   `<fleet>`). Its `per_system` block holds each unit's requirement, measured flow, ratio,
   status and caveats.
4. **The second opinion.** Read `co2_ventilation_system` and `dcv_system_verification`.
5. **Change an assumption.** In `b59.json`, give each unit a system population (`"systems":
   {"RTU01": {"ps": 32}, …}` in the `ventilation` section, half the zone population) and re-run.
   What changes, and what does not?

## Questions

1. What system requirement (Vot) does CAMBER compute for each unit, and does your hand
   calculation from step 2 agree?
2. How much outdoor air do the units actually bring in over occupied hours, and what verdict
   does CAMBER give? What is the ratio of measured to required?
3. Why is every verdict a **warn** and not a fault or an `ok`? Name the caveats the finding
   carries.
4. What do the zones' CO₂ readings say about the same question? Give the range of the zones'
   median CO₂.
5. What does `dcv_system_verification` conclude, and why is that the right answer for a
   building without DCV?
6. How much would the requirement have to be wrong for the verdict to flip to "adequate"? Which
   of your assumptions could plausibly be that wrong?
7. What would you ask for before recommending a lower minimum outdoor-air setting?

## What CAMBER shows

- **Findings.** `ventilation_system_62_1` gives one `<fleet>` finding; its `per_system` block has,
  per unit, the `requirement` (Vou, D, Ev, Vot for cooling and heating, the assumed inputs),
  `measured_cfm`, `ratio`, `status` (`under`, `adequate`, `over`, `uncertain`) and `caveats`.
  `co2_ventilation_system` has a `per_zone` block; `dcv_system_verification` a `per_ahu` block.
- **Severity.** Under-ventilated is a `fault`, over-ventilated a `warn`. Either is capped at
  `warn` when an input is assumed or the unit's zones come from a model rather than a declared
  schedule.
- **Report.** `camber report b59.json --out b59.html` shows the same findings.

## Caveats

- Everything rests on the assumed area and population. They are reasonable for an office, and
  they are not design data.
- The outdoor-air flow before 2020-04-10 is the publisher's gap fill, masked at ingest; the
  verdicts come from April–December 2020, which includes the 2020 shelter-in-place period and
  the wildfire smoke mode (dampers held at their 10 % minimum, 2020-08-24 to 09-06).
- The flow stations' accuracy is not published. They read above the unit's supply flow in a few
  percent of hours.
- The zone-to-unit grouping comes from the Brick model, which disagrees with the data
  descriptor's table for every CO₂ zone (a documented data issue).
- The office rates and default densities in CAMBER's table are a public-source convenience, not a
  substitute for a stamped ventilation calculation (see the ventilation page's *Scope*).

## Going further

- Try the `appendix` method (`"method": "appendix"` in a unit's `systems` entry) with a design
  supply airflow for the lumped zone. Why does it need airflow inputs the simplified method does
  not?
- The `full` subset adds the 51 underfloor terminals. [VENTILATION.md](../VENTILATION.md) shows
  the same check with the floor area split over them instead of over the four units. Compare its
  verdicts with yours.
- The [`zone-dcv`](zone-dcv.md) exercise shows what a *working* DCV looks like on open data.
