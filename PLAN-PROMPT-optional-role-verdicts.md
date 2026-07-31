/plan Two related defects surfaced by running Camber 0.9.1 (commit d04597f) against a real building — the El Centro Courthouse, a 50%-outside-air VAV building in CA Climate Zone 15. Both are cases of a rule producing a **confident wrong verdict** instead of declining to answer. I want a plan before any code changes.

Read `CONTRIBUTING.md`, `ROADMAP.md` and recent `CHANGELOG.md` entries first, and match existing conventions (ruff lint+format gate, pytest, changelog entry).

---

## Defect 1 — `chw_plant_reset` reports a definite "no reset" when it simply could not evaluate

**Where:** `camber/chwplant.py::analyze_chw_plant`, surfaced by `camber/rules/chwplant_rule.py::CHWPlantReset`.

**Mechanism.** `OAT` is declared in `roles_optional`. It is the regressor for the reset slope. When it is absent:

```python
slope = float("nan")                     # no OAT column
reset_present = (not np.isnan(slope)) and abs(slope) >= chwst_reset_slope_flat
# -> False
```

`nan` collapses to a hard `False`. Three things then go wrong downstream, none of them visible to the caller:

1. `metrics["chwst_reset_present"] = False` — indistinguishable from a genuinely flat CHWST.
2. The summary string asserts `"flat CHWST (no reset)"` — an affirmative false claim in prose.
3. In `CHWPlantReset.analyze`, `elif low_dt >= 20.0 or not res.chwst_reset_present:` — a **missing optional input can raise severity to `warn`**.

**Reproduction.** Same plant, same data, only difference is whether the shared OAT frame is passed to `registry.run(...)`:

| shared OAT | `chwst_reset_present` | `chwst_slope_per_F` |
|---|---|---|
| not passed | `False` | `nan` |
| passed | `True` | `-0.135` |

The reset was working the whole time. I nearly published "the chilled-water reset is not resetting" in a client-facing engineering memo on the strength of the first run.

**This is a class, not an instance.** It is the same failure as the `DAMPER` role omission in an earlier overcooling driver, where dropping an optional role silently removed a condition from the `at_min` test and over-counted faults. In both cases an absent optional input changed a verdict rather than flagging itself. So I want the plan to cover the general case:

- Audit every rule in `camber/rules/` for booleans, severities, or summary text that an absent `roles_optional` role can silently flip. Report the full list with file/line before proposing fixes — I want to see the blast radius.
- Propose a repo-wide convention for "could not evaluate." Consider a tri-state (`True` / `False` / `None`) versus an explicit `metrics["<x>_evaluated"] = False` flag versus a `Finding.caveats: list[str]` field. Argue for one; note the migration cost for `Finding.as_dict()` consumers, the report layer (`camber/report/`), and any JSON output already in the wild.
- Whatever is chosen: a rule must never assert a negative it did not test, in metrics **or** in the summary string, and an unevaluated sub-check must not contribute to severity.
- Consider whether `Registry.run` should record which optional roles were resolved versus missing on every Finding, so this is visible without reading each rule's internals. That would make the whole class self-reporting.

## Defect 2 — `economizer_high_limit` defaults misfire on a high-outside-air design

**Where:** `camber/rules/economizer_lockout_rule.py::EconomizerHighLimit.__init__` — `high_limit_f: float = 65.0`, `min_damper: float = 0.25`.

**What happened.** All four AHUs returned `fault` at `not_locked_out_pct` 99.63–99.99% over 16,014 hours above the limit. The finding is wrong in substance:

- This building's *design* minimum outside air is 43–66% of supply (per the mechanical schedule), not 25%. Three of the four units sit near 50% damper on the hottest afternoons, which is approximately their design minimum — correct behaviour, reported as a fault.
- The fourth (AH-1-1, design minimum 66% OA) sits at ~24%, which is genuinely at minimum — also reported as a fault, for the opposite reason.
- A fixed 65 °F high limit is not right for CA CZ15 anyway; Title 24 sets the changeover by climate zone and economizer type.

So the rule's `min_damper` default encodes an assumption about building type that a 100%-OA, high-OA, or lab building violates, and the rule has no way to learn otherwise even though the data to do so is often present.

**What I'd like explored, without pre-committing to one:**

- Derive the minimum damper position from the data rather than a constant — e.g. a low percentile of damper position during occupied, non-economizing hours — and use the constant only as a fallback. State clearly how you'd avoid learning a *stuck* damper as the "minimum," since that is precisely the fault the rule exists to catch. This is the crux; if it can't be made robust, say so and prefer explicit config.
- When `Role.OA_AIRFLOW` and `Role.AIRFLOW` are both available, judge on **outside-air fraction** rather than damper position. Damper percentage is not linear in flow, so a damper threshold is a weak proxy for what the rule actually cares about. Note that this changes the rule's role requirements — decide whether that is a second code path or a replacement.
- Make `high_limit_f` settable, with a note on climate-zone-aware defaults. Don't build a Title 24 lookup table in this pass unless it is genuinely small; a config value plus documentation is fine.

**Blocker worth surfacing in the plan:** `camber/config.py` takes rules as a bare list of names (`"rules": ["simultaneous_heat_cool", ...]`) with **no per-rule parameter plumbing**. The rule classes accept constructor kwargs, but `builtin_registry()` instantiates them with defaults and a config-driven run has no way to reach them. So "make it configurable" is not currently reachable from the supported entry point. Include a design for per-rule params in the config schema — something like `{"rules": [{"name": "economizer_high_limit", "params": {"high_limit_f": 75, "min_damper": 0.45}}]}` alongside the existing bare-string form for backwards compatibility — and say whether that should land in this change or as a prerequisite.

---

## What I want out of the plan

1. The audit of Defect 1's blast radius across all rules, before any fix is designed.
2. A recommended convention for "not evaluated," with the alternatives you rejected and why.
3. A design for per-rule params in `config.py` that keeps the bare-string form working.
4. For Defect 2, a recommendation between data-derived minimum, OA-fraction-based judging, and config-only — with the failure mode of each stated plainly.
5. Test plan. Both defects need regression tests that would have caught them: for Defect 1, run a plant rule with and without the optional role and assert the verdict does not silently flip; for Defect 2, a fixture with a high-OA design that must not fault. Check whether `camber/synth.py` can generate these or whether new fixtures are needed.
6. Sequencing and a view on whether these are one change or two, and whether either is breaking for existing JSON consumers.

Flag anything where you think I've misread the code — I read these two rules and `config.py` but not the whole rules package, and the blast-radius audit may well change the shape of the right fix.
