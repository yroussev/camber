"""Rule: compressor **discharge superheat** drift (provisional, 0.93; closes the #6 deferral).

Discharge superheat -- the discharge-line temperature above the dew temperature at discharge
pressure -- is the compressor's own view of the circuit. It climbs when the compressor is fed too
little refrigerant or too-hot suction gas (undercharge, a leak, a restriction, a starved
evaporator, a failing valve plate that re-compresses hot gas) and falls when liquid reaches the
compressor (overcharge, an overfeeding expansion valve, floodback). Both directions are faults,
so like subcooling this detector is **two-sided**: it scores the magnitude of the drift and reports
the sign.

It was deferred (GitHub issue #6) for want of a discharge-line temperature and of labelled data.
The NIST residential heat-pump FDD data publish discharge superheat beside labelled charge and
airflow faults, and :mod:`camber.refrigerant` derives it from a discharge pressure and a
discharge-line temperature for any mapped unit whose refrigerant is named, so the detector now
has both. It reuses the frozen-baseline machinery of the chiller drift family: a reference fitted
over a fault-free period **at matched conditions** (outdoor-air temperature plus return air for an
air-cooled DX unit; load in tons plus entering condenser water for a water-cooled chiller), the
current period's median residual against a degF and a sigma floor, and the streaming two-sided
CUSUM (:class:`camber.chillerdrift.ApproachDriftMonitor`) for "moved and stayed moved".

**Not** auto-registered (it needs an injected :class:`~camber.store.modelstore.BaselineStore`):
run it through the ``dx`` drift family (``camber drift``) or
:meth:`camber.rules.base.Registry.run_periods`. The role degrades gracefully: with no discharge
superheat point, and none derivable, the rule declines with a caveat.

Thresholds are screening-grade, characterized on the NIST laboratory heat pumps (docs/FDD-DX.md);
the temporal (CUSUM) parameters are the family's provisional, untuned defaults.
"""

from __future__ import annotations

import pandas as pd

from ..chillerdrift import (
    CUSUM_CLIP_SIGMA,
    CUSUM_LIMIT_SIGMA,
    CUSUM_MIN_CONSECUTIVE,
    CUSUM_SLACK_SIGMA,
    ApproachDriftMonitor,
)
from ..driftthresholds import threshold_confidence
from ..model.roles import Role
from ..sensorhealth import PHYSICAL_BOUNDS
from ..store.modelstore import BaselineStore
from . import _dxfit
from .base import Finding

__all__ = [
    "DSH_WARN_F",
    "DSH_FAULT_F",
    "DSH_WARN_SIGMA",
    "DSH_FAULT_SIGMA",
    "DischargeSuperheatDrift",
]

_KIND = "discharge_superheat"
_ROLE = Role.DISCHARGE_SUPERHEAT_TEMP

# SCREENING-GRADE floors, applied to |drift| (see camber.driftthresholds). Discharge superheat
# scatters more than subcooling (tens of degF, moving with lift), so the degF floors sit higher.
DSH_WARN_F = 5.0
DSH_FAULT_F = 10.0
DSH_WARN_SIGMA = 2.0
DSH_FAULT_SIGMA = 4.0


