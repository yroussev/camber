"""Baseline coverage / extrapolation guard (camber.mandv.coverage, issue #20).

The load-bearing guarantees: a baseline projected onto its own period is always ``in_range`` and
its savings numbers are byte-identical to the pre-coverage code (``GOLDEN``, frozen from main at
951e6e6); coverage never makes a fit fail; and each model family reports extrapolation it would
otherwise hide -- a flat TOWT hold, a hidden multivariate extrapolation, an unseen category.
"""

import json
import os
import pickle
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv import coverage as cov_mod  # noqa: E402
from camber.mandv.categorical import fit_categorical  # noqa: E402
from camber.mandv.coverage import (  # noqa: E402
    TIERS,
    Coverage,
    ExtrapolationPolicy,
    assess_coverage,
    support_of,
    towt_coverage,
)
from camber.mandv.degreeday import fit_degree_day  # noqa: E402
from camber.mandv.models import N_PARAMS, _design_for, best_model, fit_model  # noqa: E402
from camber.mandv.retrofit_isolation import fit_driver_model  # noqa: E402
from camber.mandv.towt import TOWTAtIndex, fit_towt  # noqa: E402

KINDS = ["2P", "3PC", "3PH", "3PHZ", "3PCZ", "4P", "5P", "5PZ"]


def _truth(T):
    return 40 + 1.5 * np.maximum(0, 50 - T) + 2.0 * np.maximum(0, T - 70)


def _baseline(kind="3PC", lo=30.0, hi=90.0, n=365, seed=0):
    rng = np.random.default_rng(seed)
    Tb = rng.uniform(lo, hi, n)
    yb = _truth(Tb) + rng.normal(0, 2, n)
    return fit_model(Tb, yb, kind), Tb, yb


def _json_ok(d):
    json.dumps(d, allow_nan=False)  # strict JSON: no NaN/inf, no numpy scalars


# ------------------------------------------------------------------------------ support + policy


def test_support_of_uses_order_statistics():
    x = np.r_[np.arange(100.0), np.nan]
    s = support_of(x, quantile=0.01)
    assert s == {"n": 100, "fit_min": 0.0, "fit_max": 99.0, "support_lo": 0.0, "support_hi": 99.0}
    s = support_of(np.arange(1000.0), quantile=0.01)
    assert (
        s["support_lo"] == 9.0 and s["support_hi"] == 990.0
    )  # observed values, never interpolated
    assert support_of([np.nan])["fit_min"] is None


def test_policy_validates_and_round_trips():
    with pytest.raises(ValueError):
        ExtrapolationPolicy(decline_share=0.01)
    with pytest.raises(ValueError):
        ExtrapolationPolicy(support_quantile=0.6)
    with pytest.raises(ValueError):
        ExtrapolationPolicy(min_cell_obs=0)
    with pytest.raises(ValueError):
        ExtrapolationPolicy(decline_distance=0.01)
    with pytest.raises(ValueError):
        ExtrapolationPolicy(edge_tolerance=-1)
    with pytest.raises(ValueError, match="unknown"):
        ExtrapolationPolicy.from_dict({"decline_shares": 0.3})
    p = ExtrapolationPolicy.from_dict({"decline": False, "caveat_share": 0.02})
    assert p.decline is False and ExtrapolationPolicy.from_dict(p.as_dict()) == p
    assert ExtrapolationPolicy.from_dict(None) == ExtrapolationPolicy()
    assert TIERS == ("in_range", "moderate", "severe", "not_evaluated")


# ------------------------------------------------------------------------------ change-point


@pytest.mark.parametrize("kind", KINDS)
def test_design_reproduces_every_kind(kind):
    m, Tb, _ = _baseline(kind)
    X = _design_for(kind, m.change_points)(Tb)
    beta, *_ = np.linalg.lstsq(X, m.predict(Tb), rcond=None)
    assert np.allclose(X @ beta, m.predict(Tb), atol=1e-8)
    assert m.fit_range == (float(Tb.min()), float(Tb.max()))


