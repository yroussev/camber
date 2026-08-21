"""Tests for the edge forwarder end-to-end (camber.edge.forwarder)."""

import io
import re

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from camber.edge import Forwarder, Spool, collect_sink
from camber.model.mapping import MappingProvider
from camber.model.roles import Role
from camber.store.parquet_store import ParquetStore


def _mapping():
    return MappingProvider.from_dict(
        {
            "patterns": [
                ["SupplyAirTemp", Role.SUPPLY_AIR_TEMP.value],
                ["OutdoorAirTemp", Role.OAT.value],
            ]
        }
    )


class _FakeSource:
    """A read-only SourceAdapter double: 2 equips, a NaN, and one unmapped 'Junk' token."""

    def __init__(self, start="2024-06-01", n=48):
        self.idx = pd.date_range(start, periods=n, freq="1h")
        self.reads = 0

    def point_names(self):
        return ["AHU_1_SupplyAirTemp", "AHU_1_OutdoorAirTemp", "AHU_2_SupplyAirTemp", "AHU_2_Junk"]

    def load_points(self, names, resample="1h"):
        self.reads += 1
        d = {n: np.linspace(50, 70, len(self.idx)) for n in names}
        d["AHU_1_SupplyAirTemp"] = d["AHU_1_SupplyAirTemp"].copy()
        d["AHU_1_SupplyAirTemp"][5] = np.nan
        return pd.DataFrame(d, index=self.idx)

    def units(self):
        return {}


def _forwarder(tmp_path, sink, **kw):
    return Forwarder(
        _FakeSource(**kw.pop("source_kw", {})),
        sink,
        facility_id="demo-fac-1",
        spool=Spool(str(tmp_path)),
        mapping=_mapping(),
        **kw,
    )


def test_poll_once_end_to_end_long_shape(tmp_path):
    sink, log = collect_sink()
    res = _forwarder(tmp_path, sink).poll_once()
    assert res.forwarded == 1 and res.spooled == 1 and len(log) == 1
    t = pq.read_table(io.BytesIO(log[0]["data"])).to_pandas()
    assert list(t.columns) == ["ts", "equip", "equip_class", "role", "value"]
    assert t["value"].notna().all()  # NaNs dropped -> one row per observation
    assert set(t["role"]) == {"supply_air_temp", "oat"}  # 'Junk' mapped out
    assert set(t["equip"]) == {"AHU_1", "AHU_2"}
    assert str(t["value"].dtype) == "float64"


def test_key_is_the_hive_layout(tmp_path):
    sink, log = collect_sink()
    _forwarder(tmp_path, sink).poll_once()
    assert re.match(
        r"^facility_id=demo-fac-1/year=\d{4}/part-[0-9a-f]{16}\.parquet$", log[0]["key"]
    )


def test_manifest_carries_audit_fields(tmp_path):
    sink, log = collect_sink()
    _forwarder(tmp_path, sink).poll_once()
    m = log[0]["metadata"]
    assert m["facility_id"] == "demo-fac-1" and m["rows"] > 0
    assert len(m["content_sha256"]) == 64 and m["content_sha256"][:16] in log[0]["key"]
    assert isinstance(m["quality"]["min_score"], float) and m["schema_version"] == 1


def test_multi_year_batch_splits_by_partition(tmp_path):
    sink, log = collect_sink()
    # a window straddling the new year -> two objects, one per year=
    fwd = _forwarder(tmp_path, sink, source_kw={"start": "2024-12-30", "n": 72})
    fwd.poll_once()
    years = sorted(re.search(r"year=(\d{4})", o["key"]).group(1) for o in log)
    assert years == ["2024", "2025"]


def test_quality_is_report_only(tmp_path):
    sink, log = collect_sink()
    res = _forwarder(tmp_path, sink).poll_once()
    # the emitted values equal the source values (nothing cleaned/mutated)
    t = pq.read_table(io.BytesIO(log[0]["data"])).to_pandas()
    sat = t[(t.equip == "AHU_1") & (t.role == "supply_air_temp")]["value"]
    assert sat.max() <= 70.0001 and sat.min() >= 50 - 0.0001
    assert res.quality["n_points"] == 3  # AHU_1 SAT+OAT, AHU_2 SAT


def test_lands_in_parquetstore_with_zero_transform(tmp_path):
    """The cloud crux: edge objects at their Hive keys are read by the EXISTING ParquetStore."""
    root = tmp_path / "lake"

    class _Landing:
        def put(self, key, data, *, content_type="", metadata=None):
            p = root / key
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            return {"ok": True}

    _forwarder(tmp_path, _Landing()).poll_once()
    back = ParquetStore(str(root)).read_long(facility_id="demo-fac-1")
    assert len(back) > 0
    assert {"ts", "equip", "equip_class", "role", "value", "facility_id", "year"} <= set(
        back.columns
    )
    assert set(back["role"]) == {"supply_air_temp", "oat"}


def test_empty_window_yields_no_objects(tmp_path):
    sink, log = collect_sink()
    res = _forwarder(tmp_path, sink).poll_once(until="2000-01-01")
    assert res.rows == 0 and res.spooled == 0 and log == []


def test_run_loop_bounded_iterations(tmp_path):
    sink, log = collect_sink()
    fwd = _forwarder(tmp_path, sink)
    slept = []
    fwd.run(1.0, iterations=2, _sleep=slept.append)
    assert fwd.source.reads == 2 and slept == [1.0]  # sleeps between, not after the last


def test_ndjson_wire_format(tmp_path):
    sink, log = collect_sink()
    fwd = Forwarder(
        _FakeSource(),
        sink,
        facility_id="demo-fac-1",
        spool=Spool(str(tmp_path)),
        mapping=_mapping(),
        wire_format="ndjson",
    )
    fwd.poll_once()
    assert log[0]["key"].endswith(".ndjson")
    import json as _json

    rows = [_json.loads(x) for x in log[0]["data"].decode().splitlines()]
    assert rows and set(rows[0]) == {"ts", "equip", "equip_class", "role", "value"}


def test_no_mapping_uses_measure_as_role_and_equip_class(tmp_path):
    sink, log = collect_sink()
    fwd = Forwarder(
        _FakeSource(),
        sink,
        facility_id="demo-fac-1",
        spool=Spool(str(tmp_path)),
        mapping=None,
        quality=False,
        equip_class_of=lambda e: "ahu",
    )
    fwd.poll_once()
    t = pq.read_table(io.BytesIO(log[0]["data"])).to_pandas()
    assert "supplyairtemp" in [r.lower() for r in t["role"]]  # measure suffix became the role
    assert set(t["equip_class"]) == {"ahu"}


def test_bad_wire_format_raises(tmp_path):
    import pytest as _pt

    with _pt.raises(ValueError, match="wire_format"):
        Forwarder(
            _FakeSource(),
            collect_sink()[0],
            facility_id="f",
            spool=Spool(str(tmp_path)),
            wire_format="xml",
        )
