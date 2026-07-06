"""Tests for grid-interactive (GEB) analytics (camber.geb)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.geb import carbon_aware_shift, demand_response, flexibility  # noqa: E402


def _load(vals, start="2026-07-01 00:00"):
    return pd.Series(vals, index=pd.date_range(start, periods=len(vals), freq="1h"))


def test_demand_response_shed_vs_baseline():
    # 12 hourly samples; an event from hour 4–7 where load drops to 60 vs a 100 baseline
    vals = [100.0] * 12
    for h in (4, 5, 6, 7):
        vals[h] = 60.0
    load = _load(vals)
    r = demand_response(load, 100.0, event_start=load.index[4], event_end=load.index[7])
    assert r.event_hours == 4.0
    assert abs(r.energy_shed_kwh - 160.0) < 1e-6          # 4h × 40 kW
    assert r.avg_shed_kw == 40.0 and r.peak_shed_kw == 40.0
    assert abs(r.pct_shed - 0.4) < 1e-6


def test_demand_response_rebound_detected():
    vals = [100.0] * 12
    for h in (4, 5):
        vals[h] = 50.0          # shed
    for h in (6, 7):
        vals[h] = 140.0         # snap-back above baseline
    load = _load(vals)
    r = demand_response(load, 100.0, event_start=load.index[4], event_end=load.index[5],
                        rebound_hours=2.0)
    assert r.energy_shed_kwh == 100.0                     # 2h × 50
    assert r.rebound_kwh == 80.0                          # 2h × 40 over baseline after the event


def test_demand_response_series_baseline():
    load = _load([100.0] * 8)
    base = _load([120.0] * 8)                             # baseline higher than actual everywhere
    r = demand_response(load, base, event_start=load.index[2], event_end=load.index[5])
    assert r.avg_shed_kw == 20.0 and r.event_hours == 4.0


def test_demand_response_array_baseline_aligned():
    # a raw array baseline (one value per sample) is aligned to the load index, not silently zeroed
    load = _load([100.0] * 8)
    r = demand_response(load, np.full(8, 120.0), event_start=load.index[2], event_end=load.index[5])
    assert r.avg_shed_kw == 20.0 and r.baseline_kwh > 0


def test_demand_response_bad_array_baseline_raises():
    load = _load([100.0] * 8)
    try:
        demand_response(load, np.full(5, 120.0),          # wrong length -> explicit error, not zeros
                        event_start=load.index[2], event_end=load.index[5])
        assert False
    except ValueError:
        pass


def test_flexibility_sheddable_above_baseload():
    # baseload ~40, peaks to 100
    rng = np.random.default_rng(1)
    occ = np.array([40 if h % 24 < 7 or h % 24 > 18 else 100 for h in range(240)], dtype=float)
    load = _load(occ + rng.normal(0, 1, 240))
    f = flexibility(load, baseload_pct=10.0)
    assert 38 < f.baseload_kw < 43
    assert f.sheddable_kw > 0 and 0 < f.sheddable_frac < 1
    assert f.peak_to_average > 1.0


def test_carbon_aware_shift_saves_co2():
    # emissions factor: dirty midday, clean overnight
    ef = _load([0.2] * 6 + [0.6] * 6 + [0.2] * 12)        # kgCO2/kWh over 24h
    load = _load([50.0] * 24)
    out = carbon_aware_shift(load, ef, shift_kwh=100.0)
    assert out["ef_high"] > out["ef_low"]
    assert out["co2_saved_kg"] > 0
    assert abs(out["co2_saved_kg"] - 100.0 * out["spread_kg_per_kwh"]) < 1e-6


def test_carbon_aware_shift_zero_when_no_shift():
    ef = _load([0.3] * 24)
    out = carbon_aware_shift(_load([10.0] * 24), ef, shift_kwh=0.0)
    assert out["co2_saved_kg"] == 0.0


# --------------------------------------------------------------------------- operation score (Day 4)

from camber.geb import operation_score  # noqa: E402


def test_operation_score_rewards_cheap_hour_use():
    # signal cheap overnight (0.1), expensive midday (0.5)
    sig = _load([0.1] * 6 + [0.5] * 6 + [0.1] * 12)
    # good building runs mostly overnight; bad building runs mostly midday
    good = _load([80.0] * 6 + [10.0] * 6 + [80.0] * 12)
    bad = _load([10.0] * 6 + [80.0] * 6 + [10.0] * 12)
    g = operation_score(good, sig, label="price")
    b = operation_score(bad, sig, label="price")
    assert g.score > b.score                              # timing rewarded
    assert g.load_weighted_avg < g.flat_avg               # good building beats flat
    assert 0.0 <= g.score <= 1.0 and 0.0 <= b.score <= 1.0


def test_operation_score_flat_is_mid():
    sig = _load([0.1] * 6 + [0.5] * 6 + [0.1] * 12)
    flat = _load([50.0] * 24)                             # indifferent operation
    r = operation_score(flat, sig)
    assert abs(r.load_weighted_avg - r.flat_avg) < 1e-9   # even load -> paid the flat average
    assert abs(r.vs_flat_pct) < 1e-6


def test_operation_score_empty_or_zero_load():
    r = operation_score(_load([0.0] * 5), _load([0.3] * 5))
    assert r.score != r.score                             # NaN when no energy used
