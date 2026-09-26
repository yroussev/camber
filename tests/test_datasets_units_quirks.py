"""Unit conversion, declared quirks, label scoring, cache paths and the refresh script (offline).

Plus the one network smoke test (``-m network``; deselected by default), which HEADs every catalog
URL and checks size + ETag against the pinned values.
"""

import importlib.util
import io
import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.datasets import _paths  # noqa: E402
from camber.datasets._labels import records_from_findings, score_records  # noqa: E402
from camber.datasets._quirks import (  # noqa: E402
    applies_to,
    apply_quirks,
    quirk_columns,
    validate_quirk,
)
from camber.datasets._units import (  # noqa: E402
    canonical_unit,
    convert_frame,
    convert_series,
    plausibility_warnings,
)
from camber.model.roles import Role  # noqa: E402
from camber.rules.base import Finding  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------------- units


@pytest.mark.parametrize(
    "unit, value, delta, expect",
    [
        ("degC", 0.0, False, 32.0),
        ("°C", 100.0, False, 212.0),
        ("degC", 10.0, True, 18.0),  # a temperature difference: no offset
        ("K", 273.15, False, 32.0),
        ("K", 5.0, True, 9.0),
        ("L/s", 1.0, False, 2.1188799727597),
        ("m3/h", 1.0, False, 0.58857777021102),
        ("m3/s", 1.0, False, 2118.8799727597),
        ("Pa", 249.08891, False, 1.0),
        ("kPa", 0.24908891, False, 1.0),
        ("W", 1500.0, False, 1.5),
        ("degF", 70.0, False, 70.0),
        ("percent", 50.0, False, 50.0),
        ("", 3.0, False, 3.0),
    ],
)
def test_convert_series(unit, value, delta, expect):
    out = convert_series(pd.Series([value]), unit, delta=delta)
    assert out.iloc[0] == pytest.approx(expect)


def test_unknown_unit_and_frame_conversion():
    with pytest.raises(ValueError, match="unsupported"):
        canonical_unit("furlongs")
    f = pd.DataFrame({Role.OAT: [20.0], Role.SUPERHEAT_TEMP: [5.0], Role.AIRFLOW: [100.0]})
    out = convert_frame(f, {"oat": "degC", "superheat_temp": "K", "airflow": "L/s"})
    assert out[Role.OAT].iloc[0] == pytest.approx(68.0)
    assert out[Role.SUPERHEAT_TEMP].iloc[0] == pytest.approx(9.0)  # delta role
    assert out[Role.AIRFLOW].iloc[0] == pytest.approx(211.888, rel=1e-4)
    assert convert_frame(f, None) is f and convert_frame(pd.DataFrame(), {"oat": "degC"}).empty


def test_plausibility_warnings_flag_unit_mistakes():
    ok = pd.DataFrame({Role.OAT: [70.0], Role.DUCT_STATIC: [1.5], Role.COOL_VALVE: [500.0]})
    assert plausibility_warnings(ok) == []
    bad = pd.DataFrame(
        {Role.OAT: [300.0], Role.DUCT_STATIC: [400.0], Role.SUBCOOLING_TEMP: [100.0], "x": [1.0]}
    )
    w = plausibility_warnings(bad, label="run1")
    assert len(w) == 3 and all(x.startswith("run1: ") for x in w)
    assert plausibility_warnings(pd.DataFrame({Role.OAT: [np.nan]})) == []
    assert plausibility_warnings(None) == []


# --------------------------------------------------------------------------- quirks


def _raw():
    idx = pd.date_range("2020-04-08", periods=4, freq="1D")
    return pd.DataFrame(
        {"A": [1.0, 2.0, 3.0, 4.0], "B": [10.0, 20.0, 30.0, 40.0], "C": [0.0, 5.0, -1.0, 7.0]},
        index=idx,
    )


