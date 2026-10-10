"""camber.interop.openfdd: open-fdd package / historian reader, ingest, crosswalk, findings JSON.

Every package here is synthetic, built in the test from open-fdd's documented ingest contract
(``openfdd_package_v1``: ``<building>/manifest.json``, a root ``column_map.json``, per-equipment
``history_wide.csv`` + ``columns.csv`` + sibling maps, ``weather/``) and historian Parquet layout.
No real open-fdd or collaborator data is used.
"""

import json
import os
import re
import sys
import zipfile

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.cli import main  # noqa: E402
from camber.interop.openfdd import (  # noqa: E402
    FINDINGS_SCHEMA,
    FINDINGS_SCHEMA_VERSION,
    crosswalk_table,
    findings_document,
    ingest_package,
    load_crosswalk,
    openfdd_meta,
    package_config,
    read_historian,
    read_package,
)
from camber.interop.openfdd._crosswalk import canonical_unit, resolve_unit  # noqa: E402
from camber.interop.openfdd._reader import to_site_clock  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.portfolio import Portfolio  # noqa: E402
from camber.rules.base import Finding, RuleSkip  # noqa: E402
from camber.store import FacilityRegistry, ParquetStore  # noqa: E402

TZ = "America/Chicago"
BLDG = "BLDG_X"


# --------------------------------------------------------------------------- package builder


def _stamps(start, periods, freq):
    idx = pd.date_range(start, periods=periods, freq=freq, tz="UTC")
    return [t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in idx], idx


def _write_equip(root, rel, frame, *, sidecar=None, columns=None, ts_col="timestamp_utc"):
    d = os.path.join(root, *rel.split("/"))
    os.makedirs(d, exist_ok=True)
    frame = frame.rename(columns={"__ts__": ts_col})
    frame.to_csv(os.path.join(d, "history_wide.csv"), index=False)
    if sidecar is not None:
        with open(os.path.join(d, sidecar[0]), "w") as fh:
            json.dump(sidecar[1], fh)
    if columns is not None:
        pd.DataFrame(columns).to_csv(os.path.join(d, "columns.csv"), index=False)


