"""0.104 (#150): size-relative flow / speed gates and constant-speed pumps in the pump drift family.

The pump and loop drift detectors used to gate on absolute numbers (50 gpm, 15 % speed, 15 / 30 gpm,
1 / 2 kW) that a small pump can never meet. These tests pin the scaled gates, the cap that keeps a
full-size pump exactly as before, explicit-parameter precedence, the learned-VFD-floor speed gate,
the constant-speed path, the pumpsim size sweep, and full-size identity against a golden captured
before the change.
"""

import json
import math
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.pumpsim import (  # noqa: E402
    DESIGN_FLOW_GPM,
    FAULTS,
    build_pump_suite,
    diagnose_pump_frames,
    make_cases,
    simulate_case,
)
from camber.rules import _pumpgates as G  # noqa: E402
from camber.rules.loop_deltat_rule import LoopDeltaTDrift  # noqa: E402
from camber.rules.loop_dp_rule import LoopDPDrift  # noqa: E402
from camber.rules.pump_flow_rule import PumpFlowDrift  # noqa: E402
from camber.rules.pump_head_rule import PumpHeadDrift  # noqa: E402
from camber.rules.pump_power_rule import PumpPowerDrift  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402

_GOLDEN = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "golden", "pumpsim_fullsize.json"
)
_FIRED = ("warn", "fault")


def _run(rule_cls, case, **kw):
    return rule_cls(BaselineStore(), **kw).analyze_periods(case.equip, case.baseline, case.current)


# --------------------------------------------------------------------------- the gates


def test_flow_gates_scale_with_size_and_cap_at_the_old_values():
    big = G.normalizer_gates(np.linspace(300, 1000, 500), Role.CHW_FLOW, None, None)
    assert (big.min_load, big.min_span, big.source) == (50.0, 10.0, "size_relative")
    small = G.normalizer_gates(np.linspace(12, 40, 500), Role.CHW_FLOW, None, None)
    p95 = float(np.percentile(np.linspace(12, 40, 500), 95))
    assert small.min_load == pytest.approx(0.10 * p95, rel=1e-6)
    assert small.min_span == pytest.approx(0.20 * p95, rel=1e-6)
    # 500 gpm p95 is where the cap starts: every larger pump is gated as before
    assert G.normalizer_gates(np.full(50, 520.0), Role.HW_FLOW, None, None).min_load == 50.0


def test_explicit_gates_and_floors_win():
    g = G.normalizer_gates(np.linspace(12, 40, 500), Role.CHW_FLOW, 50.0, 10.0)
    assert (g.min_load, g.min_span, g.source) == (50.0, 10.0, "explicit")
    assert G.scaled_floor(np.full(10, 40.0), 15.0, 0.02, 15.0) == 15.0
    assert G.speed_gate(np.full(10, 50.0), 22.0).min_load == 22.0


def test_magnitude_floors_scale_and_cap():
    assert G.scaled_floor(np.full(10, 40.0), None, G.FLOW_WARN_FRAC, 15.0) == pytest.approx(0.8)
    assert G.scaled_floor(np.full(10, 983.0), None, G.FLOW_WARN_FRAC, 15.0) == 15.0
    assert G.scaled_floor(np.full(10, 19.7), None, G.POWER_WARN_FRAC, 1.0) == 1.0
    assert G.scaled_floor(np.full(10, 0.8), None, G.POWER_WARN_FRAC, 1.0) == pytest.approx(0.044)
    assert G.scaled_floor(None, None, 0.02, 15.0) == 15.0  # no samples -> the absolute cap


def _floored_speed(n=24 * 30, floor=34.5, seed=3):
    rng = np.random.default_rng(seed)
    s = 30 + 70 * rng.random(n)
    s[rng.random(n) < 0.3] = floor  # the drive parks at its minimum ~30 % of the time
    return np.maximum(s, floor)


def test_speed_gate_comes_from_the_learned_vfd_floor():
    g = G.speed_gate(_floored_speed(), None)
    assert (g.source, g.vfd_floor) == ("learned_vfd_floor", 34.5)
    assert g.min_load == pytest.approx(34.5 + G.SPEED_FLOOR_TOL_PCT)
    smooth = np.linspace(20, 100, 720)  # no plateau -> the old 15 %
    assert G.speed_gate(smooth, None) == G.Gates(15.0, 10.0, "default")


def test_the_default_pumpsim_pump_learns_no_floor_so_full_size_keeps_15_pct():
    c = simulate_case(None, 0, seed=4)
    assert G.speed_gate(c.baseline[Role.CHW_PUMP_SPEED], None).source == "default"


