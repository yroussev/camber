"""Every drift rule renders its own evidence — the frozen baseline's band (pattern J).

The gap this closes: a drift rule's claim is "this has moved off a frozen line", and the default
evidence (a multitrend of the required roles) shows the levels while hiding the movement. These
tests pin that every shipped drift rule produces a *diagnostic* Evidence carrying a fitted band,
because `drift_evidence` returns None on any wiring mistake and a silent None is exactly how three
rules were found to be mis-wired.
"""

import os
import sys

import matplotlib

matplotlib.use("Agg")  # headless, before pyplot is imported anywhere

import matplotlib.pyplot as plt  # noqa: E402
import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber import ahusim, condensersim, driftsim, evaporatorsim, pumpsim, vavsim  # noqa: E402
from camber.charts.evidence import (  # noqa: E402
    Evidence,
    drift_evidence,
    finding_evidence,
    render_evidence,
)
from camber.driftrun import build_drift_suite  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402

# family -> the physics simulator whose simulate_case() produces its (baseline, current) frames
_SIMS = {
    "ahu": ahusim,
    "chiller": driftsim,
    "condenser": condensersim,
    "evaporator": evaporatorsim,
    "pump": pumpsim,
    "vav": vavsim,
}


@pytest.fixture(autouse=True)
def _close_figs():
    yield
    plt.close("all")


def _frozen(family, *, equip="EQ", sustained_alarm=False):
    """A healthy (baseline, current) pair, with every detector's baseline frozen."""
    case = _SIMS[family].simulate_case(None, 0, seed=5)
    store = BaselineStore()
    suite = build_drift_suite(
        family, store, site="S", run_id="R", freeze_if_missing=True, sustained_alarm=sustained_alarm
    )
    for rule in suite:
        rule.analyze_periods(equip, case.baseline, case.current)
    return suite, store, case


@pytest.mark.parametrize("family", sorted(_SIMS))
def test_every_rule_in_the_family_renders_a_fitted_band(family):
    suite, _store, case = _frozen(family)
    missing = []
    for rule in suite:
        ev = drift_evidence(rule, "EQ", case.current)
        if ev is None or ev.renderer != "diagnostic" or ev.template is None:
            missing.append(rule.name)
        else:
            render_evidence(ev, case.current)  # must not raise
    assert not missing, (
        f"{family}: drift rules with no fitted-band evidence: {sorted(set(missing))}"
    )


@pytest.mark.parametrize("family", sorted(_SIMS))
def test_the_prepared_frame_carries_the_fitted_columns(family):
    """drift_frame must return the frame the model was fitted on, not a (frame, extra) tuple."""
    suite, _store, case = _frozen(family)
    for rule in suite:
        prepared = rule.drift_frame(case.current)
        assert hasattr(prepared, "columns"), f"{rule.name}: drift_frame returned {type(prepared)}"
        _kind, load, metric = rule.drift_signature()
        for col in (load, metric):
            assert col in prepared.columns, f"{rule.name}: {col!r} missing from the prepared frame"


def test_no_frozen_baseline_means_no_evidence_rather_than_an_empty_chart():
    """Nothing frozen -> None, so a reader is never shown a scatter with no line to judge it by."""
    case = ahusim.simulate_case(None, 0, seed=5)
    for rule in build_drift_suite("ahu", BaselineStore(), freeze_if_missing=False):
        assert drift_evidence(rule, "EQ", case.current) is None


def test_finding_evidence_prefers_the_band_over_the_default_trend():
    suite, _store, case = _frozen("ahu")
    rule = next(r for r in suite if r.name == "fan_efficiency_drift")

    ev = finding_evidence(rule, "EQ", case.current)
    assert isinstance(ev, Evidence) and ev.renderer == "diagnostic"

    # with nothing frozen, the same call still yields the default trend rather than nothing
    bare = build_drift_suite("ahu", BaselineStore(), freeze_if_missing=False)[0]
    assert finding_evidence(bare, "EQ", case.current).renderer == "multitrend"


def test_a_drifting_unit_reads_far_outside_the_band_and_a_healthy_one_does_not():
    """The chart has to separate the two cases, or it is decoration."""
    healthy = ahusim.simulate_case(None, 0, seed=5)
    faulted = ahusim.simulate_case("filter_loading", 4, seed=5)

    store = BaselineStore()
    suite = build_drift_suite("ahu", store, site="S", freeze_if_missing=True)
    for rule in suite:
        rule.analyze_periods("EQ", healthy.baseline, healthy.current)
    rule = next(r for r in suite if r.name == "filter_loading_drift")

    _, healthy_mask = render_evidence(drift_evidence(rule, "EQ", healthy.current), healthy.current)
    _, fault_mask = render_evidence(drift_evidence(rule, "EQ", faulted.current), faulted.current)

    assert healthy_mask.mean() < 0.20  # ~5% expected outside a 2-sigma band
    assert fault_mask.mean() > 0.80


def test_the_sustained_alarm_rule_also_carries_a_band():
    suite, _store, case = _frozen("chiller", sustained_alarm=True)
    rule = next(r for r in suite if r.name == "chiller_approach_drift_sustained")
    ev = drift_evidence(rule, "EQ", case.current)
    assert ev is not None and ev.renderer == "diagnostic"
