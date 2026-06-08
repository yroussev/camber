"""Tests for the Haystack hisRead adapter (parse + assemble, offline transport)."""

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.ingest.base import SourceAdapter  # noqa: E402
from camber.ingest.haystack import HaystackAdapter, parse_his_grid  # noqa: E402


def _grid(rows):
    return {"meta": {"ver": "3.0"}, "cols": [{"name": "ts"}, {"name": "val"}],
            "rows": rows}


def test_parse_his_grid_decodes_t_and_n_prefixes():
    grid = _grid([
        {"ts": "t:2024-01-01T00:00:00-08:00 Los_Angeles", "val": "n:23.5 °C"},
        {"ts": "t:2024-01-01T01:00:00-08:00 Los_Angeles", "val": "n:24.0 °C"},
    ])
    s = parse_his_grid(grid, name="sat")
    assert list(s) == [23.5, 24.0]
    assert s.name == "sat"
    assert isinstance(s.index, pd.DatetimeIndex)


def test_parse_handles_plain_iso_and_bare_number():
    grid = _grid([{"ts": "2024-01-01T00:00:00", "val": 19.0}])
    s = parse_his_grid(grid)
    assert s.iloc[0] == 19.0


def test_parse_inf_and_nan_tokens():
    grid = _grid([
        {"ts": "t:2024-01-01T00:00:00Z", "val": "n:INF"},
        {"ts": "t:2024-01-01T01:00:00Z", "val": "NaN"},
    ])
    s = parse_his_grid(grid)
    assert s.iloc[0] == float("inf")
    assert pd.isna(s.iloc[1])


def test_parse_sorts_and_skips_rowless():
    grid = _grid([
        {"ts": "t:2024-01-01T02:00:00Z", "val": "n:2"},
        {"val": "n:99"},                                   # no ts -> skipped
        {"ts": "t:2024-01-01T00:00:00Z", "val": "n:0"},
    ])
    s = parse_his_grid(grid)
    assert list(s) == [0.0, 2.0]                            # sorted by ts


def test_adapter_assembles_frame_with_canned_transport():
    refs = {"AHU_1_SAT": "@p1", "AHU_1_OAT": "@p2"}
    grids = {
        "@p1": _grid([{"ts": "t:2024-01-01T00:00:00Z", "val": "n:55"},
                      {"ts": "t:2024-01-01T01:00:00Z", "val": "n:56"}]),
        "@p2": _grid([{"ts": "t:2024-01-01T00:00:00Z", "val": "n:70"},
                      {"ts": "t:2024-01-01T01:00:00Z", "val": "n:72"}]),
    }

    def transport(op, params):
        assert op == "hisRead"
        return grids[params["id"]]

    a = HaystackAdapter("http://x", point_refs=refs, transport=transport)
    assert isinstance(a, SourceAdapter)         # satisfies the adapter protocol
    df = a.load_points(["AHU_1_SAT", "AHU_1_OAT"], resample=None)
    assert list(df.columns) == ["AHU_1_SAT", "AHU_1_OAT"]
    assert df["AHU_1_OAT"].tolist() == [70.0, 72.0]


def test_adapter_without_transport_raises():
    a = HaystackAdapter("http://x", point_refs={"p": "@1"})
    with pytest.raises(NotImplementedError):
        a.load_points(["p"])


def test_point_names_sorted():
    a = HaystackAdapter("http://x", point_refs={"b": "@2", "a": "@1"})
    assert a.point_names() == ["a", "b"]


# --- transports ------------------------------------------------------------- #

import json as _json  # noqa: E402

from camber.ingest.haystack import client_transport, http_json_transport  # noqa: E402


def test_client_transport_wires_any_hisread():
    calls = []

    def fake_his_read(point_id, range_str):
        calls.append((point_id, range_str))
        return _grid([{"ts": "t:2024-01-01T00:00:00Z", "val": "n:55"}])

    a = HaystackAdapter("http://x", point_refs={"SAT": "@p1"},
                        transport=client_transport(fake_his_read), range_str="today")
    df = a.load_points(["SAT"], resample=None)
    assert calls == [("@p1", "today")]        # id + range passed through
    assert df["SAT"].iloc[0] == 55.0


def test_http_json_transport_builds_request_and_parses(monkeypatch):
    captured = {}

    class _Resp:
        status = 200
        def __init__(self, body): self._b = body
        def read(self): return self._b
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        return _Resp(_json.dumps(_grid([{"ts": "t:2024-01-01T00:00:00Z",
                                         "val": "n:70"}])).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    t = http_json_transport("http://server/api/", token="secret")
    grid = t("hisRead", {"id": "@p1", "range": "today"})
    assert "hisRead?id=%40p1&range=today" in captured["url"]
    assert captured["headers"].get("authorization") == "Bearer secret"
    assert grid["rows"][0]["val"] == "n:70"


def test_hayson_nested_scalars_decode():
    grid = _grid([{"ts": {"_kind": "dateTime", "val": "2024-01-01T00:00:00Z"},
                   "val": {"_kind": "number", "val": 21.5, "unit": "degC"}}])
    s = parse_his_grid(grid)
    assert s.iloc[0] == 21.5
