"""The session's fact ledger and where check_citations takes its facts from (#294, part 1).

A fact supports a sentence only when this server issued it to this session: ``Registry.call``
records every result's facts in ``ctx.ledger``, ``check_citations`` resolves the cited ids from
it, and a fact a caller passes must equal the issued one. Without a ledger (direct Python use)
the caller's facts are taken as given, and the result says the provenance is unverified.
"""

import os
import pickle
import sys
from dataclasses import replace

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp"))

import _ledger_registry as fake

from camber.capabilities import (
    CallContext,
    CapabilityError,
    FactLedger,
    default_registry,
)
from camber.capabilities._ledger import FORGOTTEN, ISSUED, UNKNOWN
from camber.model.roles import Role
from camber.store import ParquetStore

REG = fake.default_registry()


def _ctx(tmp_path, ledger=None, allowed=()):
    return CallContext(store=str(tmp_path), allowed_facilities=frozenset(allowed), ledger=ledger)


def _check(ctx, text, **kw):
    return REG.call("check_citations", {"text": text, "detail": True, **kw}, ctx)


# ------------------------------------------------------------------ the ledger itself


def test_ledger_records_looks_up_and_forgets_the_oldest():
    led = FactLedger(max_bytes=400, max_forgotten=2)
    led.record([{"id": f"F{i}@x", "text": "t" * 80, "value": i} for i in range(1, 6)])
    # 5 facts of ~105 bytes over a 400-byte budget: the oldest went first
    assert led.lookup("F5@x") == (ISSUED, {"id": "F5@x", "text": "t" * 80, "value": 5})
    assert led.lookup("F1@x")[0] in (FORGOTTEN, UNKNOWN)
    assert led.lookup("never@x") == (UNKNOWN, None)
    led.record([{"id": f"G{i}@x", "text": "u" * 80} for i in range(1, 6)])
    # only the 2 most recently forgotten ids are still known as forgotten
    states = [led.lookup(f"F{i}@x")[0] for i in range(1, 6)]
    assert states.count(FORGOTTEN) <= 2 and UNKNOWN in states
    # an id issued again is held again
    led.record([{"id": "F1@x", "text": "again"}])
    assert led.lookup("F1@x") == (ISSUED, {"id": "F1@x", "text": "again"})


def test_ledger_view_holds_only_what_a_call_names_and_pickles():
    led = FactLedger(max_bytes=200)
    led.record([{"id": "A1@x", "text": "a" * 100}, {"id": "B1@x", "text": "b"}])
    led.record([{"id": "C1@x", "text": "c" * 100}])  # A1 forgotten
    view = led.view(["A1@x", "B1@x", "Z9@x", 5])
    assert len(view) == 1
    assert view.lookup("B1@x")[0] == ISSUED
    assert view.lookup("A1@x")[0] == FORGOTTEN
    assert view.lookup("C1@x")[0] == UNKNOWN  # not named: not sent
    back = pickle.loads(pickle.dumps(view))
    assert back.lookup("B1@x") == view.lookup("B1@x")
    back.record([{"id": "D1@x", "text": "d"}])  # a working lock after unpickling


def test_ledger_records_facts_as_a_json_client_sees_them():
    led = FactLedger()
    led.record([{"id": "F1@x", "text": "t", "value": float("nan"), "range": (1, 2)}, "junk", {}])
    assert led.lookup("F1@x") == (
        ISSUED,
        {"id": "F1@x", "text": "t", "value": None, "range": [1, 2]},
    )
    assert len(led) == 1


# ------------------------------------------------------------------ Registry.call records


def test_every_call_records_its_facts_and_check_citations_resolves_them(tmp_path):
    led = FactLedger()
    ctx = _ctx(tmp_path, led)
    a = REG.call("fact", {"n": 2, "value": 7}, ctx)
    b = REG.call("fact", {"n": 1, "value": 9}, ctx)
    for f in a.facts + b.facts:
        assert led.lookup(f["id"]) == (ISSUED, f)
    # no facts passed: the ids are looked up in the session's ledger
    text = f"Item 1 has value 7 [{a.facts[0]['id']}]. Item 1 has value 9 [{b.facts[0]['id']}]."
    res = _check(ctx, text)
    assert res.data["grounded"] and res.data["provenance"] == "issued"
    assert res.data["cited"] == [a.facts[0]["id"], b.facts[0]["id"]]
    # check_citations' own result is recorded too
    assert led.lookup(res.facts[0]["id"])[0] == ISSUED


def test_an_id_this_session_was_not_issued_is_never_support(tmp_path):
    ctx = _ctx(tmp_path, FactLedger())
    other = _ctx(tmp_path, FactLedger())  # another session (or server, or a restart)
    foreign = REG.call("fact", {"value": 7}, other).facts[0]
    mine = REG.call("fact", {"value": 7}, ctx).facts[0]
    # the same call on the same data gives the same id in both sessions: issued in this one
    assert mine["id"] == foreign["id"]
    alien = REG.call("fact", {"value": 8}, other).facts[0]
    res = _check(ctx, f"Item 1 has value 8 [{alien['id']}].")
    assert res.data["verdicts"]["unknown_citation"] == 1 and not res.data["grounded"]
    assert res.data["sentences"][0]["unknown"] == [alien["id"]]
    # an invented id with a well-formed token, too
    res = _check(ctx, "Item 1 has value 7 [F1@fact-abcdef].")
    assert res.data["verdicts"]["unknown_citation"] == 1


