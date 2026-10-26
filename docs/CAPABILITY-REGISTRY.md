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
| `truncated` | `True` when a list was cut to the caller's row limit |

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
capability name, the canonical JSON of the validated arguments and the sorted allowlist, so the
same call always gives the same ids and two different calls give different ones: a model cannot
cite `F1@rule_run` from one call while meaning another, and the facts of several calls can be
merged for `check_citations` without a clash. A handler writes plain ids (`F1@rule_run`); the
registry scopes them. A facility is named only by `facility_id`, so the allowlist and the
research-only refusal apply to every one that reads building data. Lists are top-level, worst
first where that means something, so a row cap keeps the rows that matter.

| Name | Arguments | Returns | Cost |
|---|---|---|---|
| `facilities` | none | the allowed facilities: id, name, lifecycle state, time zone, first and last stored sample, point and equipment counts | cheap |
| `points` | `facility_id`; optional `equip`, `role`, `equip_class` | the facility's stored points (equipment, class, role), sorted, and their count | cheap |
| `coverage` | `facility_id`; optional `equips`, `roles`, `start`, `end`, `group_by` (`point` / `equip`), `min_gap_hours` | per point the samples, first and last sample, interval, coverage and gaps; per equipment the mean and the worst point; the longest gaps | one timestamp scan |
| `sensor_trust` | `facility_id`; optional `equips`, `roles`, `start`, `end`, `view` (`rules` / `report`), `min_trust` | per point the trust score, verdict, flags and whether it is below the caller's trust floor (`ctx.min_trust`, or a higher `min_trust`), least trusted first | reads every trend |
| `rule_run` | `facility_id`; optional `rules`, `params` (`{rule: {param: value}}`), `equips`, `start`, `end`, `min_severity` | the findings (`finding_id`, rule, equipment, severity, summary, caveats, scalar metrics), worst first; counts per severity; the rules skipped for missing inputs | long-running |
| `finding_detail` | `facility_id`, `finding_id`; the same optional arguments as the `rule_run` call | one finding's full metrics, the parameters it ran with (default or overridden), its flagged hours as time spans (and per named sub-condition), and which evidence chart applies; no image | one rule |
| `rcx_report` | `facility_id`; optional `equips`, `start`, `end`, `top_n` | the RCx report as data: KPIs, ranked issues (cause, cost or why uncosted, hours, confidence and its reasons), the section list and the report's fact index; no file | long-running |
| `mv_summary` | `facility_id`; optional `equips` | the M&V findings of the project the caller's run config declares for the facility: baseline fits, savings with uncertainty, triggers, method proposals, and which declined | seconds |
| `drift` | `facility_id`; optional `baseline` `[start, end]`, `current`, `families`, `equips` | drift findings and per-equipment verdicts against a known-good baseline window (fitted for the call, never saved) or the run config's frozen baselines (read, never written), and the equipment not evaluated | long-running |
| `dataset_search` | optional `query`, `kind`, `labeled`, `licence` | catalog metadata: id, title, kind, licence, access, commercial use, fault labels, equipment, download size, what it is good for | cheap |
| `dataset_entry` | `dataset_id` | one catalog entry's metadata: summary, publisher, citation, DOIs, licence and access reason, subsets, suggested rules, known issues and data issues | cheap |
| `rule_params` | optional `rules`, `query`, `detail` | the rules and opt-in drift detectors, the equipment families each is written for, and their tunable parameters with defaults, units, ranges and (with `detail`) basis and calibration advice | cheap |
| `check_citations` | `text`, `facts`; optional `per_sentence` | a verdict per sentence of a drafted text (`supported`, `unknown_citation`, `uncited`, `unsupported_number`) against the facts of earlier results, and the text with the unsupported sentences removed | cheap |

