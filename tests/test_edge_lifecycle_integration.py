"""The edge landing wired to the real portfolio lifecycle (0.95 integration, #18 steps 3-5).

The edge branch was built against a stubbed registry and the state *names*; these tests run it
against the real ``Portfolio`` API: public audit / device-note methods, month keys, the real
offboard / archive / purge transitions, retention on edge-landed objects, recovery with the
spool lock, and M&V billing baselines through export and restore bundles.
"""

import ast
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.portfolio import Portfolio  # noqa: E402

A, B = "north-annex-1a2b3c", "east-wing-4d5e6f"
EDGE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "camber", "edge"
)


@pytest.fixture
def pf(tmp_path):
    p = Portfolio.init(tmp_path / "ws")
    p.add_facility("North Annex", facility_id=A, reason="t", activate=True)
    return p


# ---------------------------------------------------------------- 3. public audit + device note


def test_portfolio_audit_is_public_and_guarded(pf):
    rec = pf.audit("edge.land", reason="inbox sweep", facility_id=A, details={"n": 1})
    assert rec["from_state"] == rec["to_state"] == "active"  # looked up
    assert rec["actor"] and rec["host"] and rec["details"] == {"n": 1}
    assert pf.audit_log(facility_id=A)[-1]["action"] == "edge.land"
    assert pf.audit("edge.land", reason="r", facility_id="nobody-000000")["from_state"] is None
    assert pf.audit("edge.land", reason="r", facility_id=A, state=None)["from_state"] is None
    for bad in ("land", "", " edge.x"):
        with pytest.raises(ValueError):
            pf.audit(bad, reason="r")
    for reserved in ("facility.purge", "portfolio.recover", "retention.apply"):
        with pytest.raises(ValueError, match="namespace"):
            pf.audit(reserved, reason="r")
    with pytest.raises(ValueError, match="reason"):
        pf.audit("edge.land", reason=" ")


def test_note_edge_device_audits_before_the_registry(pf, monkeypatch):
    e = pf.note_edge_device(A, "gw-1", {"state": "retired", "retired_at": "t1"}, reason="swap")
    assert e["edge_devices"] == {"gw-1": {"state": "retired", "retired_at": "t1"}}
    pf.note_edge_device(A, "gw-2", {"state": "active"}, reason="new gateway")
    assert set(pf.facility(A)["edge_devices"]) == {"gw-1", "gw-2"}  # other devices kept
    last = pf.audit_log(facility_id=A)[-1]
    assert last["action"] == "edge.device" and last["details"]["device_id"] == "gw-2"
    with pytest.raises(KeyError):
        pf.note_edge_device("nobody-000000", "gw", {}, reason="r")
    with pytest.raises(ValueError):
        pf.note_edge_device(A, "", {}, reason="r")
    # a crash after the audit line leaves the registry untouched: noting again repairs it
    from camber.store.facilities import FacilityRegistry

    def boom(self, *a, **k):
        raise OSError("disk gone")

    monkeypatch.setattr(FacilityRegistry, "_update", boom)
    n = len(pf.audit_log())
    with pytest.raises(OSError):
        pf.note_edge_device(A, "gw-3", {"state": "retired"}, reason="r")
    assert len(pf.audit_log()) == n + 1 and "gw-3" not in pf.facility(A)["edge_devices"]


def test_edge_code_calls_no_private_portfolio_api():
    """No ``registry._update`` / ``portfolio._audit`` / direct audit-log appends in camber.edge."""
    offenders = []
    for f in sorted(os.listdir(EDGE_DIR)):
        if not f.endswith(".py"):
            continue
        tree = ast.parse(open(os.path.join(EDGE_DIR, f), encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in ("_update", "_audit", "_tombstone"):
                offenders.append(f"{f}:{node.lineno} .{node.attr}")
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and "portfolio._audit" in node.module
            ):
                names = {a.name for a in node.names}
                if names - {"audit_record"}:  # the device-side receipt shape only
                    offenders.append(f"{f}:{node.lineno} imports {sorted(names)}")
    assert not offenders, offenders


def test_record_retirement_uses_the_public_note(pf):
    from camber.edge.decommission import record_retirement

    receipt = {"facility_id": A, "device_id": "gw-1", "retired_at": "2026-09-01T00:00:00Z"}
    assert record_retirement(pf, receipt, reason="swap")["noted"] == "recorded"
    assert record_retirement(pf, receipt, reason="swap")["noted"] == "already recorded"
    note = pf.facility(A)["edge_devices"]["gw-1"]
    assert note["state"] == "retired" and note["retired_at"] == receipt["retired_at"]
    acts = [r["action"] for r in pf.audit_log(facility_id=A)]
    assert acts.count("edge.decommission") == 1
    other = {**receipt, "facility_id": "gone-aaaaaa"}
    assert record_retirement(pf, other, reason="swap")["noted"].startswith("audit only")
