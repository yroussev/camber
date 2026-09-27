"""The ``per_point`` adapter: one series file per sensor, grouped into facilities by an index.

A tiny synthetic fixture shaped like the ``at-30bldg-sensors`` entry (no published data): a zip
holding a sensor index CSV and one parquet per sensor (change-of-value samples, a duplicated
stamp, a long silence). It runs through the shipped entry's own ingest spec, so the research-only
licence gate, the acknowledgement ledger and the ``redistribution: "prohibited"`` provenance are
exercised end to end.
"""

import copy
import hashlib
import io
import json
import os
import sys
import zipfile

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber import datasets as ds  # noqa: E402
from camber.datasets import _ingest, _ops, _paths  # noqa: E402
from camber.datasets._catalog import DatasetEntry, validate_catalog  # noqa: E402
from camber.datasets._perpoint import facility_id, hold_resample  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.store import ParquetStore  # noqa: E402


def test_hold_resample_carries_a_value_only_within_the_hold():
    t = pd.to_datetime(["2024-01-01 00:10", "2024-01-01 00:20", "2024-01-01 03:05"])
    s = pd.Series([10.0, 20.0, 30.0], index=t)
    plain = hold_resample(s, "1h")
    assert plain.iloc[0] == 15.0 and plain.isna().sum() == 2  # 01:00 and 02:00 have no sample
    held = hold_resample(s, "1h", "1h45min")
    # 01:00 holds 20 (40 min old); 02:00 does not (1 h 40 min at the bin start is within 1h45)
    assert held.tolist() == [15.0, 20.0, 20.0, 30.0]
    short = hold_resample(s, "1h", "50min")
    assert short.iloc[1] == 20.0 and pd.isna(short.iloc[2])
    assert hold_resample(pd.Series(dtype=float), "1h", "1h").empty


def test_facility_id_is_store_safe():
    assert facility_id("ds-x", "B01") == "ds-x-b01"
    assert facility_id("ds-x", "Wing A/2") == "ds-x-wing-a-2"


def _parquet(times, values) -> bytes:
    buf = io.BytesIO()
    pd.DataFrame({"datetime": pd.to_datetime(times), "value": values}).to_parquet(buf, index=False)
    return buf.getvalue()


def _zip() -> bytes:
    idx = pd.DataFrame(
        {
            "sensor_id": ["sensor_aaaa", "sensor_bbbb", "sensor_cccc", "sensor_dddd"],
            "building": ["B01", "B01", "B02", "B02"],
            "sensor_class": ["TeVentSu", "PrVentSu", "TeVentOA", "TeDoHWPi"],
        }
    )
    hours = pd.date_range("2024-01-01", periods=12, freq="1h")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("data/sensor_building_mapping.csv", idx.to_csv(index=False))
        # change-of-value: a sample every 3 h, plus a duplicated stamp with a different value
        t = list(hours[::3]) + [hours[3]]
        z.writestr(
            "data/data_anon/sensor_aaaa.parquet", _parquet(t, [20.0, 21.0, 22.0, 23.0, 99.0])
        )
        z.writestr("data/data_anon/sensor_bbbb.parquet", _parquet(hours, [249.08891] * 12))
        # a 10 h silence: the 8 h 5 min hold must leave the tail missing
        z.writestr("data/data_anon/sensor_cccc.parquet", _parquet(hours[:1], [5.0]))
        z.writestr("data/data_anon/sensor_dddd.parquet", _parquet(hours, [50.0] * 12))
    return buf.getvalue()


class _Opener:
    def __init__(self, body: bytes):
        self.body = body

    def open(self, req, timeout=None):
        body = self.body

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


def _entry(zb: bytes) -> DatasetEntry:
    d = copy.deepcopy(ds.get("at-30bldg-sensors").as_dict())
    d["id"] = "test-perpoint"
    d["ingest"]["facility"] = "ds-test-perpoint"
    d["files"] = [
        {
            "name": "data.zip",
            "url": "https://example.org/data.zip",
            "size": len(zb),
            "sha256": hashlib.sha256(zb).hexdigest(),
            "pinned": True,
            "archive": "zip",
        }
    ]
    d["subsets"] = {
        "default": {"files": "all", "groups": ["B01"], "store_bytes_estimate": 1000},
        "full": {"files": "all", "groups": "all", "store_bytes_estimate": 1000},
    }
    assert validate_catalog({"schema": 1, "datasets": [d]}) == []
    return DatasetEntry.from_dict(d)