def make_package(base, *, si=False, days=14, meter_days=120, building=BLDG):
    """A synthetic openfdd_package_v1 building folder under ``base``; returns its path."""
    root = os.path.join(base, building)
    os.makedirs(root, exist_ok=True)
    rng = np.random.default_rng(7)
    n = days * 96
    ts, idx = _stamps("2025-07-01", n, "15min")
    hour = idx.tz_convert(TZ).hour.to_numpy()
    occ = ((hour >= 7) & (hour < 18)).astype(float)
    oat_f = 75 + 12 * np.sin((hour - 9) / 24 * 2 * np.pi) + rng.normal(0, 0.5, n)
    sat_f = 55 + rng.normal(0, 0.4, n)

    def t(f):  # degF -> package unit
        return (f - 32) * 5 / 9 if si else f

    with open(os.path.join(root, "manifest.json"), "w") as fh:
        json.dump(
            {
                "schema_version": "openfdd_package_v1",
                "building_id": building,
                "grid_minutes": 15,
                "timezone": "UTC",
            },
            fh,
        )
    root_map = {
        "version": 1,
        "equipment": {
            "AHU_1": {
                "equipType": "ahu",
                "points": {
                    "discharge-air-temp": "SAT",
                    "mixed-air-temp": "MAT",
                    "return-air-temp": "RAT",
                    "outside-air-temp": "OAT",
                    "fan-status": "SF_S",
                    "fan-cmd": "SF_CMD",
                    "cooling-valve": "CLG_V",
                    "outside-air-damper": "OAD",
                    "duct-static-pressure": "DSP",
                    "return-fan-cmd": "RF_CMD",
                    "occupied": "OCC",
                },
            },
            "VAV_1": {
                "equipType": "vav",
                "parentAhu": "AHU_1",
                "points": {
                    "zone-air-temp": "ZN_T",
                    "zone-airflow": "FLOW",
                    "damper": "DPR",
                    "reheat-valve": "RHV",
                    "discharge-air-temp": "DA_T",
                    "vav-discharge-air-temp": "DA_T2",
                },
            },
            "METER_1": {"equipType": "meter", "points": {"elec-power": "KW", "elec-energy": "KWH"}},
            "PLANT_1": {
                "equipType": "chwPlant",
                "points": {
                    "chilled-water-supply-temp": "CHWS",
                    "chilled-water-return-temp": "CHWR",
                    "chw-diff-pressure": "CHW_DP",
                    "chw-flow": "CHW_FLOW",
                    "pump-1-speed": "P1",
                },
            },
            "LOOSE_1": {"points": {"zone-air-temp": "ZT"}},  # no equipType stamp
        },
    }
    with open(os.path.join(root, "column_map.json"), "w") as fh:
        json.dump(root_map, fh)
    with open(os.path.join(root, "equipment_inventory.json"), "w") as fh:
        json.dump([{"equip_id": "HP_1", "type": "HEAT_PUMP"}], fh)

    ahu = pd.DataFrame(
        {
            "__ts__": ts,
            # a vendor reset-curve column ahead of the real SAT, labelled with a role name in
            # columns.csv: the package map names SAT, so this one must never claim the role
            "SAT_ALT": t(np.full(n, 99.0)),
            "oa_t": t(np.full(n, 99.0)),  # an exact SQL-role header for a role the map has
            "SAT": t(sat_f),
            "MAT": t(68 + rng.normal(0, 0.5, n)),
            "RAT": t(73 + rng.normal(0, 0.5, n)),
            "OAT": t(oat_f),
            "SF_S": occ,
            "SF_CMD": occ,  # a 0/1 command
            "CLG_V": np.clip(occ * (40 + rng.normal(0, 5, n)), 0, 100),
            "OAD": np.clip(occ * 0.3 + rng.normal(0, 0.01, n), 0, 1),  # a 0-1 fraction
            "DSP": (1.2 * 249.08891 if si else 1.2) * occ,
            "RF_CMD": occ * 50,
            "OCC": occ,
            "vendor_alarm": 0.0,
            "equipment_id": "equip:ahu-1",
        }
    )
    _write_equip(
        root,
        "AHU_1",
        ahu,
        columns=[
            {"col": "SAT", "point_role": "discharge-air-temp", "unit": ""},
            {"col": "CLG_V", "point_role": "cooling-valve", "unit": "%"},
            {"col": "vendor_alarm", "point_role": "ahu_point", "unit": ""},
            {"col": "SAT_ALT", "point_role": "discharge-air-temp", "unit": ""},
        ],
    )
    # an equipment with no map at all: columns.csv point_role (exact open-fdd names only) applies
    _write_equip(
        root,
        "LOOSE_2",
        pd.DataFrame({"__ts__": ts, "zt": t(70.0), "zt2": t(71.0), "sat": t(55.0)}),
        columns=[
            {"col": "zt", "point_role": "zone-air-temp"},
            {"col": "zt2", "point_role": "zone_air_temp"},
        ],
    )
    vav = pd.DataFrame(
        {
            "__ts__": ts,
            "ZN_T": t(72 + rng.normal(0, 0.6, n)),
            "FLOW": (300 / 2.1188799727597 if si else 300.0) * (0.5 + occ / 2),
            "DPR": 30 + 20 * occ,
            "RHV": 0.0,
            "DA_T": t(58 + rng.normal(0, 0.4, n)),
            "DA_T2": t(58 + rng.normal(0, 0.4, n)),
        }
    )
    _write_equip(root, "VAV/VAV_1", vav)
    hp = pd.DataFrame(
        {
            "__ts__": ts,
            "da_t": t(95 - 30 * occ + rng.normal(0, 0.5, n)),
            "zn_t": t(71 + rng.normal(0, 0.5, n)),
            "fan_s": occ,
        }
    )
    _write_equip(
        root,
        "HP_1",
        hp,
        sidecar=(
            "history_wide.json",
            {
                "equipType": "heatPump",
                "equip": "HP_1",
                "points": {
                    "discharge-air-temp": "da_t",
                    "zone-air-temp": "zn_t",
                    "fan-status": "fan_s",
                },
            },
        ),
    )
    plant = pd.DataFrame(
        {
            "__ts__": ts,
            "CHWS": t(44 + rng.normal(0, 0.3, n)),
            "CHWR": t(54 + rng.normal(0, 0.3, n)),
            "CHW_DP": (10 / 0.14503773773 if si else 10.0) + rng.normal(0, 0.1, n),
            "CHW_FLOW": (400 / 15.850323141489 if si else 400.0) + rng.normal(0, 1, n),
            "P1": 60.0,
        }
    )
    _write_equip(root, "PLANT_1", plant)
    loose = pd.DataFrame({"__ts__": ts, "ZT": t(70 + rng.normal(0, 0.5, n))})
    _write_equip(root, "LOOSE_1", loose)
    # meter + weather: hourly, long enough for a daily M&V fit
    mts, midx = _stamps("2025-04-01", meter_days * 24, "h")
    mh = midx.tz_convert(TZ)
    day_oat = 70 + 15 * np.sin((mh.dayofyear.to_numpy() - 120) / 365 * 2 * np.pi)
    w_oat = day_oat + 8 * np.sin((mh.hour.to_numpy() - 9) / 24 * 2 * np.pi)
    kw = 40 + 2.5 * np.clip(w_oat - 65, 0, None) + rng.normal(0, 2, len(mts))
    _write_equip(root, "METER_1", pd.DataFrame({"__ts__": mts, "KW": kw, "KWH": np.cumsum(kw)}))
    _write_equip(
        root,
        "weather",
        pd.DataFrame(
            {
                "__ts__": mts,
                "web-outside-air-temp": t(w_oat),
                "web-outside-air-humidity": 55.0,
                "web-outside-air-dewpoint": t(55.0),
                "wind_speed_mph": 5.0,
            }
        ),
        sidecar=(
            "history_wide.json",
            {
                "equipType": "weather",
                "equip": "weather",
                "points": {"web-outside-air-temp": "web-outside-air-temp"},
            },
        ),
    )
    # open-fdd outputs that sit in packages but are not the ingest contract
    pd.DataFrame({"fault": [1]}).to_csv(os.path.join(root, "AHU_1", "fdd_faults.csv"))
    with open(os.path.join(root, "session_config.json"), "w") as fh:
        json.dump({}, fh)
    return root


@pytest.fixture(scope="module")
def pkg_dir(tmp_path_factory):
    return make_package(str(tmp_path_factory.mktemp("pkg")))


@pytest.fixture(scope="module")
def pkg(pkg_dir):
    return read_package(pkg_dir, timezone=TZ, unit_system="ip")


def _col(pkg, equip, column):
    return next(c for c in pkg.equipment[equip].columns if c.column == column)


# --------------------------------------------------------------------------- crosswalk


def test_crosswalk_is_versioned_pinned_and_valid():
    cw = load_crosswalk()
    assert cw.version == 1
    assert re.fullmatch(r"[0-9a-f]{40}", cw.docs_commit)
    pairs = [(r.haystack, r.sql_role) for r in cw.rows]
    assert len(pairs) == len(set(pairs))
    for r in cw.rows:
        if r.camber_role is None:
            assert r.reason, f"{r.haystack}: a non-mapping needs its reason"
        else:
            assert isinstance(r.role, Role)
    # both spellings resolve to the same row, case/punctuation-insensitively
    assert cw.lookup("discharge-air-temp") is cw.lookup("sat")
    assert cw.lookup("Discharge-Air-Temp ").camber_role == "supply_air_temp"
    assert cw.lookup("discharge_air_temp") is None  # a near-miss is not an open-fdd name
    assert cw.lookup("no-such-point") is None
    assert cw.lookup("elec_power").camber_role == "power"  # the second SQL spelling
    table = crosswalk_table()
    assert len(table) == len(cw.rows) and {"haystack", "camber_role"} <= set(table[0])


