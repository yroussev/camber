"""Rule: cooling-tower **approach** drift -- the tower's heat-rejection detector, over time.

:class:`~camber.rules.coolingtower_rule.CoolingTowerApproach` answers "is the approach high?"
against a static design constant. This rule answers the earlier question, "has the approach been
**climbing**?" -- the signal that shows a tower fouling weeks before it's a work order. A tower
can only cool the condenser water *toward* the ambient wet-bulb; the gap it achieves is the
approach::

    approach = CW_SUPPLY_TEMP - wet_bulb        (condenser water leaving the tower)

and it widens as the fill fouls or scales, nozzles plug, or airflow drops (failed / under-driven
fans). It is **one-sided**: like a heat exchanger's approach, fouling only ever *widens* it, so a
narrowing is not a fault. A widening approach raises condenser-water temperature, chiller lift, and
kW/ton -- so this pairs directly with the chiller condenser-side drift detectors.

The comparison is made **at matched load**. Approach widens with heat rejection on its own, so a
busier current period looks like degradation to any level-vs-level test; scoring the residuals
against a fitted ``approach ~ f(tons)`` line removes that confound (:mod:`camber.chillerbaseline`),
using chiller tons as the condenser-heat proxy the CW-range detector already uses. A tower that is
its own equipment carries no chilled-water points (0.106, #184), so the load falls back to the
condenser side: the heat rejected (``cw_flow * range / 24``, tons) when the condenser flow is
trended, else the tower's range (``cw_return_temp - cw_supply_temp``), which is right only while
the condenser flow is constant and says so when a finding fires. The reference is
**frozen**, not rolling (:mod:`camber.store.modelstore`): a refit from the window being judged would
define away the drift it is meant to catch.

Wet-bulb is rarely a BAS point, so it is taken measured when present, else derived from outdoor
dry-bulb + RH via Stull's approximation (:func:`camber.coolingtower.tower_approach_f`)
-- no psychrometric dependency. The tower's condenser-water supply temperature plus *some* wet-bulb
source are optional inputs; when they are absent the rule **declines with a caveat** rather than
being silently skipped, so a tower missing from a performance report cannot read as a healthy one.
"""

from __future__ import annotations

import pandas as pd

from ..chillerbaseline import tons_from_flow
from ..chillerdrift import (
    CUSUM_CLIP_SIGMA,
    CUSUM_LIMIT_SIGMA,
    CUSUM_MIN_CONSECUTIVE,
    CUSUM_SLACK_SIGMA,
    ApproachDriftMonitor,
)
from ..coolingtower import tower_approach_f
from ..driftthresholds import threshold_confidence
from ..model.roles import Role
from . import _chillerfit
from .base import Finding

_ROLE_TO_COL = {
    Role.CW_RETURN_TEMP: "CWR_Temp",
    Role.CW_FLOW: "CW_Flow",
    Role.CHW_SUPPLY_TEMP: "CHWS_Temp",
    Role.CHW_RETURN_TEMP: "CHWR_Temp",
    Role.CHW_FLOW: "CHW_Flow",
    Role.CW_SUPPLY_TEMP: "CWS_Temp",
    Role.WETBULB_TEMP: "WetBulb",
    Role.OAT: "OAT",
    Role.OUTDOOR_RH: "RH",
}

_KIND = "cooling_tower_approach"
_METRIC = "tower_approach_f"  # the derived column the baseline is fitted on

# ---------------------------------------------------------------------------------------------
# MAGNITUDE FLOORS -- SCREENING-GRADE (see camber.driftthresholds).
#
# Characterized from the behaviour of this signal class, not established on the towers this will run
# against. All are constructor arguments, so tuning is a config change, not a code change.
#
# The floors sit a little wider than the chiller condenser-approach rule's (1/2 degF, 2/3 sigma):
# tower approach is a *difference against wet-bulb*, and the wet-bulb term is noisy -- more so
# when it is derived from OAT + RH rather than measured. As with the chiller detectors, a finding
# must clear BOTH a degF floor and a sigma floor. This rule is one-sided: only a *widening* (a
# positive drift, the fouling direction) is scored; a narrowing returns ok.
# ---------------------------------------------------------------------------------------------
TOWER_APPROACH_WARN_F = 1.5  # screening-grade, applied to the (signed) widening
TOWER_APPROACH_FAULT_F = 3.0  # screening-grade
TOWER_APPROACH_WARN_SIGMA = 2.5  # screening-grade
TOWER_APPROACH_FAULT_SIGMA = 4.0  # screening-grade

