"""Rule: **economizer OA-delivery** drift -- outdoor-air fraction at matched damper command.

An air-side economizer's outdoor-air damper is supposed to deliver a repeatable outdoor-air
fraction for a given command. When the **mechanical delivery** drifts -- linkage slipping, seals
leaking, the blade sticking, minimum-position creeping -- the same command no longer buys the same
outdoor air. This rule catches that mechanical drift. It is **not** a logic/sequence check: an
economizer commanded *wrong* for the conditions is the job of
:mod:`camber.rules.economizer_lockout_rule` and :mod:`camber.rules.freecoolingmissed_rule`. Clean
division of labor -- this rule assumes the
command is what it is and asks whether the damper still delivers on it.

**The signal is a temperature-inferred outdoor-air fraction; the load is the OA-damper command.**
From the mixing box, ``OAF = 100 * (RAT - MAT) / (RAT - OAT)`` (PNNL Ch.5; the orientation in
:mod:`camber.oafraction`). Freezing an ``OAF ~ f(damper command)`` baseline and scoring the current
period's OAF residual **at matched command** isolates the damper's delivered characteristic:
mechanical drift moves OAF-at-matched-command, weather and sequence unchanged. The choice is
deliberate and non-circular -- the naive residual ``OAF_temp - OAF_from_command`` would need a
*fixed* command->fraction calibration, but that calibration is exactly what drifts, so baking it in
blinds the detector to its own fault. Learning the command->delivery curve from the baseline and
scoring drift *of that curve* is faithful to the physics and native to the ``metric ~ f(load)``
engine. Because the OAT/RAT terms cancel in the ratio, ``OAF`` is regime-agnostic (economizing or
not), so economizing samples are **kept**: we want the full command sweep to identify the slope.

It is **two-sided**. A residual **up** (more OA than the baseline at matched command) is a damper
leaking / stuck or slipping open -- excess outdoor air, a cooling penalty in hot weather. A residual
**down** (less OA than the baseline at matched command) is a damper stuck or slipping closed -- lost
free cooling and possible under-ventilation. Both are faults; the direction routes the cause.

**The mixed-air sensor is a first-class confound (Sellers, *Relative Accuracy*).** The MAT sensor
sits in the numerator and stratifies badly, so a modest apparent OAF drift can be sensor, not
damper. A standing caveat says so on every scoreable finding, and the OA-fraction magnitude floor is
set deliberately high to sit above that noise. The **degenerate-mixing** case is handled, not
caveated only: when ``|RAT - OAT|`` is small the ratio's noise amplification blows up, so those rows
are excluded before the fit (the excluded fraction is reported).

Reuses only existing roles (``OAT`` / ``RETURN_AIR_TEMP`` / ``MIXED_AIR_TEMP`` / ``OA_DAMPER``); no
new role. Declines loudly when any of those is unmapped, or when the damper command never sweeps a
usable range. **Not** auto-registered (needs an injected ``BaselineStore``); run via
:meth:`camber.rules.base.Registry.run_periods`.
"""

from __future__ import annotations

import pandas as pd

from ..chillerbaseline import fit_load_baseline, load_drift_stats
from ..chillerdrift import (
    CUSUM_CLIP_SIGMA,
    CUSUM_LIMIT_SIGMA,
    CUSUM_MIN_CONSECUTIVE,
    CUSUM_SLACK_SIGMA,
    ApproachDriftMonitor,
)
from ..driftthresholds import threshold_confidence
from ..model.roles import Role
from .base import Finding

_KIND = "economizer_damper"
_METRIC = "econ_oa_fraction_pct"  # temperature-inferred OA fraction (%), the fitted metric
_LOAD = "econ_damper_cmd_pct"  # OA-damper command (%), the load the baseline is fitted against

# Plausibility bounds. The OA fraction is a percentage (allow a little overshoot for sensor slop);
# the damper command is 0-100%.
OAF_PLAUSIBLE = (-20.0, 120.0)
DAMPER_PLAUSIBLE = (0.0, 100.0)

