"""0.113 (#343): an error result carries its notices and ``structuredContent``, in MCP and A2A.

An MCP tool error (``isError: true``) now carries ``structuredContent: {"camber": {"facts",
"notices", "error"}}`` from the shared grounding helper, with the notices any result of the call
would carry (the damaged-registry notice); an A2A task that fails or is rejected carries the same
notices in its status message. Every store is synthetic, in a tmp directory.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)

import pandas as pd
import pytest

from camber.a2a import A2AServer, LocalRunner
from camber.capabilities import CallContext, CapabilityError, default_registry
from camber.mcp import RESULT_KEY, McpServer
from camber.mcp._workers import RemoteError, _decode, _outcome
from camber.model.roles import Role
from camber.store import ParquetStore

REG = default_registry()
IDX = pd.date_range("2024-03-01", periods=24, freq="h")
MODERN = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientCapabilities": {},
    "io.modelcontextprotocol/clientInfo": {"name": "t", "version": "1"},
}


@pytest.fixture
def store(tmp_path):
    st = ParquetStore(str(tmp_path / "store"))
    frame = pd.DataFrame({Role.SUPPLY_AIR_TEMP: 55.0, Role.OAT: 60.0}, index=IDX)
    st.write_role_frame(frame, facility_id="hq", equip="AHU-1", equip_class="AHU")
    st.register_facility("hq", name="HQ")
    st.points()
    return st


def _corrupt(store):
    with open(os.path.join(store.root, "_facilities.json"), "w") as fh:
        fh.write("{")


def _ctx(store):
    return CallContext(store=store.root, allowed_facilities=frozenset({"hq"}))


def _tool(server, name, arguments, *, modern=True):
    params = {"name": name, "arguments": arguments}
    if modern:
        params["_meta"] = MODERN
    req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params}
    if not modern:
        server.handle(
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "1"},
                },
            }
        )
    return server.handle(req)["result"]


def test_a_capability_error_carries_the_registry_notice(store):
    _corrupt(store)
    with pytest.raises(CapabilityError) as e:
        REG.call("points", {"facility_id": "hq"}, _ctx(store))
    assert e.value.code == "refused_licence"
    (notice,) = e.value.banners
    assert notice.startswith("The store's facility registry")
    with pytest.raises(CapabilityError) as e:  # every code, not only the licence refusal
        REG.call("points", {"facility_id": 5}, _ctx(store))
    assert e.value.code == "invalid_params" and e.value.banners == [notice]


def test_an_intact_registry_adds_no_notice(store):
    with pytest.raises(CapabilityError) as e:
        REG.call("points", {"facility_id": "elsewhere"}, _ctx(store))
    assert e.value.code == "not_allowed" and e.value.banners == []


@pytest.mark.parametrize("modern", [True, False])
def test_an_mcp_error_result_has_structured_content_with_its_notices(store, modern):
    _corrupt(store)
    out = _tool(McpServer(REG, _ctx(store)), "points", {"facility_id": "hq"}, modern=modern)
    assert out["isError"] is True
    camber = out["structuredContent"][RESULT_KEY]
    assert camber["facts"] == {}
    (notice,) = camber["notices"]
    assert notice.startswith("The store's facility registry")
    assert camber["error"]["code"] == "refused_licence"
    assert camber["error"]["data"] == {"facility_id": "hq", "reason": "licence_unverified"}
    text = out["content"][0]["text"]
    assert text.startswith("NOTICE: The store's facility registry")
    assert "\n\nrefused_licence: " in text
    assert out["_meta"]["camber/banners"] == [notice]
    assert out["_meta"]["camber/error"]["code"] == "refused_licence"
    json.dumps(out, allow_nan=False)


def test_an_mcp_error_without_notices(store):
    out = _tool(McpServer(REG, _ctx(store)), "points", {"facility_id": "elsewhere"})
    assert out["structuredContent"] == {
        RESULT_KEY: {
            "facts": {},
            "notices": [],
            "error": {
                "code": "not_allowed",
                "message": "facility 'elsewhere' is not among the facilities this caller may read",
            },
        }
    }
    assert out["content"][0]["text"].startswith("not_allowed: ")


def test_the_worker_pipe_carries_the_banners(store):
    _corrupt(store)
    outcome = _outcome(REG, "points", {"facility_id": "hq"}, _ctx(store))
    assert outcome[0] == "err" and len(outcome) == 5
    err = _decode(outcome)
    assert isinstance(err, RemoteError) and err.banners == list(outcome[4])
    assert err.banners and err.code == "refused_licence"
    assert RemoteError("busy", "m").banners == []


def _a2a(store):
    reg = REG
    token = "t" * 20
    agent = A2AServer(reg, _ctx(store), LocalRunner(reg), token=token, port=8770)
    headers = {
        "Host": "127.0.0.1:8770",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "A2A-Version": "1.0",
    }
    return agent, headers


def _status_parts(agent, headers, data):
    msg = {"messageId": "m", "role": "ROLE_USER", "parts": [{"data": data}]}
    body = {"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": {"message": msg}}
    task = json.loads(agent.handle("POST", "/a2a", headers, json.dumps(body).encode())[2])
    task = task["result"]["task"]
    parts = {p["metadata"]["camber/part"]: p for p in task["status"]["message"]["parts"]}
    return task, parts


def test_a_rejected_a2a_task_carries_the_registry_notice(store):
    _corrupt(store)
    agent, headers = _a2a(store)
    task, parts = _status_parts(
        agent, headers, {"skill": "findings", "input": {"facility_id": "hq"}}
    )
    assert task["status"]["state"] == "TASK_STATE_REJECTED"
    grounded = parts["grounded-facts"]["data"]
    assert grounded["error"]["code"] == "refused_licence"
    (notice,) = grounded["notices"]
    assert notice.startswith("The store's facility registry")
    assert parts["summary"]["text"].startswith("NOTICE: The store's facility registry")
    # the same notice on a task cancelled here and on a worker's failure
    assert agent._notices() == [notice]


def test_a_failed_a2a_task_carries_its_outcome_notices(store):
    from camber.a2a._tasks import SkillCall, Task, TaskStore

    class Job:
        outcome = RemoteError("timeout", "too slow", {"max_seconds": 1}, ["a trust notice"])

    store_ = TaskStore(None, "v")
    from camber.a2a._protocol import SKILLS

    task = Task(SKILLS[1], {}, {"messageId": "m"}, "c")
    store_._done(task, Job())
    msg = task.status_message
    assert task.state == "TASK_STATE_FAILED"
    assert msg["parts"][0]["text"].startswith("NOTICE: a trust notice\n\nfailed: timeout")
    assert msg["parts"][1]["data"]["notices"] == ["a trust notice"]
    assert SkillCall  # the call shape the pool takes


def test_an_intact_store_rejects_without_notices(store):
    agent, headers = _a2a(store)
    task, parts = _status_parts(
        agent, headers, {"skill": "findings", "input": {"facility_id": "elsewhere"}}
    )
    assert parts["grounded-facts"]["data"]["notices"] == []
    assert agent._notices() == []
