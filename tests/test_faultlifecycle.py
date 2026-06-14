"""Tests for the persistent fault lifecycle (camber.faultlifecycle)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.faultlifecycle import FaultLifecycle, FaultRecord, OPEN_STATUSES  # noqa: E402
from camber.rules.base import Finding  # noqa: E402


def _f(rule, equip, sev):
    return Finding(rule=rule, equip=equip, severity=sev, metrics={}, summary="")


_RUN1 = [_f("simultaneous_heat_cool", "AHU-1", "fault"),
         _f("chiller_efficiency", "CH-1", "warn"),
         _f("co2_ventilation", "VAV-3", "info")]      # info is non-actionable


def test_update_creates_open_records_for_actionable():
    lc = FaultLifecycle()
    res = lc.update(_RUN1, run_id="2026-01-01T00:00", site="S1")
    assert len(res["new"]) == 2 and not res["ongoing"]    # info dropped
    assert {r.status for r in lc.open_faults()} == {"open"}
    assert all(r.occurrences == 1 for r in lc.records())


def test_ongoing_bumps_occurrences_and_absent_listed():
    lc = FaultLifecycle()
    lc.update(_RUN1, run_id="2026-01-01T00:00", site="S1")
    # next run: chiller persists, simultaneous gone
    res = lc.update([_f("chiller_efficiency", "CH-1", "warn")],
                    run_id="2026-01-02T00:00", site="S1")
    assert len(res["ongoing"]) == 1 and len(res["absent"]) == 1
    ch = [r for r in lc.records() if r.rule == "chiller_efficiency"][0]
    assert ch.occurrences == 2 and ch.last_seen == "2026-01-02T00:00"


def test_auto_resolve_absent():
    lc = FaultLifecycle()
    lc.update(_RUN1, run_id="r1", site="S1")
    res = lc.update([], run_id="r2", site="S1", auto_resolve_absent=True)
    assert len(res["resolved"]) == 2 and lc.open_faults() == []


def test_workflow_assign_ack_start_resolve():
    lc = FaultLifecycle()
    lc.update([_f("simultaneous_heat_cool", "AHU-1", "fault")], run_id="r1", site="S1")
    fp = lc.records()[0].fingerprint
    lc.assign(fp, "alice")
    lc.acknowledge(fp, "2026-01-01T06:00")
    lc.start(fp)
    assert lc.get(fp).assignee == "alice" and lc.get(fp).status == "in_progress"
    lc.resolve(fp, "2026-01-01T10:00", note="replaced actuator")
    r = lc.get(fp)
    assert r.status == "resolved" and r.resolved_at == "2026-01-01T10:00"
    assert any("replaced actuator" in n for n in r.notes)
    assert lc.open_faults() == []


def test_reopen_on_recurrence():
    lc = FaultLifecycle()
    lc.update([_f("simultaneous_heat_cool", "AHU-1", "fault")], run_id="r1", site="S1")
    fp = lc.records()[0].fingerprint
    lc.resolve(fp, "r2")
    res = lc.update([_f("simultaneous_heat_cool", "AHU-1", "fault")], run_id="r3", site="S1")
    assert fp in res["reopened"] and lc.get(fp).status == "open"


def test_aging_and_overdue_by_sla():
    lc = FaultLifecycle()
    lc.update(_RUN1, run_id="2026-01-01T00:00", site="S1")
    now = "2026-01-02T00:00"                                # 24h later
    ages = lc.aging(now)
    assert all(abs(h - 24.0) < 0.01 for h in ages.values())
    # fault must be acked within 4h, resolved within 48h; warn resolved within 168h
    overdue = lc.overdue(now, ack_sla_hours={"fault": 4, "warn": 12},
                         resolve_sla_hours={"fault": 48, "warn": 168})
    kinds = {(r.rule, kind) for r, kind, _age, _sla in overdue}
    assert ("simultaneous_heat_cool", "ack") in kinds      # fault unacked > 4h
    assert ("chiller_efficiency", "ack") in kinds          # warn unacked > 12h
    assert all(k[1] != "resolve" for k in kinds)           # nothing past the resolve SLA yet


def test_persistence_round_trip(tmp_path):
    path = str(tmp_path / "faults.json")
    lc = FaultLifecycle.load(path)                          # empty (file absent)
    lc.update(_RUN1, run_id="r1", site="S1")
    fp = lc.records()[0].fingerprint
    lc.assign(fp, "bob")
    lc.save()
    # reload in a fresh instance — state survives
    lc2 = FaultLifecycle.load(path)
    assert len(lc2.records()) == 2
    assert lc2.get(fp).assignee == "bob"
    assert isinstance(lc2.records()[0], FaultRecord)


def test_summary_counts():
    lc = FaultLifecycle()
    lc.update(_RUN1, run_id="r1", site="S1")
    fp = lc.records()[0].fingerprint
    lc.suppress(fp)
    s = lc.summary()
    assert s["total"] == 2 and s["by_status"]["suppressed"] == 1
    assert s["open"] == 1 and sum(s["open_by_severity"].values()) == 1


def test_unknown_fingerprint_raises():
    lc = FaultLifecycle()
    try:
        lc.resolve("deadbeef", "r1")
        assert False
    except KeyError:
        pass