def test_pump_flow_excludes_samples_parked_at_the_vfd_floor():
    n = 24 * 30
    idx = pd.date_range("2025-01-01", periods=n, freq="1h")
    spd = _floored_speed(n)
    rng = np.random.default_rng(9)
    flow = 4.0 * spd + rng.normal(0, 2, n)
    flow[spd == 34.5] = rng.uniform(40, 140, int((spd == 34.5).sum()))  # valves set it, not speed
    frame = pd.DataFrame({Role.CHW_FLOW: flow, Role.CHW_PUMP_SPEED: spd}, index=idx)
    f = PumpFlowDrift(BaselineStore()).analyze_periods("P", frame, frame)
    assert f.metrics["pump_flow_speed_gate_source"] == "learned_vfd_floor"
    assert f.metrics["pump_flow_speed_gate_pct"] == pytest.approx(36.5)
    assert f.metrics["pump_flow_n_current"] == int((spd >= 36.5).sum())


def test_loop_deltat_uses_the_speed_gate_when_speed_stands_in_for_flow():
    c = simulate_case(None, 0, seed=8, design_flow_gpm=40.0)
    rule = LoopDeltaTDrift(BaselineStore(), load_role=Role.CHW_PUMP_SPEED)
    f = rule.analyze_periods(c.equip, c.baseline, c.current)
    assert f.severity == "ok"
    assert f.metrics["loop_deltat_load_gate"] == 15.0  # not 50 % speed
    assert f.metrics["loop_deltat_gate_source"] == "default"
    floored = c.baseline.assign(**{Role.CHW_PUMP_SPEED: _floored_speed(len(c.baseline))})
    g = rule.analyze_periods(c.equip, floored, floored)
    assert g.metrics["loop_deltat_gate_source"] == "learned_vfd_floor"


# --------------------------------------------------------------------------- small pumps


@pytest.mark.parametrize("rule_cls", [LoopDeltaTDrift, LoopDPDrift, PumpPowerDrift])
def test_a_40_gpm_pump_is_scored_not_declined(rule_cls):
    c = simulate_case(None, 0, seed=12, design_flow_gpm=40.0)
    f = _run(rule_cls, c)
    assert f.severity == "ok" and not f.metrics.get("declined")
    assert any(k.endswith("_gate_source") and v == "size_relative" for k, v in f.metrics.items())


def test_the_old_absolute_gates_still_decline_a_small_pump_when_asked_for():
    c = simulate_case(None, 0, seed=12, design_flow_gpm=40.0)
    assert _run(LoopDPDrift, c, min_load=50.0).metrics.get("declined")
    wear = simulate_case("impeller_wear", 3, seed=12, design_flow_gpm=40.0)
    assert _run(PumpFlowDrift, wear).severity in _FIRED
    assert _run(PumpFlowDrift, wear, warn_gpm=15.0, fault_gpm=30.0).severity == "ok"
    drag = simulate_case("bearing_drag", 3, seed=12, design_flow_gpm=40.0)
    assert _run(PumpPowerDrift, drag).severity in _FIRED
    assert _run(PumpPowerDrift, drag, warn_kw=1.0, fault_kw=2.0).severity == "ok"


# --------------------------------------------------------------------------- the size sweep

_SIZES = (20.0, 40.0, 100.0, DESIGN_FLOW_GPM)


@pytest.mark.parametrize("size", _SIZES)
def test_every_fault_localizes_at_every_size(size):
    for name, spec in FAULTS.items():
        c = simulate_case(name, 3, seed=42, design_flow_gpm=size)
        d = diagnose_pump_frames(c.baseline, c.current, equip=c.equip)
        assert d.locus == spec.expected_locus, (size, name, d.locus)


@pytest.mark.parametrize("size", _SIZES[:-1])
def test_small_pumps_detect_what_full_size_detects_without_false_alarms(size):
    def fired(cases):
        out = []
        for c in cases:
            suite = build_pump_suite(BaselineStore())
            out.append(
                {r.name: r.analyze_periods(c.equip, c.baseline, c.current).severity for r in suite}
            )
        return out

    kw = {"seed0": 77, "severities": (2, 4), "n_healthy": 3, "n_per_fault": 1}
    full = fired(make_cases(**kw))
    small = fired(make_cases(design_flow_gpm=size, **kw))
    cases = make_cases(**kw)
    for case, f, s in zip(cases, full, small):
        if not case.is_fault or case.expected_locus == "steady":
            assert not any(v in _FIRED for v in s.values()), (size, case.fault_name, s)
        for rule, sev in f.items():
            if sev in _FIRED:
                assert s[rule] in _FIRED, (size, case.fault_name, case.severity, rule)


# --------------------------------------------------------------------------- constant speed


