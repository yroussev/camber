"""Tests for the live web dashboard served by the read-only API (``GET /ui``).

No browser: the routing + content-type/CSP branch + the self-contained HTML string are all checked
via pure ``dispatch`` calls, a ``make_server`` thread + stdlib ``urllib`` round-trip, and an
``html.parser`` well-formedness pass. The regression guard is that every JSON endpoint stays
unchanged (the content-type branch must not leak into the JSON path).
"""

import json
import os
import sys
import threading
import urllib.request
from html.parser import HTMLParser

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.api import ReadAPI, dispatch, make_server  # noqa: E402
from camber.api.ui import live_dashboard_html  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.store import ParquetStore  # noqa: E402


def _store(tmp_path):
    st = ParquetStore(str(tmp_path / "tsdb"))
    idx = pd.date_range("2024-01-01", periods=6, freq="1h")
    frame = pd.DataFrame({Role.HEAT_VALVE: range(6), Role.COOL_VALVE: range(6)}, index=idx)
    st.write_role_frame(frame, facility_id="S", equip="AHU_1", equip_class="AHU", name="Site S")
    return st


# --------------------------------------------------------------------------- pure dispatch


def test_dispatch_ui_returns_html_string(tmp_path):
    api = ReadAPI(_store(tmp_path))
    status, body = dispatch(api, "GET", "/ui", {})
    assert status == 200 and isinstance(body, str)
    assert body.lstrip().lower().startswith("<!doctype html>")


def test_dispatch_ui_trailing_slash(tmp_path):
    api = ReadAPI(_store(tmp_path))
    assert dispatch(api, "GET", "/ui/", {})[0] == 200


def test_dispatch_ui_is_get_only(tmp_path):
    api = ReadAPI(_store(tmp_path))
    assert dispatch(api, "POST", "/ui", {})[0] == 405  # read-only guard inherited


def test_json_routes_still_return_dicts(tmp_path):
    api = ReadAPI(_store(tmp_path))
    for path in ("/", "/about", "/facilities", "/points", "/history"):
        status, body = dispatch(api, "GET", path, {})
        assert status == 200 and isinstance(body, dict), (
            path
        )  # not a str — the /ui branch is scoped


# --------------------------------------------------------------------------- the HTML builder


def test_live_dashboard_html_is_self_contained():
    h = live_dashboard_html()
    assert "<script src" not in h and "<link " not in h  # no external asset
    assert "cdn" not in h.lower() and "https://" not in h  # no CDN / remote fetch
    # the only http:// permitted is the SVG XML namespace identifier (never a network request)
    assert h.count("http://") == 1 and "http://www.w3.org/2000/svg" in h


def test_live_dashboard_html_has_live_bus_and_endpoints():
    h = live_dashboard_html()
    for token in ("window.CAMBER", "fetch(", "setInterval", "/facilities", "/points", "/history"):
        assert token in h, token


def test_live_dashboard_html_is_well_formed():
    seen = set()

    class _P(HTMLParser):
        def handle_starttag(self, tag, attrs):
            seen.add(tag)

    _P().feed(live_dashboard_html())
    assert {"html", "head", "body", "svg", "select", "script"} <= seen


# --------------------------------------------------------------------------- live HTTP round-trip


def _serve(store):
    srv = make_server(store, port=0)
    threading.Thread(target=srv.handle_request, daemon=True).start()  # one request, then stop
    return srv.server_address[1]


def test_ui_route_serves_html_with_csp(tmp_path):
    port = _serve(_store(tmp_path))
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/ui") as r:
        assert r.status == 200
        assert r.headers["Content-Type"].startswith("text/html")
        csp = r.headers["Content-Security-Policy"]
        assert csp and "default-src 'self'" in csp and "connect-src 'self'" in csp
        body = r.read().decode("utf-8")
    assert body.lstrip().lower().startswith("<!doctype html>")


def test_json_endpoint_unchanged_over_http(tmp_path):
    port = _serve(_store(tmp_path))
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/facilities") as r:
        assert r.headers["Content-Type"] == "application/json"
        assert "Content-Security-Policy" not in r.headers  # CSP is HTML-only
        data = json.loads(r.read())
    assert [f["facility_id"] for f in data["facilities"]] == ["S"]
