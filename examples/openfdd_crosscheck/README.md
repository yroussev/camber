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
| `profiles.json` | Tolerance profiles: `openfdd_defaults` (no override) and `g36` (the G36 Table 5.16.14.7 tolerances CAMBER uses), per engine, with the SQL rules the tuning file cannot reach |
| `harness.py` | Engine inputs, one normaliser per engine, the verdict rule, scoring (Wilson CIs), Markdown |
| `run_crosscheck.py` | The runner: one command, every engine, same frames |
| `openfdd_pandas_driver.py` | Runs inside open-fdd's venv and calls `open_fdd.rules.run_rule` |
| `Dockerfile.fdd_cli` | Builds open-fdd's SQL CLI from the pinned commit |
| `fixtures/` | Synthetic test fixtures: pandas output recorded on the synthetic probes, and hand-written SQL result files |
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
docker builder prune   # drop the Rust build cache afterwards
```

An engine whose prerequisite is missing is listed under "Not run" with the reason. The runner
checks `docker info` once, with a 20 s timeout. `lbnl-fcu` is not part of the run: neither
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
