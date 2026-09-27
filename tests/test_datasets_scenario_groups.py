"""Grouped scenario runs without a Brick model, and the ingest features 0.89's entries use.

Tiny synthetic fixtures only (no published data):

* ``group: "mapping"`` splits one wide table into an air handler and its boxes by the mapping
  file's ``equipment`` map; a run's ``target`` names the box under test -- the only equipment that
  carries the run's label -- and the other boxes are recorded as unscored context;
* ``derive: {"copy": ...}`` hands one building-wide schedule column to every box;
* an elapsed ``clock`` (``{"kind": "elapsed", "unit": "s", "origin": ...}``) reads a simulation
  clock in seconds, ``encoding`` a Latin-1 CSV, and ``m3/min`` converts to cfm;
* an ORNL-FRP-shaped workbook (unit row under the header, ``NAN`` strings) runs through the shipped
  ``ornl-frp-vav`` ingest spec and mapping.
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

from camber import datasets as ds  # noqa: E402
from camber.datasets import _ingest, _ops  # noqa: E402
from camber.datasets._catalog import DatasetEntry, validate_catalog  # noqa: E402
from camber.datasets._units import convert_series  # noqa: E402
from camber.model.mapping import MappingProvider  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.store import ParquetStore  # noqa: E402

MAPPING = {
    "aliases": {
        "AHU_SAT": "supply_air_temp",
        "AHU_FLOW": "airflow",
        "B1_FLOW": "airflow",
        "B1_TEMP": "space_temp",
        "B2_FLOW": "airflow",
        "B2_TEMP": "space_temp",
        "CSP_B1": "cool_sp",
        "CSP_B2": "cool_sp",
    },
    "equipment": {
        "AHU_SAT": "AHU",
        "AHU_FLOW": "AHU",
        "B1_FLOW": "AHU_VAV_1",
        "B1_TEMP": "AHU_VAV_1",
        "CSP_B1": "AHU_VAV_1",
        "B2_FLOW": "AHU_VAV_2",
        "B2_TEMP": "AHU_VAV_2",
        "CSP_B2": "AHU_VAV_2",
    },
    "equipment_classes": {"AHU": "AHU", "AHU_VAV_1": "VAV", "AHU_VAV_2": "VAV"},
}


def _csv(day: str, b1_flow: float) -> bytes:
    idx = pd.date_range(day, periods=8, freq="15min")
    df = pd.DataFrame(
        {
            "ts": idx.strftime("%Y-%m-%d %H:%M:%S"),
            "AHU_SAT": np.full(8, 13.0),
            "AHU_FLOW": np.full(8, 2.0),
            "B1_FLOW": np.full(8, b1_flow),
            "B1_TEMP": np.full(8, 22.0),
            "B2_FLOW": np.full(8, 1.0),
            "B2_TEMP": np.full(8, 23.0),
            "SCHED_CSP": np.full(8, 24.0),
            "UNMAPPED": np.ones(8),
        }
    )
    return df.to_csv(index=False).encode()


class _Opener:
    def __init__(self, bodies: dict):
        self.bodies = bodies

    def open(self, req, timeout=None):
        body = self.bodies[req.full_url]

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


def _file(name: str, body: bytes) -> dict:
    return {
        "name": name,
        "url": f"https://example.org/{name}",
        "size": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "pinned": True,
    }


def _entry_dict(bodies: dict) -> dict:
    base = {"equip": "AHU", "class": "AHU", "group": "mapping", "target": ["AHU_VAV_1"]}
    return {
        "id": "test-groups",
        "title": "grouped scenario test",
        "summary": "one AHU and two boxes per scenario",
        "publisher": "Test Lab",
        "citation": "Test Lab (2026).",
        "landing_url": "https://example.org/g",
        "licence": "CC-BY-4.0",
        "access": "open",
        "verified_on": "2026-09-26",
        "kind": "real",
        "labeled_faults": True,
        "labels": {"fault_types": {"terminal_damper": "stuck damper"}},
        "files": [_file(n, b) for n, b in bodies.items()],
        "subsets": {
            k: {"files": "all", "runs": "all", "store_bytes_estimate": 1000}
            for k in ("default", "full")
        },
        "ingest": {
            "adapter": "wide_csv",
            "facility": "ds-test-groups",
            "mapping": "test_groups.json",
            "timestamp": "ts",
            "resample": "15min",
            "units": {"supply_air_temp": "degC", "space_temp": "degC", "cool_sp": "degC"},
            "derive": [
                {"column": "CSP_B1", "copy": "SCHED_CSP"},
                {"column": "CSP_B2", "copy": "SCHED_CSP"},
            ],
            "runs": [
                dict(base, id="s_fault_free", file="ff.csv", label=""),
                dict(base, id="s_stuck_000", file="st.csv", label="terminal_damper"),
            ],
        },
    }


@pytest.fixture
def grouped(tmp_path, monkeypatch):
    from camber.datasets import _catalog

    real = _catalog.package_text

    def fake(*parts):
        if parts == ("mappings", "test_groups.json"):
            return json.dumps(MAPPING)
        return real(*parts)

    monkeypatch.setattr(_catalog, "package_text", fake)
    monkeypatch.setattr(_ingest, "package_text", fake)
    monkeypatch.setattr(_catalog, "_package_has", lambda *p: True)
    monkeypatch.setenv("CAMBER_DATA_DIR", str(tmp_path / "cache"))
    bodies = {"ff.csv": _csv("2023-08-03", 3.0), "st.csv": _csv("2023-08-04", 0.0)}
    d = _entry_dict(bodies)
    assert validate_catalog({"schema": 1, "datasets": [d]}) == []
    entry = DatasetEntry.from_dict(d)
    _ops.fetch_dataset(
        entry, opener=_Opener({f"https://example.org/{n}": b for n, b in bodies.items()})
    )
    return entry, d, tmp_path


def test_mapping_group_splits_scenarios_and_scores_only_the_target(grouped):
    entry, _d, tmp = grouped
    st = ParquetStore(str(tmp / "store"))
    res = _ingest.ingest_dataset(entry, st)
    eq = st.equipment()["ds-test-groups"]
    assert eq == {
        "AHU__s_fault_free": "AHU",
        "AHU_VAV_1__s_fault_free": "VAV",
        "AHU_VAV_2__s_fault_free": "VAV",
        "AHU__s_stuck_000": "AHU",
        "AHU_VAV_1__s_stuck_000": "VAV",
        "AHU_VAV_2__s_stuck_000": "VAV",
    }
    meta = st.facilities_meta()["ds-test-groups"]["dataset"]
    # only the box under test is labelled (a fault-free scenario keeps its own equipment)
    assert meta["labels"] == {
        "AHU_VAV_1__s_fault_free": "",
        "AHU_VAV_1__s_stuck_000": "terminal_damper",
    }
    assert set(meta["context"]) == {
        "AHU__s_fault_free",
        "AHU_VAV_2__s_fault_free",
        "AHU__s_stuck_000",
        "AHU_VAV_2__s_stuck_000",
    }
    assert meta["context"]["AHU_VAV_2__s_stuck_000"] == {
        "run": "s_stuck_000",
        "label": "terminal_damper",
    }
    assert meta["grouping"] == "mapping" and res.equipment == 6
    assert not any("overrides Brick" in n for n in res.notes)
    box = st.read_role_frame(facility_id="ds-test-groups", equip="AHU_VAV_2__s_stuck_000")
    assert set(box.columns) == {Role.AIRFLOW, Role.SPACE_TEMP, Role.COOL_SP}
    assert box[Role.COOL_SP].iloc[0] == pytest.approx(75.2)  # the copied schedule, degC -> degF
    assert box[Role.SPACE_TEMP].iloc[0] == pytest.approx(73.4)
    ahu = st.read_role_frame(facility_id="ds-test-groups", equip="AHU__s_stuck_000")
    assert set(ahu.columns) == {Role.SUPPLY_AIR_TEMP, Role.AIRFLOW}


def test_scores_count_only_the_box_under_test(grouped):
    entry, _d, tmp = grouped
    st = ParquetStore(str(tmp / "store"))
    _ingest.ingest_dataset(entry, st)
    findings = [
        {"rule": "r", "equip": "AHU_VAV_1__s_stuck_000", "severity": "fault"},
        {"rule": "r", "equip": "AHU_VAV_2__s_fault_free", "severity": "fault"},  # context: ignored
    ]
    out = _ops.score_dataset(entry, st, findings=findings, rules=["r"])
    assert out["n"] == 2 and out["overall"]["tp"] == 1 and out["overall"]["fp"] == 0


def test_validation_of_groups_targets_copy_clock_and_encoding(grouped):
    _entry0, d0, _tmp = grouped

    def errs(mut):
        d = copy.deepcopy(d0)
        mut(d)
        return validate_catalog({"schema": 1, "datasets": [d]})

    def no_mapping(d):
        del d["ingest"]["mapping"]

    assert any("needs ingest.mapping" in e for e in errs(no_mapping))
    assert any(
        "'target'" in e for e in errs(lambda d: d["ingest"]["runs"][0].update(target="AHU_VAV_1"))
    )
    assert any("'target'" in e for e in errs(lambda d: d["ingest"]["runs"][0].pop("group")))
    assert any(
        "group must be 'brick' or 'mapping'" in e
        for e in errs(lambda d: d["ingest"]["runs"][0].update(group="zone"))
    )
    assert any(
        "exactly one of" in e
        for e in errs(lambda d: d["ingest"]["derive"][0].update(sum=["A", "B"]))
    )
    assert any(
        "needs an ISO 'origin'" in e
        for e in errs(lambda d: d["ingest"].update(clock={"kind": "elapsed", "unit": "s"}))
    )
    assert any(
        "an elapsed clock's unit" in e
        for e in errs(
            lambda d: d["ingest"].update(
                clock={"kind": "elapsed", "unit": "weeks", "origin": "2025-01-01"}
            )
        )
    )
    assert any(
        "ingest.timestamp_unit is not a key; use clock:" in e  # intake D's pre-0.89 name
        for e in errs(
            lambda d: d["ingest"].update(timestamp_unit="s", timestamp_origin="2025-01-01")
        )
    )
    assert any(
        "text encoding" in e
        for e in errs(lambda d: d["ingest"]["runs"][1].update(encoding="klingon-8"))
    )
    assert (
        errs(
            lambda d: d["ingest"].update(
                clock={"kind": "elapsed", "unit": "s", "origin": "2025-01-01"}, encoding="latin-1"
            )
        )
        == []
    )


def test_seconds_clock_and_latin1_header(tmp_path):
    # a Modelica-style export: seconds since 1 January, Kelvin, a Latin-1 degree sign
    body = "time (s),T (°K)\n0,293.15\n60,294.15\n120,\n".encode("latin-1")
    path = tmp_path / "sim.csv"
    path.write_bytes(body)
    spec = {
        "timestamp": "time (s)",
        "clock": {"kind": "elapsed", "unit": "s", "origin": "2025-01-01"},
        "encoding": "latin-1",
    }
    mapping = MappingProvider.from_dict({"aliases": {"T (°K)": "supply_air_temp"}})
    raw, _ = _ingest.read_raw_run(str(path), mapping, spec)
    assert list(raw.index) == list(pd.date_range("2025-01-01", periods=3, freq="1min"))
    assert raw["T (°K)"].iloc[1] == pytest.approx(294.15)
    with pytest.raises(UnicodeDecodeError):  # the declared encoding is what makes it readable
        _ingest.read_raw_run(str(path), mapping, {**spec, "encoding": "utf-8"})


def test_cubic_metres_per_minute_convert_to_cfm():
    assert convert_series(pd.Series([1.0]), "m3/min").iloc[0] == pytest.approx(35.3146667)
    assert convert_series(pd.Series([8.5]), "m³/min").iloc[0] == pytest.approx(300.17, abs=0.01)


# --------------------------------------------------------------------------- the ornl-frp-vav spec


def _ornl_sheet(day: str, rooms, stuck: float | None) -> pd.DataFrame:
    """One ORNL-FRP-shaped sheet: a unit row under the header, 'NAN' strings with the fan off."""
    idx = pd.date_range(day, periods=24 * 4, freq="15min")
    occ = ((idx.hour >= 7) & (idx.hour < 22)).astype(int)
    on = occ == 1
    cols = {
        "TIMESTAMP": list(idx),
        "Terminal: Room Air Temperature Heating Setpoint": np.where(on, 21.0, 15.6),
        "Terminal: Room Air Temperature Cooling Setpoint": np.where(on, 24.0, 29.4),
        "Occupancy Mode Indicator": occ,
        "RTU: Supply Air Temperature": np.where(on, 12.8, np.nan),
        "RTU: Return Air Temperature": np.where(on, 23.0, np.nan),
        "RTU: Mixed Air Temperature": np.full(len(idx), 22.0),
        "RTU: Supply Air Volumetric Flow Rate": np.where(on, 80.0, np.nan),
        "RTU: Static Pressure": np.where(on, 249.0, 16.0),
        "RTU: Supply Air Fan Electricity ": np.where(on, 25.0, 0.32),
        "RTU: Outdoor Air Damper Opening": np.where(on, 10.0, 0.0),
        "Outdoor air temperature": np.full(len(idx), 25.0),
    }
    for r in rooms:
        cols[f"VAV Box - Room {r}: Discharge Airflow Rate"] = np.where(on, 8.5, np.nan)
        pos = stuck if (stuck is not None and r == "205") else 40.0
        cols[f"VAV Box - Room {r}: VAV Damper Opening"] = np.full(len(idx), pos)
        cols[f"Room {r}: Room Temperature"] = np.full(len(idx), 23.5)
    df = pd.DataFrame(cols).astype(object)
    df = df.where(pd.notna(df), "NAN")
    units = {c: ("°C" if "Temperature" in c or "temperature" in c else "") for c in df.columns}
    units["TIMESTAMP"] = None
    return pd.concat([pd.DataFrame([units]), df], ignore_index=True)


def test_ornl_frp_vav_spec_ingests_a_synthetic_workbook(tmp_path, monkeypatch):
    pytest.importorskip("openpyxl")
    monkeypatch.setenv("CAMBER_DATA_DIR", str(tmp_path / "cache"))
    rooms = ["102", "103", "104", "105", "106", "202", "203", "204", "205", "206"]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        _ornl_sheet("2023-12-14", rooms, None).to_excel(xw, sheet_name="Fault-Free", index=False)
        _ornl_sheet("2023-12-02", rooms, 0.0).to_excel(
            xw, sheet_name="Damper Stuck at 0% Open", index=False
        )
    body = buf.getvalue()
    d = copy.deepcopy(ds.get("ornl-frp-vav").as_dict())
    name = "Set_03_Damper Tests_Room 205.xlsx"
    d["id"] = "test-ornl"
    d["ingest"]["facility"] = "ds-test-ornl"
    d["files"] = [dict(_file("set3.xlsx", body), name=name)]
    d["ingest"]["runs"] = [
        r for r in d["ingest"]["runs"] if r["id"] in ("d3_fault_free", "d3_stuck_000")
    ]
    d["subsets"] = {
        k: {"files": "all", "runs": "all", "store_bytes_estimate": 1000}
        for k in ("default", "full")
    }
    d["data_issues"] = [
        i
        for i in d["data_issues"]
        if not i.get("runs") or i["id"] == "mixed-air-below-return-in-cooling"
    ]
    assert validate_catalog({"schema": 1, "datasets": [d]}) == []
    entry = DatasetEntry.from_dict(d)
    _ops.fetch_dataset(entry, opener=_Opener({"https://example.org/set3.xlsx": body}))
    st = ParquetStore(str(tmp_path / "store"))
    res = _ingest.ingest_dataset(entry, st)
    eq = st.equipment()["ds-test-ornl"]
    assert len(eq) == 2 * 11  # per day: the RTU and its ten boxes
    assert eq["RTU__d3_stuck_000"] == "AHU" and eq["RTU_VAV_104__d3_stuck_000"] == "VAV"
    meta = st.facilities_meta()["ds-test-ornl"]["dataset"]
    assert meta["labels"] == {
        "RTU_VAV_205__d3_fault_free": "",
        "RTU_VAV_205__d3_stuck_000": "terminal_damper",
    }
    assert meta["redistribution"] == "allowed" and res.warnings == []
    box = st.read_role_frame(facility_id="ds-test-ornl", equip="RTU_VAV_205__d3_stuck_000")
    assert box[Role.DAMPER].max() == 0.0
    assert box[Role.AIRFLOW].max() == pytest.approx(8.5 * 35.3146667)  # m3/min -> cfm
    assert box[Role.COOL_SP].max() == pytest.approx(85.0, abs=0.1)  # 29.4 C unoccupied
    assert set(box.columns) >= {Role.SPACE_TEMP, Role.HEAT_SP, Role.OCCUPANCY}
    rtu = st.read_role_frame(facility_id="ds-test-ornl", equip="RTU__d3_stuck_000")
    assert rtu[Role.SUPPLY_FAN_STATUS].sum() == 15 * 4  # derived from fan electricity > 5 Wh/min
    assert rtu[Role.DUCT_STATIC].max() == pytest.approx(1.0, abs=0.01)  # 249 Pa -> inH2O


# --------------------------------------------------------------------------- the rbc-g36-ahu spec

_RBC = "Public_ScientificData_AHUFaults"
_ZONES = ("East", "South", "West", "North", "Core")
_RBC_OCC = (
    "Indicator if the System Operates in Occupied Mode (0: unoccupied mode, 1: occupied mode)"
)


def _rbc_csv(sat: list, south_temp: float) -> bytes:
    """A 114-column-shaped simulation export (a few of its columns): seconds, Kelvin, fractions."""
    n = len(sat)
    cols = {
        "time (s)": [60 * i for i in range(n)],
        "AHU Supply Air Temperature (°K)": sat,
        "AHU Supply Air Fan Status (0: off, 1: on)": [1] * n,
        _RBC_OCC: [1] * n,
    }
    for z in _ZONES:
        cols[f"{z} Zone Room Temperature (°K)"] = [south_temp if z == "South" else 296.15] * n
    return pd.DataFrame(cols).to_csv(index=False).encode("utf-8")


def test_rbc_g36_ahu_spec_ingests_a_synthetic_archive(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMBER_DATA_DIR", str(tmp_path / "cache"))
    members = {
        f"{_RBC}/08_G36-HIL/BaselineSystem.csv": _rbc_csv([286.15, -123456.0, 286.15], 296.15),
        f"{_RBC}/04_G36-1wk/CoolingSeason/TAirSou_p2.csv": _rbc_csv([286.15] * 3, 298.15),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, body in members.items():
            z.writestr(zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0)), body)
    body = buf.getvalue()
    d = copy.deepcopy(ds.get("rbc-g36-ahu").as_dict())
    runs = {r["member"]: r for r in d["ingest"]["runs"]}
    picked = [runs[m] for m in members]
    assert picked[1]["label"] and picked[1]["target"] == ["AHU_VAV_South"]
    d["id"] = "test-rbc"
    d["ingest"]["facility"] = "ds-test-rbc"
    d["ingest"]["runs"] = picked
    d["files"] = [
        dict(
            _file("rbc.zip", body), name=d["files"][0]["name"], archive="zip", members=list(members)
        )
    ]
    d["subsets"] = {
        k: {"files": "all", "runs": "all", "store_bytes_estimate": 1000}
        for k in ("default", "full")
    }
    keep = {"hil-sentinel-values", "bundled-1312-rp-folder"}
    d["data_issues"] = [i for i in d["data_issues"] if i["id"] in keep or not i.get("exclude")]
    for r in d["ingest"]["runs"]:
        r.pop("exclude", None)
    assert validate_catalog({"schema": 1, "datasets": [d]}) == []
    entry = DatasetEntry.from_dict(d)
    opener = _Opener({"https://example.org/rbc.zip": body})
    # held research-only (the bundled 1312-RP folder) although the record's licence is CC BY
    assert entry.research_only and entry.licence == "CC-BY-4.0" and entry.commercial_ok
    with pytest.raises(PermissionError, match="held research-only by CAMBER.*01_RBC-ASHRAE1312"):
        _ops.fetch_dataset(entry, opener=opener)
    _ops.fetch_dataset(entry, opener=opener, accept_noncommercial=True)
    st = ParquetStore(str(tmp_path / "store"))
    _ingest.ingest_dataset(entry, st)
    meta = st.facilities_meta()["ds-test-rbc"]["dataset"]
    assert meta["redistribution"] == "prohibited" and meta["access"] == "research_only"
    assert "01_RBC-ASHRAE1312" in meta["access_reason"]
    assert any("1312-RP" in n for n in meta["quirks"])
    fault = picked[1]["id"]
    assert meta["labels"][f"AHU_VAV_South__{fault}"] == picked[1]["label"]
    assert f"AHU_VAV_North__{fault}" in meta["context"]  # a neighbour of the biased zone
    base = picked[0]["id"]
    assert meta["labels"][f"AHU__{base}"] == ""  # a baseline scores every one of its units
    ahu = st.read_role_frame(facility_id="ds-test-rbc", equip=f"AHU__{base}")
    assert ahu.index[0] == pd.Timestamp("2025-01-01")  # seconds from the stated origin
    assert ahu[Role.SUPPLY_AIR_TEMP].iloc[0] == pytest.approx(55.4)  # 286.15 K; the sentinel masked
    south = st.read_role_frame(facility_id="ds-test-rbc", equip=f"AHU_VAV_South__{fault}")
    assert south[Role.SPACE_TEMP].iloc[0] == pytest.approx(77.0)  # 298.15 K
    assert south[Role.OCCUPANCY].iloc[0] == 1.0  # the AHU's occupied mode, copied to the zone
