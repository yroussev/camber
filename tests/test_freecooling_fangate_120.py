"""#120: ``free_cooling_missed`` judges fan-on hours only, and its evidence plots only those."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from camber.charts.diagnostic import template_violations
from camber.charts.evidence import finding_evidence, render_evidence
from camber.model.roles import Role
from camber.rules.freecoolingmissed_rule import FreeCoolingMissed
from camber.schedules import FAN_GATE_NONE

N = 24 * 10


def _frame(*, fan: bool = True) -> pd.DataFrame:
    """Ten days hourly, all free-cooling weather (40-56 F). Fan on 06-18: the valve is closed
    except 08-10 (three missed hours a day). Fan off overnight with the valve parked at 50 %,
    a controller output that cools nothing because no air moves."""
    idx = pd.date_range("2025-03-03", periods=N, freq="h")
    hour = idx.hour.to_numpy()
    on = (hour >= 6) & (hour < 18)
    oat = 48.0 + 8.0 * np.sin(np.arange(N) / 24 * 2 * np.pi)
    valve = np.where(on, np.where((hour >= 8) & (hour < 11), 40.0, 0.0), 50.0)
    cols = {Role.OAT: oat, Role.COOL_VALVE: valve}
    if fan:
        cols[Role.SUPPLY_FAN_STATUS] = on.astype(float)
    return pd.DataFrame(cols, index=idx)


def test_fan_off_hours_are_neither_available_nor_missed():
    f = FreeCoolingMissed().analyze("AHU-1", _frame())
    m = f.metrics
    assert m["fan_gate"] == "fan status"
    assert m["n_free_cooling_samples"] == 10 * 12  # fan-on hours only
    assert m["n_masked_fan_off"] == 10 * 12
    assert m["missed_pct"] == 25.0  # 3 of the 12 fan-on hours a day
    assert f.severity == "fault"
    assert "_missing_optional" not in m or "supply_fan_status" not in m["_missing_optional"]


def test_fan_off_cooling_alone_is_not_a_fault():
    fr = _frame()
    fr.loc[fr[Role.SUPPLY_FAN_STATUS] > 0.5, Role.COOL_VALVE] = 0.0  # fan-on: valve closed
    f = FreeCoolingMissed().analyze("AHU-1", fr)
    assert f.severity == "ok" and f.metrics["missed_pct"] == 0.0
    # ungated, the parked valve overnight read as missed free cooling
    ungated = FreeCoolingMissed(fan_gate=False).analyze("AHU-1", fr)
    assert ungated.metrics["fan_gate"] == "off"
    assert ungated.metrics["missed_pct"] == 50.0 and ungated.severity == "fault"


def test_no_fan_signal_runs_ungated_and_says_so():
    fr = _frame(fan=False)
    f = FreeCoolingMissed().analyze("AHU-1", fr)
    off = FreeCoolingMissed(fan_gate=False).analyze("AHU-1", fr)
    assert f.metrics["fan_gate"] == FAN_GATE_NONE
    assert f.metrics["n_masked_fan_off"] == 0
    assert "supply_fan_status" in f.metrics["_missing_optional"]
    assert f.metrics["missed_pct"] == off.metrics["missed_pct"]
    assert f.metrics["n_free_cooling_samples"] == N


def test_fan_speed_is_the_fallback_gate():
    fr = _frame()
    fr[Role.SUPPLY_FAN_SPEED] = fr.pop(Role.SUPPLY_FAN_STATUS) * 60.0
    m = FreeCoolingMissed().analyze("AHU-1", fr).metrics
    assert m["fan_gate"] == "fan speed proxy"
    assert m["n_free_cooling_samples"] == 10 * 12 and m["missed_pct"] == 25.0


def test_all_free_cooling_weather_fan_off_is_info():
    fr = _frame()
    fr[Role.SUPPLY_FAN_STATUS] = 0.0
    f = FreeCoolingMissed().analyze("AHU-1", fr)
    assert f.severity == "info" and "supply fan on" in f.summary
    assert f.metrics["n_masked_fan_off"] == N
    assert FreeCoolingMissed().evidence("AHU-1", fr) is None


def test_evidence_plots_only_judged_samples_and_flags_the_missed():
    fr = _frame()
    rule = FreeCoolingMissed()
    ev = finding_evidence(rule, "AHU-1", fr)
    assert ev.renderer == "diagnostic"
    on = fr[Role.SUPPLY_FAN_STATUS] > 0.5
    assert ev.frame.index.equals(fr.index[on])  # no fan-off hour plotted
    # with no integrated economizer the band alone flags exactly the rule's missed samples
    assert template_violations(ev.frame, ev.template).equals(ev.mask)
    assert int(ev.mask.sum()) == 30  # the 08-10 hours, ten days
    assert float(ev.mask.mean()) * 100 == rule.analyze("AHU-1", fr).metrics["missed_pct"]
    import matplotlib

    matplotlib.use("Agg")
    ax, mask = render_evidence(ev, fr)
    assert int(mask.sum()) == 30 and "fan on" in ax.get_title()
    assert ax.get_xlabel() == "oat (°F)" and ax.get_ylabel() == "cool valve (%)"


def test_evidence_leaves_out_weather_outside_the_window_and_never_reds_integrated():
    fr = _frame()
    fr.iloc[:24, fr.columns.get_loc(Role.OAT)] = 70.0  # day 1: no free-cooling weather
    fr.iloc[24:48, fr.columns.get_loc(Role.OAT)] = 30.0  # day 2: below a 33.8 F lockout
    # day 3: the missed hours ran on full outside air (integrated economizer)
    fr[Role.OA_DAMPER] = 20.0
    fr.iloc[48 + 8 : 48 + 11, fr.columns.get_loc(Role.OA_DAMPER)] = 100.0
    rule = FreeCoolingMissed(low_limit_f=33.8)
    m = rule.analyze("AHU-1", fr).metrics
    ev = rule.evidence("AHU-1", fr)
    assert ev.frame.index.min() >= fr.index[48]  # days 1-2 not plotted
    assert len(ev.frame) == m["n_free_cooling_samples"] == 8 * 12
    assert m["n_integrated_economizer_samples"] == 3
    # day 3's integrated hours sit above the band but the chart does not shade them
    assert int(template_violations(ev.frame, ev.template).sum()) == 8 * 3
    import matplotlib

    matplotlib.use("Agg")
    _, red = render_evidence(ev, fr)
    assert int(red.sum()) == 7 * 3 and red.equals(ev.mask)
    assert float(red.mean()) * 100 == pytest.approx(m["missed_pct"], abs=0.01)