@pytest.mark.parametrize("kind", KINDS)
def test_own_period_is_always_in_range(kind):
    for seed, (lo, hi, n) in enumerate([(30, 90, 365), (10, 100, 60), (40, 60, 20)]):
        m, Tb, _ = _baseline(kind, lo, hi, n, seed=seed)
        c = assess_coverage(m, Tb)
        assert c.tier == "in_range", (kind, seed, c.reason)
        assert c.n_outside == 0 and c.max_beyond_rel == 0.0


@pytest.mark.parametrize("kind", KINDS)
def test_moderate_and_severe_every_kind(kind):
    m, _, _ = _baseline(kind)
    rng = np.random.default_rng(9)
    hot8 = np.r_[rng.uniform(35, 85, 190), np.linspace(94, 98, 10)]  # up to +8 F, 5% of points
    c = assess_coverage(m, hot8)
    assert c.tier == "moderate", c.as_dict()
    assert c.reason.startswith("Moderate extrapolation:")
    assert c.n_above == 10 and c.n_below == 0
    assert c.max_beyond_high == pytest.approx(98 - m.fit_range[1], abs=1e-3)
    summer = rng.uniform(80, 115, 200)
    c = assess_coverage(m, summer)
    assert c.tier == "severe"
    assert c.reason.startswith("SEVERE extrapolation — not a defensible saving")


def test_zero_intercept_and_5p_fallback_design(monkeypatch):
    from camber.mandv import models

    # a 5P fit that falls back to a line keeps kind="5P" with no change points
    monkeypatch.setitem(models._FITTERS, "5P", models._fit_2p)
    rng = np.random.default_rng(3)
    Tb = rng.uniform(30, 90, 200)
    m = fit_model(Tb, 3 * Tb + rng.normal(0, 1, 200), "5P")
    assert m.kind == "5P" and m.change_points == ()
    assert assess_coverage(m, Tb).tier == "in_range"
    assert assess_coverage(m, Tb + 40).tier == "severe"
    assert _design_for("5P", ()) is models._design_2p
    with pytest.raises(ValueError):
        _design_for("9P", (1.0,))


def test_single_outlier_day_does_not_widen_the_band():
    rng = np.random.default_rng(1)
    Tb = np.r_[rng.uniform(30, 85, 364), 110.0]  # one freak hot day
    m = fit_model(Tb, _truth(Tb) + rng.normal(0, 2, 365), "3PC")
    Tr = rng.uniform(92, 105, 100)  # inside the hard range, far outside the support
    c = assess_coverage(m, Tr)
    assert c.max_beyond_rel == 0.0  # distance is against the hard range...
    assert c.share_points_outside == 1.0  # ...but the share is against the band
    assert c.tier == "severe"


def test_edge_tolerance():
    Tb = np.linspace(40, 80, 401)
    m = fit_model(Tb, 2 * Tb, "2P")
    s = support_of(Tb)
    w = s["support_hi"] - s["support_lo"]
    just = np.full(50, s["support_hi"] + 0.04 * w)  # within 5% of the band's width
    assert assess_coverage(m, just).n_outside == 0
    over = np.full(50, s["support_hi"] + 0.06 * w)
    assert assess_coverage(m, over).n_outside == 50
    assert assess_coverage(m, over, policy=ExtrapolationPolicy(edge_tolerance=0.1)).n_outside == 0


def test_both_sides_counted_including_a_flat_segment():
    m, _, _ = _baseline("3PC", 50, 90)
    # below 50 the 3PC model is flat -- still outside the data
    c = assess_coverage(m, np.r_[np.full(30, 35.0), np.full(30, 95.0), np.full(140, 70.0)])
    assert c.n_below == 30 and c.n_above == 30 and c.tier == "severe"


def test_zero_projected_sum_gives_no_energy_share():
    Tb = np.linspace(30, 90, 100)
    m = fit_model(Tb, np.zeros(100), "2P")
    c = assess_coverage(m, np.linspace(30, 100, 50))
    assert c.share_energy_outside is None
    assert any("not positive" in x for x in c.caveats)


