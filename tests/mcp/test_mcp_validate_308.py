"""0.111 (#308): ``camber mcp`` checks each ``--config`` once and serves that snapshot.

- the calls read the run configs as loaded and checked at startup: an edit to the file while the
  server runs (another rule set, a syntax error, the file deleted) changes no answer and no fact
  id, and never brings back the "No run config names this facility" notice;
- the startup check refuses a NaN or out-of-range rule parameter, a duplicate JSON key, a bad
  ``report.rcx.references`` role and a ventilation zones file that cannot be read, each with one
  line naming the ``--config`` and exit 2;
- a message line over the size cap is refused with its own id when the line's first bytes
  name one, so the client's call ends at once instead of at its timeout.
"""

import io
import json
import os
import pickle
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import _fake_registry as fake  # noqa: E402
import _rcx_fixture as fx  # noqa: E402
import pytest  # noqa: E402

from camber.capabilities import default_registry  # noqa: E402
from camber.mcp import LaunchError, McpServer, build_context, serve  # noqa: E402
from camber.mcp._stdio import head_id  # noqa: E402

FID = fx.FID
REG = default_registry()
DRIVER = os.path.join(HERE, "_mcp_driver.py")


@pytest.fixture()
def store(tmp_path):
    return fx.make_store(tmp_path, meta=None)


def _good(rules=("outdoor_air_fraction",), **over):
    cfg = {
        "source": {"kind": "store", "store": "store", "facility_id": FID},
        "equipment": [{"class": "AHU"}],
        "rules": list(rules),
    }
    cfg.update(over)
    return cfg


def _file(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content if isinstance(content, str) else json.dumps(content))
    return str(p)


# --------------------------------------------------------------------------- the snapshot


@pytest.mark.parametrize("edit", ["other-rules", "trailing-comma", "deleted"])
def test_an_edit_after_startup_changes_nothing(store, tmp_path, edit):
    path = _file(tmp_path, "site.json", _good())
    ctx = build_context(store.root, allow=[FID], config=path)
    args = {"facility_id": FID}
    before = REG.call("rule_run", args, ctx)
    if edit == "other-rules":
        _file(tmp_path, "site.json", _good(rules=("supply_air_reset",)))
    elif edit == "trailing-comma":
        _file(tmp_path, "site.json", json.dumps(_good()) + ",")
    else:
        os.remove(path)
    after = REG.call("rule_run", args, ctx)
    assert after.text == before.text and after.data == before.data
    assert [f["id"] for f in after.facts] == [f["id"] for f in before.facts]
    assert not any("No run config" in b for b in after.banners)
    # the worker processes get the same snapshot with the pickled context
    again = REG.call("rule_run", args, pickle.loads(pickle.dumps(ctx)))
    assert again.text == before.text


def test_a_library_context_without_a_snapshot_still_reads_the_file(store, tmp_path):
    from camber.capabilities import CallContext

    path = _file(tmp_path, "site.json", _good())
    ctx = CallContext(store=store.root, allowed_facilities=frozenset({FID}), config=path)
    assert ctx.config_snapshot == ()
    res = REG.call("rule_run", {"facility_id": FID}, ctx)
    assert "outdoor_air_fraction" in json.dumps(res.data)


def test_the_snapshot_holds_each_checked_config(store, tmp_path):
    path = _file(tmp_path, "site.json", _good())
    ctx = build_context(store.root, allow=[FID], config=path)
    assert ctx.config == path
    assert [p for p, _c in ctx.config_snapshot] == [path]
    assert ctx.config_snapshot[0][1]["rules"] == ["outdoor_air_fraction"]


# --------------------------------------------------------------------------- startup checks


def _with_params(params):
    return _good(rules=[{"name": "outdoor_air_fraction", "params": params}])


_REFUSED = [
    ("nan.json", json.dumps(_with_params({"min_oa_pct": float("nan")})), "not a finite number"),
    ("range.json", _with_params({"min_oa_pct": 500}), "outside its range"),
    (
        "dup.json",
        '{"source": {"kind": "store", "store": "store", "facility_id": "elsewhere", '
        f'"facility_id": "{FID}"}}, "rules": []}}',
        "duplicate key 'facility_id'",
    ),
    (
        "ref.json",
        {
            **_good(),
            "report": {
                "rcx": {"references": {"DemoAHU": {"outdoor_air_temp": {"equip": "DemoAHU2"}}}}
            },
        },
        "is not a CAMBER role",
    ),
    ("vent.json", _good(ventilation={"zones": "zones-missing.csv"}), "ventilation.zones"),
]


@pytest.mark.parametrize("fname, content, message", _REFUSED, ids=[c[0] for c in _REFUSED])
def test_the_startup_check_refuses(store, tmp_path, fname, content, message):
    path = _file(tmp_path, fname, content)
    with pytest.raises(LaunchError, match="--config") as e:
        build_context(store.root, allow=[FID], config=path)
    assert message in str(e.value)


def test_a_missing_zones_file_exits_2_naming_the_config(store, tmp_path):
    path = _file(tmp_path, "vent.json", _good(ventilation={"zones": "zones-missing.csv"}))
    proc = subprocess.run(
        [sys.executable, DRIVER, "mcp", "--store", store.root, "--allow", FID, "--config", path],
        input=b"",
        capture_output=True,
        timeout=60,
    )
    assert proc.returncode == 2 and proc.stdout == b""
    err = proc.stderr.decode()
    assert f"error: --config {path}: ventilation.zones" in err and "Traceback" not in err


# --------------------------------------------------------------------------- oversized lines


def _server():
    ctx = fake.CallContext(store="store", allowed_facilities=frozenset({"hq"}))
    return McpServer(fake.default_registry(), ctx, server_version="TEST")


def test_an_oversized_line_is_answered_with_its_id():
    big = b'{"jsonrpc":"2.0","id":"call-7","method":"tools/call","pad":"' + b"x" * 1000 + b'"}\n'
    ok = b'{"jsonrpc":"2.0","id":2,"method":"ping"}\n'
    out = io.BytesIO()
    assert serve(_server(), io.BytesIO(big + ok), out, max_message_bytes=400) == 0
    first, second = (json.loads(x) for x in out.getvalue().splitlines())
    assert first["id"] == "call-7" and first["error"]["code"] == -32600
    assert second["id"] == 2


_HEADS = [
    (b'{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"x":"', 7),
    (b'{"method":"tools/call", "id" : "a\\"b", "params":{"x":"', 'a"b'),
    (b'{"\\u0069d":"esc","params":{"x":"', "esc"),
    (b'{"jsonrpc":"2.0","method":"tools/call","params":{"id":5,"x":"', None),  # nested only
    (b'{"jsonrpc":"2.0","id":1,"id":2,"x":"', None),  # ambiguous
    (b'{"jsonrpc":"2.0","id":true,"x":"', None),
    (b'{"jsonrpc":"2.0","id":1.5,"x":"', None),
    (b'{"jsonrpc":"2.0","id":null,"x":"', None),
    (b'{"jsonrpc":"2.0","id":[1],"x":"', None),
    (b'{"jsonrpc":"2.0","id":123', None),  # the number may be cut
    (b'{"jsonrpc":"2.0","id":"\\ud83d","x":"', None),  # a lone surrogate
    (b'[{"id":1}', None),
    (b"not json at all", None),
]


@pytest.mark.parametrize("head, expected", _HEADS)
def test_head_id(head, expected):
    assert head_id(head + b"y" * 200) == expected
