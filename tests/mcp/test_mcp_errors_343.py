"""0.113 (#343): every error carries the notices that apply to its call, in MCP and in A2A.

With a damaged facility registry every result carries the registry's notice; so must every error,
whoever raised it: the registry (a refusal, invalid arguments), the worker pool (busy, timeout, a
lost worker), or the server (internal, a reply over the size cap). An MCP error result carries
them as ``NOTICE:`` lines first in its text and in ``_meta["camber/banners"]`` -- and carries no
``structuredContent`` (see :func:`test_an_mcp_error_result_has_no_structured_content`). An A2A
task that fails, is rejected or is cancelled carries them in its status message's text and
``grounded-facts`` part. Every store is synthetic, in a tmp directory.
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
from camber.capabilities import CallContext, CapabilityError, Caps, default_registry
from camber.datasets import get
from camber.mcp import McpServer, WorkerPool
from camber.mcp._workers import RemoteError, _decode, _outcome, _timeout
from camber.model.roles import Role
from camber.store import ParquetStore

REG = default_registry()
IDX = pd.date_range("2024-03-01", periods=24, freq="h")
NC = "ds-at-30bldg-sensors-b02"  # named for a research-only catalog dataset
MODERN = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientCapabilities": {},
    "io.modelcontextprotocol/clientInfo": {"name": "t", "version": "1"},
}
NOTICE = "The store's facility registry"


@pytest.fixture
def store(tmp_path):
    assert get("at-30bldg-sensors").research_only
    st = ParquetStore(str(tmp_path / "store"))
    for v, fid in enumerate(("hq", NC)):
        frame = pd.DataFrame({Role.SUPPLY_AIR_TEMP: 55.0 + v, Role.OAT: 60.0}, index=IDX)
        st.write_role_frame(frame, facility_id=fid, equip="AHU-1", equip_class="AHU")
    st.register_facility("hq", name="HQ")
    st.register_facility(NC, name="NC", dataset=get("at-30bldg-sensors").provenance())
    st.points()
    return st


def _corrupt(store):
    with open(os.path.join(store.root, "_facilities.json"), "w") as fh:
        fh.write("{")


def _ctx(store, **kw):
    return CallContext(store=store.root, allowed_facilities=frozenset({"hq", NC}), **kw)


def _notice(notices):
    """The registry's notice, first (a result over the cap also carries the result's own)."""
    assert notices and notices[0].startswith(NOTICE), notices
    return notices[0]


# --------------------------------------------------------------------------- the registry


def test_registry_errors_carry_the_notice(store):
    _corrupt(store)
    for args, code in (
        ({"facility_id": "hq"}, "refused_licence"),
        ({"facility_id": NC}, "refused_licence"),
        ({"facility_id": "elsewhere"}, "not_allowed"),
        ({"facility_id": 5}, "invalid_params"),
    ):
        with pytest.raises(CapabilityError) as e:
            REG.call("points", args, _ctx(store))
        assert e.value.code == code
        _notice(e.value.banners)


def test_an_intact_registry_adds_no_notice(store):
    with pytest.raises(CapabilityError) as e:
        REG.call("points", {"facility_id": "elsewhere"}, _ctx(store))
    assert e.value.code == "not_allowed" and e.value.banners == []


def test_the_worker_pipe_carries_the_banners(store):
    _corrupt(store)
    outcome = _outcome(REG, "points", {"facility_id": "hq"}, _ctx(store))
    assert outcome[0] == "err" and len(outcome) == 5
    err = _decode(outcome)
    assert isinstance(err, RemoteError) and err.banners == list(outcome[4])
    _notice(err.banners)
    assert RemoteError("busy", "m").banners == []


# --------------------------------------------------------------------------- MCP


def _req(name, arguments, *, legacy=False):
    params = {"name": name, "arguments": arguments}
    if not legacy:
        params["_meta"] = MODERN
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params}


def _legacy_server(server):
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
    return server


def _tool_error(resp, code):
    out = resp["result"]
    assert out["isError"] is True
    # Never structuredContent on an error: MCP clients check it against the tool's outputSchema
    # even when isError is true (the TypeScript SDK client, 1.32.2, validates whenever it is
    # present), an error object never matches that schema, and the client then throws a schema
    # error that hides this error and its notices (#343).
    assert "structuredContent" not in out
    assert out["_meta"]["camber/error"]["code"] == code
    notice = _notice(out["_meta"]["camber/banners"])
    text = out["content"][0]["text"]
    assert text.startswith(f"NOTICE: {notice}\n") and f"\n\n{code}: " in text
    return out


@pytest.mark.parametrize("legacy", [False, True])
def test_an_mcp_error_result_has_no_structured_content(store, legacy):
    _corrupt(store)
    server = McpServer(REG, _ctx(store))
    if legacy:
        _legacy_server(server)
    resp = server.handle(_req("points", {"facility_id": "hq"}, legacy=legacy))
    _tool_error(resp, "refused_licence")


@pytest.mark.parametrize(
    "args, code",
    [
        ({"facility_id": NC}, "refused_licence"),  # research-only
        ({"facility_id": "elsewhere"}, "not_allowed"),
        ({"facility_id": 5}, "invalid_params"),
    ],
)
def test_mcp_registry_errors_carry_the_notice(store, args, code):
    _corrupt(store)
    _tool_error(McpServer(REG, _ctx(store)).handle(_req("points", args)), code)


@pytest.mark.parametrize(
    "params",
    [
        {"name": 5, "arguments": {}},
        {"name": "points", "arguments": []},
        {"name": "no_such_tool", "arguments": {}},
    ],
)
def test_mcp_calls_refused_before_the_registry_carry_the_notice(store, params):
    _corrupt(store)
    req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {**params, "_meta": MODERN}}
    resp = McpServer(REG, _ctx(store)).handle(req)
    assert resp["error"]["code"] == -32602
    _notice(resp["error"]["data"]["camber/banners"])


def _deferred(store):
    server = McpServer(REG, _ctx(store))
    call = server.handle(_req("points", {"facility_id": "hq"}), defer=True)
    assert not isinstance(call, dict)
    return server, call


def test_mcp_pool_errors_carry_the_notice(store):
    _corrupt(store)
    server, call = _deferred(store)
    pool = WorkerPool("camber.capabilities:default_registry")  # never started: no process
    for outcome, code in (
        (pool.busy_error(), "busy"),
        (_timeout("points", 1.0), "timeout"),
        (WorkerPool._worker_lost(call, None), "unavailable"),
    ):
        _tool_error(server.finish(call, outcome), code)


def test_mcp_internal_errors_carry_the_notice(store):
    _corrupt(store)
    server, call = _deferred(store)
    for outcome in (RemoteError("internal", "the worker failed"), RuntimeError("bug")):
        resp = server.finish(call, outcome)
        assert resp["error"]["code"] == -32603
        _notice(resp["error"]["data"]["camber/banners"])


def test_mcp_internal_error_without_notices_has_no_data(store):
    server, call = _deferred(store)
    assert "data" not in server.finish(call, RuntimeError("bug"))["error"]


def test_mcp_a_reply_over_the_cap_carries_the_notice(store):
    _corrupt(store)  # facilities still answers (count 0), with the notice
    server = McpServer(REG, _ctx(store, caps=Caps(max_bytes=600)))
    _tool_error(server.handle(_req("facilities", {})), "too_large")


def test_mcp_errors_without_a_registry_of_this_shape(store, monkeypatch):
    import camber.capabilities as caps

    monkeypatch.delattr(caps, "_error_notices")
    server, call = _deferred(store)
    out = server.finish(call, _timeout("points", 1.0))["result"]
    assert out["_meta"]["camber/banners"] == []
    assert out["content"][0]["text"].startswith("timeout: ")


# --------------------------------------------------------------------------- A2A


class _Job:
    def __init__(self, call, on_done):
        self.call, self.on_done = call, on_done
        self.state = "queued"
        self.outcome = None


class _Runner:
    """Hands each call an outcome the test chose, as the worker pool would deliver it."""

    def __init__(self, outcome=None):
        self.outcome = outcome

    def submit(self, call, on_done):
        job = _Job(call, on_done)
        if self.outcome is not None:
            job.state, job.outcome = "done", self.outcome
            # delivered on another thread, as the pool does (the store's lock is held here)
            import threading

            threading.Thread(target=on_done, args=(job,)).start()
        return job

    def cancel(self, job):
        job.state = "cancelled"
        return True

    def busy_error(self):
        return WorkerPool("camber.capabilities:default_registry").busy_error()


TOKEN = "t" * 20
HEADERS = {
    "Host": "127.0.0.1:8770",
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
    "A2A-Version": "1.0",
}


def _agent(store, runner):
    return A2AServer(REG, _ctx(store), runner, token=TOKEN, port=8770)


def _rpc(agent, method, params):
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    return json.loads(agent.handle("POST", "/a2a", HEADERS, json.dumps(body).encode())[2])


def _send(agent, fid="hq", **config):
    msg = {
        "messageId": "m",
        "role": "ROLE_USER",
        "parts": [{"data": {"skill": "findings", "input": {"facility_id": fid}}}],
    }
    params = {"message": msg, **({"configuration": config} if config else {})}
    return _rpc(agent, "SendMessage", params)["result"]["task"]


def _a2a_error(task, state, code):
    assert task["status"]["state"] == state, task
    parts = {p["metadata"]["camber/part"]: p for p in task["status"]["message"]["parts"]}
    grounded = parts["grounded-facts"]["data"]
    assert grounded["error"]["code"] == code
    notice = _notice(grounded["notices"])
    assert parts["summary"]["text"].startswith(f"NOTICE: {notice}\n\n")
    return grounded


@pytest.mark.parametrize(
    "fid, code", [("hq", "refused_licence"), (NC, "refused_licence"), ("elsewhere", "not_allowed")]
)
def test_a2a_refusals_carry_the_notice(store, fid, code):
    _corrupt(store)
    _a2a_error(_send(_agent(store, LocalRunner(REG)), fid), "TASK_STATE_REJECTED", code)


def test_a2a_invalid_input_carries_the_notice(store):
    _corrupt(store)
    task = _send(_agent(store, LocalRunner(REG)), 5)
    grounded = _a2a_error(task, "TASK_STATE_REJECTED", "invalid_params")
    assert grounded["error"]["data"]["errors"][0].startswith("/facility_id")


def _past_the_precheck(agent, monkeypatch):
    """The registry refuses every facility while it is damaged, so no task would reach a
    worker; these tests let it through to exercise the pool's own errors."""
    monkeypatch.setattr(agent, "_precheck", lambda cap, arguments: None)
    return agent