def test_crosswalk_equipment_types():
    cw = load_crosswalk()
    assert cw.equip_class("ahu") == "AHU"
    assert cw.equip_class("heatPump") == "HEAT_PUMP"
    assert cw.equip_class("HEAT_PUMP") == "HEAT_PUMP"  # an equipment_type CAMBER recognises
    assert cw.equip_class("chwPlant") == "CHW_PLANT"
    assert cw.equip_class("mystery") is None
    assert cw.equip_class(None) is None
    assert cw.openfdd_type("HEATPUMP") == "heatPump"


def test_units():
    assert canonical_unit("in/wc") == "inH2O"
    assert canonical_unit("°C") == "degC"
    assert canonical_unit("units") == ""
    assert canonical_unit("furlongs") is None
    assert resolve_unit("temp", "", "si") == ("degC", "")
    assert resolve_unit("temp", "degF", "si") == ("degF", "")  # a declared unit wins
    assert resolve_unit("water_flow", "", "si") == ("L/s", "")
    assert resolve_unit("temp", "%", "ip")[0] is None
    assert resolve_unit("binary", "", "si") == ("", "")
    assert resolve_unit("percent", "", "si") == ("", "")
    assert resolve_unit("power", "W", "ip") == ("W", "")


# --------------------------------------------------------------------------- reader


def test_timezone_and_units_are_required(pkg_dir):
    with pytest.raises(ValueError, match="timezone is required"):
        read_package(pkg_dir, timezone=None, unit_system="ip")
    with pytest.raises(ValueError, match="unknown timezone"):
        read_package(pkg_dir, timezone="Mars/Olympus", unit_system="ip")
    with pytest.raises(ValueError, match="unit_system is required"):
        read_package(pkg_dir, timezone=TZ, unit_system="")
    with pytest.raises(ValueError, match="unit_system is required"):
        read_package(pkg_dir, timezone=TZ, unit_system="metric")


def test_equipment_and_classes(pkg):
    classes = {e: x.equip_class for e, x in pkg.equipment.items()}
    assert classes == {
        "AHU_1": "AHU",
        "VAV_1": "VAV",
        "HP_1": "HEAT_PUMP",
        "METER_1": "METER",
        "PLANT_1": "CHW_PLANT",
        "LOOSE_1": "UNCLASSIFIED",  # no stamp: never inferred from the id
        "LOOSE_2": "UNCLASSIFIED",
        "weather": "WEATHER",
    }
    assert pkg.building_id == BLDG
    assert pkg.equipment["VAV_1"].parent == "AHU_1"
    assert pkg.equipment["VAV_1"].path == "VAV/VAV_1"
    assert pkg.equipment["HP_1"].equip_type == "heatPump"  # from the sibling sidecar
    assert any("fdd_faults.csv" in i for i in pkg.ignored)
    assert any("session_config.json" in i for i in pkg.ignored)
    assert any("manifest timezone 'UTC'" in n for n in pkg.notes)
    assert "AHU_1/history_wide.csv" in pkg.files and len(pkg.files["manifest.json"]) == 64


def test_column_mapping_and_coverage(pkg):
    ahu = pkg.equipment["AHU_1"]
    roles = {c.column: c.camber_role for c in ahu.mapped}
    assert roles["SAT"] == "supply_air_temp" and roles["OAD"] == "oa_damper"
    assert roles["SF_CMD"] == "supply_fan_speed" and roles["OCC"] == "occupancy"
    assert _col(pkg, "AHU_1", "RF_CMD").status == "no_camber_role"
    assert _col(pkg, "AHU_1", "vendor_alarm").status == "no_role"
    assert "not in the package map" in _col(pkg, "AHU_1", "vendor_alarm").reason
    assert "ahu_point" in _col(pkg, "AHU_1", "vendor_alarm").reason
    assert _col(pkg, "AHU_1", "equipment_id").status == "metadata"
    assert _col(pkg, "PLANT_1", "P1").status == "unknown_name"
    assert _col(pkg, "METER_1", "KWH").status == "no_camber_role"
    assert _col(pkg, "VAV_1", "DA_T2").status == "duplicate_role"
    w = {c.column: c for c in pkg.equipment["weather"].columns}
    assert w["web-outside-air-temp"].source == "map"
    assert w["web-outside-air-temp"].camber_role == "oat"
    assert w["web-outside-air-dewpoint"].status == "no_camber_role"
    assert w["wind_speed_mph"].status == "no_role"
    cov = pkg.coverage()
    assert cov["mapped"] + cov["unmapped"] == cov["columns"]
    assert cov["by_status"]["metadata"] == 1 and cov["by_status"]["duplicate_role"] == 2
    assert cov["by_equipment"]["AHU_1"]["mapped"] == 10
    names = pkg.unmapped_names()
    assert names["return-fan-cmd"] == 1 and "pump-1-speed" in names


