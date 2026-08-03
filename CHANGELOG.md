# Changelog

All notable changes to CAMBER are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
[Semantic Versioning](https://semver.org/) from 1.0 onward.

## [0.9.6] — 2026-08-02

Pre-1.0 hardening: **honest failures at the edges.** Analytics entry points and the untrusted
parsers now degrade to a clear error or a partial result instead of a raw traceback or a
plausible-looking wrong answer. No change on valid input.

### Fixed / Added — input validation
- **`forecast` / `disaggregate` / `tariff.compute_bill`** reject a non-`DatetimeIndex` series up
  front (they previously coerced a numeric index into nanosecond timestamps and returned a
  meaningless result). Empty input stays graceful. New private `camber._validate` helpers.
- **`fault_economics.EnergyPrice`** rejects a negative or NaN rate at construction (rather than
  "costing" a fault at a bogus rate); **`scorecard.build_scorecard`** rejects `None` and now
  accepts any iterable of findings.

### Fixed — adversarial fuzzing of the hand-rolled / untrusted parsers
- **`interop.brick`** — the minimal Turtle reader no longer `IndexError`s on a predicate list with
  no object, and `roles_from_brick`/`mapping_from_brick` normalize any backend parse failure
  (rdflib `BadSyntax`/`AssertionError`, or a minimal-reader error) into a clear `ValueError`.
- **`interop.haystack_semantic`** — `role_from_tags` tolerates a malformed tag collection (non-string
  markers) and `roles_from_haystack` skips un-parseable points instead of crashing on an unpack.
- **`tariff.compute_bill`** — a malformed rate structure (empty `energy_rates`, or a schedule naming
  a period with no matching rate) raises a clear error naming the period, not an `IndexError`.
- Regression tests: `tests/test_input_validation.py`, `tests/test_hardening_interop.py`,
  `tests/test_hardening_reports_econ.py` (report builders confirmed robust on empty/single/large).

## [0.9.5] — 2026-08-02

Pre-1.0 hardening: **CAMBER is now a typed library.** No API or behavior change (full suite
unchanged).

### Added
- **`py.typed` marker** (PEP 561), shipped in the wheel — downstream mypy/pyright now trust
  CAMBER's inline type hints.
- **`mypy` gate.** A pragmatic `[tool.mypy]` config (optional-extra libs import-ignored,
  untyped function bodies not deep-checked yet — a floor that ratchets in later releases) and
  a `types` CI job running `mypy` on every push/PR. `mypy` added to the `dev` extra.
- **Local, gitignored content denylist** for the pre-commit guard (`.githooks/denylist.local`,
  templated by `.githooks/denylist.local.example`): a per-clone file that can hold sensitive
  terms — e.g. a real client site name — to block them at commit time *without* committing the
  term itself. Reinforces the vendor-/site-neutral contribution rule.

### Changed
- Backfilled type annotations across ~25 modules to reach a clean `mypy` run — missing variable
  annotations, `None`-narrowing asserts that restate invariants the code already enforced, a
  `Site`→`Equip` parameter-annotation fix in `interop/site_model`, and targeted
  `# type: ignore[code]` (with reasons) only where mypy can't see a dynamic/dataclass/numpy
  type. No runtime behavior change.

## [0.9.4] — 2026-07-31

Correctness release (same high-outside-air design as 0.9.3): the `economizer_high_limit` rule
false-faulted a high-outside-air building, and there was no way to tell it the design minimum.

### Fixed
- **`economizer_high_limit` OA-damper unit bug.** `OA_DAMPER` is a percent role (the pipeline
  scales it to 0–100), but the rule compared it against a `0.25` *fraction* — so every open damper
  read "not locked out" (≈99.99% of hot hours in the field). The damper is now canonicalized to a fraction
  regardless of source scale (0–1 or 0–100).
- **Judge on outside-air fraction when available.** When mixed- and return-air temperatures are
  present, the rule now judges on temperature-balance OA-fraction (the `camber.oafraction` method)
  instead of damper position — damper % isn't linear in OA flow. It falls back to a damper threshold
  otherwise, recording a caveat that names the basis. Distinct from `outdoor_air_fraction` (excess OA
  in cooling generally) vs this rule's lockout-above-the-high-limit.
- Both the high limit and the minimum (`high_limit_f`, `min_damper`/`min_oa_pct`) are documented as
  tunable; the defaults encode a typical, not universal, building (CA Title 24 sets the changeover by
  climate zone).

### Added
- **Per-rule config parameters.** A `rules` entry in a config may now be `{"name", "params"}` to
  override a rule's constructor for the run, alongside the existing bare-string form (backward
  compatible). Benefits ~24 tunable rules. New `camber.rules.builtin.make_rule(name, **params)`
  constructs a built-in rule by name with clear errors on an unknown name or invalid parameter.

## [0.9.3] — 2026-07-31

Correctness release (field-found on a high-outside-air, 50%-OA VAV design): a rule must never
assert a negative it did not test. When an absent **optional** input made a sub-check
impossible, several rules silently collapsed the missing input into a confident wrong
verdict — a `False` metric, a raised severity, or a summary asserting something untested.

### Fixed — the "could not evaluate" honesty convention
- **`Finding.caveats: list[str]`** (new field) + a documented convention in
  `camber.rules.base`: an unevaluated sub-check is represented as `None` (tri-state), never a
  `nan`/`False`/`0` sentinel; it is excluded from severity, written as a null metric, kept out
  of the summary, and recorded as a caveat. The audit report surfaces finding caveats.
- **`Registry.run`/`run_fleet`** now record any absent optional roles on each finding
  (`metrics["_missing_optional"]`) so the whole class is visible without reading each rule.
