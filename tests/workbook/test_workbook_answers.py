"""Workbook answer keys (#79): every exercise's expected answers still hold.

Offline (always): each exercise's synthetic stand-in -- facilities shaped like its datasets --
runs through the exercise's own configs. Network (``pytest -m network``, deselected by default):
the real catalog data is fetched, ingested and checked against the same expectations.

For the network run, ``CAMBER_WORKBOOK_STORE`` reuses a store: a dataset already ingested there
with the same subset and the same inputs and mapping is not ingested again (a changed mapping or
subset re-ingests it). Each ``Run(subset=...)`` is honoured: an exercise whose runs use a
non-default subset (the plant exercises use ``full``) gets its own sibling store,
``<store>-subset-<name>``, so the default and the full ingest of a dataset never replace each
other. Each test ingests only its own exercise's datasets. Research-only datasets are never
fetched here; an exercise's optional research-only extras are not network-tested.

``CAMBER_WORKBOOK_FROM_DIR`` takes the publisher files from a local directory instead of
downloading them (``ingest --from-dir``; pinned files are still size- and sha256-verified). A
``<dir>/<dataset-id>/`` subdirectory is used when it exists, else ``<dir>`` itself. Files are
found by their catalog path or their bare catalog name; a checkout's ``examples/_data`` uses other
names in places (e.g. ``LBNL_Chiller_Plant.zip``), so build the directory from symlinks. A
``manual: true`` dataset (``lbnl-b59``, downloaded by hand) is never fetched: without its files
under ``CAMBER_WORKBOOK_FROM_DIR`` (or already in the cache), its exercises are skipped with a
message saying what to download. The files the exercises need, per dataset:

- ``b4b-windesheim``: ``preprocessed_data/b4b_preprocess_properties.zip``,
  ``metadata/b4b-room-metadata.zip``, ``README.md``
- ``bdg2``: ``metadata.csv``, ``weather.csv``, ``cleaned/electricity_cleaned.csv``,
  ``cleaned/chilledwater_cleaned.csv``
- ``cofactor-drammen``: ``Cofactor_Drammen_Buildings_45_v3.zip``
- ``finnish-dcv``: ``training_data_1.csv``, ``training_data_2.csv``, ``ventilation_test.csv``
- ``irish-ahu``: ``Data_Article_Dataset.csv``
- ``lbnl-b59`` (manual): ``Building_59.zip``, ``README_Dryad_Bldg59.txt``
- ``lbnl-boiler``: ``LBNL_FDD_Data_Sets_Boiler_Plant.zip``,
  ``LBNL_FDD_Data_Sets_Boiler_Plant_ttl.zip``
- ``lbnl-chiller``: ``LBNL_FDD_Data_Sets_Chiller_Plant.zip``,
  ``LBNL_FDD_Data_Sets_Chiller_Plant_ttl.zip``
- ``lbnl-ddahu``: ``LBNL_FDD_Data_Sets_DDAHU.zip``, ``LBNL_FDD_Data_Sets_DDAHU_ttl.zip``
- ``lbnl-fpu``: ``LBNL_FDD_Data_Sets_FPU.zip``, ``LBNL_FDD_Data_Sets_FPU_ttl.zip``
- ``lbnl-sdahu``: ``LBNL_FDD_Data_Sets_SDAHU.zip``, ``LBNL_FDD_Data_Sets_SDAHU_ttl.zip``
- ``nuig-ahu101``: ``nuig-data.tar.gz``, ``NUIG_variables_MR1_MP5_AHU101 focus v1.xlsx``
- ``ornl-frp-ops``: ``Building_Base_Heating.csv``, ``Weather_Base_Heating.csv``,
  ``Building_SB_Heating.csv``, ``Weather_SB_Heating.csv``
- ``ornl-frp-vav``: ``Set_03_Damper Tests_Room 205.xlsx``
- ``sdu-ou44``: ``Dataset.zip``
- ``valladolid-uva``: ``db_building_A.csv``, ``db_building_B.csv``

The small ``*_ttl.zip`` Brick files of the LBNL sets are separate catalog downloads; fetch them
with ``camber datasets fetch`` if a local copy lacks them (a test keeps this list in step with
the catalog).
"""

import dataclasses
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _workbook import (  # noqa: E402
    REAL,
    STANDIN,
    Finding,
    Metric,
    Run,
    check_exercise,
    load_exercises,
    stale_message,
)

from camber import datasets  # noqa: E402
from camber.store import ParquetStore  # noqa: E402

