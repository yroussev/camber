"""The pre-0.105 CalTRACK-named entry points are deprecated aliases of the renamed functions (#156).

``caltrack_savings`` (a daily change-point baseline) is now ``daily_changepoint_savings`` and
``caltrack_savings_hourly`` (a pooled TOWT) is ``towt_savings_hourly``; their results are
``DailyNMECResult`` and ``TOWTHourlyResult``. The old names warn and return the same numbers.
The ``caltrack_*`` names of the faithful CalTRACK 2.0 methods are new names, not the old ones.
"""

from __future__ import annotations

import inspect
import warnings

import numpy as np
import pandas as pd
import pytest

import camber.mandv as mv
from camber.mandv import caltrack


def _daily(start, days, seed):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=days * 24, freq="h")
    t = pd.Series(
        70 + 15 * np.sin(np.arange(len(idx)) / 24 / 9) + rng.normal(0, 2, len(idx)), index=idx
    )
    e = pd.Series(
        3 + 0.12 * np.maximum(t.to_numpy() - 65, 0) + rng.normal(0, 0.2, len(idx)), index=idx
    )
    return e, t


def _hourly(start, weeks, seed):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=weeks * 168, freq="h")
    t = pd.Series(55 + 15 * np.sin(np.arange(len(idx)) / 24 / 7), index=idx)
    occ = (idx.dayofweek < 5) & (idx.hour >= 8) & (idx.hour < 18)
    e = pd.Series(
        5 + 10 * occ + 0.2 * np.maximum(t.to_numpy() - 60, 0) + rng.normal(0, 0.3, len(idx)),
        index=idx,
    )
    return e, t


def test_caltrack_savings_warns_and_matches_daily_changepoint_savings():
    be, bt = _daily("2023-01-01", 120, 1)
    re_, rt = _daily("2024-01-01", 60, 2)
    new = caltrack.daily_changepoint_savings(be, bt, re_, rt)
    with pytest.warns(
        DeprecationWarning, match=r"caltrack_savings is deprecated since CAMBER 0\.105"
    ):
        old = caltrack.caltrack_savings(be, bt, re_, rt)
    assert old.as_dict() == new.as_dict()
    assert isinstance(old, caltrack.NMECResult) and isinstance(old, caltrack.DailyNMECResult)
    assert caltrack.caltrack_savings.__deprecated__["use"].endswith("daily_changepoint_savings")
    assert inspect.signature(caltrack.caltrack_savings) == inspect.signature(
        caltrack.daily_changepoint_savings
    )


def test_caltrack_savings_hourly_warns_and_matches_towt_savings_hourly():
    be, bt = _hourly("2023-01-02", 12, 3)
    re_, rt = _hourly("2023-04-03", 4, 4)
    new = caltrack.towt_savings_hourly(be, bt, re_, rt)
    with pytest.warns(DeprecationWarning, match="caltrack_savings_hourly is deprecated"):
        old = caltrack.caltrack_savings_hourly(be, bt, re_, rt)
    assert old.as_dict() == new.as_dict()
    assert isinstance(old, caltrack.HourlyNMECResult) and isinstance(old, caltrack.TOWTHourlyResult)
    # the renamed function also returns its fitted model (not part of as_dict)
    assert new.model is not None and "model" not in new.as_dict()
    p = new.model.predict(re_.index, rt.to_numpy())
    assert np.isfinite(p).all()


def test_old_result_classes_warn_on_construction_and_new_ones_do_not():
    be, bt = _daily("2023-01-01", 90, 5)
    re_, rt = _daily("2024-01-01", 30, 6)
    res = caltrack.daily_changepoint_savings(be, bt, re_, rt)
    kw = {k: getattr(res, k) for k in res.__dataclass_fields__}
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        caltrack.DailyNMECResult(**kw)
    with pytest.warns(DeprecationWarning, match="NMECResult is deprecated"):
        caltrack.NMECResult(**kw)
    assert caltrack.HourlyNMECResult.__deprecated__["since"] == "0.105"


def test_package_exports_old_and_new_names():
    assert mv.caltrack_savings is caltrack.caltrack_savings
    assert mv.NMECResult is caltrack.NMECResult
    assert mv.daily_changepoint_savings is caltrack.daily_changepoint_savings
    assert mv.towt_savings_hourly is caltrack.towt_savings_hourly
    # the caltrack_* names of the faithful methods are new names, not the deprecated ones
    assert mv.caltrack_daily_savings.__module__ == "camber.mandv.caltrack_daily"
    assert mv.caltrack_hourly_savings.__module__ == "camber.mandv.caltrack_hourly"
    assert not hasattr(mv.caltrack_daily_savings, "__deprecated__")
