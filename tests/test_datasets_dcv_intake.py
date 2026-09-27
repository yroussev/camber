"""The DCV / CO2 catalog entries: lbnl-b59, finnish-dcv, b4b-windesheim (no network, no real data).

Tiny synthetic fixtures shaped like each publisher's files exercise what these entries added to the
wide-CSV ingester: runs that join several per-quantity CSVs (``members``) through a run-level
mapping whose ``{n}`` / ``{z}`` placeholders the run's ``vars`` fill, verbatim ``equip_id`` names,
per-run source units, a table stacking several rooms (``where``), timestamps published in UTC or
with per-row UTC offsets moved onto the site's wall clock, plain (non-archive) CSV downloads, and
the shipped entries' own data-issue wiring (the gap-filled OA flow masked, the assumed-constant
valve excluded).
"""

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
from camber.datasets import _ingest, _paths  # noqa: E402
from camber.datasets._catalog import DatasetEntry, package_text, validate_catalog  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.store import ParquetStore  # noqa: E402

BASE = "https://example.org/ds/"


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


def _place(entry, files: dict, root) -> None:
    """Put the 'downloads' where a fetch would (the ingester re-hashes them)."""
    ddir = _paths.downloads_dir(str(root), entry.id)
    for name, body in files.items():
        path = os.path.join(ddir, *name.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(body)


def _entry(did, files, ingest, **over) -> dict:
    d = {
        "id": did,
        "title": did,
        "summary": "synthetic",
        "publisher": "Test Lab",
        "citation": "Test Lab (2026). doi:10.0000/test",
        "dois": ["10.0000/test"],
        "landing_url": "https://example.org/ds",
        "licence": "CC-BY-4.0",
        "access": "open",
        "verified_on": "2026-09-26",
        "kind": "real",
        "files": files,
        "subsets": {
            "default": {"files": "all", "runs": "all", "store_bytes_estimate": 1_000},
            "full": {"files": "all", "runs": "all", "store_bytes_estimate": 1_000},
        },
        "ingest": ingest,
    }
    d.update(over)
    return d


# --------------------------------------------------------------------------- B59-shaped


def _b59_zip() -> bytes:
    """Two RTUs split over per-quantity CSVs, zone CO2 and site weather, stamped in UTC.

    2 days of 1-minute data from 2020-04-09 00:00 UTC; the OA flow is a 'gap fill' (constant 111)
    before 2020-04-10 22:00 UTC and a measurement (500 + n) after.
    """
    idx = pd.date_range("2020-04-09", periods=2 * 24 * 60, freq="1min")
    stamps = idx.strftime("%Y-%m-%d %H:%M:%S")
    real = idx >= pd.Timestamp("2020-04-10 22:00")
    wave = 100.0 * np.sin(np.arange(len(idx)) / 240.0)  # a CO2 that moves (a flat one is "stuck")
    tables = {
        "rtu_sa_t.csv": {f"rtu_00{n}_sa_temp": 55.0 + n for n in (1, 2)},
        "rtu_ra_t.csv": {f"rtu_00{n}_ra_temp": 72.0 for n in (1, 2)},
        "rtu_oa_fr.csv": {
            f"rtu_00{n}_oa_flow_tn": np.where(real, 500.0 + n, 111.0) for n in (1, 2)
        },
        "rtu_oa_damper.csv": {f"rtu_00{n}_oadmpr_pct": 10.0 * n for n in (1, 2)},
        "zone_co2.csv": {"zone_022_co2": 450.0 + wave, "zone_045_co2": 600.0 + wave},
        "site_weather.csv": {"air_temp_set_1": 20.0},
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, cols in tables.items():
            df = pd.DataFrame({"date": stamps, **cols})
            z.writestr(f"Bldg59_clean data/{name}", df.to_csv(index=False))
        z.writestr("model.ttl", _TTL)
    return buf.getvalue()


# The publisher's Brick 1.1 shape: each RTU owns its temperature points; the OA flow and damper
# are named differently from the CSV columns (the catalog mapping overrides those).
_TTL = """@prefix bldg: <http://example.org/building#> .
@prefix brick1: <https://brickschema.org/schema/1.1/Brick#> .

bldg:RTU01 a brick1:Rooftop_Unit ;
    brick1:hasPoint bldg:rtu_001_sa_temp, bldg:rtu_001_ra_temp, bldg:rtu_001_oa_fr .
bldg:RTU02 a brick1:Rooftop_Unit ;
    brick1:hasPoint bldg:rtu_002_sa_temp, bldg:rtu_002_ra_temp, bldg:rtu_002_oa_fr .
bldg:rtu_001_sa_temp a brick1:Supply_Air_Temperature_Sensor .
bldg:rtu_002_sa_temp a brick1:Supply_Air_Temperature_Sensor .
bldg:rtu_001_ra_temp a brick1:Return_Air_Temperature_Sensor .
bldg:rtu_002_ra_temp a brick1:Return_Air_Temperature_Sensor .
bldg:rtu_001_oa_fr a brick1:Outdoor_Air_Flow_Rate .
bldg:rtu_002_oa_fr a brick1:Outdoor_Air_Flow_Rate .
"""


def _b59_dict(zb: bytes) -> dict:
    P = "Bldg59_clean data/"
    rtu_files = ("rtu_sa_t.csv", "rtu_ra_t.csv", "rtu_oa_fr.csv", "rtu_oa_damper.csv")
    members = [P + m for m in rtu_files] + [P + "zone_co2.csv", P + "site_weather.csv"]
    members.append("model.ttl")
    runs = [
        {
            "id": m[:-4],
            "file": "b59.zip",
            "member": P + m,
            "group": "brick",
            "equip": "RTU_unassigned",
            "class": "AHU",
            "label": "",
        }
        for m in rtu_files
    ]
    runs += [
        {
            "id": f"zone_{z}",
            "file": "b59.zip",
            "member": P + "zone_co2.csv",
            "mapping": "b59_zone.json",
            "vars": {"z": z},
            "equip": rtu,
            "equip_id": f"{rtu}_zone_{z}",
            "class": "VAV",
            "label": "",
        }
        for z, rtu in (("022", "RTU01"), ("045", "RTU02"))
    ]
    runs.append(
        {
            "id": "weather",
            "file": "b59.zip",
            "member": P + "site_weather.csv",
            "mapping": "b59_weather.json",
            "units": {"oat": "degC"},
            "equip": "weather",
            "equip_id": "weather",
            "class": "WEATHER",
            "label": "",
        }
    )
    ingest = {
        "adapter": "wide_csv",
        "facility": "ds-test-b59",
        "brick": {
            "file": "b59.zip",
            "member": "model.ttl",
            "equip_classes": {"Rooftop_Unit": "AHU"},
        },
        "timestamp": "date",
        "source_timezone": "UTC",
        "local_timezone": "America/Los_Angeles",
        "resample": "15min",
        "mapping": "b59_rtu.json",
        "units": {},
        "quirks": [
            {
                "op": "mask",
                "action": "fix",
                "columns": [f"rtu_00{n}_oa_flow_tn" for n in (1, 2)],
                "before": "2020-04-10 22:00",
                "runs": ["rtu_oa_fr"],
                "note": "gap fill",
                "issue": "oa-flow-gap-filled",
            }
        ],
        "runs": runs,
    }
    issue = {
        "id": "oa-flow-gap-filled",
        "title": "gap-filled OA flow",
        "columns": ["rtu_00N_oa_flow_tn"],
        "evidence": "the four units correlate 0.999 before the onset",
        "contradicts": {"document": "data table", "citation": "doi:10.0000/test"},
        "handling": "fix",
        "handling_note": "masked",
    }
    return _entry(
        "test-b59",
        [_file("b59.zip", zb, archive="zip", members=members)],
        ingest,
        data_issues=[issue],
    )


def test_brick_grouped_rtus_zone_vars_equip_ids_utc_clock_and_masked_gap_fill(
    tmp_path, monkeypatch
):
    zb = _b59_zip()
    d = _b59_dict(zb)
    assert validate_catalog({"schema": 1, "datasets": [d]}) == []
    entry = DatasetEntry.from_dict(d)
    monkeypatch.setenv("CAMBER_DATA_DIR", str(tmp_path / "cache"))
    _place(entry, {"b59.zip": zb}, tmp_path / "cache")
    store = ParquetStore(str(tmp_path / "store"))
    res = _ingest.ingest_dataset(entry, store)
    assert not res.warnings
    eq = store.equipment()["ds-test-b59"]
    assert eq == {
        "RTU01": "AHU",
        "RTU02": "AHU",
        "RTU01_zone_022": "VAV",
        "RTU02_zone_045": "VAV",
        "weather": "WEATHER",
    }
    r1 = store.read_role_frame(facility_id="ds-test-b59", equip="RTU01")
    r2 = store.read_role_frame(facility_id="ds-test-b59", equip="RTU02")
    # the Brick model split each per-quantity CSV by owner; the mapping placed the OA damper
    # and the misnamed OA flow, which the model does not name as the CSV does
    assert r1[Role.SUPPLY_AIR_TEMP].iloc[0] == pytest.approx(56.0)
    assert r2[Role.SUPPLY_AIR_TEMP].iloc[0] == pytest.approx(57.0)
    assert r2[Role.OA_DAMPER].iloc[0] == pytest.approx(20.0)
    # UTC -> Pacific daylight time (UTC-7): the first UTC stamp is 17:00 local the day before
    assert r1.index[0] == pd.Timestamp("2020-04-08 17:00")
    # the gap fill before 22:00 UTC (15:00 PDT) is masked; the measurement after it is kept
    oa = r1[Role.OA_AIRFLOW]
    assert oa.loc[:"2020-04-10 14:45"].isna().all()
    assert oa.loc["2020-04-10 15:00":].dropna().eq(501.0).all()
    z = store.read_role_frame(facility_id="ds-test-b59", equip="RTU02_zone_045")
    z22 = store.read_role_frame(facility_id="ds-test-b59", equip="RTU01_zone_022")
    assert list(z.columns) == [Role.CO2]
    assert (z[Role.CO2] - z22[Role.CO2]).abs().sub(150.0).abs().max() < 1e-6  # {z} picked its zone
    w = store.read_role_frame(facility_id="ds-test-b59", equip="weather")
    assert w[Role.OAT].iloc[0] == pytest.approx(68.0)  # the weather run's own degC -> degF
    assert r1[Role.RETURN_AIR_TEMP].iloc[0] == pytest.approx(72.0)  # RTU degF left alone
    # corrections off: the published gap fill comes back, and the mode is part of the hash
    raw = _ingest.ingest_dataset(entry, store, corrections=False)
    assert raw.content_hash != res.content_hash
    r1 = store.read_role_frame(facility_id="ds-test-b59", equip="RTU01")
    assert r1[Role.OA_AIRFLOW].iloc[0] == pytest.approx(111.0)


def test_zone_co2_joins_its_rtu_through_the_name_prefix(tmp_path, monkeypatch):
    zb = _b59_zip()
    entry = DatasetEntry.from_dict(_b59_dict(zb))
    monkeypatch.setenv("CAMBER_DATA_DIR", str(tmp_path / "cache"))
    _place(entry, {"b59.zip": zb}, tmp_path / "cache")
    _ingest.ingest_dataset(entry, ParquetStore(str(tmp_path / "store")))
    cfg = json.loads(package_text("configs", "lbnl-b59.json"))
    cfg["source"].update(store=str(tmp_path / "store"), facility_id="ds-test-b59")
    cfg.pop("report")
    res = run_config(cfg, base_dir=str(tmp_path))
    fleet = next(f for f in res.findings if f.rule == "dcv_system_verification")
    assert fleet.metrics["n_zones_joined"] == 2 and fleet.metrics["n_zones_unattributed"] == 0
    assert fleet.metrics["grouping_provenance"] == "heuristic"
    assert fleet.severity in ("ok", "info", "warn")  # heuristic grouping never reaches fault


# --------------------------------------------------------------------------- B4B-shaped


def _b4b_zip() -> bytes:
    """Two rooms stacked in one table, ISO stamps with UTC offsets across the 2022-10-30 DST end."""
    utc = pd.date_range("2022-10-29 22:00", periods=16, freq="15min", tz="UTC")
    local = utc.tz_convert("Europe/Amsterdam")
    rows = []
    for room, co2 in ((917810, 500.0), (925038, 700.0)):
        for i, t in enumerate(local):
            rows.append(
                {
                    "": i,
                    "id": room,
                    "timestamp": t.isoformat(sep=" "),
                    "bms_co2__ppm": co2 + i,
                    "bms_valve_frac__0": 0.2 if room == 917810 else 1.0,
                    "bms_occupancy__bool": 0,
                    "bms_temp_in__degC": 20.0,
                    "CO2-meter-SCD4x_co2__ppm": co2 - 50,
                    "CO2-meter-SCD4x_occupancy__p": 1,
                }
            )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("b4b.csv", pd.DataFrame(rows).to_csv(index=False))
    return buf.getvalue()


def test_stacked_rooms_offset_timestamps_and_per_room_mappings(tmp_path, monkeypatch):
    zb = _b4b_zip()

    def run(room, mapping, eid):
        return {
            "id": eid,
            "file": "b4b.zip",
            "member": "b4b.csv",
            "where": {"id": room},
            "mapping": mapping,
            "equip": f"ROOM_{room}",
            "equip_id": eid,
            "class": "VAV",
            "label": "",
        }

    ingest = {
        "adapter": "wide_csv",
        "facility": "ds-test-b4b",
        "timestamp": "timestamp",
        "source_timezone": "offset",
        "local_timezone": "Europe/Amsterdam",
        "resample": "15min",
        "mapping": "b4b_bms.json",
        "units": {"space_temp": "degC"},
        "runs": [
            run(917810, "b4b_bms.json", "ROOM_917810"),
            run(925038, "b4b_bms_novalve.json", "ROOM_925038"),
            run(917810, "b4b_scd41.json", "ROOM_917810_scd41"),
        ],
    }
    d = _entry(
        "test-b4b",
        [_file("b4b.zip", zb, archive="zip", members=["b4b.csv"])],
        ingest,
        dois=[],
    )
    assert validate_catalog({"schema": 1, "datasets": [d]}) == []
    entry = DatasetEntry.from_dict(d)
    monkeypatch.setenv("CAMBER_DATA_DIR", str(tmp_path / "cache"))
    _place(entry, {"b4b.zip": zb}, tmp_path / "cache")
    store = ParquetStore(str(tmp_path / "store"))
    res = _ingest.ingest_dataset(entry, store)
    # three runs read one member with different rows / mappings: none is a "duplicate"
    assert not any("byte-identical" in n for n in res.notes)
    assert set(store.equipment()["ds-test-b4b"]) == {
        "ROOM_917810",
        "ROOM_925038",
        "ROOM_917810_scd41",
    }
    a = store.read_role_frame(facility_id="ds-test-b4b", equip="ROOM_917810")
    # wall clock: 22:00 UTC is midnight CEST (+02:00); the repeated 02:00 hour is merged
    assert a.index[0] == pd.Timestamp("2022-10-30 00:00")
    assert a.index.is_monotonic_increasing and a.index.is_unique
    assert a[Role.CO2].iloc[0] == pytest.approx(500.0)  # room 917810's rows only
    assert a[Role.OA_DAMPER].iloc[0] == pytest.approx(20.0)  # fraction -> percent
    assert a[Role.SPACE_TEMP].iloc[0] == pytest.approx(68.0)
    b = store.read_role_frame(facility_id="ds-test-b4b", equip="ROOM_925038")
    assert Role.OA_DAMPER not in b.columns  # the assumed-constant valve is not mapped
    assert b[Role.CO2].iloc[0] == pytest.approx(700.0)
    s = store.read_role_frame(facility_id="ds-test-b4b", equip="ROOM_917810_scd41")
    assert s[Role.CO2].iloc[0] == pytest.approx(450.0)  # the second sensor


# --------------------------------------------------------------------------- Finnish-shaped


def test_plain_csv_downloads_and_litres_per_second(tmp_path, monkeypatch):
    idx = pd.date_range("2025-11-24 06:00", periods=120, freq="1min")
    body = (
        pd.DataFrame(
            {
                "time": idx.strftime("%Y-%m-%d %H:%M:%S"),
                "Room CO2 (ppm)": 600.0,
                "Temperature (°C)": 21.0,
                "Supply airflow (l/s)": 10.0,
                "Occupant count": 1,
            }
        )
        .to_csv(index=False)
        .encode("utf-8")
    )
    ingest = {
        "adapter": "wide_csv",
        "facility": "ds-test-fin",
        "timestamp": "time",
        "timestamp_format": "%Y-%m-%d %H:%M:%S",
        "resample": "5min",
        "mapping": "finnish_dcv.json",
        "units": {"space_temp": "degC", "oa_airflow": "L/s"},
        "runs": [
            {"id": "ROOM__test", "file": "test.csv", "equip": "ROOM", "class": "VAV", "label": ""}
        ],
    }
    d = _entry("test-fin", [_file("test.csv", body)], ingest, kind="lab")
    assert validate_catalog({"schema": 1, "datasets": [d]}) == []
    entry = DatasetEntry.from_dict(d)
    monkeypatch.setenv("CAMBER_DATA_DIR", str(tmp_path / "cache"))
    _place(entry, {"test.csv": body}, tmp_path / "cache")
    store = ParquetStore(str(tmp_path / "store"))
    _ingest.ingest_dataset(entry, store)
    fr = store.read_role_frame(facility_id="ds-test-fin", equip="ROOM__test")
    assert fr[Role.OA_AIRFLOW].iloc[0] == pytest.approx(21.19, abs=0.01)  # 10 l/s in cfm
    assert fr[Role.SPACE_TEMP].iloc[0] == pytest.approx(69.8)
    assert fr[Role.OCCUPANCY].iloc[0] == pytest.approx(1.0)


# --------------------------------------------------------------------------- validation


def _one(d):
    return validate_catalog({"schema": 1, "datasets": [d]})


@pytest.mark.parametrize(
    ("mutate", "needle"),
    [
        (lambda r, i: r.update(members=["x.csv"]), "used instead of 'member'"),
        (lambda r, i: (r.pop("member"), r.update(members=["nope.csv"])), "is not in b59.zip"),
        (lambda r, i: r.update(mapping="missing.json"), "is not shipped"),
        (lambda r, i: r.update(vars={"n": True}), "'vars' must map"),
        (lambda r, i: r.update(units={"oat": "furlongs"}), "unsupported source unit"),
        (lambda r, i: i["runs"][-1].update(equip_id="RTU01_zone_022"), "must be a unique plain"),
        (lambda r, i: i.update(local_timezone="Mars/Olympus"), "local_timezone"),
        (lambda r, i: i.pop("local_timezone"), "needs a local_timezone"),
    ],
)
def test_validator_checks_the_new_run_and_clock_keys(mutate, needle):
    d = _b59_dict(_b59_zip())
    mutate(d["ingest"]["runs"][0], d["ingest"])
    errs = _one(d)
    assert any(needle in e for e in errs), errs


def test_an_entry_without_dois_may_cite_a_pinned_url_but_not_nothing():
    d = _b59_dict(_b59_zip())
    d["data_issues"][0]["contradicts"]["citation"] = (
        "README, https://example.org/repo/blob/188573d39ca9/README.md"
    )
    assert any("by DOI" in e for e in _one(d))  # the entry has DOIs: a DOI is required
    d["dois"] = []
    assert _one(d) == []  # no DOI: a URL fixed to a commit will do
    for moving in (
        "README, https://example.org/repo/blob/main/README.md",  # a branch moves
        "README, https://example.org/repo",  # a landing page moves
        "the README",
    ):
        d["data_issues"][0]["contradicts"]["citation"] = moving
        assert any("pinned https URL" in e for e in _one(d)), moving
    for pinned in (
        "https://example.org/repo/tree/v1.2.0/README.md",  # a version tag
        "https://example.org/files/README.md?version=3",
        "https://zenodo.org/records/20065842",  # a record id is one version
    ):
        d["data_issues"][0]["contradicts"]["citation"] = f"README, {pinned}"
        assert _one(d) == [], pinned


# --------------------------------------------------------------------------- shipped entries


def test_shipped_dcv_entries_wire_their_data_issues():
    b59 = ds.get("lbnl-b59")
    assert b59.licence == "CC-BY-4.0" and b59.access == "open" and b59.kind == "real"
    assert b59.ingest["source_timezone"] == "UTC"
    assert b59.ingest["local_timezone"] == "America/Los_Angeles"
    assert b59.ingest["brick"]["equip_classes"] == {"Rooftop_Unit": "AHU"}
    assert b59.manual and "--from-dir" in b59.manual_instructions
    rtu = json.loads(package_text("mappings", "b59_rtu.json"))
    assert rtu["equipment"]["rtu_004_oa_flow_tn"] == "RTU04"  # Brick calls it rtu_004_oa_fr
    assert rtu["aliases"]["rtu_004_oadmpr_pct"] == "oa_damper"
    fixes = [q for q in b59.ingest["quirks"] if q["action"] == "fix"]
    assert len(fixes) == 1 and fixes[0]["before"] == "2020-04-10 22:00"
    assert b59.data_issue("oa-flow-gap-filled")["handling"] == "fix"
    assert b59.data_issue("rtu4-return-copies-supply")["handling"] == "annotate"
    assert all(r.get("group") == "brick" for r in b59.runs() if r["id"].startswith("rtu_"))
    assert {r.get("equip_id") for r in b59.runs()} >= {"RTU01_zone_022", "weather"}
    assert all(not r["id"].startswith("uft_") for r in b59.runs())
    assert len(b59.runs("full")) == len(b59.runs()) + 51
    assert any("CC0" in k for k in b59.known_issues)  # the host's CC0 vs the metadata's CC BY
    fin = ds.get("finnish-dcv")
    assert [r["id"] for r in fin.runs()] == [
        "ROOM__training_1",
        "ROOM__training_2",
        "ROOM__ventilation_test",
    ]
    b4b = ds.get("b4b-windesheim")
    assert b4b.dois == () and b4b.landing_url.startswith("https://github.com/")
    excluded = [r for r in b4b.runs() if r.get("exclude")]
    assert [r["equip_id"] for r in excluded] == ["ROOM_925038"]
    assert excluded[0]["mapping"] == "b4b_bms_novalve.json"
    novalve = json.loads(package_text("mappings", "b4b_bms_novalve.json"))["aliases"]
    assert "bms_valve_frac__0" not in novalve
    for name in ("b4b_bms.json", "b4b_scd41.json", "b4b_bms_novalve.json"):
        assert "temp_out__degC" not in json.loads(package_text("mappings", name))["aliases"]
