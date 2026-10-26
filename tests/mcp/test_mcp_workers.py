"""Tool calls in killable worker processes (#292): concurrency, time caps, cancel, busy, exit."""

import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)

import _slow_registry as slow
import pytest

from camber.capabilities import CallContext, Caps, default_registry
from camber.mcp import McpServer, WorkerPool, serve
from camber.mcp._server import encode_reply

META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientCapabilities": {},
}

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals and pipes")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # a zombie is not running; on macOS / Linux ps shows its state as Z
    out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return bool(out.stdout.strip()) and not out.stdout.strip().startswith("Z")


def _wait_dead(pids, within: float = 3.0) -> bool:
    end = time.monotonic() + within
    while time.monotonic() < end:
        if not any(_alive(p) for p in pids):
            return True
        time.sleep(0.05)
    return False


class Session:
    """``serve`` with a worker pool on real pipes, in a thread; replies collected as they come."""

    def __init__(
        self,
        tmp_path,
        *,
        registry=None,
        factory="_slow_registry:default_registry",
        max_seconds=30.0,
        allowed=(),
        read=True,
        **pool_kw,
    ):
        ctx = CallContext(
            store=str(tmp_path),
            allowed_facilities=frozenset(allowed),
            caps=Caps(max_seconds=max_seconds),
        )
        self.server = McpServer(registry or slow.default_registry(), ctx, server_version="TEST")
        self.pool = WorkerPool(factory, **pool_kw)
        r_in, self.w_in = os.pipe()
        self.r_out, w_out = os.pipe()
        self.instream, self.outstream = os.fdopen(r_in, "rb"), os.fdopen(w_out, "wb")
        self.replies: queue.Queue = queue.Queue()
        self.raw: list = []
        self.rc = None
        self.seen_pids: set = set()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()
        if read:
            threading.Thread(target=self._read, daemon=True).start()
        self._wait_warm()

    def _serve(self):
        self.rc = serve(self.server, self.instream, self.outstream, workers=self.pool)
        self.outstream.close()

    def _read(self):
        with os.fdopen(self.r_out, "rb") as f:
            for line in f:
                self.raw.append(line)
                self.replies.put((time.monotonic(), json.loads(line)))

    def _wait_warm(self):
        end = time.monotonic() + 30
        while not self.pool.worker_pids() and time.monotonic() < end:
            time.sleep(0.02)
        self.note_pids()

    def note_pids(self):
        self.seen_pids.update(self.pool.worker_pids())
        return self.pool.worker_pids()

    def send(self, obj):
        os.write(self.w_in, json.dumps(obj).encode() + b"\n")

    def call(self, id_, name, **arguments):
        self.send(
            {
                "jsonrpc": "2.0",
                "id": id_,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments, "_meta": META},
            }
        )

    def request(self, id_, method):
        self.send({"jsonrpc": "2.0", "id": id_, "method": method, "params": {"_meta": META}})

    def cancel(self, id_):
        self.send(
            {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": id_}}
        )

    def reply(self, timeout=20.0):
        return self.replies.get(timeout=timeout)

    def close(self, timeout=10.0) -> float:
        t0 = time.monotonic()
        try:
            os.close(self.w_in)
        except OSError:
            pass
        self.thread.join(timeout)
        return time.monotonic() - t0


@pytest.fixture
def session(tmp_path):
    made = []

    def make(**kw):
        s = Session(tmp_path, **kw)
        made.append(s)
        return s

    yield make
    for s in made:
        s.close()
        assert s.pool.worker_pids() == []
        assert _wait_dead(s.seen_pids), "a worker process outlived its session"


def test_other_messages_are_answered_while_a_call_runs_and_replies_come_out_of_order(session):
    s = session(max_concurrent=2)
    s.call(1, "sleep", seconds=1.5)
    time.sleep(0.2)
    t0 = time.monotonic()
    s.request(2, "server/discover")
    s.request(3, "tools/list")
    s.call(4, "pid")
    order = []
    for _ in range(4):
        at, msg = s.reply()
        order.append(msg["id"])
        if msg["id"] == 2:
            assert at - t0 < 0.5
    assert order[:2] == [2, 3] and order[-1] == 1  # each with its own id
    assert {msg_id for msg_id in order} == {1, 2, 3, 4}
    for line in s.raw:  # one complete JSON line per write
        assert line.endswith(b"\n") and json.loads(line)


def test_a_timed_out_call_is_killed_and_its_worker_replaced(session):
    s = session(max_seconds=1.0, max_concurrent=1)
    (first,) = s.note_pids()
    s.call(1, "spin", seconds=60)
    _, msg = s.reply()
    res = msg["result"]
    assert res["isError"] and res["_meta"]["camber/error"]["code"] == "timeout"
    assert res["content"][0]["text"] == "timeout: 'spin' ran longer than 1 s and was abandoned"
    assert _wait_dead([first], 2.0)  # the CPU loop stopped with its process
    s.call(2, "pid")
    _, msg = s.reply()
    pid = msg["result"]["structuredContent"]["pid"]
    assert pid != first and s.note_pids() == [pid]


def test_a_worker_that_cannot_answer_is_killed_at_the_cap_plus_grace(session):
    s = session(max_seconds=0.5, grace_seconds=0.5, max_concurrent=1)
    (first,) = s.note_pids()
    t0 = time.monotonic()
    s.call(1, "freeze")
    at, msg = s.reply()
    assert 0.9 < at - t0 < 5
    assert msg["result"]["_meta"]["camber/error"]["code"] == "timeout"
    assert _wait_dead([first], 2.0)


def test_repeated_timeouts_never_leave_more_than_n_workers(session):
    s = session(max_seconds=0.5, max_concurrent=2, max_queued=8)
    for i in range(6):
        s.call(i, "spin", seconds=60)
    codes = [s.reply(30)[1]["result"]["_meta"]["camber/error"]["code"] for _ in range(6)]
    assert codes == ["timeout"] * 6
    time.sleep(1.0)
    alive = [p for p in s.note_pids() if _alive(p)]
    assert len(alive) <= 2
    assert _wait_dead(s.seen_pids - set(alive), 3.0)


def test_a_cancelled_call_stops_and_gets_no_reply(session):
    s = session(max_concurrent=1)
    (first,) = s.note_pids()
    s.call(1, "spin", seconds=60)
    time.sleep(0.5)
    t0 = time.monotonic()
    s.cancel(1)
    assert _wait_dead([first], 2.0)
    assert time.monotonic() - t0 < 2.0
    s.request(2, "server/discover")
    _, msg = s.reply()
    assert msg["id"] == 2  # nothing for id 1
    s.call(3, "pid")
    _, msg = s.reply()
    assert msg["id"] == 3 and msg["result"]["structuredContent"]["pid"] != first
    with pytest.raises(queue.Empty):
        s.reply(0.3)


def test_a_cancelled_queued_call_never_runs(session):
    s = session(max_concurrent=1)
    s.call(1, "sleep", seconds=0.8)
    s.call(2, "sleep", seconds=0.1)
    s.call(3, "pid")
    s.cancel(2)
    ids = [s.reply()[1]["id"] for _ in range(2)]
    assert ids == [1, 3]
    with pytest.raises(queue.Empty):
        s.reply(0.5)


def test_a_call_beyond_the_queue_is_busy_at_once(session):
    s = session(max_concurrent=1, max_queued=1)
    s.call(1, "sleep", seconds=1.0)
    s.call(2, "sleep", seconds=0.1)
    t0 = time.monotonic()
    s.call(3, "pid")
    at, msg = s.reply()
    assert msg["id"] == 3 and at - t0 < 0.5
    res = msg["result"]
    assert res["isError"] and res["content"][0]["text"].startswith("busy: ")
    assert res["_meta"]["camber/error"] == {
        "code": "busy",
        "data": {"retryable": True, "max_concurrent": 1, "max_queued": 1},
    }
    assert sorted(s.reply()[1]["id"] for _ in range(2)) == [1, 2]


def test_end_of_input_mid_call_kills_the_workers_and_returns_at_once(session):
    s = session(max_concurrent=2)
    s.call(1, "spin", seconds=60)
    s.call(2, "sleep", seconds=60)
    time.sleep(0.8)
    pids = s.note_pids()
    assert len(pids) == 2
    took = s.close()
    assert took < 2.0 and s.rc == 0
    assert _wait_dead(pids, 1.0)
    with pytest.raises(queue.Empty):
        s.reply(0.2)


def test_a_closed_output_stream_mid_call_ends_the_session(session):
    s = session(max_concurrent=1, read=False)
    s.call(1, "spin", seconds=60)
    time.sleep(0.5)
    pids = s.note_pids()
    t0 = time.monotonic()
    os.close(s.r_out)  # the client closes its end of our stdout, stdin stays open
    s.thread.join(5)
    assert not s.thread.is_alive() and s.rc == 0
    assert time.monotonic() - t0 < 2.0
    assert _wait_dead(pids, 1.0)


def test_results_are_the_same_bytes_as_in_process(tmp_path):
    """The real registry through a worker answers byte for byte what it answers in-process."""
    (tmp_path / "facility_id=hq").mkdir()
    calls = [
        ("facilities", {}),
        ("rule_params", {"rules": ["leaking_valve"]}),
        ("points", {"facility_id": "hq"}),
        ("points", {"facility_id": "elsewhere"}),
        ("points", {"facility_id": 7}),
    ]
    ctx = CallContext(store=str(tmp_path), allowed_facilities=frozenset({"hq"}))
    local = McpServer(default_registry(), ctx, server_version="TEST")
    expected = []
    for i, (name, args) in enumerate(calls):
        msg = {
            "jsonrpc": "2.0",
            "id": i,
            "method": "tools/call",
            "params": {"name": name, "arguments": args, "_meta": META},
        }
        expected.append(encode_reply(local.handle(msg)))
    s = Session(
        tmp_path,
        registry=default_registry(),
        factory="camber.capabilities:default_registry",
        allowed=("hq",),
        max_concurrent=1,
    )
    try:
        got = []
        for i, (name, args) in enumerate(calls):
            s.call(i, name, **args)
            s.reply(60)
            got.append(s.raw[-1])
        assert got == expected
    finally:
        s.close()


def test_a_worker_exits_when_its_server_dies(tmp_path):
    script = tmp_path / "server.py"
    script.write_text(
        "import os, sys, time\n"
        f"sys.path[:0] = {[os.path.dirname(os.path.dirname(HERE)), HERE]!r}\n"
        "from camber.mcp import WorkerPool\n"
        "pool = WorkerPool('_slow_registry:default_registry', max_concurrent=1)\n"
        "pool.start()\n"
        "while not pool.worker_pids(): time.sleep(0.02)\n"
        "print(pool.worker_pids()[0], flush=True)\n"
        "time.sleep(60)\n"
    )
    proc = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE)
    try:
        worker = int(proc.stdout.readline())
        assert _alive(worker)
        proc.send_signal(signal.SIGKILL)
        proc.wait(5)
        assert _wait_dead([worker], 3.0)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_pool_arguments_are_checked():
    with pytest.raises(ValueError):
        WorkerPool("x:y", max_concurrent=0)
    with pytest.raises(ValueError):
        WorkerPool("x:y", max_queued=-1)