def test_constant_oat_zero_width_support():
    m = fit_model(np.full(30, 60.0), np.linspace(1, 2, 30), "2P")
    same = assess_coverage(m, np.full(10, 60.0))
    assert same.tier == "in_range"
    diff = assess_coverage(m, np.full(10, 61.0))
    assert diff.tier == "severe" and diff.max_beyond_rel is None
    assert "never varied" in diff.reason
    _json_ok(diff.as_dict())


def test_nan_heavy_and_all_nan():
    m, _, _ = _baseline()
    rng = np.random.default_rng(2)
    Tr = rng.uniform(35, 85, 300)
    Tr[rng.random(300) < 0.7] = np.nan
    c = assess_coverage(m, Tr)
    assert c.tier == "in_range" and c.n_report == 300 and c.n_used == int(np.isfinite(Tr).sum())
    allnan = assess_coverage(m, np.full(20, np.nan))
    assert allnan.tier == "not_evaluated" and allnan.n_used == 0
    _json_ok(allnan.as_dict())


@pytest.mark.parametrize("n", [3, 12])
def test_tiny_baselines(n):
    Tb = np.linspace(40, 80, n)
    m = fit_model(Tb, 2 * Tb + 1, "2P")
    assert assess_coverage(m, Tb).tier == "in_range"
    assert assess_coverage(m, Tb + 30).tier == "severe"


def test_as_dict_is_json_and_hides_the_mask():
    m, _, _ = _baseline()
    c = assess_coverage(m, np.linspace(20, 100, 80))
    d = c.as_dict()
    _json_ok(d)
    assert "_outside" not in d and d["policy"]["decline"] is True
    mask = c.outside_mask()
    assert mask.dtype == bool and mask.sum() == c.n_outside
    assert Coverage(tier="not_evaluated", n_report=0, n_used=0).outside_mask() is None


# ------------------------------------------------------------------------------ never raise


def test_fit_never_fails_because_of_coverage(monkeypatch):
    """BDG2's score_meter swallows exceptions per building -- coverage must never cause one."""

    def boom(*a, **k):
        raise RuntimeError("support recorder broke")

    monkeypatch.setattr(cov_mod, "_linear_fit_record", boom)
    monkeypatch.setattr(cov_mod, "_towt_fit_record", boom)
    rng = np.random.default_rng(0)
    Tb = rng.uniform(30, 90, 120)
    m = best_model(Tb, _truth(Tb) + rng.normal(0, 1, 120))
    assert m._fit_record is None and m.fit_range is not None
    assert assess_coverage(m, Tb).tier == "not_evaluated"
    assert fit_driver_model(Tb, _truth(Tb))._fit_record is None
    assert fit_degree_day(Tb[:24], _truth(Tb[:24]))._fit_record is None


def test_duck_typed_and_broken_models_are_not_evaluated():
    class Duck:
        def predict(self, T):
            return np.asarray(T) * 2

    c = assess_coverage(Duck(), np.arange(5.0))
    assert c.tier == "not_evaluated" and "Duck" in c.reason

    class Broken(Duck):
        def coverage(self, T, **kw):
            raise RuntimeError("nope")

    assert "RuntimeError" in assess_coverage(Broken(), np.arange(5.0)).reason

    class Liar(Duck):
        def coverage(self, T, **kw):
            return "fine"

    assert assess_coverage(Liar(), np.arange(5.0)).tier == "not_evaluated"
    assert assess_coverage(Duck(), 3.0).n_report == 1


def test_fit_state_lives_in_one_private_record():
    """#21 (rebaselining / backcast) extends this record; no other attribute carries fit state."""
    from camber.mandv.coverage import _FitRecord

    m, Tb, _ = _baseline("4P")
    assert isinstance(m._fit_record, _FitRecord) and m._fit_record.linear
    assert m._fit_record.xtx_pinv.shape == (3, 3)
    assert "_fit_record" not in repr(m)
    e, t, _ = _towt_site("2024-01-01", 4)
    rec = fit_towt(e, t)._fit_record
    assert isinstance(rec, _FitRecord) and rec.towt is not None and not rec.linear


