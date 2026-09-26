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

## Cross-sensor and provenance checks

Each returns a `ConsistencyResult` (`check`, `n_checked`, `violation_frac`, `severity`, `summary`,
`metrics`, `caveats`).

| Function | Catches | Grade |
|---|---|---|
| `mixing_consistency(frame)` | MAT outside [min(OAT, RAT), max(OAT, RAT)] +/- 5 F | physics |
| `mixing_flow_consistency(frame)` | MAT biased against the flow-weighted OA/RA blend | screening (max `warn`) |
| `copied_signal_consistency(frame)` | two measured roles carrying identical data | hard evidence (`fault`) |
| `gapfill_signature(series)` | imputed / interpolated stretches, repeated days | screening (max `warn`) |
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
(both 0 while off) don't count. It cannot tell which of the two is the copy.

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
