"""Docs figures (#113): every referenced image exists and fits the cap; nothing generated is stale.

``scripts/docs_figures.py --check`` is the gate: it reads the Markdown and ``docs/img/`` only, so
it runs offline with no matplotlib and no browser.
"""

import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _script():
    path = os.path.join(ROOT, "scripts", "docs_figures.py")
    spec = importlib.util.spec_from_file_location("docs_figures", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_docs_figures_check_passes():
    mod = _script()
    assert mod.check() == []
    assert mod.main(["--check"]) == 0


def test_every_output_lives_under_docs_img_with_a_known_kind():
    mod = _script()
    outs = mod.expected_outputs()
    assert len(outs) >= 40
    for rel in outs:
        assert rel.endswith(".png") and not rel.startswith("/") and ".." not in rel
        assert rel.split("/")[0] in ("viz", "analytics", "workbook", "shots", "thumbs"), rel
    for src, width in mod.THUMBS.values():
        assert src in mod.FIGURES and 200 <= width <= 600


def test_the_check_catches_missing_oversized_uncaptioned_and_stale_images(tmp_path, monkeypatch):
    mod = _script()
    docs = tmp_path / "docs"
    img = docs / "img"
    (img / "viz").mkdir(parents=True)
    (img / "viz" / "a.png").write_bytes(b"x" * 10)
    (img / "viz" / "big.png").write_bytes(b"x" * (mod.MAX_BYTES + 1))
    (img / "viz" / "stale.png").write_bytes(b"x")
    (img / "viz" / "unused.png").write_bytes(b"x")
    (tmp_path / "README.md").write_text("[![thumb](docs/img/viz/a.png)](https://example.org)\n")
    (docs / "page.md").write_text(
        "![A chart](img/viz/a.png)\n\n*Caption.*\n\n"
        "![Big](img/viz/big.png)\n\n**Not a caption.**\n\n"
        "![](img/viz/missing.png)\n\n*x*\n\n"
        "```\n![ignored in a fence](img/viz/nope.png)\n```\n"
    )
    monkeypatch.setattr(mod, "ROOT", str(tmp_path))
    monkeypatch.setattr(mod, "DOCS", str(docs))
    monkeypatch.setattr(mod, "IMG", str(img))
    monkeypatch.setattr(mod, "FIGURES", {"viz/a.png": None, "viz/big.png": None})
    monkeypatch.setattr(mod, "THUMBS", {"viz/unused.png": ("viz/a.png", 300)})
    monkeypatch.setattr(mod, "SCREENSHOTS", {"shots/x.png": "a page"})
    errs = "\n".join(mod.check())
    assert "missing image img/viz/missing.png" in errs
    assert "big.png is" in errs and "cap" in errs
    assert "big.png needs an italic caption" in errs
    assert "missing.png" in errs
    assert "nope.png" not in errs  # fenced code is not a reference
    assert "docs/img/shots/x.png: not generated" in errs
    assert "docs/img/viz/stale.png: stale" in errs
    assert "docs/img/viz/unused.png: generated but referenced from no page" in errs
    assert "a.png needs" not in errs  # README thumbnails need no caption; the page one has one
    monkeypatch.setattr(mod, "MAX_TOTAL", 100)
    assert "in total (cap" in "\n".join(mod.check())
    assert mod.main(["--check"]) == 1


def test_per_entry_figure_modules_register_their_figures(tmp_path):
    """0.105: a module in scripts/docs_figures.d registers its figures through register(df)."""
    mod = _script()
    # the synthetic entries' workbook figures come from their own modules
    shipped = sorted(f[:-3] for f in os.listdir(mod.FIGURES_DIR) if f.endswith(".py"))
    assert {"plant_lockout", "plant_boiler_cycling", "plant_chiller_staging"} <= set(shipped)
    assert "workbook/plant-lockout.png" in mod.FIGURES
    # 0.108 (#231): a module can register a browser screenshot too
    assert "shots/rcx-prose.png" in mod.SCREENSHOTS and "shots/rcx-prose.png" in mod.SHOT_BUILDERS
    (tmp_path / "probe.py").write_text(
        "def register(df):\n"
        "    assert df.WIDE and callable(df._evidence_fig)\n"
        "    @df.figure('workbook/probe.png')\n"
        "    def _probe():\n"
        "        return None\n"
        "    @df.screenshot('shots/probe.png', 'a probe page')\n"
        "    def _shot(out):\n"
        "        return None\n",
        encoding="utf-8",
    )
    (tmp_path / "_private.py").write_text("raise RuntimeError('loaded')\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not a module\n", encoding="utf-8")
    assert mod.load_figure_modules(str(tmp_path)) == ["probe"]
    assert "workbook/probe.png" in mod.FIGURES
    assert (
        mod.SCREENSHOTS["shots/probe.png"] == "a probe page"
        and "shots/probe.png" in mod.SHOT_BUILDERS
    )
    assert mod.load_figure_modules(str(tmp_path / "missing")) == []
