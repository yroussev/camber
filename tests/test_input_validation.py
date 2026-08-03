"""Input validation on the analytics entry points that previously surfaced raw pandas errors.

These functions assume a timestamp-indexed numeric series (or a non-negative price); on a
wrong-shaped input they used to fail deep in the math, or silently coerce a numeric index into
nanosecond timestamps and return a plausible-looking wrong answer. Now they raise one clear
ValueError up front (in the io.load_csv style). Empty input stays graceful (empty in → empty out).
"""

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.disaggregate import disaggregate_load  # noqa: E402
from camber.fault_economics import EnergyPrice, cost_findings  # noqa: E402
from camber.forecast import backtest, seasonal_forecast  # noqa: E402
from camber.scorecard import build_scorecard  # noqa: E402
from camber.tariff import compute_bill, flat_tariff  # noqa: E402

_HRS = pd.date_range("2024-07-01", periods=48, freq="h")
_NUM = pd.Series(range(48), dtype=float)  # RangeIndex -- the wrong shape


def test_seasonal_forecast_rejects_non_datetime_index():
    with pytest.raises(ValueError, match="DatetimeIndex"):
        seasonal_forecast(_NUM, _HRS)
    with pytest.raises(ValueError, match="horizon_index"):
        seasonal_forecast(pd.Series(1.0, index=_HRS), pd.RangeIndex(5))


def test_backtest_rejects_non_datetime_index():
    with pytest.raises(ValueError, match="DatetimeIndex"):
        backtest(_NUM)


def test_disaggregate_rejects_non_datetime_index_and_non_series():
    with pytest.raises(ValueError, match="DatetimeIndex"):
        disaggregate_load(_NUM, pd.Series(60.0, index=_HRS))
    with pytest.raises(ValueError, match="Series"):
        disaggregate_load(pd.Series(1.0, index=_HRS), [60.0] * 48)


def test_compute_bill_rejects_non_datetime_index():
    with pytest.raises(ValueError, match="DatetimeIndex"):
        compute_bill(flat_tariff(0.15), _NUM)


@pytest.mark.parametrize(
    "fn",
    [
        lambda: seasonal_forecast(pd.Series(dtype=float), _HRS),
        lambda: disaggregate_load(pd.Series(dtype=float), pd.Series(dtype=float)),
        lambda: compute_bill(flat_tariff(0.15), pd.Series(dtype=float)),
        lambda: backtest(pd.Series(dtype=float)),
    ],
)
def test_empty_series_stays_graceful_not_rejected(fn):
    fn()  # must not raise -- empty in, empty/degenerate out


@pytest.mark.parametrize(
    "kwargs",
    [
        {"electricity_per_kwh": -0.1},
        {"gas_per_therm": -1.0},
        {"electricity_per_kwh": float("nan")},
        {"gas_per_therm": None},
    ],
)
def test_energy_price_rejects_negative_or_nan(kwargs):
    with pytest.raises(ValueError, match="non-negative"):
        EnergyPrice(**kwargs)


def test_energy_price_valid_and_zero_ok():
    EnergyPrice()  # defaults
    EnergyPrice(electricity_per_kwh=0.0, gas_per_therm=0.0)  # zero is allowed
    # and a bad price surfaces at construction even via the costing entry point
    with pytest.raises(ValueError):
        cost_findings([], price=EnergyPrice(electricity_per_kwh=-1.0))


def test_build_scorecard_rejects_none_and_accepts_iterable():
    with pytest.raises(ValueError, match="findings"):
        build_scorecard(None)
    # a generator (not a list) is accepted -- materialized internally
    sc = build_scorecard(x for x in [])
    assert sc.n_findings == 0
