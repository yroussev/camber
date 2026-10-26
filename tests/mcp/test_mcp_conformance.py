"""JSON-RPC conformance and resilience of `camber mcp` (#289, #300).

A lone UTF-16 surrogate escape anywhere in a request, a reply that cannot be serialised or
written, duplicate keys, an integer too long to convert, a second ``initialize``: each is answered
with a JSON-RPC error (``"id": null`` when the id cannot be read) and the server keeps serving.
"""

import io
import json
import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)

import _fake_registry as fake
import pytest
from test_mcp_stdio import _lines, _req, _run, _server, _store

from camber.mcp import McpServer, serve
from camber.mcp._server import encode_message, encode_reply

META = (
    '"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28",'
    '"io.modelcontextprotocol/clientCapabilities":{}}'
)
PROBE = '{"jsonrpc":"2.0","id":"probe","method":"server/discover","params":{' + META + "}}"
INIT = (
    '{"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":"2025-11-25",'
    '"capabilities":{},"clientInfo":{"name":"t","version":"1"}}}'
)

# (case, line, the id the error should echo); every string escape below is a lone surrogate
SURROGATES = [
    (
        "id",
        '{"jsonrpc":"2.0","id":"\\ud800","method":"server/discover","params":{' + META + "}}",
        None,
    ),
    ("method", '{"jsonrpc":"2.0","id":1,"method":"x\\udfff","params":{' + META + "}}", 1),
    (
        "tool name",
        '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"a\\udc00b",'
        '"arguments":{},' + META + "}}",
        2,
    ),
    (
        "prompt argument",
        '{"jsonrpc":"2.0","id":3,"method":"prompts/get","params":{"name":"triage",'
        '"arguments":{"facility":"a\\ud83db"},' + META + "}}",
        3,
    ),
    (
        "tool argument",
        '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"rows",'
        '"arguments":{"facility":"\\udfff"},' + META + "}}",
        4,
    ),
    (
        "object key",
        '{"jsonrpc":"2.0","id":5,"method":"tools/list","\\ud800":1,"params":{' + META + "}}",
        5,
    ),
]


def _serve(*lines, server=None, **kw):
    out = io.BytesIO()
    data = b"".join(line.encode("utf-8") + b"\n" for line in lines)
    assert serve(server or _server(), io.BytesIO(data), out, **kw) == 0
    raw = out.getvalue()
    raw.decode("utf-8")  # strict: every reply is valid UTF-8
    return _lines(raw)


@pytest.mark.parametrize("case,line,id_", SURROGATES, ids=[s[0] for s in SURROGATES])
def test_a_lone_surrogate_is_refused_and_the_server_goes_on(case, line, id_):
    err, probe = _serve(line, PROBE)
    assert err == {
        "jsonrpc": "2.0",
        "id": id_,
        "error": {
            "code": -32600,
            "message": "Invalid Request: a string holds a lone UTF-16 surrogate",
        },
    }
    assert probe["id"] == "probe" and "result" in probe


def test_a_lone_surrogate_in_a_legacy_session():
    init, err, probe = _serve(INIT, '{"jsonrpc":"2.0","id":"\\udc80","method":"ping"}', PROBE)
    assert init["result"]["protocolVersion"] == "2025-11-25"
    assert err["id"] is None and err["error"]["code"] == -32600
    assert probe["id"] == "probe"


def test_a_notification_with_a_lone_surrogate_is_dropped():
    note = '{"jsonrpc":"2.0","method":"notifications/cancelled","params":{"reason":"\\ud800"}}'
    (probe,) = _serve(note, PROBE)
    assert probe["id"] == "probe"


def test_a_lone_surrogate_over_real_pipes(tmp_path):
    store = _store(tmp_path, "hq")
    stdin = "".join(line + "\n" for _, line, _ in SURROGATES).encode() + PROBE.encode() + b"\n"
    proc = _run(["--store", store, "--allow", "hq"], stdin)
    assert proc.returncode == 0, proc.stderr.decode()
    replies = _lines(proc.stdout)
    assert [r["id"] for r in replies] == [s[2] for s in SURROGATES] + ["probe"]
    assert b"surrogates not allowed" not in proc.stderr


