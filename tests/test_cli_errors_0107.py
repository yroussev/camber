"""0.107: config mistakes end in one ``error:`` line and exit 1, not a traceback (#214); the store
commands refuse a path that is not a store, an unknown facility and a negative --limit (#215).

Every test works in its own tmp directory.
"""

import json
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.cli import main  # noqa: E402
from camber.config import _check_config_shape, run_config  # noqa: E402
from camber.store import ParquetStore  # noqa: E402


@pytest.fixture
def site(tmp_path):
    """A store with one AHU facility and a valid config pointing at it."""
    idx = pd.date_range("2024-07-01", periods=96 * 2, freq="15min")
    frames = [
        pd.DataFrame({"ts": idx, "equip": "AHU-1", "equip_class": "AHU", "role": r, "value": v})
        for r, v in (("oat", 85.0), ("sat", 55.0), ("rat", 74.0), ("mat", 78.0))
    ]
    store = tmp_path / "store"
    ParquetStore(str(store)).write_long(pd.concat(frames), facility_id="site-a", name="Site A")
    cfg = {
        "source": {"kind": "store", "store": str(store), "facility_id": "site-a"},
        "equipment": [{"class": "AHU"}],
        "rules": ["simultaneous_heat_cool"],
    }
    return tmp_path, cfg


def _write(tmp, name, data) -> str:
    p = tmp / name
    p.write_text(data if isinstance(data, str) else json.dumps(data))
    return str(p)


def _one_line_error(capsys) -> str:
    err = capsys.readouterr().err
    assert "Traceback" not in err
    lines = [x for x in err.splitlines() if x.strip()]
    assert len(lines) == 1 and lines[0].startswith("error: "), err
    return lines[0]


# --------------------------------------------------------------------------- #214


@pytest.mark.parametrize(
    "case, expect",
    [
        ("missing", "No such file or directory"),
        ("invalid", "invalid JSON"),
        ("list", "a config must be a JSON object at the top level, not list"),
        ("unknown_rule", "unknown rule 'no_such_rule'; valid rules: "),
        ("close_rule", "(did you mean simultaneous_heat_cool?)"),
        ("resample", "'resample' 'banana' is not a pandas frequency"),
        ("equipment_str", "'equipment' must be a list of"),
        (
            "rules_str",
            """'rules' must be a list of rule names, not str (write ["filter_fouling"])""",
        ),
        ("rules_entry", 'rules[0] must be a rule name or an object with a "name"'),
        ("no_facility", "facility 'nope' has no data in store"),
    ],
)
@pytest.mark.parametrize("cmd", ["run", "report"])
def test_config_mistakes_are_one_line_errors(site, capsys, case, expect, cmd):
    tmp, cfg = site
    bad = {
        "list": [cfg],
        "unknown_rule": {**cfg, "rules": ["no_such_rule"]},
        "close_rule": {**cfg, "rules": ["simultaneous_heat_coo"]},
        "resample": {**cfg, "resample": "banana"},
        "equipment_str": {**cfg, "equipment": "AHU"},
        "rules_str": {**cfg, "rules": "filter_fouling"},
        "rules_entry": {**cfg, "rules": [{"params": {}}]},
        "no_facility": {**cfg, "source": {**cfg["source"], "facility_id": "nope"}},
        "invalid": "{nope",
    }
    path = str(tmp / "missing.json") if case == "missing" else _write(tmp, "c.json", bad[case])
    argv = [cmd, path] + (["--out", str(tmp / "r.html")] if cmd == "report" else [])
    assert main(argv) == 1
    line = _one_line_error(capsys)
    assert expect in line, line
    if case == "unknown_rule":
        assert "simultaneous_heat_cool" in line and "`camber rules params`" in line


def test_a_valid_config_still_runs(site, capsys):
    tmp, cfg = site
    assert main(["run", _write(tmp, "ok.json", cfg)]) == 0


