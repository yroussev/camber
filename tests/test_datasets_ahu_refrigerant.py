"""The AHU / refrigerant catalog entries (0.89 intake B) and the source layouts they need.

Tiny synthetic fixtures (no network, no real data) shaped like each published source:

* ``nuig-ahu101`` -- one headerless ``timestamp,value`` CSV per point inside a tar.gz, every stamp
  labelled ``+00:00`` though it is the local wall clock, one empty member;
* ``irish-ahu`` -- one plain wide CSV (no archive) with sentinel readings, outages logged as 0.00
  and a stuck-at-zero damper window, fixed by declared quirks;
* ``nist-heatpump-fdd`` -- a clockless workbook of steady-state test points, runs selected by file
  name, numbered on a synthetic clock, absolute refrigerant pressures (the ``xlsx`` extra);
* ``nist-ibal`` -- a manual (portal-export) entry whose CSV carries mixed ``-05:00`` / ``-04:00``
  offsets (``source_timezone: "offset"``), a stamp without fractional seconds and a repeated stamp.

The keys are the reconciled 0.89 names: one-point files are ``members`` as ``{raw column:
member}``, row selection is ``where: {column: [values]}``, the steady-state sheet's clock is
``clock: {"kind": "rows"}``.

Plus the validator rules for the new keys and sanity checks on the four shipped entries.
"""

import hashlib
import io
import json
import os
import sys
import tarfile

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camber.datasets as ds  # noqa: E402
from camber.datasets import _ingest, _ops, _readers  # noqa: E402
from camber.datasets._catalog import DatasetEntry, package_text, validate_catalog  # noqa: E402
from camber.datasets._units import convert_series  # noqa: E402
from camber.model.mapping import MappingProvider  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.store import ParquetStore  # noqa: E402

BASE = "https://example.org/ds/"
DOC = {"document": "Test descriptor, Table 1", "citation": "doi:10.0000/test"}


class _Resp:
    def __init__(self, body, url):
        self._buf, self._url, self.status = io.BytesIO(body), url, 200
        self.headers = {"Content-Length": str(len(body)), "ETag": '"x"'}

    def read(self, n=-1):
        return self._buf.read(n)

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Opener:
    def __init__(self, files):
        self.files, self.calls = files, []

    def open(self, req, timeout=None):
        self.calls.append(req.full_url)
        return _Resp(self.files[req.full_url], req.full_url)


def _file(name, body, **extra):
    return {
        "name": name,
        "url": BASE + name,
        "size": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "pinned": True,
        "etag": None,
        "archive": None,
        **extra,
    }


def _issue(iid, handling, **extra):
    return {
        "id": iid,
        "title": f"test issue {iid}",
        "columns": ["X"],
        "evidence": "3 of 3 rows",
        "contradicts": DOC,
        "handling": handling,
        "handling_note": "what the test ingester does",
        **extra,
    }


def _entry(did, files, ingest, **over):
    d = {
        "id": did,
        "title": did,
        "summary": "synthetic",
        "publisher": "Test Lab",
        "citation": "Test Lab (2026).",
        "landing_url": "https://example.org/ds",
        "licence": "CC-BY-4.0",
        "access": "open",
        "verified_on": "2026-09-26",
        "kind": "real",
        "files": files,
        "subsets": {
            k: {"files": "all", "runs": "all", "store_bytes_estimate": 1000}
            for k in ("default", "full")
        },
        "ingest": {"adapter": "wide_csv", "facility": f"ds-{did}", "derived": [], **ingest},
        "data_issues": [],
    }
    d.update(over)
    errs = validate_catalog({"schema": 1, "datasets": [d]})
    assert errs == [], errs
    return d


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMBER_DATA_DIR", str(tmp_path / "cache"))
    return tmp_path


# --------------------------------------------------------------------------- per-point tar


def _point_csv(values, start="2018-03-24 00:00"):
    idx = pd.date_range(start, periods=len(values), freq="1min")
    return "".join(f"{t.strftime('%Y-%m-%dT%H:%M:%S')}+00:00,{v}\n" for t, v in zip(idx, values))


