"""The workbook's package side (#79): the catalog ``exercise`` field, exercise config templates
(``camber datasets config --exercise``), and how ``camber lab`` resolves and serves a relative
exercise link (offline from a local docs tree, else the published docs site)."""

import copy
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _workbook import DOCS  # noqa: E402

from camber import cli, datasets  # noqa: E402
from camber.datasets._catalog import (  # noqa: E402
    DatasetEntry,
    is_exercise_link,
    load_catalog_data,
    validate_catalog,
)
from camber.datasets._ops import exercise_config, exercise_config_ids  # noqa: E402
from camber.lab import LabApp, dispatch_lab  # noqa: E402
from camber.lab._docs import (  # noqa: E402
    DOCS_CSP,
    DOCS_SITE,
    default_docs_dir,
    exercise_href,
    exercise_url,
    page_html,
    slug,
)
from camber.lab._ui import lab_page_html  # noqa: E402

PORT = 8765


def _entry(did: str) -> dict:
    d = next(e for e in load_catalog_data()["datasets"] if e["id"] == did)
    return copy.deepcopy(d)


# --------------------------------------------------------------------------- catalog field


@pytest.mark.parametrize(
    "value,ok",
    [
        ("workbook/air-economizer.md", True),
        ("workbook/air-economizer.md#setup", True),
        ("https://example.org/course#ex-1", True),
        ("workbook/_template.md", False),
        ("workbook/../README.md", False),
        ("/workbook/x.md", False),
        ("workbook/x.html", False),
        ("DATASETS.md#x", False),
        ("http://example.org/x", False),
        ("https://example.org/a b", False),
        ("javascript:alert(1)", False),
        (None, False),
        (3, False),
    ],
)
def test_exercise_link_shapes(value, ok):
    assert is_exercise_link(value) is ok


def test_catalog_validates_the_exercise_field():
    d = _entry("lbnl-sdahu")
    assert d["suggested_analyses"]["exercise"] == "workbook/air-economizer.md"
    assert validate_catalog({"schema": 1, "datasets": [d]}) == []
    d["suggested_analyses"]["exercise"] = "../escape.md"
    errs = validate_catalog({"schema": 1, "datasets": [d]})
    assert len(errs) == 1 and "exercise" in errs[0] and "workbook/<id>.md" in errs[0]
    entry = DatasetEntry.from_dict(_entry("lbnl-sdahu"))
    assert entry.exercise == "workbook/air-economizer.md"


def test_exercise_url_maps_a_page_to_the_docs_site():
    assert exercise_url("workbook/air-economizer.md") == DOCS_SITE + "workbook/air-economizer/"
    assert exercise_url("workbook/a.md#setup") == DOCS_SITE + "workbook/a/#setup"
    assert exercise_url("https://example.org/x") == "https://example.org/x"
    with pytest.raises(ValueError):
        exercise_url("nope.md")


# --------------------------------------------------------------------------- exercise configs


def test_exercise_config_templates(tmp_path):
    assert "air-economizer" in exercise_config_ids()
    entry = datasets.get("lbnl-sdahu")
    raw = exercise_config(entry, "air-economizer")
    assert raw["_dataset"] == "lbnl-sdahu" and raw["source"]["store"] == ""
    out = tmp_path / "econ.json"
    cfg = datasets.config_template("lbnl-sdahu", tmp_path, exercise="air-economizer", out=str(out))
    assert cfg["source"] == {
        "kind": "store",
        "store": os.path.abspath(str(tmp_path)),
        "facility_id": "ds-lbnl-sdahu",
    }
    assert [r if isinstance(r, str) else r["name"] for r in cfg["rules"]] == [
        "outdoor_air_fraction",
        "economizer_high_limit",
        "free_cooling_missed",
    ]
    assert json.loads(out.read_text()) == cfg
    default = datasets.config_template("lbnl-sdahu", tmp_path)
    assert "leaking_valve" in default["rules"] and "_dataset" not in default
    with pytest.raises(ValueError, match="is for dataset 'lbnl-sdahu'"):
        datasets.config_template("lbnl-ddahu", tmp_path, exercise="air-economizer")
    with pytest.raises(KeyError, match="no exercise config"):
        datasets.config_template("lbnl-sdahu", tmp_path, exercise="no-such-exercise")
    with pytest.raises(KeyError, match="invalid exercise id"):
        exercise_config(entry, "../x")


def test_cli_config_exercise_and_info(tmp_path, capsys):
    out = tmp_path / "e.json"
    rc = cli.main(
        [
            "datasets",
            "config",
            "lbnl-sdahu",
            "--exercise",
            "air-economizer",
            "--store",
            str(tmp_path),
            "--out",
            str(out),
        ]
    )
    assert rc == 0 and "free_cooling_missed" in out.read_text()
    assert (
        cli.main(
            [
                "datasets",
                "config",
                "lbnl-ddahu",
                "--exercise",
                "air-economizer",
                "--store",
                str(tmp_path),
            ]
        )
        == 1
    )
    assert "is for dataset" in capsys.readouterr().err
    assert cli.main(["datasets", "info", "lbnl-sdahu"]) == 0
    assert f"exercise  : {DOCS_SITE}workbook/air-economizer/" in capsys.readouterr().out


