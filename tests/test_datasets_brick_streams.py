"""The ``brick_streams`` adapter (0.96, #75): per-site Brick models whose points name their series.

A tiny synthetic fixture shaped like the ``bts`` entry (no published data): per site a Brick model
whose points carry ``senaps:stream_id`` literals, a stream index CSV and a zip of numbered pickle
files, each ``[name, datetime64 array, float array]``. It runs through the shipped entry's own
ingest spec (sites, index columns, equipment classes, units, resample and hold).
"""

import copy
import hashlib
import io
import os
import pickle
import sys
import zipfile

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber import datasets as ds  # noqa: E402
from camber.datasets import _ingest, _ops  # noqa: E402
from camber.datasets._brickstreams import (  # noqa: E402
    iter_site_series,
    load_series_pickle,
    peek_stream_id,
    site_points,
)
from camber.datasets._catalog import DatasetEntry, validate_catalog  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.store import ParquetStore  # noqa: E402

SAT = "11111111_1111_4111_8111_111111111111"
DMP = "22222222_2222_4222_8222_222222222222"
OAT = "33333333_3333_4333_8333_333333333333"
SAT2 = "44444444_4444_4444_8444_444444444444"
MODE = "55555555_5555_4555_8555_555555555555"
TWO = "66666666_6666_4666_8666_666666666666"
NOFILE = "77777777_7777_4777_8777_777777777777"
SPACE = "88888888_8888_4888_8888_888888888888"

TTL = f"""@prefix bldg: <dch:org/o1/site/s1/building/b1#> .
@prefix brick: <https://brickschema.org/schema/Brick#> .
@prefix senaps: <http://senaps.io/schema/1.0/senaps#> .

bldg:aaaa1111_0000_4000_8000_000000000001 a brick:Air_Handler_Unit .

bldg:aaaa1111_0000_4000_8000_000000000001.p1 a brick:Supply_Air_Temperature_Sensor ;
    senaps:stream_id "{SAT}" ;
    brick:isPointOf bldg:aaaa1111_0000_4000_8000_000000000001 .

bldg:aaaa1111_0000_4000_8000_000000000001.p2 a brick:Supply_Air_Temperature_Sensor ;
    senaps:stream_id "{SAT2}" ;
    brick:isPointOf bldg:aaaa1111_0000_4000_8000_000000000001 .

bldg:dddd2222_0000_4000_8000_000000000002 a brick:Outside_Damper ;
    brick:isPartOf bldg:aaaa1111_0000_4000_8000_000000000001 .

bldg:dddd2222_0000_4000_8000_000000000002.p3 a brick:Damper_Position_Sensor ;
    senaps:stream_id "{DMP}" ;
    brick:isPointOf bldg:dddd2222_0000_4000_8000_000000000002 .

bldg:oat1 a brick:Outside_Air_Temperature_Sensor ;
    senaps:stream_id "{OAT}" .

bldg:aaaa1111_0000_4000_8000_000000000001.p4 a brick:Mode_Status ;
    senaps:stream_id "{MODE}" ;
    brick:isPointOf bldg:aaaa1111_0000_4000_8000_000000000001 .

bldg:zzzz3333_0000_4000_8000_000000000003 a brick:HVAC_Zone .

bldg:zzzz3333_0000_4000_8000_000000000003.p5 a brick:Zone_Air_Temperature_Sensor ;
    senaps:stream_id "{SPACE}",
        "vendor-path.Building1.AHU_Common.ZONE_TEMP" ;
    brick:isPointOf bldg:zzzz3333_0000_4000_8000_000000000003 .

bldg:zzzz3333_0000_4000_8000_000000000003.p6 a brick:Zone_Air_Temperature_Sensor ;
    senaps:stream_id "{NOFILE}" ;
    brick:isPointOf bldg:zzzz3333_0000_4000_8000_000000000003 .
"""


def _index() -> str:
    rows = [
        (SAT, "Supply_Air_Temperature_Sensor"),
        (SAT2, "Supply_Air_Temperature_Sensor"),
        (DMP, "Damper_Position_Sensor"),
        (OAT, "Outside_Air_Temperature_Sensor"),
        (MODE, "Mode_Status"),
        (SPACE, "Zone_Air_Temperature_Sensor"),
        (NOFILE, "Zone_Air_Temperature_Sensor"),
        (TWO, "Point"),  # listed in the index, absent from the model
    ]
    df = pd.DataFrame(rows, columns=["StreamID", "Brick"])
    df.insert(0, "", range(len(df)))
    return df.to_csv(index=False)


