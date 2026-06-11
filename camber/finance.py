"""Energy-conservation-measure (ECM) financials: payback, NPV, IRR, SIR.

Turns a measure's capital cost and dollar savings (e.g. from the tariff engine applied
to an avoided-energy estimate) into the standard investment metrics a capital request
needs:

- **simple payback** -- years to recover cost at the year-one net saving (the quick screen),
- **discounted payback** -- the same, but recognizing the time value of money,
- **NPV** -- net present value of the cashflows at the discount rate (the go/no-go number),
- **IRR** -- the discount rate at which NPV is zero (compare to the hurdle rate),
- **SIR** -- savings-to-investment ratio, PV(savings)/cost (the FEMP/BLCC screen; >1 = worth it).

Dependency-free: NPV is the textbook discounted sum and IRR is solved by bisection on it,
so no `numpy_financial`. Savings may escalate (energy-price escalation) and carry annual
O&M and end-of-life salvage. Currency-agnostic; rates are real unless you feed nominal.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict


def npv(rate: float, cashflows) -> float:
    """Net present value of ``cashflows`` (index 0 = today) discounted at ``rate``."""
    return float(sum(cf / (1.0 + rate) ** i for i, cf in enumerate(cashflows)))


def irr(cashflows, *, lo: float = -0.9999, hi: float = 10.0, tol: float = 1e-7) -> float:
    """Internal rate of return: the rate where NPV(cashflows) == 0 (NaN if none exists).

    Solved by bisection over [lo, hi]; returns NaN when NPV doesn't change sign there
    (e.g. a measure that never recovers its cost at any rate).
    """
    f_lo, f_hi = npv(lo, cashflows), npv(hi, cashflows)
    if f_lo != f_lo or f_hi != f_hi or (f_lo > 0) == (f_hi > 0):
        return float("nan")
    for _ in range(200):
        mid = (lo + hi) / 2.0
        f_mid = npv(mid, cashflows)
        if abs(f_mid) < tol or (hi - lo) < tol:
            return round(mid, 6)
        if (f_mid > 0) == (f_lo > 0):
            lo, f_lo = mid, f_mid
        else:
            hi = mid
    return round((lo + hi) / 2.0, 6)


@dataclass
class ECMResult:
    """Financial metrics for one energy-conservation measure."""

    cost: float
    annual_savings: float         # year-one net saving (before escalation / O&M nuance)
    life_years: int
    discount_rate: float
    simple_payback_years: float   # inf if the net annual saving is <= 0
    discounted_payback_years: float  # NaN if never recovered within the measure life
    npv: float
    irr: float                    # NaN if undefined
    sir: float                    # PV(savings) / cost
    total_savings: float          # nominal sum of yearly net savings
    cashflows: list               # [-cost, net_1, ..., net_life]

    def as_dict(self) -> dict:
        """Return the result as a plain dict."""
        return asdict(self)


def ecm_financials(
    cost: float,
    annual_savings: float,
    *,
    life_years: int,
    discount_rate: float = 0.0,
    escalation: float = 0.0,      # annual growth of the savings (energy-price escalation)
    annual_om: float = 0.0,       # recurring O&M cost of the measure ($/yr)
    salvage: float = 0.0,         # one-time value at end of life
) -> ECMResult:
    """Compute payback / NPV / IRR / SIR for a measure costing ``cost`` up front.

    Year-t net saving is ``annual_savings * (1+escalation)**(t-1) - annual_om`` (plus
    ``salvage`` in the final year). ``discount_rate`` and ``escalation`` are fractions
    (0.06 = 6%).
    """
    net1 = annual_savings - annual_om
    cashflows = [-float(cost)]
    for t in range(1, int(life_years) + 1):
        sv = annual_savings * (1.0 + escalation) ** (t - 1) - annual_om
        if t == life_years:
            sv += salvage
        cashflows.append(sv)

    simple_pb = (cost / net1) if net1 > 0 else float("inf")

    # discounted payback: first year cumulative discounted cashflow turns non-negative
    disc_pb = float("nan")
    cum = cashflows[0]
    for t in range(1, len(cashflows)):
        cum += cashflows[t] / (1.0 + discount_rate) ** t
        if cum >= 0:
            disc_pb = float(t)
            break

    pv_savings = sum(cashflows[t] / (1.0 + discount_rate) ** t
                     for t in range(1, len(cashflows)))
    sir = (pv_savings / cost) if cost else float("inf")

    return ECMResult(
        cost=round(float(cost), 2),
        annual_savings=round(float(annual_savings), 2),
        life_years=int(life_years),
        discount_rate=discount_rate,
        simple_payback_years=round(simple_pb, 2) if simple_pb != float("inf") else float("inf"),
        discounted_payback_years=round(disc_pb, 2) if disc_pb == disc_pb else float("nan"),
        npv=round(npv(discount_rate, cashflows), 2),
        irr=irr(cashflows),
        sir=round(sir, 3),
        total_savings=round(sum(cashflows[1:]), 2),
        cashflows=[round(c, 2) for c in cashflows],
    )
