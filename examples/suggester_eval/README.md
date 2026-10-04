# Point-role suggestion: names versus data

Evaluates CAMBER's point-role suggester (`camber.mapping_assist`, with the 0.96 time-series
evidence of `camber.mapping_timeseries`) three ways. None of these is a gated benchmark.

| script | data | what it answers |
|---|---|---|
| `real_names.py` | open catalog datasets that publish their **real BMS point names** | how the suggester does on real naming, with and without the data |
| `bts.py` | **BTS**: three real buildings, anonymised ids, Brick classes | what the data alone recovers when the names are hidden |
| `messy_names.py` | BTS points with **synthetic** vendor-style names | tolerance to abbreviations and site conventions (synthetic, never mixed into real figures) |
| `catalog_names.py` | the published point names of the catalog mappings `real_names.py` does not use (shipped, no download; `--data` reads their series) | the out-of-sample name check from 0.100 on, and from 0.101 names plus data on the same points |

Results and caveats: [docs/MAPPING-ASSIST.md](../../docs/MAPPING-ASSIST.md#evaluation).

## `real_names.py`: real point names

```sh
camber datasets fetch irish-ahu nuig-ahu101 robod b4b-windesheim sdu-ou44 ornl-frp-ops lbnl-b59 --subset full
python examples/suggester_eval/real_names.py      # --search DIR also finds already-downloaded copies
```

Scores every point of each dataset that the catalog maps to a role, by its published name:
`lexical` (the default `FeatureSuggester()`, name only), `timeseries` (the data only, role
templates) and `combined` (`FeatureSuggester(use_timeseries=True)`, the name and the data). It
reports top-1 and top-3 per dataset and pooled, the most frequent confusions, and each point
where adding the data changed the top-1 result.

- **Ground truth** is each dataset's catalog mapping (`camber/datasets/mappings/`), hand-curated
  by CAMBER from the publisher's documentation and the data. For `lbnl-b59` the rooftop-unit
  points take their role from the publisher's Brick model, with CAMBER's overrides.
- **Only mapped points are scored.** A point CAMBER left unmapped has no label. Columns CAMBER
  derives at ingest (a fan-on flag computed from a power reading) are not published names, so
  they are skipped. A point repeated across runs (scenarios, fault variants) is scored once,
  from its fault-free run. The same name with identical data on two pieces of equipment is also
  scored once.
- **Real buildings and the LBNL simulated FDD sets are reported apart.** The simulated sets have
  systematic, simulation-style names.
- **Leakage.** The name tokenizer was written against the `irish-ahu` point list, and some of its
  noise words and tests come from `lbnl-b59` names. The pooled "excluding in-sample names" row
  leaves both out.
- Only open-tier entries are used. Cite each dataset as its catalog entry asks
  (`camber datasets info <id>`).

- **From 0.100 the lexical figures are in-sample.** The 0.100 vocabulary additions (#102) were
  chosen from the 0.96 misses on these names. Use `catalog_names.py` for an out-of-sample read.

## `catalog_names.py`: held-out catalog names (0.100)

```sh
python examples/suggester_eval/catalog_names.py      # no download; reads camber/datasets/mappings
```

Scores the default name-only `FeatureSuggester()` on every published point name of the catalog
mappings that `real_names.py` does not evaluate, against the role CAMBER assigned when the
dataset was catalogued. Templated names (`zone_{z}_temp`) are skipped. It reports each mapping
file and the pool, and lists every miss. One large file (`ornl_frp_vav.json`, 92 points) weighs
heavily in the pool.

**Names plus data (`--data`, 0.101).** It also scores each held-out name with its series from
the catalog data:

```sh
camber datasets fetch cofactor-drammen finnish-dcv nist-heatpump-fdd nist-ibal ornl-frp-vav \
  ornl-supermarket-fdd valladolid-uva --subset full
python examples/suggester_eval/catalog_names.py --data   # --search DIR also finds copies on disk
```

- Three suggesters are compared on each point with data:
  - name only (`FeatureSuggester()`, no series);
  - the same default suggester with the series passed (adds the physical-range gate);
  - name plus data (`FeatureSuggester(use_timeseries=True)`).
- It reports top-1 and top-3 per mapping file and pooled, and each point whose top-1 the data
  helped or hurt against the name alone.
- Series are read as in `real_names.py`: 15-minute means from the first fault-free run, with no
  unit passed. A series needs half a day of data (`--min-samples 48`), because these lab and
  test datasets publish runs of 18 to 24 hours.
- Only open-tier entries are read. `rbc-g36-ahu` is research-only, so its names are scored by
  name only.
- The series themselves are scored, not cached profiles, because the range gate reads them.
- Archive members are extracted under `--work`.

## `bts.py`: BTS, names hidden

> Data: Prabowo, A., Lin, X., Razzak, I., Xue, H., Yap, E. W. J., Amos, M., Salim, F. D. (2024).
> *BTS: Building Timeseries Dataset: Empowering Large-Scale Building Analytics.* NeurIPS 2024
> Datasets and Benchmarks Track (arXiv doi:10.48550/arXiv.2406.08990). Licensed CC BY 4.0;
> catalog id `bts`. CAMBER redistributes none of it.

```sh
camber datasets fetch bts --subset full        # ~19 GB, verified against the catalog pins
python examples/suggester_eval/bts.py          # ~1 minute once profiled; results under examples/_data/
```

The labels are the CAMBER roles of the 903 points with data whose Brick class maps cleanly
(`camber.interop.brick`): site A 416, B 32, C 455. Scoring is leave-one-building-out.

| method | what it sees |
|---|---|
| `lexical` | the name only: the default `FeatureSuggester()` |
| `timeseries_templates` | the data only, scored against CAMBER's hand-written role templates (no training) |
| `timeseries_fitted` | the data only, a `ProfileModel` fitted on the other two buildings |
| `combined_*` | the name and the data: `FeatureSuggester(use_timeseries=True)` |

The **anonymised** runs use the published stream id. These are the honest BTS figures.

The **named** runs use the Brick class text as the name (`Zone_Air_Temperature_Sensor`). That
text is effectively the label, so those rows are **Brick-class labels used as names: an upper
bound, not real-world naming**. They show only whether the data spoils a good name.

`--files DIR` reads the published files from a directory instead of the catalog cache.

## `messy_names.py`: synthetic vendor-style names

```sh
python examples/suggester_eval/messy_names.py    # needs bts.py's profile cache
```

A seeded generator turns each BTS point's Brick class into a name in five styles:

- `AHU1_SAT`
- `VAV-2-14 DA-T`
- `B2.L3.FCU07.RmTmp`
- `ahu_03_supply_temp`
- `201-AHU3:SA-TMP`

Each style uses abbreviations, an equipment prefix and number, and separators. The name-only
and name + data suggesters are then scored on these names. **The names are synthetic.** The
abbreviation tables were written once and not tuned to the scores.
