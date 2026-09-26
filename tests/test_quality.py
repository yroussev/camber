"""Tests for the data-quality layer (ingest.quality)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.ingest.quality import (  # noqa: E402
    assess,
    clean,
    gap_count,
    infer_freq,
    longest_flatline,
    outlier_mask,
)


def _series(values, start="2024-01-01", freq="1h"):
    idx = pd.date_range(start, periods=len(values), freq=freq)
    return pd.Series(values, index=idx, dtype="float64")


# --- primitives ------------------------------------------------------------ #


def test_infer_freq_modal_interval():
    s = _series(range(10), freq="15min")
    assert infer_freq(s.index) == pd.Timedelta("15min")


def test_outlier_mask_flags_spike_not_clean_points():
    vals = [10.0] * 20 + [10000.0]  # one gross spike
    s = _series(vals)
    m = outlier_mask(s)
    assert m.sum() == 1
    assert m.iloc[-1]


def test_outlier_mask_constant_series_has_none():
    s = _series([5.0] * 30)
    assert outlier_mask(s).sum() == 0  # MAD=0 -> no false positives


def test_longest_flatline_counts_run():
    s = _series([1, 2, 2, 2, 2, 3, 4])
    assert longest_flatline(s) == 4


def test_gap_count_detects_time_holes():
    idx = list(pd.date_range("2024-01-01", periods=5, freq="1h"))
    idx += [pd.Timestamp("2024-01-01 10:00")]  # 6h jump after the 5th sample
    s = pd.Series(range(6), index=pd.DatetimeIndex(idx), dtype="float64")
    assert gap_count(s.index, pd.Timedelta("1h")) == 1


# --- assess ---------------------------------------------------------------- #


def test_assess_clean_series_scores_high():
    s = _series(np.sin(np.linspace(0, 6, 200)) * 10 + 50)
    r = assess(s)
    assert r.coverage == 1.0
    assert r.n_outliers == 0
    assert r.score > 0.95


def test_assess_counts_missing_and_lowers_coverage():
    vals = list(range(10))
    s = _series(vals).astype("float64")
    s.iloc[2] = np.nan
    s.iloc[5] = np.nan
    r = assess(s)
    assert r.n_missing == 2
    assert r.n == 8
    assert abs(r.coverage - 0.8) < 1e-9
    assert r.score < 1.0


def test_assess_outliers_penalize_score():
    # a varying (non-flatlined) baseline so the outlier penalty is what differs
    clean_s = _series(np.sin(np.linspace(0, 6, 50)) * 5 + 50)
    dirty = clean_s.copy()
    dirty.iloc[10] = 99999.0
    dirty.iloc[20] = -99999.0
    r = assess(dirty)
    assert r.n_outliers == 2
    assert r.score < assess(clean_s).score


def test_assess_as_dict_serializable():
    r = assess(_series(range(20)))
    d = r.as_dict()
    assert isinstance(d["expected_freq"], str)
    assert "score" in d


# --- clean (with audit trail) ---------------------------------------------- #


def test_clean_drop_outliers_logs_action():
    s = _series([10.0] * 20)
    s.iloc[5] = 88888.0
    cleaned, log = clean(s, drop_outliers=True)
    assert np.isnan(cleaned.iloc[5])
    assert log.steps[0]["op"] == "drop_outliers"
    assert log.steps[0]["n_affected"] == 1
    assert log.total_changed == 1


def test_clean_fill_limit_respected():
    vals = [1.0, np.nan, np.nan, np.nan, 5.0]
    s = _series(vals)
    cleaned, log = clean(s, fill_limit=1)  # only 1 consecutive NaN filled
    assert not np.isnan(cleaned.iloc[1])  # first NaN filled
    assert np.isnan(cleaned.iloc[2])  # second NaN left as honest hole
    assert log.steps[0]["op"] == "ffill"
    assert log.steps[0]["n_affected"] == 1


def test_clean_no_ops_empty_log():
    s = _series([10.0] * 10)
    cleaned, log = clean(s)  # no flags enabled
    assert log.steps == []
    assert cleaned.equals(s)


def test_clean_outliers_then_fill_order():
    s = _series([10.0, 10.0, 99999.0, 10.0, 10.0])
    cleaned, log = clean(s, drop_outliers=True, fill_limit=1)
    # outlier became NaN then was forward-filled from the prior value
    assert cleaned.iloc[2] == 10.0
    assert [st["op"] for st in log.steps] == ["drop_outliers", "ffill"]


# --- regime-aware outlier scoring (intermittent signals) ------------------- #


def _burst(duty, *, n=720, hi=50.0, noise=0.03, seed=0):
    """A duty-cycled meter: `hi` during a daily block, ~0 otherwise. No faults injected."""
    rng = np.random.default_rng(seed)
    k = max(1, int(24 * duty))
    base = np.where(np.arange(n) % 24 < k, hi, 0.0)
    return _series(np.clip(base + rng.normal(0, hi * noise, n), 0.0, None))


def test_regime_fields_are_none_when_untestable():
    """Tri-state: None means the split could not be TESTED, never 'no split found'."""
    q = assess(_series([1.0, 2.0]))
    assert q.n_regimes is None  # `is None`, not falsy
    assert q.regime_outlier_frac is None
    assert q.n_regime_outliers is None


def test_continuous_signals_are_a_single_regime():
    rng = np.random.default_rng(0)
    for label, vals in (
        ("sine", 50 + 10 * np.sin(np.arange(300) / 5)),
        ("ramp", np.arange(300.0)),
        ("noise", rng.normal(50, 5, 300)),
    ):
        q = assess(_series(list(vals)), regime_aware=True)
        assert q.n_regimes == 1, label
        assert q.regime_threshold is None, label
        # the test ran and gave the same answer -- not "untestable"
        assert q.regime_outlier_frac == q.outlier_frac, label


def test_single_spike_is_not_a_regime():
    """The mass gate: many samples in a band is a regime, one sample far away is a spike."""
    s = _series([10.0] * 20 + [10000.0])
    q = assess(s, regime_aware=True)
    assert q.n_regimes == 1
    assert q.n_regime_outliers == 1  # still caught with the regime path on
    assert q.score == assess(s).score  # and the score is untouched


def test_intermittent_meter_stops_being_scored_as_broken():
    s = _burst(0.12)  # an HHW BTU meter: weekday bursts, near-zero otherwise
    pooled, regime = assess(s), assess(s, regime_aware=True)

    assert regime.n_regimes == 2
    assert pooled.score < 0.8  # the defect
    assert regime.score > 0.95  # the fix
    assert pooled.outlier_frac > 0.05
    assert regime.outlier_frac == pooled.outlier_frac  # pooled meaning never masked
    assert regime.regime_outlier_frac < 0.01


def test_spike_inside_the_on_regime_is_still_flagged():
    """The anti-masking property: exempting the duty cycle must not blind the test inside it."""
    s = _burst(0.30, seed=3)
    vals = s.to_numpy().copy()
    vals[[100, 200, 300]] = [500.0, 480.0, 520.0]  # 10x the normal burst
    q = assess(_series(list(vals)), regime_aware=True)

    assert q.n_regimes == 2
    assert q.n_regime_outliers == 3


def test_randomly_railed_signal_is_not_treated_as_a_duty_cycle():
    """The coherence gate: a real duty cycle persists in runs, a comms dropout does not."""
    rng = np.random.default_rng(7)
    vals = 40.0 + rng.normal(0, 3.0, 720)
    vals[rng.random(720) < 0.30] = 0.0  # scattered rail to zero -- a fault, not a schedule
    s = _series(list(vals))

    q = assess(s, regime_aware=True)
    assert q.n_regimes == 1  # refused to split
    assert q.score == assess(s).score  # so the fault is not masked away


def test_regime_aware_is_off_by_default():
    """Scoring a duty cycle as normal is the direction that could mask a fault -- so opt in."""
    s = _burst(0.30)
    assert assess(s).score == assess(s, regime_aware=False).score
    assert assess(s, regime_aware=True).score > assess(s).score


def test_duty_cycle_sweep_is_stable_and_never_untrusted():
    """The regression. Today the score swings chaotically with duty cycle; it must not."""
    duties = [0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.80]
    pooled = [assess(_burst(d)).score for d in duties]
    fixed = [assess(_burst(d), regime_aware=True).score for d in duties]

    assert min(pooled) < 0.5  # the defect: healthy meters gated out at min_trust=0.5
    assert all(v > 0.95 for v in fixed), dict(zip(duties, fixed))
    assert max(fixed) - min(fixed) < 0.05  # stable across duty, not chaotic


def test_status_series_at_low_duty_scores_clean():
    """Five rules take a status role as *required*, so 0/1 points hit this path too."""
    vals = np.where(np.arange(720) % 24 < 4, 1.0, 0.0)
    q = assess(_series(list(vals)), regime_aware=True)
    assert q.n_regimes == 2
    assert q.regime_outlier_frac < 0.01


def test_in_range_railing_sensor_is_a_known_blind_spot():
    """Documents a gap this change neither creates nor closes -- so it is not mistaken for a pass.

    A sensor alternating between two plausible values in long blocks scores ~0.99 today, because
    `longest_flatline` measures the longest single run over the whole series. The regime read at
    least makes the two-state structure *visible*; catching it is future work.
    """
    vals = np.where((np.arange(720) // 24) % 2 == 0, 55.0, 75.0)
    q = assess(_series(list(vals)), regime_aware=True)
    assert q.score > 0.95  # not caught -- recorded, not asserted as correct
    assert q.n_regimes == 2  # but the structure is reported
    assert q.regime_threshold is not None


# --- the shape-aware read (skewed / tightly-controlled signals) -------------- #


def _idle_then_ramp(n=24 * 60, seed=0):
    """A healthy HW pump: idles at 29 % half the day, then ramps smoothly with load. No faults."""
    rng = np.random.default_rng(seed)
    h = np.arange(n) % 24
    load = np.clip(np.sin((h - 6) / 16 * np.pi), 0.0, None)  # 06:00-22:00 load hump
    speed = 29.0 + 55.0 * load**2 + rng.normal(0, 0.1, n)
    return _series(list(speed))


def test_skewed_operating_tail_is_not_outliers_on_the_shape_read():
    s = _idle_then_ramp()
    q = assess(s, shape_aware=True, scale_floor=1.0)
    assert q.outlier_frac > 0.1  # the defect: pooled read calls load operation outliers
    assert q.shape_outlier_frac < 0.02  # the fix
    assert q.score > 0.95
    assert assess(s).score < 0.8  # opt-in: the default score is unchanged


def test_scale_floor_ignores_deviations_inside_sensor_precision():
    """A loop DP held at setpoint: MAD ~0, so float noise was scored as outliers."""
    rng = np.random.default_rng(0)
    vals = 480.52 + rng.choice([0.0, 0.0, 0.0, 0.01, -0.01], 720) + rng.normal(0, 1e-5, 720)
    s = _series(list(vals))
    assert assess(s).outlier_frac > 0.2  # the defect
    assert assess(s, shape_aware=True, scale_floor=2.4).shape_outlier_frac == 0.0


def test_shape_read_still_catches_spikes_in_a_skewed_series():
    vals = _idle_then_ramp().to_numpy().copy()
    vals[[100, 400, 900]] = [950.0, 1000.0, -300.0]  # glitches far outside the operating range
    q = assess(_series(list(vals)), shape_aware=True, scale_floor=1.0)
    assert q.n_shape_outliers >= 3


def test_shape_read_does_not_absorb_a_scattered_rail():
    """The coherence guard: the two-sided scale alone would swallow a 30 % scattered rail."""
    rng = np.random.default_rng(7)
    vals = 40.0 + rng.normal(0, 3.0, 720)
    vals[rng.random(720) < 0.30] = 0.0
    q = assess(_series(list(vals)), shape_aware=True, scale_floor=0.2)
    assert q.shape_outlier_frac == q.outlier_frac > 0.25
    assert q.score < 0.5


def test_shape_read_matches_pooled_on_symmetric_noise():
    rng = np.random.default_rng(3)
    vals = rng.normal(50, 5, 2000)
    vals[::200] = 500.0
    q = assess(_series(list(vals)), shape_aware=True)
    assert abs(q.shape_outlier_frac - q.outlier_frac) < 0.002
