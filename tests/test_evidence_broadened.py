"""Tests for the broadened pattern J — more rule evidence hooks + Std-211 audit wiring.
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
from camber.rules.reheat_rule import ReheatPenalty  # noqa: E402
from camber.rules.setback_rule import NightWeekendSetback  # noqa: E402
from camber.rules.overcooling_rule import OvercoolingMinFlow  # noqa: E402
from camber.charts.evidence import finding_evidence, render_evidence  # noqa: E402
from camber.report.audit import AuditReport  # noqa: E402


def _frame(n_days=10, seed=0):
    idx = pd.date_range("2024-07-01", periods=n_days * 24, freq="1h")
    rng = np.random.default_rng(seed)
    oat = pd.Series(rng.uniform(50, 95, len(idx)), index=idx)
    return pd.DataFrame({
        Role.HEAT_VALVE: pd.Series(np.where(oat > 70, 0.3, 0.1), index=idx),
        Role.OAT: oat,
        Role.SUPPLY_FAN_SPEED: pd.Series(np.where((idx.hour >= 6) & (idx.hour <= 20), 0.8, 0.2),
                                         index=idx),
        Role.SPACE_TEMP: pd.Series(70 + rng.normal(0, 1, len(idx)), index=idx),
        Role.COOL_SP: pd.Series(74.0, index=idx),
    })


def test_reheat_setback_overcooling_hooks_render():
    frame = _frame()
    cases = {
        ReheatPenalty(): "oat_scatter",
        NightWeekendSetback(): "carpet",
        OvercoolingMinFlow(): "multitrend",
    }
    for rule, renderer in cases.items():
        ev = finding_evidence(rule, "AHU-1", frame)
        assert ev is not None and ev.renderer == renderer
        ax, _ = render_evidence(ev, frame)
        assert ax is not None and (ax.collections or ax.get_lines() or ax.images)


def test_hook_declines_when_required_roles_absent():
    frame = _frame()
    assert finding_evidence(ReheatPenalty(), "AHU-1", frame[[Role.HEAT_VALVE]]) is None  # no OAT
    assert finding_evidence(NightWeekendSetback(), "AHU-1", frame[[Role.OAT]]) is None    # no fan
    assert finding_evidence(OvercoolingMinFlow(), "AHU-1", frame[[Role.SPACE_TEMP]]) is None  # no SP


def test_audit_embeds_evidence_with_rules_and_frames():
    frame = _frame()
    rep = AuditReport(building="HQ", level=2)
    rep.add_findings([
        Finding(rule="reheat_penalty", equip="AHU-1", severity="fault", summary="reheat at high OAT"),
        Finding(rule="night_weekend_setback", equip="AHU-1", severity="warn", summary="no setback"),
    ])
    plain = rep.to_html()
    rich = rep.to_html(rules=[ReheatPenalty(), NightWeekendSetback()], frames={"AHU-1": frame})
    assert "Finding evidence" not in plain
    assert "Finding evidence" in rich and rich.count("<figure>") == 2


def test_audit_evidence_skips_finding_without_a_frame():
    frame = _frame()
    rep = AuditReport(building="HQ", level=2)
    rep.add_findings([
        Finding(rule="reheat_penalty", equip="AHU-1", severity="fault", summary="x"),
        Finding(rule="reheat_penalty", equip="AHU-2", severity="fault", summary="y"),  # no frame
    ])
    rich = rep.to_html(rules=[ReheatPenalty()], frames={"AHU-1": frame})
    assert rich.count("<figure>") == 1        # only AHU-1 rendered; AHU-2 skipped, no error