def test_a_lone_surrogate_in_a_reply_is_written_as_its_escape():
    raw = encode_message({"jsonrpc": "2.0", "id": 1, "result": {"text": "a\ud800b", "k\udfff": 1}})
    assert raw.decode("ascii").count("\\u") == 2
    assert json.loads(raw) == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"text": "a\ud800b", "k\udfff": 1},
    }
    paired = encode_message({"text": "\U0001f600"})  # a real non-BMP character stays UTF-8
    assert paired == '{"text":"\U0001f600"}\n'.encode()


def test_a_reply_that_cannot_be_serialised_is_an_internal_error(caplog):
    reg = fake.default_registry()
    reg.register(
        fake.Capability(
            "odd",
            "Odd",
            "a result JSON cannot hold",
            {"type": "object"},
            {},
            lambda ctx, args: fake.CapabilityResult(data={}, text="t", facts=[{"id": {1, 2}}]),
        )
    )
    server = McpServer(reg, fake.CallContext(store="s", allowed_facilities=frozenset()))
    call = json.dumps(_req(7, "tools/call", {"name": "odd"}))
    with caplog.at_level(logging.ERROR, logger="camber.mcp"):
        err, probe = _serve(call, PROBE, server=server)
    assert err == {
        "jsonrpc": "2.0",
        "id": 7,
        "error": {"code": -32603, "message": "Internal error"},
    }
    assert probe["id"] == "probe"
    assert "could not serialise" in caplog.text


def test_encode_reply_never_raises():
    assert json.loads(encode_reply({"jsonrpc": "2.0", "id": 1, "result": object()}))["id"] == 1
    last = json.loads(encode_reply({"jsonrpc": "2.0", "id": object(), "result": object()}))
    assert last == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32603, "message": "Internal error"},
    }


class _FlakyOut(io.BytesIO):
    """Fails the first ``failures`` writes with an OSError that is not a closed pipe."""

    def __init__(self, failures):
        super().__init__()
        self.failures = failures

    def write(self, b):
        if self.failures:
            self.failures -= 1
            raise OSError(5, "I/O error")
        return super().write(b)


@pytest.mark.parametrize("failures,first", [(1, -32603), (2, None)])
def test_a_failed_write_is_logged_and_the_server_goes_on(caplog, failures, first):
    out = _FlakyOut(failures)
    data = (json.dumps(_req(1, "tools/list")) + "\n" + PROBE + "\n").encode()
    with caplog.at_level(logging.ERROR, logger="camber.mcp"):
        assert serve(_server(), io.BytesIO(data), out) == 0
    replies = _lines(out.getvalue())
    if first is None:  # the reply and its error both failed: lost, but the session goes on
        assert [r["id"] for r in replies] == ["probe"]
        assert "is lost" in caplog.text
    else:  # the reply failed: the same id is answered with an internal error
        assert [r["id"] for r in replies] == [1, "probe"]
        assert replies[0]["error"]["code"] == first
    assert "could not write a reply" in caplog.text


def test_a_bug_in_handle_line_is_an_internal_error(monkeypatch):
    server = _server()
    calls = []

    def broken(line):
        calls.append(line)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return McpServer.handle_line(server, line)

    monkeypatch.setattr(server, "handle_line", broken)
    err, probe = _serve("{}", PROBE, server=server)
    assert err == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32603, "message": "Internal error"},
    }
    assert probe["id"] == "probe"


# --------------------------------------------------------------------------- "id": null (#300)


@pytest.mark.parametrize(
    "line,code",
    [
        ('{"jsonrpc":"2.0","id":1,"method":"server/disc', -32700),
        ("[]", -32600),
        ('[{"jsonrpc":"2.0","id":1,"method":"ping"}]', -32600),
        ("42", -32600),
        ('{"jsonrpc":"2.0","id":null,"method":"ping"}', -32600),
        ('{"jsonrpc":"2.0","id":1.5,"method":"ping"}', -32600),
        ('{"jsonrpc":"2.0","id":true,"method":"ping"}', -32600),
    ],
)
def test_an_error_whose_id_cannot_be_read_has_id_null(line, code):
    (err,) = _serve(line)
    assert list(err) == ["jsonrpc", "id", "error"]
    assert err["id"] is None and err["error"]["code"] == code


def test_an_oversized_line_has_id_null():
    (err,) = _serve('{"pad":"' + "x" * 500 + '"}', max_message_bytes=100)
    assert err["id"] is None and err["error"]["code"] == -32600


# --------------------------------------------------------------------------- duplicate keys


