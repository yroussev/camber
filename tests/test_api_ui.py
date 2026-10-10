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


# --------------------------------------------------------------------------- 0.103 (#122)


def test_ui_reads_a_window_thinned_server_side_not_the_first_5000():
    h = live_dashboard_html()
    assert "limit=5000" not in h  # the old first-5,000-samples read is gone
    assert "max_points=" in h and "&start=" in h and "&end=" in h
    # whole span thinned to 2,000 per series; a window read at full resolution up to 20,000
    assert "OVERVIEW=2000" in h and "WINDOW=20000" in h
    assert "source_count" in h and "downsampled" in h  # the page reports what it drew
    assert "id='window'" in h and "min and max of each time bucket kept" in h


def test_ui_has_date_range_presets_and_zoom_out():
    h = live_dashboard_html()
    for token in ("type='date' id='from'", "type='date' id='to'", "id='zoomout'"):
        assert token in h, token
    for days in ("data-days='7'", "data-days='30'", "data-days='0'"):
        assert days in h, days
    assert "Last 7 days" in h and "Last 30 days" in h and ">All<" in h


def test_ui_brush_zooms_and_still_selects():
    h = live_dashboard_html()
    assert "pendingSel={a:a,b:b};go(" in h  # mouseup zooms to the brushed span ...
    assert "window.CAMBER.set(sel)" in h  # ... and the 'N selected' readout still fires
    assert "' selected: '" in h


def test_ui_refreshes_lists_without_a_reload():
    h = live_dashboard_html()
    assert "id='lists'" in h and "Reload lists" in h
    assert "facSel.addEventListener('mousedown',onOpen)" in h
    assert "eqSel.addEventListener('focus',onOpen)" in h
    assert "refreshAll" in h
    # the deep link camber lab uses is still honoured
    assert "get('facility_id')" in h


def test_ui_time_axis_label_comes_from_points_and_never_defaults_to_utc():
    h = live_dashboard_html()
    assert "d.time_axis" in h and "local_label" in h and "utc_label" in h
    assert "local time (no time zone recorded)" in h
    assert "'time ('+zoneName()+')'" not in h  # the old label that said UTC with no zone


def test_ui_hidden_attribute_beats_label_display_rules():
    # the UTC box is hidden by the hidden attribute when no zone is recorded; the
    # .controls label display rule must not override it
    h = live_dashboard_html()
    assert "[hidden]{display:none!important}" in h
    assert "id='utcbox' hidden" in h


def test_ui_page_has_no_external_asset_after_the_rework():
    h = live_dashboard_html()
    assert "<script src" not in h and "<link " not in h and "https://" not in h
    assert "eval(" not in h and "new Function" not in h  # nothing a stricter CSP would refuse