def test_the_package_map_is_authoritative(pkg):
    # regression: a columns.csv label earlier in the header must not take a mapped role
    assert _col(pkg, "AHU_1", "SAT").camber_role == "supply_air_temp"
    alt = _col(pkg, "AHU_1", "SAT_ALT")
    assert alt.status == "no_role" and "discharge-air-temp" in alt.reason
    assert pkg.equipment["AHU_1"].frame[Role.SUPPLY_AIR_TEMP].max() < 60
    # an exact open-fdd name as a header fills only a role the map left open
    oa = _col(pkg, "AHU_1", "oa_t")
    assert oa.status == "duplicate_role" and "'OAT'" in oa.reason
    hum = {c.column: c for c in pkg.equipment["weather"].columns}["web-outside-air-humidity"]
    assert hum.source == "identity" and hum.camber_role == "outdoor_rh"
    # no map: columns.csv point_role, then an exact role-name header; a near-miss stays unmapped
    loose = {c.column: c for c in pkg.equipment["LOOSE_2"].columns}
    assert loose["zt"].source == "columns.csv" and loose["zt"].camber_role == "space_temp"
    assert loose["zt2"].status == "no_role" and "zone_air_temp" in loose["zt2"].reason
    assert loose["sat"].source == "identity" and loose["sat"].camber_role == "supply_air_temp"


def test_equipment_type_limited_rows(tmp_path):
    root = make_package(str(tmp_path))
    blk = json.load(open(os.path.join(root, "column_map.json")))
    blk["equipment"]["AHU_1"]["points"]["vav-inlet-air-temp"] = "MAT"
    del blk["equipment"]["AHU_1"]["points"]["mixed-air-temp"]
    json.dump(blk, open(os.path.join(root, "column_map.json"), "w"))
    p = read_package(root, timezone=TZ, unit_system="ip")
    c = _col(p, "AHU_1", "MAT")
    assert c.status == "not_applicable" and "vav only" in c.reason


def test_site_clock_and_frames(pkg):
    f = pkg.equipment["AHU_1"].frame
    # 2025-07-01T00:00Z is 2025-06-30 19:00 CDT, naive
    assert f.index[0] == pd.Timestamp("2025-06-30 19:00")
    assert f.index.tz is None
    assert f[Role.OA_DAMPER].max() > 1.5  # the 0-1 fraction scaled to percent
    assert f[Role.COOL_VALVE].max() <= 100
    assert set(pd.unique(f[Role.SUPPLY_FAN_SPEED])) <= {0.0, 100.0}
    assert any("0/1 command" in w for w in pkg.warnings)
    assert any("read as a 0-1 fraction" in w for w in pkg.warnings)


def test_to_site_clock_variants():
    s = pd.Series(
        ["2025-01-15T18:00:00Z", "2025-01-15 18:00:00+00:00", "2025-01-15T18:00:00", "bad"]
    )
    out = to_site_clock(s, TZ, utc_column=True)
    assert list(out[:3]) == [pd.Timestamp("2025-01-15 12:00")] * 3  # naive *_utc read as UTC
    assert pd.isna(out[3])
    local = to_site_clock(
        pd.Series(["2025-01-15 12:00:00", "2025-01-15T18:00:00Z"]), TZ, utc_column=False
    )
    assert list(local) == [pd.Timestamp("2025-01-15 12:00")] * 2
    # the skipped spring-forward hour has no local reading
    gap = to_site_clock(pd.Series(["2025-03-09 02:30:00"]), TZ, utc_column=False)
    assert pd.isna(gap[0])


def test_resample(pkg_dir):
    p = read_package(pkg_dir, timezone=TZ, unit_system="ip", resample="1h")
    f = p.equipment["AHU_1"].frame
    assert (f.index[1] - f.index[0]) == pd.Timedelta("1h")


def test_si_package_converts(tmp_path):
    root = make_package(str(tmp_path), si=True)
    p = read_package(root, timezone=TZ, unit_system="si")
    ip = read_package(make_package(str(tmp_path / "ip")), timezone=TZ, unit_system="ip")
    for eq, role in (
        ("AHU_1", Role.SUPPLY_AIR_TEMP),
        ("AHU_1", Role.DUCT_STATIC),
        ("VAV_1", Role.AIRFLOW),
        ("PLANT_1", Role.CHW_DIFF_PRESS),
        ("PLANT_1", Role.CHW_FLOW),
        ("weather", Role.OAT),
    ):
        a = p.equipment[eq].frame[role].mean()
        b = ip.equipment[eq].frame[role].mean()
        assert a == pytest.approx(b, rel=1e-3), (eq, role)
    assert p.equipment["PLANT_1"].frame[Role.CHW_DIFF_PRESS].mean() == pytest.approx(10, abs=0.1)


def test_declared_unit_overrides_and_rejects(tmp_path):
    root = make_package(str(tmp_path))
    cols = os.path.join(root, "AHU_1", "columns.csv")
    pd.DataFrame(
        [
            {"column": "SAT", "point_role": "discharge-air-temp", "units": "degF"},
            {"column": "MAT", "point_role": "mixed-air-temp", "units": "furlongs"},
            {"column": "RAT", "point_role": "return-air-temp", "units": "%"},
        ]
    ).to_csv(cols, index=False)
    p = read_package(root, timezone=TZ, unit_system="si")  # declared degF wins over si
    assert _col(p, "AHU_1", "SAT").unit_applied == "degF"
    assert p.equipment["AHU_1"].frame[Role.SUPPLY_AIR_TEMP].mean() == pytest.approx(55, abs=0.5)
    assert _col(p, "AHU_1", "MAT").status == "unit_unsupported"
    assert "does not fit" in _col(p, "AHU_1", "RAT").reason


def test_zip_and_layout_errors(tmp_path, pkg_dir):
    z = tmp_path / "pkg.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for dirpath, _d, files in os.walk(pkg_dir):
            for f in files:
                full = os.path.join(dirpath, f)
                zf.write(full, os.path.relpath(full, os.path.dirname(pkg_dir)))
    p = read_package(str(z), timezone=TZ, unit_system="ip")
    assert p.package_sha256 and len(p.package_sha256) == 64
    assert set(p.equipment) == set(read_package(pkg_dir, timezone=TZ, unit_system="ip").equipment)
    with pytest.raises(ValueError, match="not an open-fdd package"):
        read_package(str(tmp_path / "empty"), timezone=TZ, unit_system="ip") if os.makedirs(
            tmp_path / "empty"
        ) is None else None
    bad = tmp_path / "x.txt"
    bad.write_text("x")
    with pytest.raises(ValueError, match="not a package folder"):
        read_package(str(bad), timezone=TZ, unit_system="ip")


