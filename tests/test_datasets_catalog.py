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
    is_pinned_url,
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
    # POSIX ERE classes -> Python re. Rules that exempt the catalog (the dataset-host rules) do
    # not apply to it; every other rule, the licence-encumbered ones included, does.
    return [
        rx.replace("[:space:]", r"\s")
        for rx, _why, exempt in mod.rules()
        if "camber/datasets/catalog.json" not in exempt
    ]


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
        # 0.89 intake A: DCV / CO2
        "lbnl-b59",
        "finnish-dcv",
        "b4b-windesheim",
        # 0.89 intake B: AHU and refrigerant side
        "nuig-ahu101",
        "irish-ahu",
        "nist-heatpump-fdd",
        "nist-ibal",
        # 0.89 intake C: real buildings and refrigeration
        "robod",
        "sdu-ou44",
        "ornl-frp-ops",
        "ornl-supermarket-fdd",
        # 0.89 intake D: multi-zone VAV and the research-only tier
        "ornl-frp-vav",
        "rbc-g36-ahu",
        "at-30bldg-sensors",
        # 0.92 intake: meter data
        "cofactor-drammen",
    ]


def test_every_file_is_pinned_https_and_bdg2_is_share_alike():
    for e in datasets.catalog():
        for f in e.files:
            # a manual entry's files come from a portal export: no direct URL, but still pinned
            assert f["url"].startswith("https://") if f["url"] or not e.manual else True
            assert f["pinned"] and len(f["sha256"]) == 64 and f["size"] > 0
    bdg2 = datasets.get("bdg2")
    assert bdg2.licence == "CC-BY-SA-4.0" and bdg2.share_alike and bdg2.commercial_ok
    assert len(bdg2.files) == 19
    assert all(datasets.get(i).licence == "CC-BY-4.0" for i in ("lbnl-sdahu", "lbnl-boiler"))


#: A default subset is a quick first ingest: small in bytes, whatever its run count (a real
#: building's runs are its equipment, a simulated collection's its scenarios).
DEFAULT_STORE_BYTES_MAX = 100_000_000


