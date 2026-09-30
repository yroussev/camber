# Point-role suggestion on BTS: names versus data

Evaluates CAMBER's point-role suggester (`camber.mapping_assist`, with the 0.96 time-series
evidence of `camber.mapping_timeseries`) on **BTS**, the Building TimeSeries dataset: three real
Australian buildings whose ~20,000 BMS points carry a Brick v1.2.1 class but anonymised ids.

> Data: Prabowo, A., Lin, X., Razzak, I., Xue, H., Yap, E. W. J., Amos, M., Salim, F. D. (2024).
> *BTS: Building Timeseries Dataset: Empowering Large-Scale Building Analytics.* NeurIPS 2024
> Datasets and Benchmarks Track (arXiv doi:10.48550/arXiv.2406.08990). Licensed CC BY 4.0;
> catalog id `bts` (`camber datasets info bts` gives the record, citation and data issues).
> CAMBER redistributes none of it.

## Run

```sh
camber datasets fetch bts --subset full        # ~19 GB, verified against the catalog pins
python examples/bts_suggester/evaluate.py      # ~1 minute; results under examples/_data/
```

`--files DIR` reads the published files from a directory instead of the catalog cache. The
labelled-point profiles are cached, so a re-run only re-scores.

## What it measures

The labels are the CAMBER roles of the points whose Brick class maps cleanly
(`camber.interop.brick`): 903 points with data (site A 416, B 32, C 455). Each method ranks every
CAMBER role for each point; top-1 and top-3 accuracy are reported overall, per role (macro), per
held-out building, with the most frequent top-1 confusions.

| method | what it sees |
|---|---|
| `lexical` | the name only: the current default `FeatureSuggester()` |
| `timeseries_templates` | the data only, scored against CAMBER's hand-written role templates (no training) |
| `timeseries_fitted` | the data only, a `ProfileModel` fitted on the other two buildings (leave one building out) |
| `combined_*` | the name and the data: `FeatureSuggester(use_timeseries=True)` |

BTS publishes no BMS point names, so the **named** runs use the Brick class text as the name
(`Zone_Air_Temperature_Sensor`): an upper-bound stand-in for a well-named point, which shows
whether the data spoils a good name. The **anonymised** runs use the published stream id.

The results and their caveats are in [docs/MAPPING-ASSIST.md](../../docs/MAPPING-ASSIST.md#time-series-evidence-on-bts).
