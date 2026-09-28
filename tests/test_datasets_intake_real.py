"""The 0.89 real-building / refrigeration catalog entries on tiny synthetic fixtures (no network).

``robod``, ``sdu-ou44``, ``ornl-frp-ops`` and ``ornl-supermarket-fdd`` are ingested through their
**real** catalog specs (mapping, units, derives, quirks, run layout), with each publisher file
replaced by a few hours of synthetic data in the publisher's own layout. Covers the ingest layouts
they introduced: timestamps carrying a UTC offset (wall clock kept), one-point ``members`` joined on
a synthetic day ``clock``, a run's own ``mapping`` + ``vars``, a run's own ``timestamp_format``,
verbatim ``equip_id``, and ``contiguous: false``.
"""

import copy
import hashlib
import importlib.util
import io
import json
import os
import re
import sys
import zipfile

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.config import run_config  # noqa: E402
from camber.datasets import _ingest, _ops  # noqa: E402
from camber.datasets._catalog import (  # noqa: E402
    DatasetEntry,
    load_catalog_data,
    validate_catalog,
)
from camber.model.roles import Role  # noqa: E402
from camber.store import ParquetStore  # noqa: E402

BASE = "https://example.org/intake/"
IDS = ("robod", "sdu-ou44", "ornl-frp-ops", "ornl-supermarket-fdd")


class _Resp:
    def __init__(self, body, url):
        self._buf = io.BytesIO(body)
        self.status = 200
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


class _Opener:
    def __init__(self, files: dict):
        self.files = files

    def open(self, req, timeout=None):
        return _Resp(self.files[req.full_url], req.full_url)