**Which config a run uses.** `rule_run`, `finding_detail`, `rcx_report` and `drift` use the
caller's run config (`ctx.config`) when it names the facility -- its rules, parameters, mapping,
topology and report options -- and otherwise a store-backed config with every stored equipment
class (the facility's weather equipment, if any, as the shared outdoor-air temperature) and
every built-in rule at its defaults, with a banner saying so. `mv_summary`, and `drift` without
a `baseline`, need the caller's run config: an M&V project and frozen drift baselines are
declared there. Every run goes through `run_config_for`, so it is no-persist and gets the trust
floor. A configured OAT reference that fetches weather over the network is left out of
`rcx_report` (a CSV reference is kept), and no prose is generated.

**Finding ids.** A `rule_run` finding is `<rule>@<equip>` (`economizer_high_limit@AHU-1`), with
`#2`, `#3` ... when a rule gives one equipment more than one finding. `finding_detail` re-runs that
one rule with the arguments given, so pass the same `params`, `equips` and window as the
`rule_run` call. A drift finding id carries its family (`ahu:coil_valve_drift@AHU-1`).

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
   run gets through `run_config_for`; a config that asks for more keeps its own.
6. **Caps** (`ctx.caps`). A list in the result longer than `max_rows` (5,000) is cut, with
   `truncated=True` and a banner that says how many rows there were; a result whose JSON is
   larger than `max_bytes` (2 MB) is `too_large`; a call that runs longer than `max_seconds`
   (120 s) is `timeout`.
7. **Citations.** `text` is checked against `facts` with `camber.agent.verify.check` (strict): a
   sentence that cites an unknown id or states a figure no cited fact carries is removed, with a
   banner.
8. **Store errors.** A read of stored data that fails -- a truncated, empty or non-Parquet part
   file, an unreadable facility directory, or a part file a concurrent ingest replaced between
   listing and opening it (the store retries such a read once first) -- or any other `OSError`
   is `unavailable`, never `internal` (0.111, #301). Its `data` is `{"retryable": bool}`, plus
   `"facility_id"` when the read was of one facility the caller may see: `retryable` is `true`
   for a file that went missing (retry the call) and `false` for a damaged or unreadable one (a
   retry will not help until it is repaired or the facility re-ingested). The message names no
   path and no facility outside the allowlist. One facility's damaged file does not affect
   another facility's calls, and the `facilities` listing still lists it, with
   `"unavailable": {"retryable": ...}` and a null `start` / `end` -- while the store's point
   catalog is current. A stale catalog (after a write) is rebuilt by scanning every facility, so
   until it is rebuilt a damaged file anywhere makes calls that need the catalog `unavailable`.
9. **No leaks.** Any other exception becomes `internal`, and no error message -- nor the `data`
   of an error a handler raises, nor a result's `text` or `banners` -- holds an absolute local
   path. The known roots (the store, the config, its directory and the stores or folders it
   reads, the dataset data directory, home, temp, the working directory and the Python prefix)
   are replaced by `<path>` first, in every spelling (quoted, `repr`, JSON, either Unicode
   form), with the rest of the path after them; then a pattern takes any other absolute path
   (a quoted one whole, spaces and commas included). Words either side of a slash
   ("non-commercial / no-derivatives", `kBtu/ft2`) are not paths, and the JSON pointers of an
   `invalid_params` refusal by the input schema (`/facility_id: ...`) are left as they are.

**The timeout cannot stop a handler.** The handler runs in a worker thread; Python cannot kill a
thread, so on a timeout the call returns `timeout` at once while the handler finishes in the
background and its result is thrown away. Handlers are read-only and run no-persist, so a late
finish changes nothing.

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
`run_config_for(ctx)` for any config run, and puts every figure its `text` states into a fact it
cites. The schemas use a subset of JSON Schema 2020-12: `type`, `properties`, `required`,
`additionalProperties`, `enum`, `const`, `items`, `minItems` / `maxItems`, `minimum` /
`maximum` (and the exclusive forms), `minLength` / `maxLength` and `pattern`. `default` is an
annotation and is not applied. Registering a schema with any other keyword is a `ValueError`, so a
constraint is never declared without being enforced.