def test_assess_coverage_is_direction_agnostic():
    """A backcast projects the *reporting* model onto the *baseline* drivers -- same call."""
    rng = np.random.default_rng(12)
    Tb = rng.uniform(20, 100, 300)
    Tr = rng.uniform(60, 85, 200)
    reporting_model = fit_model(Tr, 0.8 * _truth(Tr), "3PC")
    back = assess_coverage(reporting_model, Tb)
    assert back.tier == "severe" and back.variables[0]["fit_min"] == pytest.approx(
        Tr.min(), abs=1e-3
    )


def test_models_stay_picklable():
    rng = np.random.default_rng(0)
    x = rng.uniform(0, 10, 50)
    dm = fit_driver_model(x, 2 * x + 1)
    again = pickle.loads(pickle.dumps(dm))
    assert assess_coverage(again, x).tier == "in_range"


# ------------------------------------------------------------------------------ degree-day / driver


def test_degree_day_coverage():
    rng = np.random.default_rng(11)
    tm = rng.uniform(25, 85, 36)
    em = 300 + 12 * np.maximum(0, 60 - tm) + 9 * np.maximum(0, tm - 65) + rng.normal(0, 10, 36)
    dd = fit_degree_day(tm, em)
    assert assess_coverage(dd, tm).tier == "in_range"
    assert assess_coverage(dd, tm + 40).tier == "severe"
    assert "fit" in dd.as_dict() and "_fit_record" not in dd.as_dict()


def test_driver_hidden_multivariate_extrapolation():
    rng = np.random.default_rng(0)
    x1 = rng.uniform(0, 100, 200)
    x2 = x1 + rng.normal(0, 3, 200)  # strongly correlated drivers
    X = np.column_stack([x1, x2])
    m = fit_driver_model(X, 3 + 0.5 * x1 + 0.2 * x2)
    assert assess_coverage(m, X).tier == "in_range"
    # each column in range, but anti-correlated: outside the joint region the data spans
    a = rng.uniform(10, 90, 100)
    hidden = np.column_stack([a, 100 - a])
    c = assess_coverage(m, hidden)
    assert all(v["n_below"] + v["n_above"] == 0 for v in c.variables)
    assert c.info["n_leverage_outside"] > 50 and c.tier == "severe"
    assert c.info["max_leverage_ratio"] > 1
    wrong = assess_coverage(m, np.arange(5.0))
    assert wrong.tier == "not_evaluated" and "column" in wrong.reason


def test_constant_driver_model_is_not_evaluated():
    m = fit_driver_model(None, np.array([100.0, 102.0, 98.0]))
    c = assess_coverage(m, np.zeros(4))
    assert c.tier == "not_evaluated" and "constant" in c.reason
    assert m.coverage(3).n_report == 1


# ------------------------------------------------------------------------------ categorical


def test_categorical_unseen_category_is_unsupported():
    rng = np.random.default_rng(4)
    T = rng.uniform(40, 90, 300)
    cat = np.where(rng.random(300) < 0.5, "weekday", "weekend")
    y = np.where(cat == "weekday", 50, 20) + 1.5 * np.maximum(0, T - 65)
    cm = fit_categorical(T, y, cat, kind="3PC")
    assert cm.coverage(T, cat).tier == "in_range"
    rcat = np.where(rng.random(100) < 0.6, "holiday", "weekday")
    c = cm.coverage(rng.uniform(45, 85, 100), rcat)
    assert c.info["n_unseen_category"] == int((rcat == "holiday").sum())
    assert c.info["unseen_categories"] == ["holiday"]
    assert c.tier == "severe" and any("never fitted" in x for x in c.caveats)
    bound = cm.at(rcat)
    assert np.isnan(bound.predict(np.full(100, 60.0))[rcat == "holiday"]).all()
    assert bound.coverage(np.full(100, 60.0)).n_outside == int((rcat == "holiday").sum())
    with pytest.raises(ValueError):
        bound.predict(np.arange(3.0))