def _pickle(site: str, sid: str, times, values) -> bytes:
    t = np.array(pd.to_datetime(times).values, dtype="datetime64[ns]")
    return pickle.dumps(
        [f"Site_{site}_{sid}.pickle", t, np.asarray(values, dtype=float)], protocol=5
    )


def _zip(site: str = "X") -> bytes:
    # UTC stamps every 10 minutes; Australia/Sydney is UTC+11 in January
    t = pd.date_range("2024-01-01 00:03", periods=18, freq="10min")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"Site_{site}aa/", b"")
        z.writestr(f"Site_{site}aa/1.pickle", _pickle(site, SAT, t, [13.0] * 18))
        z.writestr(f"Site_{site}aa/2.pickle", _pickle(site, SAT2, t, [30.0] * 18))
        # damper as a 0-1 fraction; a duplicated stamp keeps its first value
        z.writestr(
            f"Site_{site}aa/3.pickle", _pickle(site, DMP, list(t) + [t[0]], [0.5] * 18 + [0.9])
        )
        # outdoor air: two samples 90 minutes apart; the 1 h hold must leave a gap between them
        z.writestr(f"Site_{site}aa/4.pickle", _pickle(site, OAT, [t[0], t[9]], [20.0, 30.0]))
        z.writestr(f"Site_{site}aa/5.pickle", _pickle(site, MODE, t, [1.0] * 18))
        # a dropout recorded as exactly 0 between readings (site C's pattern)
        z.writestr(f"Site_{site}aa/6.pickle", _pickle(site, SPACE, t, [22.0, 0.0] + [22.0] * 16))
        z.writestr(f"__MACOSX/Site_{site}aa/._6.pickle", b"\x00\x05\x16\x07 resource fork")
        z.writestr(f"Site_{site}aa/7.pickle", _pickle(site, TWO, t, [0.0] * 18))
    return buf.getvalue()


def _files() -> dict:
    return {
        "Site_X.ttl": TTL.encode(),
        "Site_X_metadata.csv": _index().encode(),
        "Site_Xaa.zip": _zip("X"),
    }


class _Opener:
    def __init__(self, bodies: dict):
        self.bodies = bodies

    def open(self, req, timeout=None):
        body = self.bodies[req.full_url.rsplit("/", 1)[-1]]

        class R(io.BytesIO):
            status = 200
            headers = {"Content-Length": str(len(body))}

            def geturl(self):
                return req.full_url

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return R(body)


def _entry(bodies: dict) -> DatasetEntry:
    d = copy.deepcopy(ds.get("bts").as_dict())
    d["id"] = "test-bts"
    d["ingest"]["facility"] = "ds-test-bts"
    d["ingest"]["sites"] = {
        "X": {
            "model": "Site_X.ttl",
            "index": "Site_X_metadata.csv",
            "series": "Site_Xaa.zip",
            "local_timezone": "Australia/Sydney",
        }
    }
    d["files"] = [
        {
            "name": name,
            "url": f"https://example.org/{name}",
            "size": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
            "pinned": True,
            "archive": "zip" if name.endswith(".zip") else None,
        }
        for name, body in bodies.items()
    ]
    d["subsets"] = {
        "default": {"files": "all", "groups": ["X"], "store_bytes_estimate": 1000},
        "full": {"files": "all", "groups": "all", "store_bytes_estimate": 1000},
    }
    d["data_issues"] = []
    d["ingest"].pop("quirks", None)
    assert validate_catalog({"schema": 1, "datasets": [d]}) == []
    return DatasetEntry.from_dict(d)


