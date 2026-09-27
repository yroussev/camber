"""`camber portfolio migrate`: site-keyed fault / baseline files -> ``state/<facility_id>/``."""

import contextlib
import json
import os
import subprocess
import sys
import textwrap
import unicodedata
import warnings

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber._statefile import REDIRECT_KEY  # noqa: E402
from camber.cli import main  # noqa: E402
from camber.faultlifecycle import FaultLifecycle  # noqa: E402
from camber.integrate.tickets import fingerprint  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.portfolio import Portfolio, PortfolioLocked  # noqa: E402
from camber.portfolio._state import SiteResolver, fold, sha256_file  # noqa: E402
from camber.store import FacilityRegistry  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _M:
    def __init__(self, v=1):
        self.v = v

    def as_dict(self):
        return {"v": self.v}


def _f(equip="AHU_1", rule="filter_loading_drift", severity="fault"):
    return {"equip": equip, "rule": rule, "severity": severity}


def _ws(tmp_path, *names):
    pf = Portfolio.init(tmp_path / "ws")
    fids = [pf.add_facility(n, reason="onboard", activate=True)["facility_id"] for n in names]
    return pf, fids


def _legacy_faults(path, *sites, run_id="2026-01-01"):
    lc = FaultLifecycle()
    for s in sites:
        lc.update([_f()], run_id=run_id, site=s)
    lc.save(str(path))
    return str(path)


def _legacy_baselines(path, *sites, frozen_at="2025-06-01"):
    st = BaselineStore()
    for i, s in enumerate(sites):
        st.freeze(_M(i), site=s, equip="AHU_1", kind="fan_efficiency", frozen_at=frozen_at)
    st.save(str(path))
    return str(path)


def _tree(root):
    out = {}
    for d, _dirs, names in os.walk(root):
        for n in names:
            p = os.path.join(d, n)
            out[os.path.relpath(p, root)] = open(p, "rb").read()
    return out


# --------------------------------------------------------------------------- plan / apply


def test_dry_run_plans_and_writes_nothing(tmp_path):
    pf, (a, b) = _ws(tmp_path, "North Campus", "Annex")
    fp = _legacy_faults(tmp_path / "faults.json", "North Campus", "Annex")
    bp = _legacy_baselines(tmp_path / "baselines.json", "North Campus")
    before = _tree(tmp_path)
    r = pf.migrate([fp, bp])
    assert r["dry_run"] and not r["applied"] and not r["blocked"]
    assert r["labels"]["North Campus"]["facility_id"] == a
    assert (
        r["labels"]["North Campus"]["via"] == "exact"
        and r["labels"]["North Campus"]["records"] == 2
    )
    assert r["facilities"][a]["faults"] == 1 and r["facilities"][a]["baselines"] == 1
    assert r["facilities"][b]["faults"] == 1
    assert _tree(tmp_path) == before  # nothing written, not even an audit line


def test_apply_moves_rekeys_stubs_manifests_and_audits(tmp_path):
    pf, (a, b) = _ws(tmp_path, "North Campus", "Annex")
    fp = _legacy_faults(tmp_path / "faults.json", "North Campus", "Annex")
    bp = _legacy_baselines(tmp_path / "baselines.json", "North Campus")
    orig_sha, bsha = sha256_file(fp), sha256_file(bp)
    r = pf.migrate([fp, bp], apply=True, reason="step 2 identity migration")
    assert r["applied"] and r["changed"] and sorted(r["stubs"]) == sorted([fp, bp])
    # the legacy file is a redirect stub now
    red = json.load(open(fp))[REDIRECT_KEY]
    assert red["facilities"] == sorted([a, b]) and red["sha256"] == orig_sha
    # re-keyed records live under state/<fid>/, their old fingerprints kept as aliases
    recs = json.load(open(os.path.join(pf.state_dir(a), "faults.json")))["faults"]
    assert [x["fingerprint"] for x in recs] == [fingerprint(a, "AHU_1", "filter_loading_drift")]
    assert recs[0]["aliases"] == [fingerprint("North Campus", "AHU_1", "filter_loading_drift")]
    assert recs[0]["facility_id"] == a and recs[0]["site"] == "North Campus"
    base = json.load(open(os.path.join(pf.state_dir(a), "baselines.json")))["baselines"]
    assert base[0]["fingerprint"] == fingerprint(a, "AHU_1", "fan_efficiency")
    # originals are kept per facility, and the manifest hashes every file
    man = pf.manifest(a)
    assert set(man["files"]) == {
        "faults.json",
        "baselines.json",
        f"migrated/faults-{orig_sha[:12]}.json",
        f"migrated/baselines-{bsha[:12]}.json",
    }
    for rel, e in man["files"].items():
        assert e["sha256"] == sha256_file(os.path.join(pf.state_dir(a), rel))
    assert {m["path"] for m in man["migrated_from"]} == {fp, bp}
    # audited: one portfolio record and one per facility, with actor and reason
    log = pf.audit_log()
    top = [x for x in log if x["action"] == "portfolio.migrate"]
    per = {x["facility_id"]: x for x in log if x["action"] == "facility.migrate"}
    assert len(top) == 1 and top[0]["reason"] == "step 2 identity migration" and top[0]["actor"]
    assert set(per) == {a, b} and per[a]["details"]["faults"] == 1
    assert per[a]["from_state"] == per[a]["to_state"] == "active"
    # a store opened for the facility through the old path follows the stub (no warning)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        lc = FaultLifecycle.load(fp, facility_id=a)
        assert lc.get(fingerprint("North Campus", "AHU_1", "filter_loading_drift")).facility_id == a
        assert BaselineStore.load(bp, facility_id=a).get("x", "AHU_1", "fan_efficiency")


