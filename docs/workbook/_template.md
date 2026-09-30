# Title of the exercise, as in its EXERCISE declaration

*Workbook exercise `<exercise-id>` · PNNL re-tuning chapter N · about NN minutes*

<!--
TEMPLATE for a workbook exercise page (not published: mkdocs.yml excludes it).
Copy to docs/workbook/<exercise-id>.md and replace every <...>. Keep the nine H2 sections, in
this order and with these exact names; tests/workbook/test_workbook_docs.py checks them.
Rules:
- Link PNNL only through reference-style links whose label is a camber.references id; print the
  definitions with `python scripts/workbook_refs.py <id> ...`. Never copy PNNL text or figures.
- No answers on this page (they go in docs/workbook/instructor.md, in the exercise's block).
- Core datasets are open-licence catalog entries; a research-only one is a marked optional extra.
- Every CLI command in the EXERCISE declaration's `commands` appears verbatim in a code block.
- No absolute local paths, account names or real client sites.
Delete this comment.
-->

## Goal

<One or two sentences: what the learner finds, and why it matters for re-tuning.>

## Learn more

Read these first (PNNL, free):

- [<Short title>][<reference-id>], <one line on what to read it for>.

[<reference-id>]: <URL printed by scripts/workbook_refs.py> "<title printed by the script>"

## Datasets and licence

- **`<dataset-id>`**: <what it is, in one sentence>. Licence **<SPDX id>** (open; cite it).
  <Download size of the subset used.> The catalog lists the problems found in the published
  data: see [its data issues](../DATASETS.md#<dataset-heading-anchor>).
- *Optional extra (research-only)*: **`<dataset-id>`** ... <only if the exercise has one>

<Which equipment ids / runs the exercise uses.>

## Setup

### In the lab

1. Run `camber lab` and open `http://127.0.0.1:8765/lab`.
2. Tick `<dataset-id>` and press **Fetch & ingest**.
3. <What to open from the row: trends, report.>

### On the command line

```
camber datasets fetch <dataset-id>
camber datasets ingest <dataset-id> --store lab_store
camber datasets config <dataset-id> --exercise <exercise-id> --store lab_store --out <name>.json
camber run <name>.json --out <name>_out
```

<What the config runs and why it is tuned the way it is.>

## Steps

1. <A numbered, concrete step: what to open, what to read, where.>

## Questions

1. <A question the instructor key answers, numbered to match it.>

## What CAMBER shows

- <Which findings, metrics, charts or report pages hold the evidence (no answers).>

## Caveats

- <Limits of the data or the method a learner must keep in mind.>

## Going further

- <Optional follow-ups: another dataset, a parameter to vary, a research-only extra.>