# Plausibility bounds on the approach itself, in degF. A tower can't beat the wet-bulb, so a small
# negative reading is sensor noise (kept); the ceiling drops crossed/failed sensors.
TOWER_APPROACH_PLAUSIBLE_F = (-2.0, 40.0)


class CoolingTowerApproachDrift:
    """Detects a cooling tower's approach drifting **up** from a frozen, load-normalized baseline.

    A :class:`~camber.store.modelstore.BaselineStore` is injected so the reference survives between
    runs, which (as with the chiller drift rules) means this is **not** auto-registered in
    :func:`camber.rules.builtin.builtin_registry`; the caller instantiates and registers it. Run it
    via :meth:`camber.rules.base.Registry.run_periods`.

    Like the CW-range and subcooling rules, the period statistic and the sustained-shift alarm are
    reported in **one** Finding. Unlike them it is **one-sided**: fouling only widens, so
    :meth:`_severity` scores the signed drift (a narrowing is ok); the CUSUM runs one-sided (up).
    """

    name = "cooling_tower_approach_drift"
    # 0.106 (#184): the load no longer has to be chilled-water tons, so a tower that is its own
    # equipment (no CHW points) runs on its condenser-side load; the approach itself still needs
    # the condenser-water supply and a wet-bulb, and declines without them
    roles_required = ()
    roles_any_of = ((Role.CHW_FLOW, Role.CW_RETURN_TEMP),)
    roles_optional = (
        Role.CHW_FLOW,
        Role.CHW_SUPPLY_TEMP,
        Role.CHW_RETURN_TEMP,
        Role.CW_SUPPLY_TEMP,
        Role.CW_RETURN_TEMP,
        Role.CW_FLOW,
        Role.WETBULB_TEMP,
        Role.OAT,
        Role.OUTDOOR_RH,
    )

    #: the load sources, strongest first: chilled-water tons, condenser heat rejected (tons),
    #: the tower's range (F)
    LOAD_SOURCES = ("chw_tons", "cw_heat_rejection", "cw_range")

    def __init__(
        self,
        store,
        *,
        site: str = "",
        run_id: str = "",
        freeze_if_missing: bool = True,
        warn_f: float = TOWER_APPROACH_WARN_F,  # screening-grade -- see the module note
        fault_f: float = TOWER_APPROACH_FAULT_F,  # screening-grade
        warn_sigma: float = TOWER_APPROACH_WARN_SIGMA,  # screening-grade
        fault_sigma: float = TOWER_APPROACH_FAULT_SIGMA,  # screening-grade
        slack_sigma: float = CUSUM_SLACK_SIGMA,  # PROVISIONAL/UNTUNED -- see camber.chillerdrift
        limit_sigma: float = CUSUM_LIMIT_SIGMA,  # PROVISIONAL/UNTUNED
        clip_sigma: float = CUSUM_CLIP_SIGMA,  # PROVISIONAL/UNTUNED
        min_consecutive: int = CUSUM_MIN_CONSECUTIVE,  # PROVISIONAL/UNTUNED
        min_tons: float | None = None,  # None = size-relative (camber.chillerbaseline)
        min_tons_span: float | None = None,  # None = size-relative
        elevation_ft: float
        | None = None,  # site elevation for a derived wet-bulb (None = sea level)
        pressure_psia: float | None = None,  # or a measured barometric pressure
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
        self.min_tons_span = min_tons_span
        self.elevation_ft = elevation_ft
        self.pressure_psia = pressure_psia

    # ------------------------------------------------------------------ inputs
    @staticmethod
    def _has_approach(frame: pd.DataFrame) -> bool:
        """Whether the tower approach can be computed: a CW supply temp plus a wet-bulb source."""
        if Role.CW_SUPPLY_TEMP not in frame.columns:
            return False
        return Role.WETBULB_TEMP in frame.columns or (
            Role.OAT in frame.columns and Role.OUTDOOR_RH in frame.columns
        )

    @staticmethod
    def load_source(frame: pd.DataFrame) -> str | None:
        """The load the approach is matched on (0.106, #184): ``"chw_tons"`` (CHW flow and both
        temperatures), ``"cw_heat_rejection"`` (condenser flow and range), ``"cw_range"`` (the
        tower's range alone) or ``None``."""

        def has(*roles):
            return all(
                r in frame.columns and pd.to_numeric(frame[r], errors="coerce").notna().any()
                for r in roles
            )

        if has(Role.CHW_FLOW, Role.CHW_SUPPLY_TEMP, Role.CHW_RETURN_TEMP):
            return "chw_tons"
        if has(Role.CW_SUPPLY_TEMP, Role.CW_RETURN_TEMP):
            return "cw_heat_rejection" if has(Role.CW_FLOW) else "cw_range"
        return None

    def _prepared(self, frame: pd.DataFrame, source: str | None = None) -> pd.DataFrame:
        """A ``tons`` + tower-approach frame. ``tons`` is the matched load: chilled-water tons as in
        :mod:`camber.chiller`, else the condenser side (see :meth:`load_source`)."""
        legacy = frame.rename(columns={r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns})
        source = source or self.load_source(frame)
        if source == "chw_tons":
            load = tons_from_flow(legacy)
        elif source in ("cw_heat_rejection", "cw_range"):
            load = pd.to_numeric(legacy["CWR_Temp"], errors="coerce") - pd.to_numeric(
                legacy["CWS_Temp"], errors="coerce"
            )
            if source == "cw_heat_rejection":
                load = pd.to_numeric(legacy["CW_Flow"], errors="coerce") * load / 24.0
        else:
            load = pd.Series(float("nan"), index=frame.index)
        out = pd.DataFrame({"tons": load}, index=frame.index)
        if self._has_approach(frame):
            out[_METRIC] = tower_approach_f(
                legacy, elevation_ft=self.elevation_ft, pressure_psia=self.pressure_psia
            )
        return out

    def _frozen_baseline(self, equip, base_frame, caveats, gate=None):
        """The frozen approach baseline, freezing an initial one from ``base_frame`` if none."""
        return _chillerfit.freeze_baseline(
            self,
            equip,
            base_frame,
            caveats,
            kind=_KIND,
            metric_col=_METRIC,
            metric_range=TOWER_APPROACH_PLAUSIBLE_F,
            gate=gate or _chillerfit.gates(base_frame, self.min_tons, self.min_tons_span),
            metric_name="tower approach",
        )

    # ------------------------------------------------------------------ severity
    def _severity(self, drift, caveats) -> str:
        """One-sided severity: a *widening* (positive drift) must clear both floors."""
        if drift.drift_sigma != drift.drift_sigma:  # NaN: baseline had no residual scatter
            caveats.append("baseline had no residual scatter, so drift is judged on degF alone")
            if drift.drift_f >= self.fault_f:
                return "fault"
            return "warn" if drift.drift_f >= self.warn_f else "ok"
        if drift.drift_f >= self.fault_f and drift.drift_sigma >= self.fault_sigma:
            return "fault"
        if drift.drift_f >= self.warn_f and drift.drift_sigma >= self.warn_sigma:
            return "warn"
        return "ok"

    # ------------------------------------------------------------------ the rule
    # ------------------------------------------------------------------ pattern J evidence
    def drift_signature(self):
        """The frozen-model kind and the (load, metric) columns the baseline is fitted on."""
        return _KIND, "tons", _METRIC

    def drift_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        """The prepared frame the baseline is fitted on (tower approach at matched load)."""
        return self._prepared(frame)

    def analyze_periods(self, equip: str, baseline: pd.DataFrame, current: pd.DataFrame) -> Finding:
        """Score the current period's tower approach against the frozen baseline."""
        caveats: list = []
        if not self._has_approach(current):
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True, "reason": "tower_approach_not_available"},
                summary=f"{equip}: declined -- no cooling-tower approach available",
                caveats=[
                    "could not evaluate cooling-tower performance: the approach needs a "
                    "condenser-water supply temperature and a wet-bulb (measured, or outdoor "
                    "dry-bulb + RH to derive it), and this equipment is missing one of them"
                ],
            )

        src_b, src_c = self.load_source(baseline), self.load_source(current)
        if src_b is None or src_b != src_c:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True, "reason": "no_load"},
                summary=f"{equip}: declined -- no tower load to match the approach on",
                caveats=[
                    "could not evaluate cooling-tower approach drift: the matched load needs CHW "
                    "flow + supply + return, or the condenser-water supply and return (with the "
                    "condenser flow, if trended), read the same way in both periods"
                ],
            )
        base_t, cur_t = self._prepared(baseline, src_b), self._prepared(current, src_c)
        gate = _chillerfit.gates(base_t, self.min_tons, self.min_tons_span)
        frozen = self._frozen_baseline(equip, base_t, caveats, gate)
        if frozen is None:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True},
                summary=f"{equip}: declined -- no frozen tower-approach baseline",
                caveats=caveats,
            )

        drift = _chillerfit.score(
            frozen,
            cur_t,
            caveats,
            kind=_KIND,
            metric_col=_METRIC,
            metric_range=TOWER_APPROACH_PLAUSIBLE_F,
            gate=gate,
            metric_name="tower approach",
        )
        if drift is None:
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
            "tower_approach_drift_f": drift.drift_f,
            "tower_approach_drift_sigma": drift.drift_sigma,
            "tower_approach_drift_direction": direction,
            "tower_approach_slope_f_per_month": drift.slope_f_per_month,
            "tower_approach_pct_outside_2sigma": drift.pct_outside_2sigma,
            "tower_approach_n_current": drift.n_current,
            "tower_approach_baseline_sigma_f": frozen.sigma_f,
            "tower_approach_baseline_frozen_at": rec.frozen_at if rec else "",
        }
        metrics["tower_approach_min_tons"] = gate[0]
        metrics["tower_approach_load_source"] = src_b  # 0.106 (#184)
        if severity != "ok" and src_b == "cw_range":
            caveats.append(
                "the matched load is the tower's range, which assumes a constant condenser flow: "
                "with more chillers (and condenser pumps) on than in the baseline the range falls "
                "for the same heat -- map the condenser flow (cw_flow) to match on heat rejected"
            )
        _chillerfit.fit_notes(frozen, caveats, metrics, "tower_approach")
        _chillerfit.envelope_caveats(drift, caveats, frozen)

        # the same frozen baseline, folded sample-by-sample: has it widened and *stayed* widened?
        try:
            monitor = ApproachDriftMonitor(
                frozen,
                slack_sigma=self.slack_sigma,
                limit_sigma=self.limit_sigma,
                clip_sigma=self.clip_sigma,
                min_consecutive=self.min_consecutive,
                direction="up",  # fouling only ever widens an approach
            )
            run = monitor.run(
                cur_t,
                approach_col=_METRIC,
                tons_col="tons",
                min_tons=gate[0],
                approach_range=TOWER_APPROACH_PLAUSIBLE_F,
                covariate_col=_chillerfit.covariate_key(frozen),
            )
        except ValueError as exc:
            run = None
            caveats.append(f"could not run the sustained-shift alarm: {exc}")
        if run is not None:
            metrics.update(
                {
                    "tower_approach_sustained_alarm": run.alarmed,
                    "tower_approach_first_alarm_at": run.first_alarm_at,
                    "tower_approach_alarm_direction": run.alarm_direction,
                }
            )
        # Severity is magnitude-driven (screening-grade); the sustained-alarm metrics, when present,
        # add a temporal claim resting on the weaker, untuned parameters -- label both.
        metrics.update(threshold_confidence(magnitude=True, temporal=run is not None))

        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics=metrics,
            summary=(
                f"{equip}: cooling-tower approach widened {drift.drift_f:+.1f}°F "
                f"({drift.drift_sigma:.1f}σ) vs frozen baseline at matched load"
            ),
            caveats=caveats,
        )
