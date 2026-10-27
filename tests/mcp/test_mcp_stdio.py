"""`camber mcp` over real pipes, the stdio framing, and the launch configuration (#277)."""

import io
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)

import _fake_registry as fake
import pytest
from test_mcp_transcripts import load, normalize, wire

from camber.mcp import LaunchError, McpServer, build_context, launch, serve, serve_stdio
from camber.mcp import _launch as launch_mod

DRIVER = os.path.join(HERE, "_mcp_driver.py")
MODERN_META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientCapabilities": {},
    "io.modelcontextprotocol/clientInfo": {"name": "pipe-test", "version": "1"},
}


def _store(tmp_path, *ids):
    root = tmp_path / "store"
    root.mkdir()
    for fid in ids:
        (root / f"facility_id={fid}").mkdir()
    return str(root)


def _run(args, stdin: bytes, env_extra=None, timeout=60):
    env = dict(os.environ)
    env.pop("CAMBER_TEST_NO_REGISTRY", None)
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, DRIVER, "mcp", *args],
        input=stdin,
        capture_output=True,
        timeout=timeout,
        env=env,
    )


def _session(args, messages, *, replies=None, timeout=60):
    """A real client: write each message and, when it expects a reply (``replies[i]``, by
    default every message with an ``id``), read that reply before the next; then close stdin.

    ``camber mcp`` stops every call still running when stdin closes (#292), so a client reads
    its answers first. Returns ``(returncode, [reply], stderr bytes)``.
    """
    env = dict(os.environ)
    env.pop("CAMBER_TEST_NO_REGISTRY", None)
    proc = subprocess.Popen(
        [sys.executable, DRIVER, "mcp", *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    got = []
    try:
        for i, raw in enumerate(messages):
            proc.stdin.write(raw)
            proc.stdin.flush()
            wants = replies[i] if replies is not None else b'"id"' in raw
            if wants:
                line = proc.stdout.readline()
                assert line.endswith(b"\n"), proc.stderr.read().decode()
                got.append(json.loads(line))
        proc.stdin.close()
        rest = proc.stdout.read()
        err = proc.stderr.read()
        rc = proc.wait(timeout)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert rest == b""
    return rc, got, err


def _lines(stdout: bytes):
    text = stdout.decode("utf-8")
    assert text == "" or text.endswith("\n")
    return [json.loads(line) for line in text.splitlines()]


def _req(id_, method, params=None):
    return {
        "jsonrpc": "2.0",
        "id": id_,
        "method": method,
        "params": {**(params or {}), "_meta": MODERN_META},
    }


# --------------------------------------------------------------------------- over real pipes


@pytest.mark.parametrize("name", ["modern", "legacy"])
def test_the_transcripts_over_pipes(tmp_path, name):
    store = _store(tmp_path, "hq", "annex", "empty")
    steps = load(name)
    rc, got, err = _session(
        ["--store", store, "--allow", "hq", "--allow", "annex", "--allow", "empty"],
        [wire(s) for s in steps],
        replies=[s["expect"] is not None for s in steps],
    )
    assert rc == 0, err.decode()
    expected = [s["expect"] for s in steps if s["expect"] is not None]
    assert [normalize(r) for r in got] == expected
    assert b"end of input" in err  # logs go to stderr


def test_stdout_carries_only_messages_even_when_a_tool_prints(tmp_path):
    store = _store(tmp_path, "hq")
    msgs = [_req(1, "tools/call", {"name": "noisy", "arguments": {}}), _req(2, "tools/list")]
    rc, out, err = _session(
        ["--store", store, "--allow", "hq"], [json.dumps(m).encode() + b"\n" for m in msgs]
    )
    assert rc == 0
    assert [r["id"] for r in out] == [1, 2]
    assert out[0]["result"]["structuredContent"] == {"ok": True}
    assert b"stray print from a capability" in err  # printed in the worker process
    assert b"stray bytes on fd 1" in err


def test_allow_all_active_reads_the_store_at_startup(tmp_path):
    store = _store(tmp_path, "north", "south")
    rc, (resp,), err = _session(
        ["--store", store, "--allow-all-active"],
        [json.dumps(_req(1, "tools/call", {"name": "facilities"})).encode() + b"\n"],
    )
    assert rc == 0, err.decode()
    assert resp["result"]["structuredContent"] == {"facilities": ["north", "south"]}
    assert b"2 facilities allowed" in err


def test_refuses_to_start_without_an_allowlist(tmp_path):
    proc = _run(["--store", _store(tmp_path, "hq")], b"")
    assert proc.returncode == 2
    assert b"--allow" in proc.stderr and proc.stdout == b""


def test_a_missing_registry_is_one_clear_line(tmp_path):
    proc = _run(
        ["--store", _store(tmp_path, "hq"), "--allow", "hq"],
        b"",
        env_extra={"CAMBER_TEST_NO_REGISTRY": "1"},
    )
    assert proc.returncode == 1 and proc.stdout == b""
    assert b"camber.capabilities" in proc.stderr


def test_a_bad_store_is_refused_before_serving(tmp_path):
    proc = _run(["--store", str(tmp_path / "nope"), "--allow", "hq"], b"")
    assert proc.returncode == 2 and proc.stdout == b""  # a launch mistake is a usage error
    assert b"does not exist" in proc.stderr


# --------------------------------------------------------------------------- framing (in process)


def _server(allowed=("hq",)):
    ctx = fake.CallContext(store="store", allowed_facilities=frozenset(allowed))
    return McpServer(fake.default_registry(), ctx, server_version="TEST")


def test_serve_stops_at_end_of_input_and_skips_blank_lines():
    out = io.BytesIO()
    lines = b"\n   \n" + json.dumps(_req(1, "server/discover")).encode() + b"\r\n"
    assert serve(_server(), io.BytesIO(lines), out) == 0
    (resp,) = _lines(out.getvalue())
    assert resp["result"]["supportedVersions"][0] == "2026-07-28"


def test_an_oversized_line_is_refused_and_the_stream_goes_on():
    big = b'{"jsonrpc":"2.0","id":1,"method":"tools/list","pad":"' + b"x" * 1000 + b'"}\n'
    ok = json.dumps(_req(2, "tools/list")).encode() + b"\n"
    out = io.BytesIO()
    assert serve(_server(), io.BytesIO(big + ok), out, max_message_bytes=400) == 0
    first, second = _lines(out.getvalue())
    assert first["error"]["code"] == -32600 and first["id"] == 1  # #308: read from its head
    assert "400 bytes" in first["error"]["message"]
    assert second["id"] == 2 and "tools" in second["result"]


def test_an_oversized_last_line_without_newline_ends_cleanly():
    out = io.BytesIO()
    assert serve(_server(), io.BytesIO(b"y" * 1000), out, max_message_bytes=100) == 0
    (resp,) = _lines(out.getvalue())
    assert resp["error"]["code"] == -32600


def test_a_line_of_exactly_the_cap_is_accepted():
    msg = json.dumps(_req(1, "tools/list")).encode() + b"\n"
    out = io.BytesIO()
    serve(_server(), io.BytesIO(msg), out, max_message_bytes=len(msg))
    assert "result" in _lines(out.getvalue())[0]


def test_invalid_utf8_is_a_parse_error():
    out = io.BytesIO()
    serve(_server(), io.BytesIO(b"\xff\xfe{}\n"), out)
    assert _lines(out.getvalue())[0]["error"]["code"] == -32700


class _Interrupting(io.BytesIO):
    def readline(self, *a):
        raise KeyboardInterrupt


class _ClosedPipe(io.BytesIO):
    def write(self, b):
        raise BrokenPipeError


def test_ctrl_c_exits_without_a_traceback():
    assert serve(_server(), _Interrupting(), io.BytesIO()) == 130


def test_a_closed_client_ends_the_loop():
    lines = json.dumps(_req(1, "tools/list")).encode() + b"\n"
    assert serve(_server(), io.BytesIO(lines), _ClosedPipe()) == 0


def test_serve_stdio_keeps_stray_output_off_the_protocol_stream(monkeypatch, capfd):
    msgs = [_req(1, "tools/call", {"name": "noisy"}), _req(2, "prompts/list")]
    data = b"".join(json.dumps(m).encode() + b"\n" for m in msgs)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(data)))
    before = sys.stdout
    assert serve_stdio(_server()) == 0
    assert sys.stdout is before
    cap = capfd.readouterr()
    assert [r["id"] for r in _lines(cap.out.encode())] == [1, 2]
    assert "stray print from a capability" in cap.err and "stray bytes on fd 1" in cap.err
    print("stdout is restored")
    assert capfd.readouterr().out == "stdout is restored\n"


