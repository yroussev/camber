"""The MCP server's fact ledger and facts block (#294, part 1), in both protocol revisions.

Every tool result the server answers puts its facts in a model-visible text block and records
them for the session (the process); ``check_citations`` resolves cited ids from there, also when
calls run in worker processes that are killed between them.
"""

import json
import os
import signal
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)

import _ledger_registry as fake
import pytest
from test_mcp_workers import Session, _wait_dead

from camber.capabilities import CallContext, Caps, FactLedger
from camber.mcp import McpServer
from camber.mcp._server import FACTS_HEADER, encode_message

MODERN = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientCapabilities": {},
}


class Client:
    """An in-process client speaking one protocol revision."""

    def __init__(self, server, modern: bool):
        self.server, self.modern, self.n = server, modern, 0
        if not modern:
            init = {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "1"},
            }
            self.send("initialize", init)
            self.server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def send(self, method, params):
        self.n += 1
        if self.modern:
            params = {**params, "_meta": MODERN}
        msg = {"jsonrpc": "2.0", "id": self.n, "method": method, "params": params}
        return json.loads(encode_message(self.server.handle(msg)))["result"]

    def call(self, name, **arguments):
        return self.send("tools/call", {"name": name, "arguments": arguments})


def _server(tmp_path, **caps):
    ctx = CallContext(store=str(tmp_path), allowed_facilities=frozenset(), caps=Caps(**caps))
    return McpServer(fake.default_registry(), ctx, server_version="TEST")


@pytest.mark.parametrize("modern", [True, False], ids=["2026-07-28", "2025-11-25"])
def test_facts_are_shown_to_the_model_and_resolved_without_passing_them(tmp_path, modern):
    server = _server(tmp_path)
    c = Client(server, modern)
    res = c.call("fact", n=2, value=7)
    assert not res["isError"]
    facts = res["_meta"]["camber/facts"]
    texts = [b["text"] for b in res["content"]]
    assert len(texts) == 3  # summary, JSON, facts
    assert texts[2].splitlines() == [FACTS_HEADER] + [f"[{f['id']}] {f['text']}" for f in facts]
    # the model cites what it saw in content, and passes no facts back
    fid = facts[1]["id"]
    out = c.call("check_citations", text=f"Item 2 has value 7 [{fid}].", detail=True)
    data = json.loads(out["content"][1]["text"])
    assert data["grounded"] and data["provenance"] == "issued"
    # an id from another server process does not verify here
    other = Client(_server(tmp_path), modern)
    alien = other.call("fact", value=8)["_meta"]["camber/facts"][0]["id"]
    out = c.call("check_citations", text=f"Item 1 has value 8 [{alien}].")
    data = json.loads(out["content"][1]["text"])
    assert data["verdicts"]["unknown_citation"] == 1
    # a doctored fact passed back is refused as a tool error the model can act on
    doctored = {**facts[0], "value": 70}
    out = c.call(
        "check_citations", text=f"Item 1 has value 70 [{facts[0]['id']}].", facts=[doctored]
    )
    assert out["isError"] and out["_meta"]["camber/error"]["code"] == "invalid_params"
    assert out["_meta"]["camber/error"]["data"] == {"changed": [facts[0]["id"]]}
    # the facts as returned, passed back, are accepted
    out = c.call("check_citations", text=f"Item 1 has value 7 [{facts[0]['id']}].", facts=facts)
    assert json.loads(out["content"][1]["text"])["grounded"]


def test_a_result_without_facts_has_no_facts_block(tmp_path):
    res = Client(_server(tmp_path), True).call("pid")
    assert len(res["content"]) == 2 and res["_meta"]["camber/facts"] == []


def test_the_facts_block_counts_toward_the_reply_cap_and_an_unsent_result_issues_nothing(
    tmp_path,
):
    server = _server(tmp_path, max_bytes=5000)
    c = Client(server, True)
    small = c.call("fact", n=1, pad=1000)
    assert not small["isError"]
    size = len(encode_message({"jsonrpc": "2.0", "id": 1, "result": small}))
    assert size <= 5000
    # 3 facts: about 3.4 KB in _meta alone, under the cap; repeated in content, over it
    big = c.call("fact", n=3, pad=1000)
    assert big["isError"] and big["_meta"]["camber/error"]["code"] == "too_large"
    assert big["_meta"]["camber/error"]["data"]["bytes"] > 5000
    # its facts never reached the client, so they were not issued
    first = small["_meta"]["camber/facts"][0]["id"]
    third = first.replace("F1@", "F3@")
    third = third.rsplit("-", 1)[0] + "-"  # the token differs by call: find it by prefix
    assert not any(k.startswith(third) for k in server.ledger._facts)
    assert server.ledger.lookup(first)[0] == "issued"


def test_the_server_uses_the_ledger_it_is_given(tmp_path):
    led = FactLedger()
    ctx = CallContext(store=str(tmp_path), allowed_facilities=frozenset())
    server = McpServer(fake.default_registry(), ctx, server_version="TEST", ledger=led)
    fid = Client(server, True).call("fact")["_meta"]["camber/facts"][0]["id"]
    assert server.ledger is led and led.lookup(fid)[0] == "issued"


posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX pipes")


@posix_only
def test_the_ledger_survives_killed_and_recycled_workers(tmp_path):
    s = Session(
        tmp_path,
        registry=fake.default_registry(),
        factory="_ledger_registry:default_registry",
        max_concurrent=1,
        max_seconds=1.0,
    )

    def pid():
        s.call(90, "pid")
        return s.reply()[1]["result"]["structuredContent"]["pid"]

    def check(id_, fact):
        s.call(id_, "check_citations", text=f"Item 1 has value 7 [{fact['id']}].")
        r = s.reply(30)[1]
        return r["result"]["structuredContent"]

    try:
        s.call(1, "fact", n=2, value=7)
        _, first = s.reply()
        facts = first["result"]["_meta"]["camber/facts"]
        assert first["result"]["content"][2]["text"].startswith(FACTS_HEADER)
        # killed from outside: its replacement holds nothing, the server still has the facts
        before = pid()
        os.kill(before, signal.SIGKILL)
        assert _wait_dead([before], 10)
        data = check(2, facts[0])
        assert data["grounded"] and data["provenance"] == "issued", data
        # recycled: a call that leaves a handler thread behind (the registry's own time cap)
        # gets its worker replaced
        before = pid()
        s.call(3, "sleep", seconds=3)
        assert s.reply(30)[1]["result"]["_meta"]["camber/error"]["code"] == "timeout"
        data = check(4, facts[0])
        assert data["grounded"] and data["provenance"] == "issued", data
        assert pid() != before
    finally:
        s.close()
        s.pool.close()
