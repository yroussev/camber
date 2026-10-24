"""Golden JSON-RPC transcripts of `camber mcp` for both protocol revisions (#277).

``transcripts/modern.json`` speaks 2026-07-28 (per-request ``_meta``, ``server/discover``);
``transcripts/legacy.json`` speaks 2025-11-25 (``initialize``) and ends with a modern request
in the same process. Each step is the message sent (a raw line when ``raw``) and the exact
response expected (``null``: no response). The same transcripts are replayed in-process here and
over pipes against the real command line in ``test_mcp_stdio.py``.

After an intentional protocol change, regenerate the expectations and review the diff::

    python tests/mcp/test_mcp_transcripts.py --update
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)

import _fake_registry as fake
import pytest

from camber.mcp import McpServer
from camber.mcp._server import encode_message

TRANSCRIPTS = os.path.join(HERE, "transcripts")
NAMES = ("modern", "legacy")
VERSION = "TEST"
ALLOWED = frozenset({"hq", "annex", "empty"})


def load(name):
    with open(os.path.join(TRANSCRIPTS, f"{name}.json"), encoding="utf-8") as fh:
        return json.load(fh)


def wire(step) -> bytes:
    """The step's message as the line a client writes."""
    if step["raw"]:
        return step["send"].encode("utf-8") + b"\n"
    return json.dumps(step["send"]).encode("utf-8") + b"\n"


def normalize(response):
    """Pin the server version so the transcripts do not change with every release."""
    if not isinstance(response, dict):
        return response
    result = response.get("result")
    if isinstance(result, dict):
        info = result.get("serverInfo") or (result.get("_meta") or {}).get(
            "io.modelcontextprotocol/serverInfo"
        )
        if isinstance(info, dict) and "version" in info:
            info["version"] = VERSION
    return response


def make_server():
    ctx = fake.CallContext(store="store", allowed_facilities=ALLOWED)
    return McpServer(fake.default_registry(), ctx, server_version=VERSION)


def replay(name):
    """``[(step, response)]`` from one in-process server, through the wire encoding."""
    server = make_server()
    out = []
    for step in load(name):
        response = server.handle_line(wire(step))
        if response is not None:
            response = json.loads(encode_message(response))
        out.append((step, normalize(response)))
    return out


@pytest.mark.parametrize("name", NAMES)
def test_transcript(name):
    for step, got in replay(name):
        assert got == step["expect"], step["note"]


def test_every_result_of_the_modern_transcript_is_complete_and_identified():
    for step, got in replay("modern"):
        if got is not None and "result" in got:
            assert got["result"]["resultType"] == "complete", step["note"]
            info = got["result"]["_meta"]["io.modelcontextprotocol/serverInfo"]
            assert info == {"name": "camber", "title": "CAMBER", "version": VERSION}


def test_list_results_carry_cache_hints_in_the_modern_revision_only():
    modern = {s["note"]: r for s, r in replay("modern")}
    for note in ("discover", "prompts/list"):
        assert modern[note]["result"]["ttlMs"] >= 0
        assert modern[note]["result"]["cacheScope"] in ("public", "private")
    legacy = [r for s, r in replay("legacy") if s["note"].startswith("tools/list")]
    assert "ttlMs" not in legacy[0]["result"] and "resultType" not in legacy[0]["result"]


def test_tools_list_order_is_the_registry_order():
    got = {s["note"]: r for s, r in replay("modern")}
    names = [
        t["name"]
        for t in next(v for k, v in got.items() if k.startswith("tools/list"))["result"]["tools"]
    ]
    assert names == sorted(names) == [c.name for c in fake.default_registry().capabilities()]


def _update():  # pragma: no cover - maintenance entry point
    for name in NAMES:
        steps = load(name)
        for step, (_, got) in zip(steps, replay(name)):
            step["expect"] = got
        with open(os.path.join(TRANSCRIPTS, f"{name}.json"), "w", encoding="utf-8") as fh:
            json.dump(steps, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        print(f"updated {name}.json ({len(steps)} steps)")


if __name__ == "__main__":  # pragma: no cover
    if "--update" in sys.argv:
        _update()