def test_the_cli_options(tmp_path):
    from camber.cli import _build_parser

    args = _build_parser().parse_args(["mcp", "--store", "s", "--allow", "a"])
    assert (args.max_concurrent, args.max_queued) == (2, 8)
    args = _build_parser().parse_args(
        ["mcp", "--store", "s", "--allow", "a", "--max-concurrent", "4", "--max-queued", "0"]
    )
    assert (args.max_concurrent, args.max_queued) == (4, 0)
    for bad in (["--max-concurrent", "0"], ["--max-queued", "-1"], ["--max-concurrent", "x"]):
        with pytest.raises(SystemExit):
            _build_parser().parse_args(["mcp", "--store", "s", "--allow", "a", *bad])


def test_camber_mcp_exits_cleanly_when_the_client_closes_stdout_only(tmp_path):
    """stdin stays open: the reading thread is still blocked in a read when the server exits."""
    (tmp_path / "facility_id=hq").mkdir()
    proc = subprocess.Popen(
        [sys.executable, os.path.join(HERE, "_mcp_driver.py"), "mcp", "--store", str(tmp_path)]
        + ["--allow", "hq"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        time.sleep(1.0)
        proc.stdout.close()
        rc = proc.wait(10)
        err = proc.stderr.read()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert rc == 0, err.decode()
    assert b"Fatal Python error" not in err and b"closed the output stream" in err