class DischargeSuperheatDrift:
    """Discharge superheat drifting either way from a frozen fault-free baseline (module doc)."""

    name = "discharge_superheat_drift"
    roles_required = (_ROLE,)
    roles_optional = (
        Role.OAT,
        Role.RETURN_AIR_TEMP,
        Role.SUPPLY_AIR_TEMP,
        Role.COMPRESSOR_STATUS,
        Role.REVERSING_VALVE_CMD,
        Role.CHW_FLOW,
        Role.CHW_SUPPLY_TEMP,
        Role.CHW_RETURN_TEMP,
        Role.CW_SUPPLY_TEMP,
    )

    def __init__(
        self,
        store=None,
        *,
        site: str = "",
        run_id: str = "",
        freeze_if_missing: bool = True,
        warn_f: float = DSH_WARN_F,  # screening-grade
        fault_f: float = DSH_FAULT_F,  # screening-grade
        warn_sigma: float = DSH_WARN_SIGMA,  # screening-grade
        fault_sigma: float = DSH_FAULT_SIGMA,  # screening-grade
        slack_sigma: float = CUSUM_SLACK_SIGMA,  # PROVISIONAL/UNTUNED
        limit_sigma: float = CUSUM_LIMIT_SIGMA,  # PROVISIONAL/UNTUNED
        clip_sigma: float = CUSUM_CLIP_SIGMA,  # PROVISIONAL/UNTUNED
        min_consecutive: int = CUSUM_MIN_CONSECUTIVE,  # PROVISIONAL/UNTUNED
        min_samples: int = 10,
        min_baseline_samples: int = _dxfit.MIN_BASELINE_SAMPLES,
    ):
        self.store = store if store is not None else BaselineStore()
        self.site = site
        self.run_id = run_id
        self.freeze_if_missing = freeze_if_missing
        self.warn_f = warn_f
        self.fault_f = fault_f
        self.warn_sigma = warn_sigma
        self.fault_sigma = fault_sigma
        self.slack_sigma = slack_sigma
        self.limit_sigma = limit_sigma
        self.clip_sigma = clip_sigma
        self.min_consecutive = min_consecutive
        self.min_samples = min_samples
        self.min_baseline_samples = min_baseline_samples

    def _declined(self, equip, why, reason, caveats=None) -> Finding:
        return Finding(
            rule=self.name,
            equip=equip,
            severity="info",
            metrics={"declined": True, "reason": reason},
            summary=f"{equip}: declined -- {why}",
            caveats=list(caveats or []) + [f"could not evaluate discharge superheat: {why}"],
        )

    def drift_signature(self):
        """The frozen-model kind and the (normalizer, metric) columns the baseline is fitted on."""
        return _KIND, _dxfit.LOAD, _ROLE

    def drift_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        """The prepared frame the baseline is fitted on (cooling rows, matched conditions)."""
        return _dxfit.prepared(frame, _ROLE, water=_dxfit.is_water_cooled(frame))

    def analyze_periods(self, equip: str, baseline: pd.DataFrame, current: pd.DataFrame) -> Finding:
        """Score the current period's discharge superheat against the frozen baseline."""
        if _ROLE not in current.columns:
            return self._declined(
                equip,
                "no discharge superheat point (map one, or map the discharge pressure and "
                "discharge-line temperature and name the refrigerant)",
                "discharge_superheat_not_mapped",
            )
        caveats: list = []
        water = _dxfit.is_water_cooled(current)
        base_t = _dxfit.prepared(baseline, _ROLE, water=water)
        cur_t = _dxfit.prepared(current, _ROLE, water=water)
        if base_t.empty or cur_t.empty:
            need = "chilled-water load" if water else "outdoor-air temperature"
            return self._declined(equip, f"no {need} to match conditions on", "no_normalizer")
        gate = _dxfit.gates(water, base_t)
        rng = PHYSICAL_BOUNDS[_ROLE]
        frozen = _dxfit.freeze(
            self,
            equip,
            base_t,
            caveats,
            kind=_KIND,
            metric=_ROLE,
            metric_range=rng,
            metric_name="discharge superheat",
            gate=gate,
        )
        if frozen is None:
            return self._declined(equip, "no fault-free baseline", "no_baseline", caveats)
        drift = _dxfit.score(
            frozen,
            cur_t,
            caveats,
            kind=_KIND,
            metric=_ROLE,
            metric_range=rng,
            min_samples=self.min_samples,
            gate=gate,
        )
        if drift is None:
            return self._declined(
                equip, "nothing scoreable in the current period", "empty", caveats
            )
        severity = _dxfit.severity(
            drift.drift_f,
            frozen.sigma_f,
            warn_f=self.warn_f,
            fault_f=self.fault_f,
            warn_sigma=self.warn_sigma,
            fault_sigma=self.fault_sigma,
        )
        direction = "up" if drift.drift_f >= 0 else "down"
        rec = self.store.get(self.site, equip, _KIND)
        metrics = {
            "discharge_superheat_drift_f": drift.drift_f,
            "discharge_superheat_drift_sigma": drift.drift_sigma,
            "discharge_superheat_drift_direction": direction,
            "discharge_superheat_baseline_sigma_f": frozen.sigma_f,
            "discharge_superheat_n_current": drift.n_current,
            "discharge_superheat_baseline_frozen_at": rec.frozen_at if rec else "",
            "normalized_on": "tons" if water else "oat",
            "covariate": frozen.covariate or None,
            "attribution": "refrigerant_circuit",
        }
        if drift.extrapolated or drift.covariate_extrapolated:
            caveats.append(
                "over 10% of the current period ran outside the conditions the baseline covered, "
                "so part of this drift is extrapolated"
            )
        run = None
        if frozen.sigma_f > 0:
            try:
                monitor = ApproachDriftMonitor(
                    frozen,
                    slack_sigma=self.slack_sigma,
                    limit_sigma=self.limit_sigma,
                    clip_sigma=self.clip_sigma,
                    min_consecutive=self.min_consecutive,
                    direction="both",
                )
                cov = frozen.covariate or None
                run = monitor.run(
                    cur_t,
                    approach_col=_ROLE,
                    tons_col=_dxfit.LOAD,
                    min_tons=gate[0],
                    approach_range=rng,
                    covariate_col=Role(cov) if cov else None,
                )
            except ValueError as exc:
                caveats.append(f"could not run the sustained-shift alarm: {exc}")
        if run is not None:
            metrics.update(
                {
                    "discharge_superheat_sustained_alarm": run.alarmed,
                    "discharge_superheat_first_alarm_at": run.first_alarm_at,
                    "discharge_superheat_alarm_direction": run.alarm_direction,
                }
            )
        metrics.update(threshold_confidence(magnitude=True, temporal=run is not None))
        why = (
            "the compressor is fed too little or too-hot gas (low charge, a restriction, a starved "
            "evaporator)"
            if direction == "up"
            else "liquid is reaching the compressor (overcharge, an overfeeding valve, floodback)"
        )
        summary = (
            f"{equip}: discharge superheat {'rose' if direction == 'up' else 'fell'} "
            f"{abs(drift.drift_f):.1f}°F ({abs(drift.drift_sigma):.1f}σ) vs its frozen baseline "
            "at matched conditions"
        )
        if severity in ("warn", "fault"):
            summary += f" -- {why}"
        return Finding(self.name, equip, severity, metrics, summary, caveats=caveats)
