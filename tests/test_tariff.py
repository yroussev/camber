"""Tests for the native utility tariff engine (camber.tariff)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.tariff import (  # noqa: E402
    BillResult, Tariff, compute_bill, flat_tariff, hours_schedule, tou_tariff,
    validate_bill,
)


def _bill(**totals):
    """A BillResult with given {period: total} months (other fields unused here)."""
    months = [{"period": p, "total": float(t)} for p, t in totals.items()]
    return BillResult(months=months, energy_charge=0.0, demand_charge=0.0,
                      fixed_charge=0.0, total=sum(totals.values()), n_months=len(months))


def _hourly(kw, start="2025-01-01", periods=24 * 31):
    """A constant-kW hourly load series."""
    idx = pd.date_range(start, periods=periods, freq="1h")
    return pd.Series(np.full(periods, float(kw)), index=idx)


# --- flat tariff -------------------------------------------------------------- #

def test_flat_energy_only():
    load = _hourly(10, periods=24 * 31)            # 10 kW * 744 h = 7440 kWh in Jan
    bill = compute_bill(flat_tariff(0.15), load)
    assert bill.n_months == 1
    assert abs(bill.energy_charge - 7440 * 0.15) < 0.5
    assert bill.demand_charge == 0.0


def test_flat_with_demand_and_fixed():
    load = _hourly(10, periods=24 * 31)
    bill = compute_bill(flat_tariff(0.15, demand_rate=12.0, fixed_monthly=50.0), load)
    assert abs(bill.demand_charge - 10 * 12.0) < 0.01     # peak 10 kW * $12
    assert bill.fixed_charge == 50.0
    assert abs(bill.total - (7440 * 0.15 + 120 + 50)) < 0.5


# --- TOU ---------------------------------------------------------------------- #

def test_tou_charges_peak_hours_higher():
    # 1 kW constant for a 30-day month; peak hours 16-20 (5 h/day)
    load = _hourly(1, periods=24 * 30)
    t = tou_tariff(off_peak_rate=0.10, peak_rate=0.40, peak_hours=range(16, 21))
    bill = compute_bill(t, load)
    # 30 days: peak = 5h*30=150 kWh @0.40; off-peak = 19h*30=570 kWh @0.10
    assert abs(bill.energy_charge - (150 * 0.40 + 570 * 0.10)) < 1.0


# --- tiered (block) energy ---------------------------------------------------- #

def test_tiered_energy_blocks():
    load = _hourly(10, periods=24 * 31)            # 7440 kWh
    t = Tariff(energy_rates=[[(5000, 0.10), (None, 0.20)]])   # first 5000 @0.10, rest @0.20
    bill = compute_bill(t, load)
    assert abs(bill.energy_charge - (5000 * 0.10 + 2440 * 0.20)) < 0.5


# --- demand ratchet ----------------------------------------------------------- #

def test_demand_ratchet_floors_later_months():
    # Jan peak 100 kW; Feb peak only 20 kW but a 80% ratchet floors billed demand at 80 kW
    jan = _hourly(100, start="2025-01-01", periods=24 * 31)
    feb = _hourly(20, start="2025-02-01", periods=24 * 28)
    load = pd.concat([jan, feb])
    t = flat_tariff(0.0, demand_rate=10.0)
    t.ratchet_pct = 80.0
    bill = compute_bill(t, load)
    feb_row = [m for m in bill.months if m["period"] == "2025-02"][0]
    assert abs(feb_row["demand"] - 80 * 10.0) < 0.01      # ratcheted to 80 kW, not 20


# --- schedule helper ---------------------------------------------------------- #

def test_hours_schedule_shape():
    s = hours_schedule(range(16, 21))
    assert len(s) == 12 and len(s[0]) == 24
    assert s[5][17] == 1 and s[5][9] == 0


def test_empty_load():
    bill = compute_bill(flat_tariff(0.15), pd.Series([], dtype=float))
    assert bill.n_months == 0 and bill.total == 0.0


# --- bill recalculation / validation ------------------------------------------ #

def test_validate_bill_matches_within_tolerance():
    comp = _bill(**{"2025-01": 1000.0, "2025-02": 1000.0})
    v = validate_bill(comp, {"2025-01": 1010.0, "2025-02": 990.0}, tol_pct=5.0)
    assert v.verdict == "validated"
    assert v.n_checked == 2 and v.n_within == 2
    assert v.mape < 2.0


def test_validate_bill_flags_discrepancy_high():
    comp = _bill(**{"2025-01": 1000.0, "2025-02": 1000.0})
    v = validate_bill(comp, {"2025-01": 1300.0, "2025-02": 1000.0}, tol_pct=5.0)
    assert v.verdict == "discrepancy"
    jan = [m for m in v.months if m.period == "2025-01"][0]
    assert jan.status == "high" and jan.pct_diff > 20      # invoice above the recalc


def test_validate_bill_minor_when_avg_within_tol():
    comp = _bill(**{"2025-01": 1000.0, "2025-02": 1000.0})
    v = validate_bill(comp, {"2025-01": 1080.0, "2025-02": 1000.0}, tol_pct=5.0)
    assert v.verdict == "minor"                            # one month 8% off, avg <= tol
    assert v.n_within == 1


def test_validate_bill_skips_months_without_invoice():
    comp = _bill(**{"2025-01": 1000.0, "2025-02": 1000.0})
    v = validate_bill(comp, {"2025-01": 1000.0}, tol_pct=5.0)
    assert v.n_checked == 1
    feb = [m for m in v.months if m.period == "2025-02"][0]
    assert feb.status == "no_actual"


def test_validate_bill_accepts_component_dict():
    comp = _bill(**{"2025-01": 1000.0})
    v = validate_bill(comp, {"2025-01": {"total": 1000.0, "energy": 800.0}}, tol_pct=5.0)
    assert v.verdict == "validated"
