"""The valladolid-uva catalog entry (#50): no network, no real data.

A tiny synthetic pair of files shaped like the publisher's (``m/d/Y H:MM`` local stamps labelled at
the end of each hour, a repeated 03:00 label on the DST fall-back day, daily weather repeated on
every hour) is ingested through the shipped entry's own ingest spec, mappings and config template.
The chaining example's day and month builders are checked on the same shape.
"""

import copy
import hashlib
import importlib.util
import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.config import run_config  # noqa: E402
from camber.datasets import _ingest, _paths  # noqa: E402
from camber.datasets._catalog import (  # noqa: E402
    DatasetEntry,
    load_catalog_data,
    package_text,
    validate_catalog,
)
from camber.model.roles import Role  # noqa: E402
from camber.store import ParquetStore  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _shipped() -> dict:
    data = load_catalog_data()
    return next(d for d in data["datasets"] if d["id"] == "valladolid-uva")


def _csv(level: float, start="2016-10-28 00:00", days=4) -> bytes:
    """Hour-ending local stamps over the 2016-10-30 fall-back (03:00 appears twice)."""
    idx = list(pd.date_range(start, periods=days * 24, freq="h"))
    dup = pd.Timestamp("2016-10-30 03:00")
    if dup in idx:
        idx.insert(idx.index(dup) + 1, dup)
    n = len(idx)
    day = pd.DatetimeIndex(idx).normalize()
    t2m = 10.0 + (day - day[0]).days.to_numpy(dtype=float)  # one value per calendar day
    energy = level + np.arange(n, dtype=float)
    df = pd.DataFrame(
        {
            "DATE": [f"{t.month}/{t.day}/{t.year} {t.hour}:{t.minute:02d}" for t in idx],
            "ENERGY": energy,
            "HDD18_3": 18.3 - t2m,
            "CDD0": t2m,
            "CDD10": 0.0,
            "PRECTOT": 0.1,
            "RH2M": 60.0,
            "T2M": t2m,
            "T2M_MIN": t2m - 5,
            "T2M_MAX": t2m + 5,
            "ALLSKY": 3.0,
            "HOLIDAY": 0,
        }
    )
    return df.to_csv(index=False).encode()


def _entry(files: dict) -> DatasetEntry:
    d = copy.deepcopy(_shipped())
    for f in d["files"]:
        body = files[f["name"]]
        f.update(
            url="https://example.org/" + f["name"],
            size=len(body),
            sha256=hashlib.sha256(body).hexdigest(),
        )
    assert validate_catalog({"schema": 1, "datasets": [d]}) == []
    return DatasetEntry.from_dict(d)


def _ingest_fixture(tmp_path, monkeypatch):
    files = {"db_building_A.csv": _csv(100.0), "db_building_B.csv": _csv(40.0)}
    entry = _entry(files)
    cache = tmp_path / "cache"
    monkeypatch.setenv("CAMBER_DATA_DIR", str(cache))
    ddir = _paths.downloads_dir(str(cache), entry.id)
    os.makedirs(ddir, exist_ok=True)
    for name, body in files.items():
        with open(os.path.join(ddir, name), "wb") as fh:
            fh.write(body)
    store = ParquetStore(str(tmp_path / "store"))
    return entry, store, _ingest.ingest_dataset(entry, store)


def test_shipped_entry_is_valid_and_its_issues_are_wired():
    d = _shipped()
    assert validate_catalog({"schema": 1, "datasets": [d]}) == []
    ids = {i["id"]: i for i in d["data_issues"]}
    assert ids["data-extend-to-2020"]["handling"] == "exclude"
    assert ids["building-b-net-of-onsite-generation"]["runs"] == ["building_b"]
    notes = {q["issue"] for q in d["ingest"]["quirks"]}
    assert notes == {
        "hour-ending-local-clock",
        "weather-is-daily",
        "building-b-net-of-onsite-generation",
    }
    assert d["licence"] == "CC-BY-4.0" and d["access"] == "open"
    for f in d["files"]:
        assert f["pinned"] and len(f["sha256"]) == 64


def test_meters_and_daily_weather_land_as_published(tmp_path, monkeypatch):
    _entry_, store, res = _ingest_fixture(tmp_path, monkeypatch)
    assert store.equipment()["ds-valladolid-uva"] == {
        "UVA_A": "ELECTRICITY_METER",
        "UVA_B": "ELECTRICITY_METER",
        "weather": "WEATHER",
    }
    a = store.read_role_frame(facility_id="ds-valladolid-uva", equip="UVA_A")
    # kWh per hour stored as the hour's mean kW, on the published (hour-ending, local) labels
    assert a[Role.POWER].loc["2016-10-28 00:00"] == pytest.approx(100.0)
    assert a[Role.POWER].loc["2016-10-28 05:00"] == pytest.approx(105.0)
    # the repeated fall-back 03:00 label keeps its first reading (the second is dropped)
    first = 100.0 + 2 * 24 + 3
    assert a[Role.POWER].loc["2016-10-30 03:00"] == pytest.approx(first)
    assert a[Role.POWER].loc["2016-10-30 04:00"] == pytest.approx(first + 2)
    w = store.read_role_frame(facility_id="ds-valladolid-uva", equip="weather")
    assert w[Role.OAT].loc["2016-10-28 00:00"] == pytest.approx(50.0)  # 10 C, daily, -> F
    assert w[Role.OAT].loc["2016-10-28"].nunique() == 1
    assert any("end of each hour" in n for n in res.notes)


def test_config_template_runs_a_daily_mv_baseline(tmp_path, monkeypatch):
    _entry_, _store, _res = _ingest_fixture(tmp_path, monkeypatch)
    cfg = json.loads(package_text("configs", "valladolid-uva.json"))
    cfg["source"].update(store=str(tmp_path / "store"))
    cfg["mv"][0]["period"] = ["2016-10-28", "2016-10-31"]
    cfg.pop("report")
    res = run_config(cfg, base_dir=str(tmp_path))
    mv = [f for f in res.findings if f.rule.startswith("mv_")]
    assert {f.equip for f in mv} == {"UVA_A", "UVA_B"}


def _chaining_module():
    path = os.path.join(ROOT, "examples", "valladolid", "chaining.py")
    spec = importlib.util.spec_from_file_location("valladolid_chaining", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_chaining_example_moves_hour_ending_readings_and_weights_months(tmp_path):
    mod = _chaining_module()
    p = tmp_path / "a.csv"
    p.write_bytes(_csv(100.0, start="2016-01-01 00:00", days=40))
    h = mod.hourly(str(p))
    assert h.index[0] == pd.Timestamp("2015-12-31 23:00")  # the 00:00 label ends 2015's last hour
    oat = pd.Series(50.0, index=pd.date_range("2015-12-31", "2016-02-15", freq="h"))
    day = mod.daily(h, oat)
    # a whole day is 24 readings moved to their start hour; the partial first day is dropped
    assert np.isnan(day.loc["2015-12-31", "energy"])
    first = day.loc["2016-01-01"]
    assert first["n"] == 24 and first["energy"] == pytest.approx(
        sum(100.0 + i for i in range(1, 25))
    )
    m = mod.monthly(day)
    assert list(m.index) == [pd.Timestamp("2016-01-01"), pd.Timestamp("2016-02-01")]
    assert m.loc["2016-01-01", "days"] == int(day.loc["2016-01", "workday"].sum())
