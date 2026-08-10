"""Rule: suction **superheat** drift -- the evaporator-side charge/feed detector.

Subcooling (:mod:`camber.rules.chiller_subcooling_rule`) watches the *condenser/liquid* side: how
much liquid refrigerant is standing in the condenser. Superheat watches the other end of the
circuit -- the *evaporator/suction* side -- and answers a complementary question: **is the
evaporator being fed the right amount of refrigerant?** The two together bracket the
charge-and-feed family of faults; neither alone sees all of it.

Suction superheat is how many degrees the suction gas sits above its saturation temperature. Its
two directions are two different faults, and both matter:

- **Falling superheat** -- the evaporator is *overfed*: an expansion valve stuck or hunting open,
  or an overcharge, floods the evaporator and pushes liquid toward the compressor. This is the more
  urgent direction because sustained low superheat risks liquid floodback and compressor damage.
- **Rising superheat** -- the evaporator is *starved*: undercharge/leak, a restricted metering
  device or filter-drier, or a plugged distributor. It degrades capacity and runs the compressor
  hot.

Two properties earn it its own rule rather than another leg of an approach rule, exactly as for
subcooling:

1. **It is two-sided, and both directions are faults.** A one-sided detector of the kind that suits
   condenser approach (fouling only ever widens an approach) would silently miss half the fault
   space, so this rule scores the **magnitude** of the drift and reports its sign.
2. **It is instrumentation-gated.** :attr:`camber.model.roles.Role.SUPERHEAT_TEMP` is a
   controller-reported difference, like subcooling and the approach roles: CAMBER has no refrigerant
   saturation-temperature or pressure role, so superheat cannot be derived from a suction
   temperature and must be mapped directly. Many chillers do not publish it. The role is therefore
   **optional** and the rule *declines with a caveat* when it is absent, not silently skipped -- a
   chiller missing from a feed report must not read as a chiller feeding correctly.

Everything else is the machinery the other drift detectors already use: the same metric-neutral
load-normalized fit (:func:`camber.chillerbaseline.fit_load_baseline`), the same
frozen-with-provenance coefficient store (:mod:`camber.store.modelstore`), and the same streaming
CUSUM (:mod:`camber.chillerdrift`) run two-sided. Superheat is load-dependent, so the comparison is
made at matched load for the same reason approach and subcooling are.
"""

from __future__ import annotations

import pandas as pd

from ..chillerbaseline import fit_load_baseline, load_drift_stats, tons_from_flow
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

_ROLE_TO_COL = {
    Role.CHW_SUPPLY_TEMP: "CHWS_Temp",
    Role.CHW_RETURN_TEMP: "CHWR_Temp",
    Role.CHW_FLOW: "CHW_Flow",
}

_KIND = "chiller_superheat"

# ---------------------------------------------------------------------------------------------
# MAGNITUDE FLOORS -- SCREENING-GRADE (see camber.driftthresholds).
#
# Characterized from the observed behaviour of this signal class, not established on the chillers
# this will run against; review once the site has accumulated its own trend history with known
# feed/charge events. All are constructor arguments, so tuning is a config change, not code.
#
# The degF floors sit higher than subcooling's (2/4 degF vs 1/2). Suction superheat runs over a
# wider band and hunts with the metering device (TXV/EXV modulation), so its ordinary run-to-run
# scatter in degF is larger; a subcooling-tight degF floor would fire on control hunting. The sigma
# floors match subcooling's (3/6), because in normalized terms the fault response is comparable. As
# with the other drift rules a finding must clear BOTH a degF floor and a sigma floor.
#
# **Both floors are applied to |drift|, symmetrically.** Low and high superheat are both genuine
# faults; the score is on the magnitude and the sign is reported alongside. An asymmetric floor --
# e.g. a tighter falling-side floor, since floodback is the more damaging direction -- is a
# defensible future refinement, but there is no measured basis to set the asymmetry yet, so it is
# deferred pending real-data validation rather than guessed. A wrong asymmetry is worse than none.
# ---------------------------------------------------------------------------------------------
SUPERHEAT_WARN_F = 2.0  # screening-grade, applied to |drift|
SUPERHEAT_FAULT_F = 4.0  # screening-grade, applied to |drift|
SUPERHEAT_WARN_SIGMA = 3.0  # screening-grade, applied to |drift|
SUPERHEAT_FAULT_SIGMA = 6.0  # screening-grade, applied to |drift|


