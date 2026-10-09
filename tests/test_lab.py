"""`camber lab` (#77): the loopback-only catalog UI -- routes, request checks, jobs, workspace.

No network: the fake catalog entry (a tiny LBNL-shaped zip of three labelled AHU runs) is served by
an injected opener, as in tests/test_datasets_ingest.py. Covered: the pure ``dispatch_lab`` routes
and the delegated read routes; authentication on every route (0.102, #127: 401 without the session
cookie or bearer token, the ``?token=`` exchange for the cookie and its flags, wrong secrets
refused, the 0600 launch file and a refused insecure folder); 403 for a missing / bad token, a bad
or missing Origin, a bad Host and a cross-site fetch; 415 and 413; the research-only acknowledgement
(403 without it, recorded in the ledger with it); the job lifecycle and cancel (queued and running);
a non-loopback bind refused; the CSP and security headers over real HTTP; `camber serve` still
refusing POST; the 0.95 lifecycle and audit log in a workspace; the no-OT import boundary; the CLI.
"""

import ast
import json
import os
import sys
import threading
import urllib.error
import urllib.request

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_datasets_ingest import BASE, FakeOpener, _ahu_entry_dict, _zip_bytes  # noqa: E402

from camber import cli  # noqa: E402
from camber.api import ReadAPI, dispatch, make_server  # noqa: E402
from camber.datasets import _paths  # noqa: E402
from camber.datasets._catalog import DatasetEntry  # noqa: E402
from camber.lab import (  # noqa: E402
    BODY_LIMIT,
    TOKEN_HEADER,
    JobCancelled,
    JobQueue,
    LabApp,
    _auth,
    dispatch_lab,
    make_lab_server,
)
from camber.lab._ui import LAB_CSP, lab_page_html  # noqa: E402
from camber.portfolio import Portfolio  # noqa: E402
from camber.report.audit import RESEARCH_ONLY_BANNER  # noqa: E402

PORT = 8765
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------------- fixtures


def _entries():
    zb = _zip_bytes()
    entry = DatasetEntry.from_dict(_ahu_entry_dict(zb))
    d = entry.as_dict()
    d.update(
        id="test-nc",
        title="Test NC runs",
        licence="CC-BY-NC-4.0",
        access="research_only",
        ingest={**d["ingest"], "facility": "ds-test-nc"},
    )
    return entry, DatasetEntry.from_dict(d), FakeOpener({BASE + "test.zip": zb})


def _app(tmp_path, **kw):
    entry, ro, opener = _entries()
    if "workspace" not in kw:
        kw.setdefault("store", str(tmp_path / "store"))
    app = LabApp(data_dir=str(tmp_path / "cache"), entries=[entry, ro], opener=opener, **kw)
    app.bind(PORT)
    return app


@pytest.fixture
def app(tmp_path):
    a = _app(tmp_path)
    yield a
    a.close()


def _cookie(app, value=None):
    return f"{_auth.cookie_name(app.port)}={app.session_id if value is None else value}"


def _hdrs(app, **over):
    """Request headers: the lab's Host and, unless ``Cookie=None``, a valid session cookie."""
    h = {"Host": f"127.0.0.1:{app.port}", "Cookie": _cookie(app)}
    h.update(over)
    return {k: v for k, v in h.items() if v is not None}


def get(app, path, query=None, **hdr):
    return dispatch_lab(app, "GET", path, query or {}, _hdrs(app, **hdr))


def post(app, path, body=None, *, raw=None, **hdr):
    data = raw if raw is not None else json.dumps(body if body is not None else {}).encode()
    base = {
        "Origin": f"http://127.0.0.1:{app.port}",
        "Content-Type": "application/json",
        TOKEN_HEADER: app.token,
        "Content-Length": str(len(data)),
    }
    base.update(hdr)
    return dispatch_lab(app, "POST", path, {}, _hdrs(app, **base), data)


def _wait(app, job_id, timeout=120):
    job = app.jobs.get(job_id)
    assert job.wait(timeout), f"job {job_id} did not finish"
    return job.view()


# --------------------------------------------------------------------------- routes


def test_lab_page_carries_token_and_hash_pinned_csp(app):
    status, body, h = get(app, "/lab")
    assert status == 200 and body.lower().startswith("<!doctype html>")
    assert f"content='{app.token}'" in body
    csp = h["Content-Security-Policy"]
    assert csp == LAB_CSP and "'unsafe-inline'" not in csp and "'sha256-" in csp
    assert "frame-ancestors 'none'" in csp and "default-src 'none'" in csp
    assert "style=" not in body.split("<script>")[0].split("</style>")[1]  # no inline styles
    assert "http://" not in body  # nothing absolute: every link and fetch is same-origin


def test_root_redirects_to_lab(app):
    status, _body, h = get(app, "/")
    assert status == 303 and h["Location"] == "/lab"


def test_catalog_view_lists_entries_with_licence_tiers(app):
    status, body, h = get(app, "/lab/catalog")
    assert status == 200 and h["Content-Type"] == "application/json"
    rows = {d["id"]: d for d in body["datasets"]}
    assert set(rows) == {"test-ahu", "test-nc"}
    assert rows["test-nc"]["research_only"] and not rows["test-ahu"]["research_only"]
    assert rows["test-ahu"]["subsets"]["default"]["download_bytes"] > 0
    assert rows["test-ahu"]["fetched"] == {"default": False, "full": False}
    assert body["mode"] == "store" and body["free_bytes"]["data_dir"] > 0
    assert rows["test-ahu"]["facilities"] == [] and rows["test-ahu"]["report"]