def test_fix_ops_change_the_data_and_annotations_do_not():
    quirks = [
        {"op": "swap", "action": "fix", "columns": ["A", "B"], "note": "swapped"},
        {"op": "rename", "action": "fix", "from": "C", "to": "D", "note": "renamed"},
        {"op": "mask", "action": "fix", "columns": ["A"], "before": "2020-04-10", "note": "m"},
        {"op": "mask", "action": "fix", "columns": ["D", "missing"], "lt": 0, "note": "neg"},
        {
            "op": "scale",
            "action": "fix",
            "columns": ["B", "zz"],
            "factor": 2,
            "offset": 1,
            "note": "s",
        },
        {"op": "convert", "action": "fix", "columns": ["D", "zz"], "from": "degC", "note": "c"},
        {"op": "drop", "action": "fix", "columns": ["nope"], "note": "d"},
        {"op": "swap", "action": "annotate", "columns": ["A", "B"], "note": "kept for exercise"},
        {"op": "annotate", "action": "annotate", "note": "a copied point"},
        {"op": "drop", "action": "fix", "columns": ["A"], "runs": ["other*"], "note": "not me"},
    ]
    assert all(validate_quirk(q) == [] for q in quirks)
    out, notes = apply_quirks(_raw(), quirks, run="this_run")
    assert list(out["A"].isna()) == [True, True, False, False]  # masked before the date
    assert out["A"].iloc[2] == 30.0  # swapped first
    assert out["B"].tolist() == [3.0, 5.0, 7.0, 9.0]  # swapped then scaled
    assert out["D"].iloc[1] == pytest.approx(41.0) and np.isnan(out["D"].iloc[2])
    assert len(notes) == 9 and notes[-1] == "annotate: a copied point"
    assert quirk_columns(quirks) == {"A", "B", "C", "D", "missing", "zz", "nope"}
    assert applies_to(quirks[-1], "other_run") and not applies_to(quirks[-1], "this_run")
    assert applies_to(quirks[-1], None)
    assert list(_raw().columns) == ["A", "B", "C"]  # input untouched
    out2, _ = apply_quirks(
        _raw(),
        [
            {
                "op": "mask",
                "action": "fix",
                "columns": ["C"],
                "gt": 4,
                "eq": 5.0,
                "after": "2020-04-08",
                "note": "m",
            }
        ],
    )
    assert np.isnan(out2["C"].iloc[1]) and out2["C"].iloc[3] == 7.0


def test_swap_with_one_column_missing_is_an_error_and_both_missing_a_noop():
    q = [{"op": "swap", "action": "fix", "columns": ["A", "Z"], "note": "x"}]
    with pytest.raises(ValueError, match="missing"):
        apply_quirks(_raw(), q)
    q = [{"op": "swap", "action": "fix", "columns": ["Y", "Z"], "note": "x"}]
    assert apply_quirks(_raw(), q)[0].equals(_raw())


@pytest.mark.parametrize(
    "q, needle",
    [
        ("nope", "must be an object"),
        ({"op": "eval", "action": "fix", "note": "x"}, "unknown quirk op"),
        ({"op": "drop", "action": "maybe", "columns": ["A"], "note": "x"}, "action must be"),
        ({"op": "drop", "action": "fix", "columns": ["A"]}, "needs a 'note'"),
        ({"op": "annotate", "action": "fix", "note": "x"}, "cannot be a fix"),
        ({"op": "swap", "action": "fix", "columns": ["A"], "note": "x"}, "exactly two"),
        ({"op": "rename", "action": "fix", "from": "A", "note": "x"}, "'from' and 'to'"),
        ({"op": "scale", "action": "fix", "note": "x"}, "needs 'columns'"),
        ({"op": "mask", "action": "fix", "columns": ["A"], "note": "x"}, "needs a condition"),
        ({"op": "scale", "action": "fix", "columns": ["A"], "factor": "2", "note": "x"}, "number"),
        (
            {"op": "convert", "action": "fix", "columns": ["A"], "from": "psi", "note": "x"},
            "unsupported",
        ),
    ],
)
def test_validate_quirk_rejects(q, needle):
    assert any(needle in e for e in validate_quirk(q))


# --------------------------------------------------------------------------- labels


def test_records_and_scores_follow_the_benchmark_convention():
    labels = {"E__ff": "", "E__d1": "damper", "E__leak": "valve_leak"}
    findings = [
        Finding("outdoor_air_fraction", "E__d1", "fault"),
        {"rule": "outdoor_air_fraction", "equip": "E__ff", "severity": "ok"},
        {"rule": "simultaneous_heat_cool", "equip": "E__ff", "severity": "warn"},
        {"rule": "outdoor_air_fraction", "equip": "not_labelled", "severity": "fault"},
    ]
    recs = records_from_findings(findings, labels, suite=["outdoor_air_fraction"])
    assert [r["fired"] for r in recs] == [["outdoor_air_fraction"], [], []]
    s = score_records(recs, {"outdoor_air_fraction": "damper"})
    assert s["n"] == 3 and s["overall"]["tpr"] == 0.5 and s["overall"]["fpr"] == 0.0
    assert s["per_detector"]["outdoor_air_fraction"]["tp"] == 1
    assert len(s["overall"]["tpr_ci"]) == 2
    everything = records_from_findings(findings, labels)
    assert everything[1]["fired"] == ["simultaneous_heat_cool"]


# --------------------------------------------------------------------------- paths