def test_apply_is_idempotent(tmp_path, monkeypatch):
    # an injected clock: the lock's holder line records when it was taken, and two applies a
    # second apart would otherwise differ in that text alone (#34)
    from camber.portfolio import _lock

    monkeypatch.setattr(_lock, "_utc_now", lambda: "2026-01-01T00:00:00Z")
    pf, (a,) = _ws(tmp_path, "North Campus")
    fp = _legacy_faults(tmp_path / "faults.json", "North Campus")
    pf.migrate([fp], apply=True, reason="once")
    snap = _tree(tmp_path)
    n_audit = len(pf.audit_log())
    r = pf.migrate([fp], apply=True, reason="twice")
    assert r["applied"] and not r["changed"] and r["sources"][0]["status"] == "migrated"
    assert _tree(tmp_path) == snap and len(pf.audit_log()) == n_audit


def test_crash_after_targets_before_stub_recovers_without_double_counting(tmp_path):
    pf, (a,) = _ws(tmp_path, "North Campus")
    fp = _legacy_faults(tmp_path / "faults.json", "North Campus")
    legacy = open(fp, "rb").read()
    pf.migrate([fp], apply=True, reason="first")
    open(fp, "wb").write(legacy)  # as if the process died before replacing the legacy file
    r = pf.migrate([fp], apply=True, reason="retry")
    assert r["facilities"][a]["skipped"] == 1 and r["facilities"][a]["faults"] == 0
    [rec] = json.load(open(os.path.join(pf.state_dir(a), "faults.json")))["faults"]
    assert rec["occurrences"] == 1 and REDIRECT_KEY in json.load(open(fp))


def test_no_sources_is_a_clean_empty_plan(tmp_path, capsys):
    pf, _ = _ws(tmp_path, "X")
    r = pf.migrate(apply=True, reason="nothing")
    assert not r["changed"] and not r["blocked"]
    assert main(["portfolio", "migrate", "--workspace", pf.root]) == 0
    assert "no legacy files given" in capsys.readouterr().out


# --------------------------------------------------------------------------- refusals


def test_ambiguous_label_is_refused_and_nothing_is_written(tmp_path):
    pf, (a, b) = _ws(tmp_path, "Annex", "Annex West")
    pf.rename(b, "Annex", reason="both now display 'Annex'")
    fp = _legacy_faults(tmp_path / "faults.json", "Annex")
    before = _tree(tmp_path)
    r = pf.migrate([fp], apply=True, reason="try")
    row = r["labels"]["Annex"]
    assert r["blocked"] and not r["applied"] and row["problem"] == "ambiguous"
    assert sorted(row["candidates"]) == sorted([a, b])
    assert _tree(tmp_path) == before
    # an explicit mapping is an operator decision, not a guess
    r = pf.migrate([fp], mapping=[f"Annex={b}"], apply=True, reason="it was the west wing")
    assert r["applied"] and r["labels"]["Annex"]["via"] == "map"
    assert r["labels"]["Annex"]["facility_id"] == b


