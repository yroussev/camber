"""0.112 (#337): small items from round 3 of the 0.112 acceptance test.

- ``coverage`` with ``equips`` or ``roles`` keeps the facility's whole window, so a point's
  coverage and gaps do not change with the filter.
- ``store verify`` lists a directory it cannot list (mode 000 or 0o111, at any level) as unreadable
  and goes on: counts, the listing and ``--json``, exit 1, and no absolute path in an error.
- A provenance mark with a field of the wrong type (or missing) is unreadable, so research-only,
  for the up-front check, the read-time check and ``datasets provenance`` (table and ``--json``).

Every store is a small one in its own tmp directory; nothing reaches the network.
"""

import json
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_store_provenance_319 import OPEN, REG, _ctx, _refused, _write  # noqa: E402

from camber import _servable  # noqa: E402
from camber.capabilities import CapabilityError  # noqa: E402
from camber.cli import main  # noqa: E402
from camber.store import ParquetStore, _normalize, _partprov  # noqa: E402

needs_perms = pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root reads mode 000"
)


@pytest.fixture(autouse=True)
def _fresh_memos(monkeypatch):
    _partprov._MARKS.clear()
    _partprov.research_only.cache_clear()
    _servable._SEEN.clear()
    monkeypatch.setattr(_servable, "_RETRY_WAITS", (0.0, 0.001, 0.001, 0.001))


# ------------------------------------------------------------------ coverage window


def _cov_facts(st, args):
    r = REG.call("coverage", {"facility_id": "site", **args}, _ctx(st.root, {"site"}))
    return r, {f["id"]: f for f in r.facts}


def test_coverage_with_equips_keeps_the_facility_window(tmp_path):
    st = ParquetStore(str(tmp_path / "store"))
    _write(
        st, "site", None, equip="AHU-1", idx=pd.date_range("2024-06-01", periods=24 * 20, freq="h")
    )
    # a logger with 4 days of data in the middle of a 20-day facility
    _write(
        st, "site", None, equip="LOGGER-1", idx=pd.date_range("2024-06-08", periods=96, freq="h")
    )
    whole, _ = _cov_facts(st, {})
    for args in ({"equips": ["LOGGER-1"]}, {"equips": ["LOGGER-1"], "roles": ["supply_air_temp"]}):
        sel, facts = _cov_facts(st, args)
        assert sel.data["window"] == whole.data["window"]
        for p in sel.data["points"]:
            (same,) = [
                q
                for q in whole.data["points"]
                if (q["equip"], q["role"]) == (p["equip"], p["role"])
            ]
            assert p == same
        sat = next(p for p in sel.data["points"] if p["role"] == "supply_air_temp")
        assert sat["coverage"] == pytest.approx(96 / 480, abs=1e-3) and sat["n_gaps"] == 2
        pfact = next(
            f
            for f in facts.values()
            if f["kind"] == "point_coverage" and f["role"] == "supply_air_temp"
        )
        assert "20.0% of the whole window" in pfact["text"] and "2 gap(s)" in pfact["text"]
        (f1,) = [f for f in facts.values() if f["kind"] == "coverage"]
        assert "2024-06-01" in f1["text"]
    # an explicit end still bounds the window; the open start is the facility's first sample
    sel, _ = _cov_facts(st, {"equips": ["LOGGER-1"], "end": "2024-06-12"})
    assert sel.data["window"]["start"].startswith("2024-06-01")


# ------------------------------------------------------------------ store verify


def _store(tmp_path):
    st = ParquetStore(str(tmp_path / "store"))
    _write(st, "hq", None)  # three months: 2024-06 to 2024-08
    _write(st, "other", None)
    return st


