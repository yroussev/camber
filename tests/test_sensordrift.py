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
    oat = (
        80
        + 18 * np.sin((h % 24 - 9) / 24 * 2 * np.pi)  # daily
        + 8 * np.sin(h / (24 * 60) * 2 * np.pi)  # seasonal-ish
        + rng.normal(0, 1.5, n)
    )
    return pd.Series(oat, index=idx)


def test_healthy_sensor_tracks_reference():
    ref = _weather()
    rng = np.random.default_rng(1)
    bas = ref + rng.normal(0, 0.5, len(ref))  # same signal, small noise
    r = compare_to_reference(bas, ref, name="oat")
    assert r.severity == "ok"
    assert abs(r.bias) < 2.0 and r.correlation > 0.95


def test_constant_bias_flagged():
    ref = _weather()
    bas = ref + 6.0  # BAS OAT reads 6F high everywhere
    r = compare_to_reference(bas, ref, name="oat")
    assert r.severity == "fault"
    assert 5.5 < r.bias < 6.5
    assert "biased" in r.verdict or r.bias >= 5.0


def test_drift_over_time_flagged():
    ref = _weather()
    months = (ref.index - ref.index[0]).total_seconds().to_numpy() / (86400.0 * 30.44)
    bas = ref + 4.0 * months  # grows 4F per month -> drift
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
    bas = ref.iloc[:50]  # only 50 overlapping samples
    assert compare_to_reference(bas, ref, name="oat").severity == "info"


def test_alignment_on_shared_timestamps():
    ref = _weather()
    bas = (ref + 6.0).iloc[100:]  # offset in time and value
    r = compare_to_reference(bas, ref, name="oat")
    assert r.n == len(ref) - 100  # compared only the overlap
    assert r.bias > 5.0


def test_drift_finding_shape():
    ref = _weather()
    bas = ref + 6.0
    f = drift_finding(bas, ref, "AHU-1", Role.OAT)
    assert f.rule == "sensor_drift:oat"
    assert f.equip == "AHU-1"
    assert f.severity == "fault"
    assert f.metrics["bias"] > 5.0


# --- drift needs a span; verdict ordered by severity ------------------------ #


def _co2_pair(days, *, bias=0.0, drift_per_month=0.0, occupied_excess=300.0, seed=0):
    """Room vs exhaust CO2 at 10 min: they agree at night, the room runs higher while occupied."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-03-04", periods=days * 144, freq="10min")
    hour = idx.hour + idx.minute / 60
    occ = np.clip(np.sin((hour - 8) / 10 * np.pi), 0, None) * ((idx.dayofweek < 5).astype(float))
    ref = 420 + 500 * occ + rng.normal(0, 10, len(idx))
    months = (idx - idx[0]).total_seconds().to_numpy() / (86400 * 30.44)
    # the room-vs-exhaust gap grows through each occupied day and varies day to day
    daily_amp = rng.uniform(0.3, 1.0, days).repeat(144)
    sensor = ref + bias + drift_per_month * months + occupied_excess * occ * daily_amp
    return pd.Series(sensor, index=idx), pd.Series(ref, index=idx)


_CO2 = dict(bias_warn=50, bias_fault=100, drift_warn=20, drift_fault=50)


def test_short_window_does_not_extrapolate_drift():
    """Regression: 3 days with a daytime swing was reported 'fault, drifting +275/month'."""
    s, ref = _co2_pair(3, bias=7.0)
    r = compare_to_reference(s, ref, name="co2", **_CO2)
    assert r.drift_per_month is None
    assert any("drift not evaluated" in c for c in r.caveats)
    assert "drifting" not in r.verdict
    assert r.severity == "ok"


def test_drift_is_robust_to_the_diurnal_offset():
    """Regression: a least-squares slope over a daytime swing reported +25.7/month on ~-2/month."""
    s, ref = _co2_pair(75, drift_per_month=-2.0, seed=4)
    r = compare_to_reference(s, ref, name="co2", baseline_hours=range(1, 5), **_CO2)
    assert r.drift_per_month is not None
    assert abs(r.drift_per_month - -2.0) < 1.5
    assert r.n_drift_days >= 70
    assert "drifting" not in r.verdict


def test_bias_fault_leads_the_verdict():
    """Regression: a 148 ppm bias fault was headlined as 'drifting +28.7/month'."""
    s, ref = _co2_pair(60, bias=-148.0, drift_per_month=30.0, occupied_excess=0.0)
    r = compare_to_reference(s, ref, name="co2", **_CO2)
    assert r.severity == "fault"
    assert r.verdict.startswith("biased")
    assert "drifting" in r.verdict  # still reported, after the fault


def test_negative_bias_warn_is_expressed():
    """A site OAT reading ~4.7 F low against a station is a warn-level bias, stated as such."""
    ref = _weather()
    r = compare_to_reference(ref - 4.7, ref, name="oat")
    assert r.severity == "warn"
    assert r.verdict == "biased -4.7"
    assert abs(r.drift_per_month) < 0.1