def _tar(members: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, text in members.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _per_point_entry():
    n = 2 * 24 * 60
    t = np.arange(n)
    sat = 18 + 2 * np.sin(t / 300)  # degC
    members = {
        "S/SAT": _point_csv(np.round(sat, 3)),
        "S/SAT_SP": _point_csv(np.full(n, 18.0)),
        "S/SPEED": _point_csv(np.where((t // 60) % 24 >= 7, 60.0, 0.0)),
        "S/EMPTY": "",
        "S/UNUSED": _point_csv(np.ones(n)),
    }
    body = _tar(members)
    mapping = {"aliases": {"SAT": "supply_air_temp", "SAT_SP": "supply_air_temp_sp"}}
    mapping["aliases"]["SPEED"] = "supply_fan_speed"
    run = {
        "id": "ahu",
        "file": "pts.tar.gz",
        "members": {"SAT": "S/SAT", "SAT_SP": "S/SAT_SP", "SPEED": "S/SPEED"},
        "equip": "AHU",
        "class": "AHU",
        "label": "",
    }
    d = _entry(
        "test-points",
        [_file("pts.tar.gz", body, archive="tar", members=list(members))],
        {
            "mapping": "nuig_ahu101.json",
            "units": {"supply_air_temp": "degC", "supply_air_temp_sp": "degC"},
            "resample": "15min",
            "runs": [run],
        },
    )
    return d, body, mapping


def test_per_point_tar_run_joins_points_keeps_the_wall_clock_and_extracts_only_its_members(
    cache, monkeypatch
):
    d, body, mapping = _per_point_entry()
    # the entry's raw column names are the NUIG ids; use a test mapping for the synthetic ones
    real = _ingest.package_text
    monkeypatch.setattr(
        _ingest,
        "package_text",
        lambda *p: json.dumps(mapping) if p[-1] == "nuig_ahu101.json" else real(*p),
    )
    entry = DatasetEntry.from_dict(d)
    _ops.fetch_dataset(entry, opener=_Opener({BASE + "pts.tar.gz": body}))
    store = ParquetStore(str(cache / "store"))
    res = _ingest.ingest_dataset(entry, store)
    assert res.equipment == 1 and not res.warnings
    f = store.read_role_frame(facility_id="ds-test-points", equip="AHU__ahu")
    assert set(f.columns) == {Role.SUPPLY_AIR_TEMP, Role.SUPPLY_AIR_TEMP_SP, Role.SUPPLY_FAN_SPEED}
    assert len(f) == 2 * 24 * 4  # two days of 15-minute rows
    assert f[Role.SUPPLY_AIR_TEMP_SP].round(3).eq(64.4).all()  # 18 C -> 64.4 F
    # the +00:00 label is dropped, not converted: the fan starts at 07:00 as written
    on = f[Role.SUPPLY_FAN_SPEED] > 0
    assert on.index[on.argmax()].hour == 7
    extracted = os.path.join(str(cache / "cache"), "test-points", "extracted")
    names = {fn for _root, _dirs, fns in os.walk(extracted) for fn in fns}
    assert names == {"SAT", "SAT_SP", "SPEED"}  # EMPTY / UNUSED never touched


def test_one_point_members_skip_unneeded_points_and_dedup(tmp_path):
    a = tmp_path / "a"
    a.write_text(
        "2020-01-01T00:00:00+00:00,1\n2020-01-01T00:00:00+00:00,9\n2020-01-01T00:01:00+00:00,2\n"
    )
    b = tmp_path / "b"  # never read: its column is not needed
    out = _readers.read_sources({"A": str(a), "B": str(b)}, {}, lambda c: c == "A")
    assert list(out.columns) == ["A"] and out["A"].tolist() == [1.0, 2.0]
    empty = _readers.read_sources({"A": str(a)}, {"timestamp": "T"}, lambda c: False)
    assert empty.empty and empty.index.name == "T"


# --------------------------------------------------------------------------- plain wide CSV + fixes


def _irish_csv() -> bytes:
    idx = pd.date_range("2020-12-10", periods=10 * 96, freq="15min")
    n = len(idx)
    df = pd.DataFrame(
        {
            "Datetime": idx.strftime("%Y-%m-%d %H:%M:%S"),
            "DaTemp": np.full(n, 18.0),
            "MaTemp": np.full(n, 8.0),
            "RaTemp": np.full(n, 22.0),
            "OaTemp": np.full(n, 8.0),
            "OaDmprPos": np.full(n, 100.0),
            "HWVlvPos": np.zeros(n),
            "ChWVlvPos": np.zeros(n),
        }
    )
    df.loc[5, "DaTemp"] = 1000.0  # sentinel
    df.loc[10:13, ["DaTemp", "RaTemp", "MaTemp"]] = 0.0  # an outage logged as zeros
    in_window = (idx >= "2020-12-15") & (idx < "2021-01-01")
    df.loc[in_window, "OaDmprPos"] = 0.0  # stuck-at-zero signal
    return df.to_csv(index=False).encode()


def _irish_like():
    body = _irish_csv()
    real = json.loads(package_text("catalog.json"))
    ing = next(e for e in real["datasets"] if e["id"] == "irish-ahu")["ingest"]
    issues = [_issue(q["issue"], q["action"]) for q in ing["quirks"]]
    run = dict(ing["runs"][0], file="data.csv")
    spec = {k: ing[k] for k in ("timestamp", "timestamp_format", "mapping", "units", "quirks")}
    d = _entry(
        "test-irish",
        [_file("data.csv", body)],
        dict(spec, resample="1h", runs=[run]),
        data_issues=issues,
    )
    return DatasetEntry.from_dict(d), body


def test_plain_csv_run_with_the_irish_fixes_and_no_corrections(cache):
    entry, body = _irish_like()
    _ops.fetch_dataset(entry, opener=_Opener({BASE + "data.csv": body}))
    fixed = ParquetStore(str(cache / "fixed"))
    _ingest.ingest_dataset(entry, fixed)
    f = fixed.read_role_frame(facility_id="ds-test-irish", equip="AHU__ahu")
    assert f[Role.SUPPLY_AIR_TEMP].max() < 70  # the 1000 C sentinel is gone
    assert f[Role.RETURN_AIR_TEMP].min() > 70  # 0.00 outages blanked, not read as 32 F
    dec = f.loc["2020-12-17", Role.OA_DAMPER]
    assert dec.isna().all() and f.loc["2020-12-12", Role.OA_DAMPER].eq(100).all()
    raw = ParquetStore(str(cache / "raw"))
    _ingest.ingest_dataset(entry, raw, corrections=False)
    r = raw.read_role_frame(facility_id="ds-test-irish", equip="AHU__ahu")
    assert r[Role.SUPPLY_AIR_TEMP].max() > 100 and r.loc["2020-12-17", Role.OA_DAMPER].eq(0).all()


# --------------------------------------------------------------------------- steady-state workbook


def _hp_workbook(path):
    pytest.importorskip("openpyxl")
    rows = []
    for fn, psia, n in (("NF-a.txt", 380.0, 3), ("UC-80-a.txt", 355.0, 2), ("JUNK.txt", 1.0, 1)):
        for i in range(n):
            rows.append(
                {
                    "Filename": fn,
                    "CompDisch_psia": psia + i,
                    "ODLiqSV_Tsub_F": 8.0 if fn.startswith("NF") else 2.5,
                    "OD_Xitron_Watts": 2500.0,
                    "NF(NO=0,Yes=1)": 1 if fn.startswith("NF") else 0,
                }
            )
    df = pd.DataFrame(rows)
    header = pd.DataFrame([dict(zip(df.columns, df.columns))])  # a repeated header row
    df = pd.concat([df.iloc[:3], header, df.iloc[3:]], ignore_index=True)
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        df.to_excel(xw, sheet_name="tests", index=False)
    return path.read_bytes()


def test_row_selected_workbook_runs_on_a_synthetic_clock(cache, monkeypatch):
    body = _hp_workbook(cache / "hp.xlsx")
    mapping = {
        "aliases": {
            "CompDisch_psia": "discharge_pressure",
            "ODLiqSV_Tsub_F": "subcooling_temp",
            "OD_Xitron_Watts": "power",
        }
    }
    real = _ingest.package_text
    monkeypatch.setattr(
        _ingest,
        "package_text",
        lambda *p: json.dumps(mapping) if p[-1] == "nist_heatpump_fdd.json" else real(*p),
    )

    def run(rid, files, label, **x):
        w = {"Filename": files}
        return {"id": rid, "file": "hp.xlsx", "where": w, "equip": "HP", "class": "HP", **x} | {
            "label": label
        }

    d = _entry(
        "test-hp",
        [_file("hp.xlsx", body)],
        {
            "mapping": "nist_heatpump_fdd.json",
            "sheet": "tests",
            "clock": {"kind": "rows", "freq": "1min", "start": "2000-01-01"},
            "resample": None,
            "units": {"discharge_pressure": "psia", "power": "W"},
            "runs": [
                run("nf", ["NF-a.txt"], ""),
                run("uc80", ["UC-80-a.txt"], "undercharge"),
                run("junk", ["JUNK.txt"], "undercharge", exclude="junk-file"),
            ],
        },
        kind="lab",
        labeled_faults=True,
        requires_extras=["xlsx"],
        data_issues=[_issue("junk-file", "exclude", exclude={"runs": ["junk"]})],
    )
    entry = DatasetEntry.from_dict(d)
    _ops.fetch_dataset(entry, opener=_Opener({BASE + "hp.xlsx": body}))
    store = ParquetStore(str(cache / "store"))
    res = _ingest.ingest_dataset(entry, store)
    assert res.equipment == 3
    assert any("synthetic 1min clock" in n for n in res.notes)
    nf = store.read_role_frame(facility_id="ds-test-hp", equip="HP__nf")
    assert list(nf.index) == list(pd.date_range("2000-01-01", periods=3, freq="1min"))
    assert nf[Role.DISCHARGE_PRESSURE].round(3).tolist() == [365.304, 366.304, 367.304]  # psig
    assert nf[Role.POWER].eq(2.5).all() and nf[Role.SUBCOOLING_TEMP].eq(8.0).all()  # a delta
    meta = _ingest.dataset_meta(store.facilities_meta()["ds-test-hp"])
    assert meta["labels"] == {"HP__nf": "", "HP__uc80": "undercharge"}
    assert meta["excluded"]["HP__junk"]["issue"] == "junk-file"


# --------------------------------------------------------------------------- manual portal export


def _ibal_csv() -> bytes:
    lines = ["time,date,run,ch1_p_dis,ch1_power"]
    t0 = pd.Timestamp("2025-03-08 23:59:30", tz="America/New_York")
    for i in range(12):  # crosses the 2025-03-09 spring-forward (02:00 EST -> 03:00 EDT) ...
        t = t0 + pd.Timedelta(minutes=15 * i) + pd.Timedelta(hours=1.5)
        stamp = t.isoformat(sep=" ", timespec="milliseconds")
        if i == 4:
            stamp = t.isoformat(sep=" ", timespec="seconds")  # ... one stamp with no fraction
        lines.append(f"{stamp},2025-03-08,1,{300 + i},{5000 + i}")
    lines.append(lines[-1].replace(",311,", ",999,"))  # a repeated stamp, different value
    return ("\n".join(lines) + "\n").encode()


def test_manual_portal_export_converts_offsets_and_keeps_every_stamp(cache, monkeypatch):
    from camber.datasets._fetch import ManualDownload

    body = _ibal_csv()
    real = json.loads(package_text("catalog.json"))
    ing = next(e for e in real["datasets"] if e["id"] == "nist-ibal")["ingest"]
    keys = (
        "timestamp",
        "timestamp_format",
        "source_timezone",
        "local_timezone",
        "mapping",
        "units",
    )
    spec = {k: ing[k] for k in keys}
    d = _entry(
        "test-ibal",
        [dict(_file("export.csv", body), url=None)],
        dict(
            spec,
            resample=None,
            runs=[
                {"id": "ch1", "file": "export.csv", "equip": "CH1", "class": "CHILLER", "label": ""}
            ],
        ),
        manual=True,
        manual_instructions="Export it from the portal.",
    )
    entry = DatasetEntry.from_dict(d)
    with pytest.raises(ManualDownload):
        _ops.fetch_dataset(entry, opener=_Opener({}))
    src = cache / "by-hand"
    src.mkdir()
    (src / "export.csv").write_bytes(body)
    monkeypatch.setattr(ds, "_entries", lambda: (entry,))
    store = str(cache / "store")
    ds.ingest("test-ibal", store, from_dir=str(src))
    f = ParquetStore(store).read_role_frame(facility_id="ds-test-ibal", equip="CH1__ch1")
    assert len(f) == 12  # the fractionless stamp survives; the repeated one keeps its first value
    assert f[Role.DISCHARGE_PRESSURE].max() == 311 and f[Role.POWER].iloc[0] == 5.0
    # Eastern wall clock: 01:29:30 EST, then after the jump 03:14:30 EDT
    assert f.index[0] == pd.Timestamp("2025-03-09 01:29:30")
    assert pd.Timestamp("2025-03-09 03:14:30") in f.index


def test_parse_timestamps_iso8601_offsets_and_label_drop():
    mixed = pd.Series(["2025-03-25 14:45:03-04:00", "2025-01-28 13:00:46.505000-05:00"])
    spec = {
        "timestamp_format": "ISO8601",
        "source_timezone": "offset",
        "local_timezone": "America/New_York",
    }
    got = _readers.parse_timestamps(mixed, spec)  # the source clock: UTC wall time
    assert got.tz is None and got[0] == pd.Timestamp("2025-03-25 18:45:03")
    local = _readers.to_local_clock(pd.DataFrame({"x": [1, 2]}, index=got), spec)
    assert sorted(local.index) == [
        pd.Timestamp("2025-01-28 13:00:46.505"),
        pd.Timestamp("2025-03-25 14:45:03"),
    ]
    inferred = {"source_timezone": "offset", "local_timezone": "America/New_York"}
    assert _readers.parse_timestamps(mixed, inferred).isna().sum() == 1  # why ISO8601 is pinned
    utc = pd.Series(["2018-03-25T02:00:00+00:00"])
    assert _readers.parse_timestamps(utc, {})[0] == pd.Timestamp("2018-03-25 02:00")


def test_psia_converts_to_psig_and_psig_is_a_noop():
    s = pd.Series([14.696, 314.696])
    assert convert_series(s, "psia").round(6).tolist() == [0.0, 300.0]
    assert convert_series(s, "psig") is s and convert_series(s, "psi") is s


# --------------------------------------------------------------------------- validator


def test_validator_rules_for_members_rows_clocks_and_zones():
    d, _body, _m = _per_point_entry()
    bad = json.loads(json.dumps(d))
    bad["ingest"]["runs"][0]["members"]["SAT"] = "S/NOPE"
    bad["ingest"]["source_timezone"] = "offset"
    bad["ingest"]["local_timezone"] = "Mars/Olympus"
    bad["ingest"]["clock"] = {"kind": "rows", "start": "2000-01-01"}
    bad["ingest"]["timestamp_format"] = "iso"
    bad["ingest"]["runs"].append(
        {"id": "w", "file": "pts.tar.gz", "where": {"F": []}, "equip": "A", "class": "A"}
    )
    bad["ingest"]["runs"][-1]["label"] = ""
    errs = "\n".join(validate_catalog({"schema": 1, "datasets": [bad]}))
    assert "member 'S/NOPE' is not in pts.tar.gz" in errs
    assert "local_timezone must be an IANA zone" in errs
    assert "a rows clock needs a 'freq'" in errs
    assert "timestamp_format must be a strftime format string or 'ISO8601'" in errs
    assert "'where' must map column names to a value or a list of values" in errs
    ok = json.loads(json.dumps(d))
    ok["ingest"]["timestamp_format"] = "ISO8601"
    assert validate_catalog({"schema": 1, "datasets": [ok]}) == []
    both = json.loads(json.dumps(d))
    both["ingest"]["runs"][0]["member"] = "S/SAT"
    errs = validate_catalog({"schema": 1, "datasets": [both]})
    assert any("used instead of 'member'" in e for e in errs)


def test_the_pre_reconciliation_spellings_are_rejected():
    """Intake B's own names for these concepts, before 0.89 reconciled them, name the new key."""
    d, _body, _m = _per_point_entry()
    old = json.loads(json.dumps(d))
    run = old["ingest"]["runs"][0]
    run["points"] = run.pop("members")
    old["ingest"]["tz_convert"] = "America/New_York"
    old["ingest"]["synthetic_index"] = {"start": "2000-01-01", "freq": "1min"}
    old["ingest"]["runs"].append(
        {
            "id": "w",
            "file": "pts.tar.gz",
            "where": {"column": "Filename", "in": ["a"]},
            "equip": "A",
            "class": "A",
            "label": "",
        }
    )
    errs = "\n".join(validate_catalog({"schema": 1, "datasets": [old]}))
    assert "'points' is not a run key; use members: {raw column: member}" in errs
    assert "ingest.tz_convert is not a key; use source_timezone: 'offset'" in errs
    assert "ingest.synthetic_index is not a key; use clock:" in errs
    assert "'where' is {column: value or [values]}" in errs


# --------------------------------------------------------------------------- the shipped entries


def _mapping(name):
    spec = json.loads(package_text("mappings", name))
    MappingProvider.from_dict(spec)  # every role slug is valid
    assert spec["_comment"]
    return spec["aliases"]


def test_nuig_entry_is_a_100pct_oa_unit_on_the_wall_clock():
    e = ds.get("nuig-ahu101")
    assert e.licence == "CDLA-Permissive-1.0" and e.commercial_ok and not e.research_only
    aliases = _mapping("nuig_ahu101.json")
    assert "mixed_air_temp" not in aliases.values() and "oa_damper" not in aliases.values()
    run = e.ingest["runs"][0]
    assert set(run["members"]) == set(aliases)  # every mapped point is read, nothing else
    assert "source_timezone" not in e.ingest  # the +00:00 label is local time: keep the wall clock
    assert e.data_issue("local-clock-labelled-utc")["handling"] == "annotate"
    cfg = json.loads(package_text("configs", "nuig-ahu101.json"))
    assert "outdoor_air_fraction" not in json.dumps(cfg["rules"])


def test_irish_entry_fixes_are_wired_and_the_min_oa_is_calibrated():
    e = ds.get("irish-ahu")
    fixes = {q["issue"] for q in e.ingest["quirks"] if q["action"] == "fix"}
    assert fixes == {
        "sentinel-temperatures",
        "outages-logged-as-zero",
        "damper-signal-zero-during-100pct-oa",
    }
    assert _mapping("irish_ahu.json")["OaTemp"] == "oat"
    cfg = json.loads(package_text("configs", "irish-ahu.json"))
    oaf = next(
        r for r in cfg["rules"] if isinstance(r, dict) and r["name"] == "outdoor_air_fraction"
    )
    assert oaf["params"]["min_oa_pct"] == 4.1


def test_heat_pump_entry_labels_exclusions_and_no_rules():
    e = ds.get("nist-heatpump-fdd")
    assert e.requires_extras == ("xlsx",) and e.labeled_faults and e.licence == "NIST-PD"
    assert not any(f["name"].endswith(".xls") for f in e.files)
    runs = e.ingest["runs"]
    assert len(runs) == 60 and len(e.runs()) == 8
    excluded = {r["id"] for r in runs if r.get("exclude")}
    assert excluded == {
        "s14_short_ef110_named_ef80_90",
        "s14_long_cf72_named_cf50",
        "s16_long_cf67_junk",
        "s16_short_uc95p4_junk",
    }
    names = [n for r in runs for n in r["where"]["Filename"]]
    assert len(names) == len(set(names)) == 294  # every test file in exactly one run
    assert {r["label"] for r in runs} - {""} <= set(e.labels["fault_types"])
    assert e.ingest["units"]["discharge_pressure"] == "psia"
    assert json.loads(package_text("configs", "nist-heatpump-fdd.json"))["rules"] == []


def test_ibal_entry_is_manual_pinned_and_gauge():
    e = ds.get("nist-ibal")
    assert e.manual and "ibal.nist.gov/measurements" in e.manual_instructions
    for f in e.files:
        assert f["url"] is None and f["pinned"] and f["name"] in e.manual_instructions
    assert e.ingest["source_timezone"] == "offset"
    assert e.ingest["local_timezone"] == "America/New_York"
    assert e.ingest["timestamp_format"] == "ISO8601"
    aliases = _mapping("nist_ibal.json")
    # line temperatures, not deltas: subcooling / superheat are derived from them (0.93, #39)
    assert aliases["ch1_sc_rtd"] == "liquid_line_temp"
    assert aliases["ch1_sh_rtd"] == "suction_line_temp"
    assert e.ingest["units"]["discharge_pressure"] == "psig"
    cfg = json.loads(package_text("configs", "nist-ibal.json"))
    assert cfg["drift"]["families"] == [{"class": "CHILLER", "family": "chiller"}]
    assert cfg["equipment"][0]["refrigerant"] == "R-410A"
