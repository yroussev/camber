"""Synthetic catalog entries (0.103, #133): generated locally, ingested like any dataset, labelled
synthetic everywhere, and still validated."""

import copy
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber import datasets as ds  # noqa: E402
from camber.datasets import _paths  # noqa: E402
from camber.datasets._catalog import load_catalog_data, validate_catalog  # noqa: E402
from camber.datasets._issues import render_markdown  # noqa: E402
from camber.datasets._ops import adopt_local_files  # noqa: E402
from camber.datasets._synthetic import (  # noqa: E402
    GENERATORS,
    SYNTHETIC_LICENCE,
    generate_files,
    generator_spec,
)
from camber.report.audit import SYNTHETIC_NOTE, data_sources_html, data_sources_text  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DID = "synthetic-hw-plant-lockout"


def _entry_dict():
    return copy.deepcopy(next(d for d in load_catalog_data()["datasets"] if d["id"] == DID))


def test_the_entry_is_synthetic_open_and_camber_licensed():
    e = ds.get(DID)
    assert e.synthetic and e.kind == "synthetic" and e.licence == SYNTHETIC_LICENCE
    assert e.access == "open" and not e.research_only and e.commercial_ok and not e.manual
    assert generator_spec(e) == {"name": "hw_plant_lockout", "seed": 133, "version": 1}
    assert [f["name"] for f in e.files] == list(GENERATORS["hw_plant_lockout"].files)
    assert e.download_bytes() == 0
    prov = e.provenance()
    assert prov["synthetic"] is True and prov["generator"]["name"] == "hw_plant_lockout"
    assert prov["redistribution"] == "allowed"
    assert e.as_dict()["generator"] == {"name": "hw_plant_lockout", "seed": 133}
    assert "synthetic" not in ds.get("lbnl-boiler").provenance()


@pytest.mark.parametrize(
    "mutate, needle",
    [
        (lambda d: d["generator"].update(name="nope"), "needs generator.name"),
        (lambda d: d.pop("generator"), "needs generator.name"),
        (lambda d: d["generator"].update(seed=-1), "generator.seed"),
        (lambda d: d["generator"].update(seed=True), "generator.seed"),
        (lambda d: d["files"][0].update(url="https://example.org/x.csv"), "generated, not down"),
        (lambda d: d["files"][0].update(sha256="0" * 64, size=3), "drop size, sha256"),
        (lambda d: d["files"].append({"name": "extra.csv"}), "writes ['hw_plant_lockout.csv']"),
        (lambda d: d.update(licence="CC-BY-4.0"), "CAMBER's own data"),
        (lambda d: d.update(access="research_only", access_reason="x"), "CAMBER's own data"),
        (lambda d: d.update(manual=True, manual_instructions="x"), "never a manual download"),
        (lambda d: d.update(licence_check={"url": "https://x.org"}), "no publisher licence page"),
        (lambda d: d.update(kind="simulated"), "only applies to a kind 'synthetic' entry"),
    ],
)
def test_validator_rejects_a_bad_synthetic_entry(mutate, needle):
    d = _entry_dict()
    assert validate_catalog({"schema": 1, "datasets": [d]}) == []
    mutate(d)
    errs = validate_catalog({"schema": 1, "datasets": [d]})
    assert any(needle in e for e in errs), errs


def test_generation_is_deterministic(tmp_path):
    e = ds.get(DID)
    a = generate_files(e, str(tmp_path / "a"))["hw_plant_lockout.csv"]
    b = generate_files(e, str(tmp_path / "b"))["hw_plant_lockout.csv"]
    with open(a, "rb") as fa, open(b, "rb") as fb:
        assert fa.read() == fb.read()
    with open(a, encoding="utf-8") as fh:
        head = fh.readline().strip().split(",")
    assert head == [
        "Timestamp",
        "scenario",
        "OAT",
        "BLR_FIRE",
        "BLR_GAS_KW",
        "HWP_STS",
        "HWP_SPD",
        "HWS_T",
        "HWR_T",
    ]
    assert os.path.getsize(a) < 500_000  # small


