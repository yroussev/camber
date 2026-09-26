"""Fault / baseline / ticket identity keyed by ``facility_id``, and the site-keyed compat path."""

import json
import os
import sys
import warnings

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber._statefile import REDIRECT_KEY  # noqa: E402
from camber.faultlifecycle import FaultLifecycle, FaultRecord  # noqa: E402
from camber.integrate.export import (  # noqa: E402
    diagnoses_to_frame,
    export_findings,
    findings_to_frame,
    vav_diagnoses_to_frame,
)
from camber.integrate.tickets import (  # noqa: E402
    Notifier,
    diagnosis_to_ticket,
    finding_to_ticket,
    fingerprint,
)
from camber.rules.triage import FaultRegister  # noqa: E402
from camber.store.modelstore import BaselineRecord, BaselineStore  # noqa: E402

FID = "north-campus-1a2b3c"


def _f(equip="AHU_1", rule="filter_loading_drift", severity="fault"):
    return {"equip": equip, "rule": rule, "severity": severity, "summary": "s", "metrics": {}}


class _Model:
    def __init__(self, v=1.0):
        self.v = v

    def as_dict(self):
        return {"v": self.v}


def _legacy_faults(path, site="North Campus", **extra):
    fp = fingerprint(site, "AHU_1", "filter_loading_drift")
    rec = {
        "fingerprint": fp,
        "site": site,
        "equip": "AHU_1",
        "rule": "filter_loading_drift",
        "severity": "fault",
        "status": "acknowledged",
        "first_seen": "2026-01-01",
        "last_seen": "2026-02-01",
        "occurrences": 4,
        "assignee": "tech-1",
        "acknowledged_at": "2026-01-02",
        "resolved_at": None,
        "notes": ["called the contractor"],
        **extra,
    }
    open(path, "w").write(json.dumps({"faults": [rec]}))
    return fp


# --------------------------------------------------------------------------- faults


def test_unbound_fault_store_is_unchanged(tmp_path):
    lc = FaultLifecycle()
    out = lc.update([_f()], run_id="R1", site="HQ")
    fp = fingerprint("HQ", "AHU_1", "filter_loading_drift")
    assert out["new"] == [fp] and lc.get(fp).facility_id == ""
    p = str(tmp_path / "f.json")
    lc.save(p)
    raw = open(p).read()
    assert '"facility_id": ""' in raw and not raw.endswith("\n")  # same layout as before


def test_bound_fault_store_survives_a_rename(tmp_path):
    p = str(tmp_path / "faults.json")
    lc = FaultLifecycle.load(p, facility_id=FID)
    new = lc.update([_f()], run_id="R1", site="Old Name")["new"]
    lc.save()
    lc = FaultLifecycle.load(p, facility_id=FID)
    out = lc.update([_f()], run_id="R2", site="New Name")  # the facility was renamed
    assert out["new"] == [] and out["ongoing"] == new
    rec = lc.get(new[0])
    assert rec.fingerprint == fingerprint(FID, "AHU_1", "filter_loading_drift")
    assert rec.facility_id == FID and rec.occurrences == 2 and rec.site == "Old Name"


def test_update_facility_id_kw_scopes_absent_to_that_facility():
    lc = FaultLifecycle()
    lc.update([_f()], run_id="R1", facility_id="a-1")
    lc.update([_f()], run_id="R1", facility_id="b-2")
    out = lc.update([], run_id="R2", facility_id="a-1", auto_resolve_absent=True)
    assert out["resolved"] == [fingerprint("a-1", "AHU_1", "filter_loading_drift")]
    assert len(lc.open_faults()) == 1 and lc.open_faults()[0].facility_id == "b-2"


def test_compat_read_rekeys_site_keyed_faults_with_a_deprecation_warning(tmp_path):
    p = str(tmp_path / "faults.json")
    old = _legacy_faults(p)
    with pytest.warns(DeprecationWarning, match="site-keyed fault"):
        lc = FaultLifecycle.load(p, facility_id=FID, legacy_sites=("North Campus",))
    new = fingerprint(FID, "AHU_1", "filter_loading_drift")
    rec = lc.get(new)
    assert lc.legacy_adopted == 1 and rec.aliases == [old] and rec.facility_id == FID
    assert lc.get(old) is rec  # the old fingerprint (e.g. on a CMMS ticket) still resolves
    lc.acknowledge(old, "2026-03-01")
    assert rec.status == "acknowledged" and rec.assignee == "tech-1"
    out = lc.update([_f()], run_id="2026-03-02", site="North Campus (renamed)")
    assert out["ongoing"] == [new] and rec.occurrences == 5