EXERCISES = load_exercises()
IDS = [e.id for e in EXERCISES]


@pytest.mark.parametrize("ex", EXERCISES, ids=IDS)
def test_answers_hold_on_the_synthetic_stand_in(ex, tmp_path):
    store = ParquetStore(str(tmp_path / "store"))
    ex.standin(store)
    problems = check_exercise(ex, STANDIN, store.root)
    assert not problems, stale_message(ex, STANDIN, problems)


def test_a_stale_answer_fails_loudly_naming_the_exercise(tmp_path):
    ex = next(e for e in EXERCISES if e.id == "air-economizer")
    store = ParquetStore(str(tmp_path / "store"))
    ex.standin(store)
    wrong = dataclasses.replace(
        ex,
        expect=(
            Finding("outdoor_air_fraction", "AHU__fault_free"),  # it is ok, not fired
            Metric("outdoor_air_fraction", "AHU__damper_stuck_075", "median_oaf_cooling", 20, 1),
            Finding("no_such_rule", "AHU__fault_free", severity=("fault",)),
        ),
    )
    problems = check_exercise(wrong, STANDIN, store.root)
    assert len(problems) == 3
    msg = stale_message(wrong, STANDIN, problems)
    assert "'air-economizer' IS STALE" in msg and "docs/workbook/air-economizer.md" in msg
    assert "tests/workbook/exercises/air_economizer.py" in msg and "instructor.md" in msg
    assert "CAMBER says ok" in problems[0] and "expected 20" in problems[1]
    assert "CAMBER says absent" in problems[2]


# --------------------------------------------------------------------------- real data

DEFAULT = "default"


def exercise_subsets(ex) -> dict:
    """``{dataset: subset}`` for each of ``ex``'s core datasets (``default`` unless a run says
    otherwise). One exercise cannot run one dataset at two subsets: that is a declaration error."""
    out = dict.fromkeys(ex.datasets, DEFAULT)
    seen: dict = {}
    for r in ex.runs:
        if seen.setdefault(r.dataset, r.subset) != r.subset:
            raise ValueError(
                f"{ex.id}: {r.dataset} is run at two subsets ({seen[r.dataset]}, "
                f"{r.subset}); split the exercise's runs into one subset each"
            )
        out[r.dataset] = r.subset
    return out


def store_for(root: str, ex) -> str:
    """The store ``ex`` runs against: ``root`` when all its runs use the default subsets, else the
    sibling ``<root>-subset-<name>`` (one per non-default subset), so the default and a larger
    ingest of the same dataset (same facility ids) never overwrite each other."""
    other = sorted({s for s in exercise_subsets(ex).values() if s != DEFAULT})
    if len(other) > 1:
        raise ValueError(f"{ex.id}: runs mix the subsets {other}; use one non-default subset")
    return root if not other else f"{os.path.normpath(root)}-subset-{other[0]}"


def ingested_current(store_root: str, did: str, subset: str) -> bool:
    """Whether ``did`` is in the store as a real ingest of ``subset`` whose recorded inputs, with
    the catalog's *current* ingest spec and mapping, still give its content hash -- so a changed
    subset or mapping (e.g. new roles in a mapping file) is re-ingested, not silently reused."""
    from camber.datasets._ingest import content_hash, dataset_meta, mapping_texts

    entry = datasets.get(did)
    blocks = [
        b
        for b in (dataset_meta(m) for m in ParquetStore(store_root).facilities_meta().values())
        if b.get("dataset_id") == did and not b.get("standin")
    ]
    if not blocks:
        return False
    text = mapping_texts(entry)
    for b in blocks:
        if b.get("subset") != subset:
            return False
        corrected = not str(b.get("corrections", "applied")).startswith("skipped")
        want = content_hash(entry, subset, b.get("sha256") or {}, text, corrections=corrected)
        if b.get("content_hash") != want:
            return False
    return True


def local_dir(did: str) -> str | None:
    """The ``CAMBER_WORKBOOK_FROM_DIR`` directory for ``did`` (its ``<dataset-id>/`` subfolder
    when present), or ``None`` when the variable is unset."""
    from_dir = os.environ.get("CAMBER_WORKBOOK_FROM_DIR")
    if not from_dir:
        return None
    sub = os.path.join(from_dir, did)
    return sub if os.path.isdir(sub) else from_dir


