"""`camber serve` request checks (0.103, #128): the Host allowlist against DNS rebinding, the
token auth (the `camber lab` model; the loopback default since 0.107, #221), and GET-only (405 on
every other method)."""

import http.client
import json
import os
import socket
import sys
import threading

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber import _access  # noqa: E402
from camber.api import make_server  # noqa: E402
from camber.api.server import ALLOWED_HOSTS_ENV, _announce, check_request  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.store import ParquetStore  # noqa: E402

ROUTES = ("/ui", "/facilities", "/points?facility_id=S", "/history?facility_id=S&limit=2")
TOKEN = "t" * 24


def _store(tmp_path):
    st = ParquetStore(str(tmp_path / "tsdb"))
    idx = pd.date_range("2024-01-01", periods=4, freq="1h")
    frame = pd.DataFrame({Role.HEAT_VALVE: range(4)}, index=idx)
    st.write_role_frame(frame, facility_id="S", equip="AHU_1", equip_class="AHU", name="Site S")
    return st


@pytest.fixture
def running(tmp_path, monkeypatch):
    """Start a server in a thread; yields a factory ``(**make_server kwargs) -> httpd``."""
    monkeypatch.delenv(ALLOWED_HOSTS_ENV, raising=False)
    started = []

    def start(**kw):
        httpd = make_server(_store(tmp_path), port=0, **kw)
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        started.append((httpd, t))
        return httpd

    yield start
    for httpd, t in started:
        httpd.shutdown()
        httpd.server_close()
        t.join(timeout=5)


def _req(httpd, path, *, method="GET", host="default", headers=None, connect="127.0.0.1"):
    """One raw request; ``host=None`` sends no Host header, "default" sends 127.0.0.1:<port>."""
    port = httpd.server_address[1]
    conn = http.client.HTTPConnection(connect, port, timeout=5)
    hdrs = dict(headers or {})
    if host == "default":
        hdrs["Host"] = f"127.0.0.1:{port}"
    elif host is not None:
        hdrs["Host"] = host
    conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    for k, v in hdrs.items():
        conn.putheader(k, v)
    conn.endheaders()
    r = conn.getresponse()
    body = r.read()
    out = r.status, {k.lower(): v for k, v in r.getheaders()}, body
    conn.close()
    return out


# --------------------------------------------------------------------------- the allowlist


def test_host_allowlist_values():
    a = _access.HostAllowlist("127.0.0.1", 8080)
    for ok in ("127.0.0.1:8080", "localhost:8080", "LOCALHOST:8080", "[::1]:8080"):
        assert a.allows(ok), ok
    for bad in (
        None,
        "",
        "127.0.0.1",  # no port: not what a browser sends for :8080
        "localhost:9999",  # port mismatch
        "[::1]:9999",
        "evil.example:8080",  # DNS rebinding: the attacker's name, resolving to 127.0.0.1
        "localhost.evil.example:8080",
        "127.0.0.1.nip.io:8080",
        "0.0.0.0:8080",
        "192.168.1.10:8080",
    ):
        assert not a.allows(bad), bad
    # bound to a LAN address: that address (not the loopback names)
    lan = _access.HostAllowlist("192.168.1.10", 8080)
    assert lan.allows("192.168.1.10:8080") and not lan.allows("localhost:8080")
    # IPv6: bound literal, bracketed in Host
    v6 = _access.HostAllowlist("fd00::5", 8080)
    assert v6.allows("[fd00::5]:8080") and not v6.allows("[fd00::6]:8080")
    assert _access.HostAllowlist("::1", 81).allows("127.0.0.1:81")  # ::1 is loopback
    # every interface: loopback names (the container health check) + the explicit list
    wild = _access.HostAllowlist("0.0.0.0", 8080, ["camber.example.org", "proxy.lan:8443"])
    assert wild.allows("localhost:8080") and wild.allows("camber.example.org")
    assert wild.allows("camber.example.org:443")  # a bare name matches any port
    assert wild.allows("proxy.lan:8443") and not wild.allows("proxy.lan:9000")  # explicit port
    assert not wild.allows("0.0.0.0:8080") and not wild.allows("evil.example:8080")
    star = _access.HostAllowlist("0.0.0.0", 8080, ["*"])
    assert star.allows("anything.example") and star.allows(None) and "rebinding" in star.describe()
    assert _access.parse_host_list(" a, b:1,,c ") == ["a", "b:1", "c"]
    assert _access.split_host("[::1]:80") == ("[::1]", "80")
    assert _access.split_host("fe80::1") == ("[fe80::1]", None)


