"""Brick ``owner`` grouping at ingest: per-quantity tables split into equipment by a Brick model.

A tiny Building-59-shaped fixture (no real data): one zip holding a Brick TTL and two wide CSVs,
each carrying one quantity for two rooftop units. Points belong to their unit through
``hasPoint``, the inverse ``isPointOf``, or a part (``hasPart``) of the unit; the mapping file's
overrides win over the model.
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

from camber.datasets import _ingest, _ops  # noqa: E402
from camber.datasets._brickgroup import grouping_from_brick  # noqa: E402
from camber.datasets._catalog import DatasetEntry, validate_catalog  # noqa: E402
from camber.interop.brick import brick_mapping_report, part_parents_from_brick  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.store import ParquetStore  # noqa: E402

TTL = """
@prefix bldg: <http://example.org/building#> .
@prefix brick: <https://brickschema.org/schema/1.1/Brick#> .

bldg:RTU01 a brick:Rooftop_Unit ;
    brick:hasPart bldg:RTU01_damper ;
    brick:hasPoint bldg:rtu_001_sa_temp,
        bldg:rtu_001_ma_temp .

bldg:RTU02 a brick:Rooftop_Unit ;
    brick:hasPoint bldg:rtu_002_sa_temp .

bldg:RTU01_damper a brick:Outside_Damper ;
    brick:hasPoint bldg:rtu_001_oa_damper .

bldg:rtu_001_sa_temp a brick:Supply_Air_Temperature_Sensor .
bldg:rtu_002_sa_temp a brick:Supply_Air_Temperature_Sensor .
bldg:rtu_001_ma_temp a brick:Mixed_Air_Temperature_Sensor .
bldg:rtu_001_oa_damper a brick:Damper_Position_Sensor .

bldg:rtu_002_ma_temp a brick:Mixed_Air_Temperature_Sensor ;
    brick:isPointOf bldg:RTU02 .

bldg:oat_1 a brick:Outside_Air_Temperature_Sensor ;
    brick:isPointOf bldg:Building .