def test_unmapped_and_bad_inputs_block_with_a_hint(tmp_path, capsys):
    pf, (a,) = _ws(tmp_path, "North Campus")
    fp = _legacy_faults(tmp_path / "faults.json", "North Campus", "Somewhere Else")
    junk = tmp_path / "junk.json"
    junk.write_text('{"hello": 1}')
    r = pf.migrate([fp, str(junk), str(tmp_path / "missing.json")])
    whats = {p["what"]: p["problem"] for p in r["problems"]}
    assert whats["site 'Somewhere Else'"] == "unmapped"
    assert whats[f"file {junk}"] == "unrecognized"
    assert whats[f"file {tmp_path / 'missing.json'}"] == "missing"
    rc = main(["portfolio", "migrate", fp, "--workspace", pf.root, "--apply", "--reason", "r"])
    out = capsys.readouterr().out
    assert rc == 1 and "CANNOT MAP (unmapped)" in out and '--map "SITE=FACILITY_ID"' in out
    assert "exit 1: plan blocked" in out and REDIRECT_KEY not in open(fp).read()
    with pytest.raises(ValueError, match="SITE=FACILITY_ID"):
        pf.migrate([fp], mapping=["no-equals-sign"])
    r = pf.migrate([fp], mapping={"Somewhere Else": "ghost-1"})
    assert {p["problem"] for p in r["problems"]} == {"unknown"}


def test_tombstoned_ids_are_never_reused(tmp_path):
    pf, (a, gone) = _ws(tmp_path, "North Campus", "Old Depot")
    FacilityRegistry(pf.store_root).remove(gone, reason="sold")
    fp = _legacy_faults(tmp_path / "faults.json", "Old Depot")
    r = pf.migrate([fp])
    assert r["labels"]["Old Depot"]["problem"] == "tombstoned"
    assert r["labels"]["Old Depot"]["candidates"] == [f"{gone} (tombstoned)"]
    r = pf.migrate([fp], mapping={"Old Depot": gone})
    assert r["blocked"] and any(p["problem"] == "tombstoned" for p in r["problems"])
    # a label matching a live facility *and* a tombstone is ambiguous, not "the live one"
    pf2, (x,) = _ws(tmp_path / "two", "Depot")
    pf2.add_facility("Depot", facility_id="depot-old", reason="r", activate=True)
    FacilityRegistry(pf2.store_root).remove("depot-old", reason="gone")
    res = SiteResolver(pf2).resolve("Depot")
    assert res["problem"] == "ambiguous" and "depot-old (tombstoned)" in res["candidates"]
    # a label whose derived id is tombstoned
    assert SiteResolver(pf).resolve("old depot ")["problem"] == "tombstoned"


# --------------------------------------------------------------------------- labels


def test_case_variants_unicode_and_whitespace(tmp_path):
    pf, (a, b, c) = _ws(tmp_path, "Annex", "annex", "Café Tower")
    res = SiteResolver(pf)
    assert res.resolve("Annex")["facility_id"] == a  # exact beats a folded variant
    assert res.resolve("annex")["facility_id"] == b
    amb = res.resolve("ANNEX")
    assert amb["problem"] == "ambiguous" and amb["via"] == "folded"
    nfd = unicodedata.normalize("NFD", "café  tower")
    assert nfd != "Café Tower" and fold(nfd) == fold("Café Tower")
    hit = res.resolve(nfd)
    assert hit["facility_id"] == c and hit["via"] == "folded"
    assert res.resolve("")["problem"] == "empty"
    d = pf.add_facility("東京 オフィス", reason="r", activate=True)["facility_id"]
    fp = _legacy_faults(tmp_path / "f.json", "東京 オフィス")
    r = pf.migrate([fp], apply=True, reason="unicode")
    assert r["labels"]["東京 オフィス"]["facility_id"] == d
    assert json.load(open(os.path.join(pf.state_dir(d), "faults.json")))["faults"][0]["site"] == (
        "東京 オフィス"
    )