def test_wildcard_bind_needs_an_allowlist(tmp_path, monkeypatch):
    monkeypatch.delenv(ALLOWED_HOSTS_ENV, raising=False)
    for host in ("0.0.0.0", "::", ""):
        with pytest.raises(ValueError, match="allow-host"):
            make_server(_store(tmp_path), host=host, port=0)
    # an explicit list (argument, or the env var) lets it start
    httpd = make_server(_store(tmp_path), host="0.0.0.0", port=0, allowed_hosts=["camber.lan"])
    httpd.server_close()
    monkeypatch.setenv(ALLOWED_HOSTS_ENV, "camber.lan, localhost")
    httpd = make_server(_store(tmp_path), host="0.0.0.0", port=0)
    assert httpd.access.hosts.names == {"camber.lan", "localhost"}
    httpd.server_close()
    with pytest.raises(ValueError, match="auth must be"):
        make_server(_store(tmp_path), port=0, auth="password")
    with pytest.raises(ValueError, match="at least"):
        make_server(_store(tmp_path), port=0, auth="token", access_token="short")


def test_host_check_over_http(running):
    httpd = running(auth="none")
    port = httpd.server_address[1]
    assert _req(httpd, "/facilities")[0] == 200
    assert _req(httpd, "/facilities", host=f"localhost:{port}")[0] == 200
    for bad in ("rebind.example", f"rebind.example:{port}", f"localhost:{port + 1}", None):
        status, h, body = _req(httpd, "/facilities", host=bad)
        assert status == 403, bad
        assert "host not allowed" in json.loads(body)["error"].lower()
    status, h, body = _req(httpd, "/ui", host=f"rebind.example:{port}")
    assert status == 403 and h["content-type"].startswith("text/html")
    assert "script" not in body.decode() and "default-src 'none'" in h["content-security-policy"]
    # the Host check comes first for every method too
    assert _req(httpd, "/facilities", method="POST", host="rebind.example")[0] == 403


def test_ipv6_loopback_bind(running):
    if not socket.has_ipv6:
        pytest.skip("no IPv6")
    try:
        httpd = running(host="::1", auth="none")
    except OSError:
        pytest.skip("cannot bind ::1 here")
    port = httpd.server_address[1]
    assert _req(httpd, "/health", host=f"[::1]:{port}", connect="::1")[0] == 200
    assert _req(httpd, "/health", host=f"rebind.example:{port}", connect="::1")[0] == 403
    assert httpd.access.launch_url() == f"http://[::1]:{port}/ui"


def test_allowed_hosts_env_and_proxy_name(running, monkeypatch):
    monkeypatch.setenv(ALLOWED_HOSTS_ENV, "camber.example.org")
    httpd = running(auth="none")
    assert _req(httpd, "/facilities", host="camber.example.org")[0] == 200
    assert _req(httpd, "/facilities", host="other.example.org")[0] == 403
    httpd2 = running(allowed_hosts=["*"], auth="none")  # the argument wins over the env var
    assert _req(httpd2, "/facilities", host="whatever.example")[0] == 200


# --------------------------------------------------------------------------- GET-only


def test_every_other_method_is_405(running):
    httpd = running(auth="none")
    for method in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS"):
        for path in ("/facilities", "/ui", "/history"):
            status, _, body = _req(httpd, path, method=method)
            assert status == 405, (method, path)
            assert json.loads(body)["error"] == "method not allowed"
    status, _, body = _req(httpd, "/facilities", method="HEAD")
    assert status == 405 and body == b""


# --------------------------------------------------------------------------- token auth


