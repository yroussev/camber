# G36 cross-check: CAMBER vs open-fdd

A reusable harness that runs CAMBER's `g36_afdd` and the two engines of
[open-fdd](https://github.com/bbartling/open-fdd) (MIT) on the same labelled frames, and scores
each engine on its own ([#22](https://github.com/yroussev/camber/issues/22), item 1). The
published results and their caveats are in
[docs/ECOSYSTEM.md](../../docs/ECOSYSTEM.md#cross-validation-g36-fault-conditions-vs-open-fdd).

**Ground rules.** Files and processes only: CAMBER never imports open-fdd, and no open-fdd code
or SQL is copied here. The pandas engine runs in its own venv
(`openfdd_pandas_driver.py`, launched as a subprocess); the SQL engine runs as `fdd_cli` in a
container built from the pinned commit (`Dockerfile.fdd_cli`), with `--network none` and
read-only data mounts. Every result is labelled with its engine, version and tolerance profile.
Verdicts are never merged.

## Files

| File | What it is |
|---|---|
| `role_map.json` | The versioned CAMBER role ↔ open-fdd column / SQL role mapping, plus the declared fallback (fan command from run status) and substitution (MAT/SAT as cooling-coil temperatures on a cooling-only AHU, pandas engine only) |
| `profiles.json` | Tolerance profiles: `openfdd_defaults` (no override) and `g36` (the G36 Table 5.16.14.7 tolerances CAMBER uses), per engine. SQL rules whose G36 tolerances the tuning file cannot reach run at their defaults and are listed in `sql_not_expressible` |
| `harness.py` | Engine inputs, one normaliser per engine, the verdict rule, scoring (Wilson CIs), Markdown |
| `run_crosscheck.py` | The runner: one command, every engine, same frames |
| `openfdd_pandas_driver.py` | Runs inside open-fdd's venv and calls `open_fdd.rules.run_rule` |
| `Dockerfile.fdd_cli` | Builds open-fdd's SQL CLI from the pinned commit |
| `fixtures/` | Synthetic test fixtures: both engines' output recorded on the synthetic probes, and hand-written SQL result files for the normaliser's edge paths |
| `results/` | The committed results (`results-run.*`, `results-month.*`, `probes.*`) |

## Versions

Both open-fdd engines are pinned to one source snapshot: commit
`32a6d4479abed81f9d6d0ff426260cc8ad8e30e2` (VERSION 3.5.58), from which PyPI `open-fdd` 4.4.9
was released; the wheel's `open_fdd/` tree is identical to the commit's. The repository's newest
tag (v3.2.8, the Rust edge line) predates it, so the commit is the pin. The 4.4.9 wheel declares
Python ≥ 3.10 but imports `enum.StrEnum`, so its venv needs Python 3.11 or newer.

## Run

```sh
# data: the labelled LBNL AHU runs, fetched from the publisher (CC-BY-4.0, never redistributed)
camber datasets fetch lbnl-sdahu --subset full
camber datasets ingest lbnl-sdahu --store xc_store --subset full
camber datasets fetch lbnl-ddahu && camber datasets ingest lbnl-ddahu --store xc_store

# engine (a): open-fdd's pandas engine in its own venv
python3.12 -m venv ofvenv && ofvenv/bin/pip install "open-fdd[oracle]==4.4.9" pyarrow

# engine (b): open-fdd's SQL engine, built from the pinned commit
docker build -f examples/openfdd_crosscheck/Dockerfile.fdd_cli \
    -t camber-crosscheck/fdd_cli:32a6d44 examples/openfdd_crosscheck

python examples/openfdd_crosscheck/run_crosscheck.py \
    --store lbnl-sdahu=xc_store --store lbnl-ddahu=xc_store \
    --openfdd-python ofvenv/bin/python --sql-image camber-crosscheck/fdd_cli:32a6d44
#   --window month   scores each (run, month) as a case
#   --probe          runs the synthetic probes instead of the datasets
#   --keep-verdicts  keeps the per-verdict list in the JSON (left out by default)
docker builder prune   # drop the Rust build cache afterwards
```

Since 0.101 the JSON leaves out the per-verdict list by default and collapses each "not
evaluated" map to counts per reason (a month-window run would otherwise write about 200k more
lines); the Markdown tables are the same either way. `--keep-verdicts` keeps them. The committed
results were re-run on the integrated 0.101 code at the new default, so none of them carries the
per-verdict list.

An engine whose prerequisite is missing is listed under "Not run" with the reason. The runner
checks `docker info` once, with a 20 s timeout.

CAMBER runs `g36_afdd` at the G36 defaults plus the `g36_afdd` parameters of each dataset's
catalog run template (on `lbnl-sdahu`, `min_oa_pct` 1.6, which enables FC6; on `lbnl-ddahu`,
since 0.100, the unit's seasonal minimum, `min_oa_pct` 31.8 with `min_oa_pct_by_month` 11.9 in
June to August, and since 0.101 its seasonal minimum damper position, `oa_damper_min` 45 with
`oa_damper_min_by_month` 28 in June to August), as `camber datasets` does.

The SQL engine gets **one building per equipment**. `fdd_cli run-rules` checks a rule's
required roles against the building's columns, the union over its equipment, so equipment
sharing a building would be reported as evaluated, with zero fault hours, on inputs only a
neighbour has. Two containers run per profile: one ingests the read-only CSV tree to Parquet,
and one runs the rules with the Parquet and the tuning file mounted read-only.

To rebuild the SQL image after pruning (about 4 minutes with the Rust base image cached; the image is about 360 MB):
`docker build -f examples/openfdd_crosscheck/Dockerfile.fdd_cli -t camber-crosscheck/fdd_cli:32a6d44 examples/openfdd_crosscheck`. `lbnl-fcu` is not part of the run: neither
engine applies its G36 AHU fault conditions to a fan-coil unit (CAMBER's runner declines the
class; open-fdd's FC rules are scoped to `ahu`).

## Scoring

Each labelled run is one case (`--window month`: each run-month). Positives are the faulted runs,
negatives the fault-free run. Per FC, an engine's verdict on a run is:

- **fired**: its fault hours reach 5 % of **its own** evaluated hours, over at least 24
  evaluated hours;
- **not fired**: evaluated, below that;
- **not evaluated**: the engine did not run the FC there (missing input, equipment class, no
  hours in the FC's operating states, under 24 h). Left out of that FC's confusion counts and
  listed with the reason, never counted as a miss.

Each verdict also records how its denominator is defined: the engines' percentages differ
because their denominators differ, as the 0.1.5 comparison found. "Any FC" counts a run as
detected when any evaluated FC fired. The engines' own alarms (CAMBER: a flagged FC; open-fdd:
status `FAULT`, i.e. any confirmed fault sample) are scored separately under `native`. Rates
come from `camber.validation.metrics_with_ci` (Wilson 95 % intervals).

## Tests

`tests/test_openfdd_crosscheck.py` runs offline, with no Docker and no open-fdd install: the
mapping, frame conversion, SQL tree and tuning file, each normaliser (on the recorded and
hand-written fixtures), the verdict rule and scoring, and an end-to-end `--probe` run of the
CAMBER engine.