# ---------------------------------------------------------------------------------------------
# MAGNITUDE FLOORS -- SCREENING-GRADE (see camber.driftthresholds). Two-sided, applied to |drift|;
# the sigma floor carries the weight, the OA-fraction floor is a coarse backstop set deliberately
# high because mixed-air stratification alone can contribute several points of apparent OAF error.
# Constructor args; characterized from the signal class, not established on this equipment.
# ---------------------------------------------------------------------------------------------
ECON_WARN_PCT = 10.0  # screening-grade, OA-fraction percentage-points, applied to |drift|
ECON_FAULT_PCT = 20.0  # screening-grade, applied to |drift|
ECON_WARN_SIGMA = 2.5  # screening-grade, applied to |drift|
ECON_FAULT_SIGMA = 4.0  # screening-grade, applied to |drift|

# Below this |RAT - OAT| (degF) the mixing ratio's 1/|RAT-OAT| noise amplification blows up; those
# rows are excluded before the fit (stricter than oafraction's 5.0 -- a drift baseline needs the
# stable samples, not the maximal count).
DENOM_MIN_F = 10.0
# Keep the whole command sweep down to fully closed; the engine's min-span check declines loudly
# when the command itself never varies.
MIN_COMMAND = 0.0

# Raw-temperature plausibility (reject sensor dropouts before the ratio), as in camber.oafraction.
_OAT_RANGE = (20.0, 130.0)
_MAT_RANGE = (30.0, 120.0)
_RAT_RANGE = (40.0, 110.0)

_MAT_CAVEAT = (
    "the mixed-air temperature sits in the numerator of this OA-fraction estimate and is prone to "
    "stratification error (Sellers, Relative Accuracy); corroborate a flagged drift against an "
    "OA-airflow measurement where one is mapped"
)


