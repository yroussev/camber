"""Tests for the streaming sustained-shift alarm on chiller approach drift (camber.chillerdrift)."""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.chillerbaseline import fit_approach_baseline  # noqa: E402
from camber.chillerdrift import ApproachDriftMonitor  # noqa: E402
from camber.driftthresholds import TEMPORAL_CONFIDENCE  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.chiller_drift_alarm_rule import ChillerApproachSustainedDrift  # noqa: E402
from camber.rules.chiller_drift_rule import ChillerApproachDrift  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402

_INTERCEPT_F = 2.0
_SLOPE_F_PER_TON = 0.01
_SIGMA_F = 0.25


def _tons(n, seed=0, offset=0.0):
    rng = np.random.default_rng(seed)
    h = np.arange(n)
    t = 170 + 120 * np.sin((h % 24 - 8) / 24 * 2 * np.pi) + rng.normal(0, 12, n)
    return np.clip(t + offset, 40.0, 400.0)


def _frame(n=24 * 30, *, start="2025-05-01", seed=0, extra=None, tons_offset=0.0):
    """Approach/tons frame; ``extra`` is a per-sample degF offset added to approach."""
    rng = np.random.default_rng(seed + 100)
    idx = pd.date_range(start, periods=n, freq="1h")
    tons = _tons(n, seed=seed, offset=tons_offset)
    approach = _INTERCEPT_F + _SLOPE_F_PER_TON * tons + rng.normal(0, _SIGMA_F, n)
    if extra is not None:
        approach = approach + extra
    return pd.DataFrame({"tons": tons, "approach_f": approach}, index=idx)


def _baseline(seed=1):
    b = fit_approach_baseline(_frame(start="2025-05-01", seed=seed))
    assert b is not None
    return b


# --------------------------------------------------------------------------- the alarm itself


def test_a_sustained_shift_alarms_shortly_after_it_begins():
    b = _baseline()
    n, step_at = 24 * 30, 300
    shift = np.zeros(n)
    shift[step_at:] = 4.0 * _SIGMA_F  # a 4-sigma step that then persists
    run = ApproachDriftMonitor(b).run(_frame(n=n, start="2025-06-01", seed=2, extra=shift))

    assert run is not None and run.alarmed
    # detection lands close behind the step, not tens of samples later
    assert step_at < run.first_alarm_n <= step_at + 25
    assert run.first_alarm_at.startswith("2025-06")
    assert run.peak_climbing > run.limit_f


def test_a_stable_period_never_alarms():
    run = ApproachDriftMonitor(_baseline()).run(_frame(start="2025-06-01", seed=2))
    assert run is not None
    assert not run.alarmed and run.first_alarm_n == -1
    assert run.peak_climbing < run.limit_f


def test_a_one_off_spike_does_not_alarm():
    """Outlier clipping: a single huge excursion must not accumulate like sustained drift."""
    n = 24 * 30
    spike = np.zeros(n)
    spike[500] = 20.0  # one absurd sample -- a dropout, not a fouling event
    run = ApproachDriftMonitor(_baseline()).run(
        _frame(n=n, start="2025-06-01", seed=2, extra=spike)
    )
    assert run is not None
    assert not run.alarmed


def test_transient_excursions_do_not_alarm():
    """A handful of short bursts that always return to baseline is not a sustained shift."""
    n = 24 * 30
    bursts = np.zeros(n)
    for at in (120, 300, 480, 660):
        bursts[at : at + 4] = 4.0 * _SIGMA_F  # four hours, then back to normal
    run = ApproachDriftMonitor(_baseline()).run(
        _frame(n=n, start="2025-06-01", seed=2, extra=bursts)
    )
    assert run is not None
    assert not run.alarmed


def test_a_busier_period_at_matched_load_does_not_alarm():
    """The CUSUM inherits load normalization from the baseline's predict()."""
    b = _baseline(seed=3)
    busy = _frame(start="2025-06-01", seed=4, tons_offset=90.0)
    assert float(busy["approach_f"].median()) > float(
        _frame(start="2025-05-01", seed=3)["approach_f"].median()
    )
    run = ApproachDriftMonitor(b).run(busy)
    assert run is not None
    assert not run.alarmed


def test_update_reports_the_true_residual_while_clipping_the_accumulator():
    b = _baseline()
    m = ApproachDriftMonitor(b)
    predicted = b.predict(200.0)
    st = m.update(200.0, predicted + 20.0)  # a 20 degF excursion
    assert abs(st.residual_f - 20.0) < 1e-6  # reported truthfully
    assert st.climbing <= m.clip_f  # but it contributed at most one clipped sample
    assert not st.alarming


