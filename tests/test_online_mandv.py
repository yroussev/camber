"""Tests for online/streaming M&V monitors (camber.mandv.online)."""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv.online import OnlineCusum, RollingAnomaly  # noqa: E402


def test_online_cusum_accumulates_savings():
    # baseline predicts 100 regardless of driver; actual runs at 90 -> 10 savings/step
    m = OnlineCusum(lambda d: 100.0)
    states = [m.update(d, 90.0) for d in range(5)]
    assert states[-1].n == 5
    assert abs(states[-1].cusum - 50.0) < 1e-9            # 5 × 10
    assert states[-1].last_residual == 10.0


def test_online_cusum_tabular_alarm_savings_and_waste():
    m = OnlineCusum(lambda d: 100.0, limit=25.0, slack=1.0)
    # sustained savings of 10/step -> high accumulator crosses 25 within 3 steps
    a = None
    for _ in range(3):
        a = m.update(0, 90.0)
    assert a.alarm == "savings" and a.high >= 25.0
    # now sustained waste -> low accumulator eventually alarms
    m2 = OnlineCusum(lambda d: 100.0, limit=25.0, slack=1.0)
    b = None
    for _ in range(4):
        b = m2.update(0, 110.0)
    assert b.alarm == "waste"


def test_online_cusum_accepts_array_predict_and_reset():
    m = OnlineCusum(lambda d: np.array([100.0]))          # model.predict-style length-1 array
    s = m.update(1, 95.0)
    assert s.last_residual == 5.0
    m.reset()
    assert m.n == 0 and m.cusum == 0.0


def test_rolling_anomaly_flags_spike_when_warm():
    rng = np.random.default_rng(0)
    m = RollingAnomaly(window=48, k=3.5, min_samples=10)
    states = [m.update(float(x)) for x in rng.normal(0, 1, 60)]
    assert states[0].warm is False and not states[0].is_anomaly   # cold start never flags
    assert states[-1].warm is True
    spike = m.update(50.0)                                # a clear outlier vs ~N(0,1)
    assert spike.is_anomaly and abs(spike.z) >= 3.5


def test_rolling_anomaly_quiet_stream_no_false_positive():
    m = RollingAnomaly(window=24, k=3.5, min_samples=8)
    flags = [m.update(5.0 + (i % 2) * 0.1).is_anomaly for i in range(40)]  # tiny variation
    assert not any(flags)                                 # near-constant -> no anomalies