def test_a_passed_fact_must_be_the_issued_one(tmp_path):
    ctx = _ctx(tmp_path, FactLedger())
    issued = REG.call("fact", {"value": 7}, ctx).facts[0]
    # the issued fact, passed back unchanged (as JSON: 7 and 7.0 are the same number), is fine
    same = {**issued, "value": 7.0}
    res = _check(ctx, f"Item 1 has value 7 [{issued['id']}].", facts=[same])
    assert res.data["grounded"] and res.data["provenance"] == "issued"
    # an altered value, or the same id with a recomputed text, refuses the call
    for doctored in (
        {**issued, "value": 70},
        {**issued, "text": "item 1 has value 70"},
        {"id": issued["id"], "text": issued["text"]},  # a fact rebuilt from the summary
    ):
        with pytest.raises(CapabilityError) as e:
            _check(ctx, f"Item 1 has value 70 [{issued['id']}].", facts=[doctored])
        assert e.value.code == "invalid_params"
        assert e.value.data == {"changed": [issued["id"]]}
        assert "leave 'facts' out" in e.value.message
    # an invented fact, with an id the session was never issued
    invented = {"id": "F1@fact-123abc", "text": "item 1 has value 70", "value": 70}
    with pytest.raises(CapabilityError) as e:
        _check(ctx, "Item 1 has value 70 [F1@fact-123abc].", facts=[invented])
    assert e.value.code == "invalid_params" and e.value.data == {"not_issued": ["F1@fact-123abc"]}


def test_a_fact_about_a_refused_facility_does_not_verify(tmp_path):
    """The facts of a facility this caller may not read are never issued to its session, so
    passing them (taken from another caller, say) refuses the call."""
    store = tmp_path / "store"
    idx = pd.date_range("2024-03-01", periods=24, freq="h")
    for fid in ("hq", "secret"):
        ParquetStore(str(store)).write_role_frame(
            pd.DataFrame({Role.OAT: 60.0, Role.SUPPLY_AIR_TEMP: 55.0}, index=idx),
            facility_id=fid,
            equip="AHU-1",
            equip_class="AHU",
        )
    reg = default_registry()
    admin = CallContext(
        store=str(store), allowed_facilities=frozenset({"hq", "secret"}), ledger=FactLedger()
    )
    leaked = reg.call("points", {"facility_id": "secret"}, admin).facts
    me = CallContext(store=str(store), allowed_facilities=frozenset({"hq"}), ledger=FactLedger())
    with pytest.raises(CapabilityError) as e:
        reg.call("points", {"facility_id": "secret"}, me)
    assert e.value.code == "not_allowed"
    mine = reg.call("points", {"facility_id": "hq"}, me).facts
    with pytest.raises(CapabilityError) as e:
        reg.call(
            "check_citations",
            {"text": f"{leaked[0]['text']} [{leaked[0]['id']}].", "facts": leaked},
            me,
        )
    assert e.value.code == "invalid_params"
    res = reg.call(
        "check_citations", {"text": f"{leaked[0]['text']} [{leaked[0]['id']}].", "detail": True}, me
    )
    assert res.data["verdicts"]["unknown_citation"] == 1
    res = reg.call("check_citations", {"text": f"{mine[0]['text']} [{mine[0]['id']}]."}, me)
    assert res.data["grounded"] and res.data["provenance"] == "issued"


def test_a_forgotten_fact_is_said_so(tmp_path):
    led = FactLedger(max_bytes=600)
    ctx = _ctx(tmp_path, led)
    old = REG.call("fact", {"value": 7}, ctx).facts[0]
    REG.call("fact", {"n": 20, "value": 8}, ctx)  # pushes the first out
    assert led.lookup(old["id"])[0] == FORGOTTEN
    res = _check(ctx, f"Item 1 has value 7 [{old['id']}].")
    assert res.data["verdicts"]["forgotten_citation"] == 1 and not res.data["grounded"]
    sent = res.data["sentences"][0]
    assert sent["verdict"] == "forgotten_citation" and sent["forgotten"] == [old["id"]]
    assert sent["unknown"] == []
    assert any("no longer held" in b for b in res.banners)
    assert "forgotten" in res.facts[0]["text"]
    # passed back, a forgotten fact cannot be compared with what was issued: refused
    with pytest.raises(CapabilityError) as e:
        _check(ctx, f"Item 1 has value 7 [{old['id']}].", facts=[old])
    assert e.value.code == "invalid_params" and e.value.data == {"forgotten": [old["id"]]}


def test_without_a_ledger_the_callers_facts_are_taken_and_said_unverified(tmp_path):
    ctx = _ctx(tmp_path)  # direct Python use: no session
    facts = [{"id": "F1@mine", "text": "the pump ran 12 hours"}]
    res = _check(ctx, "The pump ran 12 hours [F1@mine].", facts=facts)
    assert res.data["grounded"] and res.data["provenance"] == "unverified"
    assert res.banners == []  # as before 0.112: the field says it
    with pytest.raises(CapabilityError) as e:
        _check(ctx, "The pump ran 12 hours [F1@mine].")
    assert e.value.code == "invalid_params" and "pass 'facts'" in e.value.message


def test_ledger_view_names_only_the_cited_and_passed_ids(tmp_path):
    led = FactLedger()
    ctx = _ctx(tmp_path, led)
    a, b = REG.call("fact", {"n": 2}, ctx).facts
    view = REG.ledger_view("check_citations", {"text": f"x [{a['id']}]", "facts": [b, "junk"]}, led)
    assert len(view) == 2
    assert len(REG.ledger_view("fact", {"n": 1}, led)) == 0  # reads no facts
    assert len(REG.ledger_view("nope", {}, led)) == 0
    assert len(REG.ledger_view("check_citations", "not an object", led)) == 0
    # a call given the view resolves like one given the whole ledger
    res = _check(replace(ctx, ledger=view), f"Item 1 has value 7 [{a['id']}].")
    assert res.data["grounded"]