# --------------------------------------------------------------------------- per-request context


def test_each_call_runs_under_the_launch_context_with_the_client_as_caller():
    fake.CALLS.clear()
    server = _server(allowed=("hq", "annex"))
    server.handle(_req(1, "tools/call", {"name": "facilities"}))
    server.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "Old\nClient!"},
            },
        }
    )
    server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "ids"}})
    (_, _, c1), (_, _, c2) = fake.CALLS
    assert c1.caller == "mcp:pipe-test" and c2.caller == "mcp:OldClient"
    for c in (c1, c2):
        assert c.allowed_facilities == frozenset({"hq", "annex"}) and c.store == "store"
    assert server.context.caller == "local"  # the launch context itself is never changed


def test_initialize_offers_its_own_version_for_an_unknown_one():
    for asked, got in (("2024-11-05", "2025-11-25"), ("2025-06-18", "2025-06-18")):
        resp = _server().handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": asked, "capabilities": {}},
            }
        )
        assert resp["result"]["protocolVersion"] == got
    bad = _server().handle({"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {}})
    assert bad["error"]["code"] == -32602


def test_a_registry_that_breaks_its_contract_is_an_internal_error():
    class Broken(fake.Registry):
        def call(self, name, arguments, ctx):
            raise RuntimeError("/private/path")

    reg = Broken()
    for cap in fake.default_registry().capabilities():
        reg.register(cap)
    server = McpServer(reg, fake.CallContext(store="s", allowed_facilities=frozenset()))
    resp = server.handle(_req(1, "tools/call", {"name": "facilities"}))
    assert resp["error"] == {"code": -32603, "message": "Internal error"}


def test_a_prompt_that_fails_to_render():
    reg = fake.default_registry()

    def bad_render(args):
        raise fake.CapabilityError("invalid_params", "focus must be one of: cooling, heating")

    def worse_render(args):
        return [{"role": "system", "text": "no"}]

    def worst_render(args):
        raise KeyError("x")

    for name, render in (("bad", bad_render), ("worse", worse_render), ("worst", worst_render)):
        reg.register_prompt(fake.Prompt(name, name, name, (), render))
    server = McpServer(reg, fake.CallContext(store="s", allowed_facilities=frozenset()))
    for i, (name, code) in enumerate((("bad", -32602), ("worse", -32603), ("worst", -32603))):
        resp = server.handle(_req(i, "prompts/get", {"name": name}))
        assert resp["error"]["code"] == code, name


def test_odd_messages():
    server = _server()
    assert server.handle(42)["error"]["code"] == -32600
    assert server.handle({"jsonrpc": "2.0", "id": 1})["error"]["code"] == -32600
    assert server.handle({"jsonrpc": "2.0", "id": True, "method": "x"})["error"]["code"] == -32600
    meta_bad = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": []}}
    assert server.handle(meta_bad)["error"]["code"] == -32602
    pv_bad = _req(1, "tools/list")
    pv_bad["params"]["_meta"] = {"io.modelcontextprotocol/protocolVersion": 2026}
    assert server.handle(pv_bad)["error"]["code"] == -32602
    assert server.handle_line("") is None


def test_a_non_serializable_error_detail_is_dropped():
    reg = fake.default_registry()

    def handler(ctx, args):
        raise fake.CapabilityError("timeout", "took too long", data={"obj": object()})

    reg.register(fake.Capability("slow", "Slow", "slow", {"type": "object"}, {}, handler))
    server = McpServer(reg, fake.CallContext(store="s", allowed_facilities=frozenset()))
    resp = server.handle(_req(1, "tools/call", {"name": "slow"}))
    assert resp["result"]["isError"] is True
    assert resp["result"]["_meta"]["camber/error"] == {"code": "timeout"}


def test_an_unhandled_bug_in_dispatch_is_an_internal_error(monkeypatch):
    server = _server()
    monkeypatch.setattr(server, "_modern", lambda *a: 1 / 0)
    assert server.handle(_req(1, "tools/list"))["error"]["code"] == -32603


# --------------------------------------------------------------------------- launch configuration


@pytest.fixture
def fake_capabilities(monkeypatch):
    import camber

    monkeypatch.setitem(sys.modules, "camber.capabilities", fake)
    monkeypatch.setattr(camber, "capabilities", fake, raising=False)
    return fake


def test_build_context(tmp_path, fake_capabilities, caplog):
    store = _store(tmp_path, "hq", "annex")
    cfg = tmp_path / "site.json"
    cfg.write_text(json.dumps({"source": {"kind": "store", "store": "store", "facility_id": "hq"}}))
    ctx = build_context(store, allow=["hq", "ghost"], config=str(cfg), min_trust=0.7)
    assert ctx.allowed_facilities == frozenset({"hq", "ghost"})
    assert ctx.store == os.path.abspath(store) and ctx.config == str(cfg) and ctx.min_trust == 0.7
    assert "ghost" in caplog.text
    ctx = build_context(store, allow_all_active=True)
    assert ctx.allowed_facilities == frozenset({"hq", "annex"})


def test_build_context_follows_a_workspace_to_its_store(tmp_path, fake_capabilities):
    from camber.portfolio import Portfolio

    ws = Portfolio.init(tmp_path / "ws")
    os.mkdir(os.path.join(ws.store_root, "facility_id=hq"))
    ctx = build_context(str(tmp_path / "ws"), allow_all_active=True)
    assert ctx.store == ws.store_root and ctx.allowed_facilities == frozenset({"hq"})


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"allow_all_active": True}, "no active facility"),
        ({"allow": []}, "--allow"),
        ({"allow": [" "]}, "--allow"),
        ({"allow": ["hq"], "config": "missing.json"}, "no such file"),
        ({"allow": ["hq"], "min_trust": 1.5}, "--min-trust"),
    ],
)
def test_build_context_refusals(tmp_path, fake_capabilities, kwargs, message):
    with pytest.raises(LaunchError, match=message):
        build_context(_store(tmp_path), **kwargs)


