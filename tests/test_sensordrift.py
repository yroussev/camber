"""Tests for sensor bias/drift detection vs a reference (camber.sensordrift)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.sensordrift import compare_to_reference, drift_finding  # noqa: E402


def _weather(n=24 * 60):
    """A plausible external OAT reference: diurnal + seasonal swing + weather noise."""
    rng = np.random.default_rng(0)
    idx = pd.date_range("2025-06-01", periods=n, freq="1h")
    h = np.arange(n)
    oat = (80 + 18 * np.sin((h % 24 - 9) / 24 * 2 * np.pi)     # daily
           + 8 * np.sin(h / (24 * 60) * 2 * np.pi)             # seasonal-ish
           + rng.normal(0, 1.5, n))
    return pd.Series(oat, index=idx)


def test_healthy_sensor_tracks_reference():
    ref = _weather()
    rng = np.random.default_rng(1)
    bas = ref + rng.normal(0, 0.5, len(ref))      # same signal, small noise
    r = compare_to_reference(bas, ref, name="oat")
    assert r.severity == "ok"
    assert abs(r.bias) < 2.0 and r.correlation > 0.95


def test_constant_bias_flagged():
    ref = _weather()
    bas = ref + 6.0                               # BAS OAT reads 6F high everywhere
    r = compare_to_reference(bas, ref, name="oat")
    assert r.severity == "fault"
    assert 5.5 < r.bias < 6.5
    assert "biased" in r.verdict or r.bias >= 5.0


def test_drift_over_time_flagged():
    ref = _weather()
    months = (ref.index - ref.index[0]).total_seconds().to_numpy() / (86400.0 * 30.44)
    bas = ref + 4.0 * months                      # grows 4F per month -> drift
    r = compare_to_reference(bas, ref, name="oat")
    assert r.severity == "fault"
    assert r.drift_per_month > 3.0
    assert "drifting" in r.verdict


def test_not_tracking_flagged():
    ref = _weather()
    rng = np.random.default_rng(2)
    bas = pd.Series(rng.normal(75, 10, len(ref)), index=ref.index)  # unrelated noise
    r = compare_to_reference(bas, ref, name="oat")
    assert r.severity == "fault"
    assert r.correlation < 0.7
    assert "not tracking" in r.verdict


def test_insufficient_overlap_is_info():
    ref = _weather()
    bas = ref.iloc[:50]                           # only 50 overlapping samples
    assert compare_to_reference(bas, ref, name="oat").severity == "info"


def test_alignment_on_shared_timestamps():
    ref = _weather()
    bas = (ref + 6.0).iloc[100:]                  # offset in time and value
    r = compare_to_reference(bas, ref, name="oat")
    assert r.n == len(ref) - 100                  # compared only the overlap
    assert r.bias > 5.0


def test_drift_finding_shape():
    ref = _weather()
    bas = ref + 6.0
    f = drift_finding(bas, ref, "AHU-1", Role.OAT)
    assert f.rule == "sensor_drift:oat"
    assert f.equip == "AHU-1"
    assert f.severity == "fault"
    assert f.metrics["bias"] > 5.0