def test_compat_read_through_update_site_and_merge_with_existing_record(tmp_path):
    p = str(tmp_path / "faults.json")
    old = _legacy_faults(p, site="Old")
    lc = FaultLifecycle.load(p, facility_id=FID)  # no legacy_sites: nothing adopted yet
    assert lc.legacy_adopted == 0 and lc.records()[0].facility_id == ""
    # a facility-keyed record for the same fault already exists (a run before the migration)
    new = fingerprint(FID, "AHU_1", "filter_loading_drift")
    lc._recs[new] = FaultRecord(new, "New", "AHU_1", "filter_loading_drift", "warn",
                                first_seen="2026-02-10", last_seen="2026-02-20",
                                occurrences=2, notes=["n2"], facility_id=FID)  # fmt: skip
    with pytest.warns(DeprecationWarning):
        lc.update([], run_id="2026-03-01", site="Old")
    [rec] = lc.records()
    assert rec.fingerprint == new and rec.occurrences == 6 and old in rec.aliases
    assert rec.first_seen == "2026-01-01" and rec.last_seen == "2026-02-20"
    assert rec.notes == ["called the contractor", "n2"] and rec.severity == "warn"


def test_unrelated_legacy_records_are_left_alone():
    lc = FaultLifecycle(facility_id=FID)
    lc.update([_f()], run_id="R1", site="Elsewhere", facility_id="")  # unbound-style record
    other = lc.records()[0]
    assert other.facility_id == FID  # bound store keys every new record by its facility
    lc2 = FaultLifecycle()
    lc2.update([_f()], run_id="R1", site="Elsewhere")
    lc2.facility_id = FID
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        lc2.update([], run_id="R2", site="Here")  # 'Elsewhere' is not one of its names
    assert lc2.records()[0].facility_id == ""


def test_explicit_legacy_sites_are_authoritative(tmp_path):
    """A label that could be another facility's (e.g. a shared display name) is never adopted."""
    p = str(tmp_path / "faults.json")
    _legacy_faults(p, site="Annex")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        lc = FaultLifecycle.load(p, facility_id=FID, legacy_sites=())
        lc.update([], run_id="R1", site="Annex")
    assert lc.records()[0].facility_id == ""
    b = str(tmp_path / "b.json")
    _legacy_baselines(b, site="Annex")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        st = BaselineStore.load(b, facility_id=FID, legacy_sites=[])
        assert st.get("Annex", "AHU_1", "fan_efficiency") is None


def test_fault_record_from_dict_ignores_unknown_keys():
    rec = FaultRecord("fp", "s", "e", "r", "fault").as_dict()
    rec["from_the_future"] = 1
    assert FaultRecord.from_dict(rec).fingerprint == "fp"


# --------------------------------------------------------------------------- redirect stubs


def _stub(tmp_path, kind="faults", facilities=(FID,)):
    state = tmp_path / "ws" / "state"
    for fid in facilities:
        (state / fid).mkdir(parents=True)
        if kind == "faults":
            rec = FaultRecord(f"fp-{fid}", "s", "AHU_1", "r", "fault", facility_id=fid)
        else:
            rec = BaselineRecord(f"fp-{fid}", "s", "AHU_1", "k", {}, "t0", facility_id=fid)
        recs = [rec.as_dict()]
        (state / fid / f"{kind}.json").write_text(json.dumps({kind: recs}))
    legacy = tmp_path / "cfg" / f"{kind}.json"
    legacy.parent.mkdir()
    red = {"kind": kind, "file": f"{kind}.json", "state_root": "../ws/state",
           "state_root_abs": str(state), "facilities": list(facilities)}  # fmt: skip
    legacy.write_text(json.dumps({REDIRECT_KEY: red}))
    return str(legacy), state