def test_multi_building_needs_a_choice(tmp_path):
    make_package(str(tmp_path), building="B_ONE", days=2, meter_days=2)
    make_package(str(tmp_path), building="B_TWO", days=2, meter_days=2)
    with pytest.raises(ValueError, match="several buildings"):
        read_package(str(tmp_path), timezone=TZ, unit_system="ip")
    p = read_package(str(tmp_path), timezone=TZ, unit_system="ip", building="B_TWO")
    assert p.building_id == "B_TWO"
    with pytest.raises(ValueError, match="no building 'B_3'"):
        read_package(str(tmp_path), timezone=TZ, unit_system="ip", building="B_3")


def test_missing_timestamp_and_schema_warning(tmp_path):
    root = make_package(str(tmp_path), days=2, meter_days=2)
    m = json.load(open(os.path.join(root, "manifest.json")))
    m["schema_version"] = "openfdd_package_v9"
    json.dump(m, open(os.path.join(root, "manifest.json"), "w"))
    p = read_package(root, timezone=TZ, unit_system="ip")
    assert any("schema_version" in w for w in p.warnings)
    pd.DataFrame({"when": [1], "x": [2]}).to_csv(
        os.path.join(root, "LOOSE_1", "history_wide.csv"), index=False
    )
    with pytest.raises(ValueError, match="no timestamp_utc"):
        read_package(root, timezone=TZ, unit_system="ip")


# --------------------------------------------------------------------------- historian


def _historian(root, building=BLDG):
    rng = np.random.default_rng(1)
    idx = pd.date_range("2025-08-01", periods=96 * 3, freq="15min", tz="UTC")
    tbl = pa.table(
        {
            "timestamp_utc": pa.array(idx.to_pydatetime(), pa.timestamp("us", tz="UTC")),
            "sat": 55 + rng.normal(0, 0.3, len(idx)),
            "zone_t": 72 + rng.normal(0, 0.3, len(idx)),
            "oa_damper_pct": np.full(len(idx), 0.2),
            "mystery_role": np.ones(len(idx)),
        }
    )
    d = os.path.join(
        root, "history", f"building_id={building}", "equipment_id=AHU_1", "year=2025", "month=08"
    )
    os.makedirs(d)
    pq.write_table(tbl, os.path.join(d, "part-20250801T000000Z-a.parquet"))
    pq.write_table(tbl.slice(0, 5), os.path.join(d, ".part-old.parquet.tombstone.parquet"))
    w = os.path.join(root, "weather", f"building_id={building}", "year=2025", "month=08")
    os.makedirs(w)
    pq.write_table(
        pa.table({"timestamp_utc": tbl["timestamp_utc"], "web_oa_t": np.full(len(idx), 80.0)}),
        os.path.join(w, "part-1.parquet"),
    )
    return root


def test_read_historian(tmp_path):
    root = _historian(str(tmp_path))
    p = read_historian(
        root, building=BLDG, timezone=TZ, unit_system="ip", equip_types={"AHU_1": "ahu"}
    )
    ahu = p.equipment["AHU_1"]
    assert p.source_kind == "historian" and ahu.equip_class == "AHU"
    assert ahu.rows_read == 96 * 3  # the hidden tombstone part is not read
    assert {c.camber_role for c in ahu.mapped} == {"supply_air_temp", "space_temp", "oa_damper"}
    assert ahu.frame[Role.OA_DAMPER].iloc[0] == pytest.approx(20.0)
    assert {c.source for c in ahu.columns if c.status == "mapped"} == {"historian"}
    assert next(c for c in ahu.columns if c.column == "mystery_role").status == "no_role"
    assert p.equipment["weather"].frame[Role.OAT].iloc[0] == 80.0
    assert ahu.frame.index[0] == pd.Timestamp("2025-07-31 19:00")
    bare = read_historian(root, building=BLDG, timezone=TZ, unit_system="ip")
    assert bare.equipment["AHU_1"].equip_class == "UNCLASSIFIED" and bare.notes
    with pytest.raises(ValueError, match="no open-fdd historian data"):
        read_historian(root, building="NOPE", timezone=TZ, unit_system="ip")


def test_ingest_historian(tmp_path):
    root = _historian(str(tmp_path / "h"))
    with pytest.raises(ValueError, match="needs building"):
        ingest_package(root, timezone=TZ, unit_system="ip", store=str(tmp_path / "s"))
    res = ingest_package(
        root,
        timezone=TZ,
        unit_system="ip",
        store=str(tmp_path / "s"),
        building=BLDG,
        equip_types={"AHU_1": "ahu"},
        resample=None,
    )
    meta = openfdd_meta(FacilityRegistry(str(tmp_path / "s")).get(res.facility_id))
    assert meta["source"] == "open-fdd historian Parquet" and res.rows > 0
    assert any(k.endswith(".parquet") for k in meta["files"])


# --------------------------------------------------------------------------- ingest


