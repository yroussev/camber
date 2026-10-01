"""The shared M&V test vectors (examples/mv_vectors) regenerate exactly.

Two tiers. The synthetic cases live in the repository and regenerate offline here, byte for byte:
inputs, predicted series and their entries in expected.json. The BDG2 cases are never committed
(CAMBER redistributes no datasets): ``fetch_bdg2.py`` rebuilds their inputs from the publisher's
sha256-pinned files. Their regeneration runs when the BDG2 files are in ``examples/_data/bdg2``,
and a ``-m network`` test downloads them. Any change to CAMBER's change-point fit, selection,
Guideline 14 statistics, gates, SEP verdict, savings or billing path that moves a vector fails
here -- regenerate with ``examples/mv_vectors/generate.py`` only when the move is intended, and
say so in the CHANGELOG.
"""

import ast
import glob
import importlib.util
import json
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DIR = os.path.join(_ROOT, "examples", "mv_vectors")
_BDG2 = os.path.join(_ROOT, "examples", "_data", "bdg2")


def _load(name):
    if _DIR not in sys.path:  # generate.py imports its siblings
        sys.path.insert(0, _DIR)
    spec = importlib.util.spec_from_file_location(
        f"mv_vectors_{name}", os.path.join(_DIR, f"{name}.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _committed():
    with open(os.path.join(_DIR, "expected.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _bdg2_on_disk() -> bool:
    fb = _load("fetch_bdg2")
    return all(os.path.exists(os.path.join(_BDG2, *n.split("/"))) for n in fb.SOURCES)


@pytest.fixture(scope="module")
def regen(tmp_path_factory):
    """The synthetic tier, regenerated offline (BDG2 entries are kept from the committed file)."""
    gen = _load("generate")
    out = str(tmp_path_factory.mktemp("mv_vectors"))
    files = gen.build_inputs(out, offline=True, local=os.path.join(out, "no-local"))
    exp = gen.build_expected(out, files, previous=_committed())
    return out, files, exp, gen.build_example(exp, out)


def test_no_bdg2_data_is_committed():
    # CAMBER redistributes no datasets: no BDG2-derived inputs or series in the repository
    tracked = glob.glob(os.path.join(_DIR, "inputs", "**", "*"), recursive=True)
    tracked += glob.glob(os.path.join(_DIR, "predictions", "*"))
    assert not [p for p in tracked if "bdg2" in os.path.basename(p)]
    for c in _committed()["cases"]:
        assert (c["data"] == "local") == (c["source"] == "bdg2"), c["id"]


def test_synthetic_inputs_regenerate_byte_for_byte(regen):
    out, files, _, _ = regen
    files = dict(files)
    bills = files.pop("_bills")
    assert files.pop("_local") is None
    rels = [rel for intervals in files.values() for rel in intervals.values()]
    rels += [rel for case in bills.values() for rel in case.values()]
    assert len(rels) == 19
    on_disk = glob.glob(os.path.join(_DIR, "inputs", "**", "*.csv"), recursive=True)
    assert sorted(os.path.relpath(p, _DIR) for p in on_disk) == sorted(rels)
    for rel in rels:
        with open(os.path.join(out, rel), "rb") as a, open(os.path.join(_DIR, rel), "rb") as b:
            assert a.read() == b.read(), rel


def test_synthetic_predicted_series_regenerate_byte_for_byte(regen):
    out = regen[0]
    names = sorted(os.listdir(os.path.join(_DIR, "predictions")))
    assert len(names) == 17 and names == sorted(os.listdir(os.path.join(out, "predictions")))
    for name in names:
        rel = os.path.join("predictions", name)
        with open(os.path.join(out, rel), "rb") as a, open(os.path.join(_DIR, rel), "rb") as b:
            assert a.read() == b.read(), rel


def test_expected_outputs_regenerate_exactly(regen):
    exp = json.loads(json.dumps(regen[2]))
    committed = _committed()
    # the generator records the commit it ran on; everything else must match exactly
    for d in (exp, committed):
        d["generator"].pop("camber_version")
    for a, b in zip(exp["cases"], committed["cases"]):
        for interval in b["fits"]:
            assert a["fits"][interval] == b["fits"][interval], f"{b['id']}/{interval}"
    for a, b in zip(exp["bills"]["cases"], committed["bills"]["cases"]):
        for key in b:
            assert a[key] == b[key], f"{b['id']}: {key}"
    assert exp == committed


def test_example_results_regenerate_and_pass(regen):
    ex = regen[3]
    with open(os.path.join(_DIR, "example_results.json"), encoding="utf-8") as fh:
        assert json.loads(json.dumps(ex)) == json.load(fh)
    chk = _load("check_vectors")
    lines, n_fail = chk.compare(chk.load_expected(), ex)
    assert n_fail == 0
    assert any(ln.startswith("NOTE syn_3ph_58/monthly") for ln in lines)
    assert any("predictions row by row" in ln for ln in lines)


def test_synthetic_truth_is_recovered_where_promised():
    for c in _committed()["cases"]:
        if c["source"] == "synthetic" and c["truth"]["recovery_expected"]:
            assert all(c["fits"]["daily"]["truth_recovery"].values()), c["id"]


def test_checker_without_local_bdg2_files(tmp_path):
    chk = _load("check_vectors")
    exp = chk.load_expected()
    empty = str(tmp_path)
    assert chk.self_test(exp, local=empty) == []
    assert len(chk.missing_local(exp, local=empty)) == 8  # 6 change-point + 2 bill cases
    lines, n_fail = chk.compare(exp, chk.template(exp), local=empty)
    assert n_fail == 0 and lines


def test_checker_flags_a_wrong_result():
    chk = _load("check_vectors")
    exp = chk.load_expected()
    res = chk.template(exp)
    r = res["results"]["syn_5p_55_68"]["daily"]
    r["change_points"] = [r["change_points"][0] + 3.0, r["change_points"][1]]
    r["kind"] = "4P"  # not within 2 BIC of CAMBER's 5P here
    r2 = res["results"]["syn_3ph_58"]["monthly"]
    r2["kind"] = exp_kind = "3PH"  # CAMBER's 4P is within 2 BIC of 3PH: a NOTE, not a failure
    lines, n_fail = chk.compare(exp, res)
    assert n_fail == 1
    assert any(ln.startswith("NOTE syn_3ph_58/monthly") and exp_kind in ln for ln in lines)


def test_checker_flags_wrong_bill_results():
    chk = _load("check_vectors")
    exp = chk.load_expected()
    res = chk.template(exp)
    case = res["bills"]["bills_syn_dd_hc_55_68"]
    case["cooling_base_f"] += 5.0
    month = sorted(case["calendarized"])[3]
    case["calendarized"][month] *= 1.05
    lines, n_fail = chk.compare(exp, res)
    assert n_fail == 2
    assert any("cooling_base_f" in ln for ln in lines if ln.startswith("FAIL"))


def test_checker_rejects_another_schema():
    chk = _load("check_vectors")
    exp = chk.load_expected()
    res = chk.template(exp)
    res["schema"] = "mv_vectors/2"
    _, n_fail = chk.compare(exp, res)
    assert n_fail == 1


def _third_party(name):
    with open(os.path.join(_DIR, name), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add((node.module or "").split(".")[0])
    return mods - set(sys.stdlib_module_names) - {"__future__"}


def test_consumer_tools_never_import_camber():
    # the files-and-processes boundary: consuming the vectors needs no CAMBER install
    assert _third_party("check_vectors.py") == {"numpy", "pandas"}
    assert _third_party("export_parquet.py") == {"pandas", "pyarrow"}
    assert _third_party("fetch_bdg2.py") == {"numpy", "pandas", "check_vectors"}


def test_fetch_pins_match_the_catalog():
    fb = _load("fetch_bdg2")
    with open(os.path.join(_ROOT, "camber", "datasets", "catalog.json"), encoding="utf-8") as fh:
        cat = json.load(fh)
    entry = next(e for e in cat["datasets"] if e["id"] == "bdg2")
    files = {f["name"]: f for f in entry["files"]}
    for name, (url, sha) in fb.SOURCES.items():
        assert files[name]["url"] == url and files[name]["sha256"] == sha, name
    assert entry["licence"] == "CC-BY-SA-4.0" and "BY-SA" in fb.LICENCE
    assert set(fb.DERIVED_SHA256) == set(fb.derived_paths())


def test_parquet_export_keeps_the_columns(tmp_path):
    pytest.importorskip("pyarrow")
    ex = _load("export_parquet")
    written = ex.export(str(tmp_path))
    n_csv = len(glob.glob(os.path.join(_DIR, "inputs", "**", "*.csv"), recursive=True))
    assert len(written) == n_csv + 17
    rel = os.path.join("inputs", "bills", "bills_syn_dd_hc_55_68_bills")
    csv = pd.read_csv(os.path.join(_DIR, rel + ".csv"))
    pq = pd.read_parquet(os.path.join(str(tmp_path), rel + ".parquet"))
    assert list(pq.columns) == list(csv.columns)
    assert (pq["energy"].to_numpy() == csv["energy"].to_numpy()).all()
    assert [str(d) for d in pq["start"]] == list(csv["start"])


# --------------------------------------------------------------------------- the BDG2 tier


def _bdg2_regenerates(source: str, tmp_path):
    """Rebuild the BDG2 inputs from ``source``, check their sha256 pins, and refit them."""
    fb = _load("fetch_bdg2")
    gen = _load("generate")
    local = str(tmp_path / "local")
    fb.ensure_sources(source=source)
    fb.build(local, source)
    assert fb.verify(local) == []
    out = str(tmp_path / "out")
    files = gen.build_inputs(out, offline=False, local=local, source=source)
    exp = json.loads(json.dumps(gen.build_expected(out, files, previous=_committed())))
    committed = _committed()
    for d in (exp, committed):
        d["generator"].pop("camber_version")
    for a, b in zip(exp["cases"], committed["cases"]):
        assert a == b, a["id"]
    for a, b in zip(exp["bills"]["cases"], committed["bills"]["cases"]):
        assert a == b, a["id"]
    chk = _load("check_vectors")
    assert chk.self_test(chk.load_expected(), local=local) == []


@pytest.mark.skipif(not os.path.isdir(_BDG2), reason="no examples/_data/bdg2")
def test_bdg2_tier_regenerates_from_local_files(tmp_path):
    if not _bdg2_on_disk():
        pytest.skip("examples/_data/bdg2 lacks the steam / gas / weather files")
    _bdg2_regenerates(_BDG2, tmp_path)


@pytest.mark.network
def test_bdg2_tier_regenerates_from_the_publisher(tmp_path):
    fb = _load("fetch_bdg2")
    cache = str(tmp_path / "cache")
    fb.ensure_sources(cache=cache)  # downloads and checks every sha256 pin
    _bdg2_regenerates(cache, tmp_path)