@pytest.fixture
def entry(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMBER_DATA_DIR", str(tmp_path / "cache"))
    bodies = _files()
    return _entry(bodies), bodies, tmp_path


def test_the_shipped_entry_is_open_cc_by_and_notes_the_licence_split():
    e = ds.get("bts")
    assert e.licence == "CC-BY-4.0" and not e.research_only and e.commercial_ok
    assert e.ingest["adapter"] == "brick_streams"
    text = " ".join(e.known_issues)
    assert "MIT" in text and "CC BY 4.0" in text
    # the default subset is the metadata, the models and the smallest site only
    default = {f["name"] for f in e.subset_files()}
    assert {"Site_Baa.zip"} == {n for n in default if n.endswith(".zip")}
    assert all(f"Site_{s}.ttl" in default for s in "ABC")
    assert e.download_bytes() < 2_000_000_000 < e.download_bytes("full")


def test_safe_unpickler_reads_arrays_and_refuses_anything_else():
    t = pd.date_range("2024-01-01", periods=3, freq="10min").values
    name, tt, vv = load_series_pickle(_pickle("X", SAT, t, [1, 2, 3]))
    assert name.endswith(f"{SAT}.pickle") and tt.dtype.kind == "M" and vv.tolist() == [1, 2, 3]
    assert peek_stream_id(_pickle("X", SAT, t, [1, 2, 3])[:512]) == SAT

    class Evil:
        def __reduce__(self):
            return (os.system, ("echo pwned",))

    with pytest.raises(pickle.UnpicklingError, match="refusing"):
        load_series_pickle(pickle.dumps([f"Site_X_{SAT}.pickle", Evil(), None]))
    with pytest.raises(ValueError, match="name, timestamps, values"):
        load_series_pickle(pickle.dumps({"t": 1}))
    with pytest.raises(ValueError, match="datetime64"):
        load_series_pickle(pickle.dumps(["n", np.arange(3.0), np.arange(3.0)]))


def test_site_points_roles_owners_and_the_counted_rest(entry):
    e, _b, _t = entry
    pts = site_points(TTL, pd.read_csv(io.StringIO(_index()), dtype=str), e.ingest)
    assert pts[SAT].role == Role.SUPPLY_AIR_TEMP and pts[SAT].equip == "AHU_aaaa1111"
    assert pts[DMP].role == Role.OA_DAMPER and pts[DMP].equip == "AHU_aaaa1111"  # via isPartOf
    assert pts[OAT].equip == "site" and pts[OAT].equip_class == "SITE"  # no owner
    assert pts[SPACE].role == Role.SPACE_TEMP and pts[SPACE].equip.startswith("ZONE_")
    assert pts[MODE].role is None and pts[MODE].status == "unmapped"
    assert pts[TWO].status == "not_in_model"
    # the second, non-UUID stream-id literal is never matched: only indexed ids are
    assert not any("vendor" in sid for sid in pts)


def test_iter_site_series_reads_only_the_wanted_members(tmp_path):
    p = tmp_path / "s.zip"
    p.write_bytes(_zip("X"))
    got = {sid: t is not None for sid, t, _v in iter_site_series(str(p), {SAT, OAT})}
    assert got[SAT] and got[OAT] and not got[MODE] and len(got) == 7


def test_ingest_groups_streams_by_brick_owner_with_units_clock_and_hold(entry):
    e, bodies, tmp = entry
    _ops.fetch_dataset(e, opener=_Opener(bodies))
    st = ParquetStore(str(tmp / "store"))
    res = _ingest.ingest_dataset(e, st, subset="default")
    assert res.facilities == ["ds-test-bts-x"]
    eq = st.equipment()["ds-test-bts-x"]
    zone = next(k for k in eq if k.startswith("ZONE_"))
    assert eq == {"AHU_aaaa1111": "AHU", "AHU_aaaa1111-2": "AHU", "site": "SITE", zone: "ZONE"}
    ahu = st.read_role_frame(facility_id="ds-test-bts-x", equip="AHU_aaaa1111")
    # 13 C -> 55.4 F; the second supply-air point of the unit is its own equipment, not averaged
    assert ahu[Role.SUPPLY_AIR_TEMP].dropna().unique().tolist() == [pytest.approx(55.4)]
    second = st.read_role_frame(facility_id="ds-test-bts-x", equip="AHU_aaaa1111-2")
    assert second[Role.SUPPLY_AIR_TEMP].dropna().unique().tolist() == [pytest.approx(86.0)]
    assert ahu[Role.OA_DAMPER].dropna().iloc[0] == pytest.approx(50.0)  # 0.5 -> 50 %
    # UTC 00:03 is 11:03 in Sydney (daylight saving time in January)
    assert ahu.index.min() == pd.Timestamp("2024-01-01 11:00")
    oat = st.read_role_frame(facility_id="ds-test-bts-x", equip="site")[Role.OAT]
    # 20 C held for an hour, then a gap (a 90 min silence outlasts the 1 h hold), then 30 C
    assert oat.iloc[0] == pytest.approx(68.0) and oat.dropna().iloc[-1] == pytest.approx(86.0)
    assert pd.Timestamp("2024-01-01 12:15") not in oat.dropna().index
    meta = st.facilities_meta()["ds-test-bts-x"]["dataset"]
    assert meta["streams_listed"] == 8 and meta["series_files"] == 7
    assert meta["streams_without_file"] == 1 and meta["mapped_without_file"] == 1
    assert meta["duplicate_roles_split"] == 1 and meta["duplicate_stamps_dropped"] == 1
    assert meta["streams_ingested"] == 5 and meta["streams_by_status"]["unmapped"] == 1
    assert meta["local_timezone"] == "Australia/Sydney" and meta["licence"] == "CC-BY-4.0"
    assert meta["unmapped_classes"]["Mode_Status"] == 1
    assert meta["redistribution"] == "allowed"


def test_validation_of_the_brick_streams_spec(entry):
    e, _b, _t = entry

    def errs(mut):
        d = copy.deepcopy(e.as_dict())
        mut(d)
        return validate_catalog({"schema": 1, "datasets": [d]})

    assert any("sites" in x for x in errs(lambda d: d["ingest"].update(sites={})))
    assert any(
        ".model must name" in x
        for x in errs(lambda d: d["ingest"]["sites"]["X"].update(model="nope.ttl"))
    )
    assert any(
        "series must be a zip" in x
        for x in errs(lambda d: d["ingest"]["sites"]["X"].update(series="Site_X.ttl"))
    )
    assert any(
        "IANA" in x
        for x in errs(lambda d: d["ingest"]["sites"]["X"].update(local_timezone="Mars/Base"))
    )
    assert any("series_format" in x for x in errs(lambda d: d["ingest"].update(series_format="x")))
    assert any("equip_classes" in x for x in errs(lambda d: d["ingest"].update(equip_classes={})))
    assert any("ingest.hold" in x for x in errs(lambda d: d["ingest"].update(hold="soon")))
    assert any("site_equip" in x for x in errs(lambda d: d["ingest"].update(site_equip=["a"])))
    assert any(
        "keys of ingest.sites" in x
        for x in errs(lambda d: d["subsets"]["default"].update(groups=["Q"]))
    )
    assert any(
        "need files" in x
        for x in errs(lambda d: d["subsets"]["default"].update(files=["Site_X.ttl"]))
    )


def test_a_fix_quirk_masks_zero_dropouts_and_no_corrections_keeps_them(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMBER_DATA_DIR", str(tmp_path / "cache"))
    bodies = _files()
    d = _entry(bodies).as_dict()
    d["ingest"]["quirks"] = [
        {
            "op": "mask",
            "action": "fix",
            "issue": "zero-dropouts",
            "columns": ["space_temp"],
            "eq": 0,
            "runs": ["X"],
            "note": "zero dropouts",
        }
    ]
    d["data_issues"] = [
        {
            "id": "zero-dropouts",
            "title": "zeros",
            "columns": ["value"],
            "evidence": "1 zero sample in 18",
            "contradicts": {"document": "doc", "citation": "doi:10.1000/x"},
            "handling": "fix",
            "handling_note": "masked",
        }
    ]
    assert validate_catalog({"schema": 1, "datasets": [d]}) == []
    e = DatasetEntry.from_dict(d)
    _ops.fetch_dataset(e, opener=_Opener(bodies))

    def zone_min(corrections):
        st = ParquetStore(str(tmp_path / f"store-{corrections}"))
        res = _ingest.ingest_dataset(e, st, corrections=corrections)
        eq = st.equipment()["ds-test-bts-x"]
        zone = next(k for k in eq if k.startswith("ZONE_"))
        frame = st.read_role_frame(facility_id="ds-test-bts-x", equip=zone)
        return frame[Role.SPACE_TEMP].min(), res.notes

    fixed, notes = zone_min(True)
    raw, raw_notes = zone_min(False)
    assert fixed == pytest.approx(71.6)  # 22 C: the zero never reaches the 15-minute mean
    assert raw < 71.6  # as published, the 0 C sample drags its bin down
    assert any(n.startswith("fix: zero dropouts") for n in notes)
    assert any(n.startswith("fix skipped") for n in raw_notes)
