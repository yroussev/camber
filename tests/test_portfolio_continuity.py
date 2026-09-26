"""Renaming a facility never orphans its faults, drift baselines or tickets (lifecycle step 2).

Also: config runs inside a portfolio workspace (facility-bound stores, ``state/<fid>/`` defaults,
the manifest, audited ``drift freeze`` / ``drift accept``), and the unchanged behaviour outside.
"""

import json
import os
import sys
import warnings

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.ahusim import simulate_case  # noqa: E402
from camber.cli import main  # noqa: E402
from camber.config import drift_store_path, load_config, run_config  # noqa: E402
from camber.faultlifecycle import FaultLifecycle  # noqa: E402
from camber.integrate.tickets import findings_to_tickets, fingerprint  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.portfolio import Portfolio  # noqa: E402
from camber.store import FacilityRegistry, make_facility_id  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402

WINDOWS = {"baseline": ["2025-05-01", "2025-05-30"], "current": ["2025-06-01", "2025-07-01"]}


def _cases():
    return (
        ("AHU_1", simulate_case("filter_loading", 4, seed=3)),
        ("AHU_2", simulate_case(None, 0, seed=9)),
    )


def _workspace(tmp_path, name="Old Name"):
    pf = Portfolio.init(tmp_path / "ws")
    fid = pf.add_facility(name, reason="onboard", activate=True)["facility_id"]
    for equip, case in _cases():
        frame = pd.concat([case.baseline, case.current])
        pf.store.write_role_frame(frame, facility_id=fid, equip=equip, equip_class="AHU")
    return pf, fid


def _store_config(tmp_path, fid, **extra):
    cfg = {
        "source": {"kind": "store", "store": "ws/store", "facility_id": fid},
        "equipment": [{"class": "AHU"}],
        "rules": ["simultaneous_heat_cool"],
        "drift": {"run_id": "R1", **WINDOWS, "families": [{"class": "AHU", "family": "ahu"}]},
        "faults": {"run_id": "R1"},
        "report": {"out_html": "report.html"},
    }
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    path = str(tmp_path / "cfg.json")
    open(path, "w").write(json.dumps(cfg))
    return path


def _set_run_id(path, rid):
    cfg = load_config(path)
    cfg["faults"]["run_id"] = rid
    open(path, "w").write(json.dumps(cfg))


def _run(path):
    return run_config(load_config(path), base_dir=os.path.dirname(path))


def _drift_findings(res):
    return sorted((f.equip, f.rule, f.severity) for f in res.drift.findings)


# --------------------------------------------------------------------------- the regression


