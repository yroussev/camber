"""Tests for the free-cooling (economizer) opportunity quantifier (camber.freecooling)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.freecooling import FreeCoolingOpportunity, free_cooling_opportunity  # noqa: E402


def _data(days=30, seed=0):
    idx = pd.date_range("2025-04-01", periods=days * 24, freq="1h")
    rng = np.random.default_rng(seed)
    oat = pd.Series(
        55 + 15 * np.sin(np.arange(len(idx)) * 2 * np.pi / 24) + rng.normal(0, 3, len(idx)),
        index=idx,
    )
    cool = pd.Series(np.where((idx.hour >= 8) & (idx.hour <= 18), 0.6, 0.0), index=idx)
    kw = pd.Series(np.where(cool > 0, 50.0, 0.0), index=idx)
    return oat, cool, kw


def test_quantifies_missed_hours_energy_and_savings():
    oat, cool, kw = _data()
    r = free_cooling_opportunity(oat, cool, cooling_kw=kw, recover_frac=0.7, price_per_kwh=0.15)
    assert isinstance(r, FreeCoolingOpportunity)
    assert r.hours_missed > 0 and 0 < r.missed_fraction <= 1
    assert r.addressable_kwh > 0
    assert abs(r.recoverable_kwh - r.addressable_kwh * 0.7) < 1.0
    assert abs(r.savings_usd - r.recoverable_kwh * 0.15) < 1.0


def test_no_mechanical_cooling_no_opportunity():
    oat, _, kw = _data()
    r = free_cooling_opportunity(oat, pd.Series(0.0, index=oat.index), cooling_kw=kw)
    assert r.hours_missed == 0.0 and r.recoverable_kwh == 0.0


def test_no_price_returns_energy_but_nan_savings():
    oat, cool, kw = _data()
    r = free_cooling_opportunity(oat, cool, cooling_kw=kw)
    assert r.recoverable_kwh > 0 and r.savings_usd != r.savings_usd  # NaN savings, energy known


def test_no_power_series_gives_hours_only():
    oat, cool, _ = _data()
    r = free_cooling_opportunity(oat, cool)  # no cooling_kw
    assert r.hours_missed > 0 and r.addressable_kwh == 0.0


def test_high_limit_widens_available_hours():
    oat, cool, kw = _data()
    low = free_cooling_opportunity(oat, cool, cooling_kw=kw, high_limit_f=55.0)
    high = free_cooling_opportunity(oat, cool, cooling_kw=kw, high_limit_f=70.0)
    assert high.hours_available > low.hours_available  # a higher limit = more free-cooling weather


def test_empty_input():
    r = free_cooling_opportunity(pd.Series(dtype=float), pd.Series(dtype=float))
    assert r.hours_missed == 0.0 and r.missed_fraction != r.missed_fraction  # NaN


# --- 0.98 (#91): one free-cooling high limit for the library and the rule -------------------


def test_library_and_rule_share_one_high_limit_default():
    import inspect

    from camber.freecooling import DEFAULT_FREE_COOLING_HIGH_LIMIT_F
    from camber.rules.freecoolingmissed_rule import FreeCoolingMissed

    assert DEFAULT_FREE_COOLING_HIGH_LIMIT_F == 60.0
    lib = inspect.signature(free_cooling_opportunity).parameters["high_limit_f"].default
    assert lib == DEFAULT_FREE_COOLING_HIGH_LIMIT_F
    assert FreeCoolingMissed().high_limit_f == DEFAULT_FREE_COOLING_HIGH_LIMIT_F


def test_default_call_counts_only_weather_below_60f():
    oat, cool, kw = _data()
    r = free_cooling_opportunity(oat, cool, cooling_kw=kw)
    assert r.high_limit_f == 60.0
    explicit = free_cooling_opportunity(oat, cool, cooling_kw=kw, high_limit_f=60.0)
    assert (r.hours_available, r.hours_missed, r.addressable_kwh) == (
        explicit.hours_available,
        explicit.hours_missed,
        explicit.addressable_kwh,
    )
    # the pre-0.98 default counts more free-cooling weather; it is still one argument away
    old = free_cooling_opportunity(oat, cool, cooling_kw=kw, high_limit_f=65.0)
    assert old.hours_available > r.hours_available and old.hours_missed >= r.hours_missed
