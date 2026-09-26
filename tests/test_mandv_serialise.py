"""M&V model serialisation (#21 phase 21a): ``as_dict`` -> JSON -> ``from_dict`` is lossless.

A versioned baseline is only as good as its round trip: the rebuilt model must predict exactly as
the fitted one (bit-identical, not merely close), grade coverage the same and carry the same fit
record (support, (X'X)^-1, s2, n, p, rho).
"""

import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv.coverage import assess_coverage  # noqa: E402
from camber.mandv.degreeday import DegreeDayModel, fit_degree_day  # noqa: E402
from camber.mandv.models import N_PARAMS, ChangePointModel, fit_model  # noqa: E402
from camber.mandv.retrofit_isolation import DriverModel, fit_driver_model  # noqa: E402
from camber.mandv.towt import TOWTModel, fit_towt  # noqa: E402

KINDS = ["2P", "3PC", "3PH", "3PHZ", "3PCZ", "4P", "5P", "5PZ"]


def _rt(model, cls):
    d = json.loads(json.dumps(model.as_dict(), allow_nan=False))
    return cls.from_dict(d)


def _truth(T):
    return 40 + 1.5 * np.maximum(0, 50 - T) + 2.0 * np.maximum(0, T - 70)


@pytest.mark.parametrize("kind", KINDS)
def test_change_point_round_trip_is_bit_identical(kind):
    rng = np.random.default_rng(3)
    T = rng.uniform(20, 95, 300)
    y = _truth(T) + rng.normal(0, 2, 300)
    idx = pd.date_range("2024-01-01", periods=300, freq="D")
    m = fit_model(T, y, kind, time_index=idx)
    again = _rt(m, ChangePointModel)
    Tq = np.r_[np.linspace(-10, 120, 400), np.nan]
    assert np.array_equal(m.predict(Tq), again.predict(Tq), equal_nan=True)
    assert again.predict(55.0) == m.predict(55.0) and np.ndim(again.predict(55.0)) == 0
    a, b = m._fit_record, again._fit_record
    assert (b.s2, b.n, b.p, b.rho, b.design) == (a.s2, a.n, a.p, a.rho, a.design)
    assert a.n == 300 and a.p == N_PARAMS[kind] and a.rho is not None
    assert a.s2 == pytest.approx(m.sse / (300 - N_PARAMS[kind]))
    assert np.array_equal(a.xtx_pinv, b.xtx_pinv)
    assert assess_coverage(m, Tq[:-1]).as_dict() == assess_coverage(again, Tq[:-1]).as_dict()


@pytest.mark.parametrize("kind", ["5P", "5PZ"])
def test_5p_fallback_to_a_line_round_trips(kind, monkeypatch):
    import camber.mandv.models as models

    monkeypatch.setitem(models._FITTERS, kind, models._fit_2p)  # as when no dead-band fits
    rng = np.random.default_rng(1)
    T = rng.uniform(20, 90, 100)
    m = fit_model(T, 3 + 0.5 * T + rng.normal(0, 1, 100), kind)
    assert m.kind == kind and m.change_points == ()
    again = _rt(m, ChangePointModel)
    Tq = np.linspace(0, 100, 50)
    assert np.array_equal(m.predict(Tq), again.predict(Tq))


def test_without_a_time_index_rho_is_none_and_hand_built_models_serialise():
    rng = np.random.default_rng(0)
    T = rng.uniform(20, 95, 100)
    m = fit_model(T, _truth(T), "3PC")
    assert m._fit_record.rho is None
    bare = ChangePointModel(
        "2P", {"base": 1.0, "slope": 2.0}, (), 0.0, 3, _predict=lambda t: 1 + 2 * t
    )
    again = _rt(bare, ChangePointModel)
    assert again.predict(4.0) == 9.0 and again._fit_record is None
    with pytest.raises(ValueError, match="kind"):
        ChangePointModel.from_dict({**bare.as_dict(), "kind": "9P", "change_points": [1.0]})


def test_driver_model_round_trip():
    rng = np.random.default_rng(2)
    X = rng.uniform(0, 10, (80, 2))
    y = 3 + X @ np.array([1.5, -0.7]) + rng.normal(0, 0.3, 80)
    m = fit_driver_model(X, y, time_index=pd.date_range("2024-01-01", periods=80, freq="D"))
    again = _rt(m, DriverModel)
    Xq = rng.uniform(-5, 15, (30, 2))
    assert np.array_equal(m.predict(Xq), again.predict(Xq))
    assert again._fit_record.rho == m._fit_record.rho and m._fit_record.p == 3
    assert assess_coverage(m, Xq).as_dict() == assess_coverage(again, Xq).as_dict()
    const = fit_driver_model(None, y)
    assert _rt(const, DriverModel).predict(np.zeros(4)).tolist() == [const.intercept] * 4


def test_degree_day_round_trip():
    rng = np.random.default_rng(11)
    tm = rng.uniform(25, 85, 36)
    em = 300 + 12 * np.maximum(0, 60 - tm) + 9 * np.maximum(0, tm - 65) + rng.normal(0, 10, 36)
    dd = fit_degree_day(tm, em)
    again = _rt(dd, DegreeDayModel)
    tq = np.linspace(0, 100, 40)
    assert np.array_equal(dd.predict(tq), again.predict(tq))
    assert again.fit.r2 == dd.fit.r2 and again._fit_record.s2 == dd._fit_record.s2
    d = dd.as_dict()
    d["fit"]["a_future_field"] = 1  # unknown FitStats keys are ignored
    assert DegreeDayModel.from_dict(d).fit.n == dd.fit.n


def test_towt_round_trip():
    rng = np.random.default_rng(4)
    idx = pd.date_range("2024-01-01", periods=6 * 168, freq="1h")
    occ = (idx.dayofweek < 5) & (idx.hour >= 8) & (idx.hour < 18)
    t = 60 + 15 * np.sin(np.arange(len(idx)) / 168 * 2 * np.pi) + rng.normal(0, 2, len(idx))
    e = 40 + 30 * occ + np.clip(t - 65, 0, None) * 2 + rng.normal(0, 2, len(idx))
    m = fit_towt(pd.Series(e, index=idx), pd.Series(t, index=idx))
    again = _rt(m, TOWTModel)
    assert np.array_equal(m.predict(idx, t), again.predict(idx, t))
    rec = m._fit_record
    assert rec.xtx_pinv is not None and rec.design is None and rec.rho is not None
    assert again._fit_record.towt.counts.tolist() == rec.towt.counts.tolist()
    a = m.coverage(idx, t + 5).as_dict()
    assert a == again.coverage(idx, t + 5).as_dict()
