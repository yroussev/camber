"""Tests for the read API: facade, pure dispatch, and a live HTTP round-trip."""

import json
import os
import sys
import threading
import urllib.request

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.api import ReadAPI, dispatch, make_server  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.store import ParquetStore  # noqa: E402


def _store(tmp_path):
    st = ParquetStore(str(tmp_path / "tsdb"))
    idx = pd.date_range("2024-01-01", periods=6, freq="1h")
    frame = pd.DataFrame({Role.HEAT_VALVE: range(6), Role.COOL_VALVE: range(6)},
                         index=idx)
    st.write_role_frame(frame, site="S", equip="AHU_1", equip_class="AHU")
    return st


# --- facade ----------------------------------------------------------------- #

def test_facade_sites_points_history(tmp_path):
    api = ReadAPI(_store(tmp_path))
    assert api.sites() == {"sites": ["S"]}
    pts = api.points(site="S")
    assert pts["count"] == 2 and {p["role"] for p in pts["points"]} == {"heat_valve", "cool_valve"}
    h = api.history(site="S", equip="AHU_1", role="heat_valve")
    assert h["count"] == 6
    assert h["history"][0]["role"] == "heat_valve"
    assert "T" in h["history"][0]["ts"]            # ISO timestamp


def test_facade_history_limit_and_filter(tmp_path):
    api = ReadAPI(_store(tmp_path))
    h = api.history(site="S", role="cool_valve", limit=3)
    assert h["count"] == 3
    assert all(r["role"] == "cool_valve" for r in h["history"])


# --- pure dispatch ---------------------------------------------------------- #

def test_dispatch_routes(tmp_path):
    api = ReadAPI(_store(tmp_path))
    assert dispatch(api, "GET", "/sites", {})[0] == 200
    assert dispatch(api, "GET", "/about", {})[1]["ok"] is True
    s, body = dispatch(api, "GET", "/points", {"site": ["S"], "equip": ["AHU_1"]})
    assert s == 200 and body["count"] == 2
    s, body = dispatch(api, "GET", "/history", {"site": ["S"], "limit": ["2"]})
    assert s == 200 and body["count"] == 2


def test_dispatch_unknown_and_method(tmp_path):
    api = ReadAPI(_store(tmp_path))
    assert dispatch(api, "GET", "/nope", {})[0] == 404
    assert dispatch(api, "POST", "/sites", {})[0] == 405


# --- live HTTP round-trip --------------------------------------------------- #

def test_http_server_round_trip(tmp_path):
    httpd = make_server(_store(tmp_path), port=0)     # ephemeral port
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/sites", timeout=5) as r:
            assert r.status == 200
            assert json.loads(r.read())["sites"] == ["S"]
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/history?site=S&role=heat_valve&limit=2",
                timeout=5) as r:
            body = json.loads(r.read())
            assert body["count"] == 2
    finally:
        httpd.shutdown()
        t.join(timeout=5)
