# IPMVP Option D — calibrated simulation

CAMBER's other M&V options are **inverse** models: they regress measured energy on temperature
(change-point, TOWT, degree-day) and can only report savings *after* a measure is implemented. **Option
D** answers the *counterfactual* — "what would this building use if we fixed the control?" — with a
**forward** building model calibrated to metered data, then run under the corrected control. `camber.mandv.rc_model`
is a dependency-light, clean-room implementation (numpy only; ISO 13790 simple-hourly / ASHRAE
inverse-modeling lineage). It completes the IPMVP set: **A, B, C, and now D** all ship.

## The model — a 1R1C grey box

`RCModel(ua_eff, gain_eff, tau)` is a single-zone, single-time-constant thermal model:

- `ua_eff` — effective conductance (metered energy per °F of setpoint–OAT gap per hour),
- `gain_eff` — effective internal + solar gain offset (metered energy per conditioned hour),
- `tau` — the free-float / recovery time-constant (hours).

`model.predict(oat, schedule)` returns the **hourly HVAC energy** under a control schedule. During
conditioned hours the zone is held at setpoint (energy ∝ `ua_eff·(setpoint − OAT) − gain_eff`); during
setback it free-floats toward outdoor air with time-constant `tau`, and re-entry adds recovery
degree-hours. Because it takes a *schedule* as input, the same calibrated model can be re-run under a
different (as-corrected) control — that's the Option-D capability the inverse models lack.

Build a schedule with `daily_schedule(index, occ_setpoint=…, setback_setpoint=…, occ_start=…, occ_end=…)`
(or pass your own `{"setpoint": …, "conditioned": …}` arrays).

**Identifiability:** energy alone can't separate conductance from HVAC efficiency, so the calibrated
parameters are the **effective** combinations — enough for the counterfactual, which only re-runs the
schedule.

## Calibration — grid τ, OLS the rest, gate on G14

```python
from camber.mandv.rc_model import calibrate

cal = calibrate(oat, schedule, metered_energy)  # -> Calibration(model, fit, accept)
cal.accept  # met the ASHRAE Guideline 14 acceptance gate?
cal.fit.cv_rmse  # hourly CV(RMSE)
```

Calibration mirrors the change-point fitter (`camber.mandv.models`): the single nonlinear parameter
`tau` is **grid-searched** (coarse then refined), and for each `tau` the linear `ua_eff`/`gain_eff` are
solved by **OLS** — no scipy. Acceptance is the existing G14 gate (`stats.fit_stats` +
`cv_rmse_max_for("hourly")` = CV(RMSE) ≤ 30%). Calibration is deterministic
(`validation.check_determinism`).

## Modeled savings — as-found vs as-corrected

```python
from camber.mandv.rc_model import option_d_savings

sv = option_d_savings(cal, oat, as_found_schedule, as_corrected_schedule)
sv.avoided_energy  # modeled avoided energy (None if the calibration failed the gate)
sv.fractional_savings  # avoided / as-found
sv.frac_savings_uncertainty  # ASHRAE G14 Annex-B fractional savings uncertainty
sv.basis  # "IPMVP Option D (calibrated simulation)"  |  a not-claimed note
sv.valid  # calibration met the G14 gate
```

The savings are the difference of the calibrated model's annual profiles under the two schedules, with a
G14 Annex-B uncertainty band from the calibration CV(RMSE). **If the calibration fails the acceptance
gate, no saving is claimed** (`valid=False`, `avoided_energy=None`) — the same refuse-to-fabricate
posture as `fault_economics` (`costed`) and `ecm_savings`'s upper bound. `mandv.ecm_savings.modeled_savings`
is the same call, framed as the pre-implementation counterpart to that upper bound.

## Honest limitations

- **1R1C, single-zone.** One thermal mass and time-constant — a *minimal, citable* Option-D method, not
  a full multi-zone calibrated-simulation suite. 2R2C / multi-zone and an optional EnergyPlus
  cross-validator are future refinements, deliberately out of this scope.
- **Needs metered energy + weather + a control schedule.** Real-building accuracy depends on data
  quality and **must pass the G14 gate** — a model that doesn't is reported as such and claims nothing.
- The synthetic-fixture tests prove the *method* (they recover a known model exactly); they are not a
  claim about any particular building's fit.