def test_a2a_busy_carries_the_notice(store, monkeypatch):
    _corrupt(store)
    agent = _past_the_precheck(_agent(store, LocalRunner(REG, max_running=0)), monkeypatch)
    _a2a_error(_send(agent), "TASK_STATE_REJECTED", "busy")


@pytest.mark.parametrize(
    "outcome, state, code",
    [
        (_timeout("rule_run", 1.0), "TASK_STATE_FAILED", "timeout"),
        (
            RemoteError("unavailable", "lost", {"retryable": True}),
            "TASK_STATE_FAILED",
            "unavailable",
        ),
        (RemoteError("internal", "the worker failed"), "TASK_STATE_FAILED", "internal"),
        (RuntimeError("bug"), "TASK_STATE_FAILED", "internal"),
        (RemoteError("busy", "full"), "TASK_STATE_REJECTED", "busy"),
    ],
)
def test_a2a_pool_errors_carry_the_notice(store, monkeypatch, outcome, state, code):
    _corrupt(store)
    agent = _past_the_precheck(_agent(store, _Runner(outcome)), monkeypatch)
    _a2a_error(_send(agent), state, code)


def test_a2a_a_result_that_cannot_be_sent_carries_the_notice(store, monkeypatch):
    import camber.a2a._tasks as tasks_mod

    class Result:
        banners = ()

    def explode(*a, **k):
        raise TypeError("no JSON")

    _corrupt(store)
    monkeypatch.setattr(tasks_mod, "artifact_for", explode)
    agent = _past_the_precheck(_agent(store, _Runner(Result())), monkeypatch)
    _a2a_error(_send(agent), "TASK_STATE_FAILED", "internal")


def test_a2a_cancelled_tasks_carry_the_notice(store, monkeypatch):
    _corrupt(store)
    agent = _past_the_precheck(_agent(store, _Runner()), monkeypatch)
    task = _send(agent, returnImmediately=True)
    done = _rpc(agent, "CancelTask", {"id": task["id"]})["result"]
    _a2a_error(done, "TASK_STATE_CANCELED", "cancelled")
    other = _send(agent, returnImmediately=True)
    assert agent.tasks.cancel_all() == 1  # the server stopping
    snap = agent.tasks.snapshot(agent.tasks.get(other["id"]))
    _a2a_error(snap, "TASK_STATE_CANCELED", "cancelled")


def test_a2a_an_intact_store_rejects_without_notices(store):
    agent = _agent(store, LocalRunner(REG))
    task = _send(agent, "elsewhere")
    parts = {p["metadata"]["camber/part"]: p for p in task["status"]["message"]["parts"]}
    assert parts["grounded-facts"]["data"]["notices"] == []
    assert parts["summary"]["text"].startswith("rejected: not_allowed")
    assert agent._notices() == []
