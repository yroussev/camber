"""The workbook answer-key harness (#79): pin each exercise's answers to CAMBER's output.

Each exercise of the re-tuning workbook (``docs/workbook/<id>.md``) has one declaration module,
``tests/workbook/exercises/<id with _>.py``, defining ``EXERCISE = Exercise(...)``: its datasets,
the config(s) it runs, the CLI commands its page shows, the **expected answers** and a
**synthetic stand-in** builder. ``test_workbook_answers.py`` then checks every exercise twice:

- **offline** (always): the stand-in writes small synthetic facilities shaped like the dataset --
  same facility id, equipment ids, equipment class and roles -- into a temporary store, and the
  exercise's own configs run against it;
- **network** (``-m network``, deselected by default): the real catalog data is fetched and
  ingested, and the same configs and the same expectations run against it.

An expectation that no longer holds fails with the exercise id, the page and what changed, so a
workbook answer can never go stale silently. Expectations apply ``on=BOTH`` by default; figures
that differ between the stand-in and the real data (a metric's value) are declared once per mode.

Why this lives under ``tests/`` and not in the package: the declarations *are* the answer keys,
the harness needs only the public ``camber.datasets`` / ``camber.config`` API, and keeping it out
of ``camber/`` adds no public (snapshot-locked) API while the workbook is still growing. The
per-exercise **config templates** do ship (``camber/datasets/configs/exercises/<id>.json``), since
learners run them with ``camber datasets config <dataset> --exercise <id>``.
"""

from __future__ import annotations

import glob
import importlib.util
import math
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

from camber import datasets
from camber.config import run_config
from camber.store import ParquetStore

BOTH, STANDIN, REAL = "both", "standin", "real"
MODES = (STANDIN, REAL)
FIRED = ("warn", "fault")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOCS = os.path.join(ROOT, "docs")
WORKBOOK = os.path.join(DOCS, "workbook")
EXERCISES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "exercises")

#: content issue -> the marker of its blocks in index.md and mkdocs.yml (#79 is the framework;
#: its worked example, air-economizer, belongs to #80)
AREAS = {
    80: "workbook-air (#80)",
    81: "workbook-zone (#81)",
    82: "workbook-plant (#82)",
    83: "workbook-practice (#83)",
}
ISSUES = tuple(AREAS)
#: the exercise page's H2 sections, in order (docs/workbook/_template.md)
SECTIONS = (
    "Goal",
    "Learn more",
    "Datasets and licence",
    "Setup",
    "Steps",
    "Questions",
    "What CAMBER shows",
    "Caveats",
    "Going further",
)


# --------------------------------------------------------------------------- declarations


@dataclass(frozen=True)
class Run:
    """One config an exercise runs. ``config`` is the exercise template id
    (``configs/exercises/<config>.json``) or ``None`` for the dataset's own template."""

    dataset: str
    name: str = "main"
    config: str | None = None
    facility: str | None = None
    subset: str = "default"


@dataclass(frozen=True)
class Finding:
    """A finding present (``warn``/``fault``) or absent (``ok``/``info``/no finding) on
    equipment. ``severity``, when given, is the exact set of allowed outcomes instead and may
    mix both kinds: ``("fault",)``, or ``("ok", "warn")`` for "not a fault"; ``"absent"``
    stands for no finding at all. ``present`` is then ignored."""

    rule: str
    equip: str
    present: bool = True
    severity: tuple = ()
    run: str = "main"
    on: str = BOTH


@dataclass(frozen=True)
class Metric:
    """A finding metric within ``tol`` of ``value``. ``quote`` is how the instructor page
    writes the figure (e.g. ``"67.5%"``); when set, the instructor block must contain it."""

    rule: str
    equip: str
    metric: str
    value: float
    tol: float
    run: str = "main"
    on: str = BOTH
    quote: str = ""


@dataclass(frozen=True)
class Score:
    """A label score (:func:`camber.datasets.score` on the run's findings): ``detector`` is a
    rule name, or ``None`` for the overall detection; ``tpr`` / ``fpr`` within ``tol``."""

    detector: str | None = None
    tpr: float | None = None
    fpr: float | None = None
    tol: float = 0.0
    rules: tuple = ()
    run: str = "main"
    on: str = BOTH
    quote: str = ""


