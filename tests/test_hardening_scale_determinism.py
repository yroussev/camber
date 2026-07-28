"""Hardening: portfolio-scale fleet rollup + a determinism sweep over the numeric routines.

Broadens `validation.check_determinism` from ~2 files to a net over the core numeric engines, and
proves `build_fleet_report` scales (the EUI-percentile loop is now O(N log N), not O(N^2)).
"""

import os
import sys
import time

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.report.fleet import build_fleet_report  # noqa: E402
from camber.rules.base import Finding  # noqa: E402
from camber.validation import check_determinism  # noqa: E402

# --- scale --------------------------------------------------------------------


def test_fleet_report_scales_to_500_buildings():
    rng = np.random.default_rng(0)
    buildings = [
        {
            "site": f"B{i}",
            "eui": float(e),
            "findings": [
                Finding(rule="simultaneous_heat_cool", equip="AHU", severity="fault", summary="x")
            ],
        }
        for i, e in enumerate(rng.uniform(30, 120, 500))
    ]
    t = time.time()
    fr = build_fleet_report(buildings)
    assert len(fr.buildings) == 500 and (time.time() - t) < 2.0  # sub-quadratic, comfortably fast


def test_fleet_percentile_matches_bruteforce():
    rng = np.random.default_rng(1)
    bs = [
        {"site": f"B{i}", "eui": float(e), "findings": []}
        for i, e in enumerate(rng.uniform(30, 120, 80))
    ]
    fr = build_fleet_report(bs)
    euis = sorted(b["eui"] for b in bs)
    for b in fr.buildings:
        brute = round(100.0 * sum(1 for e in euis if e >= b.eui) / len(euis), 0)
        assert b.eui_percentile == brute  # bisect result equals the O(N) definition


# --- determinism sweep --------------------------------------------------------


def _cal():
    from camber.mandv.rc_model import RCModel, calibrate, daily_schedule

    idx = pd.date_range("2024-01-01", periods=24 * 14, freq="1h")
    oat = 45 + 15 * np.sin((idx.hour - 6) / 24 * 2 * np.pi)
    s = daily_schedule(idx)
    return calibrate(oat, s, RCModel(0.8, 3.0, 24.0).predict(oat, s)).model.as_dict()


def _best_model():
    from camber.mandv.models import best_model

    rng = np.random.default_rng(0)
    T = np.linspace(20, 90, 200)
    y = np.maximum(60 - T, 0) * 1.5 + rng.normal(0, 1, 200)
    return best_model(T, y).predict(np.array([30.0, 50.0, 80.0])).tolist()


def _level_shifts():
    from camber.changedetect import detect_level_shifts

    idx = pd.date_range("2024-01-01", periods=200, freq="1h")
    s = pd.Series(np.concatenate([np.full(100, 5.0), np.full(100, 9.0)]), index=idx)
    return repr(detect_level_shifts(s))


def _cohort():
    from camber.rules.cohort import CohortDeviation

    idx = pd.date_range("2024-01-01", periods=48, freq="1h")
    frames = {
        f"V{i}": pd.DataFrame({Role.AIRFLOW: np.full(48, 100.0 + 40 * (i == 3))}, index=idx)
        for i in range(6)
    }
    return CohortDeviation(Role.AIRFLOW).analyze_fleet(frames).as_dict()


def _faultlab():
    from camber import faultlab

    return repr(faultlab.labeled_records())


@pytest.mark.parametrize(
    "fn",
    [_cal, _best_model, _level_shifts, _cohort, _faultlab],
    ids=["calibrate", "best_model", "level_shifts", "cohort", "faultlab"],
)
def test_numeric_routine_is_deterministic(fn):
    assert check_determinism(fn, runs=3).deterministic
