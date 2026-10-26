"""0.111 (#296): ``camber mcp --config`` is checked at startup the way ``camber run`` checks it.

A config ``camber run`` refuses (bad JSON, a list, an empty file, YAML without PyYAML, an
unknown rule or facility, a wrong shape), a file that is not ``.json`` / ``.yaml`` / ``.yml``, a
config for a facility that is not allowed, and a config whose ``source.store`` is not the
``--store`` directory all stop the server with one line on stderr and exit 2. ``--config`` may
be given once per facility.
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.dirname(HERE))

import _rcx_fixture as fx  # noqa: E402
import pytest  # noqa: E402

from camber.mcp import LaunchError, build_context  # noqa: E402

FID = fx.FID
DRIVER = os.path.join(HERE, "_mcp_driver.py")


@pytest.fixture()
def store(tmp_path):
    st = fx.make_store(tmp_path, meta=None)
    st.write_role_frame(fx.ahu_frame(seed=2), facility_id="annex", equip="A-1", equip_class="AHU")
    return st


def _good(**over):
    cfg = {
        "source": {"kind": "store", "store": "store", "facility_id": FID},
        "equipment": [{"class": "AHU"}],
        "rules": ["outdoor_air_fraction"],
    }
    cfg.update(over)
    return cfg


def _file(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content if isinstance(content, str) else json.dumps(content))
    return str(p)


_REFUSED = [
    ("bad.json", '{"source": ', "invalid JSON"),
    ("trailing.json", json.dumps(_good()) + ",", "invalid JSON"),
    ("list.json", "[1, 2, 3]", "JSON object"),
    ("empty.json", "", "invalid JSON"),
    ("cfg.txt", "{}", ".json, .yaml or .yml"),
    ("empty.yaml", "", "names no facility"),
    ("nofac.json", {"rules": ["outdoor_air_fraction"]}, "names no facility"),
    ("rule.json", _good(rules=["no_such_rule"]), "unknown rule 'no_such_rule'"),
    ("param.json", _good(rules=[{"name": "outdoor_air_fraction", "params": {"x": 1}}]), "x"),
    ("shape.json", _good(rules="outdoor_air_fraction"), "must be a list"),
    ("units.json", _good(units={"system": "metric"}), "units"),
    (
        "fac.json",
        _good(source={"kind": "store", "store": "store", "facility_id": "nowhere"}),
        "not allowed",
    ),
    (
        "other.json",
        _good(source={"kind": "store", "store": "elsewhere", "facility_id": FID}),
        "is not the --store directory",
    ),
]


@pytest.mark.parametrize("fname, content, message", _REFUSED, ids=[c[0] for c in _REFUSED])
def test_a_config_camber_run_refuses_stops_the_server(store, tmp_path, fname, content, message):
    path = _file(tmp_path, fname, content)
    with pytest.raises(LaunchError, match="--config") as e:
        build_context(
            store.root, allow=[FID, "nowhere"][: 2 if "nowhere" in fname else 1], config=path
        )
    assert message in str(e.value)


def test_a_facility_the_store_does_not_hold_is_refused(store, tmp_path):
    path = _file(
        tmp_path,
        "c.json",
        _good(source={"kind": "store", "store": "store", "facility_id": "ghost"}),
    )
    with pytest.raises(LaunchError, match="has no data in store"):
        build_context(store.root, allow=[FID, "ghost"], config=path)


def test_yaml_without_pyyaml_is_refused(store, tmp_path, monkeypatch):
    import camber._yaml as y

    def missing():
        raise y.MissingYamlExtra(y._INSTALL_HINT)

    monkeypatch.setattr(y, "_yaml", missing)
    path = _file(tmp_path, "site.yaml", "source:\n  kind: store\n")
    with pytest.raises(LaunchError, match="PyYAML"):
        build_context(store.root, allow=[FID], config=path)


def test_a_directory_is_refused(store, tmp_path):
    (tmp_path / "cfg.json").mkdir()
    with pytest.raises(LaunchError, match="is a directory"):
        build_context(store.root, allow=[FID], config=str(tmp_path / "cfg.json"))


def test_a_config_store_that_differs_from_store_is_refused(store, tmp_path):
    other = fx.make_store(tmp_path / "copy", meta=None)
    cfg = _good(source={"kind": "store", "store": other.root, "facility_id": FID})
    with pytest.raises(LaunchError, match="is not the --store directory"):
        build_context(store.root, allow=[FID], config=_file(tmp_path, "c.json", cfg))


def test_the_same_store_spelled_another_way_is_accepted(store, tmp_path):
    os.symlink(store.root, tmp_path / "link")
    sub = tmp_path / "configs"
    sub.mkdir()
    for spelling in (store.root, "../store", str(tmp_path / "link")):
        cfg = _good(source={"kind": "store", "store": spelling, "facility_id": FID})
        path = _file(sub, "c.json", cfg)
        ctx = build_context(store.root, allow=[FID], config=path)
        assert ctx.config == path


def test_one_config_per_facility(store, tmp_path):
    a = _file(tmp_path, "a.json", _good())
    b = _file(
        tmp_path,
        "b.json",
        _good(source={"kind": "store", "store": "store", "facility_id": "annex"}),
    )
    ctx = build_context(store.root, allow_all_active=True, config=[a, b])
    assert ctx.config == (a, b)
    with pytest.raises(LaunchError, match="both for facility"):
        build_context(store.root, allow=[FID], config=[a, _file(tmp_path, "a2.json", _good())])


def test_the_command_exits_2_with_one_line(store, tmp_path):
    path = _file(tmp_path, "bad.json", '{"source": ')
    proc = subprocess.run(
        [sys.executable, DRIVER, "mcp", "--store", store.root, "--allow", FID, "--config", path],
        input=b"",
        capture_output=True,
        timeout=60,
    )
    assert proc.returncode == 2 and proc.stdout == b""
    assert b"error: --config" in proc.stderr and b"invalid JSON" in proc.stderr


def test_config_is_repeatable_on_the_command_line():
    from camber.cli import _build_parser

    args = _build_parser().parse_args(
        ["mcp", "--store", "s", "--allow-all-active", "--config", "a.json", "--config", "b.json"]
    )
    assert args.config == ["a.json", "b.json"]
