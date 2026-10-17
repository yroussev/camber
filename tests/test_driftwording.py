"""Tests for the shared ok-summary wording of the drift detectors (camber.rules._driftwording)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.rules._driftwording import holds_near_baseline, signed  # noqa: E402


def test_signed_keeps_the_sign_and_drops_it_at_zero():
    assert signed(1.26, 1) == "+1.3" and signed(-1.26, 1) == "-1.3"
    assert signed(0.04, 1) == "0.0" and signed(-0.04, 1) == "0.0" and signed(-0.4, 0) == "0"


def test_holds_near_baseline_names_the_shift_sigma_and_match():
    text = holds_near_baseline(
        "AHU_1", "filter DP", 0.054, -1.23, decimals=2, unit=" inH2O", matched="matched airflow"
    )
    assert text == (
        "AHU_1: filter DP holds near its baseline (+0.05 inH2O, 1.2σ vs frozen baseline at "
        "matched airflow)"
    )
    assert "(-3%, " in holds_near_baseline(
        "V", "VAV damper", -3.2, 0.5, decimals=0, unit="%", matched="matched command"
    )