# ------------------------------------------------------------------------------ TOWT


def _towt_site(start, weeks, seed=0, occ_warm=False):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=weeks * 168, freq="1h")
    occ = (idx.dayofweek < 5) & (idx.hour >= 8) & (idx.hour < 18)
    if occ_warm:  # occupied hours only ever warm, unoccupied only cool; 84 of 168 bins occupied
        occ = (idx.hour >= 6) & (idx.hour < 18)
        t = np.where(occ, 70, 50) + rng.uniform(-5, 5, len(idx))
    else:
        t = 60 + 15 * np.sin(np.arange(len(idx)) / 168 * 2 * np.pi) + rng.normal(0, 2, len(idx))
    e = 40 + 30 * occ + np.clip(t - 65, 0, None) * 2 + rng.normal(0, 2, len(idx))
    return pd.Series(e, index=idx), pd.Series(t, index=idx), occ


def test_towt_own_period_in_range_and_flat_hold_caveat():
    e, t, _ = _towt_site("2024-01-01", 12)
    m = fit_towt(e, t)
    c = towt_coverage(m, e.index, t.to_numpy())
    assert c.tier == "in_range", c.reason
    assert c.info["unit"] == "occupancy mode x temperature cell"
    assert [v["name"] for v in c.variables] == ["oat[occupied]", "oat[unoccupied]"]
    hot = TOWTAtIndex(m, e.index[:500]).coverage(np.full(500, 110.0))
    assert hot.tier == "severe" and hot.info["n_beyond_fit_range"] == 500
    assert any("flat beyond the fitted range" in x for x in hot.caveats)
    assert m.coverage(e.index, t.to_numpy()).tier == "in_range"
    with pytest.raises(ValueError):
        towt_coverage(m, e.index[:3], np.arange(4.0))


def test_towt_per_mode_ranges():
    e, t, occ = _towt_site("2024-01-01", 8, occ_warm=True)
    m = fit_towt(e, t)
    assert towt_coverage(m, e.index, t.to_numpy()).tier == "in_range"
    # occupied hours at 50F are inside the overall range but outside the occupied mode's range
    t2 = np.where(occ, 50.0, t.to_numpy())
    c = towt_coverage(m, e.index, t2)
    occv = next(v for v in c.variables if v["name"] == "oat[occupied]")
    assert occv["n_below"] == int(occ.sum()) and occv["max_beyond_low"] > 10
    assert c.info["n_beyond_fit_range"] == 0 and c.tier == "severe"


def test_towt_min_cell_obs_boundary_and_info_only_sparsity():
    e, t, _ = _towt_site("2024-01-01", 12)
    m = fit_towt(e, t)
    sup = m._fit_record.towt
    # pick the sparsest populated (mode, segment) cell and test its boundary
    mode_i, seg_j = np.unravel_index(
        np.argmin(np.where(sup.counts > 0, sup.counts, 10**9)), sup.counts.shape
    )
    count = int(sup.counts[mode_i, seg_j])
    tt = t.to_numpy()
    seg = cov_mod._towt_segments(tt, sup.breakpoints)
    mode = cov_mod._towt_modes(np.asarray(e.index.dayofweek * 24 + e.index.hour), m.occ_bins)
    rows = (np.clip(seg, 0, len(sup.breakpoints) - 2) == seg_j) & (mode == mode_i)
    at = towt_coverage(m, e.index[rows], tt[rows], policy=ExtrapolationPolicy(min_cell_obs=count))
    assert at.n_outside == 0
    over = towt_coverage(
        m, e.index[rows], tt[rows], policy=ExtrapolationPolicy(min_cell_obs=count + 1)
    )
    assert over.n_outside == int(rows.sum()) and over.info["n_sparse_cell"] == int(rows.sum())
    # hour-of-week x temperature sparsity is information only: it never moves the tier
    c = towt_coverage(m, e.index, tt)
    assert "tow_temp_unseen_share" in c.info and c.tier == "in_range"
    _json_ok(c.as_dict())