def test_rename_history_and_derived_ids_map(tmp_path):
    pf, (a,) = _ws(tmp_path, "North Campus")
    pf.rename(a, "North Campus East", reason="r1")
    pf.rename(a, "NCE", reason="r2")
    res = SiteResolver(pf)
    for label in ("North Campus", "North Campus East", "NCE", a):
        assert res.resolve(label)["facility_id"] == a
    assert pf.legacy_sites(a) == sorted({"North Campus", "North Campus East", "NCE", a})
    # an unregistered store partition is known by its id
    idx = pd.date_range("2025-07-07", periods=3, freq="h")
    pf.store.write_role_frame(
        pd.DataFrame({Role.OAT: [1.0, 2.0, 3.0]}, index=idx), facility_id="bare-1", equip="E"
    )
    res = SiteResolver(pf)
    assert res.resolve("bare-1")["facility_id"] == "bare-1"
    assert "bare-1" not in pf.registry.all()
    b = pf.add_facility("Sub Station", reason="r", activate=True)["facility_id"]
    pf.rename(b, "Substation B", reason="r")
    assert SiteResolver(pf).resolve("sub station")["facility_id"] == b  # folded history


def test_two_labels_of_one_facility_merge_deterministically(tmp_path):
    """The step-1 orphaning bug: history split across the old and the new display name."""
    pf, (a,) = _ws(tmp_path, "Old Name")
    pf.rename(a, "New Name", reason="rebrand")
    lc = FaultLifecycle()
    lc.update([_f()], run_id="2026-01-01", site="Old Name")
    lc.update([_f()], run_id="2026-01-02", site="Old Name")
    lc.update([_f()], run_id="2026-02-01", site="New Name")
    lc.save(str(tmp_path / "faults.json"))
    st = BaselineStore()
    st.freeze(_M(1), site="Old Name", equip="AHU_1", kind="fan_efficiency", frozen_at="2025-06-01")
    st.freeze(_M(2), site="New Name", equip="AHU_1", kind="fan_efficiency", frozen_at="2025-05-01")
    st.save(str(tmp_path / "b.json"))
    r = pf.migrate([str(tmp_path / "faults.json"), str(tmp_path / "b.json")], apply=True,
                   reason="heal the split")  # fmt: skip
    assert r["facilities"][a]["merged"] == 2 and len(r["merged"]) == 2
    [f] = json.load(open(os.path.join(pf.state_dir(a), "faults.json")))["faults"]
    assert (
        f["occurrences"] == 3 and f["first_seen"] == "2026-01-01" and f["last_seen"] == "2026-02-01"
    )
    assert len(f["aliases"]) == 2
    [b] = json.load(open(os.path.join(pf.state_dir(a), "baselines.json")))["baselines"]
    # the reference runs were reading (the current display name) stays live
    assert b["coefficients"] == {"v": 2} and [h["coefficients"] for h in b["history"]] == [{"v": 1}]


def test_records_that_already_carry_a_facility_id_move_as_is(tmp_path):
    pf, (a,) = _ws(tmp_path, "North Campus")
    p = str(tmp_path / "external.json")
    lc = FaultLifecycle(facility_id=a)
    lc.update([_f()], run_id="R1", site="whatever")
    lc.save(p)
    r = pf.migrate([p], apply=True, reason="bring it home")
    assert r["labels"][f"facility_id={a}"]["via"] == "record"
    assert os.path.isfile(os.path.join(pf.state_dir(a), "faults.json"))


# --------------------------------------------------------------------------- configs


def test_config_sources_map_through_the_config_and_list_reports(tmp_path):
    pf, (a,) = _ws(tmp_path, "North Campus")
    cfgdir = tmp_path / "cfg"
    cfgdir.mkdir()
    # a store config without "site": the pre-0.86 `drift accept` keyed these under site ""
    _legacy_baselines(cfgdir / "baselines.json", "")
    _legacy_faults(cfgdir / "faults.json", "North Campus")
    (cfgdir / "report.html").write_text("<html></html>")
    cfg = {
        "source": {"kind": "store", "store": pf.store_root, "facility_id": a},
        "drift": {"store": "baselines.json"},
        "faults": {"store": "faults.json"},
        "report": {"out_html": "report.html", "out_text": "never-written.txt"},
    }
    (cfgdir / "cfg.json").write_text(json.dumps(cfg))
    r = pf.migrate(configs=[str(cfgdir / "cfg.json")], apply=True, reason="configs")
    assert r["configs"][0]["facility_id"] == a and r["labels"][""]["via"] == "config"
    assert r["facilities"][a]["reports"] == 1
    ext = pf.manifest(a)["external"]
    kinds = {p: e["kind"] for p, e in ext.items()}
    assert kinds == {
        str(cfgdir / "report.html"): "report",
        str(cfgdir / "baselines.json"): "redirect",  # the stubs the config still names
        str(cfgdir / "faults.json"): "redirect",
    }
    # a folder config names its facility by facility_id, or by site through the registry
    cfg2 = {"site": "Nowhere", "source": {"folder": "."}, "drift": {"store": "x.json"}}
    (cfgdir / "cfg2.json").write_text(json.dumps(cfg2))
    r = pf.migrate(configs=[str(cfgdir / "cfg2.json")])
    assert r["blocked"] and r["configs"][0]["problem"] == "unmapped"
    cfg2["facility_id"] = a
    (cfgdir / "cfg2.json").write_text(json.dumps(cfg2))
    assert not pf.migrate(configs=[str(cfgdir / "cfg2.json")])["blocked"]


