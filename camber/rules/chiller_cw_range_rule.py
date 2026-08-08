"""Rule: condenser-water **range** drift -- the condenser-side hydraulic detector.

Condenser approach drift (:mod:`camber.rules.chiller_drift_rule`) is the heat-transfer detector and
subcooling drift (:mod:`camber.rules.chiller_subcooling_rule`) is the charge detector. Neither sees
the third way a condenser goes wrong: **the water side stops moving the design flow.**

Condenser-water range is that signal::

    range = CW_RETURN_TEMP - CW_SUPPLY_TEMP        (across the condenser, back to the tower)

and it is fixed by an energy balance, ``Q_cond = 500 * gpm * range``. At a *matched chiller load*
the heat rejected is essentially fixed, so the range is inversely proportional to condenser-water
flow, and it moves for hydraulic reasons rather than thermal ones:

* **Widening** -- flow has fallen. A scaled or silted condenser bundle restricting its own tubes, a
  degrading CW pump, a throttled or drifting balancing valve, a fouling strainer.
* **Narrowing** -- flow has risen, or is not going through the condenser at all. An opened bypass,
  short-circuiting, a balancing valve backed off. It wastes pump energy and it degrades the tower,
  which sees a colder return than it was selected for.

Both directions are therefore genuine faults, so the magnitude is scored symmetrically and the sign
is reported, exactly as for subcooling. The approach detectors stay one-sided because fouling only
ever widens an approach; there is no equivalent one-way argument here.

**Why this is worth its own rule and not another leg of the approach one.** A scale layer on the
tube wall that impedes *heat* without much restricting *flow* widens the approach and barely moves
the range; a partly-closed balancing valve does the reverse. Range and approach therefore fail
independently, and when they move together the diagnosis is much stronger than either alone -- which
is what turns a drift alert into a work order.

Everything below is the machinery the other two detectors already use: the metric-neutral
load-normalized fit (:func:`camber.chillerbaseline.fit_load_baseline`), the frozen-with-provenance
coefficient store (:mod:`camber.store.modelstore`), and the streaming CUSUM
(:mod:`camber.chillerdrift`) run two-sided. Range is load-dependent in the same way approach is, so
it is the same fit with a column swap; the range series itself comes from
:func:`camber.coolingtower.cw_range_f`, the same subtraction (and the same sign convention) the
cooling-tower diagnostic already gates on.

The rule **always runs**. ``CW_SUPPLY_TEMP``/``CW_RETURN_TEMP`` are optional roles -- plenty of
chillers publish neither -- and when they are absent the rule *declines with a caveat* rather than
being silently skipped, so a chiller missing from a condenser-flow report cannot be mistaken for a
chiller with healthy condenser flow.
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
from ..coolingtower import cw_range_f
from ..driftthresholds import threshold_confidence
from ..model.roles import Role
from .base import Finding

_ROLE_TO_COL = {
    Role.CHW_SUPPLY_TEMP: "CHWS_Temp",
    Role.CHW_RETURN_TEMP: "CHWR_Temp",
    Role.CHW_FLOW: "CHW_Flow",
    Role.CW_SUPPLY_TEMP: "CWS_Temp",
    Role.CW_RETURN_TEMP: "CWR_Temp",
}

_KIND = "chiller_cw_range"
_METRIC = "cw_range_f"  # the derived column the baseline is fitted on

# ---------------------------------------------------------------------------------------------
# MAGNITUDE FLOORS -- SCREENING-GRADE (see camber.driftthresholds).
#
# Characterized from the behaviour of this signal class, not established on the chillers this will
# run against. All are constructor arguments, so tuning is a config change, not a code change.
#
# The sigma floors sit between the approach rule's (2/3) and subcooling's (3/6). Range is a
# *difference of two independently noisy sensors* rather than a single controller-reported value, so
# its residual scatter is wider than an approach's; but its fault response is larger in degF than
# subcooling's, because a design range is ~10 degF and a hydraulic fault moves a visible fraction of
# it. As with the other two, a finding must clear BOTH a degF floor and a sigma floor, and both are
# applied to |drift| -- the direction is reported, not scored.
# ---------------------------------------------------------------------------------------------
CW_RANGE_WARN_F = 1.0  # screening-grade, applied to |drift|
CW_RANGE_FAULT_F = 2.0  # screening-grade, applied to |drift|
CW_RANGE_WARN_SIGMA = 2.5  # screening-grade, applied to |drift|
CW_RANGE_FAULT_SIGMA = 5.0  # screening-grade, applied to |drift|

# Plausibility bounds on the range itself, in degF. The floor drops crossed/failed sensors and
# intervals where nothing is being rejected, without discarding the genuinely narrow ranges this
# rule exists to catch -- so it sits well below the cooling-tower module's 2.0 degF "is the tower
# working" gate, which is answering a different question.
CW_RANGE_PLAUSIBLE_F = (0.5, 40.0)


def _has_cw(frame: pd.DataFrame) -> bool:
    """Whether both condenser-water temperature roles are present on ``frame``."""
    return Role.CW_SUPPLY_TEMP in frame.columns and Role.CW_RETURN_TEMP in frame.columns


class ChillerCwRangeDrift:
    """Detects condenser-water range drifting either way from a frozen, load-normalized baseline.

    A :class:`~camber.store.modelstore.BaselineStore` is injected so the reference survives between
    runs, which (as with the other drift rules) means this is **not** auto-registered in
    :func:`camber.rules.builtin.builtin_registry`; the caller instantiates and registers it. Run it
    via :meth:`camber.rules.base.Registry.run_periods`.

    Like the subcooling rule and unlike the approach pair, the period statistic and the
    sustained-shift alarm are reported in **one** Finding: "the range has narrowed 2 °F and has
    stayed there for a fortnight" is a single work order.

    **Alarm symmetry.** :meth:`_severity` compares ``abs(drift_f)`` and ``abs(drift_sigma)`` against
    one pair of floors, so an equal widening and narrowing score identically, and
    ``cw_range_drift_direction`` (plus the CUSUM's ``alarm_direction``, run with
    ``direction="both"``) says which way it went. Both directions are real hydraulic faults with
    different causes; neither is known to deserve a tighter floor than the other.
    """

    name = "chiller_cw_range_drift"
    roles_required = (Role.CHW_FLOW, Role.CHW_SUPPLY_TEMP, Role.CHW_RETURN_TEMP)
    roles_optional = (Role.CW_SUPPLY_TEMP, Role.CW_RETURN_TEMP)

    def __init__(
        self,
        store,
        *,
        site: str = "",
        run_id: str = "",
        freeze_if_missing: bool = True,
        warn_f: float = CW_RANGE_WARN_F,  # screening-grade -- see the module note
        fault_f: float = CW_RANGE_FAULT_F,  # screening-grade
        warn_sigma: float = CW_RANGE_WARN_SIGMA,  # screening-grade
        fault_sigma: float = CW_RANGE_FAULT_SIGMA,  # screening-grade
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
        """A ``tons`` + condenser-water-range frame; tons derived as in :mod:`camber.chiller`."""
        legacy = frame.rename(columns={r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns})
        out = pd.DataFrame({"tons": tons_from_flow(legacy)}, index=frame.index)
        if _has_cw(frame):
            # the same subtraction, and the same sign convention, the tower diagnostic gates on
            out[_METRIC] = cw_range_f(legacy)
        return out

    def _frozen_baseline(self, equip, base_frame, caveats):
        """The frozen range baseline, freezing an initial one from ``base_frame`` if none."""
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
            load_col="tons",
            min_load=self.min_tons,
            metric_range=CW_RANGE_PLAUSIBLE_F,
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

        Symmetric by construction -- one pair of floors applied to the magnitude -- so a widening
        and an equal narrowing return the same severity. The direction is reported separately.
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
        """Score the current period's CW range against the frozen baseline; return a Finding."""
        caveats: list = []
        missing = [r.value for r in self.roles_optional if r not in current.columns]
        if missing:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True, "reason": "cw_range_not_mapped"},
                summary=f"{equip}: declined -- no condenser-water range available for this chiller",
                caveats=[
                    "could not evaluate condenser-water flow: range needs both a CW supply and a "
                    f"CW return temperature and this chiller is missing {', '.join(missing)}; it "
                    "cannot be derived from the chilled-water side"
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
                summary=f"{equip}: declined -- no frozen CW-range baseline to compare against",
                caveats=caveats,
            )

        drift = load_drift_stats(
            frozen,
            cur_t,
            metric_col=_METRIC,
            load_col="tons",
            min_load=self.min_tons,
            metric_range=CW_RANGE_PLAUSIBLE_F,
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
            "cw_range_drift_f": drift.drift_f,
            "cw_range_drift_sigma": drift.drift_sigma,
            "cw_range_drift_direction": direction,
            "cw_range_slope_f_per_month": drift.slope_f_per_month,
            "cw_range_pct_outside_2sigma": drift.pct_outside_2sigma,
            "cw_range_n_current": drift.n_current,
            "cw_range_baseline_sigma_f": frozen.sigma_f,
            "cw_range_baseline_frozen_at": rec.frozen_at if rec else "",
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
                direction="both",  # hydraulic faults move the range either way
            )
            run = monitor.run(
                cur_t,
                approach_col=_METRIC,
                tons_col="tons",
                min_tons=self.min_tons,
                approach_range=CW_RANGE_PLAUSIBLE_F,
            )
        except ValueError as exc:
            run = None
            caveats.append(f"could not run the sustained-shift alarm: {exc}")
        if run is not None:
            metrics.update(
                {
                    "cw_range_sustained_alarm": run.alarmed,
                    "cw_range_first_alarm_at": run.first_alarm_at,
                    "cw_range_alarm_direction": run.alarm_direction,
                }
            )
        # Severity is magnitude-driven (screening-grade); the sustained-alarm metrics, when present,
        # add a temporal claim resting on the weaker, untuned parameters -- label both.
        metrics.update(threshold_confidence(magnitude=True, temporal=run is not None))

        arrow, cause = (
            ("widened", "less condenser-water flow")
            if direction == "up"
            else ("narrowed", "more flow, or flow bypassing the condenser")
        )
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics=metrics,
            summary=(
                f"{equip}: condenser-water range {arrow} {abs(drift.drift_f):.1f}°F "
                f"({abs(drift.drift_sigma):.1f}σ) vs frozen baseline at matched load — {cause}"
            ),
            caveats=caveats,
        )