@dataclass(frozen=True)
class Check:
    """A free-form answer: ``fn(ctx)`` raises ``AssertionError`` (with a message) when the
    answer no longer holds. For drift, M&V, sensor health or anything a rule run can't show."""

    name: str
    fn: Callable
    on: str = BOTH
    quote: str = ""


@dataclass(frozen=True)
class Exercise:
    """One workbook exercise and its answer key (see the module docstring)."""

    id: str
    title: str
    issue: int
    references: tuple  # camber.references ids its "Learn more" section cites
    datasets: tuple  # core datasets (open licence only)
    runs: tuple
    expect: tuple
    standin: Callable  # standin(store: ParquetStore) -> None
    commands: tuple = ()  # CLI commands the page shows, verbatim
    optional_datasets: tuple = ()  # marked optional extras (research-only allowed)

    @property
    def page(self) -> str:
        """The docs-relative page (``workbook/<id>.md``)."""
        return f"workbook/{self.id}.md"

    @property
    def page_path(self) -> str:
        return os.path.join(DOCS, *self.page.split("/"))

    def run(self, name: str) -> Run:
        for r in self.runs:
            if r.name == name:
                return r
        raise KeyError(f"{self.id}: no run {name!r}")

    def quotes(self) -> list:
        return [e.quote for e in self.expect if getattr(e, "quote", "")]


