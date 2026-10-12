"""Rule: pump **power-at-matched-flow** drift -- the wire-to-water efficiency detector.

A pump's electrical power tracks the cube of its flow (P ∝ Q³). At a given flow it should draw a
repeatable power; when it draws **more power at the same flow**, its wire-to-water efficiency has
fallen -- bearing / seal drag, a degrading motor or drive, internal recirculation, or a impeller
running further off its best-efficiency point. It is the energy-cost complement to the flow and head
detectors: those catch *what the pump is failing to deliver*, this catches *what it is wasting to
deliver it*. This rule freezes a load-normalized ``power ~ f(flow)`` baseline and scores the current
period's excess at matched flow.

It is **one-sided up**: only *more* power at matched flow is a fault; less is a (welcome) efficiency
gain, not a fault. Absolute pump power is size-dependent, so (as with the pressure detectors) the
sigma floor carries the weight and the kW floor is a coarse backstop.

Reuses the generic :attr:`camber.model.roles.Role.POWER` on the pump's equip-frame -- the equip
identifies the pump, so no dedicated pump-power role is needed. Loop-parameterized by the flow
normalizer. Declines loudly when power or the flow normalizer is unmapped. **Not** auto-registered
(needs an injected :class:`~camber.store.modelstore.BaselineStore`); run via
:meth:`camber.rules.base.Registry.run_periods`.

**Pump size (0.104, #150).** The flow gate is 10 % of the baseline's p95 flow (20 % for the
identifiable span), capped at 50 / 10 gpm, and the kW floors are 5.5 % / 11 % of the baseline's p95
power, capped at 1 / 2 kW, so a 40 gpm pump drawing under a kilowatt is judged on its own scale and
every pump drawing 18.2 kW or more exactly as before. A flow that barely moves (coefficient of
variation <= 2 %: a constant-speed pump) is compared as a flat level inside its observed band, with
a caveat. Explicit constructor values always win. See :mod:`camber.rules._pumpgates`.
"""

from __future__ import annotations

import pandas as pd

from ..chillerbaseline import fit_load_baseline, load_drift_stats, unscoreable_reason
from ..chillerdrift import (
    CUSUM_CLIP_SIGMA,
    CUSUM_LIMIT_SIGMA,
    CUSUM_MIN_CONSECUTIVE,
    CUSUM_SLACK_SIGMA,
    ApproachDriftMonitor,
)
from ..driftthresholds import threshold_confidence
from ..model.roles import Role
from . import _pumpgates as G
from .base import Finding

_KIND = "pump_power"

# Plausibility bounds for the power metric (kW) -- wide, only rejecting dropouts / bad values.
POWER_PLAUSIBLE = (0.0, 1e7)

# ---------------------------------------------------------------------------------------------
# MAGNITUDE FLOORS -- SCREENING-GRADE (see camber.driftthresholds). One-sided UP (excess power is
# the fault); both a kW floor and a sigma floor must be cleared. The sigma floor carries the weight
# (absolute power is pump-size-dependent); the kW floor is a coarse backstop. Constructor args.
# ---------------------------------------------------------------------------------------------
# 0.104 (#150): these are the caps; by default the floors are POWER_WARN_FRAC / POWER_FAULT_FRAC of
# the baseline's p95 power (camber.rules._pumpgates), which reach these values at an 18.2 kW pump.
POWER_WARN_KW = 1.0  # screening-grade -- coarse backstop
POWER_FAULT_KW = 2.0  # screening-grade
POWER_WARN_FRAC = G.POWER_WARN_FRAC  # of baseline p95 power, capped at POWER_WARN_KW
POWER_FAULT_FRAC = G.POWER_FAULT_FRAC  # of baseline p95 power, capped at POWER_FAULT_KW
POWER_WARN_SIGMA = 2.5  # screening-grade
POWER_FAULT_SIGMA = 4.0  # screening-grade

# Below this flow the pump carries no condition information.
# 0.104 (#150): the cap. By default the gate is 10 % of the baseline's p95 flow (20 % for the
# identifiable span), capped at 50 / 10 gpm; a pump-speed normalizer is gated at the learned VFD
# floor + 2 points (else 15 %) -- see camber.rules._pumpgates.
MIN_LOAD = G.FLOW_GATE_CAP
# A normalizer whose coefficient of variation over the baseline is at or under this is constant.
CONSTANT_CV = G.CONSTANT_CV