def test_token_auth_by_default_on_loopback(running, monkeypatch):
    """0.107 (#221): ``auth`` left unset is token auth on loopback; ``auth="none"`` is open."""
    for host in ("127.0.0.1", "localhost"):
        httpd = running(host=host)
        assert httpd.access.auth == "token" and not httpd.access.auth_explicit
        assert len(httpd.access.access_token) == 43
        assert "?token=" in httpd.access.launch_url()
        for path in ROUTES:
            assert _req(httpd, path)[0] == 401, (host, path)
        bearer = {"Authorization": f"Bearer {httpd.access.access_token}"}
        for path in ROUTES:
            assert _req(httpd, path, headers=bearer)[0] == 200, (host, path)
        assert json.loads(_req(httpd, "/health")[2]) == {"ok": True}  # probes need no secret
    opened = running(auth="none")
    assert opened.access.auth == "none" and opened.access.access_token is None
    for path in ROUTES:
        assert _req(opened, path)[0] == 200, path


def test_non_loopback_default_is_unchanged(running, monkeypatch, capsys):
    """0.107 (#221): every other bind keeps no authentication with the warning (#134's scope),
    including the container image's 0.0.0.0 with CAMBER_API_ALLOWED_HOSTS."""
    from camber.api.server import _default_auth

    assert _default_auth("127.0.0.1") == _default_auth("127.5.6.7") == "token"
    assert _default_auth("localhost") == _default_auth("::1") == _default_auth("[::1]") == "token"
    for host in ("0.0.0.0", "::", "", "192.168.1.10", "fd00::5", "camber.lan"):
        assert _default_auth(host) == "none", host
    monkeypatch.setenv(ALLOWED_HOSTS_ENV, "localhost,127.0.0.1")
    httpd = running(host="0.0.0.0")
    assert httpd.access.auth == "none" and httpd.access.access_token is None
    for path in ROUTES:
        assert _req(httpd, path)[0] == 200, path
    assert _announce(httpd) is None  # no launch file
    out, err = capsys.readouterr()
    assert "no authentication" in err and "note:" not in err and "token" not in out


def test_token_auth_on_every_route(running):
    httpd = running(auth="token", access_token=TOKEN)
    acc = httpd.access
    port = httpd.server_address[1]
    assert acc.launch_url() == f"http://127.0.0.1:{port}/ui?token={TOKEN}"
    for path in ROUTES:
        status, h, body = _req(httpd, path)
        assert status == 401, path
        assert h["www-authenticate"].startswith("Bearer")
        assert TOKEN not in body.decode()  # never echoed
    # the launch URL: 303 with the session cookie, to the same URL without the token
    status, h, _ = _req(httpd, f"/ui?facility_id=S&token={TOKEN}")
    assert status == 303 and h["location"] == "/ui?facility_id=S"
    cookie = h["set-cookie"]
    assert cookie == f"camber-serve-{port}={acc.session_id}; HttpOnly; SameSite=Strict; Path=/"
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Path=/" in cookie
    sent = {"Cookie": f"camber-serve-{port}={acc.session_id}"}
    for path in ROUTES:
        assert _req(httpd, path, headers=sent)[0] == 200, path
    # a wrong token / cookie / bearer
    assert _req(httpd, "/ui?token=wrong-token-value-xx")[0] == 403
    assert _req(httpd, "/facilities", headers={"Cookie": f"camber-serve-{port}=nope"})[0] == 401
    other_port = {"Cookie": f"camber-serve-{port + 1}={acc.session_id}"}
    assert _req(httpd, "/facilities", headers=other_port)[0] == 401
    # Authorization: Bearer
    bearer = {"Authorization": f"Bearer {TOKEN}"}
    for path in ROUTES:
        assert _req(httpd, path, headers=bearer)[0] == 200, path
    assert _req(httpd, "/facilities", headers={"Authorization": "Bearer nope"})[0] == 403
    assert _req(httpd, "/facilities", headers={"Authorization": f"Basic {TOKEN}"})[0] == 401
    # the token never opens another Host
    assert _req(httpd, f"/ui?token={TOKEN}", host="rebind.example")[0] == 403
    # still GET-only with a valid token
    assert _req(httpd, "/facilities", method="POST", headers=bearer)[0] == 405
    # /health: a bare liveness answer without credentials, the full answer with them
    status, _, body = _req(httpd, "/health")
    assert status == 200 and json.loads(body) == {"ok": True}
    status, _, body = _req(httpd, "/health", headers=bearer)
    assert status == 200 and "facilities" in json.loads(body)
    assert _req(httpd, "/about")[0] == 401