def load_exercises() -> list:
    """Every ``EXERCISE`` declared under ``tests/workbook/exercises/`` (sorted by id)."""
    # shared stand-in helpers of one area live beside the declarations (_<area>_standins.py)
    if EXERCISES_DIR not in sys.path:
        sys.path.insert(0, EXERCISES_DIR)
    out = []
    for path in sorted(glob.glob(os.path.join(EXERCISES_DIR, "*.py"))):
        name = os.path.basename(path)[:-3]
        if name.startswith("_"):
            continue
        spec = importlib.util.spec_from_file_location(f"workbook_exercise_{name}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        ex = getattr(mod, "EXERCISE", None)
        if not isinstance(ex, Exercise):
            raise TypeError(f"{path}: must define EXERCISE = Exercise(...)")
        if ex.id.replace("-", "_") != name:
            raise ValueError(f"{path}: the file must be named {ex.id.replace('-', '_')}.py")
        out.append(ex)
    return sorted(out, key=lambda e: e.id)


# --------------------------------------------------------------------------- stand-in helpers


def write_standin(
    store: ParquetStore,
    dataset_id: str,
    frames: dict,
    *,
    labels: dict | None = None,
    facility_id: str | None = None,
) -> str:
    """Write a synthetic stand-in facility shaped like ``dataset_id`` into ``store``.

    ``frames`` maps an equipment id (the one the real ingest makes, e.g. ``AHU__fault_free``) to
    ``(equipment class, role frame)``. The facility id defaults to the entry's; it is registered
    with the entry's provenance (so a config run finds its timezone and data source) plus
    ``subset: "standin"`` and ``labels`` (``{equip: fault type or ""}``, for label scores).
    Returns the facility id.
    """
    entry = datasets.get(dataset_id)
    fid = facility_id or entry.ingest.get("facility") or f"ds-{dataset_id}"
    for equip, (cls, frame) in frames.items():
        store.write_role_frame(frame, facility_id=fid, equip=equip, equip_class=cls)
    meta = entry.provenance()
    meta.update({"subset": "standin", "standin": True, "labels": dict(labels or {})})
    store.register_facility(fid, dataset=meta)
    return fid


def hourly_index(days: int = 28, start: str = "2018-01-01") -> pd.DatetimeIndex:
    """An hourly index (``days`` long) for a stand-in; 2018-01-01 is a Monday."""
    return pd.date_range(start, periods=days * 24, freq="1h")


# --------------------------------------------------------------------------- checking


class Context:
    """What one exercise's checks see: its runs' configs, results and label scores in a store."""

    def __init__(self, exercise: Exercise, mode: str, store: str):
        self.exercise, self.mode, self.store = exercise, mode, os.fspath(store)
        self._results: dict = {}

    def config(self, run: str = "main") -> dict:
        r = self.exercise.run(run)
        return datasets.config_template(
            r.dataset, self.store, facility_id=r.facility, exercise=r.config
        )

    def result(self, run: str = "main"):
        if run not in self._results:
            self._results[run] = run_config(self.config(run), base_dir=self.store)
        return self._results[run]

    def findings(self, run: str = "main") -> list:
        return list(self.result(run).findings)

    def finding(self, rule: str, equip: str, run: str = "main"):
        for f in self.findings(run):
            if f.rule == rule and f.equip == equip:
                return f
        return None

    def score(self, run: str = "main", rules: tuple = ()) -> dict:
        r = self.exercise.run(run)
        return datasets.score(
            r.dataset,
            self.store,
            findings=self.findings(run),
            rules=list(rules) or None,
            facility_id=r.facility,
        )


def _applies(exp, mode: str) -> bool:
    return exp.on in (BOTH, mode)


def _close(got, want, tol) -> bool:
    return (
        got is not None
        and not (isinstance(got, float) and math.isnan(got))
        and (abs(float(got) - float(want)) <= tol + 1e-9)
    )


def _check_one(ctx: Context, exp) -> str | None:
    """``None`` when ``exp`` holds, else a one-line description of what CAMBER says now."""
    if isinstance(exp, Finding):
        f = ctx.finding(exp.rule, exp.equip, exp.run)
        sev = f.severity if f is not None else "absent"
        fired = sev in FIRED
        if exp.severity:
            if sev not in exp.severity:
                want = "/".join(exp.severity)
                return f"expected {exp.rule} on {exp.equip} to be {want}; CAMBER says {sev}"
            return None
        if exp.present and not fired:
            return f"expected {exp.rule} to fire on {exp.equip}; CAMBER says {sev}"
        if not exp.present and fired:
            return f"expected no {exp.rule} finding on {exp.equip}; CAMBER says {sev}"
        return None
    if isinstance(exp, Metric):
        f = ctx.finding(exp.rule, exp.equip, exp.run)
        if f is None:
            return f"expected a {exp.rule} finding on {exp.equip} (metric {exp.metric}); none"
        got = (f.metrics or {}).get(exp.metric)
        if not _close(got, exp.value, exp.tol):
            return (
                f"{exp.rule} on {exp.equip}: {exp.metric} = {got!r}, expected {exp.value} "
                f"+/- {exp.tol}"
            )
        return None
    if isinstance(exp, Score):
        res = ctx.score(exp.run, exp.rules)
        block = res["overall"] if exp.detector is None else res["per_detector"].get(exp.detector)
        what = "overall detection" if exp.detector is None else exp.detector
        if block is None:
            return f"label score: no {what} in the score"
        bad = [
            f"{k} {block[k]:.3f} (expected {want} +/- {exp.tol})"
            for k, want in (("tpr", exp.tpr), ("fpr", exp.fpr))
            if want is not None and not _close(block[k], want, exp.tol)
        ]
        return f"label score, {what}: " + ", ".join(bad) if bad else None
    if isinstance(exp, Check):
        try:
            exp.fn(ctx)
        except AssertionError as e:
            return f"check {exp.name!r}: {e}"
        return None
    raise TypeError(f"unknown expectation {exp!r}")


def check_exercise(ex: Exercise, mode: str, store) -> list:
    """Every expectation of ``ex`` that does not hold in ``store`` (``[]``: all hold)."""
    ctx = Context(ex, mode, store)
    return [msg for e in ex.expect if _applies(e, mode) for msg in [_check_one(ctx, e)] if msg]


def stale_message(ex: Exercise, mode: str, problems: list) -> str:
    """The loud failure: which exercise, which page, what no longer holds, and what to do."""
    where = "the synthetic stand-in" if mode == STANDIN else "the real catalog data"
    lines = [
        f"WORKBOOK EXERCISE {ex.id!r} IS STALE on {where} (docs/{ex.page}, "
        f"tests/workbook/exercises/{ex.id.replace('-', '_')}.py):",
        *[f"  - {p}" for p in problems],
        "Either CAMBER regressed, or the answer changed on purpose: then re-run the exercise, "
        "update the expectation and the instructor key (docs/workbook/instructor.md) together.",
    ]
    return "\n".join(lines)