def test_redirect_stub_is_followed_bound_and_merged_unbound(tmp_path):
    legacy, state = _stub(tmp_path, facilities=(FID, "b-2"))
    lc = FaultLifecycle.load(legacy, facility_id=FID)
    assert [r.fingerprint for r in lc.records()] == [f"fp-{FID}"]
    lc.update([_f()], run_id="R9", site="x")
    lc.save()
    assert len(json.loads((state / FID / "faults.json").read_text())["faults"]) == 2
    assert REDIRECT_KEY in json.loads(open(legacy).read())  # the stub is never overwritten
    merged = FaultLifecycle.load(legacy)
    assert len(merged.records()) == 3
    with pytest.raises(ValueError, match="was migrated"):
        merged.save()
    # a facility the stub does not list yet gets its own derived path
    lc3 = FaultLifecycle.load(legacy, facility_id="c-3")
    assert lc3.records() == []
    lc3.update([_f()], run_id="R1", site="c")
    lc3.save()
    assert (state / "c-3" / "faults.json").is_file()


def test_redirect_falls_back_to_the_absolute_state_root(tmp_path):
    legacy, state = _stub(tmp_path, kind="baselines")
    moved = tmp_path / "elsewhere.json"
    os.replace(legacy, moved)  # the relative path no longer resolves
    st = BaselineStore.load(str(moved), facility_id=FID)
    assert len(st.records()) == 1
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({REDIRECT_KEY: {"file": "x.json"}}))
    with pytest.raises(ValueError, match="no usable state_root"):
        BaselineStore.load(str(bad), facility_id=FID)
    bad.write_text("[1]")
    with pytest.raises(ValueError, match="not a JSON object"):
        BaselineStore.load(str(bad))


# --------------------------------------------------------------------------- baselines


def test_unbound_baseline_store_is_unchanged():
    st = BaselineStore()
    rec = st.freeze(_Model(), site="HQ", equip="AHU_1", kind="fan_efficiency", frozen_at="t0")
    assert rec.fingerprint == fingerprint("HQ", "AHU_1", "fan_efficiency")
    assert rec.facility_id == "" and st.get("HQ", "AHU_1", "fan_efficiency") is rec


def test_bound_baseline_store_ignores_the_site_label(tmp_path):
    p = str(tmp_path / "b.json")
    st = BaselineStore.load(p, facility_id=FID)
    st.freeze(_Model(), site="Old Name", equip="AHU_1", kind="fan_efficiency", frozen_at="t0")
    st.save()
    st = BaselineStore.load(p, facility_id=FID)
    rec = st.get("New Name", "AHU_1", "fan_efficiency")
    assert rec is not None and rec.facility_id == FID and rec.site == "Old Name"
    with pytest.raises(ValueError, match="already frozen"):
        st.freeze(_Model(), site="New Name", equip="AHU_1", kind="fan_efficiency", frozen_at="t1")


def _legacy_baselines(path, site="North Campus"):
    st = BaselineStore()
    st.freeze(_Model(1), site=site, equip="AHU_1", kind="fan_efficiency", frozen_at="2025-06-01")
    st.save(path)
    return fingerprint(site, "AHU_1", "fan_efficiency")


def test_compat_read_of_site_keyed_baselines(tmp_path):
    p = str(tmp_path / "b.json")
    old = _legacy_baselines(p)
    st = BaselineStore.load(p, facility_id=FID)  # not a known name yet: nothing adopted
    assert st.records()[0].facility_id == ""
    with pytest.warns(DeprecationWarning, match="site-keyed baseline"):
        rec = st.get("North Campus", "AHU_1", "fan_efficiency")
    assert rec.facility_id == FID and rec.aliases == [old] and st.legacy_adopted == 1
    # the operator decision supersedes the adopted record; history and aliases carry over
    new = st.accept_new_normal(
        _Model(2),
        site="Renamed",
        equip="AHU_1",
        kind="fan_efficiency",
        accepted_by="ops",
        reason="coil cleaned",
        at="2026-01-01",
    )
    assert new.supersedes == "2025-06-01" and new.aliases == [old] and len(new.history) == 1
    assert new.facility_id == FID


