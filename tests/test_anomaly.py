"""Tests for the anomaly ensemble (camber.anomaly)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.anomaly import AnomalyResult, detect_anomalies  # noqa: E402


def _idx(n=300):
    return pd.date_range("2025-01-01", periods=n, freq="1h")


def _clean(seed=0):
    return pd.Series(50 + np.random.default_rng(seed).normal(0, 1, 300), index=_idx())


def test_clean_series_is_ok():
    r = detect_anomalies(_clean())
    assert isinstance(r, AnomalyResult)
    assert r.severity == "ok" and r.n_point_anomalies == 0 and r.n_change_points == 0


def test_spikes_flag_point_anomalies():
    s = _clean()
    s.iloc[50] += 30
    s.iloc[150] -= 30
    r = detect_anomalies(s)
    assert r.n_point_anomalies >= 2 and r.severity in ("warn", "fault")
    assert len(r.point_anomalies) == r.n_point_anomalies


def test_level_shift_flags_change_point():
    rng = np.random.default_rng(1)
    s = pd.Series(
        np.concatenate([50 + rng.normal(0, 1, 150), 70 + rng.normal(0, 1, 150)]), index=_idx()
    )
    r = detect_anomalies(s)
    assert r.n_change_points >= 1 and r.change_points


def test_two_change_points_are_a_fault():
    rng = np.random.default_rng(2)
    s = pd.Series(
        np.concatenate(
            [50 + rng.normal(0, 1, 100), 70 + rng.normal(0, 1, 100), 55 + rng.normal(0, 1, 100)]
        ),
        index=_idx(),
    )
    assert detect_anomalies(s).severity == "fault"  # >=2 change points


def test_forecast_residual_path():
    s = _clean()
    s.iloc[100] += 40
    fc = pd.Series(50.0, index=s.index)
    r = detect_anomalies(s, forecast=fc)
    assert r.n_point_anomalies >= 1 and r.severity in ("warn", "fault")


def test_jsonable():
    d = detect_anomalies(_clean()).as_dict()
    assert d["severity"] == "ok" and "change_points" in d and isinstance(d["point_anomalies"], list)


def test_intermittent_meter_still_faults_documents_the_limitation():
    """Pins a *deferred* limitation so it stays visible rather than being forgotten.

    `camber.ingest.quality` now reads a duty-cycled point within its two regimes, and
    `sensorhealth.sensor_trust` opts into that per role — but `detect_anomalies` is role-blind, so
    its quality door stays pooled, and its point test independently counts every burst. A healthy
    HHW BTU meter therefore still reads `fault` here. Fixing it means routing both doors, which is
    a real design question (is a burst an anomaly with no forecast? often yes).
    """
    rng = np.random.default_rng(0)
    base = np.where(np.arange(720) % 24 < 3, 50.0, 0.0)
    vals = np.clip(base + rng.normal(0, 1.5, 720), 0.0, None)
    s = pd.Series(vals, index=pd.date_range("2024-01-01", periods=720, freq="1h"))

    res = detect_anomalies(s)
    assert res.severity == "fault"  # not desired, but true — and now recorded
    assert res.n_point_anomalies > 0