- **Rules corrected** (absent optional role no longer flips the verdict): `chw_plant_reset`
  (OAT → false "no reset"/warn — the exemplar), `boiler_summer_lockout` (OAT → false clean),
  `supply_air_control` (no fan signal → false off-setpoint fault), `overcooling_min_flow`
  (no damper → over-count), `overcooling_severity` (no heating SP → over-flag),
  `zones_heat_cool_census` (missing flow/SP → under-count), `chw_pump_dp_reset` (false "flat
  DP setpoint"), `supply_air_reset` (false "load-tracking" verdict), `unmet_setpoint_hours`
  (one-sided setpoint → false "0%"). Each now declines the sub-check with a caveat instead.
- Hardened three `camber.aso` recommender comparisons against `None`-valued metrics.

**Behavior change (non-breaking, pre-1.0):** affected metrics may now be `null` instead of a
(wrong) `False`, and some findings that previously read `warn`/`ok` now read `ok`/`info` with a
caveat. These are corrected results; per `docs/API-STABILITY.md`, bug fixes that change a
genuinely wrong result are allowed. `Finding.caveats` and `_missing_optional` are additive.

## [0.9.2] — 2026-07-28

Second pre-1.0 hardening release: **the public API contract** — the biggest 1.0 prerequisite.
Settles what is public, writes down the SemVer + deprecation promise, and locks it in CI. No
behavior change to existing analytics (full suite green).

### Added
- **`docs/API-STABILITY.md`** — the public-API + deprecation policy: a name is public iff it
  (and its module) has no leading underscore; `__all__` is each module's curated surface;
  SemVer from 1.0; a deprecation window of at least one minor release and never removed before
  the next major. Wired into the docs nav.
- **`camber._deprecation`** (private) — `@deprecated(since=, remove_in=, use=)` decorator and
  `warn_deprecated()` helper emitting a consistent `DeprecationWarning`, attaching a
  machine-readable `__deprecated__` marker and a docstring note. Works on functions and classes.
- **`__all__` everywhere** — declared on the three previously-bare subpackages (`rules`,
  `model`, `charts`, with curated re-exports) and on all 70 flat top-level modules (each
  module's non-underscore surface). `camber.ingest` now surfaces its data-quality API
  (`assess`/`clean`/…) and `camber.report` its interactive-viz helpers, closing docstring-vs-
  export gaps.
- **`tests/test_public_api.py` + `tests/public_api_snapshot.json`** — a committed snapshot of
  the entire public surface (200 modules, 832 names); adding/removing any public name fails CI
  until the snapshot is regenerated. Also asserts every `__all__` name resolves, no private
  name leaks into an `__all__`, and every public function/class is documented (575, all pass).

### Changed
- Top-level `camber` is now an explicit namespace: it exposes only `__version__` (documented),
  not a re-export surface — import from subpackages/modules per the policy.
- `camber.scorecard.category_for` / `grade_for` reclassified as private (`_category_for` /
  `_grade_for`) — they were trivial internal lookups; users get their results via `Scorecard`.



First of the pre-1.0 hardening series. **A lint + format gate** — no behavior change, no
API change; the whole codebase is now machine-formatted and lint-clean, and CI enforces it.

### Added
- **`ruff` gate** (`[tool.ruff]` in `pyproject.toml`): line length 100, targeting Python
  3.10, rule set `E`/`F`/`I`/`W`/`UP`/`B` (pycodestyle, pyflakes, import sorting, pyupgrade,
  flake8-bugbear). A `lint` job in CI runs `ruff check .` and `ruff format --check .`.
- **`.pre-commit-config.yaml`** wiring the `ruff` + `ruff-format` hooks for local use,
  alongside the existing attribution guards in `.githooks/`.

### Changed
- Whole codebase auto-formatted with `ruff format` and lint-cleaned: removed unused imports,
  sorted imports, applied `pyupgrade` modernizations, and fixed a handful of bugbear findings
  (`assert False` → `raise AssertionError` in tests, unused loop/local variables). Two
  deferred `from .timegrid import interval_hours` imports moved to module top (no cycle).
  `zip(strict=...)` auditing (`B905`) is intentionally deferred to a later release.



Ninth release — **ingest robustness across vendor formats** + a **real-data M&V validation** on Building
Data Genome 2. Dependency-light throughout; the ingest refactor is fully backward compatible (existing
BAS/ISO exports parse identically — full suite green).

### Added — ingest robustness (`docs/INGEST-FORMATS.md`)
- **`camber.tsparse`** — one shared multi-format timestamp parser behind every adapter: an ordered
  format try-list (ISO 8601, US, European `dayfirst`, the BAS 12-hour format, LBNL `yyyymmdd`), epoch
  seconds/millis + Excel-serial detection, tz-abbrev strip, auto-detect by parse rate, naive-local by
  default. Replaces 5 scattered inline `pd.to_datetime` sites. **Fixes silent traps:** European
  `03/04/2025` read as US, a non-BAS per-point format yielding an empty series, and a trailing `AM`/`PM`
  meridiem being stripped as a timezone.
- **`camber.coerce`** — shared value coercion: a null/quality-token vocabulary (`N/A`, `---`, `Bad`,
  `Comm Fail`, …) + thousands-separator / European-decimal-comma handling, and an extended, overridable
  status vocabulary (On/Off, Open/Closed, Fault/Alarm/Normal, Override/Hand/Manual, Auto).
- **Vendor profiles** (`camber.ingest.profiles`) — an `IngestProfile` + presets
  (`niagara_n4`/`metasys`/`webctrl`/`tracer`/`desigo`) capturing each export tool's delimiter/encoding/
  skiprows/timestamp/decimal conventions; `load_csv(..., profile=…)` (and explicit `encoding`/`delimiter`/
  `skiprows`/`decimal`/`dayfirst` overrides).
- **`camber.ingest.csv_long.LongCsvAdapter`** — the `timestamp,point,value[,unit]` historian shape.
- A synthetic **per-vendor equivalence corpus** asserting every vendor format normalizes to the same frame.

### Added — BDG2 M&V validation (`docs/VALIDATION.md`)
- **`examples/bdg2/benchmark.py`** — the M&V analogue of the LBNL FDD benchmark: the ASHRAE G14
  baseline-model **acceptance rate** across ~2,044 real BDG2 whole-building meters, with Wilson CIs.
  Verified on real data: chilled water **36%** [32–40%] vs electricity **8%** [7–10%], pooled 15% — the
  engine reproduces the expected physics (weather-driven energy is ~4.5× more baseline-able) and reports
  both honestly. Committed a real (not CI-seeded) baseline; deterministic; new `mv-accuracy` CI job.

### Tests
- +43 (1285 → 1328): `test_tsparse`, `test_coerce`, `test_ingest_profiles`, `test_ingest_long`,
  `test_ingest_formats` (per-vendor equivalence), `test_bdg2_benchmark` (pure metrics, no download).

## [0.8.0] — 2026-07-27

Eighth release — one **feature** plus a **pre-1.0 stress-test / hardening pass**. Dependency-light
throughout (no new deps; the hardening uses seeded generators, not `hypothesis`).

### Added
- **Cross-panel interactive linking** (`camber.report`) — a brush in the dashboard scatter now
  propagates to every view. A shared `window.CAMBER` selection bus (a Set of selected timestamp
  strings) drives two panels promoted from static PNG to **inline SVG**: **B (fault multitrend)** shades
  the brushed time ranges and **E (load carpet)** highlights the matching hour×date cells. Panels A + I
  stay PNG; every panel keys off the same `str(timestamp)`, so they interoperate without a shared
  coordinate system. Single self-contained CSP-safe file, vanilla JS, no framework. New helpers
  `selection_bus_html`, `carpet_svg_html`, `multitrend_svg_html`.

### Hardened (bugs found + fixed by the stress pass)
- **`io.load_csv`** — empty / header-only / unparseable-timestamp CSVs now raise a clear `ValueError`;
  a single bad timestamp row is dropped instead of crashing the load; value columns are coerced to
  numeric so a stray text cell no longer silently poisons a column to `object` dtype.
- **FDD rules** — a 191-case sweep asserts every registered rule returns a `Finding` (never raises) on
  empty / 1-row / all-NaN / all-equal / duplicate-index frames; two plant rules (`condenser_water_reset`,
  `cooling_tower_approach`) hardened against a duplicate-index reindex crash.
- **M&V calibration** — `rc_model.calibrate` degrades to `accept=False` (not `ValueError`) on <4-point /
  all-NaN / gapped / constant energy, so the savings layer refuses to claim a number.
- **Fleet rollup** — the EUI-percentile loop is O(N log N) via `bisect` (was O(N²)); scale-tested to N=500.
- **Mapping** — `MappingProvider` rejects catastrophic-backtracking (ReDoS) regex patterns at config
  load (a `(a+)+`-style pattern could hang the mapper); legitimate patterns unaffected.
- **Determinism** — `validation.check_determinism` now nets `calibrate` / `best_model` /
  `detect_level_shifts` / cohort / `faultlab` (was 2 spots).

### Tests
- +230 (1055 → 1285): `test_hardening_*` (io, rules sweep, mandv, scale+determinism, timegrid+mapping),
  cross-panel linking additions, and a shared `tests/conftest.py` of degenerate-frame factories.

## [0.7.0] — 2026-07-27

Seventh release — **IPMVP Option D (calibrated simulation)**, the last remaining IPMVP boundary.
Dependency-light (numpy only), read-only toward the BAS, clean-room (ISO 13790 simple-hourly / ASHRAE
inverse-modeling lineage), synthetic-fixture tested. **CAMBER now covers IPMVP Options A/B/C/D.**

### Added
- **`camber.mandv.rc_model`** — a forward, schedule-driven **1R1C grey-box** building model.
  `RCModel(ua_eff, gain_eff, tau).predict(oat, schedule)` returns hourly HVAC energy and can be run under
  a counterfactual (as-corrected) control — the capability the inverse models (A/B/C) lack.
  `daily_schedule(...)` builds an occupied/setback control schedule.
- **`calibrate(oat, schedule, metered_energy)`** — mirrors the change-point fitter: grid the one
  nonlinear parameter `tau` (coarse→fine), OLS the linear conductance/gain, keep the best CV(RMSE);
  gated by the existing ASHRAE G14 acceptance (`stats.fit_stats` + `cv_rmse_max_for("hourly")`).
  Deterministic (`validation.check_determinism`). Returns a `Calibration` (model + fit + accept).
- **`option_d_savings(calibration, oat, as_found, as_corrected)`** — differences the calibrated model's
  as-found vs as-corrected annual profiles into modeled avoided energy with a **G14 Annex-B fractional
  savings uncertainty** band. **Refuses to claim a saving when the calibration fails the G14 gate**
  (`valid=False`, `avoided_energy=None`) — the same refuse-to-fabricate posture as `fault_economics`
  (`costed`) and `ecm_savings` (upper bound).
- **`mandv.ecm_savings.modeled_savings(...)`** — bridges the metered-waste **upper bound** to the
  pre-implementation **modeled** Option-D saving, closing the caveat that module's docstring flagged.

### Docs
- `docs/OPTION-D.md`; IPMVP A/B/C/D noted complete in `MANDV.md` + `CAPABILITIES.md`; ROADMAP marks
  Option D delivered and reshapes Next-0.8 (the deferred chiller dataset, Option-D depth, packaging).

### Tests
- +11 (1044 → 1055): `test_rc_model` — recovers a known model within tolerance, calibration is
  deterministic, unstructured noise fails the G14 gate, savings match the direct profile difference, and
  a failed calibration claims no saving.

## [0.6.0] — 2026-07-27

Sixth release — **validation & interop completeness**: finish the stories 0.5 opened rather than open a
new headline (IPMVP Option D / calibrated simulation is deferred to 0.7). Read-only toward the BAS,
dependency-light, clean-room/citable; synthetic-fixture tests + docs per capability.

### Added
- **FDD hardening → 33/33.** The synthetic fault-injection harness (`camber.faultlab`) now scores
  **every single-equipment rule** at 100% TPR / 0% FPR — scenarios added for the last 9 fixture-only
  rules (boiler summer-lockout, HW-plant ΔT, condenser-water reset, CHW/HW pump DP reset, leaking valve,
  night/weekend setback, OA-fraction, G36 reheat minimization). The fixture-only list is now empty; the
  committed baseline is regenerated and CI-gated.
- **Haystack tag→role import** (`camber.interop.haystack_semantic`): `role_from_tags`,
  `roles_from_haystack`, `mapping_from_haystack` — inverting `HAYSTACK_HINT` (subset match, most-specific
  tie-break) to close the export→import round-trip to Brick parity. All 54 roles round-trip.
- **ASHRAE 223P coverage 21 → 44 roles** (`interop.semantic223.ROLE_TO_223`): the full plant/hydronic
  side (CHW/HW/CW temps, loop pressures, pump/tower speeds), power + thermal energy, ambient/humidity,
  and the refrigerant-side approach temps. The 10 remaining binary status/command roles carry no QUDT
  quantity-kind and are listed in `_NO_223_QUANTITY` (intentionally unmapped); a test asserts the mapped
  and unmapped sets partition every role.
- **Broadened real-data LBNL benchmark (Tier 1):** a cooling-coil-valve leakage **severity sweep**
  (010–100%) characterizes the leak detector that the pooled result showed under-firing; the fetcher is
  hardened to skip zip members absent from a given release and to gate its no-op on a proven-present core.

### Deferred
- **IPMVP Option D — calibrated simulation** → 0.7 (feasible as a dependency-light grey-box RC model).
- **second labeled chiller dataset (Tier 2)** — real-data validation of the refrigerant-side rules,
  pending an owner license-clearance for clean-room use.

### Tests
- +13 net (1031 → 1044): `test_faultlab` (33/33, empty fixture-only), `test_haystack_semantic`,
  `test_semantic223` (plant/DX round-trip + partition), `test_lbnl_fetch` (robust fetch, synthetic zip).

## [0.5.0] — 2026-07-26

Fifth feature release. **Validation-led**: prove the existing FDD suite, then broaden equipment
coverage, and make the 0.4 grounded agent reachable from the terminal. Everything stays read-only
toward the BAS, dependency-light, clean-room/citable, with synthetic-fixture tests + a `docs/` page per
capability. IPMVP Option D (calibrated simulation) is deferred to 0.6.

### Added

**FDD accuracy — prove the whole suite** (`camber.faultlab`, `examples/synthetic_fdd`, `docs/VALIDATION.md`)
- A deterministic synthetic fault-injection harness that scores the registry: each rule's target fault
  is injected (a labeled positive) alongside a fault-free frame (a negative), scored with the existing
  `camber.eval` LBNL framework. **24 of 33 single-equipment rules** are now accuracy-scored at 100% TPR
  / 0% FPR (up from 2 in the LBNL benchmark); the remaining 9 are honestly reported as fixture-only.
- A **G36 §5.16.14 FC1–FC15** engine harness (6 representative fault conditions, all detected, clean
  quiet). Runner (`--json/--gate/--update-baseline`) + committed baseline, gated in normal CI
  (`tests/test_faultlab.py`) and the benchmark workflow (no download). Honest scored-vs-fixture
  coverage table.

**Packaged / DX & refrigerant-side FDD** (`docs/FDD-DX.md`)
- 10 new roles: `compressor_status`/`compressor_stage`, `condenser_fan_status`, `heat_stage`,
  `reversing_valve_cmd`, `filter_diff_press`, `supply/return_air_humidity`,
  `cond/evap_approach_temp` — each with `PHYSICAL_BOUNDS` + a Haystack hint.
- 4 equipment templates: **RTU**, **HeatPump** (VRF), **DOAS** (ERV via optional roles), and **FCU**
  (now distinct from the VAV alias).
- 5 rules: `compressor_short_cycle`, `compressor_staging`, `heatpump_defrost`, `filter_fouling`, and
  `chiller_approach_fouling` (condenser/evaporator approach-temperature degradation — the
  refrigerant-side indicator that needs no refrigerant-pressure instrumentation).

**Agent CLI** (`camber.cli`, `docs/CLI.md`)
- The `camber` console script becomes a subcommand CLI: `run`, `report`, `explain`, `ask`, `fleet`,
  and `charts`. `explain`/`ask` are grounded and useful with no LLM; `--llm-cmd` wires any model via a
  **vendor-neutral** shell seam (prompt on stdin → completion on stdout) whose subprocess wrapper lives
  in the CLI so `camber.agent` stays pure.

**Portfolio triage** (`camber.agent`)
- `facts_from_fleet(FleetReport)` (a `fleet` fact kind) and multi-site `Context`
  (`build_context(fleet=…, runs=…)`) enable grounded portfolio-wide Q&A ("which building is worst?").

### Changed
- **BREAKING (CLI):** the legacy top-level `--csv`/`--demo` AHU heating-vs-cooling charts now live under
  `camber charts` (e.g. `python -m camber.cli charts --demo reheat`).

### Tests
- +41 tests (990 → 1031): `test_faultlab`, `test_new_roles`, `test_dx_rules`, `test_cli`, plus template
  completeness and portfolio-context additions.

## [0.4.0] — 2026-07-10

Fourth feature release. Adds the two deferred **AI-assist** tracks — **assisted point mapping** and
a **grounded explanation & Q&A agent** — built dependency-light, **advisory-only** (never the source
of truth, always auditable), and **read-only toward the BAS**. The LLM path is fully
**provider-agnostic**: no vendor is named, no SDK or network client is imported, and an AST guard
proves it; everything works with **no LLM wired** via deterministic fallbacks. Each capability ships
option flags, a `docs/` page, and synthetic-fixture tests.

### Added

**Assisted point mapping** (`camber.mapping_assist`, `docs/MAPPING-ASSIST.md`)
- `suggest_roles(token, …)` / `review_unmapped(tokens, mapping, …)` — suggest roles for **unmapped**
  BAS tags as a human-confirmed review list; **never mutates a `MappingProvider`** (advisory boundary).
- `FeatureSuggester` — dependency-light baseline (numpy/stdlib): tag initials + edit distance vs the
  `Role` vocabulary, a unit-compatibility table, and physical-range fit (reusing
  `sensorhealth.range_violation_frac`) so a role the data physically contradicts is demoted.
- `MLSuggester` — optional learned backend behind the new **`[ml]` extra** (scikit-learn, lazy
  `_require()`); a char-n-gram classifier trained on the caller's / synthetic labels (`fit`,
  `from_mapping`) — **no pretrained weights** (clean-room). Predictions pass the same range gate.
- `LLMSuggester` — reuses the agent seam (no new dependency); the model proposes roles, each is
  validated `Role(value)` and **re-scored** via `mapping_confidence.score_token` so a
  physically-inconsistent suggestion can't outrank a good one.

**Grounded explanation & Q&A** (`camber.agent`, `docs/AGENT.md`)
- `agent.explain(findings, …)` and `agent.ask(question, …)` — cited, plain-language explanations and
  NL Q&A over the deterministic layers; return a `Grounded(text, cited, facts, grounded, flagged,
  source)`.
- `agent.context` — a **grounding whitelist**: `Fact(id, kind, equip, text, data)` + `Context` with
  order-stable, deterministic ids (`F1`/`C1`/`R1`/…). Builders `facts_from_findings`, `facts_from_run`,
  `facts_from_scorecard`, `facts_from_completeness` (why a rule couldn't run), `facts_from_history`
  (**bounded stats only**, never raw series), and `facts_from_mapping`. Cost facts never fabricate a
  dollar figure when uncosted — they state the basis.
- `agent.verify` — grounding by **number-traceability**: an answer is grounded iff every `[id]`
  resolves and every number it states appears in a cited fact; `strict` mode repairs (drops
  untraceable sentences, strips unknown cites), non-strict marks only.
- `agent.templates` — deterministic (no-LLM) `explain_from_facts` / `answer_from_facts`; trivially
  100% grounded and the oracle the LLM path is verified against.
- `agent.client` — the **provider-agnostic seam**: `AgentClient` wraps an injected
  `complete(prompt, **opts) -> str` callable (`client_from_callable`, network-free `stub_client`).
  Unwired is a valid state (falls back to templates); `generate()` raises a helpful error only when
  actually called.

**Packaging**
- `[ml]` optional extra (`scikit-learn>=1.3`); conda recipe filled to 0.4.0 with a `run_constrained`
  for it; a hardened MkDocs → GitHub Pages workflow (`.github/workflows/pages.yml`) + an **AI-assist**
  docs nav group. `docs/DEPLOY.md` documents the conda-forge / Pages / community owner-actions.

### Guarantees
- `tests/test_agent_readonly_guard.py` — an AST guard over `camber/agent/*.py` + `camber/mapping_assist.py`
  fails on any write/command/actuation symbol **and** on any LLM-provider or network import, mechanically
  enforcing the read-only and no-vendor/no-network contracts.

### Tests
- +77 tests (913 → 990): `test_mapping_assist`, `test_agent_context`, `test_agent_verify`,
  `test_agent_client_seam`, `test_agent_explain_ask`, `test_agent_readonly_guard`.

## [0.3.0] — 2026-07-07

Third feature release. Completes the **visualization pattern catalog** (the "charts and faults are
the same artifact" differentiator), adds an **advisory decision layer** (recommendations, a
prioritized action plan, a health scorecard), deepens **FDD / M&V / analytics**, and hardens
**time/DST handling** and the **release pipeline**. Everything stays **read-only toward the BAS/OT**
and dependency-light (numpy/pandas + stdlib; optional extras stay lazy); each capability ships with
option flags, a `docs/` page, and synthetic-fixture tests.

### Added

**Visualization — the full pattern catalog A–J** (`docs/VISUALIZATION.md`)
- **Pattern D** — `charts.oat_scatter`: X-vs-OAT "cloud-shape" scatter with change-point overlay,
  shape classification (linear / hockey-stick / V / scattered), and **brush-back** (region →
  timestamps).
- **Pattern G** — `charts.diagnostic`: templated subsystem diagnostic scatters (expected band
  overlaid, violations shaded) with a packaged `TEMPLATES` set (SAT/CHW reset, economizer,
  no-simultaneous-heat-cool) and constructors.
- **Pattern J (keystone)** — `charts.evidence`: **every rule renders its own evidence**. A duck-typed
  `evidence(equip, frame)` hook returns an `Evidence` that `render_evidence` dispatches to a
  B/D/E/G renderer; wired into the HTML dashboard and the Std-211 audit report. Rules without a
  tailored hook fall back to a default multi-trend of the roles they examined, so the whole 33-rule
  library (and future rules) carries evidence with no per-rule map.
- **Pattern C** — `charts.cohort` + `rules.cohort.CohortDeviation`: peer/cohort small-multiples
  ordered by deviation, and a fleet rule flagging a unit that runs unlike its peers.
- **Pattern H** — `charts.savings`: cumulative M&V baseline-vs-actual with the avoided energy shaded
  and an ASHRAE G14 fractional-savings uncertainty band.
- **Pattern F** — `charts.loadprofile_chart`: load profiles (weekday/weekend) and load-duration
  curves with baseload/peak annotation and cost translation.
- **Interactive linking** — `report.linking`: a brush-able inline-SVG scatter (vanilla JS, no
  framework, CSP-safe) with a linked timestamp readout; `build_dashboard(interactive=True)`.

**FDD rules** (all with evidence hooks + ASO recommenders + `docs/CAPABILITIES.md`)
- `control_hunting` — a modulating output that reverses direction excessively (unstable loop).
- `unmet_setpoint_hours` — occupied space temp outside the heating/cooling band (comfort/capacity).
- `supply_air_control` — supply-air temperature not tracking its setpoint.
- `airflow_tracking` — VAV airflow not tracking its setpoint.
- `cohort_airflow` / `cohort_space_temp` — shipped cohort-deviation fleet-rule instances.
- `economizer_high_limit` (OA damper not locked out above the high limit), `free_cooling_missed`
  (mechanical cooling while free cooling was available), `static_pressure_reset` (duct-static
  setpoint that doesn't trim with demand).

**Advisory decision layer** (read-only, human-in-the-loop)
- `camber.aso` — maps an actionable finding to a suggested setpoint/sequence change, grounded (cites
  the rule + G36/PNNL) with documented override-able targets; never a BAS command. `docs/ASO.md`.
- `camber.actionplan` — fuses findings + `fault_economics` ($/yr) + `aso` into a ranked action plan;
  wired into the audit report and config-driven runs. `docs/ACTIONPLAN.md`.
- `camber.scorecard` — rolls findings into per-category scores + an overall A–F grade.
  `docs/SCORECARD.md`.

**M&V**
- `mandv.degreeday` — variable-base HDD/CDD regression baseline (balance point auto-fit by CV(RMSE)).
- `mandv.option_a` — IPMVP Option A (measured Δparameter × stipulated duty), completing Option
  A/B/C coverage.

**Analytics**
- `camber.schedule` — infer the actual weekly operating schedule from interval load; compare to a
  stated schedule (setback opportunity). `docs/SCHEDULE.md`.
- `camber.changedetect` — operational change-point (level-shift) detection in time, for MBCx
  persistence/regression. `docs/CHANGEDETECT.md`.
- `camber.freecooling` — economizer free-cooling opportunity in hours and dollars.
  `docs/FREECOOLING.md`.
- `camber.disaggregate` — split an interval load into baseload / weather / other.
  `docs/DISAGGREGATE.md`.
- `camber.anomaly` — anomaly ensemble: fuse point (MAD), change-point, and data-quality signals
  into one severity verdict. `docs/ANOMALY.md`.

**Reporting**
- `report.build_site_report` — a one-shot self-contained HTML deliverable: health scorecard +
  chart sections + ranked action plan + per-finding evidence. `docs/SITE-REPORT.md`.

**Time handling & DST** — `camber.timegrid` (`docs/TIME-HANDLING.md`)
- `interval_hours` (shared, robust to duplicate/zero gaps), `regularize` (sort + de-duplicate
  timestamps), `localize` (tz-localize resolving DST ambiguous/nonexistent times), and
  `dst_anomalies` (count duplicates + fall-back/spring-forward transitions).

**Standards** — `interop.openadr`: map a `geb.DemandResponseResult` to an OpenADR-3.0-shaped report
payload (`docs/GEB.md`).

### Changed

- **Release pipeline hardened** (`.github/workflows/release.yml`): semver-only trigger, deny-by-
  default token with per-job least privilege, hardened runners (egress audit), no persisted git
  creds, per-job timeouts + single-flight concurrency, a **tag↔version consistency gate**, a
  3.10/3.11 test matrix, a built-wheel install smoke test, PyPI `skip-existing`, SLSA provenance +
  SBOM on the image, and changelog-extracted release notes.
- `io.load_csv(dedupe="first")` collapses duplicate timestamps on load; `ingest.quality` reports
  `n_duplicate_ts`.
- `report.build_dashboard` gains `rules`/`evidence`/`interactive` flags; `AuditReport.to_html`
  gains `rules`/`frames`/`recommend`; config-driven runs support a `recommend` report option.
- `Finding` gains an optional `evidence` field (additive; back-compatible).
- `ROADMAP.md` re-baselined and `docs/CAPABILITIES.md` extended for the 0.3 surface.

### Fixed

Correctness issues surfaced by a multi-agent code review of the 0.3 diff (regression-tested in
`tests/test_review_fixes.py`):
- cohort robust-z no longer masks a real outlier when >half the cohort share a value (MAD=0
  mean-absolute-deviation fallback);
- `scorecard` no longer silently drops unmapped/plugin-rule findings (they'd hide behind an "A");
- `degreeday` drops NaN periods before the fit and rejects degenerate n≤p fits;
- `changedetect` constrains splits by `min_segment` (no spurious shift from a single edge outlier);
- `savings_chart` guards an empty cumulative array; `interval_hours`/`hunting` are robust to
  duplicate (DST fall-back) timestamps;
- evidence rendering closes only its own figure (was `plt.close("all")`) and unifies the dashboard/
  audit loop; `outlier_mask` no longer crashes on a non-unique index;
- config `EnergyPrice` ignores unknown keys instead of crashing late; unmet/overcooling evidence
  masks now match their finding's metric.

## [0.2.0] — 2026-07-06

Second feature release. Extends the 0.1 core along the "Next — 0.2" roadmap and a streaming/
grid/carbon analytics sprint. Everything stays **read-only toward the BAS/OT** and dependency-light
(numpy/pandas + stdlib; optional extras stay lazy). Each capability ships with option flags, a
`docs/` page, and synthetic-fixture tests.

### Added

- **ASHRAE 62.1 ventilation verification** (`camber.ventilation`, `camber.rules.ventilation_rule`)
  — Ventilation Rate Procedure check of delivered outdoor air (`required_oa_cfm`, `assess_62_1`)
  and a DCV-modulation check (`assess_dcv`), plus `DemandControlledVentilation` /
  `VentilationRateProcedure` rules and a new `Role.OA_AIRFLOW`. `docs/VENTILATION.md`.
- **ASHRAE 223P + richer Brick interop** (`camber.interop.semantic223`) — map `Role`/equipment
  classes to a 223P-shaped RDF subset (minimal/full profiles, builtin or rdflib backend), and a
  broadened role↔Brick map with equipment hierarchy + relationships. `docs/ONTOLOGY.md`.
- **Continuous benchmarking gate** (`camber.eval.check_against_baseline`) — the LBNL benchmark
  runner gains `--json`/`--gate`/`--tol`/`--update-baseline` so detector accuracy (TPR/FPR/
  diagnosis) can be gated against a committed baseline in CI. `docs/VALIDATION.md`.
- **Outbound integrations** (`camber.integrate.notify` / `cmms` / `export`) — webhook, Slack/Teams,
  and email notifiers (severity filter + fingerprint dedupe), CMMS work-order rendering with a
  pluggable submit + idempotency, and findings/metrics export (CSV/Parquet/JSON). All opt-in and
  from the findings layer — never writing to the BAS. `docs/INTEGRATIONS.md`.
- **Interactive visualization MVP** (`camber.charts.readiness` / `multitrend` /
  `quality_dashboard`, `camber.report.dashboard`) — ingest-readiness ribbon, fault-annotated
  synchronized multi-trend, and a data-quality dashboard assembled into one self-contained HTML
  (matplotlib inlined; no web framework). `docs/VISUALIZATION.md`.
- **Online / streaming M&V** (`camber.mandv.online`) — `OnlineCusum` (incremental tabular CUSUM of
  savings/waste against a baseline model) and `RollingAnomaly` (rolling MAD-robust residual
  z-score); O(1) per sample. `docs/STREAMING.md`.
- **Online FDD** (`camber.rules.online.OnlineFDD`) — sliding trailing-window rule evaluation that
  emits a `Transition` only on a verdict change (no per-sample re-alert), with per-equipment
  isolation and the duck-typed rule protocol. `docs/STREAMING.md`.
- **Grid-interactive (GEB) analytics** (`camber.geb`) — `demand_response` (shed/rebound vs a
  baseline), `flexibility` (sheddable headroom), `carbon_aware_shift`, and `operation_score`
  (load-timing vs a price/carbon signal, rearrangement-inequality best/worst bounds). Advisory
  analytics; closed-loop DR remains a roadmap item. `docs/GEB.md`.
- **Hourly / marginal Scope-2 carbon** (`camber.carbon_hourly`) — `hourly_emissions` (time-varying
  factor → co2e, effective factor, timing premium) and `marginal_vs_average` (load-shift value uses
  marginal, reporting uses average). `docs/CARBON.md`.
- **Load forecasting + learned-normal anomalies** (`camber.forecast`) — `seasonal_forecast`
  (time-of-week shape + additive drift, no ML dependency), `backtest` (MAE/MAPE/CV(RMSE) honesty
  check), and `forecast_anomalies` (robust residual band → FDD signal). `docs/FORECAST.md`.
- **Persistent fault lifecycle** (`camber.faultlifecycle`) — a durable fault store keyed by the
  (site, equip, rule) fingerprint that survives across runs, with an assignment/status workflow,
  SLA/aging tracking, and atomic JSON persistence.
- **Plugin API** (`camber.plugins`) — third-party rules / ingest adapters / report formats
  discovered via Python entry points (`camber.rules` / `camber.adapters` / `camber.reports`) or
  registered in-process, duck-typed against the existing protocols with per-plugin error
  isolation. `docs/PLUGINS.md`.
- **Deployment references** — `deploy/k8s/camber-api.yaml` (namespace + read-only PVC + non-root
  2-replica Deployment + ClusterIP Service) and a `deploy/conda/meta.yaml` recipe skeleton; nothing
  is published. `docs/DEPLOY.md`.
- **Test hardening** — `camber.inventory` and `camber.io` now carry direct tests; a cross-capability
  `examples/geb_carbon_demo.py` wires GEB → carbon → forecast on synthetic data.

### Fixed

- `carbon_hourly.hourly_emissions` now reports `avg_factor` in the same unit as `effective_factor`
  on the `unit_kg_per_kwh=False` (g/kWh) path (previously left 1000× off; the timing premium was
  already correct).

## [0.1.1] — 2026-06-14

Documentation-only patch (no code or dependency changes).

### Added

- **`docs/CAPABILITIES.md`** — a single capability reference for everything in 0.1: what each
  capability does, its key API, the **option flags** that tune it, the module, and the standard
  it cites, grouped by layer (ingest · semantic model · FDD · SOO · M&V · RCx · money & compliance ·
  domain analytics · storage · reporting/integration/API · orchestration). Linked from the README.

## [0.1.0] — 2026-06-12

First public release.

### Added

- **Ingest** — per-point CSV, wide CSV, and a Project-Haystack `hisRead` client (wired through
  an injectable transport seam: `parse_his_grid` consumes a native typed-client Grid — object
  `.rows`, `datetime`/`Number` values — and `phable_transport` is the one-line hookup for a
  phable client; pyhaystack/any client via `client_transport`); per-point data-quality scoring
  with an auditable cleaning trail; valve/damper unit normalization (0–1 vs 0–100).
- **LBNL BETTER cross-check** — optional `[better]` extra (`camber.interop.better`):
  `compare_changepoint` runs CAMBER's change-point M&V and LBNL BETTER's analytical engine
  (`better-lbnl-os`) on the same monthly energy-vs-temperature series and reports
  model-order / baseload / R² agreement — corroborating a savings baseline with an
  independent engine. PySAM-style lazy import; core stays dependency-free.
- **pvlib bridge** — optional `[pv]` extra (`camber.interop.pvlib_bridge`, BSD-3):
  `poa_from_ghi` transposes horizontal irradiance (GHI/DNI/DHI) onto the array plane and
  `pvwatts_expected_kwh` applies a temperature-derated PVWatts yield — the solar-resource /
  cell-temperature modeling `camber.pv`'s flat-PR monitoring omits; `compare_expected` shows
  the temperature derate. Lazy import; core stays dependency-free.
- **PsychroLib bridge** — optional `[psychro]` extra (`camber.interop.psychro`, MIT): exact
  ASHRAE-formulation psychrometrics (`psychrometrics`: wet-bulb, dew point, humidity ratio,
  enthalpy) and `compare_wetbulb`, which validates CAMBER's dependency-free Stull wet-bulb
  against the exact value (~±1 °F). Lazy import; core stays dependency-free.
- **Network ingest adapters (read-only)** — Modbus TCP (`camber.ingest.modbus`, `[modbus]`/
  pymodbus — register snapshot + poll), MQTT/Sparkplug streaming (`camber.ingest.mqtt_stream`,
  `[mqtt]`/paho-mqtt — subscribe + buffer + shape), BACnet (`camber.ingest.bacnet`,
  `[bacnet]`/bacpypes3 — Trend-Log history + present values) incl. **experimental,
  certificate-gated BACnet/SC** (`wss://`+TLS, hub URI + operational cert config), and OPC-UA
  (`camber.ingest.opcua`, `[opcua]`/asyncua — history + current-value reads, secure-by-design
  `OpcUaSecurity`; asyncua's LGPL kept as a dynamic-only optional dep). Each is
  **read-only by construction** (a test parses the AST and fails on any write/command service),
  lazy-imports its protocol library behind an optional extra, and takes an injectable client so
  the data-shaping cores test without a network. New `docs/SECURITY.md` (NIST SP 800-82 /
  IEC 62443 threat model + posture) and `docs/INGEST-PROTOCOLS.md`. Historian/SQL/Haystack
  stays the recommended ingest path.
- **SQL/historian ingest** — `camber.ingest.sql`: `SqlSource` (a `SourceAdapter`) and
  `read_points` read a long/narrow point table (timestamp, point, value, optional unit +
  `WHERE`) over any PEP-249 DB-API connection into per-point Series — stdlib `sqlite3`,
  no new dependency.
- **Full Brick site-model interop** — `camber.interop.site_model`: `site_to_ttl` /
  `site_from_ttl` round-trip a whole Site→Equip→Point model (with relationships) to and
  from Brick Turtle, reusing the existing role↔Brick maps; minimal parser by default,
  rdflib optional — beyond the prior point→role mapping.
- **Sensor health / data-trust** — builds on the ingest quality stats with role-aware
  physical bounds (catching BAS error sentinels / unit-scaling blunders the robust
  outlier test misses), cross-sensor physical-consistency checks (e.g. mixed-air temp
  must lie between outdoor- and return-air temp), and a per-role trust roll-up with a
  `trusted_roles` gate — wired into the rule runner (and config `trust_gate`) so a rule
  whose required inputs aren't trusted declines to fire (an auditable `info` finding)
  instead of reporting a sensor problem as an equipment fault. Plus **sensor bias/drift
  detection vs a reference** (`camber.sensordrift`): bias, drift-per-month, and tracking
  correlation against an independent series — e.g. validating the outdoor-air (OAT/OSA)
  sensor against NASA POWER / a nearby station / a TMY series, which the BAS can't check
  on its own. And **point-mapping confidence** (`camber.mapping_confidence`): scores how
  surely each BAS tag resolved to its role (alias vs pattern match, ambiguity, and
  physical data-fit), flagging the low-confidence / ambiguous / unmapped tokens so
  onboarding review goes where it's needed.
- **Semantic model** — vendor-neutral `Role` vocabulary, `MappingProvider`, an
  entity model with equipment templates and completeness validation, and
  `resolve()` to assemble role-named frames.
- **FDD** — rule engine with ASHRAE Guideline 36 AFDD and PNNL Building Re-tuning
  diagnostics (simultaneous heat/cool, reheat, SAT/CHW reset, economizer, OA
  fraction incl. under-ventilation, boiler lockout, boiler short-cycling, HW-loop
  low-ΔT, overcooling, setback, static
  and pump resets, chiller efficiency (kW/ton), chiller staging/cycling, multi-chiller
  over-staging (fleet), cooling-tower approach, condenser-water reset, CHW/HW pump
  riding-the-curve + VFD-minimum, leaking valves); impact prioritization and fault
  lifecycle; an
  FDD-accuracy evaluation harness.
- **Fault economics** — `camber.fault_economics`: turns a fault into an estimated annual
  dollar impact so the prioritizer can rank by money, not just severity. Per-archetype models
  combine the rule's intensity metric (% of operating hours) with equipment sizing and
  documented, override-able assumptions — simultaneous-H/C & reheat gas (+ paired cooling),
  chiller kW/ton excess, cooling-tower approach penalty, pump riding-the-curve, duct-static
  fan waste, boiler short-cycle. `estimate_cost`/`cost_findings`/`total_cost`, `rank_by_cost`
  (dollar-first across severity) and `annotate_costs` (feeds `triage.rank_findings`). Every
  estimate carries its `basis` + `assumptions` and returns *uncosted* (naming the missing
  input) instead of fabricating when sizing is absent; triage-grade, distinct from the
  audit-grade M&V/ECM track.
- **RCx / MBCx** — `camber.rcx`: `functional_test` scores a Functional Performance Test
  from trend data (pass-rate over the intervals meeting an expected response),
  `before_after` is the monitoring-based-commissioning persistence check (did a measure's
  metric move across the intervention date, and significantly), and `track_measures` is a
  measure register grading each fix verified / regressed / inconclusive / insufficient.
  Cites ASHRAE Guideline 0/36.
- **Methods validation** — `camber.validation`: Wilson score confidence intervals on the
  FDD-accuracy rates (`metrics_with_ci` over `eval.Confusion`) so TPR/FPR/accuracy carry
  uncertainty, plus a `check_determinism` reproducibility harness; the LBNL benchmark
  publishes accuracy with CIs and `docs/VALIDATION.md` documents the methodology.
- **BPS compliance** — `camber.bps`: `site_eui` (per-fuel energy → kBtu/ft²/yr) and
  `emissions_intensity` (→ kgCO₂e/ft²/yr) compute the metric; `assess_bps` / `assess_eui`
  check it against a supplied Building-Performance-Standard limit (compliant?, margin,
  % of limit, over-amount, penalty exposure). Caller-supplies limits (no hard-coded legal
  values).
- **Sequence-of-Operations conformance** — a declarative clause engine (`camber.soo`):
  gated predicates over roles (`when <gate> then expect <predicate>`) that measure
  operated-vs-designed behavior per clause as a conformance %, with optional
  time-based persistence (forgive transient excursions), JSON-authorable
  (`examples/soo/`) and emitting Findings into the same prioritization/report/triage;
  ships a packaged ASHRAE Guideline 36 clause library (`camber.soo_library`); wired
  into config-driven runs via an optional `soo` section (library or JSON spec per class).
- **M&V retrofit isolation (IPMVP Option B)** — `camber.mandv.retrofit_isolation`: a generic
  `fit_driver_model` (affine least-squares `DriverModel` on a sub-metered system's *own*
  driver — runtime, load, cooling tons, production, or OAT; 1-D, multivariate, or constant)
  feeds `isolation_savings` (reporting-period avoided energy at the sub-meter boundary, with
  the ASHRAE G14 Annex-B fractional uncertainty and the baseline model-acceptance verdict) and
  `isolation_normalized_savings` (savings normalized to a fixed reference driver set). Reuses
  the existing G14 savings/uncertainty machinery at the narrower Option-B boundary — both are
  written against any `predict()`-able model.
- **M&V normalized savings** — `camber.mandv.normalized`: weather-**normalized annual
  savings** (project the baseline and reporting models onto a typical/normal year,
  difference their normalized annual consumption) with an ASHRAE G14 Annex-B uncertainty
  band — the IPMVP "normalized savings" complement to the existing avoided-energy use.
- **M&V** — change-point inverse models (2P–5P + heating/cooling-zero), the LBNL
  TOWT model, fit statistics with fractional savings uncertainty, CUSUM, weather
  normalization, and rate/energy-aware resampling.
- **IAQ / ventilation** — CO₂-based ventilation-adequacy diagnostic (`camber.iaq`):
  flags under-ventilation (elevated occupied CO₂, ~ASHRAE 62.1 ventilation-rate proxy)
  and over-ventilation (CO₂ near outdoor — a conditioning-energy penalty), differential
  to a measured or assumed outdoor CO₂; the air-quality companion to Std-55 comfort.
- **Tariffs / utility rates** — a native, dependency-free tariff engine (`camber.tariff`):
  bills an interval load against a URDB-shaped rate (fixed charge, TOU energy with tiered
  blocks + 12×24 weekday/weekend schedules, TOU and flat monthly demand, ratchet) into a
  per-month + annual cost breakdown. `camber.interop.openei` fetches and maps an OpenEI
  Utility Rate Database (URDB) rate (stdlib `urllib`, API key); an optional `[tariff]`
  extra bridges to NREL PySAM's `UtilityRate5` (`camber.interop.tariff_nrel`) for
  full-fidelity / cross-checking. Bill **recalculation/validation** (`validate_bill`)
  compares the recomputed bill to actual invoices month by month — validating the rate
  model and flagging over/under-billed months (MAPE + per-month high/low status).
- **ECM financials** — `camber.finance`: simple & discounted payback, NPV, IRR (hand-rolled
  bisection — no `numpy_financial`), and SIR for an energy-conservation measure from its
  cost and dollar savings, with savings escalation, annual O&M, and salvage.
- **Demand & peak analytics** — `camber.demand`: peak demand + its drivers (hour/day,
  coincident peak hour, how few intervals set it), load factor, baseload, a
  night/weekend **baseload-anomaly** check (unoccupied vs occupied load — equipment not
  setting back), and **peak-shave $ value** (demand charge recoverable by capping the
  monthly peak at a target).
- **Visualization** — three analytics-driven charts (`camber.charts`): a **load carpet**
  (`carpet`, hour-of-day × date heatmap exposing occupancy bands, weekend setback, and
  stuck-on days), a **CUSUM** savings/waste trajectory (`cusum_chart`, with optional control
  limits), and an **energy-signature** plot (`energy_signature`, energy-vs-temperature scatter
  with the fitted change-point model and balance point(s) overlaid). All draw onto a supplied
  Axes and lazy-import matplotlib, matching the existing chart convention.
- **Domain analytics** — Std-55 comfort (PMV/PPD), utility cost, carbon, water
  (irrigation budget, cooling tower, leak detection), load profiling, PV, lighting.
- **Storage** — Parquet time-series store (entity-keyed, hive-partitioned) with
  tag-filtered reads, rollups, and retention pruning. **Portfolio-scale tuning:** time-range
  reads prune `year` partitions (not just the `ts` column), `read_long` takes a `columns=`
  projection (so `points()` reads only the catalog and `read_role_frame` only ts/role/value),
  and `read_role_frame` uses a fast plain pivot when observations are unique. A **cached
  catalog** (`_catalog.json`, invalidate-on-write + rebuild-on-read) serves `points()` from an
  index instead of a partition scan (~22 ms warm) while keeping writes cheap; `rebuild_catalog()`
  materializes it for older stores. A synthetic generator + benchmark (`camber.store.bench`,
  `python -m camber.store.bench`) and [docs/SCALE.md](docs/SCALE.md) — a single-equipment read
  stays ~flat as the portfolio grows.
- **Interop** — Brick model import (derive role mappings) and Haystack/Brick export.
- **Integration & API** — findings → CMMS tickets with a pluggable notifier; a
  read-only HTTP API over the store.
- **Reporting** — ASHRAE/ACCA Standard 211 audit deliverables (text/HTML), and a
  **portfolio rollup** (`report.fleet`) that ranks a fleet by cross-sectional EUI
  benchmark, actionable-fault burden, and — when an `EnergyPrice` is supplied — estimated
  recoverable **dollars** per building (via `fault_economics`) with a fleet-wide total.
- **Examples** — runnable LBNL FDD and Building Data Genome 2 examples (public
  CC-BY datasets, fetched on demand), plus a data-free synthetic demo.
- **Distribution & Docker** — a multi-stage `Dockerfile` producing a **slim runtime image**
  (installed package + runtime deps only; non-root; healthcheck) that serves the read-only HTTP
  API over a mounted store, plus a `test` stage that proves the built wheel; a `docker compose`
  bundle (`api` / `tool` / `tests`); a release workflow that on a `vX.Y.Z` tag publishes to
  **PyPI via Trusted Publishing (OIDC, no stored token)** and pushes a **multi-arch image
  (amd64 + arm64) to GHCR**, then cuts a GitHub Release — all gated on the test suite; a
  `.devcontainer` for one-click contributor setup; and `DOCKER.md`. CI runs pytest on Python
  3.10 / 3.11.

[0.3.0]: https://github.com/yroussev/camber/releases
[0.2.0]: https://github.com/yroussev/camber/releases
[0.1.0]: https://github.com/yroussev/camber/releases