def test_read_routes_are_delegated_unchanged(app):
    api = ReadAPI(app.store)
    for path in ("/facilities", "/points", "/history"):
        status, body, h = get(app, path)
        assert (status, body) == dispatch(api, "GET", path, {})
        assert h["Content-Type"] == "application/json"
    status, body, h = get(app, "/ui")
    assert status == 200 and body == dispatch(api, "GET", "/ui", {})[1]
    assert h["Content-Security-Policy"].startswith("default-src 'self'")
    assert "facility_id" in body  # the /ui?facility_id= deep link the lab links to


def test_unknown_routes_and_methods(app):
    assert get(app, "/nope")[0] == 404
    assert get(app, "/lab/jobs/fetch")[0] == 405
    assert get(app, "/lab/jobs/0123456789ab/cancel")[0] == 405
    assert get(app, "/lab/jobs/0123456789ab")[0] == 404
    assert get(app, "/lab/jobs/../../etc")[0] == 404
    assert post(app, "/facilities")[0] == 405
    assert post(app, "/lab")[0] == 405
    assert post(app, "/elsewhere")[0] == 404
    h = _hdrs(app)
    assert dispatch_lab(app, "PUT", "/lab/jobs/fetch", {}, h, b"")[0] == 405
    assert dispatch_lab(app, "DELETE", "/lab", {}, h, b"")[0] == 405


def test_report_route_404s(app):
    assert get(app, "/lab/reports/ds-missing")[0] == 404
    assert get(app, "/lab/reports/bad=id")[0] == 404


# --------------------------------------------------------------------------- request checks


def test_post_without_or_with_a_bad_token_is_403(app):
    assert post(app, "/lab/jobs/fetch", {"ids": ["test-ahu"]}, **{TOKEN_HEADER: None})[0] == 403
    status, body, _ = post(app, "/lab/jobs/fetch", {"ids": ["test-ahu"]}, **{TOKEN_HEADER: "x"})
    assert status == 403 and "token" in body["error"]
    assert post(app, "/lab/jobs/fetch", {"ids": ["test-ahu"]}, **{TOKEN_HEADER: "é"})[0] == 403
    assert app.jobs.jobs() == []