def test_rename_keeps_faults_baselines_and_tickets(tmp_path, capsys, monkeypatch):
    pf, fid = _workspace(tmp_path)
    monkeypatch.setenv("CAMBER_PORTFOLIO", pf.root)
    cfg = _store_config(tmp_path, fid)

    assert main(["drift", "freeze", cfg]) == 1
    assert "needs --reason" in capsys.readouterr().err
    assert main(["drift", "freeze", cfg, "--reason", "commissioning baseline"]) == 0
    base_path = os.path.join(pf.state_dir(fid), "baselines.json")
    assert drift_store_path(load_config(cfg), base_dir=str(tmp_path)) == base_path
    frozen = BaselineStore.load(base_path).records()
    assert frozen and {r.facility_id for r in frozen} == {fid}

    first = _run(cfg)
    assert first.facility_id == fid and first.workspace == pf.root and first.site == "Old Name"
    new = first.faults["new"]
    assert new and first.faults["store"] == os.path.join(pf.state_dir(fid), "faults.json")
    tickets = findings_to_tickets(first.findings, site=first.site, facility_id=fid)
    before = _drift_findings(first)

    assert main(["facility", "rename", fid, "New Name", "--reason", "rebrand"]) == 0
    _set_run_id(cfg, "R2")
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)  # no compat path involved
        second = _run(cfg)
    assert second.site == "New Name"
    # faults: the same records, now ongoing -- nothing new, nothing absent
    assert second.faults["new"] == [] and second.faults["ongoing"] == new
    assert second.faults["absent"] == []
    lc = FaultLifecycle.load(second.faults["store"], facility_id=fid)
    assert {r.occurrences for r in lc.records()} == {2} and len(lc.records()) == len(new)
    # baselines: the renamed facility still scores against the same frozen references
    assert _drift_findings(second) == before
    assert not any("no frozen baseline" in str(f.summary) for f in second.drift.findings)
    # tickets: the same fingerprints, so the CMMS updates the same tickets
    again = findings_to_tickets(second.findings, site=second.site, facility_id=fid)
    assert [t["fingerprint"] for t in again] == [t["fingerprint"] for t in tickets]

    # an operator decision after the rename moves the existing reference (no orphan record)
    n = len(BaselineStore.load(base_path).records())
    argv = ["drift", "accept", cfg, "--equip", "AHU_1", "--by", "ops", "--reason", "new filters"]
    assert main(argv) == 0
    after = BaselineStore.load(base_path).records()
    assert len(after) == n and any(r.history and r.site == "New Name" for r in after)

    # everything the runs wrote is enumerable from the facility's manifest, and audited
    man = pf.manifest(fid)
    assert {"faults.json", "baselines.json"} <= set(man["files"])
    assert str(tmp_path / "report.html") in man["external"]
    actions = [r["action"] for r in pf.audit_log(facility_id=fid)]
    assert "drift.freeze" in actions and "drift.accept" in actions
    acc = [r for r in pf.audit_log(facility_id=fid) if r["action"] == "drift.accept"][0]
    assert acc["reason"] == "new filters" and acc["details"]["accepted_by"] == "ops"

    capsys.readouterr()
    assert main(["run", cfg, "--out", str(tmp_path / "out")]) == 0
    assert "faults: 0 new" in capsys.readouterr().out
    assert str(tmp_path / "out" / "findings.json") in pf.manifest(fid)["external"]
    assert main(["drift", "list", cfg]) == 0


def test_legacy_state_compat_then_migrate_then_rename(tmp_path, monkeypatch):
    """The upgrade path: site-keyed files from before 0.87, read, migrated, then a rename."""
    pf, fid = _workspace(tmp_path)
    monkeypatch.setenv("CAMBER_PORTFOLIO", pf.root)
    cfg = _store_config(
        tmp_path, fid, drift={"store": "baselines.json"}, faults={"store": "faults.json"}
    )
    # pre-0.87: an unbound baseline store keyed by the site label, and a site-keyed fault store
    legacy = BaselineStore()
    from camber.config import run_drift_config

    run_drift_config(load_config(cfg), base_dir=str(tmp_path), freeze_if_missing=True,
                     store=legacy)  # fmt: skip
    for r in list(legacy._recs.values()):  # re-key as the old code did: sha1(site, ...)
        del legacy._recs[r.fingerprint]
        r.facility_id, r.fingerprint = "", fingerprint("Old Name", r.equip, r.kind)
        legacy._recs[r.fingerprint] = r
    legacy.save(str(tmp_path / "baselines.json"))
    old_lc = FaultLifecycle()
    old_lc.update([{"equip": "AHU_1", "rule": "filter_loading_drift", "severity": "fault"}],
                  run_id="R0", site="Old Name")  # fmt: skip
    old_lc.save(str(tmp_path / "faults.json"))

    # compat: the workspace run reads the site-keyed records, with a deprecation warning
    with pytest.warns(DeprecationWarning, match="site-keyed"):
        res = _run(cfg)
    assert res.faults["legacy_adopted"] == 1 and res.faults["new"] and res.faults["ongoing"]
    assert not any("no frozen baseline" in str(f.summary) for f in res.drift.findings)
    before = _drift_findings(res)

    assert main(["portfolio", "migrate", "--config", cfg]) == 0  # dry run
    assert main(["portfolio", "migrate", "--config", cfg, "--apply", "--reason", "0.87"]) == 0
    for name in ("baselines.json", "faults.json"):
        assert "camber_redirect" in open(tmp_path / name).read()
        assert os.path.isfile(os.path.join(pf.state_dir(fid), name))

    assert main(["facility", "rename", fid, "New Name", "--reason", "rebrand"]) == 0
    _set_run_id(cfg, "R2")
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        res = _run(cfg)
    assert res.faults["new"] == [] and _drift_findings(res) == before
    # the run wrote through the stub: the manifest lists the real file, and the stub as a redirect
    assert res.faults["store"] == os.path.join(pf.state_dir(fid), "faults.json")
    man = pf.manifest(fid)
    assert {"faults.json", "baselines.json"} <= set(man["files"])
    assert man["external"][str(tmp_path / "faults.json")]["kind"] == "redirect"
    assert man["external"][str(tmp_path / "baselines.json")]["kind"] == "redirect"
    rec = FaultLifecycle.load(str(tmp_path / "faults.json"), facility_id=fid).get(
        fingerprint("Old Name", "AHU_1", "filter_loading_drift")
    )
    assert rec.occurrences == 3 and rec.first_seen == "R0"  # one history across the rename


