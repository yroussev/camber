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
    frame = pd.DataFrame({Role.HEAT_VALVE: range(6), Role.COOL_VALVE: range(6)}, index=idx)
    st.write_role_frame(frame, facility_id="S", equip="AHU_1", equip_class="AHU", name="Site S")
    return st


# --- facade ----------------------------------------------------------------- #


def test_facade_facilities_points_history(tmp_path):
    api = ReadAPI(_store(tmp_path))
    # /facilities exposes the id + human name from the registry
    assert api.facilities() == {"facilities": [{"facility_id": "S", "name": "Site S"}]}
    assert api.sites() == {"sites": ["S"]}  # deprecated alias still lists ids
    pts = api.points(facility_id="S")
    assert pts["count"] == 2 and {p["role"] for p in pts["points"]} == {"heat_valve", "cool_valve"}
    assert all(p["facility_id"] == "S" for p in pts["points"])
    h = api.history(facility_id="S", equip="AHU_1", role="heat_valve")
    assert h["count"] == 6
    assert h["history"][0]["role"] == "heat_valve"
    assert "T" in h["history"][0]["ts"]  # ISO timestamp


def test_facade_site_alias_still_resolves(tmp_path):
    api = ReadAPI(_store(tmp_path))
    assert api.points(site="S")["count"] == 2  # legacy site= kwarg -> facility_id
    assert api.history(site="S", role="cool_valve", limit=3)["count"] == 3


# --- pure dispatch ---------------------------------------------------------- #


def test_dispatch_routes(tmp_path):
    api = ReadAPI(_store(tmp_path))
    assert dispatch(api, "GET", "/facilities", {})[1]["facilities"][0]["name"] == "Site S"
    assert dispatch(api, "GET", "/sites", {})[0] == 200  # deprecated alias route
    assert dispatch(api, "GET", "/about", {})[1]["ok"] is True
    s, body = dispatch(api, "GET", "/points", {"facility_id": ["S"], "equip": ["AHU_1"]})
    assert s == 200 and body["count"] == 2
    s, body = dispatch(api, "GET", "/points", {"site": ["S"]})  # legacy query alias
    assert s == 200 and body["count"] == 2
    s, body = dispatch(api, "GET", "/history", {"facility_id": ["S"], "limit": ["2"]})
    assert s == 200 and body["count"] == 2


def test_dispatch_unknown_and_method(tmp_path):
    api = ReadAPI(_store(tmp_path))
    assert dispatch(api, "GET", "/nope", {})[0] == 404
    assert dispatch(api, "POST", "/facilities", {})[0] == 405


# --- live HTTP round-trip --------------------------------------------------- #


def test_http_server_round_trip(tmp_path):
    httpd = make_server(_store(tmp_path), port=0)  # ephemeral port
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/facilities", timeout=5) as r:
            assert r.status == 200
            assert json.loads(r.read())["facilities"] == [{"facility_id": "S", "name": "Site S"}]
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/history?facility_id=S&role=heat_valve&limit=2", timeout=5
        ) as r:
            body = json.loads(r.read())
            assert body["count"] == 2
    finally:
        httpd.shutdown()
        t.join(timeout=5)