class ChillerSuperheatDrift:
    """Detects suction superheat drifting either way from a frozen, load-normalized baseline.

    A :class:`~camber.store.modelstore.BaselineStore` is injected so the reference survives between
    runs, which (as with the other drift rules) means this is **not** auto-registered in
    :func:`camber.rules.builtin.builtin_registry`; the caller instantiates and registers it. Run it
    via :meth:`camber.rules.base.Registry.run_periods`.

    Like the subcooling rule, the period statistic and the sustained-shift alarm are reported in
    **one** Finding: "superheat has fallen 3 °F and has stayed there for a fortnight" is a single
    work order.

    **Alarm symmetry.** Scoring is symmetric in magnitude and signed in reporting: :meth:`_severity`
    compares ``abs(drift_f)`` and ``abs(drift_sigma)`` against one pair of floors, so an equal rise
    and fall score identically, while ``superheat_drift_direction`` (and the CUSUM's
    ``alarm_direction``, run with ``direction="both"``) says which way it went -- down for overfeed
    (floodback risk), up for starvation. That is deliberate: both directions are genuine faults and
    neither is *yet* given a tighter floor than the other. Per-direction floors are a future option,
    deferred until real trended fault data can say how much tighter the floodback side should be --
    see the module threshold note.
    """

    name = "chiller_superheat_drift"
    roles_required = (Role.CHW_FLOW, Role.CHW_SUPPLY_TEMP, Role.CHW_RETURN_TEMP)
    roles_optional = (Role.SUPERHEAT_TEMP,)

    def __init__(
        self,
        store,
        *,
        site: str = "",
        run_id: str = "",
        freeze_if_missing: bool = True,
        warn_f: float = SUPERHEAT_WARN_F,  # screening-grade -- see the module note
        fault_f: float = SUPERHEAT_FAULT_F,  # screening-grade
        warn_sigma: float = SUPERHEAT_WARN_SIGMA,  # screening-grade
        fault_sigma: float = SUPERHEAT_FAULT_SIGMA,  # screening-grade
        slack_sigma: float = CUSUM_SLACK_SIGMA,  # PROVISIONAL/UNTUNED -- see camber.chillerdrift
        limit_sigma: float = CUSUM_LIMIT_SIGMA,  # PROVISIONAL/UNTUNED
        clip_sigma: float = CUSUM_CLIP_SIGMA,  # PROVISIONAL/UNTUNED
        min_consecutive: int = CUSUM_MIN_CONSECUTIVE,  # PROVISIONAL/UNTUNED
        min_tons: float = 5.0,
    ):
        self.store = store
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
        self.min_tons = min_tons

    # ------------------------------------------------------------------ frame prep
    def _prepared(self, frame: pd.DataFrame) -> pd.DataFrame:
        """A ``tons`` + superheat frame; tons derived as in :mod:`camber.chiller`."""
        legacy = frame.rename(columns={r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns})
        out = pd.DataFrame({"tons": tons_from_flow(legacy)}, index=frame.index)
        if Role.SUPERHEAT_TEMP in frame.columns:
            out[Role.SUPERHEAT_TEMP] = frame[Role.SUPERHEAT_TEMP]
        return out

    def _frozen_baseline(self, equip, base_frame, caveats):
        """The frozen superheat baseline, freezing an initial one from ``base_frame`` if none."""
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
            metric_col=Role.SUPERHEAT_TEMP,
            load_col="tons",
            min_load=self.min_tons,
        )
        if fit is None:
            caveats.append(
                f"could not evaluate {_KIND}: the baseline period would not support a fit "
                "(too few loaded samples, or too narrow a load range)"
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
    def _severity(self, drift, caveats) -> str:
        """Two-sided severity: |drift| must clear both the degF and the sigma floor.

        Symmetric by construction -- one pair of floors, applied to the magnitude -- so a rise and
        an equal fall return the same severity. The direction is reported separately rather than
        being folded into the score.
        """
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
    def analyze_periods(self, equip: str, baseline: pd.DataFrame, current: pd.DataFrame) -> Finding:
        """Score the current period's superheat against the frozen baseline; return a Finding."""
        caveats: list = []
        if Role.SUPERHEAT_TEMP not in current.columns:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True, "reason": "superheat_not_mapped"},
                summary=f"{equip}: declined -- no superheat point mapped for this chiller",
                caveats=[
                    "could not evaluate refrigerant feed: superheat is a directly-reported point "
                    "and this chiller does not publish one; it cannot be derived from the suction "
                    "temperature alone"
                ],
            )

        base_t, cur_t = self._prepared(baseline), self._prepared(current)
        frozen = self._frozen_baseline(equip, base_t, caveats)
        if frozen is None:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True},
                summary=f"{equip}: declined -- no frozen superheat baseline to compare against",
                caveats=caveats,
            )

        drift = load_drift_stats(
            frozen,
            cur_t,
            metric_col=Role.SUPERHEAT_TEMP,
            load_col="tons",
            min_load=self.min_tons,
        )
        if drift is None:
            caveats.append(f"could not evaluate {_KIND}: no loaded samples in the current period")
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
        rec = self.store.get(self.site, equip, _KIND)
        metrics = {
            "superheat_drift_f": drift.drift_f,
            "superheat_drift_sigma": drift.drift_sigma,
            "superheat_drift_direction": direction,
            "superheat_slope_f_per_month": drift.slope_f_per_month,
            "superheat_pct_outside_2sigma": drift.pct_outside_2sigma,
            "superheat_n_current": drift.n_current,
            "superheat_baseline_sigma_f": frozen.sigma_f,
            "superheat_baseline_frozen_at": rec.frozen_at if rec else "",
        }
        if drift.extrapolated:
            caveats.append(
                "over 10% of the current period ran outside the baseline's fitted load envelope, "
                "so part of this drift is extrapolated"
            )

        # the same frozen baseline, folded sample-by-sample: did it move and *stay* moved?
        try:
            monitor = ApproachDriftMonitor(
                frozen,
                slack_sigma=self.slack_sigma,
                limit_sigma=self.limit_sigma,
                clip_sigma=self.clip_sigma,
                min_consecutive=self.min_consecutive,
                direction="both",  # superheat faults move it either way
            )
            run = monitor.run(
                cur_t,
                approach_col=Role.SUPERHEAT_TEMP,
                tons_col="tons",
                min_tons=self.min_tons,
            )
        except ValueError as exc:
            run = None
            caveats.append(f"could not run the sustained-shift alarm: {exc}")
        if run is not None:
            metrics.update(
                {
                    "superheat_sustained_alarm": run.alarmed,
                    "superheat_first_alarm_at": run.first_alarm_at,
                    "superheat_alarm_direction": run.alarm_direction,
                }
            )
        # Severity is magnitude-driven (screening-grade); the sustained-alarm metrics, when present,
        # add a temporal claim that rests on the weaker, untuned parameters -- label both.
        metrics.update(threshold_confidence(magnitude=True, temporal=run is not None))

        # up = starved (underfeed); down = overfed (floodback risk)
        feed = "starving" if direction == "up" else "overfeeding"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics=metrics,
            summary=(
                f"{equip}: suction superheat {'rose' if direction == 'up' else 'fell'} "
                f"{abs(drift.drift_f):.1f}°F ({abs(drift.drift_sigma):.1f}σ) vs frozen baseline at "
                f"matched load -- evaporator {feed}"
            ),
            caveats=caveats,
        )