bldg:Building a brick:Building .
"""


def test_report_carries_owner_via_haspoint_ispointof_and_parts():
    rep = {p.point: p for p in brick_mapping_report(TTL, backend="minimal").points}
    assert (rep["rtu_001_sa_temp"].owner, rep["rtu_001_sa_temp"].owner_class) == (
        "RTU01",
        "Rooftop_Unit",
    )
    assert rep["rtu_002_ma_temp"].owner == "RTU02"  # isPointOf, the inverse
    assert rep["rtu_001_oa_damper"].owner == "RTU01_damper"
    assert rep["oat_1"].owner == "Building"
    assert brick_mapping_report(TTL).as_dict()["points"][0]["owner"]
    assert part_parents_from_brick(TTL, backend="minimal")["RTU01_damper"] == ["RTU01"]


@pytest.mark.parametrize("backend", ["minimal", "auto"])
def test_grouping_walks_up_to_the_mapped_equipment(backend):
    g = grouping_from_brick(TTL, {"Rooftop_Unit": "AHU"}, backend=backend)
    assert g.points["rtu_001_oa_damper"].equip == "RTU01"  # damper part -> its unit
    assert g.points["rtu_002_ma_temp"].equip == "RTU02"
    assert g.points["oat_1"].source == "fallback"  # Building is not an equipment class


def _csv(cols: dict) -> bytes:
    idx = pd.date_range("2020-01-01", periods=8 * 4, freq="15min")
    df = pd.DataFrame({"date": idx.strftime("%Y-%m-%d %H:%M:%S")})
    for name, v in cols.items():
        df[name] = v
    return df.to_csv(index=False).encode()


def _zip() -> bytes:
    n = 32
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("B/model.ttl", TTL)
        z.writestr(
            "B/data/rtu_sa_t.csv",
            _csv({"rtu_001_sa_temp": np.full(n, 55.0), "rtu_002_sa_temp": np.full(n, 57.0)}),
        )
        z.writestr(
            "B/data/rtu_misc.csv",
            _csv(
                {
                    "rtu_001_ma_temp": np.full(n, 65.0),
                    "rtu_002_ma_temp": np.full(n, 66.0),
                    "rtu_001_oa_damper": np.full(n, 40.0),
                    "oat_1": np.full(n, 50.0),
                    "UNKNOWN_pt": np.ones(n),
                    "rtu_002_sa_temp_dup": np.full(n, 99.0),
                }
            ),
        )
    return buf.getvalue()


def _entry(zb: bytes, mapping: str | None = None) -> DatasetEntry:
    members = ["B/model.ttl", "B/data/rtu_sa_t.csv", "B/data/rtu_misc.csv"]
    run = {"file": "b.zip", "equip": "building", "class": "WEATHER", "label": "", "group": "brick"}
    d = {
        "id": "test-brick",
        "title": "Brick-grouped test building",
        "summary": "two rooftop units, per-quantity tables",
        "publisher": "Test Lab",
        "citation": "Test Lab (2026).",
        "landing_url": "https://example.org/b",
        "licence": "CC-BY-4.0",
        "access": "open",
        "verified_on": "2026-09-26",
        "kind": "real",
        "files": [
            {
                "name": "b.zip",
                "url": "https://example.org/b.zip",
                "size": len(zb),
                "sha256": hashlib.sha256(zb).hexdigest(),
                "pinned": True,
                "archive": "zip",
                "members": members,
            }
        ],
        "subsets": {
            k: {"files": "all", "runs": "all", "store_bytes_estimate": 1000}
            for k in ("default", "full")
        },
        "ingest": {
            "adapter": "wide_csv",
            "facility": "ds-test-brick",
            "timestamp": "date",
            "resample": "15min",
            "brick": {
                "file": "b.zip",
                "member": "B/model.ttl",
                "equip_classes": {"Rooftop_Unit": "AHU"},
            },
            "runs": [
                dict(run, id="sa_t", member=members[1]),
                dict(run, id="misc", member=members[2]),
            ],
        },
    }
    if mapping:
        d["ingest"]["mapping"] = mapping
    errs = validate_catalog({"schema": 1, "datasets": [d]})
    assert errs == [], errs
    return DatasetEntry.from_dict(d)


class _Opener:
    def __init__(self, body):
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


@pytest.fixture
def fetched(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMBER_DATA_DIR", str(tmp_path / "cache"))
    zb = _zip()
    entry = _entry(zb)
    _ops.fetch_dataset(entry, opener=_Opener(zb))
    return entry, zb, tmp_path


def test_ingest_groups_columns_from_several_files_into_equipment(fetched):
    entry, _zb, tmp = fetched
    st = ParquetStore(str(tmp / "store"))
    res = _ingest.ingest_dataset(entry, st)
    eq = st.equipment()["ds-test-brick"]
    assert eq == {"RTU01": "AHU", "RTU02": "AHU", "building": "WEATHER"}
    r1 = st.read_role_frame(facility_id="ds-test-brick", equip="RTU01")
    assert set(r1.columns) == {Role.SUPPLY_AIR_TEMP, Role.MIXED_AIR_TEMP, Role.OA_DAMPER}
    assert r1[Role.SUPPLY_AIR_TEMP].iloc[0] == 55.0  # from rtu_sa_t.csv
    assert r1[Role.MIXED_AIR_TEMP].iloc[0] == 65.0  # from rtu_misc.csv
    r2 = st.read_role_frame(facility_id="ds-test-brick", equip="RTU02")
    assert r2[Role.MIXED_AIR_TEMP].iloc[0] == 66.0  # owned via isPointOf
    w = st.read_role_frame(facility_id="ds-test-brick", equip="building")
    assert list(w.columns) == [Role.OAT]
    meta = st.facilities_meta()["ds-test-brick"]["dataset"]
    assert meta["grouping"] == "brick" and meta["labels"]["RTU01"] == ""
    assert res.equipment == 3


def test_mapping_file_overrides_win(fetched, monkeypatch):
    from camber.datasets import _catalog
    from camber.datasets import _ingest as ing

    _entry0, zb, tmp = fetched
    overrides = {
        "aliases": {"rtu_002_sa_temp_dup": "supply_air_temp", "rtu_002_sa_temp": "return_air_temp"},
        "equipment": {"rtu_002_sa_temp_dup": "RTU02", "oat_1": "Weather Station"},
        "equipment_classes": {"Weather_Station": "WEATHER", "RTU02": "RTU"},
    }
    real = _catalog.package_text

    def fake(*parts):
        if parts == ("mappings", "test_brick.json"):
            return json.dumps(overrides)
        return real(*parts)

    monkeypatch.setattr(_catalog, "package_text", fake)
    monkeypatch.setattr(ing, "package_text", fake)
    monkeypatch.setattr(_catalog, "_package_has", lambda *p: True)
    entry = _entry(zb, mapping="test_brick.json")
    st = ParquetStore(str(tmp / "store"))
    res = _ingest.ingest_dataset(entry, st)
    eq = st.equipment()["ds-test-brick"]
    assert eq["RTU02"] == "RTU" and eq["Weather_Station"] == "WEATHER"
    r2 = st.read_role_frame(facility_id="ds-test-brick", equip="RTU02")
    assert Role.RETURN_AIR_TEMP in r2.columns and r2[Role.SUPPLY_AIR_TEMP].iloc[0] == 99.0
    assert any("mapping file overrides Brick" in n for n in res.notes)


def test_validation_of_the_brick_spec(fetched):
    entry, _zb, _tmp = fetched

    def fresh():
        return copy.deepcopy(entry.as_dict())

    d = fresh()
    d["ingest"]["brick"]["equip_classes"] = {}
    assert any("equip_classes" in e for e in validate_catalog({"schema": 1, "datasets": [d]}))
    d = fresh()
    del d["ingest"]["brick"]
    assert any(
        "ingest.brick is not set" in e for e in validate_catalog({"schema": 1, "datasets": [d]})
    )
    d = fresh()
    d["ingest"]["brick"]["member"] = "B/other.ttl"
    assert any("is not in b.zip" in e for e in validate_catalog({"schema": 1, "datasets": [d]}))
    d = fresh()
    d["ingest"]["runs"][0]["group"] = "haystack"
    assert any(
        "group must be 'brick'" in e for e in validate_catalog({"schema": 1, "datasets": [d]})
    )