def test_build_context_refuses_a_directory_that_is_not_a_store(tmp_path, fake_capabilities):
    (tmp_path / "other.txt").write_text("x")
    with pytest.raises(LaunchError, match="camber mcp --store"):
        build_context(str(tmp_path), allow=["hq"])


def test_launch(tmp_path, fake_capabilities, monkeypatch, capsys):
    served = []
    monkeypatch.setattr(
        launch_mod, "serve_stdio", lambda server, **kw: served.append((server, kw)) or 0
    )
    store = _store(tmp_path, "hq")
    assert launch(store, allow=["hq"]) == 0
    server, kw = served[0]
    assert server.context.allowed_facilities == frozenset({"hq"})
    pool = kw["workers"]  # tool calls run in worker processes that build the same registry
    assert (pool.registry_factory, pool.max_concurrent, pool.max_queued) == (
        "_fake_registry:default_registry",
        2,
        8,
    )
    assert launch(store, allow_all_active=True, min_trust=2) == 2
    assert "--min-trust" in capsys.readouterr().err


def test_launch_without_the_registry(tmp_path, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "camber.capabilities", None)
    assert launch(_store(tmp_path, "hq"), allow=["hq"]) == 1
    assert "camber.capabilities" in capsys.readouterr().err


def test_min_trust_is_checked_by_the_parser():
    from camber.cli import _build_parser

    with pytest.raises(SystemExit):
        _build_parser().parse_args(["mcp", "--store", "s", "--allow", "a", "--min-trust", "2"])
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["mcp", "--store", "s", "--allow", "a", "--min-trust", "x"])
    args = _build_parser().parse_args(["mcp", "--store", "s", "--allow", "a", "--allow", "b"])
    assert args.allow == ["a", "b"] and args.min_trust == 0.5 and not args.allow_all_active
