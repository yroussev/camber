"""Tests for the OpenEI URDB fetch + mapping (camber.interop.openei)."""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.interop.openei import fetch_urdb_rate, tariff_from_urdb  # noqa: E402
from camber.tariff import compute_bill  # noqa: E402


# A minimal URDB-shaped rate: $10/mo fixed, two-tier energy, flat $/kW demand, schedules.
_URDB = {
    "label": "abc123",
    "name": "Test TOU rate",
    "fixedchargefirstmeter": 10.0,
    "fixedchargeunits": "$/month",
    "energyratestructure": [
        [{"max": 5000, "rate": 0.10}, {"rate": 0.20}],   # period 0: tiered
        [{"rate": 0.40}],                                 # period 1: peak
    ],
    "energyweekdayschedule": [[0] * 16 + [1] * 5 + [0] * 3 for _ in range(12)],
    "energyweekendschedule": [[0] * 24 for _ in range(12)],
    "flatdemandstructure": [[{"rate": 12.0}]],
    "flatdemandmonths": [0] * 12,
    "demandratchetpercentage": [0.8] * 12,
}


def test_tariff_from_urdb_maps_fields():
    t = tariff_from_urdb(_URDB)
    assert t.fixed_monthly == 10.0
    assert t.energy_rates[0] == [(5000, 0.10), (None, 0.20)]
    assert t.energy_rates[1] == [(None, 0.40)]
    assert t.flat_demand_rates == [[(None, 12.0)]]
    assert t.flat_demand_months == [0] * 12
    assert t.ratchet_pct == 80.0           # 0.8 fraction normalized to percent


def test_mapped_tariff_bills():
    t = tariff_from_urdb(_URDB)
    idx = pd.date_range("2025-06-01", periods=24 * 30, freq="1h")
    load = pd.Series(np.full(len(idx), 2.0), index=idx)   # 2 kW constant
    bill = compute_bill(t, load)
    assert bill.n_months == 1
    assert bill.fixed_charge == 10.0
    assert bill.demand_charge > 0 and bill.energy_charge > 0


def test_fetch_with_injected_transport():
    captured = {}

    def fake_transport(url):
        captured["url"] = url
        return {"items": [_URDB]}

    rate = fetch_urdb_rate("abc123", "FAKE_KEY", transport=fake_transport)
    assert rate["label"] == "abc123"
    assert "getpage=abc123" in captured["url"] and "api_key=FAKE_KEY" in captured["url"]


def test_fetch_no_results_raises():
    with pytest.raises(ValueError):
        fetch_urdb_rate("missing", "K", transport=lambda url: {"items": []})
