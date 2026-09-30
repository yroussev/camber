"""Tests for the persistent fault lifecycle (camber.faultlifecycle)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.faultlifecycle import FaultLifecycle, FaultRecord  # noqa: E402
from camber.rules.base import Finding  # noqa: E402


def _f(rule, equip, sev):
    return Finding(rule=rule, equip=equip, severity=sev, metrics={}, summary="")


_RUN1 = [
    _f("simultaneous_heat_cool", "AHU-1", "fault"),
    _f("chiller_efficiency", "CH-1", "warn"),
    _f("co2_ventilation", "VAV-3", "info"),
]  # info is non-actionable


def test_update_creates_open_records_for_actionable():
    lc = FaultLifecycle()
    res = lc.update(_RUN1, run_id="2026-01-01T00:00", site="S1")
    assert len(res["new"]) == 2 and not res["ongoing"]  # info dropped
    assert {r.status for r in lc.open_faults()} == {"open"}
    assert all(r.occurrences == 1 for r in lc.records())


def test_ongoing_bumps_occurrences_and_absent_listed():
    lc = FaultLifecycle()
    lc.update(_RUN1, run_id="2026-01-01T00:00", site="S1")
    # next run: chiller persists, simultaneous gone
    res = lc.update(
        [_f("chiller_efficiency", "CH-1", "warn")], run_id="2026-01-02T00:00", site="S1"
    )
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
    now = "2026-01-02T00:00"  # 24h later
    ages = lc.aging(now)
    assert all(abs(h - 24.0) < 0.01 for h in ages.values())
    # fault must be acked within 4h, resolved within 48h; warn resolved within 168h
    overdue = lc.overdue(
        now, ack_sla_hours={"fault": 4, "warn": 12}, resolve_sla_hours={"fault": 48, "warn": 168}
    )
    kinds = {(r.rule, kind) for r, kind, _age, _sla in overdue}
    assert ("simultaneous_heat_cool", "ack") in kinds  # fault unacked > 4h
    assert ("chiller_efficiency", "ack") in kinds  # warn unacked > 12h
    assert all(k[1] != "resolve" for k in kinds)  # nothing past the resolve SLA yet


def test_persistence_round_trip(tmp_path):
    path = str(tmp_path / "faults.json")
    lc = FaultLifecycle.load(path)  # empty (file absent)
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
        raise AssertionError("expected KeyError")
    except KeyError:
        pass


# --------------------------------------------------------------------------- #76: run scope
# In the legacy, site-keyed path (no facility_id) a run only reports -- and auto-resolves -- the
# faults keyed by its own ``site``; other sites' and facility-keyed records in the same file are
# left alone, and a record whose site can't be told is reported under ``unscoped``, never resolved.

import json  # noqa: E402
import warnings  # noqa: E402

import pytest  # noqa: E402

from camber.integrate.tickets import fingerprint  # noqa: E402
from camber.rules.triage import FaultRegister  # noqa: E402


def _leak(equip):
    return _f("leaking_valve", equip, "warn")


def _two_sites():
    lc = FaultLifecycle()
    lc.update([_leak("AHU-1")], run_id="r1", site="site-x")
    lc.update([_leak("AHU-9")], run_id="r1", site="site-y")
    return (
        lc,
        fingerprint("site-x", "AHU-1", "leaking_valve"),
        fingerprint("site-y", "AHU-9", "leaking_valve"),
    )


def test_auto_resolve_leaves_other_sites_faults_open_issue_76():
    lc, _x, y = _two_sites()
    out = lc.update([_leak("AHU-1")], run_id="r2", site="site-x", auto_resolve_absent=True)
    assert out["resolved"] == [] and out["absent"] == [] and "unscoped" not in out
    assert lc.get(y).status == "open" and lc.get(y).notes == []


def test_absent_is_scoped_to_the_run_site_without_auto_resolve():
    lc, x, y = _two_sites()
    lc.update([_leak("AHU-2")], run_id="r1", site="site-x")
    x2 = fingerprint("site-x", "AHU-2", "leaking_valve")
    out = lc.update([_leak("AHU-1")], run_id="r2", site="site-x")
    assert out["absent"] == [x2] and y not in out["absent"]
    out = lc.update([], run_id="r3", site="site-x", auto_resolve_absent=True)
    assert out["resolved"] == sorted([x, x2])
    assert [r.fingerprint for r in lc.open_faults()] == [y]


def test_empty_site_is_its_own_scope():
    lc, x, y = _two_sites()
    lc.update([_leak("AHU-5")], run_id="r1", site="")
    e = fingerprint("", "AHU-5", "leaking_valve")
    out = lc.update([], run_id="r2", site="", auto_resolve_absent=True)
    assert out["resolved"] == [e] and "unscoped" not in out
    assert lc.get(x).status == lc.get(y).status == "open"
    # and a named site's run never touches the "" record's siblings
    lc.update([_leak("AHU-5")], run_id="r3", site="")
    assert lc.update([], run_id="r4", site="site-y")["absent"] == [y]


def test_mixed_file_leaves_facility_keyed_records_to_their_facility(tmp_path):
    lc, x, y = _two_sites()
    lc.update([_leak("AHU-7")], run_id="r1", site="Facility One", facility_id="fac-1")
    f1 = fingerprint("fac-1", "AHU-7", "leaking_valve")
    p = str(tmp_path / "faults.json")
    lc.save(p)
    unbound = FaultLifecycle.load(p)
    out = unbound.update([], run_id="r2", site="site-x", auto_resolve_absent=True)
    assert out["resolved"] == [x] and "unscoped" not in out
    assert unbound.get(f1).status == "open" and unbound.get(y).status == "open"
    # the facility-bound run still owns exactly its own records (unchanged since 0.86)
    bound = FaultLifecycle.load(p, facility_id="fac-1", legacy_sites=())
    out = bound.update([], run_id="r2", site="Facility One", auto_resolve_absent=True)
    assert out == {"new": [], "ongoing": [], "reopened": [], "absent": [], "resolved": [f1]}
    assert bound.get(x).status == "open" and bound.get(y).status == "open"


def test_aliased_record_is_scoped_by_its_current_key():
    """A merged/adopted record keeps the old site-keyed fingerprint as an alias; that alias does
    not make it the unbound site-x run's fault (the run can never 'see' it by the alias)."""
    lc = FaultLifecycle()
    lc.update([_leak("AHU-1")], run_id="r1", site="site-x")
    with pytest.warns(DeprecationWarning):
        lc._adopt_legacy("fac-1", ("site-x",))
    old = fingerprint("site-x", "AHU-1", "leaking_valve")
    rec = lc.get(old)
    assert rec.facility_id == "fac-1" and rec.aliases == [old]
    out = lc.update([], run_id="r2", site="site-x", auto_resolve_absent=True)
    assert out["resolved"] == [] and "unscoped" not in out and rec.status == "open"


