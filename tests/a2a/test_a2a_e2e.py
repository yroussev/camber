"""``camber a2a`` end to end (#280): the real command line on a temporary store of synthetic data,
driven with plain HTTP; the launch configuration and its mistakes; the official ``a2a-sdk`` client
(a dev-only dependency; skipped without it).

Every store here is built in a tmp directory from the RCx fixture's synthetic AHUs; nothing reads a
real store, a user cache or the network.
"""

import hashlib
import io
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = os.path.dirname(HERE)
ROOT = os.path.dirname(TESTS)
sys.path.insert(0, ROOT)
sys.path.insert(0, TESTS)

import _rcx_fixture as fx
import pytest

from camber.a2a import GROUNDED_FACTS_URI, launch
from camber.a2a import _launch as launch_mod
from camber.mcp._launch import LaunchError

TOKEN = "e2e-token-0123456789abcdef"
NC = {
    "dataset_id": "some-nc-set",
    "licence": "CC-BY-NC-4.0",
    "access": "research_only",
    "redistribution": "prohibited",
}


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    """``demo-fac`` (two AHUs with faults), ``annex`` (stored, not allowed) and ``nc-lab``
    (research-only data)."""
    root = tmp_path_factory.mktemp("a2a")
    st = fx.make_store(root, meta=None)
    st.write_role_frame(fx.ahu_frame(seed=3), facility_id="annex", equip="AHU-9", equip_class="AHU")
    st.write_role_frame(
        fx.ahu_frame(seed=4), facility_id="nc-lab", equip="AHU-1", equip_class="AHU"
    )
    st.register_facility("nc-lab", name="NC Lab", dataset=NC)
    st.points()  # a current catalog cache
    return {"root": str(root), "store": st.root}


def _tree(root) -> dict:
    out = {}
    for dirpath, dirs, files in os.walk(root):
        dirs.sort()
        for f in sorted(files):
            path = os.path.join(dirpath, f)
            with open(path, "rb") as fh:
                out[os.path.relpath(path, root)] = hashlib.sha256(fh.read()).hexdigest()
    return out


class Server:
    """``python -m camber a2a`` in a subprocess, on a free port."""

    def __init__(self, args, env_extra=None):
        env = dict(os.environ)
        env.pop(launch_mod.TOKEN_ENV, None)
        env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
        env.update(env_extra or {})
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "camber", "a2a", "--port", "0", *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
        )
        self.lines = []
        self.card_url = None
        for line in self.proc.stdout:
            self.lines.append(line)
            if "agent card" in line:
                self.card_url = line.split()[-1]
            if "Ctrl-C to stop" in line:
                break
        assert self.card_url, self.stop()
        self.base = self.card_url.split("/.well-known")[0]

    def stop(self, sig=signal.SIGTERM):
        if self.proc.poll() is None:
            self.proc.send_signal(sig)
        try:
            out, err = self.proc.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            out, err = self.proc.communicate()
        return self.proc.returncode, "".join(self.lines) + out, err


def _http(method, url, *, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, dict(resp.headers), json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), json.loads(e.read() or b"null")


def _rpc(server, method, params, *, version="1.0", token=TOKEN):
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    if version is not None:
        headers["A2A-Version"] = version
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    return _http("POST", server.base + "/a2a", body=body, headers=headers)


def _send(server, skill, fid, **config):
    msg = {
        "messageId": "m-1",
        "role": "ROLE_USER",
        "parts": [{"data": {"skill": skill, "input": {"facility_id": fid}}}],
    }
    params = {"message": msg, **({"configuration": config} if config else {})}
    status, _, resp = _rpc(server, "SendMessage", params)
    assert status == 200
    return resp


def _parts(obj):
    return {pt["metadata"]["camber/part"]: pt for pt in obj["parts"]}


@pytest.fixture(scope="module")
def server(site):
    before = _tree(site["root"])
    srv = Server(
        ["--store", site["store"], "--allow", fx.FID, "--allow", "nc-lab"],
        {launch_mod.TOKEN_ENV: TOKEN},
    )
    yield srv
    rc, out, err = srv.stop()
    assert rc == 128 + signal.SIGTERM, err
    assert "SIGTERM received" in err
    assert _tree(site["root"]) == before  # no-persist: the store is untouched