def _host_rules() -> list:
    """The site-neutrality rules the catalog alone is exempt from (the data host and its DOI
    prefix): everything rendered into the docs -- a data issue's citation -- must pass them."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, ".github", "scripts", "site_neutrality_patterns.py")
    spec = importlib.util.spec_from_file_location("snp", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rules = [
        rx.replace("[:space:]", r"\s")
        for rx, _w, ex in mod.rules()
        if "camber/datasets/catalog.json" in ex
    ]
    assert rules
    return rules


def _real(did: str) -> dict:
    d = next(e for e in load_catalog_data()["datasets"] if e["id"] == did)
    return copy.deepcopy(d)  # as_dict() / the catalog JSON are shallow: never mutate them


def _synthetic(did: str, bodies: dict, runs: list) -> tuple:
    """The real entry with its files swapped for ``bodies`` ({name: bytes}) and one subset."""
    d = _real(did)
    files = []
    for f in d["files"]:
        if f["name"] not in bodies:
            continue
        body = bodies[f["name"]]
        g = dict(f, url=BASE + f["name"], size=len(body), etag=None)
        g["sha256"] = hashlib.sha256(body).hexdigest()
        if g.get("members") is not None:
            with zipfile.ZipFile(io.BytesIO(body)) as z:
                g["members"] = z.namelist()
            g.pop("extracted_size", None)
        files.append(g)
    d["files"] = files
    d["ingest"]["runs"] = [r for r in d["ingest"]["runs"] if r["id"] in runs]
    for name in ("default", "full"):
        d["subsets"][name] = {
            "files": "all",
            "runs": runs,
            "store_bytes_estimate": 1_000_000,
        }
    assert validate_catalog({"schema": 1, "datasets": [d]}) == []
    opener = _Opener({BASE + n: b for n, b in bodies.items()})
    return DatasetEntry.from_dict(d), opener


def _zip(members: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in members.items():
            z.writestr(name, text)
    return buf.getvalue()


def _fetch_ingest(entry, opener, tmp, monkeypatch, **kw):
    monkeypatch.setenv("CAMBER_DATA_DIR", str(tmp / "cache"))
    _ops.fetch_dataset(entry, opener=opener)
    store = ParquetStore(str(tmp / "store"))
    res = _ingest.ingest_dataset(entry, store, **kw)
    return store, res


# --------------------------------------------------------------------------- catalog


def test_the_four_entries_are_open_pinned_and_cite_papers_not_the_host():
    data = load_catalog_data()
    assert validate_catalog(data) == []
    by_id = {d["id"]: d for d in data["datasets"]}
    host_rules = _host_rules()
    for did in IDS:
        d = by_id[did]
        assert d["access"] == "open" and d["licence"] in ("CC-BY-4.0", "CC0-1.0")
        assert all(f["pinned"] and len(f["sha256"]) == 64 for f in d["files"])
        assert d["licence_check"]["url"].startswith("https://api.")
        for iss in d["data_issues"]:
            cite = iss["contradicts"]["citation"]
            # the generated docs may only cite the data-descriptor paper, never the data host
            assert "10.1038/" in cite or "10.1007/" in cite
            assert not any(re.search(rx, cite, flags=re.IGNORECASE) for rx in host_rules)
    assert by_id["sdu-ou44"]["licence"] == "CC0-1.0"
    assert not DatasetEntry.from_dict(by_id["robod"]).contiguous
    assert not DatasetEntry.from_dict(by_id["sdu-ou44"]).contiguous
    assert DatasetEntry.from_dict(by_id["ornl-frp-ops"]).contiguous
    # the reference-only entry is labelled but scores nothing, and its template runs no rule
    sm = by_id["ornl-supermarket-fdd"]
    assert sm["labeled_faults"] and sm["labels"]["targets"] == {}
    cfg = json.loads(
        open(
            os.path.join(
                os.path.dirname(__file__),
                "..",
                "camber",
                "datasets",
                "configs",
                "ornl-supermarket-fdd.json",
            )
        ).read()
    )
    assert cfg["rules"] == []


def test_contiguous_is_validated_and_round_trips():
    d = _real("robod")
    assert DatasetEntry.from_dict(d).as_dict()["contiguous"] is False
    assert DatasetEntry.from_dict(d).provenance()["contiguous"] is False
    d["contiguous"] = "no"
    assert any("contiguous" in e for e in validate_catalog({"schema": 1, "datasets": [d]}))


def test_run_layout_keys_are_validated():
    d = _real("ornl-frp-ops")
    run = d["ingest"]["runs"][1]
    run["mapping"] = "no_such.json"
    run["vars"] = {"n": [1]}
    run["timestamp_format"] = "no-percent"
    run["units"] = {"airflow": "furlongs"}
    errs = validate_catalog({"schema": 1, "datasets": [d]})
    for key in ("mapping", "'vars'", "timestamp_format", "units["):
        assert any(key in e for e in errs), key
    d = _real("sdu-ou44")
    d["ingest"]["runs"][0]["members"]["co2"] = "filleddata/missing.csv"
    d["ingest"]["runs"][1]["equip_id"] = "ROOM1"  # duplicate of run 0's
    d["ingest"]["clock"] = {"kind": "day", "day": "DayId"}
    errs = validate_catalog({"schema": 1, "datasets": [d]})
    assert any("member 'filleddata/missing.csv' is not in" in e for e in errs)
    assert any("unique plain name" in e for e in errs)
    assert any("a day clock needs 'day' and 'time'" in e for e in errs)
    d = _real("sdu-ou44")
    d["ingest"]["day_clock"] = d["ingest"].pop("clock")  # intake C's name before 0.89 reconciled it
    errs = validate_catalog({"schema": 1, "datasets": [d]})
    assert any("ingest.day_clock is not a key; use clock:" in e for e in errs)


# --------------------------------------------------------------------------- robod


def _robod_room(i: int) -> str:
    idx = pd.date_range("2021-09-07 00:00", periods=2 * 288, freq="5min")
    t = np.arange(len(idx))
    hour = idx.hour
    occ = ((hour >= 9) & (hour < 18)).astype(int)
    fan_hz = np.where((hour >= 8) & (hour < 19), 30.0, 0.0)
    df = pd.DataFrame(
        {
            "timestamp": idx.strftime("%Y-%m-%d %H:%M") + " +08:00",
            "indoor_co2 [ppm]": 440 + 150 * occ + (t % 3),
            "air_temperature [Celsius]": 25.0 + occ,
            "temp_setpoint [Celsius]": 25.0,
            "supply_air_temperature [Celsius]": 20.0,
            "dry_bulb_temp [Celsius]": 28.0,
            "outdoor_co2 [ppm]": 470.0,
            "outdoor_relative_humidity [%]": 80.0,
            "occupant_presence [binary]": occ,
            "occupant_count [number]": 4 * occ,
        }
    )
    if i <= 2:
        df["fcu_fan_speed [Hz]"] = fan_hz
    else:
        df["ahu_fan_speed [Hz]"] = fan_hz
        df["supply_air_flow [CMH]"] = np.where(fan_hz > 0, 900.0, 0.0)
        df["damper_position [%]"] = 100.0
        df["cooling_coil_valve_command [%]"] = 40.0
    return df.to_csv(index=False)


def test_robod_keeps_the_singapore_wall_clock_and_maps_both_room_types(tmp_path, monkeypatch):
    zb = _zip({"combined_Room1.csv": _robod_room(1), "combined_Room3.csv": _robod_room(3)})
    entry, opener = _synthetic("robod", {"SupplementaryData.zip": zb}, ["room1", "room3"])
    store, res = _fetch_ingest(entry, opener, tmp_path, monkeypatch)
    assert store.equipment()["ds-robod"] == {"Room1": "FCU", "Room3": "VAV"}
    r3 = store.read_role_frame(facility_id="ds-robod", equip="Room3")
    # '+08:00' stamps keep their wall clock: occupancy starts at 09:00 local, not 01:00 UTC
    occ = r3[Role.OCCUPANCY]
    assert occ.loc["2021-09-07 08:45"] == 0 and occ.loc["2021-09-07 09:00"] == 1
    assert r3.index.tz is None
    assert r3[Role.OA_AIRFLOW].max() == pytest.approx(900 * 0.58857777, rel=1e-3)  # m3/h -> cfm
    assert r3[Role.SPACE_TEMP].min() == pytest.approx(77.0)  # 25 C
    assert r3[Role.SUPPLY_FAN_STATUS].max() == 1.0  # derived from ahu_fan_speed above 1 Hz
    r1 = store.read_role_frame(facility_id="ds-robod", equip="Room1")
    assert r1[Role.SUPPLY_FAN_STATUS].max() == 1.0  # ... and from fcu_fan_speed
    assert Role.OA_AIRFLOW not in r1.columns
    meta = store.facilities_meta()["ds-robod"]["dataset"]
    assert meta["contiguous"] is False
    assert any("indoor_co2" in n for n in res.notes)
    res2 = run_config(_ops.build_config(entry, store.root))
    sev = {f.equip: f.severity for f in res2.findings if f.rule == "co2_ventilation"}
    assert set(sev) == {"Room1", "Room3"}


# --------------------------------------------------------------------------- sdu-ou44


def _ou44_member(value_fn, day_ids=(2, 0)) -> str:
    rows = []
    for d in day_ids:
        for m in range(0, 24 * 60, 5):
            h, mm = divmod(m, 60)
            rows.append((2018, 3, f"{h:02d}:{mm:02d}:00", d, True, value_fn(d, h)))
    return pd.DataFrame(
        rows, columns=["Year", "Month", "Time", "DayId", "Workday", "room_1"]
    ).to_csv(index=False)


def _ou44_zip() -> bytes:
    occ = lambda d, h: 20.0 if 7 <= h < 14 else 0.0  # noqa: E731
    return _zip(
        {
            "filleddata/co2_room_1.csv": _ou44_member(lambda d, h: 450 + 20 * occ(d, h)),
            "filleddata/vav_room_1.csv": _ou44_member(lambda d, h: 100.0 if occ(d, h) else 0.0),
            "filleddata/temperature_room_1.csv": _ou44_member(lambda d, h: 22.0),
            "filleddata/occupant_count_room_1.csv": _ou44_member(occ),
        }
    )


def test_ou44_joins_per_point_files_on_the_synthetic_day_clock(tmp_path, monkeypatch):
    entry, opener = _synthetic("sdu-ou44", {"Dataset.zip": _ou44_zip()}, ["room1"])
    store, res = _fetch_ingest(entry, opener, tmp_path, monkeypatch)
    assert store.equipment()["ds-sdu-ou44"] == {"ROOM1": "VAV"}
    f = store.read_role_frame(facility_id="ds-sdu-ou44", equip="ROOM1")
    # DayId d is stored on 2000-01-03 + 7 d days: DayId 0 -> Monday 3 Jan, DayId 2 -> Monday 17 Jan
    days = sorted({ts.normalize() for ts in f.index})
    assert days == [pd.Timestamp("2000-01-03"), pd.Timestamp("2000-01-17")]
    assert all(d.dayofweek == 0 for d in days)
    assert set(f.columns) >= {Role.CO2, Role.OA_DAMPER, Role.SPACE_TEMP, Role.OCCUPANCY}
    assert f.loc["2000-01-17 08:00", Role.OCCUPANCY] == 1.0  # derived: count above 0
    assert f.loc["2000-01-17 03:00", Role.OCCUPANCY] == 0.0
    assert f.loc["2000-01-17 08:00", Role.OA_DAMPER] == 100.0
    assert f[Role.SPACE_TEMP].iloc[0] == pytest.approx(71.6)
    meta = store.facilities_meta()["ds-sdu-ou44"]["dataset"]
    assert "synthetic day clock" in meta["clock"] and meta["contiguous"] is False
    assert any(n.startswith("fix: ROOM1 CO2, DayId 7") for n in res.notes)
    res2 = run_config(_ops.build_config(entry, store.root))
    dcv = [f for f in res2.findings if f.rule == "dcv_verification"]
    assert [x.equip for x in dcv] == ["ROOM1"]


def test_ou44_masks_the_forward_filled_co2_stretch_unless_raw(tmp_path, monkeypatch):
    zb = _ou44_zip()
    # DayId 7 is stored on 2000-02-21: the fix masks its CO2 after 18:13
    zb = _zip(
        {
            "filleddata/co2_room_1.csv": _ou44_member(lambda d, h: 500.0, day_ids=(7,)),
            "filleddata/vav_room_1.csv": _ou44_member(lambda d, h: 0.0, day_ids=(7,)),
            "filleddata/temperature_room_1.csv": _ou44_member(lambda d, h: 22.0, day_ids=(7,)),
            "filleddata/occupant_count_room_1.csv": _ou44_member(lambda d, h: 0.0, day_ids=(7,)),
        }
    )
    entry, opener = _synthetic("sdu-ou44", {"Dataset.zip": zb}, ["room1"])
    store, _ = _fetch_ingest(entry, opener, tmp_path, monkeypatch)
    co2 = store.read_role_frame(facility_id="ds-sdu-ou44", equip="ROOM1")[Role.CO2]
    assert co2.loc["2000-02-21 17:00"] == 500.0 and co2.loc["2000-02-21 18:30":].isna().all()
    raw, _ = _fetch_ingest(entry, opener, tmp_path / "raw", monkeypatch, corrections=False)
    co2 = raw.read_role_frame(facility_id="ds-sdu-ou44", equip="ROOM1")[Role.CO2]
    assert co2.loc["2000-02-21 18:30":].notna().all()


# --------------------------------------------------------------------------- ornl-frp-ops


def _frp_building() -> str:
    idx = pd.date_range("2021-03-04 00:00", periods=6 * 60, freq="1min")
    t = np.arange(len(idx))
    comp = np.where((t // 5) % 2 == 0, 4000.0, 2.77)  # 5 minutes on, 5 off
    df = pd.DataFrame(
        {
            "TIMESTAMP": [f"{d.month}/{d.day}/{d.year} {d.hour}:{d.minute:02d}" for d in idx],
            "T_Room_102": 17.0,
            "T_Room_103": 21.0,
            "T_Sup_RTU": 13.9,
            "T_Ret_RTU": 20.0,
            "RH_Sup_RTU": 50.0,
            "RH_Ret_RTU": 30.0,
            "T_VAV_102": 30.0,
            "T_VAV_103": 25.0,
            "WH_RTU_Comp1": comp,
            "WH_RTU_Sup_Fan": 1795.0,
            "WH_RTU_VAV102": 1100.0,
            "WH_RTU_VAV103": 100.0,
            "AF_RTU": 1.49,
            "AF_VAV_102": 0.055,
            "AF_VAV_103": 0.064,
        }
    )
    units = pd.DataFrame([{c: ("Unit" if c == "TIMESTAMP" else "Deg C") for c in df.columns}])
    return pd.concat([units, df]).to_csv(index=False)


def _frp_weather() -> str:
    idx = pd.date_range("2021-03-04 00:00", periods=6 * 60, freq="1min")
    df = pd.DataFrame(
        {
            "TIMESTAMP": [f"{d.month}/{d.day}/{d.year} {d.hour}:{d.minute:02d}" for d in idx],
            "T_out": 4.0,
            "RH_out": 70.0,
        }
    )
    units = pd.DataFrame([{"TIMESTAMP": "Unit", "T_out": "Deg C", "RH_out": "%"}])
    return pd.concat([units, df]).to_csv(index=False)


def test_frp_splits_one_table_into_the_rtu_and_its_boxes_per_scenario(tmp_path, monkeypatch):
    bodies = {
        "Building_Base_Heating.csv": _frp_building().encode(),
        "Weather_Base_Heating.csv": _frp_weather().encode(),
    }
    runs = ["rtu__base_heating", "vav102__base_heating", "vav103__base_heating"]
    runs.append("weather__base_heating")
    entry, opener = _synthetic("ornl-frp-ops", bodies, runs)
    store, res = _fetch_ingest(entry, opener, tmp_path, monkeypatch)
    assert store.equipment()["ds-ornl-frp-ops"] == {
        "RTU__base_heating": "AHU",
        "VAV_102__base_heating": "VAV",
        "VAV_103__base_heating": "VAV",
        "WEATHER__base_heating": "WEATHER",
    }
    v102 = store.read_role_frame(facility_id="ds-ornl-frp-ops", equip="VAV_102__base_heating")
    v103 = store.read_role_frame(facility_id="ds-ornl-frp-ops", equip="VAV_103__base_heating")
    # one mapping, {n} filled per run: each box reads its own room, discharge and reheat columns
    assert v102[Role.SPACE_TEMP].iloc[0] == pytest.approx(62.6)  # 17 C
    assert v103[Role.SPACE_TEMP].iloc[0] == pytest.approx(69.8)  # 21 C
    assert v102[Role.POWER].iloc[0] == pytest.approx(1.1)  # W -> kW
    assert v102[Role.MIXED_AIR_TEMP].iloc[0] == pytest.approx(57.02)  # the RTU supply entering
    rtu = store.read_role_frame(facility_id="ds-ornl-frp-ops", equip="RTU__base_heating")
    assert pd.infer_freq(rtu.index) in ("min", "T")  # kept at 1 minute
    assert rtu[Role.AIRFLOW].iloc[0] == pytest.approx(1.49 * 2118.88, rel=1e-4)  # m3/s -> cfm
    assert set(rtu[Role.COMPRESSOR_STATUS].unique()) == {0.0, 1.0}
    assert rtu[Role.SUPPLY_FAN_STATUS].min() == 1.0
    w = store.read_role_frame(facility_id="ds-ornl-frp-ops", equip="WEATHER__base_heating")
    assert w[Role.OAT].iloc[0] == pytest.approx(39.2)
    # the content hash covers the run-level mappings: editing one re-ingests
    again = _ingest.ingest_dataset(entry, store)
    assert again.skipped and again.content_hash == res.content_hash
    out = run_config(_ops.build_config(entry, store.root))
    cyc = [f for f in out.findings if f.rule == "compressor_short_cycle"]
    assert [f.equip for f in cyc] == ["RTU__base_heating"]
    assert cyc[0].severity == "fault" and cyc[0].metrics["n_starts"] == 36
    sb = [f for f in out.findings if f.rule == "night_weekend_setback"]
    assert sb[0].severity == "fault"  # a 24/7 baseline has no setback, by design


def test_mapping_texts_cover_run_level_mappings():
    entry = DatasetEntry.from_dict(_real("ornl-frp-ops"))
    text = _ingest.mapping_texts(entry)
    assert "T_Room_{n}" in text and "T_Sup_RTU" in text and "T_out" in text
    mp, filled = _ingest.run_mapping(
        entry.ingest, {"mapping": "ornl_frp_ops_vav.json", "vars": {"n": 204}}
    )
    assert mp.role_of("T_Room_204") == Role.SPACE_TEMP and mp.role_of("T_Room_102") is None
    assert "T_Room_204" in filled


# --------------------------------------------------------------------------- supermarket


def _sm_file(day: str, fmt: str, *, mt_ret: float = 45.0) -> bytes:
    idx = pd.date_range(f"{day} 00:00", periods=3 * 3600, freq="1s")
    stamps = (
        idx.strftime(fmt)
        if fmt != "minute"
        else [f"{d.month}/{d.day}/{d.year} {d.hour}:{d.minute:02d}" for d in idx]
    )
    t = np.arange(len(idx))
    df = pd.DataFrame(
        {
            "Timestamp": stamps,
            "W_MT-COMP1": 4.0,
            "W_MT-COMP2": np.where((t // 60) % 4 == 0, 12.0, 0.0),
            "W_LT-COMP1": np.where((t // 300) % 2 == 0, 1.2, 0.0),
            "P-LT-SUC": 175.0,
            "P-MT_SUC": 380.0,
            "P-MT_Dis-OilSepIn": 900.0,
            "T-GC-Fan2-In": 60.0,
            "T-LTcase-Sup": -6.0,
            "T-LTcase-Ret": 2.0,
            "T-MTCase-Sup": 30.0,
            "T-MTCase-Ret": mt_ret,
            "SupHEvap1": np.nan,
        }
    )
    return df.to_csv(index=False).encode()


def test_supermarket_reads_both_stamp_formats_and_drops_baseline_e_return_air(
    tmp_path, monkeypatch
):
    bodies = {
        "BaselineTestE.csv": _sm_file("2019-10-26", "%m/%d/%Y  %H:%M:%S", mt_ret=94.0),
        "Fault3_EvapValveFailure.csv": _sm_file("2019-08-11", "minute"),
    }
    runs = ["lt__baseline_e", "mt__baseline_e", "lt__evap_valve_failure", "mt__evap_valve_failure"]
    entry, opener = _synthetic("ornl-supermarket-fdd", bodies, runs)
    store, res = _fetch_ingest(entry, opener, tmp_path, monkeypatch)
    eq = store.equipment()["ds-ornl-supermarket-fdd"]
    assert eq == {
        "LT_CIRCUIT__baseline_e": "REFRIG_CIRCUIT",
        "MT_CIRCUIT__baseline_e": "REFRIG_CIRCUIT",
        "LT_CIRCUIT__evap_valve_failure": "REFRIG_CIRCUIT",
        "MT_CIRCUIT__evap_valve_failure": "REFRIG_CIRCUIT",
    }
    mt_e = store.read_role_frame(
        facility_id="ds-ornl-supermarket-fdd", equip="MT_CIRCUIT__baseline_e"
    )
    assert Role.RETURN_AIR_TEMP not in mt_e.columns  # the 94 F return air is dropped (fix)
    assert mt_e[Role.SUCTION_PRESSURE].iloc[0] == 380.0  # psig, not converted
    lt_v = store.read_role_frame(
        facility_id="ds-ornl-supermarket-fdd", equip="LT_CIRCUIT__evap_valve_failure"
    )
    # minute stamps (seconds truncated) parse with the run's own format; 3 hours -> 12 bins
    assert len(lt_v) == 12 and lt_v.index[0] == pd.Timestamp("2019-08-11")
    assert lt_v[Role.RETURN_AIR_TEMP].iloc[0] == pytest.approx(2.0)
    assert 0 < lt_v[Role.COMPRESSOR_STATUS].mean() < 1
    meta = store.facilities_meta()["ds-ornl-supermarket-fdd"]["dataset"]
    assert meta["labels"]["LT_CIRCUIT__evap_valve_failure"] == "evap_valve_failure"
    assert meta["labels"]["MT_CIRCUIT__baseline_e"] == ""
    out = run_config(_ops.build_config(entry, store.root))
    assert out.findings == []  # reference only: the template runs no rule
