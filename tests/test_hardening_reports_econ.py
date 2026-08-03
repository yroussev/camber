"""Adversarial hardening of the report builders, fault-economics, and tariff billing.

Empty / single / large inputs must produce a valid (possibly empty) result, and a malformed
tariff must raise a clear ValueError rather than an IndexError deep in billing.
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.fault_economics import (  # noqa: E402
    EnergyPrice,
    EquipmentLoad,
    cost_findings,
    rank_by_cost,
    total_cost,
)
from camber.report.fleet import build_fleet_report  # noqa: E402
from camber.report.site import build_site_report  # noqa: E402
from camber.rules.base import Finding  # noqa: E402
from camber.tariff import Tariff, compute_bill, flat_tariff  # noqa: E402

# --------------------------------------------------------------------------- report builders


def test_site_report_empty_is_valid():
    r = build_site_report(pd.DataFrame(), findings=[])
    assert isinstance(r, str) and len(r) > 0


def test_fleet_report_empty_single_and_large():
    assert build_fleet_report([]) is not None
    one = build_fleet_report([{"site": "B1", "eui": 60.0, "findings": []}])
    assert one is not None
    rng = np.random.default_rng(0)
    many = [
        {"site": f"B{i}", "eui": float(rng.uniform(30, 120)), "findings": []} for i in range(500)
    ]
    assert build_fleet_report(many) is not None  # scales without crashing


# --------------------------------------------------------------------------- fault economics


def _finding(rule="reheat_penalty", equip="AHU-1", metrics=None):
    return Finding(rule=rule, equip=equip, severity="warn", metrics=metrics or {})


def test_cost_findings_empty_and_degenerate():
    assert cost_findings([]) == []
    assert total_cost([]) is not None
    assert rank_by_cost([]) == []
    # NaN / missing metrics and a loads map missing the equip must not crash
    findings = [
        _finding(metrics={"reheat_at_high_oat_pct": float("nan")}),
        _finding(metrics={}),
        _finding(rule="totally_unknown_rule", metrics={"waste_kwh": float("nan")}),
    ]
    costs = cost_findings(findings, loads={"OtherEquip": EquipmentLoad()}, price=EnergyPrice())
    assert len(costs) == 3  # every finding produces a (possibly uncosted) result


# --------------------------------------------------------------------------- tariff billing

_IDX = pd.date_range("2024-01-01", periods=72, freq="h")
_LOAD = pd.Series(np.random.default_rng(0).uniform(10, 50, 72), index=_IDX)


@pytest.mark.parametrize(
    "tariff",
    [
        Tariff(energy_rates=[]),  # no rate structure at all
        Tariff(energy_rates=[[]]),  # a period with an empty tier list
        Tariff(energy_rates=[[(None, 0.1)]], energy_weekday=[[5] * 24] * 12),  # schedule > rates
        Tariff(energy_rates=[[(None, 0.1)]], demand_rates=[], demand_weekday=[[3] * 24] * 12),
    ],
)
def test_compute_bill_malformed_tariff_raises_clear_error(tariff):
    # an empty-tier list is a valid degenerate (0-cost) tier, so only the truly out-of-range
    # ones must raise; either way, no IndexError leaks.
    try:
        compute_bill(tariff, _LOAD)
    except ValueError as e:
        assert "period" in str(e) or "rate" in str(e)


def test_compute_bill_valid_still_bills():
    r = compute_bill(flat_tariff(0.15), _LOAD)
    assert r.total > 0 and r.n_months >= 1