def test_reset_and_rebase_clear_the_accumulator():
    b = _baseline()
    n = 24 * 10
    m = ApproachDriftMonitor(b)
    m.run(_frame(n=n, start="2025-06-01", seed=2, extra=np.full(n, 4.0 * _SIGMA_F)))
    assert m._over > 0
    m.reset()
    assert m._over == 0
    with pytest.raises(ValueError, match="residual scatter"):
        m.rebase(b.__class__(**{**b.as_dict(), "sigma_f": 0.0}))


# --------------------------------------------------------------------------- the rule


def _rule(store, **kw):
    return ChillerApproachSustainedDrift(store, site="SITE", run_id="2025-07-01T00:00", **kw)


def _role_frame(n=24 * 30, *, start="2025-05-01", seed=0, extra=None):
    f = _frame(n=n, start=start, seed=seed, extra=extra)
    return pd.DataFrame(
        {
            Role.COND_APPROACH_TEMP: f["approach_f"],
            Role.CHW_FLOW: f["tons"] * 2.0,
            Role.CHW_SUPPLY_TEMP: np.full(len(f), 44.0),
            Role.CHW_RETURN_TEMP: np.full(len(f), 56.0),
        },
        index=f.index,
    )


def test_rule_emits_a_distinct_sustained_finding():
    n = 24 * 30
    shift = np.zeros(n)
    shift[300:] = 4.0 * _SIGMA_F
    store = BaselineStore()
    f = _rule(store).analyze_periods(
        "CH_1", _role_frame(seed=1), _role_frame(n=n, start="2025-06-01", seed=2, extra=shift)
    )
    assert f.rule == "chiller_approach_drift_sustained"
    assert f.rule != ChillerApproachDrift(BaselineStore()).name  # a separate Finding, not a merge
    assert f.severity == "warn"
    assert f.metrics["cond_sustained_alarm"] is True
    assert f.metrics["cond_first_alarm_at"].startswith("2025-06")
    assert f.metrics["thresholds_provisional"] is True
    # a purely temporal verdict: labelled with the weaker grade, and no magnitude grade at all
    assert f.metrics["temporal_threshold_confidence"] == TEMPORAL_CONFIDENCE
    assert "magnitude_threshold_confidence" not in f.metrics


def test_rule_is_quiet_on_a_stable_chiller():
    f = _rule(BaselineStore()).analyze_periods(
        "CH_1", _role_frame(seed=1), _role_frame(start="2025-06-01", seed=2)
    )
    assert f.severity == "ok"
    assert f.metrics["cond_sustained_alarm"] is False


def test_rule_shares_the_frozen_baseline_with_the_period_rule():
    """Both rules read the same frozen record, so they cannot disagree about normal."""
    store = BaselineStore()
    base, cur = _role_frame(seed=1), _role_frame(start="2025-06-01", seed=2)
    ChillerApproachDrift(store, site="SITE", run_id="r1").analyze_periods("CH_1", base, cur)
    coeffs = dict(store.get("SITE", "CH_1", "chiller_approach_cond").coefficients)

    _rule(store).analyze_periods("CH_1", base, cur)
    # the alarm rule reused the frozen baseline rather than freezing a second, competing one
    assert store.get("SITE", "CH_1", "chiller_approach_cond").coefficients == coeffs
    assert len(store.records()) == 1


def test_accepting_a_new_normal_silences_the_sustained_alarm():
    """Post-service, the accepted baseline resets both the reference and the CUSUM state."""
    n = 24 * 30
    store = BaselineStore()
    base = _role_frame(seed=1)
    # the machine has settled at a new, wider steady state -- alarming against the old baseline
    drifted = _role_frame(n=n, start="2025-06-01", seed=2, extra=np.full(n, 4.0 * _SIGMA_F))
    assert _rule(store).analyze_periods("CH_1", base, drifted).metrics["cond_sustained_alarm"]

    refit = fit_approach_baseline(
        pd.DataFrame(
            {
                "tons": drifted[Role.CHW_FLOW] / 2.0,
                Role.COND_APPROACH_TEMP: drifted[Role.COND_APPROACH_TEMP],
            }
        ),
        approach_col=Role.COND_APPROACH_TEMP,
    )
    store.accept_new_normal(
        refit,
        site="SITE",
        equip="CH_1",
        kind="chiller_approach_cond",
        accepted_by="plant.operator",
        reason="condenser serviced; post-service performance accepted",
        at="2025-06-30T09:00",
    )
    # the post-step portion is now the reference, so it no longer reads as a sustained rise
    after = _rule(store).analyze_periods(
        "CH_1", base, _role_frame(n=n, start="2025-07-01", seed=6, extra=np.full(n, 4.0 * _SIGMA_F))
    )
    assert after.metrics["cond_sustained_alarm"] is False
