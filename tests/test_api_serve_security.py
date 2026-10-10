"""`camber serve` request checks (0.103, #128): the Host allowlist against DNS rebinding, the
opt-in token auth (the `camber lab` model), and GET-only (405 on every other method)."""

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
    httpd = running()
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
        httpd = running(host="::1")
    except OSError:
        pytest.skip("cannot bind ::1 here")
    port = httpd.server_address[1]
    assert _req(httpd, "/health", host=f"[::1]:{port}", connect="::1")[0] == 200
    assert _req(httpd, "/health", host=f"rebind.example:{port}", connect="::1")[0] == 403
    assert httpd.access.launch_url() == f"http://[::1]:{port}/ui"


def test_allowed_hosts_env_and_proxy_name(running, monkeypatch):
    monkeypatch.setenv(ALLOWED_HOSTS_ENV, "camber.example.org")
    httpd = running()
    assert _req(httpd, "/facilities", host="camber.example.org")[0] == 200
    assert _req(httpd, "/facilities", host="other.example.org")[0] == 403
    httpd2 = running(allowed_hosts=["*"])  # the argument wins over the env var
    assert _req(httpd2, "/facilities", host="whatever.example")[0] == 200


# --------------------------------------------------------------------------- GET-only


def test_every_other_method_is_405(running):
    httpd = running()
    for method in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS"):
        for path in ("/facilities", "/ui", "/history"):
            status, _, body = _req(httpd, path, method=method)
            assert status == 405, (method, path)
            assert json.loads(body)["error"] == "method not allowed"
    status, _, body = _req(httpd, "/facilities", method="HEAD")
    assert status == 405 and body == b""


# --------------------------------------------------------------------------- token auth


def test_no_auth_by_default_on_every_route(running):
    httpd = running()
    assert httpd.access.auth == "none" and httpd.access.access_token is None
    for path in ROUTES:
        assert _req(httpd, path)[0] == 200, path


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
    assert _announce(lan) is None
    out, err = capsys.readouterr()
    assert "no authentication" in err and "camber.lan (any port)" in out
    star = running(allowed_hosts=["*"])
    _announce(star)
    assert "Host check off" in capsys.readouterr().err
