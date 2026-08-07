"""Rule: liquid-line **subcooling** drift -- the refrigerant-charge detector.

Condenser approach drift (:mod:`camber.rules.chiller_drift_rule`) is the heat-transfer detector: it
catches anything that degrades the condenser's ability to reject heat. It is deliberately one-sided,
because fouling only ever widens an approach.

Subcooling asks a different question -- **how much liquid refrigerant is standing in the
condenser** -- and it is the natural detector for the charge-and-inventory family of faults that
barely move an approach at all. Two properties make it worth its own rule rather than another leg
of the approach rule:

1. **It is two-sided, and both directions are faults.** Subcooling *falls* when the circuit is
   short of liquid (undercharge, a leak) and *rises* when liquid backs up in the condenser
   (overcharge, non-condensables, restricted condenser flow). A one-sided detector of the kind that
   suits approach would silently miss half the fault space, so this rule scores the **magnitude**
   of the drift and reports its sign.
2. **It is instrumentation-gated.** :attr:`camber.model.roles.Role.SUBCOOLING_TEMP` is a
   controller-reported difference, like the approach roles: CAMBER has no refrigerant saturation
   temperature or pressure role, so subcooling cannot be derived from a liquid-line temperature and
   must be mapped directly. Many chillers do not publish it. The role is therefore **optional** and
   the rule *declines with a caveat* when it is absent, rather than being silently skipped -- a
   chiller missing from a charge report must not read as a chiller with good charge.

Everything else is the machinery the approach detectors already use: the same load-normalized
baseline fit (:mod:`camber.chillerbaseline`), the same frozen-with-provenance coefficient store
(:mod:`camber.store.modelstore`), and the same streaming CUSUM (:mod:`camber.chillerdrift`) run
two-sided. Subcooling is load-dependent, so the comparison is made at matched load for the same
reason approach is.
"""

from __future__ import annotations

import pandas as pd

from ..chillerbaseline import drift_stats, fit_approach_baseline, tons_from_flow
from ..chillerdrift import (
    CUSUM_CLIP_SIGMA,
    CUSUM_LIMIT_SIGMA,
    CUSUM_MIN_CONSECUTIVE,
    CUSUM_SLACK_SIGMA,
    ApproachDriftMonitor,
)
from ..model.roles import Role
from .base import Finding

_ROLE_TO_COL = {
    Role.CHW_SUPPLY_TEMP: "CHWS_Temp",
    Role.CHW_RETURN_TEMP: "CHWR_Temp",
    Role.CHW_FLOW: "CHW_Flow",
}

_KIND = "chiller_subcooling"

# ---------------------------------------------------------------------------------------------
# PROVISIONAL THRESHOLDS -- empirically tuned, not yet confirmed on the equipment being monitored.
#
# These are starting points chosen from observed behaviour of this signal class, not values
# established on the chillers this will run against, and they should be reviewed once the site has
# accumulated its own trend history with known charge events. All are constructor arguments, so
# tuning is a config change rather than a code change.
#
# The floors sit higher, in sigma, than the approach rule's. Subcooling's run-to-run scatter is
# wider relative to its fault response than an approach's, so a 2-sigma floor of the kind that
# suits approach sits inside this signal's ordinary variation. As with approach, a finding must
# clear BOTH a degF floor and a sigma floor, in either direction.
# ---------------------------------------------------------------------------------------------
SUBCOOLING_WARN_F = 1.0  # PROVISIONAL
SUBCOOLING_FAULT_F = 2.0  # PROVISIONAL
SUBCOOLING_WARN_SIGMA = 3.0  # PROVISIONAL
SUBCOOLING_FAULT_SIGMA = 6.0  # PROVISIONAL


