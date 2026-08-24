# Trim-and-Respond / G36 reset compliance

The drift families ask "is this slowly getting worse?"; this family asks a control-sequence question:
"is the plant's **setpoint-reset logic** doing what ASHRAE Guideline-36 intends?" G36 replaces fixed
supply-air-temperature and duct-static setpoints with **reset schedules** — an OAT-driven SAT target,
and Trim-and-Respond (T&R) loops driven by per-zone *requests*. When that reset logic is missing,
mis-scheduled, or dominated by one rogue zone, the plant burns avoidable reheat and fan energy while
every instantaneous check still reads "in range."

CAMBER already ships the G36 primitives in `camber.g36_reset` (the OAT→SAT map `oat_sat_setpoint`,
the T&R loop `tr_step`/`tr_simulate` with the `SAT_TR`/`STATIC_TR` parameter presets, and the per-zone
request generators `cooling_sat_requests`/`static_pressure_requests`). This family wires them into
rules.

## The detectors

| Rule | Signal | Sided | Catches |
|---|---|---|---|
| `supply_air_reset_compliance` | supply-air temp vs the G36 OAT-reset target | up (too cold) | supply air held **colder than the G36 §5.16.2.2.b OAT→SAT target** — an avoidable reheat/energy opportunity |
| `sat_reset_effectiveness` | supply-air-temp **setpoint** vs the request-implied T&R trajectory | two-sided | a SAT reset that is **stuck**, **not responding**, **not trimming**, or **diverging** vs its own zone requests |
| `static_reset_effectiveness` | duct-static **setpoint** vs the request-implied T&R trajectory | two-sided | a duct-static reset that is **stuck**, **not responding**, **not trimming**, or **diverging** vs its own zone requests |
| `sat_rogue_zone_census` | per-zone SAT reset-request rate across the zone fleet | one-sided (monopolizer) | one chronically over-demanding zone **monopolizing the SAT requests** and dragging the reset colder than the fleet needs |
| `static_rogue_zone_census` | per-zone duct-static reset-request rate across the zone fleet | one-sided (monopolizer) | one chronically over-demanding zone **monopolizing the duct-static requests** and dragging the reset higher than the fleet needs |

**SAT-reset compliance vs the existing `supply_air_reset`.** These are complementary detectors on the
same signal. `supply_air_reset` (`camber.satreset`) asks the **shape** question — does supply air get
reset *upward at all* as cooling load drops (a positive slope)? `supply_air_reset_compliance` asks the
**target** question — is supply air held colder than the *specific* G36 OAT→SAT map would command? A
plant can have a healthy reset slope yet still sit below the G36 target, and vice-versa, so both are
worth running.

The G36 map (`oat_sat_setpoint`, §5.16.2.2.b) holds SAT at `min_clg_sat` (55 °F default) when it is
hot out (OAT ≥ `oat_max`, 70 °F) and resets it up toward `t_max` (65 °F) when it is cool (OAT ≤
`oat_min`, 60 °F). The rule compares actual SAT to that **OAT-computed** target — not to a mapped
`SUPPLY_AIR_TEMP_SP` — so it works on a typical trend export carrying only SAT and OAT. It is
**one-sided** (only SAT *colder* than the target is an opportunity; running warmer than `min_clg_sat`
is the energy-saving direction) and **warn-level** (an opportunity, not a hard fault), warning when SAT
runs below target `warn_pct` % of the hours **and** the mean gap clears `warn_gap_f` (so a trivially
small persistent gap does not flag). The four G36 map parameters are constructor arguments, so a site
can match its own reset schedule (`make_rule("supply_air_reset_compliance", oat_min=…, min_clg_sat=…)`).
Thresholds are screening / opportunity-grade (provisional-untuned). OAT is building-level and arrives
via the runner's `shared` channel; the rule declines loudly when it is unmapped.

## Reset effectiveness — is the reset actually trimming-and-responding?

`supply_air_reset_compliance` above asks whether SAT sits at the right *target*; **reset
effectiveness** asks whether the reset *mechanism* works at all. Given the plant's own aggregated
per-cycle **reset-request** count (`SAT_RESET_REQUESTS` / `STATIC_PRESSURE_REQUESTS`) alongside the
actual reset **setpoint** (`SUPPLY_AIR_TEMP_SP` / `DUCT_STATIC_SP`), the rule reconstructs the
setpoint that Trim-and-Respond *should* have produced (`tr_simulate`, G36 §5.1.14) and compares it to
the trended setpoint. It flags four modes:

- **stuck** — the setpoint barely moves while the request pattern would have moved it (a frozen or
  overridden reset);
