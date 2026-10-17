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


# ------------------------------------------- every synthetic entry (0.104 #137, 0.105 scaffold)

#: one expectation file per synthetic entry, ``tests/golden/synthetic/<id>.json``: its generator,
#: seed and version, its CSV header, its scenario plants in order, the rows each plant has and a
#: size cap. A new entry adds its own file; nothing here needs editing. An entry whose scenario
#: plants are several pieces of equipment each (0.105, #145) also lists its ingest ``runs`` and
#: the ``equipment`` count; both default to one run and one equipment per scenario.
_EXPECT_DIR = os.path.join(_ROOT, "tests", "golden", "synthetic")
_SYNTHETIC_IDS = [e.id for e in ds.catalog(kind="synthetic")]


def _expect(did: str) -> dict:
    with open(os.path.join(_EXPECT_DIR, f"{did}.json"), encoding="utf-8") as fh:
        return json.load(fh)


def test_every_synthetic_entry_has_an_expectation_file_and_its_own_generator():
    assert len(_SYNTHETIC_IDS) >= 3
    files = sorted(f[:-5] for f in os.listdir(_EXPECT_DIR) if f.endswith(".json"))
    assert files == sorted(_SYNTHETIC_IDS)  # one per entry, and none for a missing entry
    used = [generator_spec(ds.get(did))["name"] for did in _SYNTHETIC_IDS]
    assert sorted(used) == sorted(GENERATORS)  # every generator is used, each by one entry


@pytest.mark.parametrize("did", _SYNTHETIC_IDS)
def test_each_synthetic_entry_is_deterministic_small_and_ingests(did, tmp_path):
    want = _expect(did)
    gen, plants = want["generator"], want["scenarios"]
    e = ds.get(did)
    assert e.synthetic and e.licence == SYNTHETIC_LICENCE and e.access == "open"
    assert generator_spec(e) == {"name": gen, "seed": want["seed"], "version": want["version"]}
    assert [f["name"] for f in e.files] == list(GENERATORS[gen].files)
    assert [r["id"] for r in e.ingest["runs"]] == want.get("runs", plants)
    name = GENERATORS[gen].files[0]
    a = generate_files(e, str(tmp_path / "a"))[name]
    b = generate_files(e, str(tmp_path / "b"))[name]
    with open(a, "rb") as fa, open(b, "rb") as fb:
        assert fa.read() == fb.read()
    assert os.path.getsize(a) < want["max_bytes"]
    import pandas as pd

    raw = pd.read_csv(a)
    assert list(raw.columns) == want["header"]
    assert list(dict.fromkeys(raw["scenario"])) == plants
    assert (raw.groupby("scenario").size() == want["rows_per_scenario"]).all()
    got = ds.ingest(did, str(tmp_path / "store"), data_dir=str(tmp_path / "cache"))
    assert got.facilities == [f"ds-{did}"] and got.equipment == want.get("equipment", len(plants))


# --------------------------------------------------------- generator modules (0.105 scaffold)


def test_register_refuses_a_taken_name_or_a_non_generator():
    from camber.datasets._synthetic import Generator, register

    gen = GENERATORS["hw_plant_lockout"]
    with pytest.raises(ValueError, match="already registered"):
        register("hw_plant_lockout", gen)
    with pytest.raises(TypeError, match="must be a Generator"):
        register("not_a_generator", {"version": 1})
    assert "not_a_generator" not in GENERATORS
    assert isinstance(gen, Generator)