def _missing_local(entry, subset: str, where: str | None) -> list:
    from camber.datasets._ops import _local_source

    files = [f["name"] for f in entry.subset_files(subset)]
    if where is None or not os.path.isdir(where):
        return files
    return [n for n in files if _local_source(where, n) is None]


def _in_cache(entry, subset: str) -> bool:
    from camber.datasets import _paths
    from camber.datasets._ingest import verified_inputs

    try:
        verified_inputs(entry, subset, _paths.data_dir(None))
    except (FileNotFoundError, ValueError):
        return False
    return True


class _RealStore:
    """The real catalog data, ingested on first use per (store, dataset, subset)."""

    def __init__(self, root: str):
        self.root, self._done = root, set()

    def prepare(self, ex) -> str:
        """Ingest ``ex``'s core datasets at the subsets its runs use; returns the store to run."""
        root = store_for(self.root, ex)
        for did, subset in exercise_subsets(ex).items():
            self.ensure(did, subset, root)
        return root

    def ensure(self, did: str, subset: str = DEFAULT, root: str | None = None) -> None:
        root = root or self.root
        key = (root, did, subset)
        if key in self._done:
            return
        entry = datasets.get(did)
        if entry.research_only:  # pragma: no cover - core datasets are open
            pytest.skip(f"{did} is research-only: not network-tested")
        if not ingested_current(root, did, subset):
            where = local_dir(did)
            missing = _missing_local(entry, subset, where)
            if not missing:
                datasets.ingest(did, root, subset=subset, from_dir=where)
            elif _in_cache(entry, subset):  # fetched (or adopted) earlier
                datasets.ingest(did, root, subset=subset)
            elif entry.manual:
                pytest.skip(
                    f"{did} is a manual download (CAMBER never fetches it) and its files are not "
                    f"in CAMBER_WORKBOOK_FROM_DIR ({where or 'unset'}) or the cache: missing "
                    f"{', '.join(missing)}. {entry.manual_instructions} Then set "
                    f"CAMBER_WORKBOOK_FROM_DIR to a directory holding them under {did}/."
                )
            elif where is not None:  # a local dir was given but lacks files: ingest names them
                datasets.ingest(did, root, subset=subset, from_dir=where)
            else:
                datasets.fetch(did, subset=subset)
                datasets.ingest(did, root, subset=subset)
        self._done.add(key)


@pytest.fixture(scope="session")
def real_store(tmp_path_factory):
    root = os.environ.get("CAMBER_WORKBOOK_STORE") or str(tmp_path_factory.mktemp("wb_store"))
    return _RealStore(root)


@pytest.mark.network
@pytest.mark.parametrize("ex", EXERCISES, ids=IDS)
def test_answers_hold_on_the_real_data(ex, real_store):
    root = real_store.prepare(ex)
    problems = check_exercise(ex, REAL, root)
    assert not problems, stale_message(ex, REAL, problems)


# --------------------------------------------------------------------------- the network harness

BY_ID = {e.id: e for e in EXERCISES}


def _recorder(monkeypatch):
    calls: list = []
    monkeypatch.setattr(datasets, "fetch", lambda did, **kw: calls.append(("fetch", did, kw)))
    monkeypatch.setattr(datasets, "ingest", lambda did, root, **kw: calls.append((did, root, kw)))
    return calls


def test_the_network_harness_keeps_each_subset_in_its_own_store(tmp_path):
    root = str(tmp_path / "store")
    assert exercise_subsets(BY_ID["plant-boiler"]) == {"lbnl-boiler": "full"}
    assert store_for(root, BY_ID["plant-cooling-tower"]) == root + "-subset-full"
    assert store_for(root, BY_ID["plant-chw-reset-pumping"]) == root  # default subset
    assert store_for(root, BY_ID["air-economizer"]) == root
    two = dataclasses.replace(
        BY_ID["plant-cooling-tower"],
        runs=(Run("lbnl-chiller"), Run("lbnl-chiller", name="b", subset="full")),
    )
    with pytest.raises(ValueError, match="two subsets"):
        exercise_subsets(two)


def test_the_network_harness_ingests_at_the_runs_subset(tmp_path, monkeypatch):
    monkeypatch.delenv("CAMBER_WORKBOOK_FROM_DIR", raising=False)
    monkeypatch.setenv("CAMBER_DATA_DIR", str(tmp_path / "cache"))
    calls = _recorder(monkeypatch)
    rs = _RealStore(str(tmp_path / "store"))
    full = rs.prepare(BY_ID["plant-cooling-tower"])
    default = rs.prepare(BY_ID["plant-chw-reset-pumping"])
    rs.prepare(BY_ID["plant-sensor-vs-equipment"])  # same store and subset: not again
    assert full != default
    assert calls == [
        ("fetch", "lbnl-chiller", {"subset": "full"}),
        ("lbnl-chiller", full, {"subset": "full"}),
        ("fetch", "lbnl-chiller", {"subset": "default"}),
        ("lbnl-chiller", default, {"subset": "default"}),
    ]


