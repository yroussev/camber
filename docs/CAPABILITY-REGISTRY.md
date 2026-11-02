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
index's appendix A (declines and data gaps; `limit` for more). One `offset` pages every list of
a result: a short list whose rows are all on the first page is empty on a later one, with a
banner that says so (`'cited' is empty on this page: all its 1 row(s) are on the first page`).

A capability cuts a list only with `camber.capabilities._page.page`, which records the notice in
the call; `Registry.call` turns every recorded notice into a banner and sets `truncated`, so a
capability cannot cut a list and leave the flag false.

| Name | Arguments | Returns | Cost |
|---|---|---|---|
| `facilities` | optional `limit`, `offset` | the allowed facilities: id, name, lifecycle state, time zone, first and last stored sample, point and equipment counts | cheap |
| `points` | `facility_id`; optional `equip`, `role`, `equip_class` | the facility's stored points (equipment, class, role), sorted, and their count | cheap |
| `coverage` | `facility_id`; optional `equips`, `roles`, `start`, `end`, `group_by` (`point` / `equip`), `min_gap_hours` | per point the samples, first and last sample, interval, coverage (of the whole window, at the point's own interval) and gaps; per equipment the mean and the worst point; the longest gaps | one timestamp scan |
| `sensor_trust` | `facility_id`; optional `equips`, `roles`, `start`, `end`, `view` (`rules` / `report`), `min_trust` | per point the trust score, verdict, flags, coverage (of the point's own span on the rule run's time grid, or its equipment's running hours: it can differ from `coverage`'s for one point) and whether it is below the caller's trust floor (`ctx.min_trust`, or a higher `min_trust`), least trusted first; the run config's sensor-reference comparisons (`references`: bias, its sign, RMSE, correlation, samples, overlap, verdict; a passing one too) | reads every trend |
| `rule_run` | `facility_id`; optional `rules`, `params` (`{rule: {param: value}}`), `equips`, `start`, `end`, `min_severity` | the findings (`finding_id`, rule, equipment, severity, summary, caveats, scalar metrics, and `conditional_on` when an input's configured sensor reference disagrees), worst first; counts per severity; the rules skipped for missing inputs | long-running |
| `finding_detail` | `facility_id`, `finding_id`; the same optional arguments as the `rule_run` call | one finding's full metrics, the parameters it ran with (default, overridden or the rule's built-in fallback), its flagged hours as contiguous time spans (and per named sub-condition; an event rule's events counted apart from the hours) or why there are none (`violations_note`), and which evidence chart applies; no image | the same run as `rule_run` |
| `rcx_report` | `facility_id`; optional `equips`, `start`, `end`, `top_n`, `offset`, `limit` | the RCx report as data: KPIs, the `top_n` ranked issues (cause, the rules and the `finding_ids` to pass to `finding_detail`, cost or why uncosted, hours, confidence and its reasons), the section list and the report's fact index; no file | long-running |
| `mv_summary` | `facility_id`; optional `equips`, `claim` (a claim to check: each figure it states becomes a `claimed_figure` fact) | the M&V findings of the project the caller's run config declares for the facility: baseline fits, savings with uncertainty (in the meter's own units unless the config has a `units` block, and "for information only" when the baseline fails G14 acceptance; both said in the text), triggers, method proposals, and which declined | seconds |
| `drift` | `facility_id`; optional `baseline` `[start, end]`, `current`, `families`, `equips` | drift findings and per-equipment verdicts against a known-good baseline window (fitted for the call, never saved) or the run config's frozen baselines (read, never written), and the equipment not evaluated | long-running |
| `dataset_search` | optional `query`, `kind`, `labeled`, `licence` | catalog metadata: id, title, kind, licence, access, commercial use, fault labels, equipment, download size, what it is good for | cheap |
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

   The data carries its own mark too (0.112, #319): `camber datasets ingest` writes the dataset
   id, licence and a research-only flag into every part file, and the handler runs in a serving
   scope in which the store refuses a research-only part whatever facility, directory or store
   it sits in. A facility holding one is `refused_licence` before the handler runs (the
   `facilities` listing withholds it), and a read the handler makes refuses it again. See
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

- **Citations.** `[F1@x]`, or several in one bracket, `[F1@x, F2@y]` or `[F1@x; F2@y]`. Only
  visible text counts: a citation in an HTML comment or tag cites nothing. With `per_sentence`
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
  `11 points: 9 trusted, 2 suspect`). A date, time or year must be one a cited fact carries in
  its text or in a date field ("in 2017" matches a `reporting_period` of 2017). A list marker
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
    "passes flow when closed" are not verdicts.
  - **Directions.** A direction word (rose, increased, fell, decreased, saved, avoided, reduced,
    wastes, ...) must be one a cited fact uses (or its `direction`), except after "would",
    "could" and the like; "fall on 2025-05-14" is not one. "High" / "low" and "above" / "below"
    must not be the opposite of what the cited facts say.
  - **Negations, both ways.** A negation the sentence states ("did not leak", "never moves",
    "without", "no <thing>", "none of <them>", "0 <things>") needs a cited fact that negates the
    same thing, about the same words: a fact's "no valve leak" supports "the valve did not
    leak", while "drift not evaluated" supports no other negation, and "CHW-leak 0%" says nothing
    of the heating valve. And a sentence must not state what a cited fact negates: "the valve
    leaks" against "no valve leak", "the setpoint moves" against "never moves". A fact that a
    check was skipped, declined or had insufficient data says it "was not judged", so that
    sentence can be cited; "not a pass" cannot.
  - **Quantifiers and ranks.** "All" or "every" needs a fact that says all, every or 100%;
    "most" one that says most or a share above half; "none" is a zero (above). "Issue 3" or "the
    1st issue" must be a rank a cited fact gives, when a cited fact gives ranks.
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
  so is a `NOTICE:` line that repeats a notice and cites its `N` id. The quote must open a line
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
gives as a subject, a point role outside the families above, and which cited fact a figure
belongs to when a sentence cites several. A model's own judgment -- a verdict on a savings
claim, advice, a ranking, how sure it is -- rests on facts but is not one, so it is never
supported: the prompts ask for it after the checked text, labelled as judgment. A sentence a
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
