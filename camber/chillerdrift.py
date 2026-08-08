"""Streaming sustained-shift alarm on chiller approach drift, against a frozen baseline.

:func:`camber.chillerbaseline.drift_stats` answers "how far above baseline did this period sit, on
average?". That is a *period* verdict: it needs the window to be over, and a single number for the
window hides whether the shift arrived as a step in week one or a ramp across week four. The
operational question is narrower and earlier -- **has the approach moved up and stayed up?** -- and
it wants answering sample by sample, as the data lands.

That is exactly a tabular CUSUM, and :class:`camber.mandv.online.OnlineCusum` already implements
one: two one-sided accumulators that ignore drift inside ``slack`` and raise an alarm when either
exceeds ``limit``. It is written against any ``predict(driver) -> float``, and
:meth:`camber.chillerbaseline.ApproachBaseline.predict` already matches that shape, so the load
normalization comes along for free and **no change to** ``online.py`` **is needed**. Its
accumulators are named for its original energy use (savings/waste); this module relabels them for
approach, where the meaningful direction is *climbing*.

Two robustness measures matter here and are worth stating plainly:

* **Residual clipping.** A raw CUSUM is dominated by outliers -- one 20-sigma sensor dropout
  accumulates more than a fortnight of genuine drift. The value fed to the accumulator is clipped to
  ``clip_sigma`` around the prediction, so a spike contributes at most one sample's worth. The
  residual *reported* is always the true unclipped one; only the accumulator sees the clipped value.
* **A decision interval, gated on the shift still being present.** The accumulator crossing its
  limit for a single sample is a candidate, not a verdict, so an alarm needs ``min_consecutive``
  samples over the limit. That alone is not enough: a one-sided accumulator only decays by ``slack``
  per sample, so a short burst that has completely ended still sits above the limit long afterwards
  and would eventually satisfy any interval on its own. Each sample must therefore *also* be
  currently elevated -- its own residual above ``slack`` -- for the interval to count. That is the
  difference between "moved up and stayed up" and "moved up once and came back".

The baseline is frozen (:mod:`camber.store.modelstore`), so accepting a new normal must also reset
the accumulator: :meth:`ApproachDriftMonitor.rebase` does both together, since carrying CUSUM state
across a baseline change would re-alarm on drift the operator has already accepted.

**On the confidence of the four parameters below.** They are a weaker class of threshold than the
magnitude floors the period rules use, and :mod:`camber.driftthresholds` says why at length: the
magnitude floors are screening-grade, these are provisional and untuned. Full temporal validation
-- measuring the false-alarm rate and detection delay these actually produce -- awaits real trended
fault data, i.e. chiller trends with confirmed, dated fault events. Findings that rest on them are
labelled ``temporal_threshold_confidence`` so the distinction survives out of the source.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

# The private cleaner is shared deliberately: the streaming alarm must score exactly the
# population the period statistic scores, or the two would disagree about the same chiller for no
# visible reason.
from .chillerbaseline import _clean
from .mandv.online import OnlineCusum

__all__ = [
    "CUSUM_CLIP_SIGMA",
    "CUSUM_LIMIT_SIGMA",
    "CUSUM_MIN_CONSECUTIVE",
    "CUSUM_SLACK_SIGMA",
    "DriftAlarmRun",
    "DriftAlarmState",
    "ApproachDriftMonitor",
]

# ---------------------------------------------------------------------------------------------
# TEMPORAL PARAMETERS -- PROVISIONAL AND UNTUNED FOR THESE SIGNALS (camber.driftthresholds).
#
# Textbook tabular-CUSUM tuning for detecting a ~1-sigma sustained shift is slack k = 0.5 sigma with
# limit h = 4-5 sigma. The limit here is deliberately looser than that, because a month of hourly
# trend is ~720 samples and h = 5 sigma has an in-control run length short enough to false-alarm
# over a window that long. These are engineering-judgement starting points, not measurements: they
# have never been checked against real chiller trend data with confirmed fault events, and the
# false-alarm/detection-delay trade-off they encode should be set from that data. That makes them a
# weaker class of threshold than the magnitude floors in the period rules, which are screening-grade
# -- so they carry TEMPORAL_CONFIDENCE ("provisional-untuned"), not MAGNITUDE_CONFIDENCE, and
# severities resting on them must not be read as dispatch-grade. All four are constructor arguments,
# so tuning is a config change rather than a code change.
# ---------------------------------------------------------------------------------------------
CUSUM_SLACK_SIGMA = 0.5  # PROVISIONAL/UNTUNED -- drift smaller than this is ignored
CUSUM_LIMIT_SIGMA = 8.0  # PROVISIONAL/UNTUNED -- accumulator level constituting a candidate alarm
CUSUM_CLIP_SIGMA = 4.0  # PROVISIONAL/UNTUNED -- per-sample outlier clamp fed to the accumulator
CUSUM_MIN_CONSECUTIVE = 6  # PROVISIONAL/UNTUNED -- decision interval, in consecutive samples


@dataclass
class DriftAlarmState:
    """Snapshot of the approach-drift CUSUM after folding one sample."""

    n: int
    residual_f: float  # true (unclipped) actual - predicted, degF; + = wider than baseline
    climbing: float  # accumulator for a sustained *rise* in approach (degrading)
    improving: float  # accumulator for a sustained *fall* in approach (recovered/serviced)
    consecutive_over: int  # consecutive samples both over the limit and still elevated
    alarming: bool  # the decision interval has been satisfied
    alarm: str | None  # "drift" once alarming, else None
    alarm_direction: str | None = None  # "up" | "down" -- which side raised it

    def as_dict(self) -> dict:
        """Return the state as a plain dict."""
        return asdict(self)


@dataclass
class DriftAlarmRun:
    """Result of folding a whole period through the monitor."""

    n: int  # samples scored after guards
    alarmed: bool
    first_alarm_n: int  # 1-based sample at which the alarm first raised, -1 if never
    first_alarm_at: str  # index label at that sample, "" if never
    peak_climbing: float  # highest the climbing accumulator reached, degF
    limit_f: float  # the accumulator limit in use, degF
    final_residual_f: float
    alarm_direction: str | None = None  # "up" | "down" -- which side raised it first
    peak_improving: float = 0.0  # highest the falling-side accumulator reached, degF

    def as_dict(self) -> dict:
        """Return the run summary as a plain dict."""
        return asdict(self)


class ApproachDriftMonitor:
    """Online sustained-shift alarm on approach residuals against a frozen baseline.

    Wraps :class:`camber.mandv.online.OnlineCusum` and relabels its accumulators: its ``low`` side
    (sustained ``predicted < actual``) is a **climbing** signal, its ``high`` side a falling one.

    ``direction`` selects which sides can alarm. The default ``"up"`` alarms only on a climbing
    signal, which is right for approach -- fouling widens it and nothing else moves it the other
    way. Some circuit signals are **two-sided**: liquid-line subcooling falls on undercharge and
    rises on overcharge or non-condensables, so both directions are faults and a one-sided monitor
    would miss half of them. Pass ``direction="both"`` for those.
    """

    def __init__(
        self,
        baseline,
        *,
        slack_sigma: float = CUSUM_SLACK_SIGMA,  # PROVISIONAL/UNTUNED -- see the module note
        limit_sigma: float = CUSUM_LIMIT_SIGMA,  # PROVISIONAL/UNTUNED
        clip_sigma: float = CUSUM_CLIP_SIGMA,  # PROVISIONAL/UNTUNED
        min_consecutive: int = CUSUM_MIN_CONSECUTIVE,  # PROVISIONAL/UNTUNED
        direction: str = "up",  # "up" (default, one-sided) | "both" (two-sided signals)
    ):
        if not baseline.sigma_f > 0:
            raise ValueError(
                "a baseline with no residual scatter cannot support a sigma-scaled CUSUM "
                "(sigma_f must be > 0)"
            )
        if direction not in ("up", "both"):
            raise ValueError(f"direction must be 'up' or 'both', got {direction!r}")
        self.slack_sigma = slack_sigma
        self.limit_sigma = limit_sigma
        self.clip_sigma = clip_sigma
        self.min_consecutive = min_consecutive
        self.direction = direction
        self.baseline = baseline
        self._start(baseline)

    def _start(self, baseline) -> None:
        """(Re)build the accumulator against ``baseline`` and clear all state."""
        self.baseline = baseline
        sigma = baseline.sigma_f
        self.limit_f = self.limit_sigma * sigma
        self.clip_f = self.clip_sigma * sigma
        self._cusum = OnlineCusum(
            baseline.predict, limit=self.limit_f, slack=self.slack_sigma * sigma
        )
        self._over = 0
        self._over_down = 0

    def reset(self) -> None:
        """Clear the accumulator, keeping the current baseline."""
        self._start(self.baseline)

    def rebase(self, baseline) -> None:
        """Swap in a newly accepted baseline **and** clear the accumulator.

        Both halves are required. A baseline moves only when an operator accepts a new normal
        (:meth:`camber.store.modelstore.BaselineStore.accept_new_normal`); carrying the old
        accumulator across that change would immediately re-alarm on drift already accepted.
        """
        if not baseline.sigma_f > 0:
            raise ValueError("cannot rebase onto a baseline with no residual scatter")
        self._start(baseline)

    def update(self, tons, approach_f) -> DriftAlarmState:
        """Fold one (load, approach) sample; return the alarm state after it."""
        predicted = float(self.baseline.predict(tons))
        actual = float(approach_f)
        # Only the accumulator sees the clipped value; the reported residual stays truthful.
        clipped = min(max(actual, predicted - self.clip_f), predicted + self.clip_f)
        st = self._cusum.update(tons, clipped)
        # Over the limit is necessary but not sufficient: the accumulator decays only by `slack` per
        # sample, so an ended burst lingers above the limit for many samples. Require this sample to
        # be elevated too, so the interval measures a shift that is still happening.
        resid_clipped = clipped - predicted
        self._over = (
            self._over + 1 if (st.low >= self.limit_f and resid_clipped > self._cusum.slack) else 0
        )
        # The falling side is tracked always but only alarms when direction == "both".
        self._over_down = (
            self._over_down + 1
            if (st.high >= self.limit_f and -resid_clipped > self._cusum.slack)
            else 0
        )
        up = self._over >= self.min_consecutive
        down = self.direction == "both" and self._over_down >= self.min_consecutive
        alarming = up or down
        return DriftAlarmState(
            n=st.n,
            residual_f=round(actual - predicted, 4),
            climbing=round(st.low, 4),
            improving=round(st.high, 4),
            consecutive_over=max(self._over, self._over_down if self.direction == "both" else 0),
            alarming=alarming,
            alarm="drift" if alarming else None,
            alarm_direction=("up" if up else "down") if alarming else None,
        )

    def run(
        self,
        frame: pd.DataFrame,
        *,
        approach_col="approach_f",
        tons_col: str = "tons",
        min_tons: float = 5.0,
        approach_range: tuple[float, float] = (0.0, 50.0),
    ) -> DriftAlarmRun | None:
        """Fold a whole period through the monitor in order; summarize when it first alarmed.

        Applies the same load/plausibility guards the period statistic uses, so both read the same
        samples. Returns ``None`` when the guards leave nothing to score.

        The argument spelling is the approach one this class was written against; the underlying
        cleaner is metric-neutral, so any load-dependent degF column works (subcooling, condenser-
        water range) by passing its column key as ``approach_col``.
        """
        w = _clean(frame, approach_col, tons_col, min_load=min_tons, metric_range=approach_range)
        if w.empty:
            return None
        first_n, first_at, first_dir = -1, "", None
        peak_up, peak_down, last = 0.0, 0.0, float("nan")
        for label, tons, metric in zip(w.index, w["tons"].to_numpy(), w["metric"].to_numpy()):
            st = self.update(tons, metric)
            peak_up = max(peak_up, st.climbing)
            peak_down = max(peak_down, st.improving)
            last = st.residual_f
            if st.alarming and first_n < 0:
                first_n, first_at, first_dir = st.n, str(label), st.alarm_direction
        return DriftAlarmRun(
            n=int(len(w)),
            alarmed=first_n > 0,
            first_alarm_n=first_n,
            first_alarm_at=first_at,
            peak_climbing=round(float(peak_up), 4),
            limit_f=round(float(self.limit_f), 4),
            final_residual_f=round(float(last), 4) if last == last else float("nan"),
            alarm_direction=first_dir,
            peak_improving=round(float(peak_down), 4),
        )