- **not responding** — under sustained demand the setpoint sits at the energy-saving (trim) end
  instead of responding toward demand (zones starve);
- **not trimming** — while zones are idle the setpoint stays parked at the demand end (energy wasted);
- **diverges** — the setpoint moves the *opposite* way to the T&R command on most cycles (an inverted
  or mis-wired reset).

It ships as two instances — `sat_reset_effectiveness` (supply-air-temp setpoint, °F, `SAT_TR` preset)
and `static_reset_effectiveness` (duct-static setpoint, in. w.c., `STATIC_TR` preset) — sharing one
reset-agnostic `camber.g36_reset.reset_effectiveness` analyzer. It is **two-sided** (both starving and
wasting are faults) and **warn-level** (an operational opportunity, not a hard equipment fault).
Unlike the compliance rule it needs the reset **setpoint and the request count** both mapped, so it
declines loudly on the common trend export that carries no request point. The reconstructed-trajectory
error (`mean_abs_error_sp`) is **informational only** — a coarser trend cadence than the controller
inflates it, so the verdict rests on the cadence-robust mode detectors above, not the raw error.
Thresholds are screening / opportunity-grade (provisional-untuned).

## Rogue-zone census — which zone is dragging the reset?

The two rules above look at the reset *setpoint*; this one looks at the *demand side* that drives it.
In G36 the SAT / duct-static reset responds to the **high-percentile of per-zone requests** (§5.14.8),
so **one chronically over-demanding zone can monopolize the requests and drag the whole reset** —
forcing a colder supply-air temperature or higher duct static than the rest of the fleet needs. The
plant then serves one bad box at everyone else's energy expense.

`sat_rogue_zone_census` and `static_rogue_zone_census` are **fleet** rules (many zones in, one
aggregate Finding out). Across the zone fleet each computes every zone's per-cycle request series — a
vectorized form of `cooling_sat_requests` / `static_pressure_requests` — then scores each zone by its
**share** of the group's total requests and the **fraction of active cycles on which it holds the
binding (maximum) request**. A zone is flagged as a **rogue** when it both holds the binding request a
dominant fraction of the time *and* commands a disproportionate share of the requests. Requiring both
is what keeps a uniformly-busy fleet quiet: when every zone is equally hot they all tie at the binding
max, but their shares are equal, so none is singled out.

**Topology honesty (auto-scoping).** Deciding that a zone drags *AHU-1's* reset requires knowing
which zones AHU-1 serves. The census now scopes **per air handler automatically** whenever a
served-by [topology](TOPOLOGY.md) is available, and the caveat it attaches reflects how trustworthy
that grouping is:

- **Semantic topology** (from a Brick `feeds` / Haystack `ahuRef` model, passed as
  `run_fleet(..., topology=site.topology)`) — the census scopes per air handler (a rogue is compared
  only to its siblings, in `rogue_by_group`) and the confound caveat **drops**.
- **Naming-heuristic topology** — when no semantic model is supplied, `Registry.run_fleet`
  **auto-builds** a grouping from the equipment ids themselves (`AHU_1_VAV_3` under `AHU_1`), so the
  census scopes per-AHU even with no model — but keeps a **softened screening caveat** because the
  grouping is inferred, not verified.
- **Partial coverage** — zones the topology does not cover are pooled together (building-wide
  fallback) and that remainder is caveated; the covered zones are still scoped.
- **No topology** — the original **building-wide** pool with the full confound caveat (a zone flagged
  in the single pool may simply serve a hotter air handler, so it is a **screening signal only**).

The grouping and its provenance are recorded in the finding's metrics (`grouped`,
`grouping_provenance`, `n_zones_ungrouped`). The census also declines loudly — demoting a clean "ok"
to `info` — when zones were unevaluable or no zone generated any request (the reset is not
demand-bound, so nothing can be dragging it). Thresholds are screening / opportunity-grade
(provisional-untuned).

## Family complete

With the rogue-zone census the Trim-and-Respond / G36-reset family is complete: **SAT-reset
compliance** (does the reset sit at the right target?), **reset effectiveness** for SAT and static
(does the reset actually trim and respond?), and the **rogue-zone census** for SAT and static (is one
zone dragging the reset?) — all five detectors built on the shared `camber.g36_reset` engine
(`oat_sat_setpoint`, `tr_step` / `tr_simulate`, `cooling_sat_requests` / `static_pressure_requests`).
The one remaining enhancement is **automatic zone→AHU discovery**: the census already *accepts* a
grouping, but the fleet runner does not yet *derive* one from the site model, so per-AHU scoping is
opt-in until that topology channel exists.
