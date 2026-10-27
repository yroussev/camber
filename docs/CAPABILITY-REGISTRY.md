# Capability registry

`camber.capabilities` (0.110, #275; **provisional**) is one read-only core that CAMBER's assistant
servers expose: the MCP server (0.110) and the A2A server (0.112) are thin adapters over it, so
the rules about what a caller may see and do are enforced in one place.

A **capability** is a named, read-only operation with JSON Schema 2020-12 input and output
schemas and a handler. Both schemas are object schemas (MCP 2025-11-25 takes only an object
`outputSchema` and `structuredContent`), so `data` is always an object whose lists sit under
keys. A call returns a `CapabilityResult`:

| Field | What it holds |
|---|---|
| `data` | the structured result, valid against the capability's output schema |
| `text` | a short summary that cites fact ids such as `[F1@points-1a2b3c]` (one token per call) |
| `facts` | the fact index those ids name: `{"id", "kind", "value", "text", ...}` |
| `banners` | licence, truncation and citation notices, always shown to the user |
| `truncated` | `True` when any list in `data` is not the whole list: a page (see **Pages** below), or a list cut to the row cap |

A refused or failed call raises `CapabilityError` with a `code`: `invalid_params`,
`unknown_capability` (no capability by that name), `not_found` (the capability found nothing,
e.g. a facility with no stored data), `not_allowed`, `refused_licence`, `too_large`, `timeout`,
`unavailable` (the stored data could not be read; see below) or `internal`.

```python
import pandas as pd

from camber.capabilities import CallContext, default_registry
from camber.model.roles import Role
from camber.store import ParquetStore

idx = pd.date_range("2024-03-01", periods=24, freq="h")
store = ParquetStore("store")
store.write_role_frame(
    pd.DataFrame({Role.OAT: 60.0, Role.SUPPLY_AIR_TEMP: 55.0}, index=idx),
    facility_id="hq",
    equip="AHU-1",
    equip_class="AHU",
)

ctx = CallContext(store="store", allowed_facilities=frozenset({"hq"}))
reg = default_registry()
print(len(reg.capabilities()), len(reg.prompts()))  # 13 8
res = reg.call("points", {"facility_id": "hq"}, ctx)
print(res.text)  # hq stores 2 matching points on 1 equipment [F1@points-<token>].
```

## Built-in capabilities

Thirteen capabilities, all read-only (0.110, #275 and #278). Each result's `text` cites fact ids
`F<n>@<capability>-<token>` and repeats the cited values next to them (`hq is active with 6
points [F3@facilities-1a2b3c]`), so a model that reads only the text has them (`rcx_report` also
returns the report's own fact index, `K1@summary-<token>`, `C1@<issue key>-<token>`, ...).

**Fact ids are unique per call.** `Registry.call` appends a token to the context of every fact id
a handler returns -- in `facts`, in the `[id]` citations of `text` and wherever `data` names a
fact id (a row's `fact`, the RCx fact index). The token is six hex digits of SHA-256 over the
capability name, the canonical JSON of the validated arguments, the sorted allowlist and what
else decides the values (0.111, #295): a digest of the loaded run config (its content, not its
path), the trust floor and a store generation (the modification signature of the facility
registry and of each facility the call reads: the ones its arguments name, else every allowed
one). So the same call on the same server and unchanged data gives the same ids, also after a
restart, while two different calls, or the same call on two servers that differ in `--config`,
`--min-trust` or stored data, or after an ingest, give different ones: a model cannot cite
`F1@rule_run` from one call while meaning another, and the facts of several calls, from one
server or several, can be merged for `check_citations` without a clash. The token is a
collision-resistant name, not a signature: it does not prove a fact came from a server. A handler writes plain ids (`F1@rule_run`); the
registry scopes them. A facility is named only by `facility_id`, so the allowlist and the
research-only refusal apply to every one that reads building data. Lists are top-level, worst
first where that means something, so a row cap keeps the rows that matter.

**Pages** (0.111, #299). A list-returning capability returns one model-sized page of each list
by default, so a typical result stays under about 100 KB (25k tokens) on the wire, and takes
`limit` (the page size, up to 1,000) and `offset` (rows to skip) to ask for others; the counts
in `data` (`count`, `n_points`, the KPIs ...) are always of the whole list. A page that is not
the whole list sets `truncated` and adds a banner such as
`'findings' shows rows 1-25 of 159 (worst first): call again with offset=25 for the next page,
or a larger limit (up to 1000)`. The default pages: `facilities` 100, `points` 250, `coverage`
50 per list (equipment, points, gaps), `sensor_trust` 100 points, `rule_run` 25 findings and 25
skipped rules, `drift` 25 per list, `rule_params` 25 rules (10 with `detail`), `check_citations`
50 sentences; `rcx_report` returns `top_n` ranked issues (default the config's, else 8; `offset`
for the next ones) with only their facts and sections, and the first 20 entries of the fact
index's appendix A (declines and data gaps; `limit` for more).

A capability cuts a list only with `camber.capabilities._page.page`, which records the notice in
the call; `Registry.call` turns every recorded notice into a banner and sets `truncated`, so a
capability cannot cut a list and leave the flag false.

| Name | Arguments | Returns | Cost |
|---|---|---|---|
| `facilities` | optional `limit`, `offset` | the allowed facilities: id, name, lifecycle state, time zone, first and last stored sample, point and equipment counts | cheap |
| `points` | `facility_id`; optional `equip`, `role`, `equip_class` | the facility's stored points (equipment, class, role), sorted, and their count | cheap |
| `coverage` | `facility_id`; optional `equips`, `roles`, `start`, `end`, `group_by` (`point` / `equip`), `min_gap_hours` | per point the samples, first and last sample, interval, coverage (of the whole window, at the point's own interval) and gaps; per equipment the mean and the worst point; the longest gaps | one timestamp scan |
| `sensor_trust` | `facility_id`; optional `equips`, `roles`, `start`, `end`, `view` (`rules` / `report`), `min_trust` | per point the trust score, verdict, flags, coverage (of the point's own span, or its equipment's running hours: it can differ from `coverage`'s for one point) and whether it is below the caller's trust floor (`ctx.min_trust`, or a higher `min_trust`), least trusted first | reads every trend |
| `rule_run` | `facility_id`; optional `rules`, `params` (`{rule: {param: value}}`), `equips`, `start`, `end`, `min_severity` | the findings (`finding_id`, rule, equipment, severity, summary, caveats, scalar metrics), worst first; counts per severity; the rules skipped for missing inputs | long-running |
| `finding_detail` | `facility_id`, `finding_id`; the same optional arguments as the `rule_run` call | one finding's full metrics, the parameters it ran with (default, overridden or the rule's built-in fallback), its flagged hours as contiguous time spans (and per named sub-condition; an event rule's events counted apart from the hours) or why there are none (`violations_note`), and which evidence chart applies; no image | the same run as `rule_run` |
| `rcx_report` | `facility_id`; optional `equips`, `start`, `end`, `top_n`, `offset`, `limit` | the RCx report as data: KPIs, the `top_n` ranked issues (cause, the rules and the `finding_ids` to pass to `finding_detail`, cost or why uncosted, hours, confidence and its reasons), the section list and the report's fact index; no file | long-running |
| `mv_summary` | `facility_id`; optional `equips` | the M&V findings of the project the caller's run config declares for the facility: baseline fits, savings with uncertainty (in the meter's own units unless the config has a `units` block, and "for information only" when the baseline fails G14 acceptance; both said in the text), triggers, method proposals, and which declined | seconds |
| `drift` | `facility_id`; optional `baseline` `[start, end]`, `current`, `families`, `equips` | drift findings and per-equipment verdicts against a known-good baseline window (fitted for the call, never saved) or the run config's frozen baselines (read, never written), and the equipment not evaluated | long-running |
| `dataset_search` | optional `query`, `kind`, `labeled`, `licence` | catalog metadata: id, title, kind, licence, access, commercial use, fault labels, equipment, download size, what it is good for | cheap |
| `dataset_entry` | `dataset_id` | one catalog entry's metadata: summary, publisher, citation, DOIs, licence and access reason, subsets, suggested rules, known issues and data issues | cheap |
| `rule_params` | optional `rules`, `query`, `detail` | the rules and opt-in drift detectors (`run_with`: `rule_run` for a rule, `drift` for a detector, which `rule_run` refuses), the equipment families each is written for, and their tunable parameters with defaults, units, ranges and (with `detail`) basis and calibration advice | cheap |
| `check_citations` | `text`, `facts`; optional `per_sentence`, `detail` | the counts per verdict (`supported`, `unknown_citation`, `uncited`, `unsupported_number`) of a drafted text's sentences against the facts of earlier results, and the sentences that fail; with `detail`, every sentence and the text with the unsupported sentences removed | cheap |

**Which config a run uses.** `rule_run`, `finding_detail`, `rcx_report` and `drift` use the
caller's run config when it names the facility -- its rules, parameters, mapping, topology and
report options. `ctx.config` is one config path or a tuple of them, one per facility (0.111,
#296). Without one, a facility ingested from a catalog dataset (`ds-...`) runs with that
dataset's packaged config template, the one `camber datasets config` writes, read from the
package (no network, nothing written), with a banner naming the dataset; `from_config` is then
true. Any other facility gets a store-backed config with every stored equipment class (the
facility's weather equipment, if any, as the shared outdoor-air temperature) and every built-in
rule at its defaults, with a banner saying so. `mv_summary`, and `drift` without a `baseline`,
need a run config with an M&V project or a drift section (a dataset template counts). Every run goes through `run_config_for`, so it is no-persist and gets the trust
floor. `rcx_report` also runs the config's `drift` section, as `camber report --layout rcx`
does (0.111, #297), so its ranked issues match the command line's. A configured OAT reference
that fetches weather over the network is left out of `rcx_report` (a CSV reference is kept), and
no prose is generated. A configured sensor reference (`report.rcx.references`, such as a duct
logger) is compared in `rcx_report` only; an issue that rests on a sensor the reference finds
wrong names it in its fact ("conditional on sensor_drift:supply_air_temp warn").

**A selection that matches nothing is `not_found`** (0.111, #296), never an empty answer that
reads as a clean bill of health. An `equips` / `equip` id, a `roles` / `role` or an `equip_class`
the facility does not store is `not_found` in every capability that takes one (`points`,
`coverage`, `sensor_trust`, `rule_run`, `finding_detail`, `rcx_report`, `drift`, `mv_summary`).
The message names the unknown values and up to 10 of the stored ones (quoted, one line), and the
error's `data` has `argument`, `unknown` and up to 50 `available` values. An `equips` id the
facility stores but the run config runs no rule on (its class is not in the config's
`equipment`) is `not_found` too. A `start` / `end` window (or a drift `baseline` / `current`
period) with no stored sample is `not_found` for the run capabilities and `sensor_trust`,
naming the first and last stored sample (`data`: `argument`, `first`, `last`): nothing can be
judged there. `coverage` alone answers such a window, with 0% coverage and a notice, because
that is the answer. A selection that exists but has no data (an equipment with no such point,
a window inside the data where some points are missing) is still an answer.

**Finding ids.** A `rule_run` finding is `<rule>@<equip>` (`economizer_high_limit@AHU-1`), with
`#2`, `#3` ... when a rule gives one equipment more than one finding. `finding_detail` makes the
same run as `rule_run` with the arguments given (every rule of it, so a verdict one rule takes
from another reads the same), so pass the same `rules`, `params`, `equips` and window as the
`rule_run` call. A span in `violations` is a run of flagged samples with no gap longer than one
and a half intervals; `share` is of the `judged_samples` the rule's mask covers, not of the
whole window. A drift finding id carries its family (`ahu:coil_valve_drift@AHU-1`).

**Catalog metadata is public.** `dataset_search` and `dataset_entry` list research-only entries
too, each with its licence, `access` and a notice (and a banner) that this server refuses any
facility ingested from it. Nothing is fetched or ingested: that is `camber datasets`, run by a
person.

## Built-in prompts

Each prompt renders one user message naming the capabilities to call and in what order, asks
for every sentence to end with the fact ids it rests on, forbids a number no cited fact carries,
says a declined check or a refused call is not a pass, and ends by calling `check_citations` on
the draft with the facts the calls returned. Argument values are reduced to one short line.

| Name | Arguments | Calls |
|---|---|---|
| `triage_ahu` | `facility_id`, `equip` | `points`, `sensor_trust`, `rule_run`, `finding_detail` (the worst findings), `check_citations` |
| `rcx_executive_summary` | `facility_id` | `rcx_report`, `check_citations` |
| `explain_finding` | `facility_id`, `finding_id` | `finding_detail`, `rule_params`, `sensor_trust`, `check_citations` |
| `check_savings_claim` | `facility_id`; optional `claim` | `mv_summary`, `check_citations` |
| `data_quality_review` | `facility_id` | `coverage` (by equipment, then the worst), `sensor_trust`, `check_citations` |
| `compare_equipment` | `facility_id`, `equips` (two or more, comma-separated) | `coverage`, `sensor_trust`, `rule_run`, `check_citations` |
| `sensor_trust_review` | `facility_id` | `sensor_trust` (both views), `rule_run`, `check_citations` |
| `plan_site_visit` | `facility_id` | `rcx_report`, `sensor_trust`, `finding_detail`, `check_citations` |

## The policy every call follows

`Registry.call(name, arguments, ctx)` enforces, in order:

1. **Input validation.** `arguments` must match the input schema, else `invalid_params` (the
   error's `data["errors"]` lists each problem with its JSON pointer).
2. **Facility allowlist.** `ctx.allowed_facilities` is required; empty means no facility is
   visible. A capability names a facility only through a `facility_id` or `facility_ids`
   argument, and one outside the allowlist is `not_allowed`, whether it exists or not, so a
   caller cannot probe for ids. A listing shows only allowed facilities, and a result that names
   another facility is an `internal` error.
3. **Research-only data is refused.** A facility ingested from a dataset the
   [catalog](DATASETS.md) holds research-only (a non-commercial or no-derivatives licence, or an
   open-licence dataset held back for a stated reason) is `refused_licence`. There is no override
   in any adapter: `--accept-noncommercial` is for the person running `camber datasets`, not for
   an assistant. The check reads the facility's recorded dataset block (`access`,
   `redistribution`, `licence`) and, by its `dataset_id`, the catalog's current terms. A listing
   leaves such facilities out and says how many it withheld.

   The check fails closed (0.111, #290). A facility named for a catalog dataset (`ds-<base>` or
   `ds-<base>-<group>`, the names `camber datasets ingest` gives) is checked against the
   catalog by its name too, independent of the registry, so a registry entry that is missing or
   records an open licence cannot unlock a research-only dataset. A facility whose licence or
   lifecycle cannot be established is also `refused_licence`, with
   `data["reason"] == "licence_unverified"`: every facility while the store's facility registry
   is missing (in a store holding `ds-` facilities), unreadable, empty or malformed, and a `ds-`
   facility the registry has no entry for or that neither the catalog nor its entry records.
   While the registry is damaged, every result carries a banner saying so, and the `facilities`
   listing counts such facilities in `withheld_unverified`. A store of your own data with no
   registry is not damaged: its unregistered facilities are served as before.
4. **No-persist.** The handler runs inside the no-persist scope (#276): a config run folds no
   fault history, writes no report file or manifest entry, writes back no store cache and
   fetches no weather. See [Fault lifecycle](FAULT-LIFECYCLE.md#no-persist-runs).
5. **Trust floor.** `ctx.min_trust` (0.5 by default) is the lowest `trust_gate.min_trust` a config
   run gets through `run_config_for`; a config that asks for more keeps its own. A check whose
   input scores below the floor declines with an `info` finding. `rule_run`, `drift` and
   `rcx_report` then carry a banner with the number of declined checks, the rules and the
   untrusted inputs, and their summary and first fact give the count (0.111, #297): a declined
   check is not a pass.
6. **Caps** (`ctx.caps`). A list in the result longer than `max_rows` (5,000) is cut, with
   `truncated=True` and a banner that says how many rows there were; a result whose JSON --
   `data`, `text`, `facts` and `banners` together (0.111, #299) -- is larger than `max_bytes`
   (2 MB) is `too_large`; a call that runs longer than `max_seconds` (120 s) is `timeout`.
7. **Citations.** `text` is checked against `facts` with `camber.agent.verify.check` (strict): a
   sentence that states a figure no cited fact carries is removed, and a citation of an id the
   result has no fact for is removed (its sentence kept), each with a banner that says which
   (0.111, #291).
8. **Text from the data** (0.111, #291). Facility, equipment and point names, classes, units and
   metadata come from the data, so they can never pass for the tool's own words. A result's
   `text`, every fact's `text`, every banner and every error message are one line: newlines and
   tabs become spaces, and control characters (C0 / C1: ESC sequences, BEL, NUL), bidi overrides
   and isolates, zero-width and other invisible characters are removed. A line that starts with
   `NOTICE:` in the MCP summary is therefore always a banner, and a text that would itself start
   with `NOTICE` reads `Summary: NOTICE ...`. A handler names a data value with
   `camber.capabilities._sanitize.name()`, which quotes it (`equipment "AHU-2"`), writes a
   fact-id bracket inside it as `(F1@rule_run)` and cuts it at 100 characters (an ellipsis and
   six hex digits of a hash keep two cut names apart); a quoted value is one word to the citation
   check, so a sentence break or a number inside a name is never "removed". In `text`, only the
   handler's own fact ids are citations: any other `[F1@x]` reads `(F1@x)`, and a fact's `text`
   holds no citation at all. `data` keeps the stored values exactly (so an id can be passed
   back), except that any string in `data` or `facts` longer than 2,000 characters is cut the
   same way, so one huge name does not make a whole facility `too_large`. Schema `pattern`s use
   ECMA-262's `$` (the end of the string; Python's `$` also matches before a final newline). New
   equipment ids, classes and roles with such characters are refused at ingest (see
   [SCALE.md](SCALE.md#facility-identity-why-an-id-not-a-name)).
9. **Store errors.** A read of stored data that fails -- a truncated, empty or non-Parquet part
   file, an unreadable facility directory, or a part file a concurrent ingest replaced between
   listing and opening it (the store retries such a read once first) -- or any other `OSError`
   is `unavailable`, never `internal` (0.111, #301). So is a call that a writer replacing or
   removing files of a facility it reads overlapped -- a forced re-ingest's partition swap, a
   prune, a removal -- whatever the handler answered (0.111, #306): each such writer marks the
   facility busy in `<store>/_fence/<facility_id>` and gives it a fresh token when done, and a
   call checks the fences of the facilities it reads (those its arguments name, else every
   allowed one) before and after it runs; it waits up to a second for a swap in progress. Its
   `data` is `{"retryable": bool}`, plus `"facility_id"` when the read was of one facility the
   caller may see: `retryable` is `true` for a file that went missing or a concurrent replace
   (retry the call) and `false` for a damaged or unreadable one (a
   retry will not help until it is repaired or the facility re-ingested). The message names no
   path and no facility outside the allowlist. One facility's damaged file does not affect
   another facility's calls, and the `facilities` listing still lists it, with
   `"unavailable": {"retryable": ...}` and a null `start` / `end`. A call reads only the
   facilities it is about (0.111, #293): while the store's point catalog is stale (after a
   write; a call never writes it back), a call on one facility scans that facility's partition
   only, and `facilities` reads each listed facility's catalog entry and its files' statistics,
   never its rows -- so neither the cost of a call nor a damaged file depends on the rest of the
   store.
10. **No leaks.** Any other exception becomes `internal`, and no error message -- nor the `data`
   of an error a handler raises, nor a result's `text` or `banners` -- holds an absolute local
   path. The known roots (the store, the config, its directory and the stores or folders it
   reads, the dataset data directory, home, temp, the working directory and the Python prefix)
   are replaced by `<path>` first, in every spelling (quoted, `repr`, JSON, either Unicode
   form), with the rest of the path after them; then a pattern takes any other absolute path
   (a quoted one whole, spaces and commas included). Words either side of a slash
   ("non-commercial / no-derivatives", `kBtu/ft2`) are not paths; after a redacted root, the
   path goes on across a space while the next word has a separator in it
   (`<path>/John Smith/My Drive/x` is one path; a last word with no separator cannot be told
   from the sentence, so quote paths in messages). The JSON pointers of an
   `invalid_params` refusal by the input schema (`/facility_id: ...`) are left as they are.

**In-process, the timeout cannot stop a handler.** The handler runs in a worker thread; Python
cannot kill a thread, so on a timeout the call returns `timeout` at once while the handler
finishes in the background and its result is thrown away. Handlers are read-only and run
no-persist, so a late finish changes nothing, but it holds its CPU and memory until then.
`camber mcp` therefore runs every call in a worker process (`camber.mcp.WorkerPool`, 0.111) that
calls `Registry.call` exactly as here and is killed on a timeout, a cancel or a disconnect; a
transport that serves untrusted or many callers should do the same.

## Adding a capability

Adding one is a one-file job: a module `camber/capabilities/_cap_<name>.py` that defines
`CAPABILITIES` (and, optionally, `PROMPTS`). `default_registry()` imports every `_cap_*` module in
name order.

<!-- fence: skip (a module in the camber package, not a session) -->
```python
from camber.capabilities import Capability, CapabilityResult


def equipment_count(ctx, arguments):
    # arguments is valid, and its facility is allowed and licence-clean
    ...
    return CapabilityResult(data={...}, text="... [F1@equipment].", facts=[...])


CAPABILITIES = (
    Capability(
        name="equipment_count",
        title="Equipment count",
        description="What it does, when to call it and what it returns (for a tool picker).",
        input_schema={"type": "object", "required": ["facility_id"], "properties": {...}},
        output_schema={"type": "object", "properties": {...}},
        handler=equipment_count,
    ),
)
```

A handler names facilities only through `facility_id` / `facility_ids`, keeps a listing to
`ctx.allowed_facilities` and leaves out research-only facilities (`research_only(meta)`), calls
`run_config_for(ctx)` for any config run, puts every figure its `text` states into a fact it
cites, and writes every name from the data in its `text`, facts and banners with
`camber.capabilities._sanitize.name()`. The schemas use a subset of JSON Schema 2020-12: `type`, `properties`, `required`,
`additionalProperties`, `enum`, `const`, `items`, `minItems` / `maxItems`, `minimum` /
`maximum` (and the exclusive forms), `minLength` / `maxLength` and `pattern`. `default` is an
annotation and is not applied. Registering a schema with any other keyword is a `ValueError`, so a
constraint is never declared without being enforced.