def test_towt_without_support_is_not_evaluated():
    e, t, _ = _towt_site("2024-01-01", 4)
    m = fit_towt(e, t)
    m._fit_record = None
    assert towt_coverage(m, e.index, t.to_numpy()).tier == "not_evaluated"
    assert assess_coverage(m, t.to_numpy()).tier == "not_evaluated"  # needs an index: bind it


# ------------------------------------------------------------------------------ golden numbers
GOLDEN = {
    "caltrack_daily": {
        "abs_uncertainty": 68.56,
        "avoided_energy": 15929.18,
        "baseline_projected": 79463.55,
        "confidence": 0.9,
        "fractional_uncertainty": 0.0043,
        "fsu_autocorrelation_adjusted": True,
        "n_effective": 86.07,
        "reporting_actual": 63534.37,
        "rho": 0.1646,
        "savings_pct": 0.2005,
    },
    "caltrack_hourly": {
        "abs_uncertainty": 153.17,
        "avoided_energy": 11381.05,
        "baseline_projected": 73198.27,
        "confidence": 0.9,
        "fractional_uncertainty": 0.0135,
        "fsu_autocorrelation_adjusted": False,
        "n_effective": 3360.0,
        "reporting_actual": 61817.22,
        "rho": 0.0,
        "savings_pct": 0.1555,
    },
    "cp_2P": {
        "abs_uncertainty": 629.75,
        "avoided_energy": 2126.64,
        "baseline_projected": 11306.75,
        "confidence": 0.9,
        "fractional_uncertainty": 0.2961,
        "fsu_autocorrelation_adjusted": True,
        "n_effective": 196.54,
        "reporting_actual": 9180.11,
        "rho": 0.3,
        "savings_pct": 0.1881,
    },
    "cp_3PC": {
        "abs_uncertainty": 514.79,
        "avoided_energy": 1995.03,
        "baseline_projected": 11175.69,
        "confidence": 0.9,
        "fractional_uncertainty": 0.258,
        "fsu_autocorrelation_adjusted": True,
        "n_effective": 196.54,
        "reporting_actual": 9180.66,
        "rho": 0.3,
        "savings_pct": 0.1785,
    },
    "cp_3PCZ": {
        "abs_uncertainty": 1043.1,
        "avoided_energy": -316.69,
        "baseline_projected": 8750.52,
        "confidence": 0.9,
        "fractional_uncertainty": 3.2937,
        "fsu_autocorrelation_adjusted": True,
        "n_effective": 196.54,
        "reporting_actual": 9067.21,
        "rho": 0.3,
        "savings_pct": -0.0362,
    },
    "cp_3PH": {
        "abs_uncertainty": 523.41,
        "avoided_energy": 2200.37,
        "baseline_projected": 11106.81,
        "confidence": 0.9,
        "fractional_uncertainty": 0.2379,
        "fsu_autocorrelation_adjusted": True,
        "n_effective": 196.54,
        "reporting_actual": 8906.44,
        "rho": 0.3,
        "savings_pct": 0.1981,
    },
    "cp_3PHZ": {
        "abs_uncertainty": 993.91,
        "avoided_energy": -909.66,
        "baseline_projected": 8329.42,
        "confidence": 0.9,
        "fractional_uncertainty": 1.0926,
        "fsu_autocorrelation_adjusted": True,
        "n_effective": 196.54,
        "reporting_actual": 9239.08,
        "rho": 0.3,
        "savings_pct": -0.1092,
    },
    "cp_4P": {
        "abs_uncertainty": 162.81,
        "avoided_energy": 1675.89,
        "baseline_projected": 11090.27,
        "confidence": 0.9,
        "fractional_uncertainty": 0.0971,
        "fsu_autocorrelation_adjusted": True,
        "n_effective": 196.54,
        "reporting_actual": 9414.38,
        "rho": 0.3,
        "savings_pct": 0.1511,
    },
    "cp_5P": {
        "abs_uncertainty": 107.33,
        "avoided_energy": 1612.69,
        "baseline_projected": 10870.82,
        "confidence": 0.9,
        "fractional_uncertainty": 0.0666,
        "fsu_autocorrelation_adjusted": True,
        "n_effective": 196.54,
        "reporting_actual": 9258.12,
        "rho": 0.3,
        "savings_pct": 0.1484,
    },
    "cp_5PZ": {
        "abs_uncertainty": 488.21,
        "avoided_energy": -770.23,
        "baseline_projected": 8348.28,
        "confidence": 0.9,
        "fractional_uncertainty": 0.6339,
        "fsu_autocorrelation_adjusted": True,
        "n_effective": 196.54,
        "reporting_actual": 9118.51,
        "rho": 0.3,
        "savings_pct": -0.0923,
    },
    "degreeday": {
        "abs_uncertainty": 65.9,
        "avoided_energy": 571.45,
        "baseline_projected": 5525.09,
        "confidence": 0.9,
        "fractional_uncertainty": 0.1153,
        "fsu_autocorrelation_adjusted": False,
        "n_effective": 36.0,
        "reporting_actual": 4953.65,
        "rho": None,
        "savings_pct": 0.1034,
    },
    "isolation": {
        "abs_uncertainty": 78.18,
        "accept": True,
        "adjusted_baseline": 11502.44,
        "boundary": "",
        "confidence": 0.9,
        "cv_rmse": 0.0246,
        "fractional_uncertainty": 0.0291,
        "n_baseline": 60,
        "n_reporting": 60,
        "option": "B",
        "reporting_actual": 8816.93,
        "savings": 2685.51,
        "savings_pct": 0.2335,
    },
    "isolation_normalized": {
        "abs_uncertainty": 23.79,
        "confidence": 0.9,
        "fractional_uncertainty": 0.045,
        "n_normal_periods": 12,
        "nac_baseline": 2266.02,
        "nac_reporting": 1737.42,
        "normalized_savings": 528.61,
        "savings_pct": 0.2333,
    },
    "normalized": {
        "abs_uncertainty": 15.36,
        "confidence": 0.9,
        "fractional_uncertainty": 0.1334,
        "n_normal_periods": 12,
        "nac_baseline": 863.13,
        "nac_reporting": 748.0,
        "normalized_savings": 115.13,
        "savings_pct": 0.1334,
    },
}