@pytest.mark.parametrize(
    "line,key",
    [
        (
            '{"jsonrpc":"2.0","id":3,"id":4,"method":"server/discover","params":{' + META + "}}",
            "id",
        ),
        (
            '{"jsonrpc":"2.0","id":3,"method":"tools/call","method":"server/discover","params":{'
            + META
            + "}}",
            "method",
        ),
        (
            '{"jsonrpc":"2.0","id":5,"method":"server/discover","params":{"_meta":{'
            '"io.modelcontextprotocol/protocolVersion":"1999-01-01",'
            '"io.modelcontextprotocol/protocolVersion":"2026-07-28",'
            '"io.modelcontextprotocol/clientCapabilities":{}}}}',
            "io.modelcontextprotocol/protocolVersion",
        ),
        (
            '{"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"rows",'
            '"arguments":{"facility":"hq","facility":"annex"},' + META + "}}",
            "facility",
        ),
    ],
)
def test_duplicate_keys_are_refused(line, key):
    fake.CALLS.clear()
    err, probe = _serve(line, PROBE)
    assert err["id"] is None and err["error"]["code"] == -32600
    assert err["error"]["message"] == f'Invalid Request: duplicate key "{key}"'
    assert probe["id"] == "probe" and not fake.CALLS


def test_a_long_duplicate_key_is_shortened_in_the_message():
    k = "k" * 500
    (err,) = _serve('{"' + k + '":1,"' + k + '":2}')
    assert len(err["error"]["message"]) < 120


# --------------------------------------------------------------------------- integers


def test_an_integer_too_long_to_convert_is_not_called_invalid_json():
    line = (
        '{"jsonrpc":"2.0","id":'
        + "9" * 5000
        + ',"method":"server/discover","params":{'
        + META
        + "}}"
    )
    err, probe = _serve(line, PROBE)
    assert err["id"] is None and err["error"]["code"] == -32600
    assert "too large" in err["error"]["message"]
    assert probe["id"] == "probe"


def test_a_400_digit_integer_id_is_echoed():
    big = 10**400
    (resp,) = _serve(
        '{"jsonrpc":"2.0","id":' + str(big) + ',"method":"server/discover","params":{' + META + "}}"
    )
    assert resp["id"] == big and "result" in resp


# --------------------------------------------------------------------------- initialize


def _init(params):
    return {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": params}


def test_a_second_initialize_is_refused_and_the_session_keeps_its_version():
    server = _server()
    first = server.handle(_init({"protocolVersion": "2025-11-25", "capabilities": {}}))
    assert first["result"]["protocolVersion"] == "2025-11-25"
    again = server.handle(_init({"protocolVersion": "2025-06-18", "capabilities": {}}))
    assert again["id"] == 1 and again["error"]["code"] == -32600
    assert "already initialized" in again["error"]["message"]
    assert server.legacy_version == "2025-11-25"
    ping = server.handle({"jsonrpc": "2.0", "id": 2, "method": "ping"})
    assert ping["result"] == {}


def test_a_refused_initialize_can_be_retried():
    server = _server()
    assert server.handle(_init({"protocolVersion": "2025-11-25"}))["error"]["code"] == -32602
    ok = server.handle(_init({"protocolVersion": "2025-11-25", "capabilities": {}}))
    assert ok["result"]["protocolVersion"] == "2025-11-25"


@pytest.mark.parametrize(
    "extra,word",
    [
        ({"capabilities": "everything"}, "capabilities"),
        ({"capabilities": [1, 2]}, "capabilities"),
        ({"capabilities": None}, "capabilities"),
        ({}, "capabilities"),
        ({"capabilities": {}, "clientInfo": "client"}, "clientInfo"),
        ({"capabilities": {}, "clientInfo": [1]}, "clientInfo"),
    ],
)
def test_initialize_needs_object_capabilities_and_client_info(extra, word):
    resp = _server().handle(_init({"protocolVersion": "2025-11-25", **extra}))
    assert resp["error"]["code"] == -32602 and word in resp["error"]["message"]


def test_the_stateless_path_needs_an_object_client_info_too():
    req = _req(1, "tools/list")
    req["params"]["_meta"] = {
        **req["params"]["_meta"],
        "io.modelcontextprotocol/clientInfo": "client",
    }
    resp = _server().handle(req)
    assert resp["error"]["code"] == -32602 and "clientInfo" in resp["error"]["message"]
