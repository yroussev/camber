"""Shared fixtures for the pre-1.0 stress/hardening pass.

Dependency-light degenerate-frame factories (empty / 1-row / all-NaN / all-equal / dup-index /
DST-dirty) so the adversarial tests share one set of generators. Seeded and deterministic. The
example-based suites use numpy/pandas + stdlib only; the property-based suites
(``test_properties*``) additionally use ``hypothesis`` under the ``ci`` profile registered below.
"""

import numpy as np
import pandas as pd
import pytest
from hypothesis import settings

# A property-test profile tuned for CI: no per-example deadline (pandas/numpy ops are slow enough
# to trip hypothesis's default timing check and flake), and a bounded example budget. A genuine
# property failure is a real bug, reproducible from the seed hypothesis prints.
settings.register_profile("ci", deadline=None, max_examples=100)
settings.load_profile("ci")


def _idx(n, freq="1h", start="2024-01-01"):
    return pd.date_range(start, periods=n, freq=freq)


@pytest.fixture
def degenerate_frames():
    """A dict of adversarial single-column frames keyed by kind (column name 'v')."""
    return {
        "empty": pd.DataFrame({"v": pd.Series(dtype=float)}),
        "one_row": pd.DataFrame({"v": [1.0]}, index=_idx(1)),
        "all_nan": pd.DataFrame({"v": np.full(48, np.nan)}, index=_idx(48)),
        "all_equal": pd.DataFrame({"v": np.full(48, 5.0)}, index=_idx(48)),
        "dup_index": pd.DataFrame(
            {"v": np.arange(4.0)}, index=pd.DatetimeIndex(["2024-01-01"] * 4)
        ),
        "huge": pd.DataFrame({"v": np.full(48, 1e18)}, index=_idx(48)),
        "tiny": pd.DataFrame({"v": np.full(48, 1e-18)}, index=_idx(48)),
    }


@pytest.fixture
def make_frame():
    """Factory: make_frame(kind, cols) -> a multi-column frame of a degenerate kind."""

    def _make(kind="all_equal", cols=("a", "b"), n=48):
        idx = _idx(n)
        if kind == "empty":
            return pd.DataFrame({c: pd.Series(dtype=float) for c in cols})
        if kind == "one_row":
            return pd.DataFrame({c: [1.0] for c in cols}, index=_idx(1))
        if kind == "all_nan":
            return pd.DataFrame({c: np.full(n, np.nan) for c in cols}, index=idx)
        if kind == "all_equal":
            return pd.DataFrame({c: np.full(n, 3.0) for c in cols}, index=idx)
        if kind == "dup_index":
            return pd.DataFrame(
                {c: np.arange(float(n)) for c in cols}, index=pd.DatetimeIndex([idx[0]] * n)
            )
        raise ValueError(kind)

    return _make


@pytest.fixture
def rng():
    return np.random.default_rng(0)