class PumpPowerDrift:
    """Detects a pump's power-at-matched-flow drifting **up** from a frozen baseline.

    Defaults to the chilled-water flow normalizer; pass ``flow_role`` for a hot-water loop, and
    ``power_role`` if the pump's power is mapped to a role other than the generic ``POWER``. A
    ``BaselineStore`` is injected, so (as with the chiller drift rules) it is **not**
    auto-registered.
    """

    name = "pump_power_drift"

    def __init__(
        self,
        store,
        *,
        site: str = "",
        run_id: str = "",
        power_role: Role = Role.POWER,
        flow_role: Role = Role.CHW_FLOW,
        status_role: Role = Role.PUMP_STATUS,
        freeze_if_missing: bool = True,
        warn_kw: float | None = None,  # None = size-relative (see the module note)
        fault_kw: float | None = None,  # None = size-relative
        warn_sigma: float = POWER_WARN_SIGMA,  # screening-grade
        fault_sigma: float = POWER_FAULT_SIGMA,  # screening-grade
        slack_sigma: float = CUSUM_SLACK_SIGMA,  # PROVISIONAL/UNTUNED -- see camber.chillerdrift
        limit_sigma: float = CUSUM_LIMIT_SIGMA,  # PROVISIONAL/UNTUNED
        clip_sigma: float = CUSUM_CLIP_SIGMA,  # PROVISIONAL/UNTUNED
        min_consecutive: int = CUSUM_MIN_CONSECUTIVE,  # PROVISIONAL/UNTUNED
        min_load: float | None = None,  # None = size-relative (see _pumpgates)
        min_load_span: float | None = None,  # None = size-relative
        constant_cv: float = CONSTANT_CV,
    ):
        self.store = store
        self.site = site
        self.run_id = run_id
        self.power_role = power_role
        self.flow_role = flow_role
        self.status_role = status_role
        self.roles_required = (power_role, flow_role)
        self.roles_optional = (status_role,)
        self.freeze_if_missing = freeze_if_missing
        self.warn_kw = warn_kw
        self.fault_kw = fault_kw
        self.warn_sigma = warn_sigma
        self.fault_sigma = fault_sigma
        self.slack_sigma = slack_sigma
        self.limit_sigma = limit_sigma
        self.clip_sigma = clip_sigma
        self.min_consecutive = min_consecutive
        self.min_load = min_load
        self.min_load_span = min_load_span
        self.constant_cv = constant_cv

    # ------------------------------------------------------------------ frame prep
    def _running(self, frame: pd.DataFrame) -> pd.DataFrame:
        if self.status_role in frame.columns:
            status = pd.to_numeric(frame[self.status_role], errors="coerce")
            return frame[status >= 0.5]
        return frame

    def _frozen_baseline(self, equip, base_frame, caveats, gate=None, mode="variable"):
        frozen = self.store.model_for(self.site, equip, _KIND)
        if frozen is not None:
            return frozen
        if not self.freeze_if_missing:
            caveats.append(
                f"could not evaluate {_KIND}: no frozen baseline and freezing is disabled"
            )
            return None
        if gate is None:
            gate = G.normalizer_gates(
                base_frame.get(self.flow_role), self.flow_role, self.min_load, self.min_load_span
            )
        level = mode == "constant"
        span = (
            G.level_span(base_frame, self.flow_role, self.power_role, gate.min_load)
            if level
            else gate.min_span
        )
        fit = fit_load_baseline(
            base_frame,
            metric_col=self.power_role,
            load_col=self.flow_role,
            min_load=gate.min_load,
            metric_range=POWER_PLAUSIBLE,
            min_load_span=span,
            level_fallback=level,
        )
        if fit is None:
            why = unscoreable_reason(
                base_frame,
                metric_col=self.power_role,
                load_col=self.flow_role,
                min_load=gate.min_load,
                metric_range=POWER_PLAUSIBLE,
                min_samples=30,
                min_load_span=None if level else span,
            )
            caveats.append(
                f"could not evaluate {_KIND}: the baseline period would not support a fit -- {why}"
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
    def _floors(self, base: pd.DataFrame) -> tuple[float, float]:
        """The (warn, fault) kW floors: explicit, else a fraction of the baseline's p95 power."""
        power = base[self.power_role] if self.power_role in base.columns else None
        return (
            G.scaled_floor(power, self.warn_kw, POWER_WARN_FRAC, POWER_WARN_KW),
            G.scaled_floor(power, self.fault_kw, POWER_FAULT_FRAC, POWER_FAULT_KW),
        )

    def _severity(self, drift, caveats, floors=None) -> str:
        """One-sided-UP severity: only *excess* power clears the floors (both kW and sigma)."""
        warn_kw, fault_kw = floors if floors is not None else (POWER_WARN_KW, POWER_FAULT_KW)
        if drift.drift_sigma != drift.drift_sigma:  # NaN: baseline had no residual scatter
            caveats.append("baseline had no residual scatter, so drift is judged on kW alone")
            if drift.drift_f >= fault_kw:
                return "fault"
            return "warn" if drift.drift_f >= warn_kw else "ok"
        if drift.drift_f >= fault_kw and drift.drift_sigma >= self.fault_sigma:
            return "fault"
        if drift.drift_f >= warn_kw and drift.drift_sigma >= self.warn_sigma:
            return "warn"
        return "ok"

    # ------------------------------------------------------------------ the rule
    # ------------------------------------------------------------------ pattern J evidence
    def drift_signature(self):
        """The frozen-model kind and the (load, metric) columns the baseline is fitted on."""
        return _KIND, self.flow_role, self.power_role

    def drift_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        """The prepared frame the baseline is fitted on (pump power at matched flow)."""
        return self._running(frame)

    def analyze_periods(self, equip: str, baseline: pd.DataFrame, current: pd.DataFrame) -> Finding:
        """Score the current period's power-at-flow vs the frozen baseline; return a Finding."""
        caveats: list = []
        missing = [r.value for r in (self.power_role, self.flow_role) if r not in current.columns]
        if missing:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True, "reason": "power_or_flow_not_mapped"},
                summary=f"{equip}: declined -- pump power needs a power and a flow point",
                caveats=[
                    "could not evaluate pump power: a power point and a flow (or speed) normalizer "
                    f"must both be mapped; missing {', '.join(missing)}"
                ],
            )

        base_r, cur_r = self._running(baseline), self._running(current)
        gate = G.normalizer_gates(
            base_r[self.flow_role], self.flow_role, self.min_load, self.min_load_span
        )
        cv = G.normalizer_cv(base_r[self.flow_role])
        mode = "constant" if cv <= self.constant_cv else "variable"
        floors = self._floors(base_r)
        frozen = self._frozen_baseline(equip, base_r, caveats, gate, mode)
        if frozen is None:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True},
                summary=f"{equip}: declined -- no frozen power baseline to compare against",
                caveats=caveats,
            )

        drift = load_drift_stats(
            frozen,
            cur_r,
            metric_col=self.power_role,
            load_col=self.flow_role,
            min_load=gate.min_load,
            metric_range=POWER_PLAUSIBLE,
        )
        if drift is None:
            why = unscoreable_reason(
                cur_r,
                metric_col=self.power_role,
                load_col=self.flow_role,
                min_load=gate.min_load,
                metric_range=POWER_PLAUSIBLE,
                min_samples=10,
                baseline=frozen,
            )
            caveats.append(
                f"could not evaluate {_KIND}: nothing scoreable in the current period -- {why}"
            )
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True},
                summary=f"{equip}: declined -- nothing scoreable in the current period",
                caveats=caveats,
            )

        severity = self._severity(drift, caveats, floors)
        direction = "up" if drift.drift_f >= 0 else "down"
        rec = self.store.get(self.site, equip, _KIND)
        metrics = {
            "pump_power_drift_kw": drift.drift_f,
            "pump_power_drift_sigma": drift.drift_sigma,
            "pump_power_drift_direction": direction,
            "pump_power_slope_kw_per_month": drift.slope_f_per_month,
            "pump_power_pct_outside_2sigma": drift.pct_outside_2sigma,
            "pump_power_n_current": drift.n_current,
            "pump_power_baseline_sigma_kw": frozen.sigma_f,
            "pump_power_baseline_frozen_at": rec.frozen_at if rec else "",
            # 0.104 (#150): the normalizer gates used, where they came from, and the model
            "pump_power_load_gate": gate.min_load,
            "pump_power_load_span_gate": gate.min_span,
            "pump_power_gate_source": gate.source,
            "pump_power_vfd_floor_pct": gate.vfd_floor,
            "pump_power_normalizer_mode": mode,
            "pump_power_warn_floor_kw": floors[0],
            "pump_power_fault_floor_kw": floors[1],
            "pump_power_baseline_model": getattr(frozen, "load_model", "linear"),
        }
        if getattr(frozen, "load_model", "linear") == "level":
            unit = "% speed" if G.is_speed(self.flow_role) else "gpm"
            caveats.append(
                G.constant_flow_caveat(
                    "pump power",
                    cv,
                    frozen.tons_min - frozen.load_band,
                    frozen.tons_max + frozen.load_band,
                    unit,
                )
            )
        if drift.extrapolated:
            caveats.append(
                "over 10% of the current period ran outside the baseline's fitted flow envelope, "
                "so part of this drift is extrapolated"
            )

        try:
            monitor = ApproachDriftMonitor(
                frozen,
                slack_sigma=self.slack_sigma,
                limit_sigma=self.limit_sigma,
                clip_sigma=self.clip_sigma,
                min_consecutive=self.min_consecutive,
                direction="up",  # only sustained excess power alarms
            )
            run = monitor.run(
                cur_r,
                approach_col=self.power_role,
                tons_col=self.flow_role,
                min_tons=gate.min_load,
                approach_range=POWER_PLAUSIBLE,
            )
        except ValueError as exc:
            run = None
            caveats.append(f"could not run the sustained-shift alarm: {exc}")
        if run is not None:
            metrics.update(
                {
                    "pump_power_sustained_alarm": run.alarmed,
                    "pump_power_first_alarm_at": run.first_alarm_at,
                    "pump_power_alarm_direction": run.alarm_direction,
                }
            )
        metrics.update(threshold_confidence(magnitude=True, temporal=run is not None))

        if direction == "up":
            headline = (
                f"{equip}: pump power excess {drift.drift_f:+.1f} kW "
                f"({drift.drift_sigma:.1f}σ) vs frozen baseline at matched flow"
            )
        else:
            headline = (
                f"{equip}: pump power {drift.drift_f:+.1f} kW vs frozen baseline at matched flow "
                "(less power is not a fault)"
            )
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics=metrics,
            summary=headline,
            caveats=caveats,
        )
