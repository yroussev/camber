"""Economizer evidence is drawn from the rule's *configured* envelope, not generic defaults.

Regression for the economizer charts: ``EconomizerHighLimit.evidence`` and
``OutdoorAirFraction.evidence`` used to draw ``TEMPLATES["economizer"]`` (65 °F, 0.2 damper)
whatever the rule was configured with, so the red points could disagree with the verdict above
them. Each test re-derives the verdict's percentage from the chart template alone.
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.charts.diagnostic import diagnostic_scatter, template_violations  # noqa: E402
from camber.charts.evidence import render_evidence  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.economizer_lockout_rule import EconomizerHighLimit  # noqa: E402
from camber.rules.oafraction_rule import OutdoorAirFraction  # noqa: E402


def _mixing_frame(n=24 * 21, seed=3):
    """Hourly OAT/RAT/MAT/damper with an economizer that stays ~60 % open in hot weather."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-06-01", periods=n, freq="h")
    hour = idx.hour.to_numpy()
    oat = 60 + 25 * np.sin((hour - 9) / 24 * 2 * np.pi) + rng.normal(0, 2, n)
    rat = np.full(n, 74.0) + rng.normal(0, 0.5, n)
    oaf = np.where(oat > 72, 0.6, 0.25) + rng.normal(0, 0.03, n)
    mat = rat - oaf * (rat - oat)
    return pd.DataFrame(
        {
            Role.OAT: oat,
            Role.RETURN_AIR_TEMP: rat,
            Role.MIXED_AIR_TEMP: mat,
            Role.OA_DAMPER: 100.0 * np.clip(oaf, 0, 1),  # the role pipeline delivers 0-100
        },
        index=idx,
    )


@pytest.mark.parametrize(
    "kw",
    [
        {"high_limit_f": 72.0, "min_oa_pct": 30.0},
        {"high_limit_f": 70.0, "min_oa_pct": None, "differential": False},
        {"high_limit_f": 78.0, "min_oa_pct": 40.0, "oa_margin_pct": 2.0},
    ],
)
def test_high_limit_evidence_rederives_the_verdict(kw):
    frame = _mixing_frame()
    rule = EconomizerHighLimit(**kw)
    f = rule.analyze("DemoAHU", frame)
    ev = rule.evidence("DemoAHU", frame)
    assert ev is not None and ev.frame is not None
    mask = template_violations(ev.frame, ev.template)
    judged = ev.frame[Role.OAT].reindex(mask.index) > kw["high_limit_f"]
    pct = 100.0 * mask[judged].sum() / judged.sum()
    assert f.metrics["n_above_limit"] == int(judged.sum())
    assert round(pct, 2) == f.metrics["not_locked_out_pct"]
    # every flagged sample is above the configured limit -- not the generic 65 °F one
    assert not (mask & ~judged).any()
    assert f"{kw['high_limit_f']:g}" in ev.template.name


def test_high_limit_damper_basis_uses_configured_min_damper():
    frame = _mixing_frame().drop(columns=[Role.MIXED_AIR_TEMP, Role.RETURN_AIR_TEMP])
    rule = EconomizerHighLimit(high_limit_f=68.0, min_damper=0.45)
    f = rule.analyze("DemoAHU", frame)
    assert f.metrics["basis"] == "damper position"
    ev = rule.evidence("DemoAHU", frame)
    mask = template_violations(ev.frame, ev.template)
    judged = ev.frame[Role.OAT].reindex(mask.index) > 68.0
    assert round(100.0 * mask[judged].sum() / judged.sum(), 2) == f.metrics["not_locked_out_pct"]
    # the damper arrives 0-100 but is judged (and drawn) as a 0-1 fraction
    assert ev.frame["oa_damper_frac"].max() <= 1.0


def test_differential_excluded_samples_are_not_drawn():
    frame = _mixing_frame()
    frame[Role.RETURN_AIR_TEMP] = 90.0  # every hot sample is below RAT -> differential excludes
    rule = EconomizerHighLimit(high_limit_f=72.0)
    ev = rule.evidence("DemoAHU", frame)
    drawn = ev.frame.dropna()
    assert (drawn[Role.OAT] <= 72.0).all()
    assert not template_violations(ev.frame, ev.template).any()


def test_oa_fraction_evidence_rederives_both_percentages():
    frame = _mixing_frame()
    rule = OutdoorAirFraction(min_oa_pct=30.0, cooling_cutoff_f=74.0)
    f = rule.analyze("DemoAHU", frame)
    ev = rule.evidence("DemoAHU", frame)
    mask = template_violations(ev.frame, ev.template)
    y = ev.frame["oa_fraction_pct"]
    cooling = ev.frame[Role.OAT] > 74.0
    excess = mask & cooling & (y > 30.0)
    under = mask & (y < 30.0)
    assert round(100.0 * excess.sum() / cooling.sum(), 1) == f.metrics["excess_oa_pct"]
    assert round(100.0 * under.mean(), 1) == f.metrics["under_vent_pct"]
    assert f.metrics["n_valid"] == len(ev.frame)


def test_oa_fraction_evidence_needs_oat():
    frame = _mixing_frame().drop(columns=[Role.OAT])
    assert OutdoorAirFraction().evidence("DemoAHU", frame) is None


def test_template_violations_matches_diagnostic_scatter():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    frame = _mixing_frame()
    ev = EconomizerHighLimit(high_limit_f=72.0).evidence("DemoAHU", frame)
    fig, ax = plt.subplots()
    _, drawn = diagnostic_scatter(ev.frame, ev.template, ax=ax)
    plt.close(fig)
    pd.testing.assert_series_equal(drawn, template_violations(ev.frame, ev.template))
    # and the evidence renderer routes the derived frame, not the caller's role frame
    fig, ax = plt.subplots()
    _, via = render_evidence(ev, frame, ax=ax)
    plt.close(fig)
    pd.testing.assert_series_equal(via, drawn)


def test_sat_reset_evidence_draws_the_samples_the_slope_is_fitted_on():
    from camber.rules.satreset_rule import SupplyAirReset

    idx = pd.date_range("2026-06-01", periods=24 * 14, freq="h")
    running = (idx.dayofweek < 5) & (idx.hour >= 7) & (idx.hour < 18)
    frame = pd.DataFrame(
        {
            Role.SUPPLY_AIR_TEMP: np.where(running, 55.0, 72.0) + np.linspace(0, 1, len(idx)),
            Role.OAT: np.linspace(50, 95, len(idx)),
            Role.COOL_VALVE: np.where(running, 40.0, 0.0),
        },
        index=idx,
    )
    rule = SupplyAirReset()
    f = rule.analyze("DemoAHU", frame)
    ev = rule.evidence("DemoAHU", frame)
    assert ev.renderer == "oat_scatter" and ev.template is None
    assert len(ev.frame.dropna()) == f.metrics["n_considered"]
    assert (ev.frame[Role.SUPPLY_AIR_TEMP] < 60).all()  # the parked, fan-off SAT is not drawn
