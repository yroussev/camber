"""Workbook answer keys (#79): every exercise's expected answers still hold.

Offline (always): each exercise's synthetic stand-in -- facilities shaped like its datasets --
runs through the exercise's own configs. Network (``pytest -m network``, deselected by default):
the real catalog data is fetched, ingested and checked against the same expectations.

For the network run, ``CAMBER_WORKBOOK_STORE`` reuses a store (a dataset already ingested there
is not ingested again), and ``CAMBER_WORKBOOK_FROM_DIR`` takes the publisher files from a local
directory instead of downloading them (``ingest --from-dir``; a ``<dir>/<dataset-id>/``
subdirectory is used when it exists). Each test ingests only its own exercise's datasets.
Research-only datasets are never fetched here; an exercise's optional research-only extras are
not network-tested.
"""

import dataclasses
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _workbook import (  # noqa: E402
    REAL,
    STANDIN,
    Finding,
    Metric,
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


class _RealStore:
    """A store holding the real catalog data, ingested on first use per dataset."""

    def __init__(self, root: str):
        self.root, self._done = root, set()

    def _ingested(self, did: str) -> bool:
        from camber.datasets._ingest import dataset_meta

        for meta in ParquetStore(self.root).facilities_meta().values():
            block = dataset_meta(meta)
            if block.get("dataset_id") == did and not block.get("standin"):
                return True
        return False

    def ensure(self, did: str) -> None:
        if did in self._done:
            return
        if datasets.get(did).research_only:  # pragma: no cover - core datasets are open
            pytest.skip(f"{did} is research-only: not network-tested")
        if not self._ingested(did):
            from_dir = os.environ.get("CAMBER_WORKBOOK_FROM_DIR")
            if from_dir:
                sub = os.path.join(from_dir, did)
                datasets.ingest(did, self.root, from_dir=sub if os.path.isdir(sub) else from_dir)
            else:
                datasets.fetch(did)
                datasets.ingest(did, self.root)
        self._done.add(did)


@pytest.fixture(scope="session")
def real_store(tmp_path_factory):
    root = os.environ.get("CAMBER_WORKBOOK_STORE") or str(tmp_path_factory.mktemp("wb_store"))
    return _RealStore(root)


@pytest.mark.network
@pytest.mark.parametrize("ex", EXERCISES, ids=IDS)
def test_answers_hold_on_the_real_data(ex, real_store):
    for did in ex.datasets:
        real_store.ensure(did)
    problems = check_exercise(ex, REAL, real_store.root)
    assert not problems, stale_message(ex, REAL, problems)