def test_run_counts_and_default_subsets_are_bounded():
    counts = {e.id: len(e.ingest.get("runs", [])) for e in datasets.catalog()}
    assert counts["lbnl-fpu"] == 62 and counts["lbnl-chiller"] == 24
    assert counts["lbnl-boiler"] == 17 and counts["lbnl-sdahu"] == 21
    for e in datasets.catalog():
        assert e.store_bytes() <= DEFAULT_STORE_BYTES_MAX, e.id
        assert e.store_bytes() <= e.store_bytes("full"), e.id
        if e.ingest.get("runs"):
            # full = every run whose file the full subset fetches (a manual entry's default and
            # full windows are separate portal exports, each read by its own run)
            full_files = {f["name"] for f in e.subset_files("full")}
            reachable = [r for r in e.ingest["runs"] if r["file"] in full_files]
            assert len(e.runs("full")) == len(reachable) <= counts[e.id]
            assert len(reachable) == counts[e.id] or e.manual


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
    assert len(datasets.catalog()) == 22
    assert [e.id for e in datasets.catalog(kind="real")] == [
        "bdg2",
        "lbnl-b59",
        "b4b-windesheim",
        "nuig-ahu101",
        "irish-ahu",
        "robod",
        "sdu-ou44",
        "ornl-frp-ops",
        "ornl-frp-vav",
        "at-30bldg-sensors",
        "cofactor-drammen",
    ]
    assert [e.id for e in datasets.catalog(kind="lab")] == [
        "finnish-dcv",
        "nist-heatpump-fdd",
        "nist-ibal",
        "ornl-supermarket-fdd",
    ]
    assert "bdg2" not in [e.id for e in datasets.catalog(labeled=True)]
    assert len(datasets.catalog(labeled=False)) == 12
    assert len(datasets.catalog(labeled=True)) == 10
    # the open tier: research-only entries (an NC/ND licence, or a stated access_reason) are out
    commercial = datasets.catalog(licence="commercial")
    assert [e.id for e in datasets.catalog() if e.research_only] == [
        "rbc-g36-ahu",  # CC BY, held research-only for its stated access_reason
        "at-30bldg-sensors",  # CC-BY-NC-SA
    ]
    assert len(commercial) == 20 and all(not e.research_only for e in commercial)
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
    e["access_reason"] = "no reason is needed"  # the NC licence already gates it
    assert any("access_reason only" in x for x in validate_catalog(data))
    del e["access_reason"]
    e["licence"] = "CC-BY-4.0"  # an open licence behind the gate needs a stated reason
    assert any("needs an access_reason" in x for x in validate_catalog(data))
    e["access_reason"] = "bundles third-party files whose licence CAMBER cannot verify"
    assert validate_catalog(data) == []
    ent = DatasetEntry.from_dict(e)
    assert ent.research_only and ent.commercial_ok  # the licence allows it; CAMBER holds it
    assert ent.provenance()["access_reason"] == e["access_reason"]
    assert ent.provenance()["redistribution"] == "prohibited"
    e["access"] = "open"  # a reason never opens anything, and is meaningless on an open entry
    assert any("access_reason only" in x for x in validate_catalog(data))
    e["licence"] = "CC-BY-NC-4.0"  # open access with an NC/ND licence stays impossible
    assert any("contradicts" in x for x in validate_catalog(data))
    del e["access_reason"]
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
        (lambda e: e["subsets"]["full"].pop("store_bytes_estimate"), "store_bytes_estimate"),
        (lambda e: e["ingest"]["quirks"][0].pop("issue"), "must link to a described data issue"),
        (lambda e: e["ingest"]["quirks"][0].update(issue="nope"), "issue='nope'"),
        (lambda e: e["data_issues"][0].update(evidence="it is wrong"), "must be quantitative"),
        (lambda e: e["data_issues"][0].update(columns=[]), "'columns' must list"),
        (lambda e: e["data_issues"][0].update(handling="maybe"), "handling must be one of"),
        (lambda e: e["data_issues"][0].pop("handling_note"), "missing 'handling_note'"),
        (lambda e: e["data_issues"][0].update(id="Bad Id"), "data issue id"),
        (lambda e: e["data_issues"].append(dict(e["data_issues"][0])), "duplicate data issue"),
        (
            lambda e: e["data_issues"][0]["contradicts"].update(citation="the PDF"),
            "cite the documentation by DOI",
        ),
        (lambda e: e["data_issues"][0]["contradicts"].pop("document"), "contradicts.document"),
        (lambda e: e["data_issues"][0].update(handling="annotate"), "whose handling is not 'fix'"),
        (lambda e: e["ingest"].update(quirks=[]), "needs a fix quirk"),
        (lambda e: e["ingest"]["runs"][0].update(exclude="sa-ra-cfm-units"), "not an 'exclude'"),
        (lambda e: e["ingest"]["runs"][0].update(exclude=3), "must name the data issue"),
        (lambda e: e["ingest"].update(timestamp_format="MM/DD"), "timestamp_format"),
        (lambda e: e["ingest"].update(recode={"SYS_CTL": {"two": 0}}), "is not a number"),
        (lambda e: e["ingest"].update(recode={"SYS_CTL": {"2": "off"}}), "must be a number"),
        (lambda e: e["ingest"].update(recode={"SYS_CTL": {}}), "must map source values"),
        (lambda e: e["ingest"].update(derive=[{"column": "X", "sum": ["A"]}]), "derive 'X' needs"),
        (lambda e: e["ingest"].update(derive=[{"sum": ["A", "B"]}]), "derive needs a 'column'"),
        (
            lambda e: e["ingest"].update(derive=[{"column": "X", "above": ["A", "0"]}]),
            "derive 'X' needs",
        ),
        (lambda e: e["ingest"].update(units={"oat": "furlongs"}), "unsupported source unit"),
        (lambda e: e["labels"]["targets"].update(leaking_valve="nope"), "not a declared fault"),
        (lambda e: e["labels"]["targets"].update(leaking_valve=[]), "must be a fault type"),
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
    for rx in _guard_patterns():  # every rule that does not exempt the catalog
        assert re.search(rx, text, re.IGNORECASE) is None, rx


# --------------------------------------------------------------------------- data issues


def _exclude_issue(runs=None, columns=None, analyses=None) -> dict:
    ex = {k: v for k, v in (("runs", runs), ("columns", columns), ("analyses", analyses)) if v}
    return {
        "id": "not-a-fault",
        "title": "a labelled fault run that carries no fault",
        "columns": ["OA_TEMP"],
        "evidence": "within 0.33 F of the fault-free run",
        "contradicts": {"document": "inventory Table 3", "citation": "doi:10.25984/1881324"},
        "handling": "exclude",
        "handling_note": "not scored",
        "exclude": ex,
    }