def test_compat_adoption_keeps_the_facility_keyed_reference_live(tmp_path):
    p = str(tmp_path / "b.json")
    _legacy_baselines(p)
    st = BaselineStore.load(p, facility_id=FID)
    st.freeze(_Model(3), site="Renamed", equip="AHU_1", kind="fan_efficiency", frozen_at="2026-02")
    # the facility-keyed record exists, so a lookup does not go looking for legacy ones ...
    assert st.get("North Campus", "AHU_1", "fan_efficiency").coefficients == {"v": 3}
    st.save()
    # ... but naming the old label at load adopts it, as history under the live reference
    with pytest.warns(DeprecationWarning):
        st = BaselineStore.load(p, facility_id=FID, legacy_sites=("North Campus",))
    [rec] = st.records()
    assert rec.coefficients == {"v": 3} and [h["frozen_at"] for h in rec.history] == ["2025-06-01"]


def test_baseline_legacy_sites_at_load_and_unknown_keys(tmp_path):
    p = str(tmp_path / "b.json")
    _legacy_baselines(p, site="Café Annex")  # unicode site label
    with pytest.warns(DeprecationWarning):
        st = BaselineStore.load(p, facility_id=FID, legacy_sites=("Café Annex",))
    assert st.records()[0].facility_id == FID
    d = st.records()[0].as_dict()
    d["new_field"] = True
    assert BaselineRecord.from_dict(d).facility_id == FID


# --------------------------------------------------------------------------- tickets / exports


def test_tickets_keyed_by_facility_carry_the_legacy_fingerprint():
    plain = finding_to_ticket(_f(), site="HQ")
    assert "facility_id" not in plain and "legacy_fingerprint" not in plain
    assert plain["fingerprint"] == fingerprint("HQ", "AHU_1", "filter_loading_drift")
    t = finding_to_ticket(_f(), site="HQ", facility_id=FID)
    assert t["fingerprint"] == fingerprint(FID, "AHU_1", "filter_loading_drift")
    assert t["legacy_fingerprint"] == plain["fingerprint"] and t["facility_id"] == FID
    renamed = finding_to_ticket(_f(), site="HQ (renamed)", facility_id=FID)
    assert renamed["fingerprint"] == t["fingerprint"]  # a rename updates the same ticket
    assert "legacy_fingerprint" not in finding_to_ticket(_f(), facility_id=FID)
    d = {"equip": "CH_1", "severity": "fault", "locus": "condenser", "machine_wide": True}
    assert diagnosis_to_ticket(d, site="HQ", facility_id=FID)["fingerprint"] == fingerprint(
        FID, "CH_1", "chiller_drift"
    )
    n = Notifier()
    sent = n.emit_findings([_f(), _f(severity="ok")], site="HQ", facility_id=FID)
    assert len(sent) == 1 and n.collected[0]["facility_id"] == FID
    sent = n.emit_diagnoses([d], site="HQ", facility_id=FID)
    assert sent[0]["facility_id"] == FID


def test_exports_add_a_facility_column_only_when_asked(tmp_path):
    df = findings_to_frame([_f()], site="HQ")
    assert "facility_id" not in df.columns and list(df.columns)[:2] == ["fingerprint", "site"]
    df = findings_to_frame([_f()], site="HQ", facility_id=FID)
    assert list(df.columns)[:3] == ["fingerprint", "facility_id", "site"]
    assert df["fingerprint"][0] == fingerprint(FID, "AHU_1", "filter_loading_drift")
    d = {"equip": "CH_1", "severity": "fault"}
    assert diagnoses_to_frame([d], site="HQ", facility_id=FID)["facility_id"][0] == FID
    assert vav_diagnoses_to_frame([d], facility_id=FID)["fingerprint"][0] == fingerprint(
        FID, "CH_1", "vav_drift"
    )
    out = str(tmp_path / "f.csv")
    assert export_findings([_f()], out, site="HQ", facility_id=FID) == 1
    assert "facility_id" in open(out).readline()


def test_fault_register_keys_by_facility_when_given():
    reg = FaultRegister()
    a = reg.update([_f()], site="Old", run_id=1, facility_id=FID)
    b = reg.update([_f()], site="New", run_id=2, facility_id=FID)
    assert a["new"] == b["ongoing"] == [fingerprint(FID, "AHU_1", "filter_loading_drift")]
