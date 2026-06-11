"""Tests for ECM financial metrics (camber.finance)."""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.finance import ecm_financials, irr, npv  # noqa: E402


# --- primitives --------------------------------------------------------------- #

def test_npv_basic():
    # -100 today, +110 in one year, at 10% -> NPV 0
    assert abs(npv(0.10, [-100, 110])) < 1e-9
    assert npv(0.0, [-100, 50, 50, 50]) == 50.0


def test_irr_basic():
    # -1000 then 500/yr for 3 years -> IRR ~23.4%
    r = irr([-1000, 500, 500, 500])
    assert 0.23 < r < 0.24
    assert abs(npv(r, [-1000, 500, 500, 500])) < 1e-3


def test_irr_undefined_returns_nan():
    assert math.isnan(irr([-1000, 0, 0, 0]))     # never recovers -> no IRR


# --- ECM roll-up -------------------------------------------------------------- #

def test_simple_payback_and_npv():
    r = ecm_financials(10000, 2500, life_years=10, discount_rate=0.06)
    assert r.simple_payback_years == 4.0          # 10000 / 2500
    assert r.npv > 0 and r.sir > 1.0              # profitable at 6%
    assert 0.20 < r.irr < 0.25                    # ~21.4% IRR for 2500/yr * 10yr / 10000


def test_discounted_payback_after_simple():
    r = ecm_financials(10000, 2500, life_years=10, discount_rate=0.10)
    # discounting pushes payback later than the simple 4.0 years
    assert r.discounted_payback_years > r.simple_payback_years


def test_unprofitable_measure():
    # expensive measure, tiny savings, short life -> negative NPV, SIR<1, payback long
    r = ecm_financials(50000, 1000, life_years=5, discount_rate=0.06)
    assert r.npv < 0 and r.sir < 1.0
    assert math.isnan(r.discounted_payback_years)   # never recovers within life
    assert r.simple_payback_years == 50.0


def test_escalation_and_om():
    base = ecm_financials(10000, 2500, life_years=10, discount_rate=0.06)
    esc = ecm_financials(10000, 2500, life_years=10, discount_rate=0.06, escalation=0.03)
    assert esc.npv > base.npv                       # escalating savings worth more
    om = ecm_financials(10000, 2500, life_years=10, discount_rate=0.06, annual_om=500)
    assert om.npv < base.npv                        # O&M drags it down
    assert om.simple_payback_years == 5.0           # 10000 / (2500-500)


def test_cashflow_shape():
    r = ecm_financials(10000, 2500, life_years=3, discount_rate=0.0)
    assert r.cashflows[0] == -10000.0
    assert len(r.cashflows) == 4 and r.cashflows[1] == 2500.0
    assert r.npv == -10000 + 3 * 2500              # at 0% discount, NPV = sum
