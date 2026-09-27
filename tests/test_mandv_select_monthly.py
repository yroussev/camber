"""select_method compares rows with rows, so chaining is proposed on monthly data (#52)."""

import numpy as np
import pandas as pd

from camber.mandv.methods import _expected_rows, select_method


def _three_years_monthly():
    """A cold baseline year, a hot reporting year and a middle year spanning both."""
    rng = np.random.default_rng(4)
    n = 36
    idx = pd.date_range("2018-01-01", periods=n, freq="MS")
    ph = np.sin(2 * np.pi * (np.arange(n) - 3.5) / 12)
    T = np.empty(n)
    T[:12] = 40 + 12 * ph[:12]  # baseline 28-52 F
    T[12:24] = 55 + 40 * ph[12:24]  # intermediate 15-95 F
    T[24:] = 75 + 12 * ph[24:]  # reporting 63-87 F
    T += rng.normal(0, 1.5, n)
    y = 500 + 18 * np.clip(55 - T, 0, None) + 14 * np.clip(T - 60, 0, None) + rng.normal(0, 15, n)
    y[24:] *= 0.85
    return pd.DataFrame({"oat": T, "energy": y}, index=idx)


def test_chaining_is_proposed_on_monthly_rows():
    f = _three_years_monthly()
    p = select_method(
        f, baseline=("2018-01-01", "2018-12-31"), reporting=("2020-01-01", "2020-12-31")
    )
    steps = {s["method"]: s for s in p.steps}
    assert not steps["forecast"]["valid"] and not steps["backcast"]["valid"]
    assert steps["chaining"]["valid"], steps["chaining"]["reasons"]
    assert p.proposed == "chaining"
    assert p.intermediate_period == ["2019-01-01", "2019-12-31"]
    assert "chaining" in p.results and not p.results["chaining"].declined


def test_a_monthly_window_missing_too_many_rows_is_still_skipped():
    f = _three_years_monthly().drop(pd.date_range("2019-03-01", periods=3, freq="MS"))
    p = select_method(
        f, baseline=("2018-01-01", "2018-12-31"), reporting=("2020-01-01", "2020-12-31")
    )
    assert p.proposed is None  # 9 of 12 monthly rows is under 90%


def test_expected_rows_follow_the_sampling_interval():
    days = pd.date_range("2024-01-01", periods=400, freq="D")
    assert _expected_rows(365, days) == 365.0  # daily: the old threshold, unchanged
    hours = pd.date_range("2024-01-01", periods=48, freq="h")
    assert _expected_rows(365, hours) == 365 * 24.0
    months = pd.date_range("2018-01-01", periods=36, freq="MS")
    assert 11.5 < _expected_rows(365, months) < 12.5
