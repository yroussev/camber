"""The shipped dataset catalog and its validator (camber.datasets._catalog) -- no network.

Locks the licence rules (research_only exactly when the licence is NC/ND; SPDX allowlist; https
only), that every mapping / config template / run / member the catalog names resolves, and that no
entry matches the repository's encumbered-dataset guard patterns (imported here, never by the
package).
"""

import copy
import importlib.util
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber import datasets  # noqa: E402
from camber.datasets._catalog import (  # noqa: E402
    LICENCES,
    DatasetEntry,
    is_research_only_licence,
    load_catalog_data,
    load_entries,
    package_text,
    validate_catalog,
)
from camber.model.mapping import MappingProvider  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _guard_patterns():
    path = os.path.join(_ROOT, ".github", "scripts", "site_neutrality_patterns.py")
    spec = importlib.util.spec_from_file_location("snp", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # POSIX ERE classes -> Python re
    return [rx.replace("[:space:]", r"\s") for rx, _why in mod.patterns()]


def _data():
    return copy.deepcopy(load_catalog_data())


def _entry(data, did):
    return next(d for d in data["datasets"] if d["id"] == did)


# --------------------------------------------------------------------------- the shipped catalog


def test_shipped_catalog_is_valid_and_matches_no_encumbered_pattern():
    data = load_catalog_data()
    assert validate_catalog(data, deny_patterns=_guard_patterns()) == []
    ids = [e.id for e in load_entries()]
    assert ids == [
        "lbnl-sdahu",
        "lbnl-fcu",
        "lbnl-ddahu",
        "lbnl-fpu",
        "lbnl-chiller",
        "lbnl-boiler",
        "bdg2",
    ]


def test_every_file_is_pinned_https_and_bdg2_is_share_alike():
    for e in datasets.catalog():
        for f in e.files:
            assert f["url"].startswith("https://")
            assert f["pinned"] and len(f["sha256"]) == 64 and f["size"] > 0
    bdg2 = datasets.get("bdg2")
    assert bdg2.licence == "CC-BY-SA-4.0" and bdg2.share_alike and bdg2.commercial_ok
    assert len(bdg2.files) == 19
    assert all(datasets.get(i).licence == "CC-BY-4.0" for i in ("lbnl-sdahu", "lbnl-boiler"))


def test_run_counts_and_default_subsets_are_bounded():
    counts = {e.id: len(e.ingest.get("runs", [])) for e in datasets.catalog()}
    assert counts["lbnl-fpu"] == 62 and counts["lbnl-chiller"] == 24
    assert counts["lbnl-boiler"] == 17 and counts["lbnl-sdahu"] == 21
    for e in datasets.catalog():
        if e.ingest.get("runs"):
            assert len(e.runs()) <= 8  # a handful of runs by default
            assert len(e.runs("full")) == counts[e.id]


def test_mappings_parse_and_keep_their_quirk_notes():
    for name in ("sdahu", "fcu", "ddahu", "fpu", "chiller", "boiler"):
        spec = json.loads(package_text("mappings", f"lbnl_{name}.json"))
        MappingProvider.from_dict(spec)  # every role slug is valid
        assert spec["_comment"]
    fpu = json.loads(package_text("mappings", "lbnl_fpu.json"))
    assert "SOUTH" in fpu["_comment"] and "RH_VLV_DM_S" in fpu["aliases"]
    assert "DEMAND" in json.loads(package_text("mappings", "lbnl_sdahu.json"))["_comment"]
    boiler = json.loads(package_text("mappings", "lbnl_boiler.json"))
    assert "BOI_STA_1" not in boiler["aliases"] and "ENABLE" in boiler["_comment"]
    chiller = json.loads(package_text("mappings", "lbnl_chiller.json"))
    assert chiller["aliases"]["OA_TEMP"] == "oat"  # as labelled; the quirk fixes the swap first
    assert "CWL_SEC_SW_TEMP" in chiller["_comment"]


def test_chiller_entry_fixes_both_label_swaps():
    quirks = datasets.get("lbnl-chiller").ingest["quirks"]
    swaps = {tuple(q["columns"]) for q in quirks if q["op"] == "swap" and q["action"] == "fix"}
    assert swaps == {("OA_TEMP", "OA_TEMP_WB"), ("CWL_SEC_SW_TEMP", "CWL_SEC_RW_TEMP")}


def test_config_templates_point_at_the_entry_facility():
    for e in datasets.catalog():
        cfg = json.loads(package_text("configs", e.suggested_analyses["config_template"]))
        assert cfg["source"]["kind"] == "store"
        assert cfg["source"]["facility_id"].startswith(e.ingest["facility"])


# --------------------------------------------------------------------------- entry helpers


def test_entry_helpers():
    e = datasets.get("lbnl-sdahu")
    assert e.file("LBNL_FDD_Data_Sets_SDAHU.zip")["archive"] == "zip"
    with pytest.raises(KeyError):
        e.file("nope")
    with pytest.raises(KeyError, match="known"):
        e.subset("tiny")
    assert e.download_bytes() == sum(f["size"] for f in e.files)
    assert [r["id"] for r in e.runs()][:2] == ["fault_free", "damper_stuck_010"]
    prov = e.provenance()
    assert prov["redistribution"] == "allowed" and prov["dois"] == ["10.25984/1881324"]
    d = e.as_dict()
    assert DatasetEntry.from_dict(d) == e
    assert "licence_check" in d
    assert not e.research_only
    bdg2 = datasets.get("bdg2")
    assert len(bdg2.subset_files()) == 4 and len(bdg2.subset_files("full")) == 19


def test_catalog_filters_and_get():
    assert len(datasets.catalog()) == 7
    assert [e.id for e in datasets.catalog(kind="real")] == ["bdg2"]
    assert "bdg2" not in [e.id for e in datasets.catalog(labeled=True)]
    assert len(datasets.catalog(labeled=False)) == 1
    assert len(datasets.catalog(licence="commercial")) == 7
    with pytest.raises(ValueError):
        datasets.catalog(licence="free")
    with pytest.raises(KeyError, match="known"):
        datasets.get("nope")


def test_load_entries_raises_on_an_invalid_catalog():
    data = _data()
    data["datasets"][0]["licence"] = "WTFPL"
    with pytest.raises(ValueError, match="invalid dataset catalog"):
        load_entries(data)


# --------------------------------------------------------------------------- validator rules


def test_research_only_iff_nc_or_nd():
    assert is_research_only_licence("CC-BY-NC-ND-4.0")
    assert is_research_only_licence("CC-BY-ND-4.0")
    assert not is_research_only_licence("CC-BY-SA-4.0")
    for lic in LICENCES:
        assert LICENCES[lic][0] == (not is_research_only_licence(lic))
    data = _data()
    e = _entry(data, "lbnl-fcu")
    e["licence"] = "CC-BY-NC-4.0"  # NC but still marked open
    assert any("contradicts" in x for x in validate_catalog(data))
    e["access"] = "research_only"
    assert validate_catalog(data) == []
    e["licence"] = "CC-BY-4.0"  # open licence hidden behind the gate
    assert any("contradicts" in x for x in validate_catalog(data))


@pytest.mark.parametrize(
    "mutate, needle",
    [
        (lambda e: e.update(licence="WTFPL"), "not an allowed SPDX"),
        (lambda e: e.update(access="members"), "access must be"),
        (lambda e: e.update(kind="guess"), "kind must be"),
        (lambda e: e.update(landing_url="http://x.org"), "landing_url must be https"),
        (lambda e: e.update(verified_on="yesterday"), "verified_on"),
        (lambda e: e.update(id="Bad_ID"), "id must match"),
        (lambda e: e.pop("citation"), "missing 'citation'"),
        (lambda e: e.update(licence_check={"url": "ftp://x"}), "licence_check.url"),
        (lambda e: e["files"][0].update(url="http://insecure/x.zip"), "must be https"),
        (lambda e: e["files"][0].update(sha256="ABC"), "64 lowercase hex"),
        (lambda e: e["files"][0].update(size=-1), "positive integer"),
        (lambda e: e["files"][0].update(sha256=None), "'pinned' must be true"),
        (lambda e: e["files"][0].update(archive="rar"), "archive must be"),
        (lambda e: e["files"][0].update(archive=None), "only applies to an archive"),
        (lambda e: e["files"][0].pop("name"), "file without a name"),
        (lambda e: e["files"].append(dict(e["files"][0])), "duplicate file"),
        (lambda e: e.update(files=[]), "at least one file"),
        (lambda e: e["ingest"].update(adapter="magic"), "ingest.adapter"),
        (lambda e: e["ingest"].update(facility="lbnl"), "must start with 'ds-'"),
        (lambda e: e["ingest"].update(mapping="nope.json"), "is not shipped"),
        (lambda e: e["ingest"]["quirks"].append({"op": "exec"}), "unknown quirk op"),
        (lambda e: e["ingest"]["runs"][0].update(id="bad id"), "bad run id"),
        (lambda e: e["ingest"]["runs"].append(dict(e["ingest"]["runs"][0])), "duplicate run"),
        (lambda e: e["ingest"]["runs"][0].update(file="x.zip"), "run file"),
        (lambda e: e["ingest"]["runs"][0].update(member="x.csv"), "is not in"),
        (lambda e: e["ingest"]["runs"][0].pop("class"), "needs 'equip' and 'class'"),
        (lambda e: e["ingest"]["runs"][0].pop("label"), "needs a 'label'"),
        (lambda e: e["ingest"]["derived"][0].update(op="blend"), "unknown derived op"),
        (lambda e: e["ingest"]["derived"][0].update(base="zzz"), "splice base"),
        (lambda e: e["ingest"]["derived"][0].update(onset="soon"), "onset must be"),
        (lambda e: e["subsets"].pop("full"), "'full' subset"),
        (lambda e: e["subsets"]["default"].update(files=["x"]), "subset file"),
        (lambda e: e["subsets"]["default"].update(runs=["zzz"]), "subset run"),
        (lambda e: e["suggested_analyses"].update(config_template="x.json"), "config template"),
    ],
)
def test_validator_rejects(mutate, needle):
    data = _data()
    mutate(_entry(data, "lbnl-sdahu"))
    errs = validate_catalog(data)
    assert any(needle in x for x in errs), errs


def test_validator_document_level_errors():
    assert validate_catalog([]) and validate_catalog({"schema": 2, "datasets": [{}]})
    assert validate_catalog({"schema": 1, "datasets": []})
    assert any("must be an object" in x for x in validate_catalog({"schema": 1, "datasets": [1]}))
    data = _data()
    data["datasets"].append(copy.deepcopy(data["datasets"][0]))
    assert any("duplicate id" in x for x in validate_catalog(data))


def test_deny_patterns_catch_an_encumbered_name():
    data = _data()
    _entry(data, "bdg2")["summary"] += " (see the ENCUMBERED-42 chiller set)"
    errs = validate_catalog(data, deny_patterns=[r"encumbered-?42"])
    assert any("encumbered-dataset pattern" in x for x in errs)


def test_package_does_not_import_the_repo_guard():
    pkg = os.path.join(_ROOT, "camber", "datasets")
    for fn in os.listdir(pkg):
        if fn.endswith(".py"):
            src = open(os.path.join(pkg, fn), encoding="utf-8").read()
            assert ".github" not in src and "site_neutrality" not in src
    text = open(os.path.join(pkg, "catalog.json"), encoding="utf-8").read()
    for rx in _guard_patterns():  # incl. the third-party-host rules (no exemption until 0.87)
        assert re.search(rx, text, re.IGNORECASE) is None, rx
