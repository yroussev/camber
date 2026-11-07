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

A transport shows `facts` and `banners` wherever it shows `data`: `camber mcp` puts them first in
every tool's `structuredContent`, under the key `camber` (`{"facts": {id: text}, "notices":
[...]}`, 0.112, #331), as well as in its text blocks, because some hosts show the model only
`structuredContent`. The key is reserved: registering a capability whose output schema declares
`camber` is a `ValueError`.

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
path; a path it reads stands for what it resolves to, not its spelling or mount point: the store by
its store generation, a folder by its entries' names, sizes and modification times, the mapping
and `shared_oat` files by their size and modification time, 0.112, #326), the trust floor and a store generation (the size and modification time of the facility
registry, the point catalog and every file of each facility the call reads, part files included,
so an in-place rewrite counts, #312: the facilities its arguments name, else every allowed one). So the same call on the same server and unchanged data gives the same ids, also after a
restart, while two different calls, or the same call on two servers that differ in `--config`,
`--min-trust` or stored data, or after an ingest, give different ones: a model cannot cite
`F1@rule_run` from one call while meaning another, and the facts of several calls, from one
server or several, never clash. The token is a collision-resistant name, not a signature: it
does not prove a fact came from a server; the session's fact ledger does (see **Where a cited fact
comes from** below). A handler writes plain ids (`F1@rule_run`); the
registry scopes them. A facility is named only by `facility_id`, so the allowlist and the
research-only refusal apply to every one that reads building data. Lists are top-level, worst
first where that means something, so a row cap keeps the rows that matter.

**What has a fact** (0.112, #294). Besides the summary facts, a result states as facts the
figures a prompt asks a model to give, so a correct sentence can be cited. Each new fact carries
its figure as fields too: `value` with its `unit`, the `subject` it is about (`facility_id`,
`equip`, `role`, and where it applies `condition` or `finding`) and, where one applies, a
`verdict` from a closed set of words, so a citation check can test the subject and the unit as
well as the digits.

| Prefix and kind | Capability | What it states |
|---|---|---|
| `S<n>` `evidence_span` | `finding_detail` | one flagged span: `start`, `end` (ISO), `value` its hours; the spans `violations` lists (the longest 20, in time order), then up to five of each named condition's own spans the union does not list. `F2` (`evidence_hours`) also states the longest span |
| `L<n>` `flat_point` | `finding_detail`, `sensor_trust`, `rcx_report` | a point that never moves over the window (every valid sample the same, at least 24 of them): its constant `value` and `unit`, `verdict` `flat`. `finding_detail` states the rule's inputs, `sensor_trust` every point on its page, `rcx_report` the inputs of the issues it returns (at most 50). A constant has no outliers, so such a point can score trusted while it decides a diagnosis |
| `R<n>` `reference_effect` | `finding_detail` | a finding `conditional_on` a disagreeing sensor reference, re-judged by its rule on the same frame with each such point corrected by its bias: `value` the severity it then gets, `verdict` `explained` (it no longer fires), `reduced`, `persists` or `worse`, with the corrected summary's figures in the text |
| `R<n>` `reference_window` | `finding_detail` | for each configured reference on one of the finding's inputs (a passing one too): how many of its flagged hours fall in the window the comparison covers (`value`, hours), `verdict` `inside`, `partly` or `outside` |
| `P<n>` `point_coverage`, `G<n>` `gap` | `coverage` | each point's coverage share (`%`) and each listed gap (`start`, `end`, `value` hours) of the page, at most 200 of each |
| `F<n>` `issue` | `rcx_report` | now "Issue 1 of 6 on ...", with `rank` and `of` fields, so a numbered ranking can be cited |
| `R<n>` `reference_comparison` | `sensor_trust` | now with `unit` (also in `data.references` and the summary): the unit CAMBER's rules read the point in, `°F` for a temperature and `%` for a valve, damper or speed (`null`: the point's own units), and the bias, RMSE, samples and overlap as fields |
| `A<n>` `claimed_figure` | `mv_summary` with `claim` | each figure the caller's claim states, `basis` `claimed`: what the claim says, never a measured result (only the figure and its unit are written, never the claim's words) |
| `N<n>` `notice` | every capability | each banner of the result, `basis` `notice`, its text exactly the banner's, so a draft that repeats a NOTICE can cite it |
| `V<n>` `caveat` | `rule_run`, `finding_detail`, `mv_summary` | (0112-cite10) each caveat a rule gives its findings, in the rule's words, one fact per rule and caveat text naming every unit it was given for (`rule`, `finding_ids`, and `equip` with `subject` when there is one unit, else `equips`): the leaking_valve rule's "a heat rise downstream of the coils ... can read as a heating leak", an M&V saving's "no mv.method declared: the SEP default, forecast, was used" or its G14-kernel band note, the days a baseline dropped |
| `M<n>` `finding_labels` | `rule_run` (fault and warn findings), `finding_detail` | (0112-cite10) a finding's string-valued metrics (`missed_cause "economizer_not_commanded"`, `oa_cost_airflow "trended supply airflow"`, `fan_gate`, a basis or source) and the samples it masked (`n_masked_fan_off 824`), each value quoted |
| `K1` `skipped_rules` | `rule_run` | (0112-cite10) the rules the run skipped for missing inputs, by name, each with the inputs it lacked and the equipment it was skipped on (`rules` lists them; `F1` counts them), so a draft naming a skipped rule can cite it |
| `B1` `parameter_basis` | `finding_detail` | (0112-cite10) the basis the rule gives for its parameters' values ("max_starts_per_day: CAMBER judgment: a generic cycling limit ...") |

**Pages** (0.111, #299; sizes 0.112, #333). A list-returning capability returns one page of
each list by default, sized so that the default result fits a host that gives the model only
`structuredContent` and caps a tool result at 25,000 tokens: at most 25,000 characters of
`structuredContent` (the data and the `camber` facts and notices) on the largest facilities
tested (two VAV fleets of about 650 points, an 8-meter M&V project, the catalog), where the
defaults run up to about 21,000 characters. Each takes `limit` (the page size, up to 1,000) and `offset` (rows
to skip) to ask for others; the counts in `data` (`count`, `n_points`, the KPIs ...) are always
of the whole list. A page that is not the whole list sets `truncated` and adds a banner such as
`'findings' shows rows 1-12 of 159 (worst first): call again with offset=12 for the next page`:
the next page, not a larger one, which a host may refuse. The default pages: `facilities` 40,
`points` 200, `coverage` 20 per list (equipment, points, gaps), `sensor_trust` 40 points,
`rule_run` 12 findings and 12 skipped rules, `drift` 10 per list, `rule_params` 15 rules (7 with
`detail`), `check_citations` 50 sentences, `dataset_search` 20 datasets, `mv_summary` 4 meters
(each with all its findings); `rcx_report` returns as many of the best-ranked issues as fit
about 22,000 characters, at most 8 (`top_n` sets the page size, `offset` gives the next ones),
with only their facts and sections, and the first 5 entries of the fact index's appendix A
(declines and data gaps; `limit` for more). `rcx_report`'s `fact_index` names each fact (`id`,
`kind`) without repeating its text, which is in the result's facts; `drift` says a long metric
note that several findings of a page share once, in a notice, instead of in each finding. One
`offset` pages every list of a result: a short list whose rows are all on the first page is
empty on a later one, with a banner that says so (`'cited' is empty on this page: all its 1
row(s) are on the first page`).

A capability cuts a list only with `camber.capabilities._page.page`, which records the notice in
the call; `Registry.call` turns every recorded notice into a banner and sets `truncated`, so a
capability cannot cut a list and leave the flag false.

| Name | Arguments | Returns | Cost |
|---|---|---|---|
| `facilities` | optional `limit`, `offset` | the allowed facilities: id, name, lifecycle state, time zone, first and last stored sample, point and equipment counts | cheap |
| `points` | `facility_id`; optional `equip`, `role`, `equip_class` | the facility's stored points (equipment, class, role), sorted, and their count | cheap |
| `coverage` | `facility_id`; optional `equips`, `roles`, `start`, `end`, `group_by` (`point` / `equip`), `min_gap_hours` | per point the samples, first and last sample, interval, coverage (of the whole window: `start` to `end`, an open end the facility's first or last stored sample whatever `equips` or `roles` select; at the point's own interval) and gaps; per equipment the mean and the worst point; the longest gaps | one timestamp scan |
| `sensor_trust` | `facility_id`; optional `equips`, `roles`, `start`, `end`, `view` (`rules` / `report`), `min_trust` | per point the trust score, verdict, flags, coverage (of the point's own span on the rule run's time grid, or its equipment's running hours: it can differ from `coverage`'s for one point) and whether it is below the caller's trust floor (`ctx.min_trust`, or a higher `min_trust`), least trusted first; the run config's sensor-reference comparisons (`references`: bias, its sign, RMSE, correlation, samples, overlap, verdict; a passing one too) | reads every trend |
| `rule_run` | `facility_id`; optional `rules`, `params` (`{rule: {param: value}}`), `equips`, `start`, `end`, `min_severity` | the findings (`finding_id`, rule, equipment, severity, summary, caveats, scalar metrics, and `conditional_on` when an input's configured sensor reference disagrees), worst first; counts per severity; the rules skipped for missing inputs | long-running |
| `finding_detail` | `facility_id`, `finding_id`; the same optional arguments as the `rule_run` call | one finding's full metrics, the parameters it ran with (default, overridden or the rule's built-in fallback), its flagged hours as contiguous time spans (and per named sub-condition; an event rule's events counted apart from the hours) or why there are none (`violations_note`), and which evidence chart applies; no image | the same run as `rule_run` |
| `rcx_report` | `facility_id`; optional `equips`, `start`, `end`, `top_n`, `offset`, `limit` | the RCx report as data: KPIs, the `top_n` ranked issues (cause, the rules and the `finding_ids` to pass to `finding_detail`, cost or why uncosted, hours, confidence and its reasons), the section list and the report's fact index; no file | long-running |
| `mv_summary` | `facility_id`; optional `equips`, `claim` (a claim to check: each figure it states becomes a `claimed_figure` fact), `limit`, `offset` (meters) | the M&V findings of the project the caller's run config declares for the facility, a page of meters at a time: each meter's baseline and reporting dates (`baseline_start` / `baseline_end`, `reporting_start` / `reporting_end` in a finding's metrics, and in its fact: `baseline_period`, `reporting_period` and its text, 0.112), baseline fits, savings with uncertainty (in the meter's own units unless the config has a `units` block, and "for information only" when the baseline fails G14 acceptance; both said in the text), triggers, method proposals, and which declined | seconds |
| `drift` | `facility_id`; optional `baseline` `[start, end]`, `current`, `families`, `equips` | drift findings and per-equipment verdicts against a known-good baseline window (fitted for the call, never saved) or the run config's frozen baselines (read, never written), and the equipment not evaluated | long-running |
| `dataset_search` | optional `query`, `kind`, `labeled`, `licence`, `limit`, `offset` | catalog metadata: id, title, kind, licence, access, commercial use, fault labels, equipment, download size, what it is good for | cheap |
| `dataset_entry` | `dataset_id` | one catalog entry's metadata: summary, publisher, citation, DOIs, licence and access reason, subsets, suggested rules, known issues and data issues | cheap |
| `rule_params` | optional `rules`, `query`, `detail` | the rules and opt-in drift detectors (`run_with`: `rule_run` for a rule, `drift` for a detector, which `rule_run` refuses), the equipment families each is written for, and their tunable parameters with defaults, units, ranges and (with `detail`) basis and calibration advice | cheap |
| `check_citations` | `text`; optional `facts`, `per_sentence`, `detail` | the counts per verdict (`supported`, `unknown_citation`, `forgotten_citation`, `uncited`, `unsupported_number`, `unsupported_claim`) of a drafted text's sentences against the facts this server issued to the session (see below), the sentences that fail, each with `why`, and the `provenance` of the facts (`issued` or `unverified`); with `detail`, every sentence and `verified_text`: the draft with the unsupported sentences removed, its lines, headings, list markers and blank lines kept, opening with a `NOTICE:` line when a removed sentence gave a verdict, a negation or a caveat. See [Where a cited fact comes from](#where-a-cited-fact-comes-from) for what `supported` means | cheap |

**Which config a run uses.** `rule_run`, `finding_detail`, `rcx_report` and `drift` use the
caller's run config when it names the facility -- its rules, parameters, mapping, topology and
report options. `ctx.config` is one config path or a tuple of them, one per facility (0.111,
#296). `ctx.config_snapshot`, when set (`camber mcp` sets it), holds `(path, config)` pairs as
loaded and checked at startup, and the calls read those instead of the files (#308). Without one, a facility ingested from a catalog dataset (`ds-...`) runs with that
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
logger) is compared the way `rcx_report` compares it in `sensor_trust`, `rule_run` and
`finding_detail` too (0.111, #313). `sensor_trust` lists every comparison under `references`
(a passing one too, so a reader can cite that a sensor was checked): `bias` is the median of
the point minus the reference over their paired samples, so a positive bias means the point
reads high. A comparison at warn or fault makes its point `suspect` (flag `reference_warn` /
`reference_fault`) whatever its trust score, since an offset has no shape in the trend; the
score and `below_floor` are unchanged, so the check is not declined. Instead every finding
whose rule reads that point on that equipment carries `conditional_on` (the comparison) in
`rule_run` and `finding_detail`, its fact says "conditional on sensor_drift:supply_air_temp
warn ...", and a notice says to treat it as a sensor problem first. The notice counts the
findings on the page returned as "listed" and names the conditional ones on other pages or below
`min_severity` apart; `rule_run`'s fact `C1@rule_run` names those (0.111, #320), so the notice's
claim can be cited. In `rcx_report` such an
issue names it in its fact the same way, and the sensor issue ranks just above the first issue
that is conditional on it, whatever their severities. A reference that cannot be compared (its
equipment has no data for the role, say) is named in a notice of `sensor_trust`, `rule_run` and
`rcx_report` alike, since the findings on that point were not checked against it (0.111, #320).

**The trust floor and `sensor_trust` read one score** (0.111, #311). A rule's input is scored
on its equipment's whole run frame -- every role at the run config's resample, over the run's
window, with the shared points merged -- by the same function that scores `sensor_trust`'s
`rules` view, which reads the frames `rule_run` reads. So a check is declined exactly when
`sensor_trust` (same window) puts one of its gated inputs `below_floor` -- a required input, or
a present input of a group the rule needs one of -- and the decline names each input the rule
reads that is below the floor, optional ones included, with that score ("declined -- untrusted
input(s): co2 (trust 0.14), oa_airflow (trust 0.08)"; 0.111, #318). An optional input below the
floor does not decline a check on its own: the rule runs without trusting it, and
`sensor_trust` shows its score. The exception is an input that is optional only because the rule
then returns no verdict, which the check is judged on whenever it is there (0.111, #320): the
outdoor-air signal of `dcv_verification` (OA flow, else the OA damper), the OAT of
`outdoor_air_fraction`, the wet-bulb source of `condenser_water_reset` and
`cooling_tower_approach` (a wet-bulb point, else the OAT and RH), and the fan signal of
`night_weekend_setback` (the status, else the speed) gate the check like a required input. A trend logged in
bursts covers only its bursts on that grid, so it can be below the floor although every stored
sample is sound.

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
and a half intervals. `share` is of the `judged_samples`: the samples the rule itself judged
(its own mask: fan-on, occupied, running, both valves shut ...), never the whole window, and
`judged_basis` names them ("fan-on samples"), so it is the share the `rule_run` summary states
(#310). A finding that judges named conditions (one per coil, per G36 fault condition, per
failure mode) has the union of their flagged samples in `violations` and lists each condition
in `conditions` with its own judged samples; each one is stated as a fact, and those are the
shares its summary states. A drift finding id carries its family (`ahu:coil_valve_drift@AHU-1`).

**Catalog metadata is public.** `dataset_search` and `dataset_entry` list research-only entries
too, each with its licence, `access` and a notice (and a banner) that this server refuses any
facility ingested from it. Nothing is fetched or ingested: that is `camber datasets`, run by a
person.

## Built-in prompts

Each prompt renders one user message naming the capabilities to call and in what order, asks
for every sentence to end with the fact ids it rests on, forbids a number no cited fact carries,
says a declined check or a refused call is not a pass, and ends by calling `check_citations` on
the draft (without facts: the server looks the cited ids up itself, 0.112). Argument values are
reduced to one short line.

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
   error's `data["errors"]` lists each problem with its JSON pointer). A whole-number float
   where the schema asks for an integer (`5.0`) passes, as JSON Schema 2020-12 says, and the
   handler gets it as the `int` (0.111, #308).
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
   leaves such facilities out and says how many it withheld; since 0.112 (#333) its notice names
   them (ids only) and says the id is right but the data's licence forbids this use.

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

   The data carries its own mark too (0.112, #319): `camber datasets ingest` writes the dataset
   id, licence and a research-only flag into every part file, and the handler runs in a serving
   scope in which the store refuses a research-only part whatever facility, directory or store
   it sits in. A facility holding one is `refused_licence` before the handler runs (the
   `facilities` listing withholds it), and a read the handler makes refuses it again. A facility
   not all of whose files could be checked for the mark (a dangling symlink, an unreadable
   directory, a symlink loop or files nested too deep, #334) is `refused_licence` with
   `data["reason"] == "licence_unverified"` and counted in `withheld_unverified`. See
   [Provenance in the part files](DATASETS.md#provenance-in-the-part-files).
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
   (0.111, #291). With a `ctx.ledger`, the result's facts (as returned, after redaction) are
   then recorded there (0.112, #294; see below).
8. **Text from the data** (0.111, #291). Facility, equipment and point names, classes, units and
   metadata come from the data, so they can never pass for the tool's own words. A result's
   `text`, every fact's `text`, every banner and every error message are one line: newlines and
   tabs become spaces, and control characters (C0 / C1: ESC sequences, BEL, NUL), bidi overrides
   and isolates, zero-width and other invisible characters are removed. A line that starts with
   `NOTICE:` in the MCP summary is therefore always a banner, and a text that would itself start
   with `NOTICE` reads `Summary: NOTICE ...`. A handler names a data value with
   `camber.capabilities._sanitize.name()`, which quotes it (`equipment "AHU-2"`), writes a
   fact-id bracket inside it as `(F1@rule_run)`, defangs markup (0.111, #307: `<` and `>` read
   `‹` and `›`, a markdown link `[x](url)` reads `[x] (url)`, and a URL `https[:]//...` or
   `www[.]...`, so no client renders a tag or a link) and cuts it at 100 characters (an ellipsis and
   six hex digits of a hash keep two cut names apart); a quoted value is one word to the citation
   check, so a sentence break or a number inside a name is never "removed". In `text`, only the
   handler's own fact ids are citations: any other `[F1@x]` reads `(F1@x)`, and a fact's `text`
   holds no citation at all. `data` keeps the stored values exactly (so an id can be passed
   back), except that any string in `data` or `facts` longer than 2,000 characters is cut the
   same way, so one huge name does not make a whole facility `too_large`; a result with a cut
   value has `truncated` set and a notice saying how many were cut (0.111, #320). A capability's
   own output named in `Capability.own_output` is never cut: `check_citations` returns the
   caller's `verified_text` and sentences whole. Schema `pattern`s use
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
   allowed one) before and after it runs; it waits up to a second for a swap in progress. A
   removal, and a first ingest, keep the mark until the facility's registry entry is gone or
   written too, and a mark left by a writer that was killed (its process gone, or its process id
   since reused by another process) blocks no call; the next writer to take the store lock
   clears it (0.111, #317). So does a malformed mark, among them one whose process id is not 1
   to 10 ASCII digits (0.112, #322). The exception is a writer killed between the two renames of its
   swap: until the next writer to take the store lock finishes that swap, the facility's calls
   stay `unavailable` with `"retryable": true` (0.111, #321). Its
   `data` is `{"retryable": bool}`, plus `"facility_id"` when the read was of one facility the
   caller may see: `retryable` is `true` for a file that went missing or a concurrent replace
   (retry the call) and `false` for a damaged or unreadable one (a
   retry will not help until it is repaired or the facility re-ingested). The message names no
   path and no facility outside the allowlist. The store's facility registry is read once per
   call, and a read that fails is tried again for a moment (0.112, #324): a registry that keeps
   failing while it changes (a writer replacing it; on a Docker Desktop bind mount a rename shows
   the file as briefly missing; or broken again before the retries end, #329: the file or its
   directory changed between the reads), or fails with an I/O error, is `unavailable` with
   `{"retryable": true, "reason": "registry_unreadable"}` -- never a run without the facility's
   config template and never `refused_licence`. One that stays missing or damaged is still
   refused (#290), and so is one that stays a directory, unreadable (no read permission), a
   symlink loop or JSON nested too deep to parse (#329). One facility's damaged file does not affect
   another facility's calls, and the `facilities` listing still lists it, with
   `"unavailable": {"retryable": ..., "reason": "unreadable"}`, a null `start` / `end` and zero
   counts. `facilities` checks each facility's fence on its own (0.112, #322): a facility a writer
   is replacing, or one a killed writer left mid-swap, is listed with
   `"unavailable": {"retryable": true, "reason": "being_replaced"}` and a banner saying to retry
   later, while the other facilities are listed as usual; its own calls stay `unavailable`. A call reads only the
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
   A name from the data (a quoted value, and the stored ids an error's `data` lists) is not a
   path of this machine, so only the known roots come out of it, never the pattern (0.111,
   #307): a Niagara ORD such as `"/Drivers/BacnetNetwork/AHU_1"` or `"slot:/Config/AHU-6"`
   reads as it is, as in the facts. A value the caller sent, echoed in an error, is redacted
   like the tool's own words, so an error never tells the caller which paths exist.

## Where a cited fact comes from

0.112 (#294). A **fact ledger** (`camber.capabilities.FactLedger`) holds the facts a server issued
to one client session. Give a session's calls the same ledger in `CallContext(ledger=...)` and
`Registry.call` records every fact of every result in it; `check_citations` then resolves each
`[id]` the text cites from the ledger, never from the caller:

- an id the ledger holds gives the fact exactly as the server issued it;
- an id it never recorded -- from another server, another config or store, an earlier session
  (a restart), or made up, a recomputed token included -- is `unknown_citation`, never support;
- an id it issued but has since dropped (the ledger is bounded) is `forgotten_citation`: run the
  tool that gave it again and cite the new id;
- `facts` is optional; every fact passed must be one the session was issued and equal to it
  (the same keys and values; `1` and `1.0` are equal). One that is not -- an altered value, an
  invented fact, a fact about a facility this caller may not see (the ledger never holds one) --
  refuses the whole call with `invalid_params` (`data`: the ids, under `not_issued`,
  `forgotten` and `changed`), so a doctored fact can never be read as support;
- the result's `provenance` is `"issued"`.

With no ledger (`ctx.ledger` is `None`, the default for direct Python use) `check_citations`
needs `facts` and takes them as given, as before 0.112: the result says `"provenance":
"unverified"`. Such a verdict says only that the text agrees with the facts it was handed.

The ledger is bounded: it keeps the most recently issued or cited facts up to 8 MB of their
compact JSON (`FactLedger(max_bytes=...)`, some tens of thousands of facts) and forgets the oldest
beyond that; it still knows the 100,000 most recently forgotten ids as forgotten
(`max_forgotten`), and older ones read as never issued. A capability that reads the ledger
names the ids a call needs (`Capability.cites`), and `Registry.ledger_view(name, arguments,
ledger)` returns just those as a small ledger, which a transport that runs calls elsewhere puts
in the call's context instead of the whole one (`camber mcp` sends it to the worker process with
the call, and records the result's facts in the session's ledger, kept in the server process,
itself). A server with several client sessions (the coming A2A server) keeps one ledger per
session.

**What "supported" means.** The ledger settles where a cited fact came from; the match below
decides whether the sentence says what that fact says. `check_citations` (and
`camber.agent.verify.check`, which `camber explain`, `camber ask`, the
RCx report's prose and every result's own summary go through) matches each sentence against
the facts it cites (0.112, #294). It is precise rather than clever, and it fails closed: what it
cannot match is not supported. **Supported means matched, not proven true.**

- **Every region is judged** (0112-cite10). The text is folded first: NFKC (superscripts,
  subscripts and fractions kept), every hyphen and minus sign (U+2010, U+2011, U+2012, U+2212,
  U+FE63, U+FF0D) as `-`, and a dash between a letter and a digit as `-` ("AHU–1" is AHU-1), in
  citation ids too. An HTML comment's words are judged like prose, and a comment whose sentences
  all fail is removed with them. An HTML tag's shown attribute values (`title`, `alt`, `aria-*`,
  `data-*`, `summary`, `label`, `placeholder`, `content`, `value`) are read with the sentence
  the tag is in; an address, a style or a class is not. A table's header row names its columns,
  also with a figure in it; a header that states something (a verb, verdict, negation, figure,
  or a unit's, rule's or point's name) is judged as a claim with its rows' citations, and each
  cell is read with its column's header: "yes" under "Fails X" in AHU-1's row is "AHU-1 fails
  X", and "no" is "AHU-1: not fails X", the header's claim negated for that unit, supported only
  when a cited fact about AHU-1 states the negation (a "no" the checker cannot read fails
  closed).
- **Citations.** `[F1@x]`, or several in one bracket, `[F1@x, F2@y]` or `[F1@x; F2@y]`. A
  citation in an HTML tag's attribute or a link's target cites nothing. With `per_sentence`
  (the default) a sentence must cite a fact itself (`uncited` otherwise) and is matched against
  the facts it cites; without it, its figures may match any fact the text cites, and only a
  sentence that cites a fact itself has its claims checked.
- **Figures** (`unsupported_number`). Every number, written in digits or in words ("forty-six",
  "half", "one" as a count), must be one a cited fact states with the same sign (`-45%` is not
  `45%`), the same unit when the sentence gives one (`%`, `°F`, `kWh`, `therms`, hours, days,
  percentage points, `$`, ...), the same counted thing when the sentence names one (hours,
  samples, points, rules, faults, ...; "units" are equipment; for a percentage, what it is
  "of"), and the same role: a figure the fact gives as a threshold, limit, target, setpoint,
  allowance or tier (`valve > 5%`, `OAT < 60°F`, `above 20% min`, `target 57.1°F`, `the 30%
  daily CV(RMSE) tier`, `fault_pct = 20.0`) is not a result, and a result is not one: "the
  target is 55.0°F", "the rule allows 3.4F" or "a CV(RMSE) of 30%" against a measured figure
  fails (0.112, #328). A fact's figures are those its text states, outside the quoted names in
  it (a number in a name from the data is not a figure), and its numeric `value` with its `unit`
  when it has one; never the other numbers of its data (a finding's metrics, a rule's
  parameters). In a fact that lists figures, a bare figure takes the unit, share or counted thing
  of its neighbour (`0.83 vs expected 0.35 in. w.c.`, `HW-leak 86% / CHW-leak 0% of samples`,
  `11 points: 9 trusted, 2 suspect`), and each keeps the words next to it (0.112, #332): a
  sentence whose words before a figure (else after it) name only what another figure of the
  same list is ("the cooling valve leaked on 86%", "CHW-leak was 86%", "0% ... showed a heating
  leak" against `HW-leak 86% / CHW-leak 0%`; "2 points are trusted" against `9 trusted, 2
  suspect`) fails. **Labels of a closed family** are read as such (0.112, #336): the side
  (heating: heating, heat, HW, hot water, HHW, reheat, preheat, steam, boiler; cooling: cooling,
  cool, CHW, chilled water, DX, refrigerant; "fan heat" is neither), the trust class (trusted,
  suspect, untrusted) and the G36 fault condition (FC10, FC11, ...). A figure's label of a
  family, the nearest in its own words or, past a parenthesis or a slash, in its list, must be
  the label of the fact's figure, and where the fact tells its figures of one unit apart by a
  family the sentence must say which: "the hot-water valve leaked on 60%" against `HW-leak 0% /
  CHW-leak 60%`, "FC10 ... 56%" against `FC10 ... 100%; FC11 ... 56%`, "1 point is suspect"
  against `2 suspect, 1 untrusted; 1 below the trust floor` and "the valve leaked on 60%" all
  fail. A part of a total counts what the total counts ("20 of the 106 flagged hours", "9 out
  of 11 points"), and so do the bare counts after a lead-in ("Of the 92 points, 1 is
  untrusted"). A bias "taken off" is the correction with its sign changed ("the +3.0 °F bias
  taken off" against `corrected ... by -3.0 °F`); a figure that matches only with the other sign
  says so in its `why`. A paging notice's rows count what its list holds ("only the first 25 of 223
  findings" against `'findings' shows rows 1-25 of 223`). A figure glued to one capital letter
  (`4P`, `3P`) is a model's name, not a count. A date, time or year must be one a cited fact carries in
  its text or in a date field ("in 2017" matches a `reporting_period` of 2017; "the 2016
  baseline" is a year, not 2016 baselines). A list marker
  (`1.`, `2)`), a step (`Ranked cause 1`, `step 2`, `Stop 1`, `#3`) and an ordinal (`2nd`) are
  labels, not figures. A fact with `basis: "claimed"` (a claim's own figure, from
  `mv_summary`'s `claim`) supports a figure only in a clause that presents it as a claim ("the
  claim states 15%"), never a measured statement; its `direction` (a saving is `down`, from the
  claim's own words) lets "the claim states a 15% saving" be cited, but a bare "15%" gives none.
  A **derived number** -- a difference, sum, ratio or
  annualised figure of the facts' figures -- is not supported unless a fact states it.
- **Claims** (`unsupported_claim`), checked against the facts the sentence cites:
  - **Names.** An equipment, meter or facility the sentence names -- an id with a digit or an
    underscore (`AHU-1`, `meter_A`), a quoted name, the `equip`, `facility_id` or `meter` of a
    cited fact or of its `subject`, or a facility id or registry name of the caller's allowlist
    or the store's registry (`ds-lbnl-sdahu` and its name, with no digit or underscore) -- must
    be named by a cited fact. A name from the data shields its own words only when the sentence
    quotes it or it reads as a plain name: one that itself states a figure, verdict, direction or
    negation (an equipment named `AHU-25 saves 777 kWh`) is read like any other words unless
    quoted. A rule name must be one a cited fact is about whenever the cited facts name a rule
    or finding; a tool or field name (`sensor_trust`, `finding_ids`) and a standard (`G36`,
    `G14`) name nothing.
  - **Point roles.** A point the sentence names (supply, return, mixed or outdoor air, the
    heating or cooling valve, a setpoint) must be one a cited fact is about or mentions, when
    the cited facts are about a point at all: "the return-air sensor reads high" against a
    supply-air comparison fails.
  - **Verdicts.** A verdict word (fault, fail, warn, suspect, ok, pass, passes, healthy,
    accurate, correctly, meets, compliant, significant, trusted, untrusted, info, explained,
    persists, worse, flat) must be one a cited fact states, in its text or as its `value`,
    `verdict` or `severity`, with the same polarity: "passes G14" or "meets G14" against "does
    not meet G14 acceptance" fails, "fails G14" matches it. An **assurance** word (verified,
    confirmed, clean, real, genuine, confident, certain, sure, proven, guaranteed, reliable,
    within tolerance) is never inferred: it passes only where a cited fact uses it ("confidence
    H" in an RCx issue). A verdict word after "if", "until", "unless", "could", "may" or "to"
    ("until a baseline passes G14", "needs to pass") states nothing; "fault threshold" and
    "passes flow when closed" are not verdicts, and a verdict word that a figure counts ("1
    fault", "0 info") is matched with that figure. A finding **re-judged** with a sensor's bias
    taken off (`finding_detail`'s `R1`) supports only its new verdict: its first one ("`(fault)
    re-judged ...: ok`") names the finding ("the leaking_valve fault on AHU-1 is explained") and
    supports no "is still a fault", "stands", "remains valid" or "still holds" (0.112, #332,
    #336). "The least trusted point" is a rank (below), not the verdict "trusted". A word that **denies** what it frames (false,
    untrue, incorrect, wrong, mistaken, inaccurate, "not true"), like an assurance word, passes
    only where a cited fact uses it: "it is false that ..." and "..., but that is wrong" fail.
    A **cause or an effect** ("accounts for", "due to", "because of", "caused by", "responsible
    for", "about the same size as", "consistent with", "will clear", "will fix", "would
    resolve", ...) is the verdict "explained" (its negation "persists"), even when hedged: it
    needs a cited fact that states the explanation (`R1`'s "explained by the sensor offset").
    A cause named after its effect ("declined because of the untrusted input", "due to",
    "caused by", "attributable to", "results from") is also supported by a cited fact that
    states that cause of that effect with "because", "due to", "caused by" or "explained by" in
    its words ("1 check(s) declined because an input scored below the trust floor 0.5 (...
    untrusted input(s): ...)"); a counted thing may stand for the cause ("because of that
    sensor") (#336).
  - **Directions.** A direction word (rose, increased, fell, decreased, saved, avoided, reduced,
    wastes, ...) must be one a cited fact uses (or its `direction`), except after "would",
    "could" and the like; "fall on 2025-05-14" and "falls below the trust floor" are not one.
    A saving's direction follows its sign (#339), in the fact and in the sentence: "avoided
    energy -21,825 (-3.1%)" is an increase, so "reduced its energy use" and "saved energy" fail
    against it and "increased its energy use", "did not save energy", "used more energy than its
    baseline", "a negative avoided energy" and "avoided energy -21,825" pass; "this saving is
    ..." naming the result is read as the fact's own saving, whatever its sign. Since #340 the
    other saving words follow the sign too: "shows a saving" (a saving shown, achieved or
    delivered), "cut", "lowered" or "trimmed its energy use", "used" or "consumed less energy"
    are a decrease, "used more energy" an increase, and "used 15.6% more energy than its
    baseline" (or "..., by 15.8% and 15.6%") gives avoided energy -15.6%. Since #341 "came in
    under / ran below its baseline", "beat its baseline", "its energy use went down" or
    "declined", "a savings success" and "consumed less" are a decrease, "above its baseline",
    "went up" and "consumed more" an increase, and "has a savings result" names the result with
    no direction; "reads warm" / "reads cold" is a reading's direction like "reads high" / "low". "High" / "low" and
    "above" / "below" must not be the opposite of what the cited facts say; "above" or "below"
    ending a phrase ("the figures above", "see below") is a place in the text (#336). A
    reading's direction ("reads high", "read low", "runs high", "biased low") must be one a
    cited fact states, in its words outside a negation ("the point reads high against it") or
    as a signed bias ("biased +3.0" reads high): "the oa_damper reads high" against a trust fact
    that gives no direction fails (#340).
  - **Negations, both ways.** A negation the sentence states ("did not leak", "never moves",
    "without", "no <thing>") needs a cited fact that negates the same thing: a fact's "no valve
    leak" supports "the valve did not leak", while "drift not evaluated" supports no other
    negation, and "CHW-leak 0%" says nothing of the heating valve. And a sentence must not
    state what a cited fact negates: "the valve leaks" against "no valve leak", "the setpoint
    moves" against "never moves", "on the same days" against "on other days". A closing "not"
    ("<a fact's words> -- not") denies the sentence and is never supported. A fact's negation of
    something it is not about ("measured_fan_heat_f not set" in a rule's parameters) supports
    only a sentence that names it or says all its words: "no fan heat was set" fails (#336).
    A leak, or any negated thing, said of the equipment a cited fact negates it of, with no
    other part named, states the opposite too (#340): "AHU-2 leaks", "AHU-2 is leaking", "Both
    AHUs show a leak" against AHU-2's "no valve leak" fail ("AHU-2's damper leaks" names another
    part and is not read). Where the sentence names equipment, a cited fact's negation of
    another unit is not read against it, and only the facts about the same unit can say it
    affirmatively: AHU-1's leak fault does not support "AHU-2 leaks". A fact that a check
    was skipped, declined or had insufficient data says it "was not judged", so that sentence
    can be cited; "not a pass" cannot.
  - **Zeros, each with its own clause** (0.112, #332). A zero ("0 <things>", "no <things>",
    "none of <them>", "0 of the 11 <things>") counts a thing and says something of it; a fact's
    zero does too, within its own list item ("`0 rule(s)) skipped for missing inputs`"). The
    sentence's zero needs a cited fact's zero of the same thing whose words before that thing
    and whose first verb are in the sentence ("0 rules were skipped"), whose list item holds the
    sentence's first verb, and whose counted things the sentence does not deny ("none of the 8
    hours was flagged" against "none of the 8 flagged hours falls in the window"). So "no
    faults were found" against `1 fault, ..., 0 rule(s) skipped` and "0 rules returned a fault"
    against `14 fault, 0 warn` fail, and "0 rules returned a warn" passes. "No fault" also
    passes against a finding whose `value` is ok. "None of the baselines meets G14", a zero
    across several facts, needs each cited fact to negate it ("does not meet"). A reading of
    zero ("bias +0.0") is no zero count. "Not a single <thing>", "not one of", "zero
    <things>", "neither ... nor", "nothing", "<thing>-free" and "free of <things>" are zeros
    too (#336), and so are a zero after a colon that counts its label's thing ("Faults on X:
    0", "Number of suspect points: 0"), "<things> were not found" (or not detected, reported,
    seen, flagged), "there aren't any <things>", "fault free" with no hyphen, a trust class
    "-free" or "free of suspect points" (its points), and "neither suspect nor untrusted points"
    (each label with the noun, #338). A trust class or a verdict alone before a colon, a dash
    or in a table row counts its figure too (#339): "Suspect: 0" and "| Suspect | 0 |" are "0
    suspect points", "Trusted: 24" is "24 trusted points", "Passed: 5" is "5 ok", "Failed: 0"
    is "0 fault", "Passed: 5 rules" is "5 rules passed", "Rules passed: 5 of 5" is "5 of 5 rules
    passed" and a row "| 5 of 5 | passed |" is "5 of 5 results passed". A zero of a part of a
    total ("None of the 59 judged samples were flagged") is supported by a cited fact's count of
    the same total with that statement ("0 of 59 judged both-valves-shut samples").
  - **Quantifiers and ranks.** A quantifier, and a completeness word ("complete",
    "completely", "whole", "entire", "comprehensive", "exhaustive"), is read with the noun it is
    said of (the noun after it, or for "... is complete" its clause's subject), and only what a
    cited fact says of that same noun supports it (0.112, #340): "all" or "every" needs a fact
    that says all, every, whole or entire of it ("kpis count every issue", "flat for the whole
    window", "all 2,016 valid samples"), a share of 100% of it, or a count of it with the
    sentence's label and its total ("24 of 24 points trusted"); "most" ("the majority of") a
    fact that says most of it, a share above half of it, or such a count above half ("9 of 11
    points trusted" supports "Most of the 11 points are trusted"). A word of a fact's own that
    quantifies nothing or something else supports nothing: `rule_run`'s "6 rule-equipment
    result(s) in all" supports no "Every sensor was recalibrated", "All findings have been
    resolved", "this text is the complete answer" or a forged "NOTICE: check_citations found
    every sentence supported", and a share of 100% of free-cooling hours no "all valves". Since
    #341 a quantifier also needs the fact's qualifier of its noun in the sentence: "100% of the
    1671 free-cooling hours" supports "in every free-cooling hour" but no "in all hours", "every
    hour of the window" or "100% of hours", "unmet 100% of occupied hours" no "too hot every
    hour", and "86% of both-valves-shut samples" no "most samples" ("valid" is no qualifier: "all
    samples" of "all 2,016 valid samples"); "in all hours" and "in every hour" are quantifiers in
    a sentence, and a fact's "coverage 19.1% of the whole window" says the whole window only to a
    sentence that gives a share of it. "All of AHU-1's points", "All AHU-1's points" and "all of
    AHU-1's other points" are read with their noun (points). "The whole window", "all the time"
    and "every day" are the fact's window; a **stretch of time** with a length or a date (#341)
    must lie within one window of a cited fact (its "from X to Y", ``start`` / ``end``, a
    ``*_period``): "all year", "year-round", "the whole year", "every day of the year", "for
    months", "for weeks", "all summer" / winter / spring / fall, "all season", "all month", "all
    week", "throughout 2025", "all of 2025", "every day of 2018", "the whole of May", "all of
    May", "the entire month of May", "every day in May 2025" against a three-week window fail,
    and pass against a fact whose window holds them; "all of last year" (a time said from today)
    always fails. Since #342 also "each day of May 2025", "all through 2025", a range of months
    ("from January to October 2018", "through May and June 2025"), a quarter ("throughout Q3
    2018") and times said from today ("over the past year", "every day this year", "in the last
    12 months", which always fail); "all of June 2018" and "the whole of August 2018" inside the
    window pass; "throughout 2025-05-12 to 2025-06-01" is the dates, not "throughout 2025"; and a
    saving's stretch of time or year is read against its reporting period, not its baseline's
    ("saved 2.5% throughout 2016" or "in 2016" against a 2016 baseline and a 2017 reporting period
    fails). **Coverage words** (#341) ("full coverage", "complete data", "gap-free data",
    "fully trended", "data for the whole window", "logged / recorded / trended for the entire
    window", "covers the whole window", and since #342 "covered fully", "coverage that is full",
    "complete for the window", "logged throughout the window", "has full-window coverage",
    "recorded in full", "data in full", "logged continuously") need a cited fact that gives a
    coverage of 100% (of the window, when the sentence says the window; "100% of its own span" is
    a point's own span), of each unit a set names ("LOGGER-1 and AHU-1 have full coverage"
    against 19.1% and 100.0% fails, #342); "gap-free" of a unit a cited fact gives 100.0% of the
    window passes (#342). Since #342 "at all times", "around the clock", "day and night",
    "nonstop", "constantly" and "continuously" are "all the time", "each hour" is "every hour",
    and "all occupied and unoccupied hours" is each with its own "all" (a qualifier with its
    opposite holds no fact's "100% of occupied hours"). A quantifier under "whether", "if" or "to"
    ("confirm whether the valves are shut all the time") states nothing. A quantifier said of a
    set of items is the set's (below), "they are all ..." with no noun keeps the reading below,
    and "none" is a zero (above). A **whole count**
    ("all 11 points", "106 of the 106", "every one of the 37") needs a fact that says all of that
    many ("all 2,016 valid samples"), or gives the count again with what the sentence says of
    them ("14 points ...; 14 point(s) are below 90%"): a fact that gives a part of them (`9
    trusted, 2 suspect`, `20 of the 106`) or none of them does not (0.112, #332). "Issue 3"
    must be a rank a cited fact gives, when a cited fact gives ranks; a rank said as one (the
    second issue, issue #1, ranked first, rank 2) needs a cited fact that gives a rank, and a
    rank word (worst, top, best, biggest, largest, most severe) needs a cited fact that is rank
    1 or uses that word of the same thing ("worst: HW (heating) coil" supports "the worst coil
    is the heating coil", not "the worst issue"). "Least trusted" is a rank word too, which
    `sensor_trust`'s point facts support with their place in its least-trusted-first list
    (`rank`); "best" and "most trusted" are never supported by a rank 1 (#336).
  - **Counted things** (0.112, #336). What a sentence says of a counted thing (points, hours,
    rules, faults, ...) as a whole, none or a part must agree with what the cited facts say of
    the same thing. The quantifier may stand before the noun ("all", "all of the", "every one
    of", "each of", "both", "N of the N", "N out of N", "none of", "no", "not a single", "not
    one of", "zero", "neither", "some", "several", "most", "k of the N"), after it ("the 11
    points are all trusted", "the 8 flagged hours all fall", ", all of them,"), or be left out:
    a counted thing opening its clause with "the" ("The 11 points are trusted", "The flagged
    hours fall in the window") is all of them, a bare count ("8 flagged hours fall ...") that
    many, and "Of the 11 points, all are ..." / "none is ..." the lead-in's. It is matched
    against (a) the facts' count of its label: "9 trusted, 2 suspect, 0 untrusted" of "11
    points" makes "The 11 points are trusted", "9 out of 11 points are suspect" and "Not a single
    point is suspect" fail; and a verdict's count anywhere in a cited fact ("14 fault, 0 warn, 2
    info and 19 ok") makes "No rule returned a fault or a warn", "Not a single fault was found",
    "<facility> is fault-free" and "No rule passed" fail; and against (b) a cited fact's count
    of things with the same statement -- the fact's verb, or two of its words, and no word it
    does not use: "None of the 8 flagged hours ... falls in the window" makes "The 8 flagged
    hours fall in the window", "8 out of 8 flagged hours ...", "Some of the 8 ..." and "The
    flagged hours are inside the window" fail, "20 of the 106 ..." makes "The 106 flagged hours
    fall in the window" fail, and "outside" (or "beyond") the window is the rest of them ("The 8
    flagged hours fall outside the reference window" passes). One thing is not two labels of
    one family: "Both suspect points are trusted" fails. Since #338: a verdict given to a whole
    set ("The 5 rules passed", "The 7 equipment are healthy", "the rules all returned a fault",
    or a bare "5 rules passed" where a cited fact runs 5 rules) needs every result of the cited
    facts' verdict counts to have it, so "The 3 rules returned a fault" against `1 fault, 1 warn,
    0 info and 1 ok` fails (a figure in what it says, "returned 14 faults", is checked as a
    figure). A fact's count with no words of its own takes its clause's verb ("The rule flagged 8
    hours (32 of 37 judged samples, 86.5%)": 32 of 37 were flagged; "mechanical cooling ran 42%
    of the 1672 free-cooling hours"), so "The 37 judged samples were flagged", "The judged
    samples were all flagged" and "The 1672 free-cooling hours ran mechanical cooling" fail; a
    sentence with that verb, of the same things, is matched with it even when it adds the
    fact's other words ("flagged as a heating leak" against "heating leak: the rule flagged
    ..."). A share of them is compared as a share: "86.5% of the judged samples", "at least
    half", "fewer than half", "more than half", "half of" (and "a quarter"); "the majority of"
    and "the bulk of" are "most of". "There were 8 flagged hours in the window" says 8 of them
    are in it and "there are flagged hours in the window" some (an existential, fronted or
    not). A bare count after "and" that a verb follows counts what the count before it counts
    ("1 point is below the trust floor and 1 is suspect", "one is untrusted and one is
    suspect"), and so does "none" after "and" ("3 points are suspect and none is trusted");
    "the rest" after a count is the fact's total less that count ("21 are trusted and the rest
    are untrusted" against `21 trusted, 3 suspect, 0 untrusted` of `24 points` fails, #339); "a
    single" and "a lone" are one; "over 9" (opening its clause or after a verb, not "over 366
    days"), "well over 9", "just over 9" and "upwards of 9" are "more than 9", which a fact's 9
    does not support. "The rest are suspect or untrusted" is the rest as both labels together
    (#340). "Of the 6 results, 1 is a fault, 2 are warn" counts results with those verdicts. "No
    suspect point is below the trust floor" says that of the suspect
    points, not that there are none; a count of rules with a verdict ("1 rule returned a fault")
    is read against `rule_run`'s verdict counts only where it ran on one equipment, since its
    verdicts count rule-equipment results, not rules. "All" with no counted thing ("They are all trusted", "all of them", a table cell
    "all trusted") is read with its label: a cited fact must count all of them with it (`17
    trusted` of `17 points`; every verdict count but that one 0), or, with no label, say all.
  - **Sets of items** (0.112, #338, #339). One rule: a sentence that says something of two
    or more items -- equipment (AHU, unit, RTU, VAV, box, chiller, tower, logger, ...), points,
    sensors, valves, dampers or reference comparisons, meters or facilities -- needs, for
    every item, the cited facts about it (one of them, or all of them together) to state that
    same claim of it: its figures, verdicts, directions, negations and point role. A cited fact
    about one item that says otherwise fails the sentence, and the `why` names that item. The
    set may be said with a quantifier or a number ("both", "each", "every", "all", "all 3",
    "the 2", "the pair of", "the two"), as a plural opening its clause with "the" or a
    possessive ("The AHUs fail ...", "The VAV boxes on X are healthy", "X's 7 AHUs are
    healthy"), with a possessive noun ("each AHU's supply_air_temp", "both AHUs' sensors"), as
    "both" or "each" with no noun ("Each fails it", "On both, ...": the items its cited facts
    are about), negated ("neither AHU", "neither of the AHUs", "no AHU", "none of the AHUs",
    "not one of the AHUs", "neither A nor B", "neither A or B", "neither of A and B", "nor A nor B", "Not A, not B:
    neither ...", "no one of A, B and C", "not a single one of A, B and C" (#341): the
    negation must be stated of each, as "A does not ..." would need, so "Neither AHU-1 nor AHU-2
    has a valve leak" fails against AHU-1's leak fault, and "not trusted" is either of the other
    trust classes; "none of A, B and C", "neither of A, B", "not one of A, B and C" too, #340),
    or as names (or "the heating valve" and "the supply-air setpoint") joined by "and", a comma
    alone, "&", "/", "+", ";", "plus", "as well as", "along with", "together with", "in
    addition to", "besides", "alongside", "with", "vs", "and also", "and, likewise,", "and/or",
    "like" ("Like B, A ...", "A (like B) ...", "A, like B, ...", "A -- like B -- ...") or "nor" /
    "or" after "neither", set off by commas, dashes or brackets ("A (and B) ...", "A -- as well
    as B -- ...", "A, and also B, ...", #341), in any position: opening the
    clause, after the verb ("valve leaks were found on A and B"), as a lead-in before a colon, a
    dash ("-", "--", "—") or a table cell ("A and B: leaking_valve fault", "- A, B -- fault",
    "| A, B | fail |"), and "A has no valve leak, and B doesn't either" ("so does B", "nor does
    B", "B as well", "as does B", "B also does", "and likewise B", "the same goes for B"). A
    plural noun or a bare "Both" as a lead-in is a set too ("The AHUs: fault", "| AHUs | fault
    |", "Both: faulted", 'The AHUs\' "supply_air_temp" points: both disagree'), and so is a set
    with a relative clause ("The AHUs, both of which pass X, warn on Y": each item needs both).
    "Most AHUs" and "the majority of the AHUs" need more than half of two or more items so
    stated; "N AHUs" opening a clause needs N of them; "k of the N AHUs" ("One of the two AHUs
    fails") needs N to be the number of items the cited facts are about and exactly k of them so
    stated. Figures are the item's own: a figure in what the set's clause says of it must be a
    cited fact's about that item ("Both meters save 2.5%" against -15.6% on one fails). A figure
    the sentence attaches to a name is that name's, and a cited fact about that name must give
    it, set or not (#340): "at 0.66 on AHU-1 and 0.68 on AHU-2", "2.5% on A and -15.6% on B", "A
    (1.00) and B (0.66)", "VAV-1 100%, VAV-2 81%", "| A | 2.5% |", and "100% of the hours on A,
    B and C" or "0.66 for A and B", which give the figure to every name joined there. A list of
    one figure for each item is matched to the items in order: the order of the names ("A and B
    have 15.8% and 33.6%", "respectively" or not), of the parts by their point role ("the
    heating valve and the supply-air setpoint read 0 % and 55 °F respectively"), of the names
    joined inside its clause ("The oa_damper points on A and B have 0.66 and 0.68
    respectively"), or, for a list said "respectively" of items it does not name ("The two
    suspect points score 0.66 and 1.00 respectively"), the order its citations give them; since #341 a list joined by "/" or "vs" ("A/B
    trust: 0.68/0.66", "A vs B: 100% vs 81%"), a list before the names ("With CV(RMSE) of 33.6% and
    15.8%, A and B ..."), "rank ... respectively", a possessive name owning the figure of its part
    of the clause ("A's trust is 0.68 and B's 0.66", "A's trust (0.68) is higher than B's (0.66)")
    and a list said "respectively" that no set reads, with one cited fact for each figure, in the
    order of its citations ("The flagged spans on D1, D2 and D3 lasted 3, 2.75 and 2.25 hours
    respectively"); each item's direction follows its own figure's sign, so a true list with mixed signs ("A and B
    have avoided energy -15.8% and 2.4%") passes and a swapped one fails. A list said
    "respectively" with no such order, or with another number of figures than items, fails
    closed. Figures listed without "respectively" of items in no order ("Both oa_damper points
    are suspect at 0.66 and 0.68") and a range ("(0.68 to 0.70)") need each item to have one of
    them. A table row is read with its header ("| A | -15.6% |" under "| Meter | Avoided energy
    |" is "A: Avoided energy -15.6% on A"), and a header row that states nothing is kept and not judged (one that
    states something is a claim, above). What the set is said is read with its
    own clause: "Both AHUs warn ..., but only AHU-1 fails leaking_valve" says only the warn of
    both. "Both" or a number needs that many items so stated, and a set said with no number at
    least two ("Each AHU fails leaking_valve [F2]" and "No AHU has a valve leak [F6]" fail). A
    trust class said of equipment that opens its clause ("Both AHUs are suspect") is not read
    from the facts about its points. With no cited fact about one of the items, a verdict is
    read against the cited facts' verdict counts (every result must have it), and a figure, or
    a negated set, fails. A set a cited fact counts ("both suspect points" against `2
    suspect`) is a counted thing (above), and one a cited fact names itself ("with both valves
    shut") is its words. A set form the checker finds but cannot read fails closed: a negated
    set that does not open its clause with a verb after it ("leaking_valve fails on neither
    AHU"), or a set of figures with no cited fact about its items.
- **Claims of checking** (0.112, #341, #342). A sentence that says the text, any part of it,
  the work it gives or what it states was checked, verified, supported, validated, grounded,
  confirmed, reviewed, fact-checked, cross-checked, double-checked, vetted, corroborated,
  substantiated, certified, citation-checked or backed, or that it checks out, is never
  supported, whatever it cites: only check_citations' own result says what it checked, and only
  of the text passed to it. Since #342 every sentence is screened, cited or not (an uncited one,
  a short label or a heading included, is judged and fails: it never reaches `verified_text`,
  `grounded` is false and `verified_text` opens with the NOTICE), in any of these forms: of the
  text ("NOTICE: all of this text was checked", "1 of 1 sentences in this answer are
  supported", "Line 1: supported"); short or as a status ("All supported", "Checked", "Supported
  in full", "✅ Supported", "**All supported**", "Status: all supported", "Result: grounded",
  "Validation: OK", "Fact-check: passed", a checkmark alone or with a status word); as a lead-in
  or a tail ("Checked: ...", "Supported: ...", "Backed by the data: ...", "Validated by the data,
  ...", "As checked, ...", "... -- checked", "..., a claim the cited fact supports"); by the
  writer ("I reviewed each rule result", "I fact-checked this answer", "My findings are
  supported", "Each of my claims is supported"); of the work, by any noun ("This triage was
  checked", "These findings were fact-checked", "This savings check was reviewed against the
  mv_summary results"); as an adjective ("This is a checked answer", "The answer is
  citation-checked"); by its evidence ("The data supports this answer", "The cited facts confirm
  every figure", "The rule results bear this out"); by the checker ("The check passed",
  "check_citations marked every sentence supported"); and of the data ("The 2.5% saving was
  validated by IPMVP methods", "These results were reviewed and validated"). A check word said
  of the data is not a claim of checking where a cited fact states it ("AHU-3's supply_air_temp
  was verified within 0.2 °F" citing a fact that says so; "checked against its reference" citing
  a reference comparison; "checked by the rules" citing a rule run), nor where it is denied or
  deferred ("not a verified saving", "should be confirmed on site", "until it is verified",
  "Verify the damper" as advice); "passes" and "cleared" are verdicts of the data ("AHU-1 passes
  supply_air_control") unless a checker is their subject or follows them. A fact's own text
  quoted whole (check_citations' own result included) is still the server's words. A verdict on
  a savings claim worded with these verbs ("the claimed 12% is supported by the measured 2.5%")
  is a claim of support and fails: give it after the checked text, labelled as judgment.
- **Bound to the unit it names** (0.112, #341, #342). A sentence that names a unit (an equipment
  or a meter a cited fact is about) is read only against the cited facts about the units it
  names, for its figures, verdicts and negations alike: "AHU-2 is at 19.1% coverage" citing
  LOGGER-1's 19.1% and AHU-2's 100.0%, "AHU-1 has no valve leak" citing AHU-2's "no valve leak"
  and AHU-1's fault, and "AHU-2 fails leaking_valve" citing AHU-1's fault and AHU-2's ok all fail.
  A unit's id written another way is that unit's (#342): "AHU 1", "AHU–1", "AHU—1", "AHU - 1",
  "AHU #1", "AHU-01", "AHU1", "Air handler 1" and "air handling unit 1" are "AHU-1" when only one
  cited fact's id has that family and number (the words of a family: air handler or air handling
  unit, rooftop unit, fan coil unit, VAV box, chiller, boiler, cooling tower); a spelling two
  ids could take ("AHU 1" with "AHU-1" and "AHU1" cited) fails closed, and one no cited id has
  ("AHU 3") is a name no cited fact names. A whole site's fact (a rule run's counts) gives a
  unit's verdict or negation nothing unless the sentence names the site; a fact about no unit (a
  notice, a rule's parameters) is still read, but one that lists units ("1 result(s) not listed
  here ...: "AHU-1" supply_air_control (ok)") is about the units it lists (#342). A sentence
  that names one rule reads its verdicts against the cited facts about that rule, a summary
  fact's included: one that lists another rule's results supports none of this rule's ("AHU-1
  passes leaking_valve" citing its leak fault and that summary's supply-air control ok fails,
  #342). A finding's verdict is the one it gives itself: the verdict of the comparison it is
  "conditional on" ("conditional on sensor_drift:supply_air_temp warn") supports no "AHU-1 warns
  on leaking_valve" or "passes supply_air_control with a warn", unless the sentence speaks of
  that condition ("conditional", "rests on", "reference", "sensor", "comparison", #342). A
  sentence that names a point (a point role a cited fact is about) is read against the cited
  facts about that point when they are about two or more (#342): another point's fact gives it
  no verdict, negation, direction or figure ('"oa_damper" never moves' citing the damper's trust
  and the heat valve's flat line fails), and "agrees" / "disagrees" with a reference needs a
  cited fact that states it. A sentence naming one date reads its figures against the dated
  facts that carry that day ("The span on 2025-05-14 lasted 2.25 hours" citing two spans); since
  #342 a sentence naming two or more reads each figure against the facts that carry its own date
  (the nearest date in its clause, or with every date before every figure the date in the same
  place of the list: "On D1 the span lasted 2.25 hours, and on D2 3 hours" with the spans
  swapped fails), and a span or gap said by its place ("The third span lasted 3 hours") is read
  against the fact that numbers it so ("span 3 of 3"). "Only X ...", "X alone", "Just X" need X's
  facts to state it and every other unit's cited facts not to; "X, not Y, ...", "X -- not Y --",
  "X but not Y", "X rather than / instead of / unlike Y", "Unlike Y, X ...", "It is X, not Y, that
  ..." and since #342 "X ..., unlike Y", "X ..., but not Y", "X ... (not Y)", "X ..., as opposed /
  in contrast to Y", "Not Y but X ...", "X's part, not Y's, ..." and "The X part, and not the Y
  one, ..." need X's facts to state it and Y's cited facts not to (with no cited fact about Y it
  fails closed); two clauses about different units ("Y passes X while X fails it", "Whereas Y
  passes, X fails", "A fails X; B passes it", "..., but B ...", and since #342 "A fails X and B
  passes", "A fails X, B passes", "A fails X -- B passes", "A agrees, and B disagrees, with its
  reference", "A fails X, but B does not" read as "B does not fail X", and "A: no valve leak", "A
  -- no valve leak", "No valve leak was found on A" as A's) are each read against their own
  units' facts; when each clause names a unit of its own, clause by clause only. A sentence
  whose clauses each cite their own facts is read clause by clause ("Both loggers are at 19.1%
  [..], while both AHUs are at 100.0% [..]"). "leaking_valve flags A" and "A was flagged by
  leaking_valve" are a fault of A (#342). A verdict said of a whole site as its subject ("The
  site is healthy", "All told, ds-x is ok", "Overall, the plant passes") needs a cited fact that
  counts the site's results, and every verdict it counts to be that verdict (one unit's ok does
  not say the site is healthy); since #342 any verdict, negation or quantifier said of a site --
  as the opening subject or named after "on", "at", "for", "across", "of" or "in", with no unit
  named -- is read only against the cited facts about the whole site: "site-north has no valve
  leak" citing one unit's "no valve leak", and "ds-x meets daily acceptance" citing the M&V
  summary's counts and one meter's "meets", fail.
- **Names no fact of the session gave, and notes** (0112-cite10). A sentence that cites a fact
  and names a unit, site or dataset as a slug no fact of the session named ("irish-ahu is
  licensed ..." when no result named irish-ahu; a slug reads as such a name when one of its
  words is a class word such as AHU, VAV, chiller, meter, logger, site or ds and another is no
  generic word: "site-wide", "AHU-level" and "duct-logger" of a cited "duct logger" are not), or
  a rule no fact of the session named, fails closed. The session's names are those of every
  fact its ledger recorded (forgotten ones too), or of the facts passed. A rule named with a
  run's summary (`F1@rule_run`) or skipped rules must be named by a cited fact. A sentence that
  says a rule's caveat, masked samples or a skipped rule needs a cited fact that says so (a
  `caveat`, `finding_labels` or `skipped_rules` fact, below, or one whose words say it): "The
  rule's own caveat says fan heat can read as a leak" citing the leaking_valve finding fails,
  citing its `V` fact passes. A sentence that cites only caveats, one of which hedges ("can read
  as a heating leak", "could not be ruled out"), must hedge too or say it is the caveat: "AHU-1
  has a heating leak" citing that caveat fails.
- **A site, a count, a spelling, a parameter** (0112-cite10). A site said as the subject after
  a heading's or list's marks or after a leading condition ("## site-a fails X", "If the trends
  are right, site-a fails X") is read like one that opens the sentence, and a figure or a rule
  said of a site ("site-a has economizer_high_limit on 43% of those samples") is read against
  the cited facts about the whole site: one unit's finding supports neither. A count of one
  verdict ("6 warn results") needs that verdict's count where the cited fact counts results by
  verdict ("2 fault, 4 warn"), not its count of results. A unit id spelled another way ("AHU 3")
  is read as an id when any fact of the session names that family, also only in its text, so a
  unit no cited fact is about fails. A sentence naming one parameter of a cited fact's list
  ("warn_pct = 10.0, ..., assumed_min_oa_pct = 20.0") fails with another listed parameter's
  value ("The default warn_pct ... is 20%").
- **Headings and notices.** Every line that states something is judged, a `#` heading, a bold
  line and a short lead-in ending in a colon included (0.112, #328). A heading or a short list
  label that states nothing -- no citation, figure, date, negation, assurance word, verb (a
  verdict verb such as "passed" included) outside a relative clause, or advice -- such as `# AHU-1 triage`, `Findings:`, `## Points
  that never move`, `## Savings claim check` or `1. Sensor offset.` is kept and not judged. A
  `NOTICE:` line (`Notice:` or bold too) repeating the text of a `notice` fact among the facts
  (passed in `facts`, or cited) is kept and not judged; any other `NOTICE:` line is judged like
  a sentence. "in." after a figure or
  before "w.c.", "vs." and "e.g." do not end a sentence, nor does a full stop inside a quoted
  name.
- **A fact's own words.** A fact's whole text quoted word for word and cited with its own id is
  one supported sentence, however many sentences, verdicts, negations or caveats it holds
  ("`"AHU-1" leaking_valve (ok): ... no valve leak ...`", an RCx "Recommended action, ..."), and
  so is a `NOTICE:` line that repeats a notice and cites its `N` id. So are whole sentences of it
  in a row, quoted word for word (the first two of a notice's three, 0.112, #332). The quote must open a line
  (after a list marker or a `NOTICE:` label) or a sentence and run whole up to its citation,
  with nothing after it but a full stop, a space or the line's end. A quote with a word added,
  changed or left out, or citing another fact, is judged like any other sentence, and words
  before it on the line are a sentence of their own.
- **`verified_text`** is the draft with only the failing sentences removed: its lines, list
  markers (a removed first sentence leaves its marker to the next), headings and blank lines
  stay, and a line whose sentences all failed goes. When a removed sentence gave a verdict, a
  negation or a caveat ("only", "conditional", "until", ...), `verified_text` opens with
  "NOTICE: check_citations removed sentences that gave a verdict, a negation or a caveat; this
  text says less than the draft and is not the complete answer." and a banner says so: without
  them the text can read as more certain than the draft. Checking that text again keeps the line
  and does not judge it.

What it does not check: whether a claim is true beyond what the cited facts state, the meaning
of a sentence's words beyond the ones above (a paraphrase with none of them matches, and a word
outside these lists is not read), an equipment name with no digit or underscore that no fact
gives as a subject, a point role outside the families above, which cited fact a figure belongs
to when a sentence cites several about the same unit and names none of their points (two points
of one AHU with neither named, #342) or names no unit, and neither attaches the figure to a name
nor lists it for its items (#340, #341), and the predicate of a whole count beyond the words
above.
Of #341's and #342's rules, these are not read and pass when their figures and names match: a
claim of checking in words outside the lists ("looked over", "went through every line", "matches
the data"), and a checkmark beside a statement ("✅ AHU-1 fails leaking_valve": read as that
statement); a unit named only by a word, with no id ("the AHU", "the logger", "the other AHU"),
or by an id with letters after its number ("VAV1__rogue_sat") written another way, or by a number
in words ("AHU one"), which is not bound to its facts; a point named by a description ("the
damper", "the supply-air sensor"), which is not bound to its point's facts; with "only X", a
unit no cited fact is about (only the cited units are set apart); "X, not Y" read as Y's cited
facts not stating it (not as a fact that negates it of Y); a site said other than as the opening
subject or after "on", "at", "for", "across", "of" or "in" ("ds-x: healthy", "Site status --
ok", a site in other words: "is in good shape"); a verdict said of one rule when the sentence
names two rules (no rule binding then); a property said with a noun that is no verdict, negation
or direction ("AHU-2 is below target with a leak", citing AHU-1's leak); a re-judged finding's
new verdict supports a bare negation ("AHU-1 has no valve leak" citing its re-judged ok); a
stretch of time in other words ("since the spring", "since last year", "since installation",
"year-round since 2016", "over the winter", "this season") and a union of two windows (a stretch
is held by one window); "for weeks" holds for a window of 14 days or more ("for weeks on end"
passes on three weeks); coverage said in other words ("never offline"); "runs cold" / "runs
warm" (only "reads" carries a reading's direction); a reference named by a word ("The duct
logger on AHU-1 reads high"); a paraphrase of a leak ("passing water through a shut valve"); a
list given as "the former ... the latter"; possessives joined with "like" or "as well as" ("A's
coverage, like B's, is 100.0%"); "flat while AHU-2's modulates" (a word outside the lists); the
second of two clauses that names no unit of its own (a pronoun: "... and it has a trust of
0.66") is the first clause's. And these fail closed although they can be true: a sentence that
names one unit and gives an unnamed unit's figure ("lower than the other AHU's 0.68": name
both); "Overall coverage is quoted over the whole window" with no share of it; "Except for B,
all AHUs fail"; "A's part is suspect, but B's is not" (a possessive left with its verb left
out); "The loggers are at 19.1% and the AHUs at 100.0%" with all ids at the end (a second set
with its verb left out: give each clause its own citations); "The two units share ..." with the
figures attached to their names; "most" across two facts ("Most of the 8 savings rest on ..."
citing a count of 5 and a total of 8); a length the facts do not state ("for three weeks", "for
21 days", "for six months", a derived number); "the second half of May" ("half" of no count);
"all winter" on a January-to-December window (a winter spans two years); a spelling of a unit
two cited ids could take; "AHU No. 1" (the sentence ends at "No."); a verdict on a savings claim
worded with a check word ("the claimed 23.8% is supported by the measured saving": judgment,
give it after the checked text).
Of counted things and labels (#336), these pass when their figures and names match and can be
wrong: a claim about every one of a set of items, when each item has a cited fact about it, beyond
the words above ("Every AHU has a new actuator" citing a fact about each AHU: the quantifier
is the set's, its predicate a word outside the lists); a list said "respectively" of groups
("The AHUs and the loggers have 100% and 19.1% respectively": each item needs one of the
figures); a range given for a set ("(100% to 100%)") is not checked to hold each item's figure;
a "respectively" in a later clause that names the set only by a pronoun ("A and B have flat
valves, and their biases are +0.0 and +3.0 respectively"); words outside the lists said of a
set or an item ("Both AHUs drift", "Both heat valves modulate"); a counted thing after its verb, or later in its clause, with no quantifier ("the window
covers the 8 flagged hours" is not read as all 8); a statement about a counted thing in other
words than the cited fact's ("the 8 hours precede the window"); a cause after a bare
"because" ("declined because the input was untrusted"); a label outside the closed sets where
the cited fact gives one figure only; a claim about all of several facts that are not items of
the kinds above ("both baselines", "the zones"). Of sets of items (#338, #339): a set said with
no number ("each AHU", "every unit", "the AHUs", "no AHU") is read as the items the cited facts
are about, so an item that no cited fact is about (a third air handler) is not known to the
check; a plural noun after its verb ("a fault on the AHUs") is not read as a set (names joined
after it are); a set across sentences is not read ("AHU-1 fails it. So does AHU-2.": each
sentence is judged alone, and "both" or "each" with no noun is read against its own sentence's
citations); names joined with their possessives ("AHU-1's and AHU-2's sensors") are not read
as a set, nor is a possessive joined with "as well as" ("A's damper, as well as B's, ..."),
and "AHU-1 or AHU-2" is one of them, not each; with no cited fact about one of its
items, what a set says that is not a figure, a verdict or a negation is not read ("Both valves
feed the finding"); "agrees" against "disagrees" is read as written, a negated form ("does not
agree") is not. A comparative with no figure ("more points are suspect than trusted") is not
read, but for "more" or "less energy" (than its baseline or not: a direction, #339, #340), nor are the words
"survives", "remains", "continues" or "is still flagged" said of a re-judged finding, nor
"lowest trust score" as a rank. And these fail closed although they can be true: "all" or "every" with no
number, unless a cited fact says all or counts that label with its total; "they are all ...",
"all of them" or a cell "all trusted" unless a cited fact counts all of them with that label (a
pronoun is not resolved); "half" (or "a quarter") of a thing no cited fact counts; a set of
items backed by fewer cited facts than its number, or by one when it has none ("The occupancy
points are flagged, for example ..."); a negated set that does not open its clause
("leaking_valve fails on neither AHU"); a set's figure with no cited fact about its items
("Both AHUs have 24 points" citing a facility); a trust class said of equipment that opens
its clause ("Both AHUs are suspect", from facts about their points); two verdicts said of one
set in one clause ("Both AHUs warn on X and fail Y": a verdict verb after the set noun is read
as its one label); "the rest" with no total in a cited fact; "over 9", "well
over 9" or "more than 9" against a fact's 9 or 10 (a bound is not a count); "most" or
"the majority of" unless a cited fact says most of the same thing, gives a share above half of
it or counts it with that label; "all", "every" or a completeness word said of a noun no cited
fact quantifies, though it is true ("Every rule returned a result" against "5 rules were run
... and 5 of them returned a result": say "All 5 rules returned a result"); "most" of a set
with one cited item; verbs listed "respectively" ("disagree and agree with their logger
respectively"); a negated verdict of a set whose cited facts give a third verdict ("No AHU
fails X" against two warns: say "Both AHUs warn on X"); a whole count the fact gives only as
a complement ("all 8 flagged hours fall outside the window" against "none of the 8 ... falls
in"); a complement's number ("86 of the 106 fall outside", a derived number); a figure the fact
tells apart by side, trust class or fault condition said without one, or with a side the
lists do not know ("the glycol valve"); a cause a cited fact implies but does not state with
"because", "due to", "caused by" or "explained by" ("declined -- untrusted input(s)"). A
model's own judgment -- a verdict on a savings claim, advice, a ranking, how sure it is, an
inference worded without the cause and effect words above -- rests on facts but is not one: it
is not recognised as judgment, so it passes when its figures, names and listed words match,
and it means nothing that it does. The prompts ask for it after the checked text, labelled as
judgment. A sentence a
reader would take as fair can fail (an inference such as "so no check was declined", a
paraphrase such as "no data" for a fact's "no valid sample", advice in the negative): say it the
way a fact does, cite the fact that says it, or leave it out.

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
`camber.capabilities._sanitize.name()`. A listing over many facilities that reports a facility
it cannot read in its own result sets `fences_itself=True` (as `facilities` does, 0.112): the
registry then takes no fence snapshot around the call, and the handler checks each facility's
fence itself (`camber.store._fence.snapshot` / `changed`). The schemas use a subset of JSON Schema 2020-12: `type`, `properties`, `required`,
`additionalProperties`, `enum`, `const`, `items`, `minItems` / `maxItems`, `minimum` /
`maximum` (and the exclusive forms), `minLength` / `maxLength` and `pattern`. `default` is an
annotation and is not applied. Registering a schema with any other keyword is a `ValueError`, so a
constraint is never declared without being enforced.