def test_relabelled_record_follows_its_fingerprint():
    """The fingerprint is the identity; a hand-edited ``site`` label doesn't move the fault."""
    lc, x, _y = _two_sites()
    lc.get(x).site = "Site X (renamed label)"
    out = lc.update([], run_id="r2", site="site-x", auto_resolve_absent=True)
    assert out["resolved"] == [x]


def _write(path, recs):
    open(path, "w").write(json.dumps({"faults": recs}))


def test_record_without_a_site_is_reported_never_resolved(tmp_path):
    """A record with no stored ``site`` (or one whose fingerprint matches no label) can't be
    placed: every unbound run lists it under ``unscoped`` and none auto-resolves it."""
    p = str(tmp_path / "faults.json")
    fx = fingerprint("site-x", "AHU-1", "leaking_valve")
    fq = fingerprint("somewhere", "AHU-3", "leaking_valve")
    base = {"rule": "leaking_valve", "severity": "warn", "status": "open", "first_seen": "r0",
            "last_seen": "r0", "occurrences": 1}  # fmt: skip
    _write(p, [
        {**base, "fingerprint": fx, "site": "site-x", "equip": "AHU-1"},
        {**base, "fingerprint": fq, "equip": "AHU-3"},  # no "site" key at all
        {**base, "fingerprint": "0123456789ab", "site": "site-x", "equip": "AHU-4"},  # edited
    ])  # fmt: skip
    lc = FaultLifecycle.load(p)
    assert lc.get(fq).site == ""
    for site in ("site-x", "", "site-z"):
        out = lc.update([], run_id="r1", site=site, auto_resolve_absent=True)
        assert out["unscoped"] == sorted([fq, "0123456789ab"])
    assert lc.get(fx).status == "resolved"  # resolved by the site-x run only
    assert lc.get(fq).status == lc.get("0123456789ab").status == "open"
    assert lc.update([], run_id="r2", site="site-x")["unscoped"] == sorted([fq, "0123456789ab"])
    lc.resolve(fq, "r3")  # a hand resolve clears it from the report
    assert lc.update([], run_id="r4", site="site-x")["unscoped"] == ["0123456789ab"]


