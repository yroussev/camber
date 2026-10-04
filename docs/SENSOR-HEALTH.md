# Sensor health & data trust (`camber.sensorhealth`, `camber.sensordrift`)

A sensor fault is not an equipment fault. A drifting, stuck, copied or gap-filled point makes a
healthy unit *look* broken, or hides a real fault. The sensor-health layer scores how far each point
can be trusted, runs cross-sensor physics checks, and lets the rule runner **decline** to diagnose
on inputs it cannot trust (`min_trust`). Everything is pandas + numpy over public physical
reasoning.

Every check returns what it could not evaluate as `None` / an `info` severity plus a caveat, never
as "clean" (the honesty convention in `camber/rules/base.py`).

## Per-point trust: `sensor_trust`, `frame_sensor_health`, `untrusted_roles`

`sensor_trust(series, role)` combines coverage, gaps, flatline, robust outliers and the role's
`PHYSICAL_BOUNDS` into a 0-1 trust and a verdict (`trusted` >= 0.8, `suspect` >= 0.5, else
`untrusted`). `untrusted_roles(frame, roles, min_trust=...)` is what the runner's trust gate calls.

**Gated mode.** `sensor_trust(series, role, gate=fan_on)` (or `frame_sensor_health(frame,
gate="fan")`) reads the flatline -- the `stuck` flag and its share of the score -- on the gated
samples only. It applies to the roles whose reading depends on moving air: duct air temperatures,
airflow, static and duct humidity (`FAN_GATED_ROLES`). A supply-air sensor that settles to one value
while the unit is off is then not called stuck, and a run is broken wherever the fan stops. Coverage,
range and outlier reads are unchanged, and an OAT or space temperature is never gated. The default
(`gate=None`) is unchanged, so the runner's trust gate behaves exactly as before (apart from the
0.90.1 checks below). The
[RCx report](RCX-REPORT.md) shows both reads. `schedules.fan_on_mask(frame)` picks the gate: fan
status, else fan speed, else airflow, else "ungated — no fan signal".