def test_ingest_into_store_is_idempotent(tmp_path, pkg_dir):
    store = str(tmp_path / "store")
    res = ingest_package(pkg_dir, timezone=TZ, unit_system="ip", store=store)
    assert not res.skipped and res.rows > 0 and res.state == "active"
    assert res.facility_id.startswith("openfdd-bldg-x-")
    st = ParquetStore(store)
    assert res.facility_id in st.facilities()
    eq = st.equipment(facility_id=res.facility_id)[res.facility_id]
    assert set(eq) >= {"AHU_1", "VAV_1", "weather"} and eq["VAV_1"] == "VAV"
    meta = openfdd_meta(FacilityRegistry(store).get(res.facility_id))
    assert meta["source"] == "open-fdd package (openfdd_package_v1)"
    assert meta["schema_version"] == "openfdd_package_v1"
    assert meta["timezone"] == TZ and meta["unit_system"] == "ip"
    assert meta["crosswalk_version"] == 1 and len(meta["crosswalk_docs_commit"]) == 40
    assert meta["files"]["manifest.json"] == res.content_hash or len(meta["files"]) > 10
    assert meta["coverage"]["unmapped"] == res.coverage["unmapped"]
    assert meta["equipment"]["VAV_1"]["parent"] == "AHU_1"
    assert meta["span"][0].startswith("2025-")
    again = ingest_package(pkg_dir, timezone=TZ, unit_system="ip", store=store)
    assert again.skipped and again.rows == res.rows
    forced = ingest_package(pkg_dir, timezone=TZ, unit_system="ip", store=store, force=True)
    assert not forced.skipped and forced.rows == res.rows
    n = len(st.read_long(facility_id=res.facility_id))
    assert n == res.rows  # replaced, not appended
    other_tz = ingest_package(pkg_dir, timezone="America/New_York", unit_system="ip", store=store)
    assert not other_tz.skipped  # a different clock is a different ingest
    st.drop_facility(res.facility_id, forget=True)  # tombstones the id
    with pytest.raises(ValueError):
        ingest_package(pkg_dir, timezone=TZ, unit_system="ip", store=store, force=True)
    assert res.facility_id not in ParquetStore(store).facilities()
    with pytest.raises(ValueError, match="exactly one"):
        ingest_package(pkg_dir, timezone=TZ, unit_system="ip")


def test_ingest_into_workspace_uses_the_lifecycle(tmp_path, pkg_dir):
    ws = Portfolio.init(tmp_path / "ws")
    res = ingest_package(
        pkg_dir, timezone=TZ, unit_system="ip", workspace=ws.root, reason="pilot onboarding"
    )
    assert res.state == "provisioning" and res.workspace == ws.root
    rec = [r for r in ws.audit_log(facility_id=res.facility_id)]
    actions = [r["action"] for r in rec]
    assert actions[:1] == ["facility.add"] and "interop.openfdd.ingest" in actions
    assert rec[-1]["reason"] == "pilot onboarding"
    assert rec[-1]["details"]["building_id"] == BLDG
    res2 = ingest_package(
        pkg_dir, timezone=TZ, unit_system="ip", workspace=ws.root, activate=True, force=True
    )
    assert res2.state == "active"
    # an unchanged package still activates a provisioning facility when asked
    ws2 = Portfolio.init(tmp_path / "ws2")
    first = ingest_package(pkg_dir, timezone=TZ, unit_system="ip", workspace=ws2.root)
    again = ingest_package(
        pkg_dir, timezone=TZ, unit_system="ip", workspace=ws2.root, activate=True
    )
    assert first.state == "provisioning" and again.skipped and again.state == "active"
    ws.transition(res.facility_id, "suspend", reason="paused")
    with pytest.raises(ValueError, match="suspended"):
        ingest_package(pkg_dir, timezone=TZ, unit_system="ip", workspace=ws.root, force=True)
    with pytest.raises(FileNotFoundError):
        ingest_package(pkg_dir, timezone=TZ, unit_system="ip", workspace=str(tmp_path / "nope"))


def test_package_config_and_findings_end_to_end(tmp_path, pkg_dir, pkg):
    store = str(tmp_path / "store")
    cfg_path = str(tmp_path / "run.json")
    res = ingest_package(pkg_dir, timezone=TZ, unit_system="ip", store=store, config_out=cfg_path)
    cfg = json.load(open(cfg_path))
    assert cfg["source"] == {
        "kind": "store",
        "store": os.path.abspath(store),
        "facility_id": res.facility_id,
        "timezone": TZ,
    }
    assert {"class": "AHU"} in cfg["equipment"] and {"class": "WEATHER"} not in cfg["equipment"]
    assert cfg["shared_oat"] == {"equip": "weather", "role": "oat"}
    assert "supply_air_reset" in cfg["rules"]
    assert cfg["mv"][0]["class"] == "METER" and cfg["mv"][0]["role"] == "power"
    out = str(tmp_path / "findings.json")
    assert main(["interop", "openfdd", "findings", cfg_path, "--out", out]) == 0
    doc = json.load(open(out))
    assert doc["schema"] == FINDINGS_SCHEMA and doc["schema_version"] == FINDINGS_SCHEMA_VERSION
    assert doc["engine"]["name"] == "camber"
    assert doc["facility"] == res.facility_id and doc["window"]["tz"] == TZ
    assert doc["window"]["start"].startswith("2025-")
    assert doc["sources"][0]["building_id"] == BLDG
    kinds = {r["kind"] for r in doc["findings"]}
    assert "mv" in kinds and "rule" in kinds
    for r in doc["findings"]:
        assert r["engine"] == doc["engine"] and r["facility"] == res.facility_id
        assert r["status"] in ("fault", "warn", "ok", "info", "declined", "not_evaluated")
        assert set(r["magnitude"]) == {"fault_hours", "fault_pct", "denominator_definition"}
    assert sum(doc["counts"].values()) == len(doc["findings"])
    # the ordinary run and mv commands on the ingested facility also work
    assert main(["run", cfg_path, "--out", str(tmp_path / "run")]) == 0
    assert main(["mv", "run", cfg_path, "--out", str(tmp_path / "mv")]) == 0
    mv = json.load(open(tmp_path / "mv" / "mv_findings.json"))
    assert mv and mv[0]["rule"] == "mv_baseline"
    again = package_config(pkg, store=store, facility_id=res.facility_id, rules=["x"])
    assert again["rules"] == ["x"]