def test_one_file_shared_by_two_configs_with_blank_sites_is_ambiguous(tmp_path):
    pf, (a, b) = _ws(tmp_path, "A", "B")
    _legacy_faults(tmp_path / "shared.json", "")
    for n, fid in (("a", a), ("b", b)):
        cfg = {"source": {"folder": "."}, "facility_id": fid, "faults": {"store": "shared.json"}}
        (tmp_path / f"{n}.json").write_text(json.dumps(cfg))
    r = pf.migrate(configs=[str(tmp_path / "a.json"), str(tmp_path / "b.json")])
    assert r["labels"][""]["problem"] == "ambiguous" and r["blocked"]


# --------------------------------------------------------------------------- lock / CLI


_HOLDER = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, {repo!r})
    from camber.portfolio._lock import portfolio_lock
    with portfolio_lock({root!r}):
        print("held", flush=True)
        sys.stdin.read()
    """
)


@contextlib.contextmanager
def _held(root):
    child = subprocess.Popen(
        [sys.executable, "-c", _HOLDER.format(repo=_REPO, root=root)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline().strip() == "held"
        yield child
    finally:
        child.stdin.close()
        child.wait(timeout=30)


def test_apply_under_lock_contention_is_refused_but_dry_run_works(tmp_path, capsys):
    pf, (a,) = _ws(tmp_path, "North Campus")
    fp = _legacy_faults(tmp_path / "faults.json", "North Campus")
    before = _tree(tmp_path / "ws")
    with _held(pf.root) as child:
        with pytest.raises(PortfolioLocked, match=f"locked by {child.pid}@"):
            pf.migrate([fp], apply=True, reason="blocked")
        rc = main(["portfolio", "migrate", fp, "--workspace", pf.root, "--apply", "--reason", "r"])
        assert rc == 1 and f"locked by {child.pid}@" in capsys.readouterr().err
        assert not pf.migrate([fp])["blocked"]  # planning needs no lock
    after = _tree(tmp_path / "ws")
    after.pop("_lock", None), before.pop("_lock", None)
    assert after == before and REDIRECT_KEY not in open(fp).read()
    assert pf.migrate([fp], apply=True, reason="lock released")["changed"]


def test_cli_migrate_dry_run_apply_json_and_reason(tmp_path, capsys, monkeypatch):
    pf, (a,) = _ws(tmp_path, "North Campus")
    fp = _legacy_faults(tmp_path / "faults.json", "North Campus")
    monkeypatch.setenv("CAMBER_PORTFOLIO", pf.root)
    assert main(["portfolio", "migrate", fp]) == 0
    out = capsys.readouterr().out
    assert "dry run -- nothing written" in out and f"-> {a}  (via exact)" in out
    assert "plan is clean" in out
    assert main(["portfolio", "migrate", fp, "--apply"]) == 1
    assert "--apply needs --reason" in capsys.readouterr().err
    assert main(["portfolio", "migrate", fp, "--apply", "--reason", "go", "--json"]) == 0
    r = json.loads(capsys.readouterr().out)
    assert r["applied"] and r["changed"]
    assert main(["portfolio", "migrate", fp, "--apply", "--reason", "again"]) == 0
    assert "nothing to do: already migrated" in capsys.readouterr().out
    assert main(["facility", "show", a]) == 0
    out = capsys.readouterr().out
    assert "faults.json" in out and "facility.migrate" in out and "sha256" in out
    assert main(["portfolio", "migrate", "--apply", "--reason", "r", "--map", "X"]) == 1