**Plant run gate (0.92, #66; provisional).** A chiller that is off lets its chilled-water supply
drift to the plant-room temperature and hold there for days; a boiler that is off lets its loop
cool. Read ungated, that is a `stuck` or `out_of_range` sensor, and the RCx report made every plant
finding that leans on it *conditional*. `frame_sensor_health(frame, plant_gate="auto")` now judges
the plant roles (`PLANT_GATED_ROLES`: chilled- and condenser-water temperatures, the refrigerant
approaches, subcooling and superheat for a chiller; hot-water supply and return for a boiler) on
their equipment's **running** samples: range, outliers, flatline and stuck runs are read over
running samples only, coverage over the whole span. `schedules.plant_run_mask(frame, loop)` picks
the gate:

| Loop | Gate, strongest first |
|---|---|
| `"chw"` | the chiller's run status / command (`compressor_status`) > 0.5; else its `power` above a tenth of its own 95th percentile (only on a frame with a chilled-water role and no supply-fan signal) |
| `"hw"` | the boiler's status (`boiler_status`) > 0.5; else its gas input (`gas_input_rate`) above 5 % of its own 95th percentile |

The first 30 minutes after each start are left out (a loop pulling down from standby is not a
sensor fault), and a value held across successive running stretches still counts as one stuck run
(the off time between them is not added). A chiller's `power` is gated too when the gate comes from
its status. A point whose equipment ran for fewer than 24 samples is flagged `not_running` and
scored on coverage alone. The running samples are read regime-aware: a "status" that is really an
enable (a boiler on through a mild month with the loop cold) splits them in two, which is flagged
`bimodal` rather than read as outliers. The all-points-frozen check ignores intervals the plant was
off throughout. The runner's trust gate (`untrusted_roles`) and the RCx report apply the plant gate;
`frame_sensor_health` keeps `plant_gate=None` as its default. `SensorTrust.run_gate` names the gate
a point was judged on.

**Outliers are read shape-aware.** The plain robust test (median / MAD modified z-score) assumes
one tight population. On a healthy plant that assumption fails two ways:

| What the point does | Why the plain test misfires | What the shape-aware read does |
|---|---|---|
| Held at setpoint (loop DP, HW supply temp) | MAD is ~0, so float rounding and 1 F swings score as outliers | floors the robust scale at the role's measurement precision |
| Idles at minimum for half the year, then ramps with load (HW pump speed, loop flow) | one *skewed* population; all load operation reads as outliers against the idle mode | scores each side of the median against its own MAD (double MAD) |

The precision floor is 0.5 F for temperatures, 1 %-pt for percent roles, 30 ppm for CO2, and 0.5 %
of the series' 99th-percentile magnitude for roles in non-canonical units (flows, pressures, power).
The two-sided scale has a lower breakdown point (it would absorb a 30 % scattered rail to zero), so
the points it excuses must be **temporally coherent** -- the same run-length gate the duty-cycle
regime split uses. Load operation persists for hours; a comms dropout scatters. If the excused
points scatter, the read falls back to the floored pooled test. The pooled `outlier_frac` is always
reported unmasked; `QualityReport.shape_outlier_frac` carries the shape-aware read.

On the fault-free LBNL boiler-plant simulation year this moved HW flow 0.28 -> 0.94, pump speed
0.18 -> 0.92, loop DP 0.21 -> 0.97 and return temp 0.38 -> 0.99, so the loop-dT drift detector no
longer declines on its own healthy inputs. The scattered-rail, spike and error-sentinel regression
tests still fail trust.

Two information flags (no trust penalty):

- `scale_suspect` -- an airflow role whose data is bounded to 0-100 (see below);
- `below_ambient` -- a zone CO2 point reading under 380 ppm on more than 5 % of samples: below any
  outdoor background, so its calibration (often NDIR automatic baseline calibration) is suspect.

**Stuck by duration, not by share (0.90.1).** The flatline read above is the longest identical
run *as a share of the series*, so a long series dilutes a real outage: a zone temperature pinned
for 12 days of a year is 4 % of it. Every analog role in `STUCK_HOURS` is therefore also judged on
the **absolute duration** of its runs -- 24 h for temperatures, humidity, flows, static, pump head
and power, 48 h for CO2 (an empty building over a weekend). A run longer than the limit sets the
`stuck` flag, is listed in `stuck_intervals` (`start`, `end`, `hours`, `value`), and caps the trust
at "suspect" (0.75, less the stuck share of the samples); `longest_flat_hours` is always reported.
Flows, static, pump head and power legitimately sit at their "off" value for a weekend, so a run
at or below 2 % of the series' 99th-percentile magnitude is never counted. The fan-dependent roles
(`FAN_GATED_ROLES`) are judged on fan-on stretches only, in the gated mode: without a gate a duct
temperature holding still while the fan is off can't be told from a stuck one. Override a limit
with `sensor_trust(..., stuck_hours={Role.SPACE_TEMP: 12})`.

**Coverage over the point's own span.** A point that was added part-way through the window is not
a low-coverage point: coverage is judged from its first valid sample (`first_valid`), the point is
flagged `late_start`, and `window_coverage` keeps the whole-window figure. A point that stops
reporting part-way is still `low_coverage`.

**Binary points.** A status point reports `n_state_changes`. With no change over 14 days or more it
is flagged `never_changes`; for a supply-fan status that caps it at "suspect" (a dead point, or a
unit nobody runs -- both worth a look). Pumps, compressors and boilers sit off for a whole season,
so for them the flag carries no penalty. A status holding values between 0 and 1 on more than 5 %
of samples at a native rate of 15 minutes or finer is flagged `fractional_status` (interpolated,
not logged) and is "suspect"; on a coarser grid those fractions are the duty resample and are fine.

**Frame-level checks (`frame_checks`).** `frame_sensor_health` and the runner's trust gate
(`untrusted_roles`) also judge the points of one equipment against each other. Each records a
`frame_checks` entry on the points it marks and caps them at "suspect":

- `implausible_fan_off` -- duct static above 0.5 in.w.c. (filter DP above 0.3) on more than half of
  the samples where the supply fan is clearly off (status and speed agree). The transmitter is
  offset, mis-scaled or mapped to the wrong point.
- `status_speed_mismatch` -- the fan status reads off (<= 0.05) while the drive runs above 20 %, or on
  (>= 0.95) with the drive stopped, on at least 1 % (and 3) of the samples. The status is capped; the
  speed is flagged only.
- `all_points_frozen` -- every varying analog sensor on the unit (at least two) holds its value at
  the same time for 12 h or more: a forward-filled collection outage. Any other point that held
  still through the same intervals is marked too.

Since 0.92 (#16) two cross-sensor checks below feed trust as well:

- `copied_signal` -- a measured point carries another measured point's data
  (`copied_signal_consistency` finds the pair; every identical stretch of it long enough to flag is
  then collected). The **copy** is told from the original at the stretch's edges: the original
  keeps measuring its own quantity, while the copy jumps across the gap between the two to the
  other's level (a return air suddenly reading supply-air temperatures). The point that closes at
  least half of that gap, and moves at least twice as far as the other, is the copy (`blame:
  "level_shift"`): its trust is scaled by the share of its samples inside the copied stretches and
  capped at "suspect", so a copy covering most of the window reads "untrusted". When the check
  cannot tell (no data around the stretch, or both moved), both points are flagged
  (`blame: "undetermined"`) and capped at "suspect", unscaled.
- `mixing_balance` -- the mixed-air temperature fails the flow-weighted OA/RA balance
  (`mixing_flow_consistency` at `warn`; it needs MAT, OAT, RAT, `OA_AIRFLOW` and `AIRFLOW` in the
  frame). MAT is capped at "suspect" -- a single-point sensor in a stratified plenum is the usual
  culprit, and the balance is screening-grade, so it never makes a point "untrusted" on its own.
  OAT and RAT are flagged with the same check but keep their scores: the balance cannot say which
  of the set is wrong.

Both flags make the unit's findings on that point **conditional** in triage
(`camber.rules.triage.sensor_causes`), on that unit only -- even on OAT, since the check judged this
unit's own points. The rule runner's trust gate resolves only a rule's own roles, so the flow balance
applies there only when the rule loads both flows; the RCx report scores the whole frame.

On the open LBNL Building 59 data (catalog example `lbnl-b59`, 2018-2020, hourly): RTU01 and RTU02's
MAT reads +4.7 F and +3.1 F against the balance and drop from "trusted" (0.96 / 0.95) to "suspect"
(0.75); RTU04's return air, a copy of its supply air from September 2019, is identified as the copy
and drops from 0.98 to 0.56 over the three years, while the supply air keeps its score. Of 10
actionable issues from the per-unit rules on the four units, 3 become conditional: RTU04's
economizer high-limit finding (on the copied return air) and RTU01/RTU02's SAT-reset compliance
(on OAT, which the failing balance also names). On 2020 alone the copy covers the whole window, so
both points are flagged "undetermined".

### Operating modes, stray rows and clipped readings (0.98, #87; provisional)

**Outliers read per operating mode.** The shape-aware read still assumes one population per
point. A supply air held to a fraction of a degree while the unit conditions, and anywhere between
plenum and coil temperature while it idles, is two populations: every idle hour reads as a robust
outlier. `sensor_trust(series, role, mode=labels)` judges the shape-aware outliers within each
operating mode (`labels` names each sample's mode; a mode with fewer than 24 samples, and
unlabelled samples, keep the whole-series read) and rebuilds the score from its parts:
coverage x (1 - min(2 x outliers, 1)) x (1 - 0.2 x flatline) x the range term. It applies only to
the fan-dependent roles (`FAN_GATED_ROLES`, less the duty-cycled airflows, which have the
two-regime read already) and never when a plant run gate applies. `SensorTrust.mode_outlier_frac`
carries the per-mode share and `mode_source` names the split; the pooled `outlier_frac` keeps its
meaning.

`frame_sensor_health(frame, mode="auto")` infers the modes only for a unit with **no fan signal**:
a sample is "off" when the OA damper is at or below 2 % and every trended coil valve at or below
1 % (the damper and at least one valve are needed, 24 samples in each mode, and an off share of at
most 60 %). A unit with a fan signal keeps the pooled read under `"auto"`; pass its fan mask as
`mode` to read per fan mode anyway. The [RCx report](RCX-REPORT.md) passes `mode="auto"`, so a
fan-less unit now shows a gated column, and *Gate used* adds "outliers read per mode: inferred
off-mode (OA damper and coil valves closed)".

On `irish-ahu` (catalog example, hourly, no fan point) the supply air goes from *untrusted* 0.19 to
*trusted* 0.89 in the RCx table (per-mode outlier share 4.1 %). No fan-gated unit changes.

*Measured and rejected: per fan mode on every gated unit.* Reading the gated units' outliers per
fan on / off as well was measured on every catalog RCx report: 175 of 511 trust-table rows moved,
by up to -0.34 (an LBNL single-duct AHU's duct static, *trusted* 1.00 -> *suspect* 0.66), with five
verdict changes in both directions on the LBNL and ORNL data. A fan-off reading is not one
population either (it drifts from coil to plenum temperature as the unit coasts), so splitting it
out moves healthy points as much as it fixes anything. *Also rejected:* reading supply air relative
to its setpoint, which scored worse on every unit tried.

*Measured and rejected (0.100, #103): the setpoint level as a grouping key.* The alternative to the
residual read is to judge a controlled point's outliers within each level of its setpoint, and to do
so only when the setpoint takes few distinct values (at most 4, compared at 0.01). It was measured
for supply air and duct static on every catalog RCx report (511 trust-table rows):

| Grouping | Rows moved | Direction |
|---|---|---|
| At most 4 distinct setpoint values (the proposed rule) | 0 | none: no catalog unit qualifies |
| At most 4 levels holding at least 95 % of the samples | 0 | none |
| The 4 most common levels, other samples read pooled | 5 | all down |
| As above, fan-on samples only | 5 | all down |

No catalog setpoint has two to four levels. The LBNL single-duct runs hold one constant supply-air
(55.18 °F) and static setpoint, which leaves nothing to group. `lbnl-b59`'s four rooftop units
(414-493 distinct values) and `nuig-ahu101` (576) reset theirs. `irish-ahu` and the LBNL dual-duct
and fan-powered units trend no supply-air or static setpoint. The rule as proposed therefore
changes nothing that real data can check. Grouping by the four most common levels reaches
`lbnl-b59` and `nuig-ahu101`, where it covers 74-88 % of the samples. There it lowers every supply
air it touches: `lbnl-b59`'s by 0.07-0.12, with RTU03 (0.88 -> 0.77) and RTU04 (0.84 -> 0.74) going
from *trusted* to *suspect*, and `nuig-ahu101`'s by 0.01-0.03. The supply air holds within
0.07-0.14 °F of its setpoint at the median (fan on). Within one level, the robust scale therefore
falls to the 0.5 °F precision floor, and the ordinary excursions after a load or setpoint change
(90th percentile 1.4-4.7 °F) read as outliers. The pooled read absorbs them in the spread across
levels. This is the same mechanism that made the residual read worse. Neither form is adopted, and
`mode="auto"` does not group by setpoint (`tests/test_sensorhealth_103.py` pins this).

**Stray lead and tail rows.** A few rows logged long before (or after) a point's real record make
its coverage over its own span low, though the record itself is complete. When a point's longest
gap is at least 30 days and 20 % of its span, and the rows on one side of it are at most 1 % of its
samples, those rows are left out: the point is flagged `stray_lead` (or `stray_tail`) and judged on
its main span (`main_start`, `main_end`; `n_stray` rows left out). `first_valid` and
`window_coverage` keep their meaning. Of 1,326 hourly series in the catalog store this applies only
to `irish-ahu`'s five points that logged 4 rows in January 2015, 904 days before the record starts
on 2017-06-23 (their coverage 0.57-0.67 -> 0.97).

**Clipped at a range limit.** A transmitter at full scale reports its limit, not the quantity: the
reading is a bound. `clipped_at_limit(series, role)` finds a pile-up at the series' maximum (or
minimum): at least 12 samples and 0.5 % of them within 0.1 % of the value span of the extreme, at
ten times the density of the adjacent 5 % band, **and** the extreme is a round number to within
0.01 % (2 significant figures; temperatures in degF or degC). The last test separates a configured
full scale (2000 ppm read as 1999.9985, 20000 cfm read as 19999) from a fan or flow at its design
maximum, which lands only near one (a simulated supply fan topping out at 3397 cfm is 0.08 % from
3400 and is not flagged). It checks both ends of CO2, outdoor CO2, OAT, wet bulb, space and return
air, and the high end of airflow, OA airflow, chilled- and hot-water flow and duct static (their low
end is "off"). Supply air, condenser water and humidity are not checked: a controller's own limits
pile up at round numbers too. `frame_checks` flags the point `clipped`, with
`SensorTrust.clipped = {"side", "limit", "limit_label", "n", "frac", "frac_fan_off"}`
(`frac_fan_off`: the share of the clipped samples with the supply fan clearly off, `None` without a
fan signal). The flag carries no trust penalty. For a fan-dependent point (return air, airflow,
OA airflow, duct static) on a unit with a fan signal, `frame_checks` also requires the pile-up on
the samples with the fan not clearly off: a BAS or a gap-fill that holds a round constant while the
fan is off (a return air parked at 70.0 °F) piles up like a range limit but is not one. CO2 and the
outdoor and space points are judged on every sample, since a CO2 transmitter in a closed room
topping out overnight is a real clip.

On the catalog store (hourly) it fires on four points: `nuig-ahu101`'s room CO2 at 2,000 ppm (97
hours, every one with the fan off), `lbnl-b59` RTU01's and RTU04's OA flow at 20,000 cfm, and the
LBNL dual-duct `DMPRStuck_OA_0` run's return air at 140 °F, where the simulation pins every air
temperature at its 140 °F bound during the fault (already flagged `out_of_range`). All four
still fire with the fan-on re-check (the three fan-dependent ones have the fan on).

## Cross-sensor and provenance checks

Each returns a `ConsistencyResult` (`check`, `n_checked`, `violation_frac`, `severity`, `summary`,
`metrics`, `caveats`).

| Function | Catches | Grade |
|---|---|---|
| `mixing_consistency(frame)` | MAT outside [min(OAT, RAT), max(OAT, RAT)] +/- 5 F | physics |
| `mixing_flow_consistency(frame)` | MAT biased against the flow-weighted OA/RA blend | screening (max `warn`) |
| `copied_signal_consistency(frame)` | two measured roles carrying identical data | hard evidence (`fault`) |
| `gapfill_signature(series, role=None)` | imputed / interpolated stretches, repeated days | screening (max `warn`) |
| `cross_unit_identity(series_by_equip, role)` | one role implausibly identical across units | screening (max `warn`) |
| `co2_outdoor_consistency(frame)` | zone CO2 below outdoor CO2 | physics |
| `percent_scale_suspect(series, role)` | a 0-100 % (or 0-1) signal mapped to a cfm role | screening (bool / `None`) |

### Flow-based mixing balance

`mixing_consistency` only asks whether MAT lies between OAT and RAT, and a sensor reading several
degrees warm usually still does. With `OA_AIRFLOW` and supply `AIRFLOW` mapped,
`mixing_flow_consistency` uses the energy balance: with `f = OA / SA`,
`expected MAT = f * OAT + (1 - f) * RAT`, and the median of `MAT - expected` is the bias. It uses
running samples (supply flow >= 20 % of its P95) with `0 <= f <= 1` and `|OAT - RAT| >= 10 F`, and
reports the colder- and warmer-half biases (stratification shows as a bias that grows in the cold)
plus the share of samples with OA > SA.

The flow stations' own accuracy is unknown, so a +/-10 % uncertainty on `f` is propagated into an
expected-MAT band and severity is `warn` only past that band plus 2 F -- never `fault`. The bias
belongs to the whole set: an OAT sensor reading low pulls the expected MAT low by `f` times its
error, so validate OAT with `compare_to_reference` first. If a mixing input is a copy of another
point, the check declines.

### Copied points

A return-air sensor reading bit-for-bit what the supply-air sensor reads, sample after sample while
both change, is not measuring return air. Two sensors on different quantities cannot agree exactly
by coincidence, so `copied_signal_consistency` counts consecutive *changing* samples where two
measured roles are equal; a run of 24 (a day of hourly data) is a `fault`. Co-flat stretches
(both 0 while off) don't count. It cannot tell which of the two is the copy on its own; the trust
check above (0.92) tells them apart at the stretch's edges when it can.

### Gap-filled data

Filled data can be plausible in range and shape, so no single-series statistic sees it.
`gapfill_signature` looks for two physical fingerprints on the **raw** trend:

- **a change in value granularity** -- a real sensor's reports repeat at its resolution (in a month
  most samples repeat a value seen elsewhere that month); interpolated, imputed or model-filled
  data is continuous (every value unique). Windows that switch class mark two different producing
  processes, and the continuous stretch is the suspect one;
- **repeated days** -- a varying measurement never reproduces a whole day exactly.

Resampled means erase the granularity fingerprint: if every window is continuous the result says
"not evaluable" rather than clean. A granularity change can also be a trend reconfiguration or a
sensor swap, so this is screening-grade.

**Scheduled points (0.98, #87).** A fan or a status on a fixed weekday schedule repeats whole days
exactly, and that is the schedule doing its job. For a *stepwise* point (a status role passed as
`role`, or any series with at least 95 % of its samples on at most two levels) days that share one
pattern with at least two other days are reported as scheduled: the summary says "N days follow a
fixed schedule", the metrics carry `scheduled_days` and `n_schedule_patterns`, and they do not warn.
`repeated_days` keeps only the repeats no shared pattern explains. On `nuig-ahu101`'s fan status
(15-minute grid) 191 days follow 12 patterns; 3 pairs of days remain unexplained and still warn.
An analog series is never excused this way.

`cross_unit_identity` complements it when the same role exists on several units: per 30-day window
of hourly means, a pairwise `r >= 0.995` is more agreement than independent measurement allows, and
is the signature of a shared, copied or filled source (a matrix-factorisation fill reconstructs
every unit from the same few factors). Units under one shared command can legitimately track each
other, so it is screening-grade; roles measuring shared ambient air (OAT, outdoor RH/wet-bulb/CO2)
and commanded roles (positions, speeds) are not evaluated.

On an open office dataset whose documentation says small and large gaps were filled by
interpolation and matrix factorisation, the four rooftop units' outdoor-air flow stations scored
"trusted" 0.95-0.96 over the filled stretch. `gapfill_signature` on the raw 1-minute trend flags 27
of 37 monthly windows as continuous (repeat share ~0.01) against ~0.93 once the real sensor
data starts, and `cross_unit_identity` flags the same stretch (pairwise r 0.998-0.9997; 0.72-0.90
afterwards). It also flags the units' supply-air flows in some windows. Those are real, quantised
data from fans driven at one common speed, which is the false positive the caveat describes.

### Percent points mapped as airflow

Physical bounds cannot see a scale error inside the range: a fan-speed percent mapped to a cfm
airflow role is well inside any airflow bound. A cfm series that never exceeds 100 over a whole
record is below the design flow of the smallest commercial VAV terminal, so
`percent_scale_suspect` flags it, `sensor_trust` adds `scale_suspect`, and
`mapping_confidence.score_token` adds `scale_suspect`, cuts confidence by 0.6 and routes the token
to `review()["needs_review"]`. A very small box trended in L/s would also trip it. `OA_AIRFLOW` now
also has physical bounds, like `AIRFLOW`.

### Indoor CO2 below outdoor

A building has no CO2 sink: zone CO2 decays towards outdoor when a space empties and rises above it
when occupied. `co2_outdoor_consistency` counts samples where the zone is below outdoor by more than
the two NDIR sensors' combined accuracy (`sqrt(2) * (30 ppm + 3 %)`, ~60 ppm), judged on occupied
samples when `OCCUPANCY` is mapped (else all samples, with a caveat), `warn` at 5 % and `fault` at
20 %. On the occupied basis a *median* at or below outdoor is at least `warn` on its own: that is a
calibration offset between the pair even when each sample is within spec. It cannot say which
sensor is wrong. An indoor sensor with automatic baseline calibration pins its floor near 400 ppm
under a local ambient that is often 420-480 ppm, or the outdoor sensor reads high.

## Bias & drift against a reference: `compare_to_reference`

`sensordrift.compare_to_reference(series, reference)` reports **bias** (median offset), **drift**
(a per-month trend in the offset) and **tracking** (correlation). Use it for a site OAT sensor
against a weather reference ([WEATHER.md](WEATHER.md)), a redundant sensor, or a sister unit.

- **Drift needs a span.** It is evaluated only when the overlap spans `min_drift_days` (28) and
  yields `min_drift_baselines` (14) daily baselines; otherwise `drift_per_month` is `None` with a
  caveat. A per-month rate from three days of data is an extrapolation of the diurnal swing, not
  drift.
- **Drift is robust.** Each day contributes one baseline, the median offset that day (over
  `baseline_hours` only when given, e.g. `range(1, 5)` for unoccupied nights on a CO2 pair), and the
  rate is the Theil-Sen slope through the baselines.
- **The verdict leads with the worst issue.** Every issue at warn or above is listed, most severe
  first; at equal severity a measured bias precedes an extrapolated drift rate. A correlation below
  `min_correlation` still dominates, because then bias and drift mean nothing.

`DriftResult` also carries `span_days`, `n_drift_days` and `caveats`; `drift_finding` passes the
caveats through to its `Finding`.