def test_check_request_is_pure_and_redirect_stays_same_origin(running):
    httpd = running(auth="token", access_token=TOKEN)
    acc = httpd.access
    host = {"Host": f"127.0.0.1:{acc.port}"}
    status, _, hdrs = check_request(acc, "GET", "//evil.example/x", {"token": [TOKEN]}, host)
    assert status == 303 and hdrs["Location"] == "/ui"
    assert (
        check_request(acc, "GET", "/facilities", {}, {**host, "authorization": "bearer " + TOKEN})
        is None
    )
    # the generated token is a fresh 43-character secret per run
    other = running(auth="token").access
    assert len(other.access_token) == 43 and other.access_token != other.session_id


def test_announce_writes_private_launch_file_and_warns(running, tmp_path, capsys):
    httpd = running(auth="token", access_token=TOKEN)
    path = _announce(httpd, launch_dir=tmp_path / "run")
    out, err = capsys.readouterr()
    assert httpd.access.launch_url() in out and path in out
    assert path.endswith(f"serve-{httpd.access.port}.url")
    if hasattr(os, "getuid"):
        assert os.stat(path).st_mode & 0o777 == 0o600
    assert open(path).read() == httpd.access.launch_url() + "\n"
    assert "warning" not in err  # loopback + token: nothing to warn about
    # a non-loopback bind without --auth token warns
    lan = running(allowed_hosts=["camber.lan"], host="0.0.0.0")
    assert lan.access.auth == "none"
    assert _announce(lan) is None
    out, err = capsys.readouterr()
    assert "no authentication" in err and "camber.lan (any port)" in out
    star = running(allowed_hosts=["*"], auth="none")
    _announce(star)
    assert "Host check off" in capsys.readouterr().err


# --------------------------------------------------------------------------- cross-site (0.104)


def test_allows_origin_and_cross_site_refusal_values():
    a = _access.HostAllowlist("127.0.0.1", 8080, ["camber.example.org"])
    for ok in (
        "http://127.0.0.1:8080",
        "http://localhost:8080",
        "http://[::1]:8080",
        "https://camber.example.org",  # a TLS proxy's public name (bare name: any port or none)
        "https://camber.example.org:8443",
    ):
        assert a.allows_origin(ok), ok
    for bad in (
        None,
        "",
        "null",  # a sandboxed frame, a file:// page
        "http://evil.example:8080",
        "http://127.0.0.1:9999",  # another local port
        "http://127.0.0.1",
        "ftp://127.0.0.1:8080",
        "http://127.0.0.1:8080/x",
        "http://user@127.0.0.1:8080",
    ):
        assert not a.allows_origin(bad), bad
    assert not a.allows_origin("https://127.0.0.1:8080", schemes=("http",))
    sites = _access.SERVE_FETCH_SITES
    ok = _access.cross_site_refusal
    assert ok(a, {}, fetch_sites=sites) is None  # curl, a script, a health probe
    for site in ("same-origin", "same-site", "none"):
        assert ok(a, {"sec-fetch-site": site}, fetch_sites=sites) is None
    assert ok(a, {"sec-fetch-site": "cross-site"}, fetch_sites=sites) == (
        "sec-fetch-site",
        "cross-site",
    )
    assert ok(a, {"sec-fetch-site": "weird"}, fetch_sites=sites)[0] == "sec-fetch-site"
    assert ok(a, {"origin": "http://evil.example"}, fetch_sites=sites) == (
        "origin",
        "http://evil.example",
    )
    # the lab's stricter default refuses same-site
    assert ok(a, {"sec-fetch-site": "same-site"})[0] == "sec-fetch-site"