def test_data_dir_resolution(monkeypatch, tmp_path):
    monkeypatch.delenv("CAMBER_DATA_DIR", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert _paths.data_dir() == str(tmp_path / "xdg" / "camber" / "datasets")
    monkeypatch.delenv("XDG_CACHE_HOME")
    assert _paths.data_dir().endswith(os.path.join(".cache", "camber", "datasets"))
    monkeypatch.setenv("CAMBER_DATA_DIR", str(tmp_path / "env"))
    assert _paths.data_dir() == str(tmp_path / "env")
    assert _paths.data_dir(tmp_path / "arg") == str(tmp_path / "arg")


def test_manifest_and_ledger_tolerate_garbage(tmp_path):
    root = str(tmp_path)
    assert _paths.read_manifest(root) == {} and _paths.read_acknowledgements(root) == []
    open(os.path.join(root, "manifest.json"), "w").write("[1, 2]")
    open(os.path.join(root, "acknowledgements.json"), "w").write("{broken")
    assert _paths.read_manifest(root) == {} and _paths.read_acknowledgements(root) == []
    rec = _paths.append_acknowledgement(root, {"dataset_id": "x"})
    assert rec["accepted_at"] and _paths.read_acknowledgements(root) == [rec]


# --------------------------------------------------------------------------- refresh script


def _refresh():
    path = os.path.join(_ROOT, "scripts", "datasets_refresh.py")
    spec = importlib.util.spec_from_file_location("datasets_refresh", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Resp:
    def __init__(self, body, headers):
        self._b = io.BytesIO(body)
        self.headers = headers
        self.status = 200

    def read(self, n=-1):
        return self._b.read(n)

    def geturl(self):
        return "https://example.org/x"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Opener:
    def __init__(self, body, etag='"e2"', fail=False):
        self.body, self.etag, self.fail = body, etag, fail

    def open(self, req, timeout=None):
        if self.fail:
            raise OSError("offline")
        hdr = {"Content-Length": str(len(self.body)), "ETag": self.etag}
        return _Resp(b"" if req.get_method() == "HEAD" else self.body, hdr)


def test_refresh_pins_reports_drift_and_writes(tmp_path):
    import hashlib

    mod = _refresh()
    body = b"hello catalog"
    data = {
        "schema": 1,
        "datasets": [
            {
                "id": "x",
                "verified_on": "2020-01-01",
                "files": [
                    {
                        "name": "a",
                        "url": "https://example.org/a",
                        "size": 99,
                        "etag": '"e1"',
                        "sha256": None,
                        "pinned": False,
                    },
                    {
                        "name": "b",
                        "url": "https://example.org/b",
                        "size": None,
                        "sha256": None,
                        "pinned": False,
                    },
                ],
            },
            {"id": "y", "files": []},
        ],
    }
    logs = []
    n = mod.refresh(data, {"x"}, pin=True, opener=_Opener(body), log=logs.append)
    f = data["datasets"][0]["files"][0]
    assert f["sha256"] == hashlib.sha256(body).hexdigest() and f["size"] == len(body)
    assert f["pinned"] and n >= 4 and data["datasets"][0]["verified_on"] != "2020-01-01"
    assert any("SIZE CHANGED" in x for x in logs) and any("ETag changed" in x for x in logs)
    local = tmp_path / "copy"
    local.write_bytes(body)
    got = mod.pin_file({"url": "https://example.org/a"}, local=str(local))
    assert got["size"] == len(body)
    logs.clear()
    mod.refresh(data, None, opener=_Opener(body, fail=True), log=logs.append)
    assert any("HEAD failed" in x for x in logs)
    # main(): dry run on the real catalog with the network stubbed out
    mod.head = lambda url, **k: {"size": None, "etag": None}
    assert mod.main(["lbnl-sdahu"]) == 0
    cat = tmp_path / "cat.json"
    cat.write_text(json.dumps({"schema": 1, "datasets": []}))
    assert mod.main(["--all", "--catalog", str(cat)]) == 1  # invalid catalog is refused
    with pytest.raises(SystemExit):
        mod.main([])


def test_refresh_main_writes_a_valid_catalog(tmp_path):
    from camber.datasets._catalog import load_catalog_data

    mod = _refresh()
    mod.head = lambda url, **k: {"size": None, "etag": None}
    cat = tmp_path / "cat.json"
    cat.write_text(json.dumps(load_catalog_data()))
    assert mod.main(["bdg2", "--catalog", str(cat), "--write"]) == 0
    assert json.loads(cat.read_text())["datasets"][-1]["id"] == "bdg2"


# --------------------------------------------------------------------------- network (opt-in)


@pytest.mark.network
def test_catalog_urls_are_live_and_unchanged():  # pragma: no cover - needs the network
    """``pytest -m network``: every catalog file still has its pinned size (and ETag if known)."""
    from camber import datasets

    mod = _refresh()
    for e in datasets.catalog():
        for f in e.files:
            h = mod.head(f["url"])
            assert h["size"] == f["size"], f"{e.id}/{f['name']}"
            if f.get("etag") and h["etag"]:
                assert h["etag"].strip("W/") == f["etag"].strip("W/"), f"{e.id}/{f['name']}"