def test_exclude_issue_must_say_what_it_excludes_and_runs_must_point_back():
    data = _data()
    e = _entry(data, "lbnl-sdahu")
    e["data_issues"].append(_exclude_issue())
    assert any("must say what it excludes" in x for x in validate_catalog(data))
    e["data_issues"][-1] = _exclude_issue(runs=["coi_stuck_010"])
    assert any("must carry exclude='not-a-fault'" in x for x in validate_catalog(data))
    run = next(r for r in e["ingest"]["runs"] if r["id"] == "coi_stuck_010")
    run["exclude"] = "not-a-fault"
    assert validate_catalog(data) == []
    e["data_issues"][-1] = _exclude_issue(runs=["zzz"])
    assert any("is not a run" in x for x in validate_catalog(data))
    e["data_issues"][-1] = _exclude_issue(columns=["MA_TEMP"], analyses=["x"])
    run.pop("exclude")
    assert any("is mapped in" in x for x in validate_catalog(data))
    e["data_issues"][-1] = _exclude_issue(analyses=["the EUI rollup"])
    assert validate_catalog(data) == []


def test_every_quirk_links_to_its_issue_and_every_fix_issue_has_a_fix():
    for e in datasets.catalog():
        ids = {i["id"] for i in e.data_issues}
        fixes = {q["issue"] for q in e.ingest.get("quirks") or [] if q["action"] == "fix"}
        for q in e.ingest.get("quirks") or []:
            assert q["issue"] in ids, (e.id, q)
        for i in e.data_issues:
            assert (i["handling"] == "fix") == (i["id"] in fixes), (e.id, i["id"])
            cite = i["contradicts"]["citation"]
            # a DOI whenever the entry has one; else a URL pinned to a commit or a version
            if e.dois:
                assert re.search(r"10\.\d{4,9}/", cite), (e.id, i["id"])
            else:
                assert re.search(r"10\.\d{4,9}/", cite) or any(
                    is_pinned_url(u) for u in re.findall(r"https://\S+", cite)
                ), (e.id, i["id"])
        assert e.provenance()["data_issues"] == [
            {k: i[k] for k in ("id", "title", "handling")} for i in e.data_issues
        ]


def test_docs_datasets_md_matches_the_catalog():
    """docs/DATASETS.md's data-issues section is generated; it must match catalog.json."""
    from camber.datasets._issues import BEGIN, END, render_markdown, splice_markdown

    doc = open(os.path.join(_ROOT, "docs", "DATASETS.md"), encoding="utf-8").read()
    assert BEGIN in doc and END in doc
    assert splice_markdown(doc, render_markdown(load_entries())) == doc, (
        "docs/DATASETS.md is out of date: run python scripts/datasets_issues_doc.py"
    )
    with pytest.raises(ValueError, match="markers"):
        splice_markdown("no markers here", "x")


def test_issues_doc_script_check_and_write(tmp_path, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location(
        "issues_doc", os.path.join(_ROOT, "scripts", "datasets_issues_doc.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main(["--check"]) == 0
    doc = tmp_path / "DATASETS.md"
    from camber.datasets._issues import BEGIN, END

    doc.write_text(f"# x\n\n{BEGIN}\nstale\n{END}\n\ntail\n", encoding="utf-8")
    monkeypatch.setattr(mod, "DOC", str(doc))
    assert mod.main(["--check"]) == 1
    assert mod.main([]) == 0
    text = doc.read_text(encoding="utf-8")
    assert "stale" not in text and "lbnl-sdahu" in text and text.endswith("\ntail\n")
    assert mod.main(["--check"]) == 0


def test_store_estimates_are_per_subset():
    for e in datasets.catalog():
        assert e.store_bytes("full") >= e.store_bytes() > 0
        assert e.store_bytes_estimate == e.store_bytes()
    d = _data()
    _entry(d, "bdg2")["subsets"]["default"].pop("store_bytes_estimate")
    raw = _entry(d, "bdg2")
    raw["subsets"]["full"]["store_bytes_estimate"] = 5
    assert DatasetEntry.from_dict(raw).store_bytes() is None


def test_a_research_only_reason_reaches_the_banner_and_the_source_block():
    from camber.report.audit import RESEARCH_ONLY_BANNER, data_sources_html, data_sources_text

    rbc = datasets.get("rbc-g36-ahu").provenance()  # CC BY, held research-only for a reason
    txt = data_sources_text([rbc])
    assert "NON-COMMERCIAL / RESEARCH USE ONLY" in txt and "01_RBC-ASHRAE1312" in txt
    assert RESEARCH_ONLY_BANNER not in txt  # its licence does not forbid commercial use
    assert "held research-only:" in txt and "licence: CC-BY-4.0 (research_only)" in txt
    html = data_sources_html([rbc])
    assert html.count("camber-nc-banner") == 1 and "held research-only:" in html
    nc = datasets.get("at-30bldg-sensors").provenance()  # NC licence: the standard banner
    assert RESEARCH_ONLY_BANNER in data_sources_text([nc])
    both = data_sources_text([nc, rbc])
    assert RESEARCH_ONLY_BANNER in both and "Also held research-only by CAMBER" in both