def test_the_card_over_http_without_a_token(server):
    status, headers, card = _http("GET", server.card_url)
    assert status == 200
    assert card["supportedInterfaces"][0]["url"] == server.base + "/a2a"
    assert card["capabilities"]["extensions"][0]["uri"] == GROUNDED_FACTS_URI
    assert [s["id"] for s in card["skills"]] == ["rcx-report", "findings"]
    assert headers["Cache-Control"] == "no-cache"


def test_auth_and_version_over_http(server):
    assert _rpc(server, "ListTasks", {}, token="")[0] == 401
    assert _rpc(server, "ListTasks", {}, token="wrong-token-wrong-token")[0] == 403
    status, _, resp = _rpc(server, "ListTasks", {}, version=None)
    assert status == 200 and resp["error"]["code"] == -32009
    host = {"Host": "evil.example", "Authorization": f"Bearer {TOKEN}"}
    assert _http("GET", server.card_url, headers=host)[0] == 403


def test_findings_completes_with_grounded_facts(server):
    task = _send(server, "findings", fx.FID)["result"]["task"]
    assert task["status"]["state"] == "TASK_STATE_COMPLETED", task
    parts = _parts(task["artifacts"][0])
    facts = parts["grounded-facts"]["data"]["facts"]
    summary = parts["summary"]["text"]
    cited = [fid for fid in facts if f"[{fid}]" in summary]
    assert cited  # the summary cites facts the part carries
    assert parts["result"]["data"]["facility_id"] == fx.FID
    assert parts["result"]["data"]["findings"]


def test_rcx_report_as_a_long_task(server):
    task = _send(server, "rcx-report", fx.FID, returnImmediately=True)["result"]["task"]
    assert task["status"]["state"] in ("TASK_STATE_SUBMITTED", "TASK_STATE_WORKING")
    end = time.monotonic() + 120
    while time.monotonic() < end:
        _, _, resp = _rpc(server, "GetTask", {"id": task["id"]})
        if resp["result"]["status"]["state"] == "TASK_STATE_COMPLETED":
            break
        time.sleep(0.2)
    art = resp["result"]["artifacts"][0]
    assert art["name"] == "rcx-report"
    data = _parts(art)["result"]["data"]
    assert data["kpis"]["n_issues"] >= 1
    assert _parts(art)["grounded-facts"]["data"]["facts"]
    _, _, listed = _rpc(server, "ListTasks", {})
    assert task["id"] in [t["id"] for t in listed["result"]["tasks"]]
    _, _, resp = _rpc(server, "CancelTask", {"id": task["id"]})
    assert resp["error"]["code"] == -32002


def test_research_only_and_unallowed_facilities_are_rejected(server):
    for fid, code in (("nc-lab", "refused_licence"), ("annex", "not_allowed")):
        task = _send(server, "findings", fid)["result"]["task"]
        assert task["status"]["state"] == "TASK_STATE_REJECTED"
        err = _parts(task["status"]["message"])["grounded-facts"]["data"]["error"]
        assert err["code"] == code


def test_the_extended_card_leaves_research_only_data_out(server):
    _, _, resp = _rpc(server, "GetExtendedAgentCard", {})
    text = json.dumps(resp["result"])
    assert fx.FID in text and "nc-lab" not in text


def test_the_official_sdk_client_round_trip(server):
    pytest.importorskip("a2a.client")
    import asyncio

    import httpx
    from a2a.client import ClientConfig, ClientFactory
    from a2a.types import (
        CancelTaskRequest,
        GetExtendedAgentCardRequest,
        GetTaskRequest,
        ListTasksRequest,
        Message,
        Part,
        Role,
        SendMessageRequest,
    )
    from google.protobuf import json_format, struct_pb2

    async def run():
        http = httpx.AsyncClient(headers={"Authorization": f"Bearer {TOKEN}"}, timeout=120)
        config = ClientConfig(streaming=False, httpx_client=http)
        client = await ClientFactory(config).create_from_url(server.base)
        value = struct_pb2.Value()
        json_format.ParseDict({"skill": "findings", "input": {"facility_id": fx.FID}}, value)
        msg = Message(message_id="sdk-1", role=Role.ROLE_USER, parts=[Part(data=value)])
        events = [ev async for ev in client.send_message(SendMessageRequest(message=msg))]
        task = events[-1].task
        got = await client.get_task(GetTaskRequest(id=task.id))
        listed = await client.list_tasks(ListTasksRequest())
        try:
            await client.cancel_task(CancelTaskRequest(id=task.id))
            cancel_error = None
        except Exception as exc:  # noqa: BLE001
            cancel_error = type(exc).__name__
        card = await client.get_extended_agent_card(GetExtendedAgentCardRequest())
        await client.close()
        await http.aclose()
        return task, got, listed, cancel_error, card

    task, got, listed, cancel_error, card = asyncio.run(run())
    from a2a.types import TaskState

    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    roles = [pt.metadata["camber/part"] for pt in task.artifacts[0].parts]
    assert roles == ["summary", "result", "grounded-facts"]
    assert got.id == task.id and listed.total_size >= 1
    assert cancel_error == "TaskNotCancelableError"
    assert card.skills[0].examples