# --------------------------------------------------------------------------- folder configs


def _write_point(folder, equip, measure, series):
    ts = series.index.strftime("%d-%b-%y %I:%M:%S %p") + " PDT"
    pd.DataFrame({"Timestamp": ts, "Value": series.values}).to_csv(
        os.path.join(folder, f"{equip}_{measure}.csv"), index=False
    )


def _token(role) -> str:
    value = role.value if isinstance(role, Role) else str(role)
    return "".join(p.capitalize() for p in value.split("_"))


def _folder_config(tmp_path, **top):
    trends = tmp_path / "trends"
    trends.mkdir(exist_ok=True)
    aliases = {}
    for equip, case in _cases():
        frame = pd.concat([case.baseline, case.current])
        for col in frame.columns:
            aliases[_token(col)] = col.value if isinstance(col, Role) else str(col)
            _write_point(str(trends), equip, _token(col), frame[col])
    cfg = {
        "site": "Folder HQ",
        "source": {"kind": "perpoint_csv", "folder": "trends"},
        "mapping": {"aliases": aliases},
        "equipment": [{"class": "AHU", "marker": "Airflow"}],
        "drift": {
            "store": "baselines.json",
            **WINDOWS,
            "families": [{"class": "AHU", "family": "ahu"}],
        },  # fmt: skip
        "faults": {"store": "faults.json", "run_id": "R1"},
        **top,
    }
    path = str(tmp_path / "cfg.json")
    open(path, "w").write(json.dumps(cfg))
    return path


def test_outside_a_workspace_state_stays_site_keyed(tmp_path):
    path = _folder_config(tmp_path)
    assert main(["drift", "freeze", path]) == 0  # no --reason needed outside a workspace
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # no derived-id warning, no deprecation
        res = _run(path)
    assert res.workspace is None and res.facility_id == make_facility_id("Folder HQ")
    lc = FaultLifecycle.load(str(tmp_path / "faults.json"))
    assert lc.records() and {r.facility_id for r in lc.records()} == {""}
    r0 = lc.records()[0]
    assert r0.fingerprint == fingerprint("Folder HQ", r0.equip, r0.rule)
    b0 = BaselineStore.load(str(tmp_path / "baselines.json")).records()[0]
    assert b0.facility_id == "" and b0.fingerprint == fingerprint("Folder HQ", b0.equip, b0.kind)
    cfg = load_config(path)
    del cfg["faults"]["store"]
    with pytest.raises(ValueError, match="faults.store is required outside"):
        run_config(cfg, base_dir=str(tmp_path))
    cfg["faults"] = ["nope"]
    with pytest.raises(ValueError, match='"faults" must be an object'):
        run_config(cfg, base_dir=str(tmp_path))
    del cfg["drift"]["store"]
    with pytest.raises(ValueError, match="drift.store is required"):
        drift_store_path(cfg, base_dir=str(tmp_path))