@pytest.fixture
def entry(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMBER_DATA_DIR", str(tmp_path / "cache"))
    zb = _zip()
    return _entry(zb), zb, tmp_path


def test_research_only_gate_ledger_and_provenance(entry):
    e, zb, tmp = entry
    assert e.research_only and e.licence == "CC-BY-NC-SA-4.0"
    with pytest.raises(PermissionError, match="accept_noncommercial"):
        _ops.fetch_dataset(e, opener=_Opener(zb))
    root = _paths.data_dir(None)
    assert not os.path.exists(os.path.join(root, "acknowledgements.json"))
    _ops.fetch_dataset(e, opener=_Opener(zb), accept_noncommercial=True)
    with open(os.path.join(root, "acknowledgements.json"), encoding="utf-8") as fh:
        ledger = json.load(fh)
    recs = ledger if isinstance(ledger, list) else ledger.get("acknowledgements", [])
    assert any(
        r["dataset_id"] == "test-perpoint" and r["licence"] == "CC-BY-NC-SA-4.0" for r in recs
    )
    st = ParquetStore(str(tmp / "store"))
    res = _ingest.ingest_dataset(e, st, subset="full")  # the fetch's acknowledgement counts
    assert res.facilities == ["ds-test-perpoint-b01", "ds-test-perpoint-b02"]
    for fid in res.facilities:
        meta = st.facilities_meta()[fid]["dataset"]
        assert meta["redistribution"] == "prohibited" and meta["access"] == "research_only"
        assert meta["acknowledged_licence"] == "CC-BY-NC-SA-4.0"


def test_ingest_groups_sensors_into_facilities_with_hold_and_units(entry):
    e, zb, tmp = entry
    _ops.fetch_dataset(e, opener=_Opener(zb), accept_noncommercial=True)
    st = ParquetStore(str(tmp / "store"))
    res = _ingest.ingest_dataset(e, st, subset="full")
    eq = st.equipment()
    assert eq["ds-test-perpoint-b01"] == {
        "sensor_aaaa": "SENSOR_TEVENT",
        "sensor_bbbb": "SENSOR_PRVENT",
    }
    assert eq["ds-test-perpoint-b02"] == {"sensor_cccc": "SENSOR_TEVENT"}  # TeDoHWPi has no role
    meta = st.facilities_meta()["ds-test-perpoint-b01"]["dataset"]
    assert meta["duplicate_stamps_dropped"] == 1 and meta["hold"] == "8h5min"
    assert meta["sensor_classes"] == {"PrVentSu": 1, "TeVentSu": 1}
    sat = st.read_role_frame(facility_id="ds-test-perpoint-b01", equip="sensor_aaaa")
    # every hour filled by the hold; the duplicate kept its first value (21 C = 69.8 F)
    assert sat[Role.SUPPLY_AIR_TEMP].notna().sum() == 10
    assert sat[Role.SUPPLY_AIR_TEMP].max() == pytest.approx(73.4)  # 23 C
    assert sat.loc["2024-01-01 04:00", Role.SUPPLY_AIR_TEMP] == pytest.approx(69.8)
    dp = st.read_role_frame(facility_id="ds-test-perpoint-b01", equip="sensor_bbbb")
    assert dp[Role.DUCT_STATIC].iloc[0] == pytest.approx(1.0)  # 249.09 Pa -> 1 inH2O
    oa = st.read_role_frame(facility_id="ds-test-perpoint-b02", equip="sensor_cccc")
    assert oa[Role.OAT].notna().sum() == 1  # one sample, nothing after it to hold into
    assert res.equipment == 3
    # the default subset keeps one building; re-ingest replaces and drops the other facility
    res2 = _ingest.ingest_dataset(e, st, subset="default")
    assert res2.facilities == ["ds-test-perpoint-b01"]
    assert "ds-test-perpoint-b02" not in st.facilities()


def test_validation_of_the_per_point_spec(entry):
    e, _zb, _tmp = entry

    def errs(mut):
        d = copy.deepcopy(e.as_dict())
        mut(d)
        return validate_catalog({"schema": 1, "datasets": [d]})

    assert any(
        "'{id}'" in x for x in errs(lambda d: d["ingest"]["series"].update(member="a.parquet"))
    )
    assert any(
        "class_map" in x for x in errs(lambda d: d["ingest"]["class_map"].update(X=["C", "nope"]))
    )
    assert any(
        "ingest.index.file" in x for x in errs(lambda d: d["ingest"]["index"].update(file="z"))
    )
    assert any("ingest.hold" in x for x in errs(lambda d: d["ingest"].update(hold="soon")))
    assert any("groups" in x for x in errs(lambda d: d["subsets"]["default"].update(groups="B01")))
    assert any("ingest.index.id" in x for x in errs(lambda d: d["ingest"]["index"].pop("id")))
    # intake D's name for the sensor index before 0.89 reconciled it
    assert any(
        "ingest.points was renamed ingest.index" in x
        for x in errs(lambda d: d["ingest"].update(points=d["ingest"].pop("index")))
    )
