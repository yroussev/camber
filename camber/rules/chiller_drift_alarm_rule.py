"""Rule: chiller approach **sustained** drift -- a CUSUM alarm, not a period average.

:class:`~camber.rules.chiller_drift_rule.ChillerApproachDrift` reports how far a period sat above
its frozen baseline on average. This rule reports something the average cannot say: whether the
approach **moved up and stayed up**, and at which sample it did. A window whose mean drift is
identical can be a genuine step in week one or a scatter of unrelated excursions, and only the
second question separates them.

The engine is :class:`camber.chillerdrift.ApproachDriftMonitor`, wrapping the existing
:class:`camber.mandv.online.OnlineCusum` around the same frozen, load-normalized baseline the
period rule uses. Both rules therefore agree about what normal is and about which samples count;
they differ only in the question asked. Emitting them as two Findings is deliberate -- "it is 2 °F
wide" and "it stepped up on the 8th and has stayed there" are different work orders.

Implemented as a subclass purely to reuse the frame preparation and the frozen-baseline lookup;
it changes nothing in the period rule.

Its severity rests entirely on **temporal** parameters, which are the weaker of the two threshold
classes: untuned for this signal, with an unmeasured false-alarm rate (:mod:`camber.driftthresholds`
and the note in :mod:`camber.chillerdrift`). Findings therefore carry
``temporal_threshold_confidence`` and default to ``warn`` -- "worth looking at now", not a
dispatch-grade verdict. Full temporal validation awaits real trended fault data.
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
from .base import Finding
from .chiller_drift_rule import _LEGS, ChillerApproachDrift


class ChillerApproachSustainedDrift(ChillerApproachDrift):
    """Raises a distinct Finding when approach drift is *sustained*, and says when it began.

    Like its parent it needs an injected :class:`~camber.store.modelstore.BaselineStore` and is not
    auto-registered; run it via :meth:`camber.rules.base.Registry.run_periods`.
    """

    name = "chiller_approach_drift_sustained"

    def __init__(
        self,
        store,
        *,
        slack_sigma: float = CUSUM_SLACK_SIGMA,  # PROVISIONAL/UNTUNED -- see camber.chillerdrift
        limit_sigma: float = CUSUM_LIMIT_SIGMA,  # PROVISIONAL/UNTUNED
        clip_sigma: float = CUSUM_CLIP_SIGMA,  # PROVISIONAL/UNTUNED
        min_consecutive: int = CUSUM_MIN_CONSECUTIVE,  # PROVISIONAL/UNTUNED
        # capped at "warn" on purpose: an untuned temporal claim must not present as a fault, and
        # magnitude severity is the period rule's job.
        alarm_severity: str = "warn",
        **kw,
    ):
        super().__init__(store, **kw)
        self.slack_sigma = slack_sigma
        self.limit_sigma = limit_sigma
        self.clip_sigma = clip_sigma
        self.min_consecutive = min_consecutive
        self.alarm_severity = alarm_severity

    def monitor_for(self, baseline) -> ApproachDriftMonitor:
        """A monitor wired to ``baseline`` with this rule's (provisional) CUSUM parameters."""
        return ApproachDriftMonitor(
            baseline,
            slack_sigma=self.slack_sigma,
            limit_sigma=self.limit_sigma,
            clip_sigma=self.clip_sigma,
            min_consecutive=self.min_consecutive,
        )

    def analyze_periods(self, equip: str, baseline: pd.DataFrame, current: pd.DataFrame) -> Finding:
        """Fold the current period through a CUSUM against the frozen baseline; return a Finding."""
        base_t, cur_t = self._with_tons(baseline), self._with_tons(current)
        caveats: list = []
        metrics: dict = {}
        legs, severity = [], "ok"

        for role, slug, label in _LEGS:
            if role not in cur_t.columns:
                continue
            kind = f"chiller_approach_{slug}"
            frozen = self._baseline_for(equip, role, kind, base_t, caveats)
            if frozen is None:
                continue
            try:
                monitor = self.monitor_for(frozen)
            except ValueError as exc:
                caveats.append(f"could not evaluate {kind}: {exc}")
                continue
            run = monitor.run(cur_t, approach_col=role, tons_col="tons", min_tons=self.min_tons)
            if run is None:
                caveats.append(
                    f"could not evaluate {kind}: no loaded samples in the current period"
                )
                continue
            metrics.update(
                {
                    f"{slug}_sustained_alarm": run.alarmed,
                    f"{slug}_first_alarm_at": run.first_alarm_at,
                    f"{slug}_first_alarm_n": run.first_alarm_n,
                    f"{slug}_peak_climbing_f": run.peak_climbing,
                    f"{slug}_cusum_limit_f": run.limit_f,
                    f"{slug}_n_current": run.n,
                }
            )
            if run.alarmed:
                severity = self.alarm_severity
                legs.append(f"{label} sustained rise from {run.first_alarm_at}")
            else:
                legs.append(f"{label} no sustained rise")

        if not legs:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True},
                summary=f"{equip}: declined -- no leg could be scored against a frozen baseline",
                caveats=caveats,
            )
        # This verdict is purely temporal, so it is labelled with the weaker grade and no magnitude
        # grade: CUSUM tuning awaits real trended fault data.
        metrics.update(threshold_confidence(magnitude=False, temporal=True))
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics=metrics,
            summary=f"{equip}: sustained approach drift (CUSUM vs frozen baseline) — "
            + "; ".join(legs),
            caveats=caveats,
        )
