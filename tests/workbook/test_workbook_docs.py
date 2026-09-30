"""Workbook structure (#79): pages, instructor keys, curriculum map, nav, catalog links, configs.

Everything here is offline and fast. It keeps the workbook's parts in step with each other: each
exercise declaration (``tests/workbook/exercises/``) has its page with the template's sections,
its "Learn more" links from the reference registry, its commands verbatim, open core datasets,
a filled instructor block quoting the pinned figures, a curriculum-map row and a nav entry in its
area's marked block; every catalog ``exercise`` link and every exercise config resolves.
"""

import glob
import importlib.util
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _workbook import (  # noqa: E402
    AREAS,
    DOCS,
    ROOT,
    SECTIONS,
    WORKBOOK,
    load_exercises,
)

from camber import datasets  # noqa: E402
from camber.datasets._ops import exercise_config_ids  # noqa: E402
from camber.lab._docs import slug  # noqa: E402
from camber.references import REFERENCES  # noqa: E402

EXERCISES = load_exercises()
IDS = [e.id for e in EXERCISES]
BY_ID = {e.id: e for e in EXERCISES}


def _refs_script():
    path = os.path.join(ROOT, "scripts", "workbook_refs.py")
    spec = importlib.util.spec_from_file_location("workbook_refs", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


REFS = _refs_script()


def _read(*parts) -> str:
    with open(os.path.join(*parts), encoding="utf-8") as fh:
        return fh.read()


def _block(text: str, begin: str, end: str) -> str | None:
    """The text between ``begin`` and ``end`` (``None`` when absent)."""
    i = text.find(begin)
    if i < 0:
        return None
    j = text.find(end, i)
    return text[i + len(begin) : j] if j >= 0 else None


def _h2s(text: str) -> list:
    out, fence = [], False
    for ln in text.splitlines():
        if ln.lstrip().startswith("```"):
            fence = not fence
        elif not fence and ln.startswith("## "):
            out.append(ln[3:].strip())
    return out


# --------------------------------------------------------------------------- declarations


def test_there_is_at_least_the_worked_example():
    assert "air-economizer" in BY_ID


def test_exercise_ids_are_unique_and_well_formed():
    assert len(IDS) == len(set(IDS))
    for e in EXERCISES:
        assert re.fullmatch(r"[a-z0-9][a-z0-9-]{1,63}", e.id), e.id
        assert e.issue in AREAS, (e.id, e.issue)
        names = [r.name for r in e.runs]
        assert e.runs and len(names) == len(set(names)), e.id
        assert e.expect, f"{e.id}: an exercise needs expected answers"
        for x in e.expect:
            assert x.on in ("both", "standin", "real"), (e.id, x)
            run = getattr(x, "run", None)
            assert run is None or run in names, (e.id, x)


@pytest.mark.parametrize("ex", EXERCISES, ids=IDS)
def test_core_datasets_are_open_catalog_entries(ex):
    known = {d.id: d for d in datasets.catalog()}
    for did in ex.datasets:
        assert did in known, f"{ex.id}: {did} is not in the catalog"
        assert known[did].access == "open", f"{ex.id}: core dataset {did} must be open-licence"
    for did in ex.optional_datasets:
        assert did in known, f"{ex.id}: optional {did} is not in the catalog"
    for r in ex.runs:
        assert r.dataset in ex.datasets + ex.optional_datasets, (ex.id, r)


@pytest.mark.parametrize("ex", EXERCISES, ids=IDS)
def test_runs_resolve_to_shipped_configs(ex, tmp_path):
    for r in ex.runs:
        cfg = datasets.config_template(
            r.dataset, tmp_path, facility_id=r.facility, exercise=r.config
        )
        assert cfg["source"]["kind"] == "store" and cfg.get("rules") is not None, (ex.id, r)


def test_every_exercise_config_is_used_by_its_exercise():
    used = {(r.config, r.dataset) for e in EXERCISES for r in e.runs if r.config}
    for cid in exercise_config_ids():
        cfg = json.loads(_read(ROOT, "camber", "datasets", "configs", "exercises", f"{cid}.json"))
        assert isinstance(cfg.get("_comment"), str) and cfg["_comment"].strip(), cid
        assert (cid, cfg.get("_dataset")) in used, f"configs/exercises/{cid}.json is unused"
        prefix = cid.split("--")[0]
        assert prefix in BY_ID, f"configs/exercises/{cid}.json: {prefix!r} is no exercise id"
    shipped = glob.glob(os.path.join(ROOT, "camber", "datasets", "configs", "exercises", "*"))
    assert sorted(os.path.basename(p)[:-5] for p in shipped) == exercise_config_ids()


# --------------------------------------------------------------------------- the page


@pytest.mark.parametrize("ex", EXERCISES, ids=IDS)
def test_page_follows_the_template(ex):
    assert os.path.isfile(ex.page_path), f"missing docs/{ex.page}"
    text = _read(ex.page_path)
    first = text.splitlines()[0]
    assert first == f"# {ex.title}", f"{ex.page}: the H1 must be the declared title"
    assert f"`{ex.id}`" in text.splitlines()[2], f"{ex.page}: the byline names the exercise id"
    assert _h2s(text) == list(SECTIONS), f"{ex.page}: H2 sections must be exactly {SECTIONS}"
    for did in ex.datasets + ex.optional_datasets:
        assert f"`{did}`" in text, f"{ex.page}: names no dataset {did}"
    for cmd in ex.commands:
        assert cmd in text, f"{ex.page}: the command {cmd!r} is not on the page verbatim"
    assert "Answer key" not in text, f"{ex.page}: answer keys go on the instructor page only"
    assert "TEMPLATE for a workbook exercise page" not in text


@pytest.mark.parametrize("ex", EXERCISES, ids=IDS)
def test_page_learn_more_cites_its_registry_references(ex):
    text = _read(ex.page_path)
    learn = _block(text, "## Learn more", "\n## ")
    assert learn is not None
    assert ex.references, f"{ex.id}: declare the reference ids its Learn more cites"
    for rid in ex.references:
        assert rid in REFERENCES, f"{ex.id}: {rid} is not a camber.references id"
        assert f"][{rid}]" in learn, f"{ex.page}: Learn more does not link [{rid}]"
    assert REFS.used_ids(text) >= set(ex.references)


def test_reference_links_on_every_workbook_page_come_from_the_registry():
    assert REFS.check_pages() == []


def test_the_reference_check_catches_bad_links():
    bad = (
        "See [the guide][pnnl-guide-economizer] and [x][nope].\n\n"
        "[pnnl-guide-economizer]: https://www.pnnl.gov/wrong.pdf\n"
        "[pnnl-not-an-id]: https://example.org/x\n"
        "[other]: https://www.pnnl.gov/sites/default/files/media/file/ch6_economizer.pdf\n"
        "Inline: [ch6](https://www.pnnl.gov/sites/default/files/media/file/ch6_economizer.pdf)\n"
        "```\n[fenced][ignored]\n```\n"
    )
    errs = REFS.check_text(bad, "x.md")
    assert len(errs) == 5, errs
    joined = "\n".join(errs)
    for want in (
        "but the registry has",
        "not a camber.references id",
        "use its registry id",
        "inline PNNL link",
        "[nope] is used but not defined",
    ):
        assert want in joined
    good = REFS.definition("pnnl-guide-economizer")
    assert REFS.check_text(f"[g][pnnl-guide-economizer]\n\n{good}\n", "y.md") == []
    assert REFS.main(["pnnl-guide-economizer"]) == 0 and REFS.main(["nope"]) == 1
    assert REFS.main(["--list"]) == 0 and REFS.main(["--check"]) == 0 and REFS.main([]) == 2


# --------------------------------------------------------------------------- shared pages


def test_instructor_blocks_are_well_formed_and_unique():
    text = _read(WORKBOOK, "instructor.md")
    begins = re.findall(r"<!-- BEGIN ([a-z0-9-]+) -->", text)
    ends = re.findall(r"<!-- END ([a-z0-9-]+) -->", text)
    assert begins == ends and len(begins) == len(set(begins))
    for eid in begins:
        body = _block(text, f"<!-- BEGIN {eid} -->", f"<!-- END {eid} -->")
        assert body is not None and f"### `{eid}`" in body, eid


@pytest.mark.parametrize("ex", EXERCISES, ids=IDS)
def test_instructor_block_holds_the_key_and_quotes_the_pinned_figures(ex):
    text = _read(WORKBOOK, "instructor.md")
    body = _block(text, f"<!-- BEGIN {ex.id} -->", f"<!-- END {ex.id} -->")
    assert body is not None, f"instructor.md has no block for {ex.id}"
    assert "_Placeholder" not in body, f"instructor.md: {ex.id}'s key is still a placeholder"
    for part in ("**Answer key**", "**Discussion points**", "**Common mistakes**"):
        assert part in body, f"instructor.md/{ex.id}: missing {part}"
    for q in ex.quotes():
        assert q in body, f"instructor.md/{ex.id}: the pinned figure {q!r} is not quoted"


@pytest.mark.parametrize("ex", EXERCISES, ids=IDS)
def test_curriculum_map_and_nav_list_the_exercise_in_its_area(ex):
    marker = AREAS[ex.issue]
    index = _block(
        _read(WORKBOOK, "index.md"), f"<!-- BEGIN {marker} -->", f"<!-- END {marker} -->"
    )
    assert index is not None and f"]({ex.id}.md)" in index, f"index.md: no link to {ex.id}.md"
    nav = _block(_read(ROOT, "mkdocs.yml"), f"# BEGIN {marker}", f"# END {marker}")
    assert nav is not None and f"workbook/{ex.id}.md" in nav, f"mkdocs.yml: {ex.id} not in nav"


def test_every_published_workbook_page_is_in_the_nav():
    nav = _read(ROOT, "mkdocs.yml")
    for path in glob.glob(os.path.join(WORKBOOK, "*.md")):
        name = os.path.basename(path)
        if name.startswith("_"):
            assert f"workbook/{name}" in nav.split("exclude_docs:")[1], name
            continue
        assert f"workbook/{name}" in nav.split("exclude_docs:")[0], f"{name} is not in the nav"
        if name not in ("index.md", "instructor.md"):
            assert name[:-3] in BY_ID, f"{name} has no exercise declaration in tests/workbook"


def test_index_and_instructor_markers_cover_every_area():
    index, instr = _read(WORKBOOK, "index.md"), _read(ROOT, "mkdocs.yml")
    for marker in AREAS.values():
        assert index.count(f"<!-- BEGIN {marker} -->") == 1 == index.count(f"<!-- END {marker} -->")
        assert instr.count(f"# BEGIN {marker}") == 1 == instr.count(f"# END {marker}")


# --------------------------------------------------------------------------- catalog links


def test_catalog_exercise_links_resolve_to_a_page_that_uses_the_dataset():
    seen = 0
    for e in datasets.catalog():
        link = e.exercise
        if not link or link.startswith("https://"):
            continue
        seen += 1
        page, _, anchor = link.partition("#")
        path = os.path.join(DOCS, *page.split("/"))
        assert os.path.isfile(path), f"{e.id}: exercise page docs/{page} does not exist"
        if anchor:
            heads = [
                ln.lstrip("#").strip() for ln in _read(path).splitlines() if ln.startswith("#")
            ]
            assert anchor in {slug(h) for h in heads}, f"{e.id}: no heading #{anchor} in {page}"
        ex = BY_ID.get(os.path.basename(page)[:-3])
        assert ex is not None and e.id in ex.datasets + ex.optional_datasets, (e.id, link)
    assert seen >= 1