# --------------------------------------------------------------------------- launch mistakes


def _cli(args, env_extra=None):
    env = dict(os.environ)
    env.pop(launch_mod.TOKEN_ENV, None)
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, "-m", "camber", "a2a", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


@pytest.mark.parametrize(
    "args, env, words",
    [
        (["--store", "{store}"], None, "--allow"),
        (["--store", "{store}", "--allow", "x", "--port", "99999"], None, "port"),
        (["--store", "{root}/nope", "--allow", "x"], None, "not a ParquetStore"),
        (["--store", "{store}", "--allow", "x", "--host", "0.0.0.0"], None, "--allow-host"),
        (["--store", "{store}", "--allow", "x"], {"CAMBER_A2A_TOKEN": "short"}, "16"),
    ],
)
def test_every_launch_mistake_exits_2(site, args, env, words):
    args = [a.format(store=site["store"], root=site["root"]) for a in args]
    res = _cli(args, env)
    assert res.returncode == 2, res.stderr
    assert words in res.stderr
    assert len(res.stderr.strip().splitlines()) == 1, res.stderr


# --------------------------------------------------------------------------- launch in process


def test_check_bind_and_resolve_token():
    assert launch_mod.check_bind("127.0.0.1", 0, None) == []
    assert launch_mod.check_bind("0.0.0.0", 1, [" a ", ""]) == ["a"]
    for host, port in (("", 1), ("127.0.0.1", -1), ("127.0.0.1", True)):
        with pytest.raises(LaunchError):
            launch_mod.check_bind(host, port, None)
    assert launch_mod.resolve_token({launch_mod.TOKEN_ENV: "k" * 16}) == ("k" * 16, True)
    token, from_env = launch_mod.resolve_token({})
    assert len(token) >= 40 and not from_env
    with pytest.raises(LaunchError):
        launch_mod.resolve_token({launch_mod.TOKEN_ENV: "with a space and more"})


def _in_thread(site, tmp_path, monkeypatch, **kw):
    """Run :func:`launch` on a thread until the test stops it; ``(thread, httpd, out, rc)``."""
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "mpl"))
    ready = threading.Event()
    box = {}
    out = io.StringIO()

    def on_ready(httpd):
        box["httpd"] = httpd
        ready.set()

    def run():
        box["rc"] = launch(
            site["store"],
            allow=[fx.FID, "nc-lab"],
            port=0,
            launch_dir=str(tmp_path / "launch"),
            out=out,
            _on_ready=on_ready,
            **kw,
        )

    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert ready.wait(60), out.getvalue()
    return t, box, out


def test_launch_writes_and_removes_the_launch_file(site, tmp_path, monkeypatch):
    monkeypatch.delenv(launch_mod.TOKEN_ENV, raising=False)
    t, box, out = _in_thread(site, tmp_path, monkeypatch, max_concurrent=1, max_queued=0)
    httpd = box["httpd"]
    files = os.listdir(tmp_path / "launch")
    assert files == [f"a2a-{httpd.server_address[1]}.url"]
    url = (tmp_path / "launch" / files[0]).read_text().strip()
    assert url.startswith("http://127.0.0.1:") and "/a2a?token=" in url
    token = url.split("token=")[1]
    assert token == httpd.agent.token
    assert "readable by you only" in out.getvalue()
    httpd.shutdown()
    t.join(30)
    assert box["rc"] == 0
    assert os.listdir(tmp_path / "launch") == []


def test_launch_with_the_token_from_the_environment(site, tmp_path, monkeypatch):
    monkeypatch.setenv(launch_mod.TOKEN_ENV, TOKEN)
    t, box, out = _in_thread(site, tmp_path, monkeypatch)
    assert box["httpd"].agent.token == TOKEN
    assert "CAMBER_A2A_TOKEN" in out.getvalue()
    assert not (tmp_path / "launch").exists()
    box["httpd"].shutdown()
    t.join(30)
    assert box["rc"] == 0