def test_a_store_ingested_at_another_subset_or_mapping_is_re_ingested(tmp_path):
    from camber.datasets._ingest import content_hash, mapping_texts

    store = ParquetStore(str(tmp_path / "store"))
    entry = datasets.get("lbnl-chiller")
    shas = {f["name"]: "0" * 64 for f in entry.subset_files("default")}
    meta = entry.provenance()
    meta.update(
        subset="default",
        sha256=shas,
        content_hash=content_hash(entry, "default", shas, mapping_texts(entry)),
    )
    store.register_facility("ds-lbnl-chiller", dataset=meta)
    assert ingested_current(store.root, "lbnl-chiller", "default")
    assert not ingested_current(store.root, "lbnl-chiller", "full")  # another subset
    assert not ingested_current(store.root, "lbnl-boiler", "default")  # never ingested
    meta["content_hash"] = "an older mapping"
    store.register_facility("ds-lbnl-chiller", dataset=meta)
    assert not ingested_current(store.root, "lbnl-chiller", "default")


def test_a_manual_dataset_without_local_files_is_skipped_not_fetched(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMBER_DATA_DIR", str(tmp_path / "cache"))
    calls = _recorder(monkeypatch)
    for from_dir in (None, str(tmp_path / "empty")):
        if from_dir is None:
            monkeypatch.delenv("CAMBER_WORKBOOK_FROM_DIR", raising=False)
        else:
            os.makedirs(from_dir, exist_ok=True)
            monkeypatch.setenv("CAMBER_WORKBOOK_FROM_DIR", from_dir)
        with pytest.raises(pytest.skip.Exception, match="lbnl-b59 is a manual download") as e:
            _RealStore(str(tmp_path / "store")).prepare(BY_ID["zone-min-oa"])
        assert "Building_59.zip" in str(e.value) and "CAMBER_WORKBOOK_FROM_DIR" in str(e.value)
    assert calls == []  # nothing fetched, nothing ingested
    local = tmp_path / "local" / "lbnl-b59"
    local.mkdir(parents=True)
    for f in datasets.get("lbnl-b59").subset_files("default"):
        (local / f["name"]).write_text("")
    monkeypatch.setenv("CAMBER_WORKBOOK_FROM_DIR", str(tmp_path / "local"))
    root = _RealStore(str(tmp_path / "store")).prepare(BY_ID["zone-min-oa"])
    assert calls == [("lbnl-b59", root, {"subset": "default", "from_dir": str(local)})]


def _datasets_md_file_list() -> dict:
    """The per-dataset file list of docs/DATASETS.md's "Running the workbook answer checks on
    local files" subsection (#89)."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "docs")
    with open(os.path.join(path, "DATASETS.md"), encoding="utf-8") as fh:
        text = fh.read()
    head = "### Running the workbook answer checks on local files"
    assert head in text, "DATASETS.md lost its workbook local-files subsection"
    section = text.split(head, 1)[1].split("\n## ", 1)[0].split("\n### ", 1)[0]
    assert "CAMBER_WORKBOOK_FROM_DIR" in section and "<dir>/<dataset-id>/" in section
    return {
        m.group(1): re.findall(r"`([^`]+)`", m.group(2))
        for m in re.finditer(r"^- `([a-z0-9-]+)`(?: \(manual\))?: (.+)$", section, re.M)
    }


def test_the_from_dir_file_list_matches_the_catalog():
    doc = re.sub(r"\s+", " ", __doc__)
    listed = {
        m.group(1): re.findall(r"``([^`]+)``", m.group(2))
        for m in re.finditer(r"- ``([a-z0-9-]+)``(?: \(manual\))?: (.*?)(?= - ``| The small)", doc)
    }
    assert _datasets_md_file_list() == listed  # the user docs list the same files
    want: dict = {}
    for ex in EXERCISES:
        for did, subset in exercise_subsets(ex).items():
            names = want.setdefault(did, [])
            names += [
                f["name"] for f in datasets.get(did).subset_files(subset) if f["name"] not in names
            ]
    assert listed == want