def test_findings_document_statuses():
    fs = [
        Finding("r1", "AHU_1", "fault", metrics={"fault_pct": 12.5, "fault_hours": 30}),
        Finding("r2", "AHU_1", "info", metrics={"declined": True, "reason": "no data"}),
        Finding("mv_baseline", "M1", "ok", evidence={"kind": "x"}, caveats=["c"]),
        Finding("r3", "AHU_1", "odd"),
    ]
    sk = [RuleSkip("r4", "VAV_1", "VAV", missing=["airflow"])]
    doc = findings_document(fs, facility_id="f", skipped=sk)
    st = {r["rule_id"]: r for r in doc["findings"]}
    assert st["r1"]["status"] == "fault" and st["r1"]["magnitude"]["fault_pct"] == 12.5
    assert st["r1"]["magnitude"]["fault_hours"] == 30
    assert st["r2"]["status"] == "declined" and st["r2"]["declined_reason"] == "no data"
    assert st["mv_baseline"]["kind"] == "mv" and st["mv_baseline"]["evidence"] == [{"kind": "x"}]
    assert st["r3"]["status"] == "info"
    assert st["r4"]["status"] == "not_evaluated" and "airflow" in st["r4"]["declined_reason"]
    assert st["r1"]["cost"] is None and st["r1"]["native"]["severity"] == "fault"
    assert doc["counts"]["not_evaluated"] == 1
    mv_only = findings_document(facility_id="f", mv_findings=[fs[0]])
    assert mv_only["findings"][0]["kind"] == "mv"


def test_store_timezone_defaults_to_the_ingested_zone():
    from camber.config import _catalog_timezone

    assert _catalog_timezone({"openfdd": {"timezone": TZ}}) == TZ
    assert _catalog_timezone({"openfdd": {"timezone": "Mars/X"}}) is None


# ---------------------------------------------- 0.99.1 (#96): the read API reports the zone


def _drop_registry_timezone(store, fid):
    """Rewrite a registry entry as 0.99.0 left it: the zone only in the openfdd provenance."""
    path = os.path.join(store, "_facilities.json")
    with open(path) as fh:
        data = json.load(fh)
    data[fid].pop("timezone", None)
    with open(path, "w") as fh:
        json.dump(data, fh)


def _ui_axis_label(facilities: dict, points: dict, fid: str, *, utc: bool = False) -> str | None:
    """Run the trend viewer's own zone code (setZone / axisLabel) on a /facilities body and the
    facility's /points body in node and return its time-axis label; ``None`` when node is not
    installed. 0.103 (#122): the label comes from /points' ``time_axis``."""
    import shutil
    import subprocess

    from camber.api.ui import live_dashboard_html

    node = shutil.which("node")
    if not node:
        return None
    h = live_dashboard_html()
    assert "FTZ[f.facility_id]=f.timezone||null" in h  # how the page reads /facilities
    assert "AXIS=d.time_axis||AXIS" in h  # and the label /points gives
    a = h.index("function setZone(){")
    b = h.index("function offMs(", a)
    script = (
        "var FTZ={},TZ=null,fmtZ=null,offC={};"
        f"var facSel={{value:{json.dumps(fid)}}},utcBox={{checked:{json.dumps(utc)}}},"
        "utcLbl={hidden:true};"
        f"var d={json.dumps(facilities)},AXIS={json.dumps(points['time_axis'])};"
        "d.facilities.forEach(function(f){FTZ[f.facility_id]=f.timezone||null;});"
        + h[a:b]
        + "setZone();process.stdout.write(axisLabel());"
    )
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_ingested_facility_reports_its_zone_through_the_read_api(tmp_path, pkg_dir):
    from camber.api import ReadAPI
    from camber.api.server import dispatch

    store = str(tmp_path / "store")
    res = ingest_package(pkg_dir, timezone=TZ, unit_system="ip", store=store)
    fid = res.facility_id
    entry = FacilityRegistry(store).get(fid)
    assert entry["timezone"] == TZ == entry["openfdd"]["timezone"]  # the standard place, too
    # a facility with no zone anywhere, in the same store
    st = ParquetStore(store)
    idx = pd.date_range("2025-01-01", periods=3, freq="1h")
    st.write_role_frame(
        pd.DataFrame({Role.HEAT_VALVE: range(3)}, index=idx),
        facility_id="plain",
        equip="AHU_1",
        equip_class="AHU",
        name="plain",
    )
    code, body = dispatch(ReadAPI(ParquetStore(store)), "GET", "/facilities", {})
    assert code == 200
    rows = {f["facility_id"]: f for f in body["facilities"]}
    assert rows[fid]["timezone"] == TZ
    assert set(rows["plain"]) == {"facility_id", "name", "display_name", "state"}  # unchanged
    api = ReadAPI(ParquetStore(store))
    pts = {f: dispatch(api, "GET", "/points", {"facility_id": [f]})[1] for f in (fid, "plain")}
    label = _ui_axis_label(body, pts[fid], fid)
    if label is not None:
        assert label == f"local time ({TZ})"
        assert _ui_axis_label(body, pts[fid], fid, utc=True) == "time (UTC)"  # the box converts
        # no zone: the page never claims UTC, even with the (hidden) box ticked (0.103, #122)
        no_zone = "local time (no time zone recorded)"
        assert _ui_axis_label(body, pts["plain"], "plain") == no_zone
        assert _ui_axis_label(body, pts["plain"], "plain", utc=True) == no_zone
    # re-ingesting the same building in another zone moves the facility's zone with it
    ingest_package(pkg_dir, timezone="America/New_York", unit_system="ip", store=store)
    rows = {f["facility_id"]: f for f in ReadAPI(ParquetStore(store)).facilities()["facilities"]}
    assert rows[fid]["timezone"] == "America/New_York"