def test_siteless_record_keyed_by_empty_site_is_in_the_empty_scope(tmp_path):
    p = str(tmp_path / "faults.json")
    fe = fingerprint("", "AHU-1", "leaking_valve")
    _write(p, [{"fingerprint": fe, "equip": "AHU-1", "rule": "leaking_valve",
                "severity": "warn", "status": "open"}])  # fmt: skip
    lc = FaultLifecycle.load(p)
    assert lc.update([], run_id="r1", site="site-x", auto_resolve_absent=True)["resolved"] == []
    assert lc.update([], run_id="r1", site="", auto_resolve_absent=True)["resolved"] == [fe]


def test_unscoped_is_absent_from_bound_runs(tmp_path):
    p = str(tmp_path / "faults.json")
    _write(p, [{"fingerprint": "0123456789ab", "equip": "AHU-4", "rule": "leaking_valve",
                "severity": "warn", "status": "open"}])  # fmt: skip
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # nothing to adopt, so no deprecation warning either
        lc = FaultLifecycle.load(p, facility_id="fac-1", legacy_sites=())
        out = lc.update([], run_id="r1", site="site-x", auto_resolve_absent=True)
    assert "unscoped" not in out and lc.get("0123456789ab").status == "open"


def test_config_runs_sharing_a_store_keep_other_sites_open(tmp_path):
    """The config ``faults`` section (unbound outside a workspace) shares the fix."""
    import _rcx_fixture as rfx

    from camber.config import run_config

    rfx.make_store(tmp_path)
    store = str(tmp_path / "faults.json")
    lc = FaultLifecycle.load(store)
    lc.update([_leak("AHU-9")], run_id="r0", site="site-y")
    lc.save()
    y = fingerprint("site-y", "AHU-9", "leaking_valve")
    cfg = rfx.config()
    cfg["faults"] = {"store": "faults.json", "run_id": "r1", "auto_resolve_absent": True}
    res = run_config(cfg, base_dir=str(tmp_path))
    assert y not in res.faults["resolved"] and "unscoped" not in res.faults
    assert FaultLifecycle.load(store).get(y).status == "open"


def test_fault_register_resolves_only_its_own_key():
    reg = FaultRegister()
    reg.update([_leak("AHU-1")], site="site-x", run_id=1)
    reg.update([_leak("AHU-9")], site="site-y", run_id=1)
    reg.update([_leak("AHU-7")], site="site-x", facility_id="fac-1", run_id=1)
    out = reg.update([], site="site-x", run_id=2)
    assert out["resolved"] == [fingerprint("site-x", "AHU-1", "leaking_valve")]
    assert set(reg.open_faults()) == {
        fingerprint("site-y", "AHU-9", "leaking_valve"),
        fingerprint("fac-1", "AHU-7", "leaking_valve"),
    }
    out = reg.update([], site="site-x", facility_id="fac-1", run_id=3)
    assert out["resolved"] == [fingerprint("fac-1", "AHU-7", "leaking_valve")]


def test_cli_run_prints_unscoped_count(tmp_path, capsys):
    import _rcx_fixture as rfx

    from camber.cli import main

    rfx.make_store(tmp_path)
    _write(str(tmp_path / "faults.json"), [
        {"fingerprint": "0123456789ab", "equip": "AHU-4", "rule": "leaking_valve",
         "severity": "warn", "status": "open"},
    ])  # fmt: skip
    cfg = rfx.config()
    cfg["faults"] = {"store": "faults.json", "run_id": "r1", "auto_resolve_absent": True}
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps(cfg))
    assert main(["run", str(path)]) == 0
    assert "faults: 1 open fault(s) of unknown site left untouched" in capsys.readouterr().out
    assert FaultLifecycle.load(str(tmp_path / "faults.json")).get("0123456789ab").status == "open"