@needs_perms
@pytest.mark.parametrize("mode", [0, 0o111])
@pytest.mark.parametrize(
    "rel", ["facility_id=hq/year=2024/month=6", "facility_id=hq/year=2024", "facility_id=hq"]
)
def test_store_verify_lists_a_directory_it_cannot_list_and_goes_on(tmp_path, capsys, rel, mode):
    st = _store(tmp_path)
    before = _normalize.verify(st.root)["counts"]["parts"]
    d = os.path.join(st.root, rel)
    os.chmod(d, mode)
    try:
        r = _normalize.verify(st.root)
        (bad,) = [p for p in r["parts"] if p["error"]]
        assert bad["path"] == rel and "cannot be listed" in bad["error"] and not r["ok"]
        assert r["counts"]["unreadable"] == 1
        others = [p for p in r["parts"] if p["facility_id"] == "other"]
        assert others and all(p["canonical"] for p in others)  # the rest is still checked
        assert r["counts"]["parts"] < before + 1
        capsys.readouterr()
        assert main(["store", "verify", st.root]) == 1
        out, err = capsys.readouterr()
        assert "1 unreadable" in out and f"store/{rel} [store]: " in out and not err
        assert main(["store", "verify", st.root, "--json"]) == 1
        out, err = capsys.readouterr()
        assert json.loads(out)["counts"]["unreadable"] == 1 and not err
        assert main(["store", "verify", st.root, "--facility", "hq"]) == 1
        # normalize's dry run walks the same way and does not stop either
        assert _normalize.normalize(st.root)["dry_run"]
    finally:
        os.chmod(d, 0o755)
    assert _normalize.verify(st.root)["ok"]


def test_store_verify_error_names_a_path_by_its_store_relative_name(tmp_path, capsys, monkeypatch):
    st = _store(tmp_path)
    inside = os.path.join(st.root, "facility_id=hq", "year=2024")

    def denied(*_a, **_k):
        raise PermissionError(13, "Permission denied", inside)

    monkeypatch.setattr(_normalize, "_health", denied)
    assert main(["store", "verify", st.root]) == 1
    err = capsys.readouterr().err
    assert err == "error: Permission denied: facility_id=hq/year=2024\n"


@needs_perms
def test_a_readable_but_unsearchable_directory_is_called_a_permission_problem(tmp_path):
    st = _store(tmp_path)
    d = os.path.join(st.root, "facility_id=hq", "year=2024")
    os.chmod(d, 0o444)
    try:
        _files, bad = _partprov.scan(os.path.join(st.root, "facility_id=hq"))
    finally:
        os.chmod(d, 0o755)
    assert bad and all(why == "cannot be read (permission denied)" for _p, why in bad)


# ------------------------------------------------------------------ one rule for a mark's types


def _mark(**fields):
    doc = {"dataset_id": "lbnl-fcu", "licence": "CC-BY-4.0", "research_only": False, **fields}
    doc = {k: v for k, v in doc.items() if v is not _DROP}
    return json.dumps(doc).encode()


_DROP = object()


@pytest.mark.parametrize(
    "value",
    [
        _mark(dataset_id=7),
        _mark(dataset_id={"a": 1}),
        _mark(dataset_id=None),
        _mark(dataset_id=""),
        _mark(dataset_id=["lbnl-fcu"]),
        _mark(licence=5),
        _mark(licence=None),
        _mark(research_only=0),
        _mark(research_only="false"),
        _mark(dataset_id=_DROP),
        _mark(licence=_DROP),
    ],
    ids=lambda v: v.decode()[:60],
)
def test_a_mark_with_a_field_of_the_wrong_type_is_research_only_everywhere(tmp_path, capsys, value):
    st = ParquetStore(str(tmp_path / "store"))
    _write(st, "own1", value)
    _write(st, "open", OPEN)
    assert _partprov.decode(value) is None and _partprov.research_only(value)
    # datasets provenance: the table and --json agree
    assert main(["datasets", "provenance", "--store", st.root]) == 0
    table = {x.split()[0]: x for x in capsys.readouterr().out.splitlines() if x.strip()}
    assert "[mark unreadable: counts as research-only]" in table["own1"]
    assert "research-only" not in table["open"]
    assert main(["datasets", "provenance", "--store", st.root, "--json"]) == 0
    rows = {r["facility_id"]: r for r in json.loads(capsys.readouterr().out)}
    assert (
        rows["own1"]["research_only"] and rows["own1"]["unreadable_marks"] == rows["own1"]["parts"]
    )
    assert not rows["open"]["research_only"] and rows["open"]["unreadable_marks"] == 0
    # the up-front check
    assert _servable.refusal("own1", st.root)[0] == "research_only"
    assert _servable.refusal("open", st.root) is None
    _refused(_ctx(st.root, {"own1"}), "own1")
    _refused(_ctx(st.root, {"own1"}), "own1", "coverage")
    # the read-time check, which needs no facility name
    with _partprov.serving(), pytest.raises(CapabilityError) as e:
        st.read_long()
    assert e.value.code == "refused_licence"
    assert REG.call("points", {"facility_id": "open"}, _ctx(st.root, {"open"})).data["count"] == 3