def _golden_cases():
    """The exact in-range cases GOLDEN was frozen from (pre-coverage code, main 951e6e6)."""
    from camber.mandv.caltrack import caltrack_savings, caltrack_savings_hourly
    from camber.mandv.normalized import normalized_savings
    from camber.mandv.retrofit_isolation import isolation_normalized_savings, isolation_savings
    from camber.mandv.stats import avoided_energy_savings, fit_stats

    out = {}
    for i, kind in enumerate(KINDS):
        rng = np.random.default_rng(100 + i)
        Tb = rng.uniform(20, 95, 365)
        yb = _truth(Tb) + rng.normal(0, 3, 365)
        m = fit_model(Tb, yb, kind)
        st = fit_stats(yb, m.predict(Tb), N_PARAMS[kind])
        Tr = rng.uniform(25, 90, 200)
        yr = 0.85 * _truth(Tr) + rng.normal(0, 3, 200)
        out[f"cp_{kind}"] = avoided_energy_savings(
            m, Tr, yr, cv_rmse=st.cv_rmse, n_baseline=st.n, p_baseline=N_PARAMS[kind], rho=0.3
        )

    def series(start, days, reduction=0.0, seed=0):
        idx = pd.date_range(start, periods=days * 24, freq="1h")
        rng = np.random.default_rng(seed)
        temp = (
            60
            + 18 * np.sin((idx.hour - 9) / 24 * 2 * np.pi)
            + 8 * np.sin(np.arange(len(idx)) / (24 * 30))
        )
        e = (20.0 + np.clip(temp - 65, 0, None) * 1.5) * (1 - reduction)
        e = e + rng.normal(0, 0.5, len(idx))
        return pd.Series(e, index=idx), pd.Series(temp, index=idx)

    be, bt = series("2023-01-01", 120, seed=1)
    re_, rt = series("2024-01-01", 120, 0.2, seed=2)
    out["caltrack_daily"] = caltrack_savings(be, bt, re_, rt).savings

    def site(start, weeks, scale=1.0, seed=0):
        rng = np.random.default_rng(seed)
        idx = pd.date_range(start, periods=weeks * 168, freq="1h")
        occ = (idx.dayofweek < 5) & (idx.hour >= 8) & (idx.hour < 18)
        t = 60 + 15 * np.sin(np.arange(len(idx)) / 168 * 2 * np.pi) + rng.normal(0, 2, len(idx))
        e = (40 + 30 * occ + np.clip(t - 65, 0, None) * 2) * scale + rng.normal(0, 2, len(idx))
        return pd.Series(e, index=idx), pd.Series(t, index=idx)

    eb, tb = site("2024-01-01", 20)
    er, tr = site("2024-06-01", 8, 0.85, seed=1)
    out["caltrack_hourly"] = caltrack_savings_hourly(eb, tb, er, tr).savings
    rng = np.random.default_rng(7)
    tons_b = rng.uniform(50, 400, 60)
    tons_r = rng.uniform(60, 390, 60)
    yb = 10 + 0.8 * tons_b + rng.normal(0, 5, 60)
    yr = 10 + 0.6 * tons_r + rng.normal(0, 5, 60)
    out["isolation"] = isolation_savings(yb, yr, baseline_driver=tons_b, reporting_driver=tons_r)
    normal = np.linspace(60, 390, 12)
    out["isolation_normalized"] = isolation_normalized_savings(
        yb, yr, normal, baseline_driver=tons_b, reporting_driver=tons_r
    )
    temps = np.array([55, 58, 63, 70, 78, 88, 95, 93, 86, 75, 63, 56], dtype=float)
    rng = np.random.default_rng(3)
    Tb = rng.uniform(50, 100, 200)
    Tr2 = rng.uniform(50, 100, 200)
    mb = fit_model(Tb, 50 + 2 * np.maximum(0, Tb - 65) + rng.normal(0, 2, 200), "3PC")
    mr = fit_model(Tr2, 45 + 1.6 * np.maximum(0, Tr2 - 65) + rng.normal(0, 2, 200), "3PC")
    out["normalized"] = normalized_savings(
        mb,
        mr,
        temps,
        baseline_cv_rmse=0.05,
        n_baseline=200,
        reporting_cv_rmse=0.06,
        n_reporting=200,
        p_baseline=3,
        p_reporting=3,
        rho=0.2,
    )
    rng = np.random.default_rng(11)
    tm = rng.uniform(25, 85, 36)
    em = 300 + 12 * np.maximum(0, 60 - tm) + 9 * np.maximum(0, tm - 65) + rng.normal(0, 10, 36)
    dd = fit_degree_day(tm, em)
    tr3 = rng.uniform(28, 82, 12)
    er3 = 0.9 * (300 + 12 * np.maximum(0, 60 - tr3) + 9 * np.maximum(0, tr3 - 65))
    out["degreeday"] = avoided_energy_savings(
        dd, tr3, er3, cv_rmse=dd.fit.cv_rmse, n_baseline=dd.fit.n, p_baseline=dd.fit.p
    )
    return out


def test_in_range_numbers_are_byte_identical_to_main():
    got = _golden_cases()
    assert set(got) == set(GOLDEN)
    for name, res in got.items():
        d = res.as_dict()
        for key, want in GOLDEN[name].items():
            assert d[key] == want, (name, key, d[key], want)
