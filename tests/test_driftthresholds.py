"""Tests for drift-threshold confidence labelling (camber.driftthresholds).

The point of the module is that the two threshold classes must not be conflated, so these tests
pin the distinction rather than the exact wording.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.driftthresholds import (  # noqa: E402
    MAGNITUDE_CONFIDENCE,
    TEMPORAL_CONFIDENCE,
    threshold_confidence,
)


def test_the_two_classes_are_labelled_differently():
    """Conflating them is the failure this module exists to prevent."""
    assert MAGNITUDE_CONFIDENCE != TEMPORAL_CONFIDENCE
    assert "screening" in MAGNITUDE_CONFIDENCE
    assert "untuned" in TEMPORAL_CONFIDENCE


def test_a_magnitude_only_finding_makes_no_temporal_claim():
    meta = threshold_confidence(magnitude=True)
    assert meta["magnitude_threshold_confidence"] == MAGNITUDE_CONFIDENCE
    assert "temporal_threshold_confidence" not in meta
    assert "screening-grade" in meta["threshold_confidence_note"]


def test_a_temporal_only_finding_makes_no_magnitude_claim():
    meta = threshold_confidence(magnitude=False, temporal=True)
    assert meta["temporal_threshold_confidence"] == TEMPORAL_CONFIDENCE
    assert "magnitude_threshold_confidence" not in meta
    # the note must say what is missing and what would fix it
    assert "untuned" in meta["threshold_confidence_note"]
    assert "real trended fault data" in meta["threshold_confidence_note"]


def test_a_finding_can_carry_both_grades():
    meta = threshold_confidence(magnitude=True, temporal=True)
    assert meta["magnitude_threshold_confidence"] == MAGNITUDE_CONFIDENCE
    assert meta["temporal_threshold_confidence"] == TEMPORAL_CONFIDENCE


def test_the_provisional_flag_is_kept_whatever_the_grades():
    """Existing consumers key off this; the grades refine it, they do not replace it."""
    for kw in ({}, {"temporal": True}, {"magnitude": False, "temporal": True}):
        assert threshold_confidence(**kw)["thresholds_provisional"] is True