def test_launch_mistakes_in_process(site, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv(launch_mod.TOKEN_ENV, raising=False)
    assert launch(site["store"], allow=["x"], max_concurrent=0) == 2
    assert launch(site["store"], allow=["x"], max_queued=-1) == 2
    assert launch(site["store"], allow=["x"], host="::", port=0) == 2
    err = capsys.readouterr().err
    assert err.count("error: ") == 3


def test_launch_reports_a_port_in_use(site, tmp_path, monkeypatch, capsys):
    import socket

    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "mpl"))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        rc = launch(site["store"], allow=[fx.FID], port=port, out=io.StringIO())
    assert rc == 2
    assert "cannot bind" in capsys.readouterr().err


def test_announce_on_a_non_loopback_bind_warns(site, capsys):
    from camber.a2a import A2AServer, LocalRunner
    from camber.capabilities import CallContext, default_registry

    reg = default_registry()
    ctx = CallContext(store=site["store"], allowed_facilities={fx.FID})
    agent = A2AServer(
        reg,
        ctx,
        LocalRunner(reg),
        token=TOKEN,
        bind_host="0.0.0.0",
        port=8770,
        allowed_hosts=["*"],
    )
    out = io.StringIO()
    launch_mod._announce(agent, TOKEN, True, out, None)
    err = capsys.readouterr().err
    assert "TLS reverse proxy" in err and "DNS rebinding" in err
    assert "Ctrl-C" in out.getvalue()


def test_the_http_handler(site, tmp_path, monkeypatch):
    import http.client

    monkeypatch.setenv(launch_mod.TOKEN_ENV, TOKEN)
    t, box, _ = _in_thread(site, tmp_path, monkeypatch)
    port = box["httpd"].server_address[1]

    def req(method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        try:
            conn.putrequest(method, path)
            for k, v in (headers or {}).items():
                conn.putheader(k, v)
            conn.endheaders()
            if body:
                conn.send(body)
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()

    auth = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
    status, body = req("GET", "/.well-known/agent-card.json")
    assert status == 200 and json.loads(body)["name"] == "CAMBER"
    assert req("HEAD", "/.well-known/agent-card.json") == (200, b"")
    for method in ("PUT", "DELETE", "PATCH", "OPTIONS"):
        assert req(method, "/a2a", headers=auth)[0] == 405
    rpc = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ListTasks"}).encode()
    ok = {**auth, "A2A-Version": "1.0", "Content-Length": str(len(rpc))}
    status, body = req("POST", "/a2a", rpc, ok)
    assert status == 200 and json.loads(body)["result"]["totalSize"] == 0
    assert req("POST", "/a2a", rpc, auth)[0] == 411  # no Content-Length
    chunked = {**ok, "Transfer-Encoding": "chunked"}
    assert req("POST", "/a2a", None, chunked)[0] == 411
    big = {**auth, "Content-Length": str(70 * 1024)}
    assert req("POST", "/a2a", None, big)[0] == 413

    def boom(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr(box["httpd"].agent, "handle", boom)
    assert req("GET", "/.well-known/agent-card.json")[0] == 500
    box["httpd"].shutdown()
    t.join(30)


@pytest.mark.parametrize(
    "exc, rc", [(KeyboardInterrupt, 130), (launch_mod.Terminated(signal.SIGTERM), 143)]
)
def test_a_stop_while_serving_exits_with_its_status(site, tmp_path, monkeypatch, exc, rc):
    monkeypatch.setenv(launch_mod.TOKEN_ENV, TOKEN)
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "mpl"))

    def stop(httpd):
        raise exc

    assert launch(site["store"], allow=[fx.FID], port=0, out=io.StringIO(), _on_ready=stop) == rc


def test_launch_without_a_store_or_a_launch_folder(site, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv(launch_mod.TOKEN_ENV, raising=False)
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "mpl"))
    assert launch(str(tmp_path / "missing"), allow=["x"]) == 2
    assert "not a ParquetStore" in capsys.readouterr().err
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a folder")
    out = io.StringIO()

    def stop(httpd):
        raise KeyboardInterrupt

    rc = launch(
        site["store"],
        allow=[fx.FID, "nc-lab"],
        port=0,
        out=out,
        launch_dir=str(blocker / "sub"),
        _on_ready=stop,
    )
    assert rc == 130
    assert "launch file not written" in capsys.readouterr().err
    assert "bearer token (keep it private)" in out.getvalue()