def test_fetch_generates_then_skips_and_ingest_is_like_any_dataset(tmp_path):
    cache = str(tmp_path / "cache")
    seen = []
    res = ds.fetch(DID, data_dir=cache, progress=lambda n, d, t: seen.append((n, d, t)))
    f = res.files[0]
    assert res.downloaded_bytes == 0 and f["generated"] and not f["skipped"] and seen
    rec = _paths.read_manifest(cache)[DID]
    assert rec["source"] == "generated" and rec["generator"] == generator_spec(ds.get(DID))
    assert rec["files"]["hw_plant_lockout.csv"]["sha256"] == f["sha256"]
    again = ds.fetch(DID, data_dir=cache)
    assert again.files[0]["skipped"] and again.files[0]["sha256"] == f["sha256"]

    store = str(tmp_path / "store")
    got = ds.ingest(DID, store, data_dir=cache)
    assert got.facilities == ["ds-synthetic-hw-plant-lockout"] and got.equipment == 4
    st = ds.status(data_dir=cache, store=store)
    row = next(r for r in st if r["id"] == DID)
    assert row["fetched"]["default"] and row["ingested"]
    from camber._provenance import facility_provenance
    from camber.store import ParquetStore

    meta = ParquetStore(store).facilities_meta()["ds-synthetic-hw-plant-lockout"]
    prov = facility_provenance(meta, "ds-synthetic-hw-plant-lockout")
    assert prov["synthetic"] is True and prov["generator"]["seed"] == 133
    assert ds.ingest(DID, store, data_dir=cache).skipped  # identical inputs


def test_ingest_generates_when_nothing_was_fetched(tmp_path):
    got = ds.ingest(DID, str(tmp_path / "store"), data_dir=str(tmp_path / "cache"))
    assert got.rows > 0 and not got.skipped


def test_a_changed_generator_spec_regenerates(tmp_path):
    cache = str(tmp_path / "cache")
    ds.fetch(DID, data_dir=cache)
    man = _paths.read_manifest(cache)
    man[DID]["generator"]["version"] = 0  # written by an older generator
    _paths.write_manifest(cache, man)
    assert not ds.fetch(DID, data_dir=cache).files[0]["skipped"]


def test_from_dir_does_not_apply(tmp_path):
    with pytest.raises(ValueError, match="is synthetic"):
        adopt_local_files(ds.get(DID), tmp_path)


def test_reports_say_synthetic():
    src = [ds.get(DID).provenance()]
    text, page = data_sources_text(src), data_sources_html(src)
    assert SYNTHETIC_NOTE in text and "synthetic: " in text and "seed 133" in text
    assert "camber-synthetic-banner" in page and "<b>synthetic</b>" in page
    other = [ds.get("lbnl-boiler").provenance()]
    assert SYNTHETIC_NOTE not in data_sources_text(other)


def test_the_issues_doc_says_synthetic():
    md = render_markdown([ds.get(DID)])
    assert "Synthetic: CAMBER generates this dataset" in md


def test_cli_lists_and_describes_it(capsys, tmp_path):
    from camber.cli import main

    assert main(["datasets", "list", "--kind", "synthetic"]) == 0
    out = capsys.readouterr().out
    assert DID in out and "generated" in out and "[synthetic: generated locally]" in out
    assert "describes no real building" in out
    assert main(["datasets", "info", DID]) == 0
    out = capsys.readouterr().out
    assert "synthetic: generated locally by CAMBER (generator hw_plant_lockout, seed 133)" in out
    assert main(["datasets", "fetch", DID, "--dir", str(tmp_path), "--quiet"]) == 0
    out = capsys.readouterr().out
    assert "generating" in out and "hw_plant_lockout.csv: generated" in out


def test_lab_row_and_page_label_it(tmp_path):
    from camber.lab import LabApp
    from camber.lab._ui import lab_page_html

    app = LabApp(data_dir=str(tmp_path / "cache"), store=str(tmp_path / "store"))
    try:
        row = next(d for d in app.catalog_view()["datasets"] if d["id"] == DID)
        assert row["synthetic"] is True and row["kind"] == "synthetic"
        assert row["subsets"]["default"]["needs"]["download"] == 0
    finally:
        app.close()
    page = lab_page_html("t")
    assert '<option value="synthetic">synthetic</option>' in page
    assert "badge synthetic" in page and "how it is made" in page


def test_maintainer_scripts_skip_it():
    import importlib.util

    def load(name):
        spec = importlib.util.spec_from_file_location(name, os.path.join(_ROOT, "scripts", name))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    data = {"schema": 1, "datasets": [_entry_dict()]}
    link = load("datasets_linkcheck.py")
    assert link.run(data, opener=object()) == []
    refresh = load("datasets_refresh.py")
    logs = []
    assert refresh.refresh(data, None, pin=True, opener=object(), log=logs.append) == 0
    assert logs == [f"  {DID}: synthetic (generated by CAMBER), skipped"]
    assert json.dumps(data["datasets"][0]) == json.dumps(_entry_dict())  # nothing pinned