def test_folder_config_in_a_workspace(tmp_path, capsys):
    pf = Portfolio.init(tmp_path / "ws")
    path = _folder_config(tmp_path, workspace="ws")
    derived = make_facility_id("Folder HQ")
    with pytest.raises(ValueError, match="register it first"):
        _run(path)
    pf.add_facility("Folder HQ", reason="onboard", activate=True)
    with pytest.warns(UserWarning, match="config has no facility_id"):
        assert main(["drift", "freeze", path, "--reason", "commissioning"]) == 0
    with pytest.warns(UserWarning, match="config has no facility_id"):
        res = _run(path)
    assert res.facility_id == derived and res.workspace == pf.root
    assert FaultLifecycle.load(str(tmp_path / "faults.json")).records()[0].facility_id == derived
    assert str(tmp_path / "faults.json") in pf.manifest(derived)["external"]

    cfg = load_config(path)
    cfg["facility_id"] = derived
    cfg["site"] = "Folder HQ (renamed label)"
    cfg["faults"]["run_id"] = "R2"
    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        res = run_config(cfg, base_dir=str(tmp_path))
    assert res.faults["new"] == []  # a pinned id survives a changed site label
    pf.transition(derived, "suspend", reason="paused")
    with pytest.warns(UserWarning, match="is suspended; skipping"):
        assert run_config(cfg, base_dir=str(tmp_path)).equipment == 0
    cfg["include_inactive"] = True
    assert run_config(cfg, base_dir=str(tmp_path)).equipment == 2

    for bad, msg in (
        ({"workspace": "nowhere"}, "is not a portfolio workspace"),
        ({"site": "", "facility_id": None}, "needs facility_id"),
        ({"facility_id": "bad id"}, "invalid facility_id"),
    ):
        c = {**load_config(path), **bad}
        with pytest.raises(ValueError, match=msg):
            run_config(c, base_dir=str(tmp_path))
    pf.add_facility("Gone", facility_id="gone-1", reason="r")
    FacilityRegistry(pf.store_root).remove("gone-1", reason="sold")
    with pytest.raises(ValueError, match="tombstoned"):
        run_config({**load_config(path), "facility_id": "gone-1"}, base_dir=str(tmp_path))


def test_drift_writes_in_a_workspace_take_the_lock(tmp_path, capsys, monkeypatch):
    pf, fid = _workspace(tmp_path)
    cfg = _store_config(tmp_path, fid)
    import subprocess
    import textwrap

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {repo!r})
        from camber.portfolio._lock import portfolio_lock
        with portfolio_lock({pf.root!r}):
            print("held", flush=True)
            sys.stdin.read()
        """
    )
    child = subprocess.Popen(
        [sys.executable, "-c", src], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    )
    try:
        assert child.stdout.readline().strip() == "held"
        assert main(["drift", "freeze", cfg, "--reason", "r"]) == 1
        assert "locked by" in capsys.readouterr().err
        assert main(["drift", "freeze", cfg, "--dry-run"]) == 0  # a dry run writes nothing
        rc = main(["drift", "accept", cfg, "--equip", "AHU_1", "--by", "o", "--reason", "r"])
        assert rc == 1
    finally:
        child.stdin.close()
        child.wait(timeout=30)
    assert not os.path.exists(os.path.join(pf.state_dir(fid), "baselines.json"))
    assert main(["drift", "freeze", cfg, "--reason", "r"]) == 0
    assert main(["drift", "accept", cfg, "--equip", "AHU_1", "--by", "o", "--reason", "r",
                 "--dry-run"]) == 0  # fmt: skip
    out = capsys.readouterr().out
    assert "not written" in out
    assert main(["drift", "run", cfg, "--out", str(tmp_path / "d")]) == 0
    assert main(["drift", "report", cfg, "--out", str(tmp_path / "d.html")]) == 0
    assert main(["report", cfg, "--out", str(tmp_path / "r.html")]) == 0
    ext = pf.manifest(fid)["external"]
    for p in ("d/drift.json", "d/findings.json", "d.html", "r.html"):
        assert str(tmp_path / p) in ext
