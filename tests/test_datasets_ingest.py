"""Fetch -> ingest -> config -> run -> score on synthetic catalog entries (no network).

A tiny LBNL-shaped zip (three labelled AHU runs, 1-minute data) and a tiny BDG2-shaped file set are
served by a fake HTTPS opener, so the whole pipeline runs offline: the research-only licence gate
and its ledger, run namespacing, labels + the splice onset, quirks (fix vs annotate), unit
conversion, duty resampling, idempotent skip / force / replace, a crash between staging and swap,
the store-backed config + report banner, label scoring, and the CLI exit codes.
"""

import copy
import hashlib
import io
import json
import os
import sys
import zipfile

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camber.datasets as ds  # noqa: E402
from camber.config import run_config  # noqa: E402
from camber.datasets import _ingest, _ops, _paths  # noqa: E402
from camber.datasets._catalog import DatasetEntry, validate_catalog  # noqa: E402
from camber.datasets._fetch import ChecksumMismatch, InsufficientSpace  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.report.audit import RESEARCH_ONLY_BANNER  # noqa: E402
from camber.store import ParquetStore  # noqa: E402

BASE = "https://example.org/ds/"


# --------------------------------------------------------------------------- fixtures


def _run_csv(kind: str, periods: int = 2 * 24 * 60) -> bytes:
    idx = pd.date_range("2018-01-01", periods=periods, freq="1min")
    t = np.arange(periods)
    oat_c = 26 + 8 * np.sin(t / 720)  # degC in the source (cooling weather)
    oat_f = oat_c * 9 / 5 + 32
    rat = np.full(periods, 72.0)
    damper = np.full(periods, 0.016)  # held at the lbnl-sdahu template's 1.6 % design minimum
    if kind == "damper":
        damper = np.full(periods, 1.0)
    mat = damper * oat_f + (1 - damper) * rat
    valve = np.clip(0.5 + 0.4 * np.sin(t / 200), 0, 1)
    sat = mat - valve * 15
    if kind == "leak":
        # cooling while the valve is nearly shut; the valve never goes below 0.1, so gate at 0.2
        # (a < 0.05 gate never fired and made this run byte-identical to the fault-free one)
        sat = sat - 6 * (valve < 0.2)
    df = pd.DataFrame(
        {
            "Datetime": idx.strftime("%Y-%m-%d %H:%M:%S"),
            "CHWC_VLV_DM": valve,
            "MA_TEMP": mat,
            "OA_DMPR_DM": damper,
            "OA_TEMP": oat_c,
            "RA_TEMP": rat,
            "SA_TEMP": sat,
            "SA_SPSPT": -400.25,
            "SF_CS": (t // 20) % 2,  # fan cycles 20 min on / 20 min off -> 50 % duty
            "JUNK_COL": 1.0,  # unmapped: pruned at read
        }
    )
    return df.to_csv(index=False).encode()


def _zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, kind in (
            ("AHU_ff.csv", "ok"),
            ("AHU_damper.csv", "damper"),
            ("AHU_leak.csv", "leak"),
        ):
            z.writestr(f"TEST/{name}", _run_csv(kind))
    return buf.getvalue()


def _file(name, body, *, archive=None, members=None):
    f = {
        "name": name,
        "url": BASE + name,
        "size": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "pinned": True,
        "etag": None,
        "archive": archive,
    }
    if members is not None:
        f["members"] = members
    return f


def _issue(iid, columns, handling, **extra) -> dict:
    d = {
        "id": iid,
        "title": f"test issue {iid}",
        "columns": columns,
        "evidence": "the column reads -400.25 in 3 of 3 runs",
        "contradicts": {"document": "Test Lab inventory, Table 2", "citation": "doi:10.0000/test"},
        "handling": handling,
        "handling_note": "what the test ingester does",
    }
    d.update(extra)
    return d


def _ahu_entry_dict(zbytes, **over) -> dict:
    members = ["TEST/AHU_ff.csv", "TEST/AHU_damper.csv", "TEST/AHU_leak.csv"]
    d = {
        "id": "test-ahu",
        "title": "Test AHU runs",
        "summary": "three synthetic runs",
        "publisher": "Test Lab",
        "citation": "Test Lab (2025). Test AHU runs.",
        "dois": ["10.0000/test"],
        "landing_url": "https://example.org/ds",
        "licence": "CC-BY-4.0",
        "access": "open",
        "verified_on": "2026-09-26",
        "kind": "simulated",
        "labeled_faults": True,
        "labels": {"targets": {"outdoor_air_fraction": "damper", "leaking_valve": "valve_leak"}},
        "files": [_file("test.zip", zbytes, archive="zip", members=members)],
        "subsets": {
            "default": {
                "files": "all",
                "runs": ["fault_free", "damper"],
                "store_bytes_estimate": 1_000_000,
            },
            "full": {"files": "all", "runs": "all", "store_bytes_estimate": 2_000_000},
        },
        "ingest": {
            "adapter": "wide_csv",
            "facility": "ds-test-ahu",
            "timestamp": "Datetime",
            "resample": "15min",
            "mapping": "lbnl_sdahu.json",
            "units": {"oat": "degC"},
            "quirks": [
                {
                    "op": "mask",
                    "action": "fix",
                    "columns": ["SA_SPSPT"],
                    "lt": -100,
                    "note": "p",
                    "issue": "placeholder-setpoint",
                },
                {
                    "op": "annotate",
                    "action": "annotate",
                    "note": "left in place",
                    "issue": "odd-units",
                },
            ],
            "runs": [
                {
                    "id": "fault_free",
                    "file": "test.zip",
                    "member": members[0],
                    "equip": "AHU",
                    "class": "AHU",
                    "label": "",
                },
                {
                    "id": "damper",
                    "file": "test.zip",
                    "member": members[1],
                    "equip": "AHU",
                    "class": "AHU",
                    "label": "damper",
                },
                {
                    "id": "leak",
                    "file": "test.zip",
                    "member": members[2],
                    "equip": "AHU",
                    "class": "AHU",
                    "label": "valve_leak",
                },
            ],
            "derived": [
                {
                    "op": "splice",
                    "base": "fault_free",
                    "fault": "damper",
                    "onset": "2018-01-02",
                    "equip": "AHU",
                    "class": "AHU",
                }
            ],
        },
        "suggested_analyses": {"config_template": "lbnl-sdahu.json"},
        "known_issues": ["synthetic"],
        "data_issues": [
            _issue("placeholder-setpoint", ["SA_SPSPT"], "fix"),
            _issue("odd-units", ["SA_CFM"], "annotate"),
        ],
    }
    d.update(over)
    assert validate_catalog({"schema": 1, "datasets": [d]}) == []
    return d


class FakeResponse:
    def __init__(self, body, url, status=200):
        self._buf = io.BytesIO(body)
        self.status = status
        self.headers = {"Content-Length": str(len(body)), "ETag": '"x"'}
        self._url = url

    def read(self, n=-1):
        return self._buf.read(n)

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    def __init__(self, files: dict):
        self.files = files
        self.calls = []

    def open(self, req, timeout=None):
        url = req.full_url
        self.calls.append(url)
        return FakeResponse(self.files[url], url)


@pytest.fixture
def ahu(tmp_path, monkeypatch):
    zb = _zip_bytes()
    entry = DatasetEntry.from_dict(_ahu_entry_dict(zb))
    opener = FakeOpener({BASE + "test.zip": zb})
    monkeypatch.setenv("CAMBER_DATA_DIR", str(tmp_path / "cache"))
    return entry, opener, tmp_path


# --------------------------------------------------------------------------- fetch


def test_fetch_downloads_verifies_records_manifest_then_skips(ahu):
    entry, opener, tmp = ahu
    seen = []
    res = _ops.fetch_dataset(entry, opener=opener, progress=lambda n, d, t: seen.append((n, d)))
    assert res.data_dir == str(tmp / "cache")  # $CAMBER_DATA_DIR honoured
    assert res.files[0]["skipped"] is False and res.downloaded_bytes == entry.files[0]["size"]
    assert seen[-1] == ("test.zip", entry.files[0]["size"])
    man = _paths.read_manifest(res.data_dir)["test-ahu"]
    assert man["files"]["test.zip"]["sha256"] == entry.files[0]["sha256"]
    assert man["subsets"] == ["default"] and not res.acknowledged
    again = _ops.fetch_dataset(entry, opener=opener)
    assert again.files[0]["skipped"] and len(opener.calls) == 1
    assert _paths.read_acknowledgements(res.data_dir) == []


def test_fetch_rejects_a_changed_upstream_file(ahu):
    entry, _opener, _tmp = ahu
    bad = FakeOpener({BASE + "test.zip": b"not the pinned bytes"})
    with pytest.raises(ChecksumMismatch):
        _ops.fetch_dataset(entry, opener=bad)


def test_fetch_unpinned_file_warns(ahu):
    entry, opener, _tmp = ahu
    d = entry.as_dict()
    d["files"][0].update(sha256=None, size=None, pinned=False)
    res = _ops.fetch_dataset(DatasetEntry.from_dict(d), opener=opener)
    assert "not pinned" in res.warnings[0]


def test_fetch_disk_precheck(ahu, monkeypatch):
    entry, opener, _tmp = ahu
    import shutil
    from collections import namedtuple

    du = namedtuple("du", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda p: du(10, 10, 1))
    with pytest.raises(InsufficientSpace):
        _ops.fetch_dataset(entry, opener=opener)


def test_research_only_gate_ledger_and_banner(ahu):
    entry, opener, tmp = ahu
    d = entry.as_dict()
    d.update(licence="CC-BY-NC-ND-4.0", access="research_only")
    ro = DatasetEntry.from_dict(d)
    with pytest.raises(PermissionError, match="accept_noncommercial"):
        _ops.fetch_dataset(ro, opener=opener)
    assert opener.calls == []  # nothing downloaded before the gate
    res = _ops.fetch_dataset(ro, opener=opener, accept_noncommercial=True)
    assert res.acknowledged
    ledger = _paths.read_acknowledgements(res.data_dir)
    assert ledger[0]["dataset_id"] == "test-ahu" and ledger[0]["licence"] == "CC-BY-NC-ND-4.0"
    store = str(tmp / "store")
    _ingest.ingest_dataset(ro, store)
    meta = ParquetStore(store).facilities_meta()["ds-test-ahu"]["dataset"]
    assert meta["redistribution"] == "prohibited" and meta["acknowledgement"]
    cfg = _ops.build_config(ro, store)
    res = run_config(cfg)
    assert RESEARCH_ONLY_BANNER in res.report.to_html()


def _research_only(entry, licence="CC-BY-NC-4.0"):
    d = entry.as_dict()
    d.update(licence=licence, access="research_only")
    return DatasetEntry.from_dict(d)


def test_research_only_ack_is_in_ledger_and_manifest_before_any_download(ahu):
    entry, _opener, _tmp = ahu
    ro = _research_only(entry)

    class Boom(FakeOpener):
        def open(self, req, timeout=None):
            raise OSError("network down")

    with pytest.raises(Exception):  # noqa: B017 - the download fails, the acceptance stands
        _ops.fetch_dataset(ro, opener=Boom({}), accept_noncommercial=True)
    root = _paths.data_dir()
    ledger = _paths.read_acknowledgements(root)
    assert [r["via"] for r in ledger] == ["fetch"]
    assert ledger[0]["statement"].startswith("accepted: research / non-commercial")
    man = _paths.read_manifest(root)["test-ahu"]
    assert man["acknowledged_licence"] == "CC-BY-NC-4.0" and man["acknowledged_at"]


def test_research_only_every_fetch_needs_the_flag_and_every_acceptance_is_logged(ahu):
    entry, opener, _tmp = ahu
    ro = _research_only(entry)
    _ops.fetch_dataset(ro, opener=opener, accept_noncommercial=True)
    with pytest.raises(PermissionError):  # an earlier acceptance is not a standing licence
        _ops.fetch_dataset(ro, opener=opener)
    _ops.fetch_dataset(ro, opener=opener, accept_noncommercial=True)
    assert len(_paths.read_acknowledgements(_paths.data_dir())) == 2


def test_research_only_ingest_needs_an_acknowledgement_of_the_current_licence(ahu):
    entry, opener, tmp = ahu
    _ops.fetch_dataset(entry, opener=opener)  # fetched while the entry was open
    ro = _research_only(entry)
    store = str(tmp / "store")
    with pytest.raises(PermissionError, match="ingest it"):
        _ingest.ingest_dataset(ro, store)
    # an acknowledgement of a different licence does not carry over
    _ops.fetch_dataset(
        _research_only(entry, "CC-BY-NC-SA-4.0"), opener=opener, **{"accept_noncommercial": True}
    )
    with pytest.raises(PermissionError):
        _ingest.ingest_dataset(ro, store)
    res = _ingest.ingest_dataset(ro, store, accept_noncommercial=True)
    assert not res.skipped
    meta = ParquetStore(store).facilities_meta()["ds-test-ahu"]["dataset"]
    assert meta["access"] == "research_only" and meta["redistribution"] == "prohibited"
    assert meta["acknowledged_licence"] == "CC-BY-NC-4.0"
    vias = [r["via"] for r in _paths.read_acknowledgements(_paths.data_dir())]
    assert vias == ["fetch", "ingest"]


def test_research_only_banner_on_every_report_built_from_the_store(ahu):
    from camber._provenance import PROVENANCE_ATTR
    from camber.report.dashboard import build_dashboard
    from camber.report.site import build_site_report
    from camber.resolve import StoreEquipRef, resolve

    entry, opener, tmp = ahu
    ro = _research_only(entry)
    _ops.fetch_dataset(ro, opener=opener, accept_noncommercial=True)
    store = str(tmp / "store")
    _ingest.ingest_dataset(ro, store)
    st = ParquetStore(store)
    frame = st.read_role_frame(facility_id="ds-test-ahu", equip="AHU__fault_free")
    assert frame.attrs[PROVENANCE_ATTR][0]["access"] == "research_only"
    ref = StoreEquipRef("AHU__fault_free", "AHU", "ds-test-ahu", store)
    resolved = resolve(ref, None, [Role.OAT, Role.SUPPLY_AIR_TEMP], resample="1h")
    assert resolved.attrs[PROVENANCE_ATTR][0]["redistribution"] == "prohibited"
    # site report + dashboard: the banner comes from the frame when no data_sources are passed
    for html in (
        build_dashboard(frame, sections=()),
        build_site_report(frame, sections=()),
    ):
        assert RESEARCH_ONLY_BANNER in html
    # ... and an explicit empty list still means "no provenance block"
    assert RESEARCH_ONLY_BANNER not in build_dashboard(frame, sections=(), data_sources=[])
    # audit (run_config) and the config-level data_sources used by the drift / RCx CLI paths
    from camber.config import data_sources

    cfg = _ops.build_config(ro, store)
    assert run_config(cfg).report is not None
    assert RESEARCH_ONLY_BANNER in run_config(cfg).report.to_html()
    assert data_sources(cfg)[0]["access"] == "research_only"


def test_open_data_frames_carry_no_banner(ahu):
    from camber.report.dashboard import build_dashboard

    entry, opener, tmp = ahu
    _ops.fetch_dataset(entry, opener=opener)
    store = str(tmp / "store")
    _ingest.ingest_dataset(entry, store)
    frame = ParquetStore(store).read_role_frame(facility_id="ds-test-ahu", equip="AHU__fault_free")
    html = build_dashboard(frame, sections=())
    assert "CC-BY-4.0" in html and RESEARCH_ONLY_BANNER not in html


# --------------------------------------------------------------------------- ingest


def test_ingest_namespaces_runs_labels_splice_units_quirks_and_duty(ahu):
    entry, opener, tmp = ahu
    _ops.fetch_dataset(entry, opener=opener)
    store = ParquetStore(str(tmp / "store"))
    res = _ingest.ingest_dataset(entry, store)
    assert res.facilities == ["ds-test-ahu"] and not res.skipped
    eq = store.equipment()["ds-test-ahu"]
    assert eq == {"AHU__fault_free": "AHU", "AHU__damper": "AHU", "AHU__onset_damper": "AHU"}
    full_meta = store.facilities_meta()["ds-test-ahu"]
    lifecycle = {"state", "created_at", "state_changed_at", "display_name", "owner", "portfolio"}
    # provenance namespaced under one key; the rest is the registry-v2 lifecycle record
    assert set(full_meta) == {"name", "dataset", "notes"} | lifecycle
    assert full_meta["state"] == "active"  # automated registration starts active
    meta = full_meta["dataset"]
    assert meta["labels"] == {"AHU__fault_free": "", "AHU__damper": "damper"}
    assert meta["onsets"]["AHU__onset_damper"]["onset"] == "2018-01-02"
    assert meta["licence"] == "CC-BY-4.0" and meta["content_hash"] == res.content_hash
    assert any(n.startswith("annotate:") for n in res.notes)
    ff = store.read_role_frame(facility_id="ds-test-ahu", equip="AHU__fault_free")
    assert pd.infer_freq(ff.index) == "15min"
    assert ff[Role.OAT].min() > 60  # degC -> degF (source min 18 C)
    assert ff[Role.COOL_VALVE].max() > 1.5  # fraction -> percent
    assert ff[Role.SUPPLY_FAN_SPEED].mean() == pytest.approx(50.0, abs=5)  # SF_CS is the speed
    assert Role.DUCT_STATIC_SP not in ff.columns  # the -400.25 placeholder was masked away
    onset = store.read_role_frame(facility_id="ds-test-ahu", equip="AHU__onset_damper")
    assert onset.loc[:"2018-01-01 23:45", Role.OA_DAMPER].max() < 100
    assert onset.loc["2018-01-02":, Role.OA_DAMPER].min() == pytest.approx(100.0)


def test_ingest_is_idempotent_force_reingests_and_subset_change_replaces(ahu):
    entry, opener, tmp = ahu
    _ops.fetch_dataset(entry, opener=opener)
    store = ParquetStore(str(tmp / "store"))
    first = _ingest.ingest_dataset(entry, store)
    again = _ingest.ingest_dataset(entry, store)
    assert again.skipped and again.facilities == ["ds-test-ahu"]
    store.register_facility("ds-test-ahu", lifecycle={"state": "active"})  # someone else's key
    forced = _ingest.ingest_dataset(entry, store, force=True)
    assert forced.rows == first.rows  # replaced, not appended
    assert store.facilities_meta()["ds-test-ahu"]["lifecycle"] == {"state": "active"}
    assert len(store.read_long(facility_id="ds-test-ahu")) == first.rows
    full = _ingest.ingest_dataset(entry, store, subset="full")
    assert full.equipment == 4 and full.content_hash != first.content_hash
    assert "AHU__leak" in store.equipment()["ds-test-ahu"]


def test_no_corrections_ingests_the_published_data_and_records_the_mode(ahu):
    entry, opener, tmp = ahu
    _ops.fetch_dataset(entry, opener=opener)
    store = ParquetStore(str(tmp / "store"))
    fixed = _ingest.ingest_dataset(entry, store)
    ff = store.read_role_frame(facility_id="ds-test-ahu", equip="AHU__fault_free")
    assert Role.DUCT_STATIC_SP not in ff.columns  # the fix masked the placeholder
    meta = store.facilities_meta()["ds-test-ahu"]["dataset"]
    assert meta["corrections"] == "applied" and fixed.corrections
    assert [i["id"] for i in meta["data_issues"]] == ["placeholder-setpoint", "odd-units"]
    raw = _ingest.ingest_dataset(entry, store, corrections=False)
    assert not raw.skipped and raw.content_hash != fixed.content_hash  # mode changes the hash
    assert not raw.corrections and any(n.startswith("fix skipped:") for n in raw.notes)
    ff = store.read_role_frame(facility_id="ds-test-ahu", equip="AHU__fault_free")
    assert ff[Role.DUCT_STATIC_SP].median() == pytest.approx(-400.25)  # as published
    assert store.facilities_meta()["ds-test-ahu"]["dataset"]["corrections"].startswith("skipped")
    assert _ingest.ingest_dataset(entry, store, corrections=False).skipped
    assert not _ingest.ingest_dataset(entry, store).skipped  # back to corrected: re-ingests


def test_excluded_runs_are_ingested_but_never_scored(ahu):
    entry, opener, tmp = ahu
    d = entry.as_dict()
    d["data_issues"].append(
        {
            "id": "leak-not-a-leak",
            "title": "the leak run carries no leak",
            "columns": ["CHWC_VLV_DM"],
            "evidence": "identical to fault-free in 100% of rows",
            "contradicts": {"document": "inventory Table 3", "citation": "doi:10.0000/test"},
            "handling": "exclude",
            "handling_note": "ingested for inspection; not scored",
            "exclude": {"runs": ["leak"]},
        }
    )
    next(r for r in d["ingest"]["runs"] if r["id"] == "leak")["exclude"] = "leak-not-a-leak"
    assert validate_catalog({"schema": 1, "datasets": [d]}) == []
    entry = DatasetEntry.from_dict(d)
    _ops.fetch_dataset(entry, opener=opener)
    store = ParquetStore(str(tmp / "store"))
    res = _ingest.ingest_dataset(entry, store, subset="full")
    meta = store.facilities_meta()["ds-test-ahu"]["dataset"]
    assert "AHU__leak" in store.equipment()["ds-test-ahu"] and res.equipment == 4
    assert "AHU__leak" not in meta["labels"]
    assert meta["excluded"] == {"AHU__leak": {"label": "valve_leak", "issue": "leak-not-a-leak"}}
    scored = _ops.score_dataset(entry, store)
    assert {r["equip"] for r in scored["records"]} == {"AHU__fault_free", "AHU__damper"}


def test_read_raw_run_pins_the_timestamp_format_and_applies_transforms(tmp_path):
    from camber.model.mapping import MappingProvider

    csv = tmp_path / "run.csv"
    pd.DataFrame(
        {
            "Datetime": [
                "01/02/2018 00:00",
                "01/02/2018 00:01",
                "13/02/2018 00:02",
                "01/02/2018 00:03",
            ],
            "SYS_CTL": [0, 1, 2, 2],
            "CSA_CFM": [100.0, 200.0, 300.0, None],
            "HSA_CFM": [10.0, 20.0, 30.0, 40.0],
            "OA_TEMP": [50.0, 51.0, 52.0, 53.0],
        }
    ).to_csv(csv, index=False)
    mapping = MappingProvider.from_dict(
        {"aliases": {"SYS_CTL": "occupancy", "SA_CFM_TOTAL": "airflow", "OA_TEMP": "oat"}}
    )
    spec = {
        "timestamp": "Datetime",
        "timestamp_format": "%m/%d/%Y %H:%M",
        "recode": {"SYS_CTL": {"2": 0}},
        "derive": [
            {"column": "SA_CFM_TOTAL", "sum": ["CSA_CFM", "HSA_CFM"]},
            {"column": "CSF_ON", "above": ["CSA_CFM", 150]},
        ],
    }
    raw, notes = _ingest.read_raw_run(str(csv), mapping, spec)
    # month-first: Jan 2nd, never Feb 1st; "13/02" is not a month-first stamp -> dropped
    assert list(raw.index) == list(
        pd.to_datetime(["2018-01-02 00:00", "2018-01-02 00:01", "2018-01-02 00:03"])
    )
    assert raw["SYS_CTL"].tolist() == [0, 1, 0]  # setback (2) is not occupied
    assert raw["SA_CFM_TOTAL"].tolist()[:2] == [110.0, 220.0]
    assert np.isnan(raw["SA_CFM_TOTAL"].iloc[2])  # a missing deck flow is not a zero
    assert raw["CSF_ON"].tolist()[:2] == [0.0, 1.0] and np.isnan(raw["CSF_ON"].iloc[2])
    assert notes == []
    frame, _, _ = _ingest.read_wide_run(str(csv), mapping, {**spec, "resample": None})
    assert set(frame.columns) == {Role.OCCUPANCY, Role.AIRFLOW, Role.OAT}


def test_crash_between_staging_and_swap_keeps_old_data(ahu, monkeypatch):
    entry, opener, tmp = ahu
    _ops.fetch_dataset(entry, opener=opener)
    store = ParquetStore(str(tmp / "store"))
    first = _ingest.ingest_dataset(entry, store)

    def boom(*a, **k):
        raise RuntimeError("power cut")

    monkeypatch.setattr(_ingest, "_swap_in", boom)
    with pytest.raises(RuntimeError):
        _ingest.ingest_dataset(entry, store, subset="full")
    assert len(store.read_long(facility_id="ds-test-ahu")) == first.rows
    assert not os.listdir(os.path.join(store.root, "_staging"))  # staging cleaned up


def test_ingest_needs_fetched_files(ahu):
    entry, _opener, tmp = ahu
    with pytest.raises(FileNotFoundError, match="camber datasets fetch test-ahu"):
        _ingest.ingest_dataset(entry, str(tmp / "store"))
    with pytest.raises(KeyError):
        _ingest.ingest_dataset(entry, str(tmp / "store"), subset="nope")


def test_ingest_rejects_a_tampered_download(ahu):
    entry, opener, tmp = ahu
    res = _ops.fetch_dataset(entry, opener=opener)
    man = _paths.read_manifest(res.data_dir)
    man["test-ahu"]["files"]["test.zip"]["sha256"] = "0" * 64
    _paths.write_manifest(res.data_dir, man)
    with pytest.raises(ValueError, match="re-fetch"):
        _ingest.ingest_dataset(entry, str(tmp / "store"))


def test_splice_skipped_when_its_runs_are_not_in_the_subset(ahu):
    entry, opener, tmp = ahu
    d = entry.as_dict()
    d["subsets"]["default"]["runs"] = ["fault_free", "leak"]
    e2 = DatasetEntry.from_dict(d)
    _ops.fetch_dataset(e2, opener=opener)
    res = _ingest.ingest_dataset(e2, str(tmp / "store"))
    assert any("splice damper skipped" in n for n in res.notes) or res.equipment == 2
    assert res.equipment == 2


# --------------------------------------------------------------------------- config + score


def test_config_run_and_score(ahu):
    entry, opener, tmp = ahu
    _ops.fetch_dataset(entry, opener=opener)
    store = str(tmp / "store")
    _ingest.ingest_dataset(entry, store, subset="full")
    out = str(tmp / "cfg.json")
    cfg = _ops.build_config(entry, store, out=out)
    assert json.load(open(out))["source"] == cfg["source"]
    assert cfg["source"]["facility_id"] == "ds-test-ahu" and cfg["source"]["store"] == store
    res = run_config(cfg)
    oaf = {f.equip: f.severity for f in res.findings if f.rule == "outdoor_air_fraction"}
    assert oaf["AHU__damper"] in ("warn", "fault")
    assert oaf["AHU__fault_free"] not in ("warn", "fault")
    sc = _ops.score_dataset(entry, store)
    assert sc["n"] == 3 and sc["suite"] == ["leaking_valve", "outdoor_air_fraction"]
    assert sc["per_detector"]["outdoor_air_fraction"]["tp"] == 1
    assert {r["equip"] for r in sc["records"]} == {"AHU__fault_free", "AHU__damper", "AHU__leak"}
    # findings from a file, and an explicit rule suite
    fj = tmp / "findings.json"
    json.dump([f.as_dict() for f in res.findings], open(fj, "w"), default=str)
    sc2 = _ops.score_dataset(entry, store, findings=str(fj), rules=["outdoor_air_fraction"])
    assert sc2["overall"]["tp"] == 1 and sc2["suite"] == ["outdoor_air_fraction"]


def test_score_errors(ahu):
    entry, opener, tmp = ahu
    with pytest.raises(ValueError, match="not ingested"):
        _ops.score_dataset(entry, str(tmp / "store"))
    d = entry.as_dict()
    d["labeled_faults"] = False
    with pytest.raises(ValueError, match="no fault labels"):
        _ops.score_dataset(DatasetEntry.from_dict(d), str(tmp / "store"))
    d["suggested_analyses"] = {}
    with pytest.raises(KeyError):
        _ops.build_config(DatasetEntry.from_dict(d), str(tmp / "store"))


def test_status_and_remove(ahu):
    entry, opener, tmp = ahu
    _ops.fetch_dataset(entry, opener=opener)
    store = str(tmp / "store")
    _ingest.ingest_dataset(entry, store)
    st = _ops.dataset_status([entry], store=store)[0]
    assert st["fetched"] == {"default": True, "full": True}
    assert st["bytes_on_disk"] > 0 and "ds-test-ahu" in st["ingested"]
    with pytest.raises(ValueError):
        _ops.remove_dataset(entry, purge_store=True)
    res = _ops.remove_dataset(entry, store=store, purge_store=True)
    assert res["freed_bytes"] > 0 and res["facilities_dropped"] == ["ds-test-ahu"]
    assert ParquetStore(store).facilities() == []
    st = _ops.dataset_status([entry])[0]
    assert st["fetched"]["default"] is False and st["bytes_on_disk"] == 0


def test_purge_tombstones_and_reingest_of_the_same_dataset_reclaims(ahu):
    """A purged dataset facility is tombstoned; re-ingesting the *same* dataset reclaims its id."""
    from camber.store import FacilityRegistry

    entry, opener, tmp = ahu
    _ops.fetch_dataset(entry, opener=opener)
    store = str(tmp / "store")
    _ingest.ingest_dataset(entry, store)
    _ops.remove_dataset(entry, store=store, purge_store=True)
    reg = FacilityRegistry(store)
    assert reg.tombstones()["ds-test-ahu"]["dataset_id"] == entry.id
    with pytest.raises(ValueError, match="tombstoned"):
        reg.register("ds-test-ahu", name="some other building")
    _ops.fetch_dataset(entry, opener=opener)
    res = _ingest.ingest_dataset(entry, store)
    assert res.facilities == ["ds-test-ahu"] and "ds-test-ahu" not in reg.tombstones()
    assert reg.get("ds-test-ahu")["state"] == "active"


def test_ingest_into_a_portfolio_workspace_is_audited(ahu):
    from camber.portfolio import Portfolio

    entry, opener, tmp = ahu
    _ops.fetch_dataset(entry, opener=opener)
    pf = Portfolio.init(tmp / "ws")
    _ingest.ingest_dataset(entry, pf.store_root)
    assert pf.facility("ds-test-ahu")["state"] == "active"
    rec = pf.audit_log(facility_id="ds-test-ahu")
    assert [r["action"] for r in rec] == ["facility.register"] and rec[0]["to_state"] == "active"


# --------------------------------------------------------------------------- BDG2 adapter


def _bdg2_files():
    idx = pd.date_range("2016-01-01", periods=24 * 90, freq="1h")
    meta = pd.DataFrame(
        {
            "building_id": ["Ant_office_A", "Ant_office_B", "Bee_lab_C"],
            "site_id": ["Ant", "Ant", "Bee"],
            "timezone": ["US/Eastern", "US/Eastern", "US/Pacific"],
        }
    )
    t = np.arange(len(idx))
    temp_c = 15 + 10 * np.sin(t / (24 * 30))
    weather = pd.concat(
        [
            pd.DataFrame({"timestamp": idx, "site_id": s, "airTemperature": temp_c})
            for s in ("Ant", "Bee")
        ]
    )
    elec = pd.DataFrame(
        {
            "timestamp": idx,
            "Ant_office_A": 50 + t % 24,
            "Ant_office_B": 30.0,
            "Bee_lab_C": 80.0,
        }
    )
    chw = pd.DataFrame({"timestamp": idx, "Ant_office_A": np.maximum(0, temp_c - 12) * 20})

    def b(df):
        return df.to_csv(index=False).encode()

    return {
        "metadata.csv": b(meta),
        "weather.csv": b(weather),
        "cleaned/electricity_cleaned.csv": b(elec),
        "cleaned/chilledwater_cleaned.csv": b(chw),
    }


def _bdg2_entry(files):
    d = copy.deepcopy(ds.get("bdg2").as_dict())
    d["id"] = "test-bdg2"
    d["files"] = [_file(n, body) for n, body in files.items()]
    d["subsets"] = {
        "default": {
            "files": list(files),
            "sites": ["Ant"],
            "meters": ["electricity", "chilledwater"],
            "max_buildings_per_site": 1,
            "include_buildings": ["Ant_office_A"],
            "store_bytes_estimate": 1_000_000,
        },
        "full": {
            "files": "all",
            "sites": "all",
            "meters": ["electricity", "chilledwater"],
            "max_buildings_per_site": None,
            "store_bytes_estimate": 2_000_000,
        },
    }
    d["ingest"]["facility"] = "ds-tb"
    assert validate_catalog({"schema": 1, "datasets": [d]}) == []
    return DatasetEntry.from_dict(d)


def test_bdg2_one_facility_per_site_weather_in_f_and_mv(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMBER_DATA_DIR", str(tmp_path / "cache"))
    files = _bdg2_files()
    entry = _bdg2_entry(files)
    _ops.fetch_dataset(entry, opener=FakeOpener({BASE + n: b for n, b in files.items()}))
    store = ParquetStore(str(tmp_path / "store"))
    res = _ingest.ingest_dataset(entry, store)
    assert res.facilities == ["ds-tb-ant"]
    eq = store.equipment()["ds-tb-ant"]
    assert eq == {
        "weather": "WEATHER",
        "Ant_office_A__electricity": "ELECTRICITY_METER",
        "Ant_office_A__chilledwater": "CHILLEDWATER_METER",
    }
    meta = store.facilities_meta()["ds-tb-ant"]["dataset"]
    assert meta["site_id"] == "Ant" and meta["timezone"] == "US/Eastern"
    assert meta["licence"] == "CC-BY-SA-4.0"
    w = store.read_role_frame(facility_id="ds-tb-ant", equip="weather")
    assert w[Role.OAT].min() > 40  # degC -> degF
    el = store.read_role_frame(facility_id="ds-tb-ant", equip="Ant_office_A__electricity")
    assert Role.POWER in el.columns
    cfg = _ops.build_config(entry, store)
    assert cfg["source"]["facility_id"] == "ds-tb-ant"  # first ingested site
    cfg["mv"][0]["period"] = ["2016-01-01", "2016-03-31"]
    cfg["mv"][1]["period"] = ["2016-01-01", "2016-03-31"]
    out = run_config(cfg)
    mv = {f.equip: f for f in out.findings if f.rule == "mv_baseline"}
    assert mv["Ant_office_A__chilledwater"].metrics["model"] in ("3PC", "4P", "5P", "2P")
    assert "mv:CHILLEDWATER_METER" in out.rules_run
    assert "Share-alike" in out.report.to_text()
    # the full subset adds the other site; shrinking back drops it again
    full = _ingest.ingest_dataset(entry, store, subset="full")
    assert full.facilities == ["ds-tb-ant", "ds-tb-bee"]
    _ingest.ingest_dataset(entry, store)
    assert store.facilities() == ["ds-tb-ant"]


# --------------------------------------------------------------------------- CLI


@pytest.fixture
def cli_catalog(ahu, monkeypatch):
    entry, opener, tmp = ahu
    import camber.datasets._ops as ops_mod

    monkeypatch.setattr(ds, "_entries", lambda: (entry,))
    real = ops_mod.fetch_dataset
    monkeypatch.setattr(
        ds, "fetch_dataset", lambda e, **kw: real(e, **{**kw, "opener": kw.get("opener") or opener})
    )
    return entry, opener, tmp


def test_cli_full_flow(cli_catalog, capsys):
    from camber.cli import main

    _entry, _opener, tmp = cli_catalog
    store = str(tmp / "store")
    assert main(["datasets", "list"]) == 0
    assert main(["datasets", "list", "--json", "--labeled", "--kind", "simulated"]) == 0
    assert main(["datasets", "info", "test-ahu"]) == 0
    out = capsys.readouterr().out
    assert "data issues in the published data" in out and "[fix] test issue" in out
    assert "--no-corrections" in out and "/    1.0 MB" in out  # per-subset store estimate
    assert main(["datasets", "info", "test-ahu", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["data_issues"][0]["id"] == "placeholder-setpoint"
    assert main(["datasets", "fetch"]) == 1  # nothing named
    assert main(["datasets", "fetch", "test-ahu"]) == 0
    assert "Please cite: Test Lab (2025)" in capsys.readouterr().out
    assert main(["datasets", "fetch", "--all", "--quiet"]) == 0
    assert main(["datasets", "ingest", "--store", store]) == 1
    assert main(["datasets", "ingest", "test-ahu", "--store", store]) == 0
    assert "ingested" in capsys.readouterr().out
    assert main(["datasets", "ingest", "--all", "--store", store, "--quiet"]) == 0
    assert "skipped" in capsys.readouterr().out
    assert main(["datasets", "ingest", "test-ahu", "--store", store, "--no-corrections"]) == 0
    assert "published data as-is" in capsys.readouterr().out
    assert main(["datasets", "ingest", "test-ahu", "--store", store, "--quiet"]) == 0
    assert main(["datasets", "status", "--store", store]) == 0
    assert main(["datasets", "status", "--json"]) == 0
    cfg = str(tmp / "c.json")
    assert main(["datasets", "config", "test-ahu", "--store", store, "--out", cfg]) == 0
    assert main(["datasets", "config", "test-ahu", "--store", store]) == 0
    assert main(["datasets", "score", "test-ahu", "--store", store]) == 0
    out = capsys.readouterr().out
    assert "overall detection" in out and "AHU__damper" in out
    assert main(["datasets", "score", "test-ahu", "--store", store, "--json"]) == 0
    assert main(["datasets", "remove", "test-ahu", "--store", store, "--purge-store"]) == 0
    assert "dropped from the store" in capsys.readouterr().out
    assert main(["datasets", "info", "nope"]) == 1


def test_cli_exit_codes(cli_catalog, monkeypatch, capsys):
    from camber.cli import main

    entry, _opener, _tmp = cli_catalog
    bad = FakeOpener({BASE + "test.zip": b"changed upstream"})
    monkeypatch.setattr(
        ds, "fetch_dataset", lambda e, **kw: _ops.fetch_dataset(e, **{**kw, "opener": bad})
    )
    assert main(["datasets", "fetch", "test-ahu", "--quiet"]) == 2
    d = entry.as_dict()
    d.update(licence="CC-BY-NC-4.0", access="research_only")
    monkeypatch.setattr(ds, "_entries", lambda: (DatasetEntry.from_dict(d),))
    assert main(["datasets", "fetch", "test-ahu", "--quiet"]) == 3
    import shutil
    from collections import namedtuple

    du = namedtuple("du", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda p: du(10, 10, 1))
    monkeypatch.setattr(ds, "_entries", lambda: (entry,))
    assert main(["datasets", "fetch", "test-ahu", "--quiet"]) == 4
    assert "not enough disk" in capsys.readouterr().err
    # ingest warns (but proceeds) when the store's disk is smaller than the subset's estimate
    monkeypatch.setattr(ds, "ingest", lambda *a, **k: (_ for _ in ()).throw(KeyError("stop")))
    store = str(_tmp / "no" / "such" / "store")
    assert main(["datasets", "ingest", "test-ahu", "--store", store, "--subset", "full"]) == 1
    err = capsys.readouterr().err
    assert "needs about 2.0 MB in the store" in err and "(full)" in err


def test_cli_research_only_tier(cli_catalog, monkeypatch, capsys):
    from camber.cli import main

    entry, opener, tmp = cli_catalog
    ro = _research_only(entry)
    monkeypatch.setattr(ds, "_entries", lambda: (entry, _renamed(ro, "test-nc")))
    assert main(["datasets", "list"]) == 0
    out = capsys.readouterr().out
    assert "research-only" in out and "open" in out and "--accept-noncommercial" in out
    assert main(["datasets", "info", "test-nc"]) == 0
    assert "tier: research-only" in capsys.readouterr().out
    # --all is the open tier only: the research-only entry is not fetched
    assert main(["datasets", "fetch", "--all", "--quiet"]) == 0
    out = capsys.readouterr().out
    assert "fetching test-ahu" in out and "test-nc" not in out
    # --licence all without the acknowledgement: refused before anything is downloaded
    calls = len(opener.calls)
    assert main(["datasets", "fetch", "--all", "--licence", "all", "--quiet"]) == 3
    assert "--accept-noncommercial" in capsys.readouterr().err and len(opener.calls) == calls
    # a named research-only id with an open one: the gate runs first, nothing is fetched
    assert main(["datasets", "fetch", "test-ahu", "test-nc", "--quiet"]) == 3
    assert "Nothing was downloaded" in capsys.readouterr().err
    # --accept-noncommercial alone does not widen --all
    assert main(["datasets", "fetch", "--all", "--accept-noncommercial", "--quiet"]) == 0
    assert "open tier only" in capsys.readouterr().out
    assert _paths.read_acknowledgements(_paths.data_dir()) == []
    # both flags: research-only data too, acknowledged
    argv = ["datasets", "fetch", "--all", "--licence", "all", "--accept-noncommercial", "--quiet"]
    assert main(argv) == 0
    assert "licence acknowledged: CC-BY-NC-4.0" in capsys.readouterr().out
    store = str(tmp / "store")
    assert main(["datasets", "ingest", "test-nc", "--store", store, "--quiet"]) == 0
    assert "redistribution prohibited" in capsys.readouterr().out


def test_cli_no_env_var_bypasses_the_gate(cli_catalog, monkeypatch):
    from camber.cli import main

    entry, _opener, _tmp = cli_catalog
    monkeypatch.setattr(ds, "_entries", lambda: (_research_only(entry),))
    for var in ("CAMBER_ACCEPT_NONCOMMERCIAL", "ACCEPT_NONCOMMERCIAL", "CAMBER_ACCEPT_LICENCE"):
        monkeypatch.setenv(var, "1")
    assert main(["datasets", "fetch", "test-ahu", "--quiet"]) == 3
    with pytest.raises(PermissionError):
        ds.fetch("test-ahu")


def _renamed(entry, new_id):
    d = entry.as_dict()
    d["id"] = new_id
    d["ingest"] = dict(d["ingest"], facility=f"ds-{new_id}")
    return DatasetEntry.from_dict(d)


def test_public_api_wrappers(ahu, monkeypatch):
    entry, opener, tmp = ahu
    monkeypatch.setattr(ds, "_entries", lambda: (entry,))
    res = ds.fetch("test-ahu", opener=opener)
    assert isinstance(res, ds.FetchResult) and res.as_dict()["dataset_id"] == "test-ahu"
    store = str(tmp / "store")
    ing = ds.ingest("test-ahu", store)
    assert isinstance(ing, ds.IngestResult) and ing.as_dict()["rows"] > 0
    assert ds.config_template("test-ahu", store)["source"]["facility_id"] == "ds-test-ahu"
    assert ds.score("test-ahu", store)["n"] == 2
    assert ds.status(store=store)[0]["id"] == "test-ahu"
    assert ds.remove("test-ahu")["dataset_id"] == "test-ahu"


def test_byte_identical_archive_runs_are_ingested_once(tmp_path, monkeypatch):
    """A publisher shipping one simulation under several labels (LBNL's single-duct AHU "leak
    severities" 010/025/040/050 are one file four times) must not become several scored cases."""
    buf = io.BytesIO()
    leak = _run_csv("leak")
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("TEST/AHU_ff.csv", _run_csv("ok"))
        z.writestr("TEST/AHU_damper.csv", _run_csv("damper"))
        z.writestr("TEST/AHU_leak.csv", leak)
        z.writestr("TEST/AHU_leak_copy.csv", leak)
    zb = buf.getvalue()
    d = _ahu_entry_dict(_zip_bytes())
    members = [
        "TEST/AHU_ff.csv",
        "TEST/AHU_damper.csv",
        "TEST/AHU_leak.csv",
        "TEST/AHU_leak_copy.csv",
    ]
    d["files"] = [_file("test.zip", zb, archive="zip", members=members)]
    d["ingest"]["runs"].append(
        {
            "id": "leak_copy",
            "file": "test.zip",
            "member": members[3],
            "equip": "AHU",
            "class": "AHU",
            "label": "valve_leak",
        }
    )
    assert validate_catalog({"schema": 1, "datasets": [d]}) == []
    entry = DatasetEntry.from_dict(d)
    monkeypatch.setenv("CAMBER_DATA_DIR", str(tmp_path / "cache"))
    _ops.fetch_dataset(entry, opener=FakeOpener({BASE + "test.zip": zb}))
    store = ParquetStore(str(tmp_path / "store"))
    res = _ingest.ingest_dataset(entry, store, subset="full")
    eq = store.equipment()["ds-test-ahu"]
    assert "AHU__leak" in eq and "AHU__leak_copy" not in eq
    meta = store.facilities_meta()["ds-test-ahu"]["dataset"]
    assert meta["duplicates"] == {"leak_copy": "leak"}
    assert list(meta["labels"].values()).count("valve_leak") == 1  # scored once, not twice
    assert any("leak_copy skipped: byte-identical to leak" in n for n in res.notes)