def test_cross_site_requests_are_refused(running):
    httpd = running()
    port = httpd.server_address[1]
    for path in ROUTES + ("/health",):
        status, h, body = _req(httpd, path, headers={"Sec-Fetch-Site": "cross-site"})
        assert status == 403, path
        assert h["cache-control"] == "no-store"
        text = body.decode()
        assert "Sec-Fetch-Site: cross-site" in text
        if path == "/ui":
            assert h["content-type"].startswith("text/html")
            assert "default-src 'none'" in h["content-security-policy"]
        else:
            assert "cross-site" in json.loads(body)["error"].lower()
    for origin in ("http://evil.example", f"http://127.0.0.1:{port + 1}", "null"):
        status, _, body = _req(httpd, "/facilities", headers={"Origin": origin})
        assert status == 403, origin
        assert "origin not allowed" in json.loads(body)["error"].lower()
    # a cross-site page that sends both: refused on Sec-Fetch-Site first
    both = {"Sec-Fetch-Site": "cross-site", "Origin": "http://evil.example"}
    assert "Sec-Fetch-Site" in json.loads(_req(httpd, "/points", headers=both)[2])["error"]
    # the Host check still comes first
    status, _, body = _req(httpd, "/facilities", host="rebind.example", headers=both)
    assert status == 403 and "host not allowed" in json.loads(body)["error"].lower()
    # a cross-site POST is 403, not 405 (refused before the method check)
    assert _req(httpd, "/facilities", method="POST", headers=both)[0] == 403