class EconomizerDamperDrift:
    """Detects an OA damper no longer delivering its baseline OA fraction at matched command.

    Reuses the ``OAT`` / ``RETURN_AIR_TEMP`` / ``MIXED_AIR_TEMP`` / ``OA_DAMPER`` roles; override
    any ``*_role`` to match a site's mapping. ``OAT`` is building-level and arrives via the shared
    merge in :meth:`camber.rules.base.Registry.run_periods`. A ``BaselineStore`` is injected, so (as
    with the other drift rules) it is **not** auto-registered.
    """

    name = "economizer_damper_drift"

    def __init__(
        self,
        store,
        *,
        site: str = "",
        run_id: str = "",
        oat_role: Role = Role.OAT,
        return_role: Role = Role.RETURN_AIR_TEMP,
        mixed_role: Role = Role.MIXED_AIR_TEMP,
        damper_role: Role = Role.OA_DAMPER,
        status_role: Role = Role.SUPPLY_FAN_STATUS,
        oa_airflow_role: Role = Role.OA_AIRFLOW,
        freeze_if_missing: bool = True,
        warn_pct: float = ECON_WARN_PCT,  # screening-grade -- see the module note
        fault_pct: float = ECON_FAULT_PCT,  # screening-grade
        warn_sigma: float = ECON_WARN_SIGMA,  # screening-grade
        fault_sigma: float = ECON_FAULT_SIGMA,  # screening-grade
        slack_sigma: float = CUSUM_SLACK_SIGMA,  # PROVISIONAL/UNTUNED -- see camber.chillerdrift
        limit_sigma: float = CUSUM_LIMIT_SIGMA,  # PROVISIONAL/UNTUNED
        clip_sigma: float = CUSUM_CLIP_SIGMA,  # PROVISIONAL/UNTUNED
        min_consecutive: int = CUSUM_MIN_CONSECUTIVE,  # PROVISIONAL/UNTUNED
        denom_min_f: float = DENOM_MIN_F,
        min_command: float = MIN_COMMAND,
    ):
        self.store = store
        self.site = site
        self.run_id = run_id
        self.oat_role = oat_role
        self.return_role = return_role
        self.mixed_role = mixed_role
        self.damper_role = damper_role
        self.status_role = status_role
        self.oa_airflow_role = oa_airflow_role
        self.roles_required = (oat_role, return_role, mixed_role, damper_role)
        self.roles_optional = (status_role, oa_airflow_role, Role.ECON_CMD)
        self.freeze_if_missing = freeze_if_missing
        self.warn_pct = warn_pct
        self.fault_pct = fault_pct
        self.warn_sigma = warn_sigma
        self.fault_sigma = fault_sigma
        self.slack_sigma = slack_sigma
        self.limit_sigma = limit_sigma
        self.clip_sigma = clip_sigma
        self.min_consecutive = min_consecutive
        self.denom_min_f = denom_min_f
        self.min_command = min_command

    # ------------------------------------------------------------------ frame prep
    def _prepared(self, frame: pd.DataFrame):
        """An ``OAF`` + ``damper command`` frame, masked to active, well-conditioned samples.

        Returns ``(prepared, degenerate_excluded_fraction)`` where the fraction is the share of
        otherwise-active rows dropped by the ``|RAT - OAT|`` floor.
        """
        oat = pd.to_numeric(frame[self.oat_role], errors="coerce")
        rat = pd.to_numeric(frame[self.return_role], errors="coerce")
        mat = pd.to_numeric(frame[self.mixed_role], errors="coerce")
        cmd = pd.to_numeric(frame[self.damper_role], errors="coerce")
        oaf = 100.0 * (rat - mat) / (rat - oat)
        out = pd.DataFrame({_METRIC: oaf, _LOAD: cmd}, index=frame.index)

        active = pd.Series(True, index=frame.index)
        if self.status_role in frame.columns:
            active &= pd.to_numeric(frame[self.status_role], errors="coerce") >= 0.5
        active &= oat.between(*_OAT_RANGE) & mat.between(*_MAT_RANGE) & rat.between(*_RAT_RANGE)
        active &= out[_LOAD].between(*DAMPER_PLAUSIBLE)

        well_conditioned = (rat - oat).abs() >= self.denom_min_f
        n_active = int(active.sum())
        excluded = float((active & ~well_conditioned).sum()) / n_active if n_active else 0.0
        return out[active & well_conditioned], round(excluded, 4)

    def _frozen_baseline(self, equip, base_frame, caveats):
        frozen = self.store.model_for(self.site, equip, _KIND)
        if frozen is not None:
            return frozen
        if not self.freeze_if_missing:
            caveats.append(
                f"could not evaluate {_KIND}: no frozen baseline and freezing is disabled"
            )
            return None
        fit = fit_load_baseline(
            base_frame,
            metric_col=_METRIC,
            load_col=_LOAD,
            min_load=self.min_command,
            metric_range=OAF_PLAUSIBLE,
        )
        if fit is None:
            caveats.append(
                f"could not evaluate {_KIND}: the baseline period would not support a fit -- too "
                "few well-conditioned samples, or the OA-damper command never sweeps a usable range"
            )
            return None
        idx = base_frame.index
        self.store.freeze(
            fit,
            site=self.site,
            equip=equip,
            kind=_KIND,
            frozen_at=self.run_id,
            period=(str(idx.min()), str(idx.max())),
            reason="initial baseline frozen from the supplied baseline period",
        )
        return fit

    # ------------------------------------------------------------------ severity
    def _severity(self, drift_f, drift_sigma) -> str:
        """Two-sided severity from a (drift, sigma) pair; |drift| must clear both floors."""
        mag_f = abs(drift_f)
        if drift_sigma != drift_sigma:  # NaN: baseline had no residual scatter
            if mag_f >= self.fault_pct:
                return "fault"
            return "warn" if mag_f >= self.warn_pct else "ok"
        mag_sigma = abs(drift_sigma)
        if mag_f >= self.fault_pct and mag_sigma >= self.fault_sigma:
            return "fault"
        if mag_f >= self.warn_pct and mag_sigma >= self.warn_sigma:
            return "warn"
        return "ok"

    # ------------------------------------------------------------------ the rule
    def analyze_periods(self, equip: str, baseline: pd.DataFrame, current: pd.DataFrame) -> Finding:
        """Score the current OA-fraction-at-command vs the frozen baseline; return a Finding."""
        caveats: list = []
        missing = [r.value for r in self.roles_required if r not in current.columns]
        if missing:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True, "reason": "economizer_inputs_not_mapped"},
                summary=f"{equip}: declined -- economizer drift needs OAT + return/mixed + damper",
                caveats=[
                    "could not evaluate the economizer: outdoor-air, return-air and mixed-air "
                    "temps and the OA-damper command must all be mapped; missing "
                    f"{', '.join(missing)}"
                ],
            )

        base_t, base_excl = self._prepared(baseline)
        cur_t, cur_excl = self._prepared(current)

        frozen = self._frozen_baseline(equip, base_t, caveats)
        if frozen is None:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True},
                summary=f"{equip}: declined -- no frozen economizer baseline to compare against",
                caveats=caveats,
            )

        drift = load_drift_stats(
            frozen,
            cur_t,
            metric_col=_METRIC,
            load_col=_LOAD,
            min_load=self.min_command,
            metric_range=OAF_PLAUSIBLE,
        )
        if drift is None:
            caveats.append(
                f"could not evaluate {_KIND}: no well-conditioned samples in the current period"
            )
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True},
                summary=f"{equip}: declined -- nothing scoreable in the current period",
                caveats=caveats,
            )

        severity = self._severity(drift.drift_f, drift.drift_sigma)
        direction = "up" if drift.drift_f >= 0 else "down"
        caveats.append(_MAT_CAVEAT)
        if max(base_excl, cur_excl) > 0.0:
            caveats.append(
                "some shoulder-season samples were excluded where outdoor and return air were too "
                "close for a stable OA-fraction; economizer coverage may be thin"
            )
        rec = self.store.get(self.site, equip, _KIND)
        metrics = {
            "econ_oa_fraction_drift_pct": drift.drift_f,
            "econ_oa_fraction_drift_sigma": drift.drift_sigma,
            "econ_oa_fraction_drift_direction": direction,
            "econ_oa_fraction_slope_pct_per_month": drift.slope_f_per_month,
            "econ_oa_fraction_pct_outside_2sigma": drift.pct_outside_2sigma,
            "econ_oa_fraction_n_current": drift.n_current,
            "econ_oa_fraction_baseline_sigma_pct": frozen.sigma_f,
            "econ_oa_fraction_baseline_frozen_at": rec.frozen_at if rec else "",
            "econ_damper_median_cmd_pct": round(float(cur_t[_LOAD].median()), 3)
            if len(cur_t)
            else None,
            "econ_degenerate_excluded_pct": round(100.0 * max(base_excl, cur_excl), 2),
        }
        if drift.extrapolated:
            caveats.append(
                "over 10% of the current period ran outside the baseline's fitted command "
                "envelope, so part of this drift is extrapolated"
            )

        try:
            monitor = ApproachDriftMonitor(
                frozen,
                slack_sigma=self.slack_sigma,
                limit_sigma=self.limit_sigma,
                clip_sigma=self.clip_sigma,
                min_consecutive=self.min_consecutive,
                direction="both",  # a sustained rise and a sustained fall are both faults
            )
            run = monitor.run(
                cur_t,
                approach_col=_METRIC,
                tons_col=_LOAD,
                min_tons=self.min_command,
                approach_range=OAF_PLAUSIBLE,
            )
        except ValueError as exc:
            run = None
            caveats.append(f"could not run the sustained-shift alarm: {exc}")
        if run is not None:
            metrics.update(
                {
                    "econ_oa_fraction_sustained_alarm": run.alarmed,
                    "econ_oa_fraction_first_alarm_at": run.first_alarm_at,
                    "econ_oa_fraction_alarm_direction": run.alarm_direction,
                }
            )
        metrics.update(threshold_confidence(magnitude=True, temporal=run is not None))

        if direction == "up":
            headline = (
                f"{equip}: economizer over-delivering OA {drift.drift_f:+.0f}pp "
                f"({drift.drift_sigma:.1f}σ) at matched damper command -- damper leaking / stuck "
                "or slipping open (excess outdoor air)"
            )
        else:
            headline = (
                f"{equip}: economizer under-delivering OA {drift.drift_f:+.0f}pp "
                f"({drift.drift_sigma:.1f}σ) at matched damper command -- damper stuck or slipping "
                "closed (lost free cooling / under-ventilation)"
            )
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics=metrics,
            summary=headline,
            caveats=caveats,
        )