def test_camber_debug_shows_the_traceback(site, monkeypatch):
    tmp, cfg = site
    path = _write(tmp, "c.json", {**cfg, "rules": ["no_such_rule"]})
    monkeypatch.setenv("CAMBER_DEBUG", "1")
    with pytest.raises(KeyError, match="unknown rule"):
        main(["run", path])
    monkeypatch.setenv("CAMBER_DEBUG", "0")
    assert main(["run", path]) == 1


def test_the_api_checks_the_shape_too(site):
    tmp, cfg = site
    with pytest.raises(ValueError, match="'rules' must be a list"):
        run_config({**cfg, "rules": "filter_fouling"}, base_dir=str(tmp))
    with pytest.raises(KeyError, match="valid rules"):
        run_config({**cfg, "rules": ["nope"]}, base_dir=str(tmp))
    with pytest.raises(ValueError, match="must be a JSON object"):
        _check_config_shape(["x"])
    assert _check_config_shape({"rules": None, "equipment": []}) == {"rules": None, "equipment": []}


def test_catalog_key_errors_print_the_message_not_the_repr(capsys):
    assert main(["datasets", "info", "no-such-dataset"]) == 1
    line = _one_line_error(capsys)
    assert line.startswith("error: unknown dataset 'no-such-dataset'"), line


# --------------------------------------------------------------------------- #215


@pytest.mark.parametrize(
    "argv",
    [
        ["store", "verify", "{p}", "--json", "--strict"],
        ["store", "normalize", "{p}", "--apply", "--yes"],
        ["store", "normalize", "{p}"],
        ["store", "migrate-partitions", "{p}", "--apply", "--yes"],
        ["store", "migrate-partitions", "{p}"],
    ],
)
@pytest.mark.parametrize("kind", ["missing", "empty", "file"])
def test_store_commands_refuse_a_path_that_is_not_a_store(tmp_path, capsys, argv, kind):
    p = tmp_path / "not-a-store"
    if kind == "empty":
        p.mkdir()
    elif kind == "file":
        p.write_text("x")
    before = sorted(os.listdir(tmp_path))
    assert main([a.format(p=p) for a in argv]) == 1
    assert "is not a ParquetStore" in capsys.readouterr().err
    assert sorted(os.listdir(tmp_path)) == before  # nothing created
    if kind == "empty":
        assert os.listdir(p) == []  # not even a _lock


def test_store_verify_and_normalize_refuse_an_unknown_facility(site, capsys):
    tmp, cfg = site
    store = cfg["source"]["store"]
    assert main(["store", "verify", store, "--facility", "site-a"]) == 0
    capsys.readouterr()
    for argv in (
        ["store", "verify", store, "--facility", "nope"],
        ["store", "normalize", store, "--facility", "nope"],
    ):
        assert main(argv) == 1
        assert "unknown facility 'nope'" in capsys.readouterr().err


def test_store_verify_limit_must_be_non_negative(site, capsys):
    tmp, cfg = site
    store = cfg["source"]["store"]
    with pytest.raises(SystemExit) as e:
        main(["store", "verify", store, "--limit", "-1"])
    assert e.value.code == 2 and "must be >= 0" in capsys.readouterr().err
    assert main(["store", "verify", store, "--limit", "0"]) == 0
    assert "... and" not in capsys.readouterr().out


def test_retention_apply_refuses_an_unknown_facility(tmp_path, capsys):
    from camber.portfolio import Portfolio

    pf = Portfolio.init(tmp_path / "ws")
    pf.add_facility("Site", facility_id="site-a", reason="t", activate=True)
    ws = ["--workspace", pf.root]
    argv = ["retention", "apply", *ws, "--facility", "nope"]
    assert main(argv) == 1
    assert "unknown facility 'nope'" in _one_line_error(capsys)
    assert main([*argv, "--apply", "--reason", "r", "--yes"]) == 1
    assert "unknown facility 'nope'" in _one_line_error(capsys)
    assert main(["retention", "apply", *ws, "--facility", "site-a"]) == 0