@pytest.mark.parametrize("speed_point", [True, False])
def test_a_constant_speed_pump_compares_flat_levels_with_a_caveat(speed_point):
    kw = {"fixed_speed": 90.0, "speed_point": speed_point, "design_flow_gpm": 60.0}
    healthy = simulate_case(None, 0, seed=21, **kw)
    wear = simulate_case("impeller_wear", 3, seed=21, **kw)
    mode = "constant" if speed_point else "status_only"
    for rule_cls, prefix in ((PumpFlowDrift, "pump_flow"), (PumpHeadDrift, "pump_head")):
        ok = _run(rule_cls, healthy)
        assert ok.severity == "ok", (rule_cls, ok.summary)
        assert ok.metrics[f"{prefix}_speed_mode"] == mode
        assert ok.metrics[f"{prefix}_baseline_model"] == "level"
        assert any("constant-speed pump" in c and "balancing valve" in c for c in ok.caveats)
        bad = _run(rule_cls, wear)
        assert bad.severity in _FIRED, (rule_cls, bad.summary)
    d = diagnose_pump_frames(wear.baseline, wear.current, equip=wear.equip)
    assert d.locus == "pump"


def test_a_constant_flow_loop_scores_a_flat_level():
    c = simulate_case(None, 0, seed=30, fixed_speed=90.0, design_flow_gpm=200.0)
    f = _run(LoopDeltaTDrift, c)
    assert f.severity == "ok"
    assert f.metrics["loop_deltat_normalizer_mode"] == "constant"
    assert f.metrics["loop_deltat_baseline_model"] == "level"
    assert any("flat level valid for" in cav for cav in f.caveats)


def test_status_only_needs_a_status_and_a_speedless_pump_without_one_still_declines():
    c = simulate_case(None, 0, seed=31, fixed_speed=90.0, speed_point=False)
    bare = c.baseline.drop(columns=[Role.PUMP_STATUS])
    f = PumpFlowDrift(BaselineStore()).analyze_periods("P", bare, bare)
    assert f.metrics["declined"] and f.metrics["reason"] == "flow_or_speed_not_mapped"
    rule = PumpFlowDrift(BaselineStore())
    assert rule.roles_any_of == ((Role.CHW_PUMP_SPEED, Role.PUMP_STATUS),)


def test_a_variable_speed_pump_is_not_treated_as_constant():
    c = simulate_case(None, 0, seed=32)
    f = _run(PumpFlowDrift, c)
    assert f.metrics["pump_flow_speed_mode"] == "variable"
    assert not any("constant-speed" in cav for cav in f.caveats)


# --------------------------------------------------------------------------- pumpsim


def test_pumpsim_scales_flow_and_power_but_not_pressure():
    big = simulate_case(None, 0, seed=5)
    small = simulate_case(None, 0, seed=5, design_flow_gpm=40.0)
    k = 40.0 / DESIGN_FLOW_GPM
    np.testing.assert_allclose(small.baseline[Role.CHW_FLOW], big.baseline[Role.CHW_FLOW] * k)
    for role in (Role.PUMP_HEAD, Role.CHW_DIFF_PRESS, Role.CHW_RETURN_TEMP):
        np.testing.assert_array_equal(small.baseline[role], big.baseline[role])
    fixed = simulate_case(None, 0, seed=5, fixed_speed=90.0, speed_point=False)
    assert Role.CHW_PUMP_SPEED not in fixed.baseline.columns
    assert (fixed.baseline[Role.PUMP_STATUS] == 1.0).all()


def _r6(v):
    if isinstance(v, bool) or v is None or isinstance(v, str):
        return v
    if isinstance(v, (int, float)):
        return None if (isinstance(v, float) and math.isnan(v)) else round(float(v), 6)
    return str(v)


def test_full_size_results_are_unchanged():
    """Every metric, severity, summary and caveat 0.103 produced at full size is unchanged."""
    with open(_GOLDEN, encoding="utf-8") as fh:
        golden = json.load(fh)["cases"]
    cases = make_cases(seed0=0, severities=(2, 4), n_healthy=4, n_per_fault=1)
    assert len(cases) == len(golden)
    for c, g in zip(cases, golden):
        for name, fr in (("baseline", c.baseline), ("current", c.current)):
            sums = {str(getattr(col, "value", col)): round(float(fr[col].sum()), 6) for col in fr}
            assert sums == pytest.approx(g["frames"][name], rel=1e-9, abs=1e-6)
        suite = build_pump_suite(BaselineStore())
        for rule, want in zip(suite, g["findings"]):
            f = rule.analyze_periods(c.equip, c.baseline, c.current)
            assert (f.rule, f.severity, f.summary) == (
                want["rule"],
                want["severity"],
                want["summary"],
            )
            assert list(f.caveats) == want["caveats"]
            for key, val in want["metrics"].items():
                # pytest 9.2 refuses approx() of text, so only numbers get a tolerance
                numeric = isinstance(val, (int, float)) and not isinstance(val, bool)
                expected = pytest.approx(val, rel=1e-6, abs=1e-6) if numeric else val
                assert _r6(f.metrics[key]) == expected, (g["case"], key)