class ChillerSubcoolingDrift:
    """Detects liquid-line subcooling drifting either way from a frozen, load-normalized baseline.

    A :class:`~camber.store.modelstore.BaselineStore` is injected so the reference survives between
    runs, which (as with the approach rules) means this is **not** auto-registered in
    :func:`camber.rules.builtin.builtin_registry`; the caller instantiates and registers it. Run it
    via :meth:`camber.rules.base.Registry.run_periods`.

    Unlike the approach pair, the period statistic and the sustained-shift alarm are reported in
    **one** Finding. The approach side is split across two rules only because its level check
    (``chiller_approach_fouling``) predates the drift work and had to keep its behaviour; subcooling
    has no such legacy, and "subcooling has moved 2 °F and has been there for a fortnight" is a
    single work order rather than two.
    """

    name = "chiller_subcooling_drift"
    roles_required = (Role.CHW_FLOW, Role.CHW_SUPPLY_TEMP, Role.CHW_RETURN_TEMP)
    roles_optional = (Role.SUBCOOLING_TEMP,)

    def __init__(
        self,
        store,
        *,
        site: str = "",
        run_id: str = "",
        freeze_if_missing: bool = True,
        warn_f: float = SUBCOOLING_WARN_F,  # PROVISIONAL -- see the module note
        fault_f: float = SUBCOOLING_FAULT_F,  # PROVISIONAL
        warn_sigma: float = SUBCOOLING_WARN_SIGMA,  # PROVISIONAL
        fault_sigma: float = SUBCOOLING_FAULT_SIGMA,  # PROVISIONAL
        slack_sigma: float = CUSUM_SLACK_SIGMA,  # PROVISIONAL
        limit_sigma: float = CUSUM_LIMIT_SIGMA,  # PROVISIONAL
        clip_sigma: float = CUSUM_CLIP_SIGMA,  # PROVISIONAL
        min_consecutive: int = CUSUM_MIN_CONSECUTIVE,  # PROVISIONAL
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
        """A ``tons`` + subcooling frame; tons derived as in :mod:`camber.chiller`."""
        legacy = frame.rename(columns={r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns})
        out = pd.DataFrame({"tons": tons_from_flow(legacy)}, index=frame.index)
        if Role.SUBCOOLING_TEMP in frame.columns:
            out[Role.SUBCOOLING_TEMP] = frame[Role.SUBCOOLING_TEMP]
        return out

    def _frozen_baseline(self, equip, base_frame, caveats):
        """The frozen subcooling baseline, freezing an initial one from ``base_frame`` if none."""
        frozen = self.store.model_for(self.site, equip, _KIND)
        if frozen is not None:
            return frozen
        if not self.freeze_if_missing:
            caveats.append(
                f"could not evaluate {_KIND}: no frozen baseline and freezing is disabled"
            )
            return None
        fit = fit_approach_baseline(
            base_frame,
            approach_col=Role.SUBCOOLING_TEMP,
            tons_col="tons",
            min_tons=self.min_tons,
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
        """Two-sided severity: magnitude must clear both the degF and the sigma floor."""
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
        """Score the current period's subcooling against the frozen baseline; return a Finding."""
        caveats: list = []
        if Role.SUBCOOLING_TEMP not in current.columns:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True, "reason": "subcooling_not_mapped"},
                summary=f"{equip}: declined -- no subcooling point mapped for this chiller",
                caveats=[
                    "could not evaluate refrigerant charge: subcooling is a directly-reported "
                    "point and this chiller does not publish one; it cannot be derived from the "
                    "approach temperatures"
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
                summary=f"{equip}: declined -- no frozen subcooling baseline to compare against",
                caveats=caveats,
            )

        drift = drift_stats(
            frozen,
            cur_t,
            approach_col=Role.SUBCOOLING_TEMP,
            tons_col="tons",
            min_tons=self.min_tons,
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
            "subcooling_drift_f": drift.drift_f,
            "subcooling_drift_sigma": drift.drift_sigma,
            "subcooling_drift_direction": direction,
            "subcooling_slope_f_per_month": drift.slope_f_per_month,
            "subcooling_pct_outside_2sigma": drift.pct_outside_2sigma,
            "subcooling_n_current": drift.n_current,
            "subcooling_baseline_sigma_f": frozen.sigma_f,
            "subcooling_baseline_frozen_at": rec.frozen_at if rec else "",
            "thresholds_provisional": True,
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
                direction="both",  # subcooling faults move it either way
            )
            run = monitor.run(
                cur_t,
                approach_col=Role.SUBCOOLING_TEMP,
                tons_col="tons",
                min_tons=self.min_tons,
            )
        except ValueError as exc:
            run = None
            caveats.append(f"could not run the sustained-shift alarm: {exc}")
        if run is not None:
            metrics.update(
                {
                    "subcooling_sustained_alarm": run.alarmed,
                    "subcooling_first_alarm_at": run.first_alarm_at,
                    "subcooling_alarm_direction": run.alarm_direction,
                }
            )

        arrow = "widened" if direction == "up" else "narrowed"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics=metrics,
            summary=(
                f"{equip}: liquid-line subcooling {arrow} {abs(drift.drift_f):.1f}°F "
                f"({abs(drift.drift_sigma):.1f}σ) vs frozen baseline at matched load"
            ),
            caveats=caveats,
        )
