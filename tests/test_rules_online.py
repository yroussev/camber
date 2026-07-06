"""Tests for the sliding-window online FDD evaluator (camber.rules.online)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.rules.base import Finding  # noqa: E402
from camber.rules.online import OnlineFDD, Transition  # noqa: E402


class _ThresholdRule:
    """A tiny rule: fault when the window-mean of SUPPLY_AIR_TEMP exceeds `hi`."""
    name = "sat_high"
    roles_required = (Role.SUPPLY_AIR_TEMP,)
    roles_optional = ()

    def __init__(self, hi=70.0):
        self.hi = hi

    def analyze(self, equip, frame):
        m = float(frame[Role.SUPPLY_AIR_TEMP].mean())
        sev = "fault" if m > self.hi else "ok"
        return Finding(rule=self.name, equip=equip, severity=sev, metrics={"mean": m}, summary="")


def _rows(vals):
    idx = pd.date_range("2026-07-01", periods=len(vals), freq="1h")
    return idx, [{Role.SUPPLY_AIR_TEMP: v} for v in vals]


def test_emits_transition_on_first_fault_only():
    fdd = OnlineFDD([_ThresholdRule(hi=70)], window=24, eval_every=1, min_samples=3)
    idx, rows = _rows([60, 60, 60, 90, 90, 90])       # cold -> then rises
    trans = []
    for ts, r in zip(idx, rows):
        trans.extend(fdd.push("AHU-1", r, ts=ts))
    faults = [t for t in trans if t.to_severity == "fault"]
    assert len(faults) == 1                            # only the crossing into fault, not each sample
    assert isinstance(faults[0], Transition) and faults[0].from_severity == "ok"


def test_no_transition_while_state_unchanged():
    fdd = OnlineFDD([_ThresholdRule(hi=70)], window=24, eval_every=1, min_samples=3)
    idx, rows = _rows([90, 90, 90, 90, 90])           # fault throughout after warmup
    emitted = []
    for ts, r in zip(idx, rows):
        emitted.extend(fdd.push("AHU-1", r, ts=ts))
    assert sum(1 for t in emitted if t.to_severity == "fault") == 1   # single alert, no spam


def test_recovery_emitted_only_when_emit_ok():
    # window=3 so the high samples age out of the trailing window and the mean recovers
    idx, rows = _rows([90, 90, 90, 50, 50, 50])
    quiet = OnlineFDD([_ThresholdRule(hi=70)], window=3, eval_every=1, min_samples=3)
    q = [t for ts, r in zip(idx, rows) for t in quiet.push("A", r, ts=ts)]
    assert all(t.to_severity == "fault" for t in q)   # recovery to ok suppressed by default

    loud = OnlineFDD([_ThresholdRule(hi=70)], window=3, eval_every=1, min_samples=3, emit_ok=True)
    ev = [t for ts, r in zip(idx, rows) for t in loud.push("A", r, ts=ts)]
    assert any(t.to_severity == "ok" and t.from_severity == "fault" for t in ev)


def test_min_samples_gates_evaluation():
    fdd = OnlineFDD([_ThresholdRule(hi=70)], eval_every=1, min_samples=5)
    idx, rows = _rows([90, 90])                        # below min_samples
    out = [t for ts, r in zip(idx, rows) for t in fdd.push("A", r, ts=ts)]
    assert out == [] and fdd.window_frame("A").shape[0] == 2


def test_extend_and_window_bound():
    fdd = OnlineFDD([_ThresholdRule(hi=70)], window=10, eval_every=100, min_samples=3)
    idx, rows = _rows(list(np.full(30, 60.0)))
    fdd.extend("A", pd.DataFrame([r for r in rows], index=idx))
    assert fdd.window_frame("A").shape[0] == 10        # deque bounded to window


def test_rule_skipped_when_required_role_absent():
    fdd = OnlineFDD([_ThresholdRule(hi=70)], eval_every=1, min_samples=2)
    out = fdd.push("A", {Role.OAT: 80.0})              # no SUPPLY_AIR_TEMP
    out += fdd.push("A", {Role.OAT: 82.0})
    assert out == [] and fdd.state() == {}             # rule never fired (role missing)


def test_per_equipment_isolation():
    fdd = OnlineFDD([_ThresholdRule(hi=70)], eval_every=1, min_samples=3)
    idx, rows = _rows([90, 90, 90])
    a = [t for ts, r in zip(idx, rows) for t in fdd.push("AHU-1", r, ts=ts)]
    b = [t for ts, r in zip(idx, rows) for t in fdd.push("AHU-2", r, ts=ts)]
    assert len(a) == 1 and len(b) == 1                 # independent state per equip
    assert {k[0] for k in fdd.state()} == {"AHU-1", "AHU-2"}
