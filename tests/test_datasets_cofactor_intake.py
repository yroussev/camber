"""The cofactor-drammen catalog entry (no network, no real data).

A tiny synthetic zip shaped like the publisher's building files -- a ``key;value`` metadata block
of a different length in each file (one with a UTF-8 byte-order mark, one with trailing
semicolons), three unit / description rows, a ``;``-separated header starting ``TimeStamp``,
hourly Wh readings stamped with a fixed ``+0100`` offset -- exercises what the entry added to the
wide-CSV reader (``sep`` and ``header_marker``), its clock (fixed UTC+1 instants stored on the
Europe/Oslo wall clock), its units (Wh per hour stored as a kW rate, degC as degF), the
building-6412 fix quirks, and the shipped M&V template.
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

from camber.config import run_config  # noqa: E402
from camber.datasets import _ingest, _paths  # noqa: E402
from camber.datasets._catalog import DatasetEntry, package_text, validate_catalog  # noqa: E402
from camber.datasets._readers import read_table  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.store import ParquetStore  # noqa: E402

ZIP = "Cofactor_Drammen_Buildings_45_v3.zip"
START = "2018-01-01"
HOURS = 24 * 120  # January-April 2018: across the 2018-03-25 spring-forward day


def _shipped() -> dict:
    data = json.loads(package_text("catalog.json"))
    return next(d for d in data["datasets"] if d["id"] == "cofactor-drammen")


def _building(bid: str, cols: dict, *, bom=False, trailing="", meta_lines=16) -> bytes:
    """One building file in the publisher's layout; ``cols`` maps a column to (unit, values)."""
    idx = pd.date_range(START, periods=HOURS, freq="1h")
    head = [f"Header_line;{meta_lines + 6}", "location;Drammen - Norway"]
    head += [f"key_{i};value_{i}" for i in range(meta_lines - 5)]
    head += ["timestamp_format;%Y-%m-%dT%H:%M:%S%z", "time_zone;Etc/Gmt-1", f"building_id;{bid}"]
    lines = [h + trailing for h in head] + [trailing]
    names = ["Tout", *cols]
    lines.append(";" + ";".join("Weather variable" if c == "Tout" else "Energy" for c in names))
    lines.append(";" + ";".join("C" if c == "Tout" else cols[c][0] for c in names))
    lines.append(";" + ";".join(f"long name of {c}" for c in names))
    lines.append("TimeStamp;" + ";".join(names))
    tout = -5.0 + 10.0 * (np.arange(HOURS) / HOURS)  # a cold winter warming into spring
    for i, t in enumerate(idx):
        vals = [f"{tout[i]:.2f}"] + [
            "" if np.isnan(v := cols[c][1][i]) else f"{v:.1f}" for c in cols
        ]
        lines.append(t.strftime("%Y-%m-%dT%H:%M:%S") + "+0100;" + ";".join(vals))
    text = "\r\n".join(lines) + "\r\n"
    return (b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8")


def _load_profile() -> np.ndarray:
    """An occupied-weekday electricity shape (Wh per hour), heavier when colder."""
    idx = pd.date_range(START, periods=HOURS, freq="1h")
    day = (idx.dayofweek < 5) & (idx.hour >= 7) & (idx.hour < 16)
    heat = 20_000.0 * (1.0 - np.arange(HOURS) / HOURS)
    return np.round((10_000.0 + 30_000.0 * day + heat) / 1000.0) * 1000.0


def _zip() -> bytes:
    el = _load_profile()
    htt = np.full(HOURS, 60_000.0)
    hthp = np.full(HOURS, 60_000.0)
    # 6412's published faults: HtHP 100x until 2019-05-09 (here: the whole fixture), a register
    # spike in HtTot, a spike in HtDHW
    hthp_pub = hthp * 100.0
    htt[100] = 1_850_530_000.0
    dhw = np.full(HOURS, 3_000.0)
    dhw[200] = 11_466_010.0
    files = {
        "building_6397.txt": _building(
            "6397",
            {"ElBoil": ("Wh", el / 2), "ElImp": ("Wh", el), "ElPV": ("Wh", np.zeros(HOURS))},
            meta_lines=19,
        ),
        "building_6412.txt": _building(
            "6412",
            {
                "ElImp": ("Wh", el),
                "HtTot": ("Wh", htt),
                "HtHP": ("Wh", hthp_pub),
                "HtDHW": ("Wh", dhw),
            },
            bom=True,
            trailing=";;;;",
            meta_lines=18,
        ),
        "building_6404.txt": _building(
            "6404", {"HtDH": ("Wh", htt.clip(max=90_000.0)), "ElImp": ("Wh", el)}, meta_lines=20
        ),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in files.items():
            z.writestr(name, body)
    return buf.getvalue()


def _fixture_entry(zb: bytes) -> dict:
    """The shipped entry with the fixture zip in place of the publisher's and its runs cut to it."""
    d = json.loads(json.dumps(_shipped()))
    members = ["building_6397.txt", "building_6412.txt", "building_6404.txt"]
    f = d["files"][0]
    f.update(
        size=len(zb),
        sha256=hashlib.sha256(zb).hexdigest(),
        members=members,
        extracted_size=None,
    )
    keep = {
        "b6397_ElBoil",
        "b6397_ElImp",
        "b6397_ElPV",
        "b6412_ElImp",
        "b6412_HtTot",
        "b6412_HtHP",
        "b6412_HtDHW",
        "b6404_HtDH",
        "b6404_ElImp",
    }
    d["ingest"]["runs"] = [r for r in d["ingest"]["runs"] if r["id"] in keep]
    assert {r["id"] for r in d["ingest"]["runs"]} == keep
    for sub in d["subsets"].values():
        if sub.get("runs") != "all":
            sub["runs"] = [r for r in sub["runs"] if r in keep]
    return d


def _run_ingest(tmp_path, monkeypatch, *, subset="full", corrections=True):
    zb = _zip()
    d = _fixture_entry(zb)
    assert validate_catalog({"schema": 1, "datasets": [d]}) == []
    entry = DatasetEntry.from_dict(d)
    root = tmp_path / "cache"
    monkeypatch.setenv("CAMBER_DATA_DIR", str(root))
    ddir = _paths.downloads_dir(str(root), entry.id)
    os.makedirs(ddir, exist_ok=True)
    with open(os.path.join(ddir, ZIP), "wb") as fh:
        fh.write(zb)
    store = ParquetStore(str(tmp_path / "store"))
    res = _ingest.ingest_dataset(entry, store, subset=subset, corrections=corrections)
    return store, res


def test_the_shipped_entry_is_open_pinned_and_wired():
    d = _shipped()
    assert d["licence"] == "CC-BY-4.0" and d["access"] == "open" and d["attribution_required"]
    assert d["licence_check"]["json_path"] == "metadata.license.id"
    (f,) = d["files"]
    assert f["pinned"] and f["size"] == 22_417_463
    assert f["sha256"] == "20b6795f859ff62ccd39a2af12078423535223e5cfa285d960364b3f20952a11"
    assert len(f["members"]) == 45
    runs = d["ingest"]["runs"]
    assert {r["member"] for r in runs} == set(f["members"])
    # every building's import is in the default subset, with its own weather
    elimp = [r["id"] for r in runs if r["vars"]["m"] == "ElImp"]
    assert len(elimp) == 45 and set(elimp) <= set(d["subsets"]["default"]["runs"])
    assert all(r["class"] == "ELECTRICITY_METER" for r in runs if r["vars"]["m"] == "ElImp")
    for sub in ("default", "full"):
        assert d["subsets"][sub]["store_bytes_estimate"] > 1_000_000
    ids = {i["id"] for i in d["data_issues"]}
    assert {"covid-19-closures-2020", "timestamps-fixed-utc-plus-1"} <= ids


def test_header_marker_skips_each_files_own_preamble(tmp_path):
    for name, lines in (("a.txt", 3), ("b.txt", 7)):
        body = "".join(f"k{i};v{i};;\n" for i in range(lines)) + "\n;C;Wh\nTimeStamp;Tout;ElImp\n"
        body += "2018-01-01T00:00:00+0100;1.5;2000.0\n"
        path = tmp_path / name
        path.write_bytes(b"\xef\xbb\xbf" + body.encode())
        df = read_table(str(path), sep=";", header_marker="TimeStamp")
        assert list(df.columns) == ["TimeStamp", "Tout", "ElImp"]
        assert df["ElImp"].iloc[0] == 2000.0
    bad = tmp_path / "c.txt"
    bad.write_text("a;b\n1;2\n")
    with pytest.raises(ValueError, match="TimeStamp"):
        read_table(str(bad), sep=";", header_marker="TimeStamp")


def test_layout_keys_are_validated():
    d = json.loads(json.dumps(_shipped()))
    d["ingest"]["sep"] = ";;"
    d["ingest"]["header_marker"] = " "
    errs = validate_catalog({"schema": 1, "datasets": [d]})
    assert any("ingest.sep" in e for e in errs)
    assert any("ingest.header_marker" in e for e in errs)


def test_ingest_stores_kw_degf_on_the_oslo_wall_clock(tmp_path, monkeypatch):
    store, res = _run_ingest(tmp_path, monkeypatch)
    assert res.facilities == ["ds-cofactor-drammen"] and res.equipment == 9
    assert not res.warnings
    eq = set(store.equipment()["ds-cofactor-drammen"])
    assert {"b6397_ElImp", "b6404_HtDH", "b6412_HtHP"} <= eq
    f = store.read_role_frame(facility_id="ds-cofactor-drammen", equip="b6397_ElImp")
    el = _load_profile()
    # winter: +01:00 is the Oslo wall clock; Wh per hour -> kW; degC -> degF
    assert f.index[0] == pd.Timestamp("2018-01-01 00:00")
    assert f[Role.POWER].iloc[0] == pytest.approx(el[0] / 1000.0)
    assert f[Role.OAT].iloc[0] == pytest.approx(-5.0 * 9 / 5 + 32)
    # spring forward: 02:00 local does not exist; 02:00 +01:00 is 03:00 CEST
    assert pd.Timestamp("2018-03-25 02:00") not in f.index
    i = pd.Timestamp("2018-03-25 02:00") - pd.Timestamp(START)
    k = int(i / pd.Timedelta("1h"))
    assert f.loc["2018-03-25 03:00", Role.POWER] == pytest.approx(el[k] / 1000.0)
    # summer time: the 07:00 +01:00 start of occupancy is 08:00 on the Oslo clock
    assert f.loc["2018-04-02 08:00", Role.POWER] > f.loc["2018-04-02 07:00", Role.POWER]
    h = store.read_role_frame(facility_id="ds-cofactor-drammen", equip="b6404_HtDH")
    assert Role.ENERGY_RATE in h.columns and h[Role.ENERGY_RATE].max() == pytest.approx(90.0)


def test_6412_fix_quirks_mask_and_no_corrections_keeps_them(tmp_path, monkeypatch):
    store, res = _run_ingest(tmp_path, monkeypatch)
    hp = store.read_role_frame(facility_id="ds-cofactor-drammen", equip="b6412_HtHP")
    # the whole fixture sits before 2019-05-09: the 100x HtHP is masked, not rescaled
    assert Role.ENERGY_RATE not in hp.columns or hp[Role.ENERGY_RATE].isna().all()
    tot = store.read_role_frame(facility_id="ds-cofactor-drammen", equip="b6412_HtTot")
    assert tot[Role.ENERGY_RATE].max() == pytest.approx(60.0)
    dhw = store.read_role_frame(facility_id="ds-cofactor-drammen", equip="b6412_HtDHW")
    assert dhw[Role.ENERGY_RATE].max() == pytest.approx(3.0)
    assert any(n.startswith("fix:") and "HtHP" in n for n in res.notes)
    raw, res2 = _run_ingest(tmp_path / "raw", monkeypatch, corrections=False)
    hp = raw.read_role_frame(facility_id="ds-cofactor-drammen", equip="b6412_HtHP")
    assert hp[Role.ENERGY_RATE].median() == pytest.approx(6000.0)
    tot = raw.read_role_frame(facility_id="ds-cofactor-drammen", equip="b6412_HtTot")
    assert tot[Role.ENERGY_RATE].max() > 1e6
    assert any(n.startswith("fix skipped:") for n in res2.notes)


def test_default_subset_is_the_import_meters(tmp_path, monkeypatch):
    store, _ = _run_ingest(tmp_path, monkeypatch, subset="default")
    assert set(store.equipment()["ds-cofactor-drammen"]) == {
        "b6397_ElImp",
        "b6412_ElImp",
        "b6404_ElImp",
        "b6404_HtDH",
    }


def test_template_fits_baselines_and_trips_nothing(tmp_path, monkeypatch):
    store, _ = _run_ingest(tmp_path, monkeypatch, subset="default")
    cfg = json.loads(package_text("configs", "cofactor-drammen.json"))
    cfg["source"]["store"] = store.root
    res = run_config(cfg)
    assert res.findings
    assert {f.rule for f in res.findings} == {"mv_baseline"}
    assert all(f.severity in ("ok", "info") for f in res.findings)
    assert {f.equip for f in res.findings} == {
        "b6397_ElImp",
        "b6412_ElImp",
        "b6404_ElImp",
        "b6404_HtDH",
    }