def test_bad_origin_is_403(app):
    for origin in ("http://evil.example", f"http://127.0.0.1:{PORT + 1}", "null"):
        assert post(app, "/lab/jobs/fetch", {"ids": ["test-ahu"]}, Origin=origin)[0] == 403
    assert post(app, "/lab/jobs/fetch", {"ids": ["test-ahu"]}, Origin=None)[0] == 403
    assert get(app, "/lab/catalog", Origin="http://evil.example")[0] == 403
    assert get(app, "/lab/catalog", **{"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert get(app, "/lab/catalog", **{"Sec-Fetch-Site": "same-origin"})[0] == 200
    assert post(app, "/lab/jobs/fetch", {"ids": ["x"]}, Origin=f"http://localhost:{PORT}")[0] == 400
    assert app.jobs.jobs() == []


def test_bad_host_is_403(app):
    for host in ("evil.example", f"evil.example:{PORT}", "127.0.0.1", f"0.0.0.0:{PORT}", None):
        assert get(app, "/lab", Host=host)[0] == 403
        assert post(app, "/lab/jobs/fetch", {"ids": ["test-ahu"]}, Host=host)[0] == 403
    assert get(app, "/lab", Host=f"localhost:{PORT}")[0] == 200


def test_non_json_is_415(app):
    for ctype in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data", None):
        status, *_ = post(app, "/lab/jobs/fetch", {"ids": ["test-ahu"]}, **{"Content-Type": ctype})
        assert status == 415
    ok = post(
        app,
        "/lab/jobs/fetch",
        {"ids": ["nope"]},
        **{"Content-Type": "application/json; charset=utf-8"},
    )
    assert ok[0] == 400  # the charset parameter is fine; the id is not


def test_oversized_body_is_413(app):
    big = json.dumps({"ids": ["test-ahu"], "subset": "x" * BODY_LIMIT}).encode()
    assert post(app, "/lab/jobs/fetch", raw=big)[0] == 413
    # the handler does not read an oversized body: the declared length alone decides
    assert post(app, "/lab/jobs/fetch", raw=b"{}", **{"Content-Length": "99999"})[0] == 413
    status, *_ = dispatch_lab(
        app,
        "POST",
        "/lab/jobs/fetch",
        {},
        _hdrs(
            app,
            Origin=f"http://127.0.0.1:{PORT}",
            **{"Content-Type": "application/json", TOKEN_HEADER: app.token},
        ),
        b"x" * (BODY_LIMIT + 1),
    )
    assert status == 413


def test_body_validation_is_400(app):
    assert post(app, "/lab/jobs/fetch", raw=b"{not json")[0] == 400
    assert post(app, "/lab/jobs/fetch", ["test-ahu"])[0] == 400
    assert post(app, "/lab/jobs/fetch", {"ids": ["test-ahu"], "url": "https://x"})[0] == 400
    assert post(app, "/lab/jobs/fetch", {"ids": []})[0] == 400
    assert post(app, "/lab/jobs/fetch", {"ids": "test-ahu"})[0] == 400
    assert post(app, "/lab/jobs/fetch", {"ids": ["test-ahu", "test-ahu"]})[0] == 400
    assert post(app, "/lab/jobs/fetch", {"ids": ["../etc"]})[0] == 400
    assert post(app, "/lab/jobs/fetch", {"ids": ["test-ahu"], "subset": "huge"})[0] == 400
    assert post(app, "/lab/jobs/fetch", {"ids": ["test-ahu"], "ingest": "yes"})[0] == 400
    assert post(app, "/lab/jobs/fetch", {"ids": ["test-ahu"], "acknowledge": ["x"]})[0] == 400
    assert post(app, "/lab/jobs/0123456789ab/cancel", {"x": 1})[0] == 400
    assert app.jobs.jobs() == []


def test_manual_entry_is_not_fetched(tmp_path):
    entry, _ro, opener = _entries()
    d = entry.as_dict()
    d.update(manual=True, manual_instructions="Download it from the portal.")
    app = LabApp(
        store=str(tmp_path / "s"),
        data_dir=str(tmp_path / "c"),
        entries=[DatasetEntry.from_dict(d)],
        opener=opener,
    )
    app.bind(PORT)
    try:
        status, body, _ = post(app, "/lab/jobs/fetch", {"ids": ["test-ahu"]})
        assert status == 400 and "manual download" in body["error"]
    finally:
        app.close()


# --------------------------------------------------------------------------- authentication (#127)

_GET_ROUTES = (
    "/",
    "/lab",
    "/lab/",
    "/lab/catalog",
    "/lab/jobs",
    "/lab/jobs/0123456789ab",
    "/lab/jobs/fetch",
    "/lab/reports/ds-test-ahu",
    "/lab/docs/workbook/air-economizer.md",
    "/ui",
    "/ui/",
    "/facilities",
    "/points",
    "/history",
    "/nope",
)
_PAGES = {
    "/",
    "/lab",
    "/lab/",
    "/lab/reports/ds-test-ahu",
    "/lab/docs/workbook/air-economizer.md",
    "/ui",
    "/ui/",
}


def test_every_route_needs_credentials(app):
    for path in _GET_ROUTES:
        status, body, h = get(app, path, Cookie=None)
        assert status == 401, path
        assert h["WWW-Authenticate"].startswith("Bearer") and "Set-Cookie" not in h
        if path in _PAGES:
            assert h["Content-Type"].startswith("text/html") and "terminal" in body
            assert "default-src 'none'" in h["Content-Security-Policy"]
            assert "<script" not in body and app.token not in body
        else:
            assert "terminal" in body["error"]
    for path in ("/lab/jobs/fetch", "/lab/jobs/ingest", "/lab/jobs/0123456789ab/cancel"):
        assert post(app, path, {"ids": ["test-ahu"]}, Cookie=None)[0] == 401, path
    for method in ("PUT", "DELETE", "HEAD", "OPTIONS"):
        assert dispatch_lab(app, method, "/lab", {}, _hdrs(app, Cookie=None), b"")[0] == 401
    assert app.jobs.jobs() == []


def test_token_sets_the_cookie_and_redirects_without_it(app):
    q = {"token": [app.access_token], "facility_id": ["ds-test-ahu"]}
    status, body, h = get(app, "/ui", q, Cookie=None)
    assert status == 303 and h["Location"] == "/ui?facility_id=ds-test-ahu"
    assert app.access_token not in h["Location"] and app.access_token not in json.dumps(body)
    name, _, rest = h["Set-Cookie"].partition("=")
    value, *flags = [p.strip() for p in rest.split(";")]
    assert name == f"camber-lab-{app.port}" and value == app.session_id
    assert sorted(flags) == ["HttpOnly", "Path=/", "SameSite=Strict"]  # no Expires: per session
    assert get(app, "/lab", {"token": [app.access_token]}, Cookie=None)[2]["Location"] == "/lab"
    # the cookie it set then opens every route
    assert get(app, "/ui", {"facility_id": ["ds-test-ahu"]}, Cookie=h["Set-Cookie"])[0] == 200
    # a crafted path never redirects off the lab
    for path in ("//evil.example/x", "/\\evil.example", "evil"):
        status, _, h = get(app, path, {"token": [app.access_token]}, Cookie=None)
        assert status == 303 and h["Location"] == "/lab"


def test_secrets_are_distinct_and_wrong_ones_are_refused(app):
    assert len({app.access_token, app.session_id, app.token}) == 3
    for bad in ("x", app.token, app.session_id, app.access_token[:-1], "é" * 20):
        status, _, h = get(app, "/lab", {"token": [bad]}, Cookie=None)
        assert status == 403 and "Set-Cookie" not in h
        assert get(app, "/lab/catalog", Cookie=None, Authorization=f"Bearer {bad}")[0] == 403
    for bad in ("", "x", app.access_token, app.token, app.session_id + "x"):
        assert get(app, "/lab/catalog", Cookie=_cookie(app, bad))[0] == 401
    # the right session id under another port's cookie name (another lab's) is not this lab's
    other = f"{_auth.cookie_name(app.port + 1)}={app.session_id}"
    assert get(app, "/lab/catalog", Cookie=other)[0] == 401
    assert get(app, "/lab/catalog", Cookie=f"a=b; {_cookie(app)}; c=d")[0] == 200
    for scheme, status in (("Bearer", 200), ("bearer", 200), ("Basic", 401)):
        auth = f"{scheme} {app.access_token}"
        assert get(app, "/lab/catalog", Cookie=None, Authorization=auth)[0] == status


def test_post_needs_the_session_and_the_csrf_token(app):
    body = {"ids": ["test-ahu"]}
    # the cookie alone is not enough: the CSRF header is still required
    status, out, _ = post(app, "/lab/jobs/fetch", body, **{TOKEN_HEADER: None})
    assert status == 403 and "CSRF" in out["error"]
    bearer = {"Cookie": None, "Authorization": f"Bearer {app.access_token}"}
    assert post(app, "/lab/jobs/fetch", body, **bearer, **{TOKEN_HEADER: None})[0] == 403
    # the CSRF token alone is not enough either, and a POST takes no ?token=
    assert post(app, "/lab/jobs/fetch", body, Cookie=None)[0] == 401
    data = json.dumps(body).encode()
    hdrs = _hdrs(
        app,
        Cookie=None,
        Origin=f"http://127.0.0.1:{PORT}",
        **{"Content-Type": "application/json", TOKEN_HEADER: app.token},
    )
    q = {"token": [app.access_token]}
    assert dispatch_lab(app, "POST", "/lab/jobs/fetch", q, hdrs, data)[0] == 401
    assert app.jobs.jobs() == []


def test_host_and_origin_are_checked_before_the_credentials(app):
    q = {"token": [app.access_token]}
    status, body, h = get(app, "/lab", q, Cookie=None, Host="rebind.example")
    assert status == 403 and "host" in body["error"] and "Set-Cookie" not in h
    assert get(app, "/lab", q, Cookie=None, **{"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert get(app, "/lab", q, Cookie=None, Origin="http://evil.example")[0] == 403
    assert get(app, "/lab", q, Cookie=None, **{"Sec-Fetch-Site": "none"})[0] == 303
    assert post(app, "/lab/jobs/fetch", {"ids": ["x"]}, Origin=None)[0] == 403


def test_explicit_access_token(tmp_path):
    a = _app(tmp_path, access_token="s" * 20)
    try:
        assert a.access_token == "s" * 20
        assert a.launch_url() == f"http://127.0.0.1:{PORT}/lab?token={'s' * 20}"
        assert a.launch_url("/ui") == f"http://127.0.0.1:{PORT}/ui?token={'s' * 20}"
    finally:
        a.close()
    with pytest.raises(ValueError, match="at least 16"):
        _app(tmp_path, access_token="short")
    with pytest.raises(ValueError, match="differ"):
        _app(tmp_path, access_token="t" * 20, token="t" * 20)


# --------------------------------------------------------------------------- the launch file


def test_launch_file_is_private(tmp_path):
    d = tmp_path / "cfg" / "camber"
    path = _auth.write_launch_file("http://127.0.0.1:1/lab?token=abc", 1, d)
    assert path == str(d / "lab-1.url")
    assert open(path).read() == "http://127.0.0.1:1/lab?token=abc\n"
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert os.stat(d).st_mode & 0o777 == 0o700
    # rewritten in place (a restarted lab), still 0600, no temporary file left behind
    os.chmod(path, 0o644)
    _auth.write_launch_file("http://127.0.0.1:1/lab?token=new", 1, d)
    assert os.stat(path).st_mode & 0o777 == 0o600 and os.listdir(d) == ["lab-1.url"]
    # removed on exit only while it still holds this lab's URL
    _auth.remove_launch_file(path, "http://127.0.0.1:1/lab?token=abc")
    assert os.path.exists(path)
    _auth.remove_launch_file(path, "http://127.0.0.1:1/lab?token=new")
    assert not os.path.exists(path)


def test_launch_dir_resolution(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    assert _auth.launch_dir() == str(tmp_path / "run" / "camber")
    monkeypatch.delenv("XDG_RUNTIME_DIR")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert _auth.launch_file(8765) == str(tmp_path / "cfg" / "camber" / "lab-8765.url")
    monkeypatch.delenv("XDG_CONFIG_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert _auth.launch_dir() == str(tmp_path / "home" / ".config" / "camber")


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX owners and modes")
def test_insecure_launch_folder_or_file_is_refused(tmp_path, monkeypatch, capsys):
    shared = tmp_path / "shared"
    shared.mkdir()
    for mode in (0o770, 0o777, 0o722):
        os.chmod(shared, mode)
        with pytest.raises(PermissionError, match="group- or world-writable"):
            _auth.write_launch_file("u", 1, shared)
        assert os.listdir(shared) == []
    os.chmod(shared, 0o700)
    bad = shared / "lab-1.url"
    bad.write_text("planted")
    os.chmod(bad, 0o666)
    with pytest.raises(PermissionError, match="file"):
        _auth.write_launch_file("u", 1, shared)
    assert bad.read_text() == "planted"
    # a folder another user owns
    me = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: me + 1)
    with pytest.raises(PermissionError, match="owned by another user"):
        _auth.write_launch_file("u", 2, tmp_path / "theirs")
    monkeypatch.undo()
    # the lab still runs and prints the URL, but writes nothing there
    a = _app(tmp_path)
    try:
        os.chmod(shared, 0o777)
        from camber.lab import announce

        assert announce(a, launch_dir=shared) is None
        out, err = capsys.readouterr()
        assert a.launch_url() in out and "launch file not written" in err
        os.chmod(shared, 0o700)
        path = announce(a, launch_dir=tmp_path / "ok")
        assert path and path in capsys.readouterr().out
    finally:
        os.chmod(shared, 0o700)
        a.close()


# --------------------------------------------------------------------------- research-only


def test_research_only_needs_the_typed_acknowledgement(app):
    status, body, _ = post(app, "/lab/jobs/fetch", {"ids": ["test-nc"]})
    assert status == 403 and "research / non-commercial" in body["error"]
    wrong = {"ids": ["test-nc"], "acknowledge": {"test-nc": "test-n"}}
    assert post(app, "/lab/jobs/fetch", wrong)[0] == 403
    assert post(app, "/lab/jobs/ingest", {"ids": ["test-nc"]})[0] == 403
    assert app.jobs.jobs() == [] and app.opener.calls == []
    assert _paths.read_acknowledgements(app.data_dir) == []

    ok = {"ids": ["test-nc"], "ingest": True, "acknowledge": {"test-nc": "test-nc"}}
    status, body, _ = post(app, "/lab/jobs/fetch", ok)
    assert status == 202
    view = _wait(app, body["job"]["id"])
    assert view["state"] == "done", view
    ledger = _paths.read_acknowledgements(app.data_dir)
    assert [(r["dataset_id"], r["via"]) for r in ledger] == [("test-nc", "lab fetch")]
    rows = {d["id"]: d for d in get(app, "/lab/catalog")[1]["datasets"]}
    assert rows["test-nc"]["acknowledged"]
    assert rows["test-nc"]["facilities"][0]["redistribution"] == "prohibited"
    # a later ingest needs no new acknowledgement (the fetch recorded one) ...
    assert post(app, "/lab/jobs/ingest", {"ids": ["test-nc"]})[0] == 202
    # ... but every fetch does, as on the CLI
    assert post(app, "/lab/jobs/fetch", {"ids": ["test-nc"]})[0] == 403
    status, html, h = get(app, "/lab/reports/ds-test-nc")
    assert status == 200 and RESEARCH_ONLY_BANNER in html
    assert "sandbox" in h["Content-Security-Policy"]


# --------------------------------------------------------------------------- jobs


def test_fetch_and_ingest_job_lifecycle_then_trends_and_report(app):
    status, body, _ = post(app, "/lab/jobs/fetch", {"ids": ["test-ahu"], "ingest": True})
    assert status == 202 and body["job"]["state"] in ("queued", "running")
    jid = body["job"]["id"]
    view = _wait(app, jid)
    assert view["state"] == "done" and view["error"] is None
    assert view["started_at"] and view["finished_at"]
    res = view["result"][0]
    assert res["fetch"]["downloaded_bytes"] > 0 and res["fetch"]["citation"]
    assert res["ingest"]["facilities"] == ["ds-test-ahu"] and res["ingest"]["rows"] > 0
    assert "store" not in res["ingest"]
    assert any("downloading test.zip" in m for m in view["log"])
    assert get(app, f"/lab/jobs/{jid}")[1]["job"]["state"] == "done"
    assert [j["id"] for j in get(app, "/lab/jobs")[1]["jobs"]] == [jid]

    rows = {d["id"]: d for d in get(app, "/lab/catalog")[1]["datasets"]}
    assert rows["test-ahu"]["fetched"]["default"]
    assert rows["test-ahu"]["facilities"][0]["facility_id"] == "ds-test-ahu"
    pts = get(app, "/points", {"facility_id": ["ds-test-ahu"]})[1]
    assert pts["count"] > 0
    status, html, h = get(app, "/lab/reports/ds-test-ahu")
    assert status == 200 and "Test Lab (2025)" in html and RESEARCH_ONLY_BANNER not in html
    assert "script-src 'unsafe-inline'" in h["Content-Security-Policy"]
    assert get(app, "/lab/reports/ds-test-ahu")[1] is html  # cached per content hash

    # an ingest of unchanged inputs is skipped
    status, body, _ = post(app, "/lab/jobs/ingest", {"ids": ["test-ahu"]})
    view = _wait(app, body["job"]["id"])
    assert view["state"] == "done" and view["result"][0]["ingest"]["skipped"]


def test_failed_job_reports_its_error(app):
    status, body, _ = post(app, "/lab/jobs/ingest", {"ids": ["test-ahu"]})  # nothing fetched
    assert status == 202
    view = _wait(app, body["job"]["id"])
    assert view["state"] == "failed" and view["error"]


def _blocker(app):
    """Occupy the single worker until ``release`` is set; it reports until cancelled."""
    started, release = threading.Event(), threading.Event()

    def work(job):
        started.set()
        while not release.wait(0.01):
            job.report("blocking")
        return "released"

    job = app.jobs.submit("block", {}, work)
    assert started.wait(5)
    return job, release


def test_cancel_queued_and_running_jobs(app):
    blocker, release = _blocker(app)
    status, body, _ = post(app, "/lab/jobs/fetch", {"ids": ["test-ahu"], "ingest": True})
    queued = body["job"]["id"]
    assert get(app, f"/lab/jobs/{queued}")[1]["job"]["state"] == "queued"  # one worker: waits
    status, body, _ = post(app, f"/lab/jobs/{queued}/cancel")
    assert status == 200 and body["job"]["state"] == "cancelled"

    status, body, _ = post(app, f"/lab/jobs/{blocker.id}/cancel")
    assert status == 200 and body["job"]["cancel_requested"]
    assert blocker.wait(5) and blocker.view()["state"] == "cancelled"
    release.set()
    assert app.opener.calls == []  # the cancelled fetch never ran
    assert post(app, "/lab/jobs/0123456789ab/cancel")[0] == 404
    assert post(app, "/lab/jobs/not-a-job/cancel")[0] == 404


def test_cancel_during_a_download_keeps_the_part_file(app):
    class CancellingOpener(FakeOpener):
        def open(self, req, timeout=None):  # the user presses Cancel mid-download
            for j in app.jobs.jobs():
                app.jobs.cancel(j.id)
            return super().open(req, timeout)

    app.opener = CancellingOpener(app.opener.files)
    status, body, _ = post(app, "/lab/jobs/fetch", {"ids": ["test-ahu"], "ingest": True})
    view = _wait(app, body["job"]["id"])
    assert view["state"] == "cancelled" and view["result"] is None
    dl = _paths.downloads_dir(app.data_dir, "test-ahu")
    assert os.path.isfile(os.path.join(dl, "test.zip.part"))  # kept for a Range resume
    assert not os.path.exists(os.path.join(dl, "test.zip"))
    assert get(app, "/lab/catalog")[1]["datasets"][0]["facilities"] == []


def test_job_queue_runs_one_job_at_a_time_and_survives_errors():
    q = JobQueue()
    try:
        order, lock = [], threading.Lock()

        def work(tag):
            def run(job):
                with lock:
                    order.append(("start", tag))
                job.report(f"{tag} half", 1, 2)
                with lock:
                    order.append(("end", tag))
                return tag

            return run

        jobs = [q.submit("t", {"n": i}, work(i)) for i in range(3)]
        bad = q.submit("t", {}, lambda job: 1 / 0)
        assert all(j.wait(5) for j in jobs) and bad.wait(5)
        assert order == [(e, i) for i in range(3) for e in ("start", "end")]
        assert [j.view()["result"] for j in jobs] == [0, 1, 2]
        assert bad.view()["state"] == "failed" and "ZeroDivisionError" in bad.view()["error"]
        assert jobs[0].view()["done"] == 1 and jobs[0].view()["total"] == 2
        assert not q.busy() and q.cancel(jobs[0].id).view()["state"] == "done"
        assert q.cancel("nope") is None
        with pytest.raises(JobCancelled):
            j = q.submit("t", {}, lambda job: None)
            j.wait(5)
            j._cancel.set()
            j.report("late")
    finally:
        q.close()


def test_too_many_pending_jobs_is_429(app, monkeypatch):
    from camber.lab import _app as lab_app

    monkeypatch.setattr(lab_app, "MAX_PENDING_JOBS", 1)
    _blocker_job, release = _blocker(app)
    try:
        assert post(app, "/lab/jobs/fetch", {"ids": ["test-ahu"]})[0] == 429
    finally:
        release.set()


# --------------------------------------------------------------------------- binding + HTTP


@pytest.mark.parametrize("host", ["0.0.0.0", "localhost", "::1", "192.168.1.10", ""])
def test_non_loopback_bind_is_refused(tmp_path, host):
    a = _app(tmp_path)
    try:
        with pytest.raises(ValueError, match="127.0.0.1 only"):
            make_lab_server(a, port=0, host=host)
    finally:
        a.close()


def _http(url, *, method="GET", data=None, headers=None, opener=None):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with (opener or urllib.request.build_opener()).open(req, timeout=30) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kw):
        return None


def test_http_round_trip_headers_and_checks(tmp_path):
    import http.cookiejar

    a = _app(tmp_path)
    httpd = make_lab_server(a, port=0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        assert httpd.server_address[0] == "127.0.0.1" and a.port == httpd.server_address[1]
        base = f"http://127.0.0.1:{a.port}"
        # no credentials: 401 everywhere, the delegated read routes included
        for path in ("/lab", "/lab/catalog", "/ui", "/facilities", "/points", "/history"):
            status, h, body = _http(base + path)
            assert status == 401 and a.token.encode() not in body, path
        assert b"terminal" in _http(base + "/lab")[2]
        # the launch URL: 303 with the cookie, without following it
        raw = urllib.request.build_opener(_NoRedirect)
        status, h, _ = _http(a.launch_url(), opener=raw)
        assert status == 303 and h["Location"] == "/lab"
        assert h["Set-Cookie"] == _auth.session_cookie(a.port, a.session_id)
        # a browser-like client: follows the redirect, keeps the cookie
        jar = http.cookiejar.CookieJar()
        browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        status, h, body = _http(a.launch_url(), opener=browser)
        assert status == 200 and h["Content-Security-Policy"] == LAB_CSP
        assert [c.value for c in jar] == [a.session_id] and a.access_token.encode() not in body
        assert h["X-Content-Type-Options"] == "nosniff" and h["Referrer-Policy"] == "no-referrer"
        assert h["X-Frame-Options"] == "DENY" and h["Cache-Control"] == "no-store"
        assert a.token.encode() in body
        status, h, _ = _http(base + "/lab/catalog", opener=browser)
        assert status == 200 and "default-src 'none'" in h["Content-Security-Policy"]
        assert _http(base + "/facilities", opener=browser)[0] == 200
        # a script: the bearer token instead of the cookie
        bearer = {"Authorization": f"Bearer {a.access_token}"}
        assert _http(base + "/lab/catalog", headers=bearer)[0] == 200

        payload = json.dumps({"ids": ["test-ahu"]}).encode()
        hdr = {"Content-Type": "application/json", "Origin": base}
        assert _http(base + "/lab/jobs/fetch", method="POST", data=payload, headers=hdr)[0] == 401
        post_ = dict(opener=browser, method="POST")
        assert _http(base + "/lab/jobs/fetch", data=payload, headers=hdr, **post_)[0] == 403
        hdr[TOKEN_HEADER] = a.token
        status, _, body = _http(base + "/lab/jobs/fetch", data=payload, headers=hdr, **post_)
        assert status == 202
        assert a.jobs.get(json.loads(body)["job"]["id"]).wait(60)
        big = b"{" + b" " * (BODY_LIMIT + 10) + b"}"
        assert _http(base + "/lab/jobs/fetch", data=big, headers=hdr, **post_)[0] == 413
        assert _http(base + "/lab", method="PUT", data=b"{}", headers=hdr, opener=browser)[0] == 405
        assert _http(base + "/lab", method="OPTIONS", headers=hdr, opener=browser)[0] == 405
        # DNS rebinding: a foreign Host header is refused even on the loopback socket
        assert _http(base + "/lab", headers={"Host": "rebind.example"}, opener=browser)[0] == 403
    finally:
        httpd.shutdown()
        httpd.server_close()
        a.close()


def test_camber_serve_still_refuses_post(tmp_path):
    """Regression: the lab's POST routes are the lab's; `camber serve` stays GET-only."""
    from camber.store import ParquetStore

    api = ReadAPI(ParquetStore(str(tmp_path / "s")))
    for path in ("/", "/ui", "/facilities", "/points", "/history", "/lab/jobs/fetch"):
        assert dispatch(api, "POST", path, {})[0] == 405
    httpd = make_server(ParquetStore(str(tmp_path / "s")), port=0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}/lab/jobs/fetch"
        status, _, _ = _http(
            url, method="POST", data=b"{}", headers={"Content-Type": "application/json"}
        )
        assert status in (405, 501)  # the read handler defines no POST at all
    finally:
        httpd.shutdown()
        httpd.server_close()


# --------------------------------------------------------------------------- workspace


def test_workspace_ingest_follows_the_lifecycle_and_is_audited(tmp_path):
    pf = Portfolio.init(str(tmp_path / "ws"))
    a = _app(tmp_path, workspace=pf.root)
    try:
        assert a.mode == "workspace" and a.store.root == pf.store_root
        status, body, _ = post(a, "/lab/jobs/fetch", {"ids": ["test-ahu"], "ingest": True})
        view = _wait(a, body["job"]["id"])
        assert view["state"] == "done", view
        fac = pf.facility("ds-test-ahu")
        assert fac["state"] == "active" and fac["registered"]
        actions = [(r["action"], r["facility_id"]) for r in pf.audit_log()]
        assert ("facility.add", "ds-test-ahu") in actions
        assert ("facility.activate", "ds-test-ahu") in actions
        assert ("lab.fetch", None) in actions and ("lab.ingest", "ds-test-ahu") in actions
        add = next(r for r in pf.audit_log() if r["action"] == "facility.add")
        assert add["to_state"] == "provisioning"
        assert get(a, "/lab/catalog")[1]["workspace"] == pf.root

        # re-ingest: no second registration, still audited
        n_add = actions.count(("facility.add", "ds-test-ahu"))
        status, body, _ = post(a, "/lab/jobs/ingest", {"ids": ["test-ahu"], "force": True})
        assert _wait(a, body["job"]["id"])["state"] == "done"
        actions = [(r["action"], r["facility_id"]) for r in pf.audit_log()]
        assert actions.count(("facility.add", "ds-test-ahu")) == n_add
        assert actions.count(("lab.ingest", "ds-test-ahu")) == 2

        # a suspended facility is left to its lifecycle
        pf.transition("ds-test-ahu", "suspend", reason="test")
        status, body, _ = post(a, "/lab/jobs/ingest", {"ids": ["test-ahu"], "force": True})
        view = _wait(a, body["job"]["id"])
        assert view["state"] == "failed" and "suspended" in view["error"]
        rows = {d["id"]: d for d in get(a, "/lab/catalog")[1]["datasets"]}
        assert rows["test-ahu"]["facilities"][0]["state"] == "suspended"
    finally:
        a.close()


def test_workspace_research_only_acknowledgement_is_audited(tmp_path):
    pf = Portfolio.init(str(tmp_path / "ws"))
    a = _app(tmp_path, workspace=pf.root)
    try:
        ok = {"ids": ["test-nc"], "acknowledge": {"test-nc": "test-nc"}}
        status, body, _ = post(a, "/lab/jobs/fetch", ok)
        assert _wait(a, body["job"]["id"])["state"] == "done"
        rec = [r for r in pf.audit_log() if r["action"] == "lab.acknowledge"]
        assert rec and rec[0]["details"]["licence"] == "CC-BY-NC-4.0"
    finally:
        a.close()


def test_lab_app_needs_exactly_one_target(tmp_path):
    with pytest.raises(ValueError):
        LabApp(data_dir=str(tmp_path))
    with pytest.raises(ValueError):
        LabApp(store=str(tmp_path / "s"), workspace=str(tmp_path / "w"), data_dir=str(tmp_path))


# --------------------------------------------------------------------------- import boundary

_FORBIDDEN_TOP = {"BAC0", "bacpypes", "bacpypes3", "pymodbus", "asyncua", "opcua", "paho"}
_FORBIDDEN_PARTS = ("bacnet", "modbus", "opcua", "mqtt", "openadr")


def _module_file(name: str):
    p = os.path.join(_ROOT, *name.split("."))
    for cand in (p + ".py", os.path.join(p, "__init__.py")):
        if os.path.isfile(cand):
            return cand
    return None


def _imports(name: str, path: str) -> set:
    tree = ast.parse(open(path, encoding="utf-8").read())
    pkg = name if path.endswith("__init__.py") else name.rsplit(".", 1)[0]
    out = set()
    for node in ast.walk(tree):  # module level *and* lazy imports inside functions
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = pkg.split(".")
                parts = parts[: len(parts) - (node.level - 1)]
                mod = ".".join(parts + ([node.module] if node.module else []))
            else:
                mod = node.module or ""
            out.add(mod)
            out.update(f"{mod}.{a.name}" for a in node.names)
    return out


def _forbidden(mod: str) -> bool:
    parts = mod.split(".")
    if parts[0] in _FORBIDDEN_TOP:
        return True
    if parts[0] != "camber":
        return False
    return "edge" in parts or any(p.startswith(_FORBIDDEN_PARTS) for p in parts)


def test_lab_and_datasets_import_no_ot_protocol_or_edge_module():
    """Static closure over every import (including lazy ones) reachable from camber.lab and
    camber.datasets inside the package: no BACnet / Modbus / OPC-UA / MQTT / OpenADR / edge."""
    seen: dict = {}
    todo = ["camber.lab", "camber.datasets"]
    for sub in ("lab", "datasets"):
        todo += [
            f"camber.{sub}.{f[:-3]}"
            for f in os.listdir(os.path.join(_ROOT, "camber", sub))
            if f.endswith(".py") and f != "__init__.py"
        ]
    while todo:
        mod = todo.pop()
        if mod in seen:
            continue
        path = _module_file(mod)
        if path is None:
            continue
        seen[mod] = _imports(mod, path)
        todo += [m for m in seen[mod] if m.startswith("camber")]
    assert "camber.lab._server" in seen and "camber.datasets._fetch" in seen
    bad = sorted(f"{m} -> {i}" for m, imps in seen.items() for i in imps if _forbidden(i))
    assert bad == []
    assert _forbidden("camber.edge.spool") and _forbidden("camber.ingest.bacnet_client")
    assert _forbidden("pymodbus.client") and not _forbidden("camber.config._MV_LEDGER_KEYS")


# --------------------------------------------------------------------------- CLI


class _FakeHTTPD:
    def __init__(self, app):
        self.app = app
        self.closed = False

    def serve_forever(self):
        raise KeyboardInterrupt

    def server_close(self):
        self.closed = True


def test_cli_lab_plain_store_and_workspace(tmp_path, monkeypatch, capsys):
    import camber.lab as lab

    made = []

    def fake(app, *, port):
        app.bind(port)
        made.append((app, port))
        return _FakeHTTPD(app)

    seen = []
    real_announce = lab.announce

    def announce(app):  # the launch file exists while the lab runs
        path = real_announce(app)
        seen.append((path, open(path).read(), oct(os.stat(path).st_mode & 0o777)))
        return path

    monkeypatch.setattr(lab, "make_lab_server", fake)
    monkeypatch.setattr(lab, "announce", announce)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.delenv("CAMBER_PORTFOLIO", raising=False)
    monkeypatch.chdir(tmp_path)
    store = str(tmp_path / "st")
    assert cli.main(["lab", "--store", store, "--dir", str(tmp_path / "c"), "--port", "9001"]) == 0
    app, port = made[-1]
    assert port == 9001 and app.mode == "store" and app.store.root == store
    out = capsys.readouterr().out
    assert f"http://127.0.0.1:9001/lab?token={app.access_token}" in out
    path, text, mode = seen[-1]
    assert path == str(tmp_path / "run" / "camber" / "lab-9001.url") and path in out
    assert text == app.launch_url() + "\n" and mode == "0o600"
    assert not os.path.exists(path)  # removed when the lab stops

    ws = Portfolio.init(str(tmp_path / "ws")).root
    assert cli.main(["lab", "--workspace", ws, "--dir", str(tmp_path / "c")]) == 0
    assert made[-1][0].mode == "workspace"
    # a --store that belongs to a workspace runs in workspace mode
    assert cli.main(["lab", "--store", os.path.join(ws, "store")]) == 0
    assert made[-1][0].mode == "workspace"
    # neither: the current directory's lab_store
    assert cli.main(["lab"]) == 0
    assert made[-1][0].store.root == "lab_store"

    assert cli.main(["lab", "--store", store, "--workspace", ws]) == 1
    assert cli.main(["lab", "--workspace", str(tmp_path / "nope")]) == 1
    assert "not a portfolio workspace" in capsys.readouterr().err


def test_page_is_well_formed_and_inline_only():
    from html.parser import HTMLParser

    page = lab_page_html("tok'en<")

    class P(HTMLParser):
        srcs: list = []

        def handle_starttag(self, tag, attrs):
            d = dict(attrs)
            for k in ("src", "href"):
                if d.get(k):
                    self.srcs.append(d[k])
            assert not any(k.startswith("on") for k in d)
            assert "style" not in d

    p = P()
    p.feed(page)
    assert p.srcs == []  # no external script, stylesheet or font
    assert "tok&#x27;en&lt;" in page
