"""Property-based tests (hypothesis) — round-trip / idempotence / invariants.

These assert *laws* that must hold for all inputs, not single examples: the store's partition-key
safety contract, unit-normalization idempotence, parser totality, and the numeric bounds of the
M&V / stats primitives. A failure shrinks to a minimal counterexample and is reproducible from the
seed hypothesis prints. See the ``ci`` hypothesis profile in conftest.py.
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.coerce import coerce_numeric, coerce_status  # noqa: E402
from camber.eval import confusion  # noqa: E402
from camber.mandv.degreeday import degree_days  # noqa: E402
from camber.mandv.resample import resample, resample_energy  # noqa: E402
from camber.mandv.stats import fit_stats  # noqa: E402
from camber.store import make_facility_id, valid_facility_id  # noqa: E402
from camber.timegrid import interval_hours, regularize  # noqa: E402
from camber.tsparse import parse_timestamps  # noqa: E402
from camber.units import looks_like_fraction, normalize_percent  # noqa: E402
from camber.validation import wilson_interval  # noqa: E402

_floats = st.floats(allow_nan=False, allow_infinity=False, width=32)


def _series(vals, start="2024-01-01", freq="h"):
    return pd.Series(vals, index=pd.date_range(start, periods=len(vals), freq=freq))


# --------------------------------------------------------------------------- Tier A: facilities


@given(st.text())
def test_make_facility_id_is_always_path_safe(name):
    """The store's partition-key contract: any string yields a valid, deterministic id."""
    fid = make_facility_id(name)
    assert valid_facility_id(fid)  # never a "/", "=", space, unicode, or empty
    assert make_facility_id(name) == fid  # deterministic


# --------------------------------------------------------------------------- Tier A: units


@given(st.lists(st.floats(0.02, 1.5), min_size=1))
def test_normalize_percent_scales_a_fraction_then_is_idempotent(vals):
    s = pd.Series(vals)
    once = normalize_percent(s)
    assert not looks_like_fraction(once)  # a clear 0-1 fraction scaled up to 0-100
    assert normalize_percent(once).equals(once)  # second pass is a no-op


@given(st.lists(st.floats(1.6, 1e6), min_size=1))
def test_normalize_percent_leaves_a_percent_series_unchanged(vals):
    s = pd.Series(vals)
    assert normalize_percent(s).equals(s)  # already percent -> no-op


@given(st.lists(_floats, min_size=1))
def test_looks_like_fraction_matches_its_definition(vals):
    s = pd.Series(vals)
    v = s.dropna()
    expected = bool(v.max() <= 1.5 and v.min() >= -0.01)
    assert looks_like_fraction(s) == expected


# --------------------------------------------------------------------------- Tier A: coerce


@given(st.lists(st.text()))
def test_coerce_numeric_is_total_and_idempotent(vals):
    s = pd.Series(vals, dtype=object)
    out = coerce_numeric(s)  # never raises on arbitrary text (docstring guarantee)
    assert coerce_numeric(out).equals(out)  # idempotent on its own output


@given(st.lists(st.text()))
def test_coerce_status_output_is_tristate(vals):
    out = coerce_status(pd.Series(vals, dtype=object))
    assert out.dropna().isin([0.0, 1.0]).all()  # every non-NaN value is 0 or 1


# --------------------------------------------------------------------------- Tier A: tsparse


@given(
    st.lists(
        st.datetimes(min_value=pd.Timestamp("1990-01-01"), max_value=pd.Timestamp("2100-01-01")),
        min_size=1,
    )
)
def test_parse_timestamps_round_trips_iso(dts):
    iso = [d.isoformat() for d in dts]
    out = parse_timestamps(iso)
    assert list(out) == [pd.Timestamp(d) for d in dts]  # format -> parse recovers it


@given(st.lists(st.text()))
def test_parse_timestamps_never_raises(vals):
    parse_timestamps(vals)  # unparseable -> NaT, never an exception


# --------------------------------------------------------------------------- Tier A: timegrid


@given(st.lists(st.integers(0, 10**6), min_size=1, max_size=200))
def test_regularize_is_idempotent_and_yields_a_clean_index(offsets):
    idx = pd.Timestamp("2024-01-01") + pd.to_timedelta(sorted(offsets), unit="h")
    s = pd.Series(np.arange(len(idx), dtype=float), index=pd.DatetimeIndex(idx))
    once = regularize(s)
    assert once.index.is_monotonic_increasing and once.index.is_unique
    pd.testing.assert_series_equal(regularize(once), once)  # fixpoint


