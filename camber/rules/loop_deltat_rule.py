"""Rule: hydronic **loop delta-T** drift -- the low-delta-T-syndrome detector.

A hydronic loop is designed to run a target temperature difference (return - supply on a
chilled-water loop, supply - return on a hot-water loop). When that ΔT **collapses at matched
flow**, the loop is moving water without moving heat -- the classic *low-ΔT syndrome*: overpumping,
fouled or air-bound coils, stuck-open or leaking control valves, or a decoupler short-circuit. It
wastes pump energy and starves the far end of the distribution. The other direction -- ΔT *widening*
at matched flow -- is **underflow / starvation** (a throttled loop, a failed pump, a closed valve).

Both directions are faults, so this rule is **two-sided**: it scores the magnitude of the ΔT drift
and reports the sign, exactly as liquid-line subcooling does on the refrigerant side. It freezes a
load-normalized ``deltaT ~ f(flow)`` baseline and scores the current period's residual at matched
flow. **Flow is the normalizer, deliberately:** the loop's own thermal load is ``flow x deltaT``, so
normalizing ΔT on load would be circular; flow is the non-circular proxy (load/OAT variation is
extra residual scatter, which the sigma floor absorbs). Where no flow point exists, pass pump
speed as the normalizer -- an affinity proxy for flow.

**Gates scale with the loop (0.104, #150).** A flow normalizer is gated at 10 % of the baseline's
p95 flow (20 % for the identifiable span), capped at 50 / 10 gpm, so a small loop is not declined
for never reaching 50 gpm and every loop of 500 gpm or more is gated exactly as before; a pump-speed
normalizer is gated just above the pump's learned VFD floor (else 15 %), not at 50. A normalizer
that barely moves (coefficient of variation <= 2 %: a constant-speed pump, a constant-flow loop) is
compared as a flat level inside its observed band, with a caveat. Explicit values always win.

**Loop-parameterized** by the warm/cool temperature pair and the normalizer, so one class serves a
chilled-water loop (warm=return, cool=supply) or a hot-water loop (warm=supply, cool=return); the
equip identifies the loop. Declines loudly when the temperature pair or the normalizer is unmapped.
**Not** auto-registered (needs an injected ``BaselineStore``); run via
:meth:`camber.rules.base.Registry.run_periods`.
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
from ._driftwording import holds_near_baseline
from .base import Finding

_KIND = "loop_deltat"
_METRIC = "loop_deltat_f"  # the derived column the baseline is fitted on

# Plausibility bounds for the ΔT metric (degF) -- a real loop ΔT is a small positive number.
DELTAT_PLAUSIBLE = (0.5, 60.0)

# ---------------------------------------------------------------------------------------------
# MAGNITUDE FLOORS -- SCREENING-GRADE (see camber.driftthresholds). Two-sided, applied to |drift|.
# A two-sensor temperature *difference* (like the condenser-water range), so the sigma floors sit
# a little higher than a single-sensor signal's. Constructor args; characterized, not established.
# ---------------------------------------------------------------------------------------------
DELTAT_WARN_F = 1.0  # screening-grade, applied to |drift|
DELTAT_FAULT_F = 2.0  # screening-grade, applied to |drift|
DELTAT_WARN_SIGMA = 2.5  # screening-grade, applied to |drift|
DELTAT_FAULT_SIGMA = 5.0  # screening-grade, applied to |drift|

# Below this normalizer value (gpm of flow, or % speed) the loop carries no condition information.
# 0.104 (#150): the cap. By default a flow normalizer is gated at 10 % of the baseline's p95 flow
# (20 % for the identifiable span), capped at 50 / 10 gpm, and a pump-speed normalizer at the
# learned VFD floor + 2 points (else 15 %) -- see camber.rules._pumpgates.
MIN_LOAD = G.FLOW_GATE_CAP
# A normalizer whose coefficient of variation over the baseline is at or under this is constant.
CONSTANT_CV = G.CONSTANT_CV


class LoopDeltaTDrift:
    """Detects a hydronic loop's ΔT drifting either way from a frozen, flow-normalized baseline.

    Defaults to a chilled-water loop (``warm_role=CHW_RETURN_TEMP``, ``cool_role=CHW_SUPPLY_TEMP``,
    ``load_role=CHW_FLOW``); for a hot-water loop pass ``warm_role=HW_SUPPLY_TEMP`` /
    ``cool_role=HW_RETURN_TEMP`` (and the HW flow or a pump-speed proxy as ``load_role``). A
    ``BaselineStore`` is injected, so (as with the chiller drift rules) it is **not**
    auto-registered.
    """

    name = "loop_deltat_drift"
    kind = _KIND  # the baseline-store model kind (0.105, #152: overridable per loop)

    def __init__(
        self,
        store,
        *,
        site: str = "",
        run_id: str = "",
        warm_role: Role = Role.CHW_RETURN_TEMP,
        cool_role: Role = Role.CHW_SUPPLY_TEMP,
        load_role: Role = Role.CHW_FLOW,
        status_role: Role = Role.PUMP_STATUS,
        freeze_if_missing: bool = True,
        warn_f: float = DELTAT_WARN_F,  # screening-grade -- see the module note
        fault_f: float = DELTAT_FAULT_F,  # screening-grade
        warn_sigma: float = DELTAT_WARN_SIGMA,  # screening-grade
        fault_sigma: float = DELTAT_FAULT_SIGMA,  # screening-grade
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
        self.warm_role = warm_role
        self.cool_role = cool_role
        self.load_role = load_role
        self.status_role = status_role
        self.roles_required = (warm_role, cool_role, load_role)
        self.roles_optional = (status_role,)
        self.freeze_if_missing = freeze_if_missing
        self.warn_f = warn_f
        self.fault_f = fault_f
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
    def _prepared(self, frame: pd.DataFrame) -> pd.DataFrame:
        """A ``loop_deltat_f`` (warm - cool) + normalizer frame; masked to running samples."""
        out = pd.DataFrame(index=frame.index)
        warm = pd.to_numeric(frame[self.warm_role], errors="coerce")
        cool = pd.to_numeric(frame[self.cool_role], errors="coerce")
        out[_METRIC] = warm - cool
        out[self.load_role] = pd.to_numeric(frame[self.load_role], errors="coerce")
        if self.status_role in frame.columns:
            status = pd.to_numeric(frame[self.status_role], errors="coerce")
            out = out[status >= 0.5]
        return out

    def _frozen_baseline(self, equip, base_frame, caveats, gate=None, mode="variable"):
        """The frozen ΔT~flow baseline, freezing an initial one from ``base_frame`` if none."""
        frozen = self.store.model_for(self.site, equip, self.kind)
        if frozen is not None:
            return frozen
        if not self.freeze_if_missing:
            caveats.append(
                f"could not evaluate {self.kind}: no frozen baseline and freezing is disabled"
            )
            return None
        if gate is None:
            gate = G.normalizer_gates(
                base_frame.get(self.load_role), self.load_role, self.min_load, self.min_load_span
            )
        level = mode == "constant"
        span = (
            G.level_span(base_frame, self.load_role, _METRIC, gate.min_load)
            if level
            else gate.min_span
        )
        fit = fit_load_baseline(
            base_frame,
            metric_col=_METRIC,
            load_col=self.load_role,
            min_load=gate.min_load,
            metric_range=DELTAT_PLAUSIBLE,
            min_load_span=span,
            level_fallback=level,
        )
        if fit is None:
            why = unscoreable_reason(
                base_frame,
                metric_col=_METRIC,
                load_col=self.load_role,
                min_load=gate.min_load,
                metric_range=DELTAT_PLAUSIBLE,
                min_samples=30,
                min_load_span=None if level else span,
            )
            caveats.append(
                f"could not evaluate {self.kind}: the baseline period would not support a fit "
                f"-- {why}"
            )
            return None
        idx = base_frame.index
        self.store.freeze(
            fit,
            site=self.site,
            equip=equip,
            kind=self.kind,
            frozen_at=self.run_id,
            period=(str(idx.min()), str(idx.max())),
            reason="initial baseline frozen from the supplied baseline period",
        )
        return fit

    # ------------------------------------------------------------------ severity
    def _severity(self, drift, caveats) -> str:
        """Two-sided severity: |drift| must clear both the degF and the sigma floor."""
        mag_f = abs(drift.drift_f)
        if drift.drift_sigma != drift.drift_sigma:  # NaN: baseline had no residual scatter
            caveats.append("baseline had no residual scatter, so drift is judged on degF alone")
            if mag_f >= self.fault_f:
                return "fault"
            return "warn" if mag_f >= self.warn_f else "ok"
        mag_sigma = abs(drift.drift_sigma)
        if mag_f >= self.fault_f and mag_sigma >= self.fault_sigma:
            return "fault"
        if mag_f >= self.warn_f and mag_sigma >= self.warn_sigma:
            return "warn"
        return "ok"

    # ------------------------------------------------------------------ the rule
    # ------------------------------------------------------------------ pattern J evidence
    def drift_signature(self):
        """The frozen-model kind and the (load, metric) columns the baseline is fitted on."""
        return self.kind, self.load_role, _METRIC

    def drift_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        """The prepared frame the baseline is fitted on (loop delta-T at matched load)."""
        return self._prepared(frame)

    def analyze_periods(self, equip: str, baseline: pd.DataFrame, current: pd.DataFrame) -> Finding:
        """Score the current period's loop ΔT vs the frozen baseline; return a Finding."""
        caveats: list = []
        missing = [
            r.value
            for r in (self.warm_role, self.cool_role, self.load_role)
            if r not in current.columns
        ]
        if missing:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True, "reason": "deltat_inputs_not_mapped"},
                summary=f"{equip}: declined -- loop ΔT needs a supply/return pair and a flow proxy",
                caveats=[
                    "could not evaluate loop ΔT: a warm and a cool loop temperature plus a flow "
                    f"(or speed) normalizer must all be mapped; missing {', '.join(missing)}"
                ],
            )

        base_t, cur_t = self._prepared(baseline), self._prepared(current)
        gate = G.normalizer_gates(
            base_t[self.load_role], self.load_role, self.min_load, self.min_load_span
        )
        cv = G.normalizer_cv(base_t[self.load_role])
        mode = "constant" if cv <= self.constant_cv else "variable"
        frozen = self._frozen_baseline(equip, base_t, caveats, gate, mode)
        if frozen is None:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True},
                summary=f"{equip}: declined -- no frozen ΔT baseline to compare against",
                caveats=caveats,
            )

        drift = load_drift_stats(
            frozen,
            cur_t,
            metric_col=_METRIC,
            load_col=self.load_role,
            min_load=gate.min_load,
            metric_range=DELTAT_PLAUSIBLE,
        )
        if drift is None:
            why = unscoreable_reason(
                cur_t,
                metric_col=_METRIC,
                load_col=self.load_role,
                min_load=gate.min_load,
                metric_range=DELTAT_PLAUSIBLE,
                min_samples=10,
                baseline=frozen,
            )
            caveats.append(
                f"could not evaluate {self.kind}: nothing scoreable in the current period -- {why}"
            )
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True},
                summary=f"{equip}: declined -- nothing scoreable in the current period",
                caveats=caveats,
            )

        severity = self._severity(drift, caveats)
        direction = "up" if drift.drift_f >= 0 else "down"
        cause = "underflow / starvation" if direction == "up" else "low-ΔT syndrome (overpumping)"
        rec = self.store.get(self.site, equip, self.kind)
        metrics = {
            "loop_deltat_drift_f": drift.drift_f,
            "loop_deltat_drift_sigma": drift.drift_sigma,
            "loop_deltat_drift_direction": direction,
            "loop_deltat_slope_f_per_month": drift.slope_f_per_month,
            "loop_deltat_pct_outside_2sigma": drift.pct_outside_2sigma,
            "loop_deltat_n_current": drift.n_current,
            "loop_deltat_baseline_sigma_f": frozen.sigma_f,
            "loop_deltat_baseline_frozen_at": rec.frozen_at if rec else "",
            # 0.104 (#150): the normalizer gates used, where they came from, and the model
            "loop_deltat_load_gate": gate.min_load,
            "loop_deltat_load_span_gate": gate.min_span,
            "loop_deltat_gate_source": gate.source,
            "loop_deltat_vfd_floor_pct": gate.vfd_floor,
            "loop_deltat_normalizer_mode": mode,
            "loop_deltat_baseline_model": getattr(frozen, "load_model", "linear"),
        }
        if getattr(frozen, "load_model", "linear") == "level":
            unit = "% speed" if G.is_speed(self.load_role) else "gpm"
            caveats.append(
                G.constant_flow_caveat(
                    "loop ΔT",
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
                direction="both",  # a collapse and a widening are both faults
            )
            run = monitor.run(
                cur_t,
                approach_col=_METRIC,
                tons_col=self.load_role,
                min_tons=gate.min_load,
                approach_range=DELTAT_PLAUSIBLE,
            )
        except ValueError as exc:
            run = None
            caveats.append(f"could not run the sustained-shift alarm: {exc}")
        if run is not None:
            metrics.update(
                {
                    "loop_deltat_sustained_alarm": run.alarmed,
                    "loop_deltat_first_alarm_at": run.first_alarm_at,
                    "loop_deltat_alarm_direction": run.alarm_direction,
                }
            )
        metrics.update(threshold_confidence(magnitude=True, temporal=run is not None))

        if severity == "ok":
            # 0.105 (#153): an ok finding names no fault -- the cause belongs to warn and fault
            summary = holds_near_baseline(
                equip,
                "loop ΔT",
                drift.drift_f,
                drift.drift_sigma,
                decimals=1,
                unit="°F",
                matched="matched flow",
            )
        else:
            arrow = "widened" if direction == "up" else "collapsed"
            summary = (
                f"{equip}: loop ΔT {arrow} {abs(drift.drift_f):.1f}°F "
                f"({abs(drift.drift_sigma):.1f}σ) vs frozen baseline at matched flow -- {cause}"
            )
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics=metrics,
            summary=summary,
            caveats=caveats,
        )
