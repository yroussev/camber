"""Tests for pattern J — rules as a chart engine (camber.charts.evidence + dashboard wiring).
Rendering runs headless on Agg."""

import os
import sys

import matplotlib
matplotlib.use("Agg")  # headless, before pyplot is imported anywhere

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def _close_figs():
    yield
    plt.close("all")


from camber.model.roles import Role  # noqa: E402
from camber.rules.base import Finding  # noqa: E402
from camber.rules.simul_hc import SimultaneousHeatCool  # noqa: E402
from camber.charts.diagnostic import TEMPLATES  # noqa: E402
from camber.charts.evidence import (  # noqa: E402
    Evidence, evidence_descriptor, finding_evidence, render_evidence,
)
from camber.report.dashboard import build_dashboard  # noqa: E402


def _hc_frame(n=200, seed=0):
    idx = pd.date_range("2024-07-01", periods=n, freq="1h")
    rng = np.random.default_rng(seed)
    cool = pd.Series(np.where(idx.hour > 12, 0.6, 0.0), index=idx)
    heat = pd.Series(np.where((idx.hour > 12) & (idx.hour < 18), 0.5, 0.0), index=idx)  # overlaps
    oat = pd.Series(rng.uniform(60, 90, n), index=idx)
    return pd.DataFrame({Role.HEAT_VALVE: heat, Role.COOL_VALVE: cool, Role.OAT: oat})


def test_render_evidence_diagnostic_returns_axes_and_mask():
    frame = _hc_frame()
    ev = Evidence(renderer="diagnostic", template=TEMPLATES["no_simultaneous_hc"])
    ax, mask = render_evidence(ev, frame)
    assert ax.collections                         # expected band + scatter drawn
    assert isinstance(mask, pd.Series) and mask.sum() > 0   # both-open points detected


def test_render_evidence_multitrend_with_mask():
    frame = _hc_frame()
    mask = (frame[Role.HEAT_VALVE] > 0) & (frame[Role.COOL_VALVE] > 0)
    ev = Evidence(renderer="multitrend", roles=[Role.HEAT_VALVE, Role.COOL_VALVE],
                  mask=mask, label="both_open")
    ax, m = render_evidence(ev, frame)
    assert ax.get_lines() and m is mask           # trends drawn, mask passed through


def test_render_evidence_unknown_renderer_raises():
    with pytest.raises(ValueError):
        render_evidence(Evidence(renderer="nope"), _hc_frame())


def test_finding_evidence_hook_present_absent_and_declined():
    frame = _hc_frame()
    rule = SimultaneousHeatCool()
    ev = finding_evidence(rule, "AHU-1", frame)
    assert ev is not None and ev.renderer == "diagnostic"

    class _NoHook:                                                   # no hook, no required roles
        name = "x"
        roles_required = ()
    assert finding_evidence(_NoHook(), "AHU-1", frame) is None      # nothing to plot -> None

    # tailored hook declines (cooling role missing) -> default evidence of the present required role
    bare = frame[[Role.HEAT_VALVE]]
    ev_bare = finding_evidence(rule, "AHU-1", bare)
    assert ev_bare is not None and ev_bare.renderer == "multitrend"

    # no required role present at all -> None
    assert finding_evidence(rule, "AHU-1", frame[[Role.OAT]]) is None


def test_evidence_descriptor_is_jsonable():
    frame = _hc_frame()
    mask = (frame[Role.HEAT_VALVE] > 0) & (frame[Role.COOL_VALVE] > 0)
    ev = Evidence(renderer="multitrend", roles=[Role.HEAT_VALVE, Role.COOL_VALVE], mask=mask)
    d = evidence_descriptor(ev)
    assert d["renderer"] == "multitrend"
    assert d["roles"] == ["HEAT_VALVE", "COOL_VALVE"]
    assert len(d["violations"]) == int(mask.sum()) and all(isinstance(t, str) for t in d["violations"])


def test_finding_has_optional_evidence_field():
    f = Finding(rule="r", equip="e", severity="fault")     # constructs without evidence
    assert f.evidence is None and "evidence" in f.as_dict()


def test_dashboard_renders_evidence_section_for_actionable_finding():
    frame = _hc_frame()
    rule = SimultaneousHeatCool()
    fault = Finding(rule=rule.name, equip="AHU-1", severity="fault",
                    summary="AHU-1: both coils open 14% of hours")
    html = build_dashboard(frame, findings=[fault], rules=[rule], carpet_col=Role.COOL_VALVE)
    assert "<h2>Evidence</h2>" in html and "<figcaption>" in html
    # without rules there is no evidence section, but the dashboard still builds
    html2 = build_dashboard(frame, findings=[fault], carpet_col=Role.COOL_VALVE)
    assert "<h2>Evidence</h2>" not in html2 and "Findings" in html2


def test_dashboard_skips_evidence_for_rule_without_hook():
    frame = _hc_frame()

    class _Plain:
        name = "plain_rule"
    fault = Finding(rule="plain_rule", equip="AHU-1", severity="fault", summary="x")
    html = build_dashboard(frame, findings=[fault], rules=[_Plain()], carpet_col=Role.COOL_VALVE)
    assert "<h2>Evidence</h2>" not in html          # no hook -> no evidence, no error


def test_default_evidence_covers_rules_without_a_hook():
    # a rule with no tailored evidence() hook still renders a multitrend of its required roles
    from camber.rules.chiller_rule import ChillerEfficiency
    idx = pd.date_range("2024-07-01", periods=48, freq="1h")
    frame = pd.DataFrame({Role.POWER: pd.Series(100.0, index=idx),
                          Role.CHW_SUPPLY_TEMP: pd.Series(44.0, index=idx),
                          Role.CHW_RETURN_TEMP: pd.Series(54.0, index=idx),
                          Role.CHW_FLOW: pd.Series(500.0, index=idx)})
    ev = finding_evidence(ChillerEfficiency(), "CH-1", frame)
    assert ev is not None and ev.renderer == "multitrend" and len(ev.roles) == 4


def test_default_evidence_none_when_no_required_role_present():
    from camber.rules.chiller_rule import ChillerEfficiency
    idx = pd.date_range("2024-07-01", periods=48, freq="1h")
    assert finding_evidence(ChillerEfficiency(), "CH-1",
                            pd.DataFrame({Role.OAT: pd.Series(70.0, index=idx)})) is None


def test_tailored_hook_still_wins_over_default():
    frame = _hc_frame()
    ev = finding_evidence(SimultaneousHeatCool(), "AHU-1", frame)
    assert ev.renderer == "diagnostic"        # the tailored hook, not the default multitrend