def test_a_099_0_ingest_reports_its_zone_without_reingest(tmp_path, pkg_dir):
    from camber.api import ReadAPI
    from camber.api.read import _facility_timezone

    store = str(tmp_path / "store")
    fid = ingest_package(pkg_dir, timezone=TZ, unit_system="ip", store=store).facility_id
    _drop_registry_timezone(store, fid)
    assert "timezone" not in FacilityRegistry(store).get(fid)
    rows = {f["facility_id"]: f for f in ReadAPI(ParquetStore(store)).facilities()["facilities"]}
    assert rows[fid]["timezone"] == TZ  # from the openfdd provenance
    # an unchanged re-ingest is skipped, but backfills the standard key
    again = ingest_package(pkg_dir, timezone=TZ, unit_system="ip", store=store)
    assert again.skipped and FacilityRegistry(store).get(fid)["timezone"] == TZ
    # lookup order: explicit zone, then the catalog dataset zone, then the openfdd provenance
    ofdd = {"openfdd": {"timezone": TZ}}
    assert _facility_timezone(ofdd) == TZ
    assert _facility_timezone({**ofdd, "timezone": "Europe/Oslo"}) == "Europe/Oslo"
    lbnl = {"dataset": {"dataset_id": "lbnl-b59"}}
    assert _facility_timezone({**ofdd, **lbnl}) == "America/Los_Angeles"
    nozone = {"dataset": {"dataset_id": "no-such-dataset"}}
    assert _facility_timezone({**ofdd, **nozone}) == TZ
    assert _facility_timezone({"openfdd": {"timezone": "Mars/X"}}) is None
    assert _facility_timezone({"openfdd": "not a block"}) is None


# --------------------------------------------------------------------------- CLI


def test_cli_crosswalk(capsys):
    assert main(["interop", "openfdd", "crosswalk"]) == 0
    out = capsys.readouterr().out
    assert "discharge-air-temp" in out and "supply_air_temp" in out and "deliberately" in out
    assert main(["interop", "openfdd", "crosswalk", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["crosswalk_version"] == 1 and doc["roles"]


def test_cli_requires_timezone_and_units(pkg_dir, tmp_path):
    with pytest.raises(SystemExit) as e:
        main(["interop", "openfdd", "ingest", pkg_dir, "--units", "ip", "--store", str(tmp_path)])
    assert e.value.code == 2
    with pytest.raises(SystemExit):
        main(["interop", "openfdd", "ingest", pkg_dir, "--timezone", TZ, "--store", str(tmp_path)])
    rc = main(
        [
            "interop",
            "openfdd",
            "ingest",
            pkg_dir,
            "--timezone",
            "Nowhere/X",
            "--units",
            "ip",
            "--store",
            str(tmp_path),
        ]
    )
    assert rc == 1


def test_cli_ingest_and_inspect(pkg_dir, tmp_path, capsys):
    ws = Portfolio.init(tmp_path / "ws")
    js = str(tmp_path / "res.json")
    rc = main(
        [
            "interop",
            "openfdd",
            "ingest",
            pkg_dir,
            "--timezone",
            TZ,
            "--units",
            "ip",
            "--workspace",
            ws.root,
            "--config-out",
            str(tmp_path / "c.json"),
            "--json",
            js,
            "--resample",
            "native",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "provisioning" in out and "unmapped" in out and "camber facility activate" in out
    res = json.load(open(js))
    assert res["coverage"]["mapped"] > 0 and res["unmapped"]
    rc = main(
        [
            "interop",
            "openfdd",
            "ingest",
            pkg_dir,
            "--timezone",
            TZ,
            "--units",
            "ip",
            "--workspace",
            ws.root,
            "--resample",
            "native",
        ]
    )
    assert rc == 0 and "skipped" in capsys.readouterr().out
    ij = str(tmp_path / "inspect.json")
    assert (
        main(
            [
                "interop",
                "openfdd",
                "inspect",
                pkg_dir,
                "--timezone",
                TZ,
                "--units",
                "ip",
                "--json",
                ij,
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "AHU_1 [AHU]" in out
    doc = json.load(open(ij))
    assert doc["equipment"]["AHU_1"]["columns"][0]["column"]
    assert (
        main(
            [
                "interop",
                "openfdd",
                "inspect",
                str(tmp_path / "nope"),
                "--timezone",
                TZ,
                "--units",
                "ip",
            ]
        )
        == 1
    )


def test_cli_historian_and_findings_stdout(tmp_path, capsys):
    root = _historian(str(tmp_path / "h"))
    types = tmp_path / "types.json"
    types.write_text(json.dumps({"AHU_1": "ahu"}))
    assert main(["interop", "openfdd", "inspect", root, "--timezone", TZ, "--units", "ip"]) == 1
    assert (
        main(
            [
                "interop",
                "openfdd",
                "inspect",
                root,
                "--timezone",
                TZ,
                "--units",
                "ip",
                "--building",
                BLDG,
                "--equip-types",
                str(types),
            ]
        )
        == 0
    )
    assert "AHU_1 [AHU]" in capsys.readouterr().out
    cfg = tmp_path / "c.json"
    assert (
        main(
            [
                "interop",
                "openfdd",
                "ingest",
                root,
                "--timezone",
                TZ,
                "--units",
                "ip",
                "--building",
                BLDG,
                "--equip-types",
                str(types),
                "--store",
                str(tmp_path / "s"),
                "--config-out",
                str(cfg),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main(["interop", "openfdd", "findings", str(cfg), "--out", "-"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["schema"] == FINDINGS_SCHEMA
    bad = tmp_path / "bad.json"
    bad.write_text("[1]")
    assert (
        main(
            [
                "interop",
                "openfdd",
                "inspect",
                root,
                "--timezone",
                TZ,
                "--units",
                "ip",
                "--building",
                BLDG,
                "--equip-types",
                str(bad),
            ]
        )
        == 1
    )
