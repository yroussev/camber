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

## Family roadmap

This is the first of the Trim-and-Respond family. The next items build on the same
`camber.g36_reset` engine:

- **Reset effectiveness** — compute the per-zone requests from the zone fleet
  (`cooling_sat_requests` / `static_pressure_requests`), run the expected T&R trajectory
  (`tr_simulate`), and compare it to the actual setpoint: is the reset actually trimming and
  responding, or stuck?
- **Rogue-zone census** — a fleet analytic that finds the one zone monopolizing the requests and
  dragging the whole reset (so the plant serves one bad box at everyone else's energy expense).
