"""0.110 (#276): a no-persist config run computes the same findings and writes nothing.

A run with ``persist=False`` (or inside :func:`camber._nopersist.no_persist`, the capability
registry's scope) leaves the store, its facility registry, the fault history and the portfolio
workspace byte-identical -- every file's bytes and modification time, and the set of paths.

Every test works in its own tmp directory; no default cache or example data is touched.
"""

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_portfolio_continuity import _run, _set_run_id, _store_config, _workspace  # noqa: E402

from camber._nopersist import no_persist, persisting  # noqa: E402
from camber.cli import main  # noqa: E402
from camber.config import load_config, run_config  # noqa: E402
from camber.mvbilling import billing_oat  # noqa: E402


def _tree(root, skip=()) -> dict:
    """``{relative path: (sha256, mtime_ns)}`` of every file under ``root``, and every dir."""
    out = {}
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if os.path.join(dirpath, d) not in skip)
        rel = os.path.relpath(dirpath, root)
        out[rel + "/"] = None
        for f in sorted(files):
            p = os.path.join(dirpath, f)
            with open(p, "rb") as fh:
                out[os.path.join(rel, f)] = (
                    hashlib.sha256(fh.read()).hexdigest(),
                    os.stat(p).st_mtime_ns,
                )
    return out


def _findings(res) -> str:
    return json.dumps([f.as_dict() for f in res.findings], sort_keys=True, default=str)


def _primed(tmp_path, monkeypatch):
    """A workspace facility with a frozen baseline and one persisted run (fault history exists),
    and the store's catalog cache invalidated so a reader would write it back."""
    pf, fid = _workspace(tmp_path)
    monkeypatch.setenv("CAMBER_PORTFOLIO", pf.root)
    cfg = _store_config(tmp_path, fid)
    assert main(["drift", "freeze", cfg, "--reason", "commissioning baseline"]) == 0
    first = _run(cfg)
    assert first.persisted and first.faults["persisted"] is True
    assert os.path.isfile(os.path.join(pf.state_dir(fid), "faults.json"))
    _set_run_id(cfg, "R2")
    pf.store._invalidate_catalog()
    return pf, fid, cfg


def test_no_persist_run_writes_nothing_and_matches_a_persisting_run(tmp_path, monkeypatch):
    pf, fid, cfg = _primed(tmp_path, monkeypatch)
    before = _tree(tmp_path)

    dry = run_config(load_config(cfg), base_dir=str(tmp_path), persist=False)
    assert _tree(tmp_path) == before
    assert dry.persisted is False and dry.faults["persisted"] is False
    assert dry.report is not None and dry.drift is not None

    wet = _run(cfg)  # the same run, persisting: identical findings and fault fold
    assert wet.persisted is True
    assert _findings(dry) == _findings(wet)
    assert {k: v for k, v in dry.faults.items() if k != "persisted"} == {
        k: v for k, v in wet.faults.items() if k != "persisted"
    }
    assert _tree(tmp_path) != before  # ...and the persisting one does write


def test_the_no_persist_scope_beats_persist_true(tmp_path, monkeypatch):
    _pf, _fid, cfg = _primed(tmp_path, monkeypatch)
    before = _tree(tmp_path)
    assert persisting()
    with no_persist():
        assert not persisting()
        res = run_config(load_config(cfg), base_dir=str(tmp_path), persist=True)
    assert persisting()
    assert res.persisted is False and _tree(tmp_path) == before


def test_cli_no_persist_writes_only_the_requested_output(tmp_path, monkeypatch, capsys):
    _pf, _fid, cfg = _primed(tmp_path, monkeypatch)
    out = tmp_path / "out"
    out.mkdir()
    before = _tree(tmp_path, skip={str(out)})
    assert main(["run", cfg, "--no-persist", "--out", str(out)]) == 0
    assert "(not saved: --no-persist)" in capsys.readouterr().out
    assert main(["report", cfg, "--no-persist", "--out", str(out / "r.html")]) == 0
    assert _tree(tmp_path, skip={str(out)}) == before
    assert (out / "findings.json").is_file() and (out / "r.html").is_file()


def test_no_persist_fetches_no_weather():
    entry = {"oat": {"fetch": "auto", "latitude": 40.0, "longitude": -75.0}}
    with no_persist():
        series, why = billing_oat(entry, object(), window=("2024-01-01", "2024-02-01"))
    assert series is None and "fetches no weather" in why