def test_same_origin_browser_and_non_browser_requests_pass(running):
    httpd = running(auth="none")
    port = httpd.server_address[1]
    me = f"http://127.0.0.1:{port}"
    # the trend viewer opened from the address bar, then its own same-origin fetches (a GET
    # fetch() sends no Origin; send one too, as a browser may)
    nav = {"Sec-Fetch-Site": "none", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
    assert _req(httpd, "/ui", headers=nav)[0] == 200
    fetch = {"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"}
    for path in ROUTES:
        assert _req(httpd, path, headers=fetch)[0] == 200, path
        assert _req(httpd, path, headers={**fetch, "Origin": me})[0] == 200, path
    host_l = f"localhost:{port}"
    assert _req(httpd, "/facilities", host=host_l, headers={"Origin": f"http://{host_l}"})[0] == 200
    # a link from a sibling name / another port of this host (same-site navigation)
    assert _req(httpd, "/ui", headers={"Sec-Fetch-Site": "same-site"})[0] == 200
    # neither header: curl, a script
    assert _req(httpd, "/history?facility_id=S&limit=2")[0] == 200


def test_container_and_k8s_health_probes_pass(running, monkeypatch):
    import urllib.request

    # the Dockerfile HEALTHCHECK: urllib against 127.0.0.1:<port>/health, bound to 0.0.0.0 with
    # CAMBER_API_ALLOWED_HOSTS=localhost,127.0.0.1 (urllib sends Host 127.0.0.1:<port>, no Origin)
    monkeypatch.setenv(ALLOWED_HOSTS_ENV, "localhost,127.0.0.1")
    httpd = running(host="0.0.0.0")
    port = httpd.server_address[1]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # no CI proxy
    with opener.open(f"http://127.0.0.1:{port}/health", timeout=5) as r:
        assert r.status == 200
    # deploy/k8s: kubelet's httpGet with the header `Host: localhost` (and its own User-Agent)
    probe = {"User-Agent": "kube-probe/1.30", "Accept": "*/*"}
    assert _req(httpd, "/health", host="localhost", headers=probe)[0] == 200
    # with token auth, the bare liveness answer still needs no secret
    tok = running(auth="token", access_token=TOKEN)
    status, _, body = _req(tok, "/health", headers=probe)
    assert status == 200 and json.loads(body) == {"ok": True}


def test_cross_site_launch_url_gets_no_cookie(running):
    httpd = running(auth="token", access_token=TOKEN)
    status, h, _ = _req(httpd, f"/ui?token={TOKEN}", headers={"Sec-Fetch-Site": "cross-site"})
    assert status == 403 and "set-cookie" not in h
    status, h, _ = _req(httpd, f"/ui?token={TOKEN}", headers={"Sec-Fetch-Site": "none"})
    assert status == 303 and "set-cookie" in h
    # a valid bearer token does not open a cross-site request either
    bearer = {"Authorization": f"Bearer {TOKEN}", "Origin": "http://evil.example"}
    assert _req(httpd, "/facilities", headers=bearer)[0] == 403


# --------------------------------------------------------------------------- the default note


def test_auth_default_note_on_loopback(running, capsys, monkeypatch, tmp_path):
    """0.107 (#221): the 0.104 deprecation notice is now a note that token auth is the default,
    printed with the launch URL; the token is in the terminal and the launch file, as with
    ``--auth token``."""
    from camber.api.server import _AUTH_DEFAULT_NOTICE

    assert "\n" not in _AUTH_DEFAULT_NOTICE
    assert "--auth none" in _AUTH_DEFAULT_NOTICE and "0.107" in _AUTH_DEFAULT_NOTICE
    assert "future" not in _AUTH_DEFAULT_NOTICE
    implicit = running()
    assert implicit.access.auth == "token" and not implicit.access.auth_explicit
    path = _announce(implicit, launch_dir=tmp_path / "run")
    out, err = capsys.readouterr()
    assert err.count(_AUTH_DEFAULT_NOTICE) == 1 and "note:" not in out
    url = implicit.access.launch_url()
    assert url in out and path in out and open(path).read() == url + "\n"
    if hasattr(os, "getuid"):
        assert os.stat(path).st_mode & 0o777 == 0o600
    # the printed URL opens the viewer: 303 + the session cookie
    status, h, _ = _req(implicit, url.split(str(implicit.access.port), 1)[1])
    assert status == 303 and "set-cookie" in h
    # --auth none: the open server, chosen explicitly -- no note
    explicit = running(auth="none")
    assert explicit.access.auth == "none" and explicit.access.auth_explicit
    _announce(explicit)
    assert "note:" not in capsys.readouterr().err
    for path in ROUTES:
        assert _req(explicit, path)[0] == 200, path
    # --auth token: no note
    _announce(running(auth="token", access_token=TOKEN), launch_dir=tmp_path / "run2")
    assert "note:" not in capsys.readouterr().err
    # not loopback: the existing no-authentication warning, not the note
    monkeypatch.setenv(ALLOWED_HOSTS_ENV, "camber.lan")
    _announce(running(host="0.0.0.0"))
    err = capsys.readouterr().err
    assert "no authentication" in err and "note:" not in err


# --------------------------------------------------------------------------- errors (0.107, #216)


def _corrupt_one_part(store):
    for root, _, files in os.walk(store.root):
        for f in files:
            if f.endswith(".parquet"):
                p = os.path.join(root, f)
                with open(p, "wb") as fh:
                    fh.write(b"PAR1 this is not a parquet file PAR1")
                return p
    raise AssertionError("no part file")


def test_bad_query_is_400_store_failure_is_generic_500(tmp_path, capsys):
    from camber.api import BadQuery, ReadAPI, dispatch

    st = _store(tmp_path)
    api = ReadAPI(st)
    for q in ({"start": ["nope"]}, {"limit": ["x"]}, {"limit": ["0"]}, {"max_points": ["1"]}):
        status, body = dispatch(api, "GET", "/history", {"facility_id": ["S"], **q})
        assert status == 400 and body["error"] == "bad request", q
    assert issubclass(BadQuery, ValueError)
    with pytest.raises(BadQuery):
        api.history(facility_id="S", end="not-a-time")
    part = _corrupt_one_part(st)
    status, body = dispatch(api, "GET", "/history", {"facility_id": ["S"], "limit": ["1"]})
    assert status == 500 and body == {"error": "internal error"}
    text = json.dumps(body)
    assert str(tmp_path) not in text and os.path.basename(part) not in text
    err = capsys.readouterr().err
    assert "internal error on GET '/history'" in err and "Traceback" in err

    # a ValueError that is not a BadQuery (pyarrow's ArrowInvalid is one) is a 500, not a 400
    class Boom:
        def history(self, **kw):
            raise ValueError(f"cannot read {tmp_path}/facility_id=S/part-0.parquet")

        def points(self, **kw):
            raise OSError(2, "No such file", str(tmp_path / "x"))

    for path in ("/history", "/points"):
        status, body = dispatch(Boom(), "GET", path, {})
        assert status == 500 and body == {"error": "internal error"}, path
    assert str(tmp_path) in capsys.readouterr().err  # the detail is logged, not sent


def test_corrupt_store_over_http_and_lab_passthrough(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv(ALLOWED_HOSTS_ENV, raising=False)
    st = _store(tmp_path)
    _corrupt_one_part(st)
    httpd = make_server(st, port=0, auth="none")
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        status, h, body = _req(httpd, "/history?facility_id=S&limit=1")
        assert status == 500 and json.loads(body) == {"error": "internal error"}
        assert str(tmp_path).encode() not in body and b"parquet" not in body
        assert _req(httpd, "/history?facility_id=S&start=bad")[0] == 400
    finally:
        httpd.shutdown()
        httpd.server_close()
        t.join(timeout=5)
    assert "internal error" in capsys.readouterr().err
    # camber lab delegates /history to the same dispatch
    from camber.lab import LabApp
    from camber.lab._server import dispatch_lab

    app = LabApp(store=st.root, data_dir=str(tmp_path / "cache"))
    try:
        app.bind(8765)
        hdrs = {"Host": "127.0.0.1:8765", "Authorization": f"Bearer {app.access_token}"}
        status, body, _ = dispatch_lab(
            app, "GET", "/history", {"facility_id": ["S"], "limit": ["1"]}, hdrs, b""
        )
        assert status == 500 and str(tmp_path) not in json.dumps(body)
    finally:
        app.close()


def test_handler_500_is_generic(running, monkeypatch, capsys, tmp_path):
    import camber.api.server as server

    def boom(*a, **k):
        raise RuntimeError(f"failed at {tmp_path}/secret/path")

    httpd = running(auth="none")
    monkeypatch.setattr(server, "check_request", boom)
    status, _, body = _req(httpd, "/facilities")
    assert status == 500 and json.loads(body) == {"error": "internal error"}
    assert str(tmp_path).encode() not in body
    assert "secret/path" in capsys.readouterr().err


# --------------------------------------------------------------------------- startup (0.107, #217)


def test_port_range_and_store_check(tmp_path, capsys, monkeypatch):
    from camber.api.server import _store_problem
    from camber.cli import main

    for argv in (
        ["serve", str(tmp_path), "--port", "99999"],
        ["serve", str(tmp_path), "--port", "-1"],
        ["serve", str(tmp_path), "--port", "http"],
        ["lab", "--port", "-1"],
        ["lab", "--port", "65536"],
    ):
        with pytest.raises(SystemExit) as e:
            main(argv)
        assert e.value.code == 2, argv
        err = capsys.readouterr().err
        assert "--port" in err and "Traceback" not in err and "OverflowError" not in err
    with pytest.raises(ValueError, match="0 to 65535"):
        make_server(_store(tmp_path), port=70000)
    # a missing path, a file, a folder of other things: exit 1, "not a ParquetStore"
    junk = tmp_path / "junk"
    junk.mkdir()
    (junk / "notes.txt").write_text("x")
    (tmp_path / "file").write_text("x")
    for bad in (tmp_path / "does-not-exist", tmp_path / "file", junk):
        assert main(["serve", str(bad), "--auth", "none"]) == 1, bad
        assert "not a ParquetStore" in capsys.readouterr().err
        assert _store_problem(bad) is not None
    assert not (tmp_path / "does-not-exist").exists()  # nothing created
    # a real store, a fresh (empty) store folder and a workspace store are servable
    assert _store_problem(_store(tmp_path).root) is None
    (tmp_path / "empty").mkdir()
    assert _store_problem(tmp_path / "empty") is None
