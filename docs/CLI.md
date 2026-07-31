# Command-line interface

The `camber` console script (installed with the package; also `python -m camber.cli`) exposes the
analysis pipeline and the grounded agent as subcommands.

```
camber run     <config.json> [--out DIR]        # run a config, print/write findings
camber report  <config.json> --out site.html    # run + write an HTML audit report
camber explain <config.json> [--llm-cmd CMD]    # grounded plain-language explanation of findings
camber ask "<question>" --config <config.json>  # grounded natural-language Q&A over the run
camber fleet   '<glob>' [--ask Q] [--out f.html] # portfolio rollup across configs + triage
camber charts  (--csv F | --demo reheat) [--ahu N] [--out DIR]   # legacy AHU HeC charts
```

A **config** is the same declarative JSON that drives `camber.config.run_config` (source, mapping,
equipment, rules — see the config examples). `run`/`report` execute it; `explain`/`ask` build the
grounded [agent](AGENT.md) context from the run and answer over it.

A `rules` entry is either a bare name or a `{"name", "params"}` object that overrides that rule's
constructor for the run — e.g. a high-outside-air building setting its design minimum:

```json
"rules": ["simultaneous_heat_cool",
          {"name": "economizer_high_limit", "params": {"high_limit_f": 75, "min_damper": 0.45}}]
```

## Grounded agent from the shell

`explain` and `ask` are useful with **no LLM** — they fall back to the deterministic template answer,
fully grounded with `[id]` citations. To wire a model, pass `--llm-cmd` a shell command that reads the
**prompt on stdin** and writes the **completion on stdout**:

```bash
camber ask "which zones are uncomfortable and why?" --config site.json \
  --llm-cmd 'my-llm-cli --model whatever'
```

This is deliberately **vendor-neutral**: CAMBER names and imports no provider. The subprocess wrapper
lives in the CLI, not in `camber.agent`, so the agent package stays free of I/O (enforced by
`tests/test_agent_readonly_guard.py`). Every answer is verified against the fact whitelist; ungrounded
claims are repaired (or the answer falls back to the template).

## Portfolio triage

`camber fleet 'sites/*/config.json' --ask "which building wastes the most?"` runs each config, builds a
[fleet rollup](SITE-REPORT.md), and answers the question grounded in per-building facts (EUI, fault
counts, recoverable $/yr). Add `--out fleet.html` for the rollup report.

## Backward compatibility

Before 0.5 the CLI took `--csv`/`--demo` at the top level; those AHU heating-vs-cooling charts now live
under **`camber charts`** (e.g. `camber charts --demo reheat --ahu 1 --out out/`).
