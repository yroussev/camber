"""Confidence labelling for drift-detector thresholds: two classes, two different epistemic states.

Every chiller drift detector in CAMBER carries thresholds, and every one of them is flagged
``thresholds_provisional`` on its Findings. That single flag is too coarse, because the thresholds
are not all provisional in the same way, and treating them as one class either overstates the weak
ones or understates the strong ones. There are two classes:

**Magnitude floors** -- the degF and sigma floors a period statistic must clear before it is a warn
or a fault (``DRIFT_WARN_F``, ``SUBCOOLING_FAULT_SIGMA``, and their siblings). These are
*screening-grade*: they are characterized against the observed behaviour of the signal class, and
the paired degF-and-sigma construction means a tight baseline cannot fire on a thermally
meaningless fraction of a degree and a noisy one cannot bury a real move. They are good enough to
rank equipment for a walkdown. They are **not** established on the specific machines being
monitored, so they should not be dispatched on without a local review.

**Temporal / CUSUM parameters** -- the slack, limit, clip and decision-interval settings that decide
whether a shift counts as *sustained* (:mod:`camber.chillerdrift`). These are weaker. They are
textbook tabular-CUSUM starting points adjusted by engineering judgement for sample rate, and they
have never been tuned for these signals at all: nobody has measured the false-alarm rate or the
detection delay they actually produce on chiller trend data. Their run-length behaviour is a
prediction, not a measurement. **Full temporal validation awaits real trended fault data** -- a
window of chiller trends with confirmed, dated fault events -- and until that exists, a sustained
alarm should be read as "worth looking at now" and never as a dispatch-grade verdict.

The distinction is deliberately made visible rather than left to the source comments: it travels on
the Finding, so a downstream consumer that decides what to act on can see which kind of claim it is
holding. :func:`threshold_confidence` produces that metadata block; the detectors merge it into
``Finding.metrics``.
"""

from __future__ import annotations

__all__ = [
    "MAGNITUDE_CONFIDENCE",
    "TEMPORAL_CONFIDENCE",
    "MAGNITUDE_NOTE",
    "TEMPORAL_NOTE",
    "threshold_confidence",
]

#: Magnitude floors: characterized on the signal class, adequate for screening, not for dispatch.
MAGNITUDE_CONFIDENCE = "screening-grade"

#: Temporal/CUSUM parameters: never tuned for these signals; run-length behaviour is unmeasured.
TEMPORAL_CONFIDENCE = "provisional-untuned"

MAGNITUDE_NOTE = (
    "magnitude floors (degF and sigma) are screening-grade: characterized for this signal class "
    "and adequate for ranking equipment, but not established on this equipment"
)

TEMPORAL_NOTE = (
    "temporal/CUSUM parameters are provisional and untuned for this signal: the false-alarm rate "
    "and detection delay they produce have not been measured, so a sustained alarm is a prompt to "
    "look, not a verdict; full temporal validation awaits real trended fault data"
)


def threshold_confidence(*, magnitude: bool = True, temporal: bool = False) -> dict:
    """Metadata describing how much a detector's thresholds can be trusted.

    Merge the result into a Finding's ``metrics``. ``magnitude`` marks that the Finding's severity
    rests on degF/sigma floors; ``temporal`` marks that it also (or instead) rests on CUSUM timing
    parameters. ``thresholds_provisional`` stays in the payload unchanged -- this refines the
    labelling rather than replacing it, so existing consumers keep working.

    Keys returned: ``thresholds_provisional`` (always), ``magnitude_threshold_confidence`` and/or
    ``temporal_threshold_confidence``, and a human-readable ``threshold_confidence_note``.
    """
    out: dict = {"thresholds_provisional": True}
    notes = []
    if magnitude:
        out["magnitude_threshold_confidence"] = MAGNITUDE_CONFIDENCE
        notes.append(MAGNITUDE_NOTE)
    if temporal:
        out["temporal_threshold_confidence"] = TEMPORAL_CONFIDENCE
        notes.append(TEMPORAL_NOTE)
    out["threshold_confidence_note"] = "; ".join(notes)
    return out