def test_a_generator_module_registers_itself_when_discovered(tmp_path, monkeypatch):
    """A ``_synthetic_<name>.py`` module in the package is imported by discovery and registers
    its generator; other modules are not imported."""
    import camber.datasets as pkg
    from camber.datasets import _synthetic

    (tmp_path / "_synthetic_scaffold_probe.py").write_text(
        "from ._synthetic import Generator, _noise, register\n"
        "def _build(seed):\n"
        "    return {'probe.csv': ','.join(f'{v:.3f}' for v in _noise(3, seed, 1)) + '\\n'}\n"
        "register('scaffold_probe', Generator(version=1, files=('probe.csv',), build=_build))\n",
        encoding="utf-8",
    )
    (tmp_path / "_not_a_generator.py").write_text("raise RuntimeError('imported')\n")
    monkeypatch.setattr(pkg, "__path__", [*pkg.__path__, str(tmp_path)])
    name = "camber.datasets._synthetic_scaffold_probe"
    try:
        assert "_synthetic_scaffold_probe" in _synthetic._generator_modules()
        _synthetic._discover()
        assert GENERATORS["scaffold_probe"].files == ("probe.csv",)
        text = GENERATORS["scaffold_probe"].build(7)["probe.csv"]
        assert text == GENERATORS["scaffold_probe"].build(7)["probe.csv"]
        _synthetic._discover()  # discovery again is a no-op (the module is imported once)
    finally:
        GENERATORS.pop("scaffold_probe", None)
        sys.modules.pop(name, None)


# ------------------------------------------ generator version 2 (0.106, #184 and #186 points)


def _frame(gen: str, seed: int):
    import io

    import pandas as pd

    (text,) = GENERATORS[gen].build(seed).values()
    return pd.read_csv(io.StringIO(text))


def _aliases(name: str) -> dict:
    path = os.path.join(_ROOT, "camber", "datasets", "mappings", name)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["aliases"]


def test_chw_plant_multi_tower_trends_its_condenser_flow():
    """One 600 gpm condenser pump per running chiller, read about 1 % noisy; 0 with none on."""
    df = _frame("chw_plant_multi", 145)
    count = df["CH1_STS"] + df["CH2_STS"]
    on = count > 0
    ratio = df.loc[on, "CW_GPM"] / (600.0 * count[on])
    assert ratio.between(0.989, 1.011).all()
    assert (df.loc[~on, "CW_GPM"] == 0).all()
    assert set(count[df["scenario"] == "over_staged"]) <= {0, 2}  # both pumps whenever it runs
    assert _aliases("synthetic_chw_multi_tower.json")["CW_GPM"] == "cw_flow"


def test_g36_vav_fleet_boxes_trend_their_active_setpoint_and_loops():
    """The active airflow setpoint sits between the minimum and the cooling maximum with the fan
    on (0 off), the loops are 0-100 %, the cooling loop over 95 % is the tier-1 SAT request, and
    only heat_cool's fighting boxes run both loops at once."""
    df = _frame("g36_vav_fleet", 144)
    fan = df["SF_STS"] == 1
    vmax = {1: 1000, 2: 900, 3: 800, 4: 1100, 5: 700, 6: 600}
    for z, top in vmax.items():
        sp, vmin = df[f"ZN{z}_FLOW_SP"], df[f"ZN{z}_FLOW_MIN"]
        assert (sp[fan] >= vmin[fan]).all() and (sp[fan] <= top).all()
        assert (sp[~fan] == 0).all()
        for loop in ("HTG_LOOP", "CLG_LOOP"):
            col = df[f"ZN{z}_{loop}"]
            assert col.between(0.0, 100.0).all() and (col[~fan] == 0).all()
        both = (df[f"ZN{z}_HTG_LOOP"] > 0) & (df[f"ZN{z}_CLG_LOOP"] > 0)
        assert set(df.loc[both, "scenario"]) <= {"heat_cool"}
    # the healthy air handler's summed tier-1 requests are the boxes' cooling loops over 95 %
    # wherever no box is warm enough to ask for more
    h = df[(df["scenario"] == "healthy") & fan]
    warm = sum((h[f"ZN{z}_T"] - h[f"ZN{z}_CLG_SP"] >= 3.0) for z in vmax).astype(bool)
    tier1 = sum((h[f"ZN{z}_CLG_LOOP"] > 95.0).astype(int) for z in vmax)
    assert (tier1[~warm] == h.loc[~warm, "SAT_REQ"]).mean() > 0.95
    zone = _aliases("synthetic_g36_fleet_zone.json")
    assert zone["ZN{n}_FLOW_SP"] == "airflow_sp"
    assert zone["ZN{n}_FLOW_MIN"] == "airflow_min_sp"
    assert zone["ZN{n}_HTG_LOOP"] == "zone_heat_loop"
    assert zone["ZN{n}_CLG_LOOP"] == "zone_cool_loop"