@given(st.integers(2, 500), st.sampled_from(["15min", "h", "D"]))
def test_interval_hours_positive_on_a_regular_grid(n, freq):
    idx = pd.date_range("2024-01-01", periods=n, freq=freq)
    assert interval_hours(idx) > 0


# --------------------------------------------------------------------------- Tier B: validation


@given(st.integers(0, 5000), st.integers(1, 5000))
def test_wilson_interval_stays_ordered_within_unit_range(k, n):
    assume(k <= n)
    lo, hi = wilson_interval(k, n)
    # Why Wilson beats Wald: the interval never leaves [0,1], even at k=0 / k=n (Wald does).
    # (The score interval shrinks toward 1/2, so it need not contain the raw p at the extremes.)
    assert 0.0 <= lo <= hi <= 1.0


# --------------------------------------------------------------------------- Tier B: eval


@given(st.lists(st.booleans(), min_size=1), st.lists(st.booleans(), min_size=1))
def test_confusion_conserves_count_and_bounds_rates(labels, preds):
    n = min(len(labels), len(preds))
    c = confusion(labels[:n], preds[:n])
    assert c.tp + c.fp + c.fn + c.tn == n  # every case lands in exactly one cell
    for rate in (c.true_positive_rate, c.false_positive_rate, c.accuracy):
        assert rate != rate or 0.0 <= rate <= 1.0  # in [0,1] or NaN (empty denominator)


# --------------------------------------------------------------------------- Tier B: degree days


@given(st.lists(st.floats(-40, 130, width=32), min_size=1), st.floats(40, 80))
def test_degree_days_are_nonnegative_and_complementary(temps, balance):
    hdd, cdd = degree_days(np.asarray(temps), balance)
    assert (hdd >= 0).all() and (cdd >= 0).all()
    # exactly one side is nonzero per point: hdd - cdd == balance - tavg
    assert np.allclose(hdd - cdd, balance - np.asarray(temps), atol=1e-6)


@given(st.lists(st.floats(-40, 130, width=32), min_size=1), st.floats(40, 60), st.floats(61, 80))
def test_degree_days_monotonic_in_balance_point(temps, lo_bp, hi_bp):
    t = np.asarray(temps)
    hdd_lo, cdd_lo = degree_days(t, lo_bp)
    hdd_hi, cdd_hi = degree_days(t, hi_bp)
    assert (hdd_hi >= hdd_lo - 1e-9).all()  # HDD rises with the balance point
    assert (cdd_hi <= cdd_lo + 1e-9).all()  # CDD falls


# --------------------------------------------------------------------------- Tier B: stats


@given(st.lists(st.floats(1.0, 1e4, width=32), min_size=3))
def test_fit_stats_perfect_fit_is_ideal(vals):
    y = np.asarray(vals)
    assume(np.std(y) > 1e-6)  # R^2 is undefined for a zero-variance y
    st_ = fit_stats(y, y.copy(), 1)  # yhat == y
    assert st_.cv_rmse == pytest.approx(0.0, abs=1e-9)
    assert st_.r2 == pytest.approx(1.0, abs=1e-9)
    assert st_.nmbe == pytest.approx(0.0, abs=1e-9)


@given(st.integers(0, 5))
def test_fit_stats_requires_more_points_than_params(n):
    y = np.ones(n)
    with pytest.raises(ValueError):
        fit_stats(y, y, n)  # n <= p is undefined -> raises


# --------------------------------------------------------------------------- Tier B: resample


@given(st.lists(st.floats(0.0, 1e4, width=32), min_size=48, max_size=480))
def test_resample_energy_conserves_total(vals):
    s = _series(vals, freq="h")
    daily = resample_energy(s, "D")
    assert daily.sum() == pytest.approx(s.sum(), rel=1e-9, abs=1e-6)  # energy is conserved


@given(st.lists(st.floats(-1e4, 1e4, width=32), min_size=10, max_size=200))
def test_resample_same_freq_mean_is_idempotent(vals):
    s = _series(vals, freq="h")
    once = resample(s, "h", method="mean")
    pd.testing.assert_series_equal(resample(once, "h", method="mean"), once)