# --------------------------------------------------------------------------- the lab


def _lab(tmp_path, docs_dir):
    sd = DatasetEntry.from_dict(_entry("lbnl-sdahu"))
    ext = _entry("lbnl-ddahu")
    ext["suggested_analyses"]["exercise"] = "https://example.org/course#dd"
    none = _entry("lbnl-fcu")
    app = LabApp(
        store=str(tmp_path / "store"),
        data_dir=str(tmp_path / "cache"),
        entries=[sd, DatasetEntry.from_dict(ext), DatasetEntry.from_dict(none)],
        docs_dir=docs_dir,
    )
    app.bind(PORT)
    return app


def _get(app, path):
    return dispatch_lab(app, "GET", path, {}, {"Host": f"127.0.0.1:{PORT}"})


def test_default_docs_dir_is_the_checkout_docs():
    assert default_docs_dir() == DOCS


def test_lab_serves_a_relative_exercise_link_offline(tmp_path):
    app = _lab(tmp_path, DOCS)
    try:
        status, body, _h = _get(app, "/lab/catalog")
        rows = {r["id"]: r for r in body["datasets"]}
        assert rows["lbnl-sdahu"]["exercise"] == "/lab/docs/workbook/air-economizer.md"
        assert rows["lbnl-ddahu"]["exercise"] == "https://example.org/course#dd"
        assert rows["lbnl-fcu"]["exercise"] is None
        status, html, h = _get(app, "/lab/docs/workbook/air-economizer.md")
        assert status == 200 and h["Content-Security-Policy"] == DOCS_CSP
        assert "script" not in DOCS_CSP and "<script" not in html
        assert "id='setup'" in html and "id='learn-more'" in html
        # registry links are clickable, open in a new tab, send no referrer
        assert (
            "<a href='https://www.pnnl.gov/sites/default/files/media/file/pnnl_sa_86706.pdf' "
            "target='_blank' rel='noopener noreferrer'>Air-Side Economizer Operation</a>" in html
        )
        # another docs page -> the published site; the rendered page is linked at the top
        assert f"href='{DOCS_SITE}DATASETS/#lbnl-sdahu" in html
        assert f"href='{DOCS_SITE}workbook/air-economizer/'" in html
        status, idx, _h = _get(app, "/lab/docs/workbook/index.md")
        assert status == 200 and "href='/lab/docs/workbook/air-economizer.md'" in idx
        for bad in (
            "/lab/docs/workbook/../index.md",
            "/lab/docs/../README.md",
            "/lab/docs/workbook/_template.md",
            "/lab/docs/workbook/nope.md",
            "/lab/docs/workbook/air-economizer.html",
            "/lab/docs/workbook/%2e%2e/x.md",
        ):
            assert _get(app, bad)[0] == 404, bad
    finally:
        app.close()


def test_lab_links_the_published_site_without_a_docs_tree(tmp_path):
    empty = tmp_path / "docs"
    empty.mkdir()
    app = _lab(tmp_path, str(empty))
    try:
        rows = {r["id"]: r for r in _get(app, "/lab/catalog")[1]["datasets"]}
        assert rows["lbnl-sdahu"]["exercise"] == DOCS_SITE + "workbook/air-economizer/"
        assert _get(app, "/lab/docs/workbook/air-economizer.md")[0] == 404
    finally:
        app.close()


def test_the_page_script_renders_both_link_kinds():
    page = lab_page_html("t")
    assert "\\/lab\\/docs\\/workbook\\/" in page and "https:\\/\\/" in page


def test_exercise_href_and_page_html_edges(tmp_path):
    assert exercise_href(None, DOCS) is None and exercise_href("bad", DOCS) is None
    assert exercise_href("workbook/air-economizer.md#goal", None).endswith("/#goal")
    assert exercise_href("workbook/air-economizer.md#goal", DOCS).endswith(".md#goal")
    assert page_html(None, "workbook/air-economizer.md") is None
    wb = tmp_path / "workbook"
    wb.mkdir()
    (wb / "t.md").write_text(
        "# T <x>\n\nSee [a](other.md#s), [b](#local), [c](mailto:x), [d][missing] and [e][r].\n\n"
        "```\n## not a heading <b>\n[a](https://example.org)\n```\n\n"
        "[r]: https://example.org/r\n",
        encoding="utf-8",
    )
    out = page_html(str(tmp_path), "workbook/t.md")
    assert "<h1 id='t-x'>T &lt;x&gt;</h1>" in out
    assert "href='/lab/docs/workbook/other.md#s'" in out and "href='#local'" in out
    assert "[c](mailto:x)" in out and "[d][missing]" in out
    assert "href='https://example.org/r'" in out
    assert "## not a heading &lt;b&gt;" in out and "<h2" not in out
    assert slug("`air-economizer`: a stuck damper (x)") == "air-economizer-a-stuck-damper-x"
